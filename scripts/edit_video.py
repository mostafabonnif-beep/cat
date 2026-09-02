import json
import os
import subprocess
import tempfile

import cv2
import numpy as np

# v6.9.1: mediapipe is optional — a missing/broken install must not kill the
# module import (the pipeline crashed mid-run with a bare ModuleNotFoundError
# before). The usage site below already falls back to OpenCV Haar Cascade
# when `mp` lacks `solutions`.
try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    mp = None
    MEDIAPIPE_AVAILABLE = False
    print("MediaPipe not found. Install with: pip install mediapipe — will fall back to OpenCV Haar Cascade if needed.")
from scripts.active_speaker import ActiveSpeakerSelector
from scripts.audio_analysis import get_audio_energy
from scripts.media_validation import validate_media_file
from scripts.one_face import (
    crop_and_resize_single_face,
    crop_center_zoom,
    resize_with_padding,
)
from scripts.two_face import (
    crop_and_resize_multi_faces,
    detect_face_or_body_two_faces,
)

try:
    from scripts.face_detection_insightface import (
        crop_and_resize_insightface,
        detect_faces_insightface,
        init_insightface,
    )
    INSIGHTFACE_AVAILABLE = True
except ImportError:
    INSIGHTFACE_AVAILABLE = False
    print("InsightFace not found or error importing. Install with: pip install insightface onnxruntime-gpu")


# Global cache for encoder
CACHED_ENCODER = None

def get_best_encoder():
    global CACHED_ENCODER
    if CACHED_ENCODER: return CACHED_ENCODER
    
    try:
        # Check available encoders
        result = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True)
        output = result.stdout
        
        # Priority: NVENC (NVIDIA) > AMF (AMD) > QSV (Intel) > CPU
        if "h264_nvenc" in output:
            print("Encoder Detected: NVIDIA (h264_nvenc)")
            CACHED_ENCODER = ("h264_nvenc", "fast") # p1-p7 presets could be used but 'fast' maps well
            return CACHED_ENCODER
        
        if "h264_amf" in output:
            print("Encoder Detected: AMD (h264_amf)")
            CACHED_ENCODER = ("h264_amf", "speed") # quality, speed, balanced
            return CACHED_ENCODER
            
        if "h264_qsv" in output:
             print("Encoder Detected: Intel QSV (h264_qsv)")
             CACHED_ENCODER = ("h264_qsv", "veryfast")
             return CACHED_ENCODER
             
        # Mac OS (VideoToolbox)
        if "h264_videotoolbox" in output:
             print("Encoder Detected: MacOS (h264_videotoolbox)")
             CACHED_ENCODER = ("h264_videotoolbox", "default")
             return CACHED_ENCODER

    except Exception as e:
        print(f"Error checking encoders: {e}")

    print("Encoder Detected: CPU (libx264)")
    CACHED_ENCODER = ("libx264", "ultrafast")
    return CACHED_ENCODER

def get_center_bbox(bbox):
    # bbox: [x1, y1, x2, y2]
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

def get_center_rect(rect):
    # rect: (x, y, w, h)
    return (rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)


class SmoothBox:
    """Exponential-moving-average smoother for a tracked crop box.

    Reduces "shaky cam" jitter between detection frames: the crop center and
    size follow an EMA instead of jumping instantly, while the dead-zone in
    the pipeline already ignores sub-pixel noise. ``alpha`` is the speed of
    the response (1.0 = no smoothing, 0.0 = frozen).
    """

    def __init__(self, alpha=0.55):
        self.alpha = max(0.05, min(1.0, float(alpha)))
        self.center = None  # (cx, cy)
        self.size = None    # (width, height)

    def reset(self):
        self.center = None
        self.size = None

    def update(self, bbox):
        """Feed a raw bbox [x1, y1, x2, y2]; return the smoothed bbox."""
        x1, y1, x2, y2 = (float(v) for v in bbox[:4])
        center = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        size = (x2 - x1, y2 - y1)
        if self.center is None:
            self.center = center
            self.size = size
        else:
            a = self.alpha
            self.center = (a * center[0] + (1 - a) * self.center[0],
                           a * center[1] + (1 - a) * self.center[1])
            self.size = (a * size[0] + (1 - a) * self.size[0],
                         a * size[1] + (1 - a) * self.size[1])
        cx, cy = self.center
        w, h = self.size
        return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]

    @property
    def active(self):
        return self.center is not None

def sort_by_proximity(new_faces, old_faces, center_func):
    """
    Sorts new_faces to match the order of old_faces based on distance.
    new_faces: list of face objects (bbox or tuple)
    old_faces: list of face objects (bbox or tuple)
    center_func: function that takes a face object and returns (cx, cy)
    """
    if not old_faces or len(old_faces) != 2 or len(new_faces) != 2:
        return new_faces
    
    old_c1 = center_func(old_faces[0])
    old_c2 = center_func(old_faces[1])
    
    new_c1 = center_func(new_faces[0])
    new_c2 = center_func(new_faces[1])
    
    # Cost if we keep order: [new1, new2]
    # dist(old1, new1) + dist(old2, new2)
    dist_keep = ((old_c1[0]-new_c1[0])**2 + (old_c1[1]-new_c1[1])**2) + \
                ((old_c2[0]-new_c2[0])**2 + (old_c2[1]-new_c2[1])**2)
                
    # Cost if we swap: [new2, new1]
    # dist(old1, new2) + dist(old2, new1)
    dist_swap = ((old_c1[0]-new_c2[0])**2 + (old_c1[1]-new_c2[1])**2) + \
                ((old_c2[0]-new_c1[0])**2 + (old_c2[1]-new_c1[1])**2)
                
    # If swapping reduces total movement distance, do it
    if dist_swap < dist_keep:
        return [new_faces[1], new_faces[0]]
    
    return new_faces


def smooth_boxes_per_slot(boxes, smoothers, alpha, prev_count, frame_w, frame_h):
    """EMA-smooth every active face slot independently (v7.27).

    Slots keep their identity through the proximity/area ordering of the
    detection step, so slot ``i`` tracks the same physical face across
    detection cycles. Returns ``(smoothed_boxes, new_count)`` and resets all
    smoothers when the face count changes, so stale history never drags a
    vanished face across a layout switch. Boxes are clamped to the frame.
    """
    boxes = [list(b[:4]) for b in (boxes or [])]
    if alpha <= 0 or not boxes:
        return boxes, len(boxes)
    if len(boxes) != prev_count:
        for smoother in smoothers:
            smoother.reset()
    out = []
    for i, box in enumerate(boxes):
        if i >= len(smoothers):
            out.append(box)
            continue
        smoothed = smoothers[i].update(box)
        x1 = max(0.0, min(float(frame_w), float(smoothed[0])))
        y1 = max(0.0, min(float(frame_h), float(smoothed[1])))
        x2 = max(x1, min(float(frame_w), float(smoothed[2])))
        y2 = max(y1, min(float(frame_h), float(smoothed[3])))
        out.append([x1, y1, x2, y2])
    return out, len(boxes)


def face_count_hold(state, num_faces, prev_faces, misses, grace):
    """Auto-mode face-count down-grace (v7.27).

    When a 2+ face layout is active and one face is momentarily missing
    (turned head, detector blip), return ``(True, misses+1)`` for up to
    ``grace`` detection cycles so the lookahead + frozen-box logic can ride
    through the blip instead of popping the crop to 1 face and back.
    """
    if (state >= 2 and num_faces > 0 and num_faces < state
            and prev_faces is not None and len(prev_faces) >= state):
        if misses < grace:
            return True, misses + 1
        return False, 0
    if num_faces > 0 and num_faces >= state:
        return False, 0
    return False, misses


def generate_short_fallback(input_file, output_file, index, project_folder, final_folder, no_face_mode="padding"):
    """Fallback function: Center Crop (Zoom) or Padding if detection fails."""
    print(f"Processing (Fallback): {input_file} | Mode: {no_face_mode}")
    cap = cv2.VideoCapture(input_file)
    if not cap.isOpened():
        print(f"Error opening video: {input_file}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0 or fps > 240:
        fps = 30.0  # broken/VFR metadata → assume 30; keeps A/V in sync
    
    # Target dimensions (9:16)
    
    target_width = 1080
    target_height = 1920
    
    encoder_name, encoder_preset = get_best_encoder()
    
    # Use FFmpeg Pipe instead of cv2.VideoWriter to avoid OpenCV backend errors
    ffmpeg_cmd = [
        'ffmpeg', '-y', '-loglevel', 'error', '-hide_banner', '-stats',
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-s', f'{target_width}x{target_height}',
        '-pix_fmt', 'bgr24',
        '-r', str(fps),
        '-i', '-',
        '-c:v', encoder_name,
        '-preset', encoder_preset,
        '-pix_fmt', 'yuv420p',
        output_file
    ]
    
    # If using hardware encoder, we might want to set bitrate to ensure quality
    if "nvenc" in encoder_name or "amf" in encoder_name:
         ffmpeg_cmd.extend(["-b:v", "5M"])
    
    stderr_file = tempfile.TemporaryFile()
    process = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=stderr_file,
    )
    pipe_broken = False
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        if no_face_mode == "zoom":
             result = crop_center_zoom(frame)
        else:
             result = resize_with_padding(frame)
        
        if not pipe_broken:
            try:
                # Write raw bytes to ffmpeg stdin
                process.stdin.write(result.tobytes())
            except (BrokenPipeError, OSError) as e:
                # ffmpeg died mid-stream (bad encoder args, codec not
                # available, out of disk...). Stop feeding frames and let
                # the return-code check below raise a clear error instead
                # of silently producing a truncated "successful" clip.
                print(f"ffmpeg pipe closed early: {e}")
                pipe_broken = True

    cap.release()
    if process.stdin and not process.stdin.closed:
        try:
            process.stdin.close()
        except OSError:
            pass
    returncode = process.wait()
    stderr_file.seek(0)
    stderr_tail = "".join(stderr_file.read().decode("utf-8", "replace").splitlines()[-25:])
    stderr_file.close()
    if returncode != 0 or pipe_broken:
        raise RuntimeError(
            "ffmpeg failed while encoding clip frames (exit code {}). "
            "Command: {}\n--- stderr tail ---\n{}"
            .format(returncode, " ".join(ffmpeg_cmd), stderr_tail))
    
    if not finalize_video(input_file, output_file, index, fps, project_folder, final_folder):
        raise RuntimeError(f"Could not finalize edited clip {index}: audio/mux validation failed")

def finalize_video(input_file, output_file, index, fps, project_folder, final_folder):
    """Mux audio and video and return True only for a validated final file."""
    audio_file = os.path.join(project_folder, "cuts", f"output-audio-{index}.aac")
    extraction = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", input_file,
         "-vn", "-acodec", "copy", audio_file],
        check=False, capture_output=True, text=True,
    )
    if extraction.returncode != 0 or not os.path.exists(audio_file) or os.path.getsize(audio_file) <= 0:
        stderr = (extraction.stderr or "").strip().splitlines()[-5:]
        print(f"Error extracting audio for {input_file}: {' | '.join(stderr)}")
        return False

    if os.path.exists(audio_file) and os.path.getsize(audio_file) > 0:
        final_output = os.path.join(final_folder, f"final-output{str(index).zfill(3)}_processed.mp4")
        encoder_name, encoder_preset = get_best_encoder()
        # A/V sync fix (v6.6): the OpenCV frame pipe can drop/duplicate frames,
        # making the video slightly shorter than the source audio. Without
        # -shortest + aresample the muxed audio drifted out of sync.
        temp_output = final_output + ".tmp.mp4"
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
            "-i", output_file,
            "-i", audio_file,
            "-c:v", encoder_name, "-preset", encoder_preset, "-b:v", "5M",
            "-c:a", "aac", "-b:a", "192k",
            "-r", str(fps),
            "-af", "aresample=async=1",
            "-shortest",
            temp_output
        ]
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            if result.returncode != 0:
                stderr = (result.stderr or "").strip().splitlines()[-12:]
                raise RuntimeError(
                    "ffmpeg mux failed (exit code {}): {}".format(
                        result.returncode, " | ".join(stderr)))
            validation = validate_media_file(temp_output, require_audio=True)
            if not validation.get("ok"):
                raise RuntimeError(
                    "final media validation failed: {}".format(
                        "; ".join(validation.get("errors", ["invalid output"]))))
            os.replace(temp_output, final_output)
            print(f"Final file generated: {final_output}")
            try:
                os.remove(audio_file)
                os.remove(output_file)
            except OSError:
                pass
        except (OSError, RuntimeError) as e:
            try:
                if os.path.exists(temp_output):
                    os.remove(temp_output)
            except OSError:
                pass
            print(f"Error finalizing video: {e}")
            return False
        return True
    print(f"Warning: No audio extracted for {input_file}")
    return False


def calculate_mouth_ratio_106(landmarks):
    """Estimate mouth openness from InsightFace's 106-point landmarks."""
    if landmarks is None:
        return 0.0
    try:
        pts = np.asarray(landmarks, dtype=float)
        if pts.ndim != 2 or len(pts) < 72 or pts.shape[1] < 2:
            return 0.0
        # InsightFace 106-point models place the mouth contour around 52:72.
        mouth = pts[52:72, :2]
        width = float(np.max(mouth[:, 0]) - np.min(mouth[:, 0]))
        height = float(np.max(mouth[:, 1]) - np.min(mouth[:, 1]))
        return height / width if width > 1e-6 else 0.0
    except (TypeError, ValueError, IndexError):
        return 0.0


def calculate_mouth_ratio(landmarks):
    """
    Calculate Mouth Aspect Ratio (MAR) using 68-point landmarks (inner lips).
    Indices: 
    Inner Lips: 60-67 (0-indexed 60 to 67)
    Left Corner: 60
    Right Corner: 64
    Top Center: 62
    Bottom Center: 66
    """
    if landmarks is None:
        return 0
    
    # 3D points (x,y,z) or 2D (x,y). We use first 2 cols.
    pts = landmarks.astype(float)
    
    # Simple vertical vs horizontal
    # Vertical
    p62 = pts[62]
    p66 = pts[66]
    h = np.linalg.norm(p62[:2] - p66[:2])
    
    # Horizontal
    p60 = pts[60]
    p64 = pts[64]
    w = np.linalg.norm(p60[:2] - p64[:2])
    
    if w < 1e-6: return 0
    
    return h / w

def order_faces_for_crop(faces, *, focus_active_speaker, selector=None, frame_index=0):
    """Return crop candidates with the selected speaker first when enabled."""
    faces = list(faces or [])
    if not focus_active_speaker or len(faces) < 2 or selector is None:
        return faces, False
    return selector.reorder(faces, frame_index=frame_index)


def generate_short_mediapipe(input_file, output_file, index, face_mode, project_folder, final_folder, face_detection, face_mesh, pose, detection_period=None, no_face_mode="padding"):
    try:
        cap = cv2.VideoCapture(input_file)
        if not cap.isOpened():
            print(f"Error opening video: {input_file}")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(output_file, fourcc, fps, (1080, 1920))

        # v6.14.1 fix: coordinate_log was referenced below (empty-face frames)
        # but only ever initialized in the insightface function → NameError
        # crash in the mediapipe path with no_face_mode="padding" (default).
        coordinate_log = []  # Store raw face coordinates frame-by-frame

        next_detection_frame = 0

        last_detected_faces = None
        last_frame_face_positions = None
        last_success_frame = -1000
        max_frames_without_detection = int(3.0 * fps) # 3 seconds timeout

        transition_duration = int(fps)
        transition_frames = []

        for frame_index in range(total_frames):
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            if frame_index >= next_detection_frame:
                # Detect up to four faces for explicit multi/grid modes.
                mode_name = str(face_mode).lower()
                requested_faces = 4 if mode_name in {"multi", "grid"} else int(mode_name) if mode_name in {"2", "3", "4"} else 2
                detections = detect_face_or_body_two_faces(
                    frame, face_detection, face_mesh, pose, max_faces=requested_faces,
                )
                
                # Dynamic Logic
                target_faces = 1
                if mode_name in {"2", "3", "4"}:
                    target_faces = int(mode_name)
                elif mode_name in {"multi", "grid"}:
                    target_faces = 4
                elif mode_name == "auto":
                    if detections and len(detections) >= 2:
                        target_faces = 2
                    else:
                        target_faces = 1
                
                # Filter detections based on target
                current_detections = []
                if detections:
                    # Sort detections by approximate Area (w*h) descending to pick main faces first
                    detections.sort(key=lambda s: s[2] * s[3], reverse=True)
                    
                    if len(detections) >= target_faces:
                        current_detections = detections[:target_faces]
                    elif len(detections) > 0:
                        # Explicit multi modes keep every available speaker;
                        # auto falls back conservatively to one face.
                        if mode_name not in {"auto", "1"} and len(detections) >= 2:
                            current_detections = detections[:requested_faces]
                            target_faces = len(current_detections)
                        else:
                            current_detections = detections[:1]
                            target_faces = 1
                    
                    # Apply proximity matching for two speakers; grid modes use
                    # stable left-to-right ordering to keep tiles from jumping.
                    if target_faces == 2 and len(current_detections) == 2:
                         if last_detected_faces is not None and len(last_detected_faces) == 2:
                             current_detections = sort_by_proximity(current_detections, last_detected_faces, get_center_rect)
                    elif target_faces >= 3 and len(current_detections) >= 2:
                         current_detections = sorted(
                             current_detections,
                             key=lambda b: (b[0] + b[2] / 2.0, b[1] + b[3] / 2.0),
                         )
                
                # Check for stability/lookahead could go here but skipping for brevity unless requested.
                
                if current_detections and len(current_detections) == target_faces:
                    if last_frame_face_positions is not None:
                        start_faces = np.array(last_frame_face_positions)
                        end_faces = np.array(current_detections)
                        try:
                            transition_frames = np.linspace(start_faces, end_faces, transition_duration, dtype=int)
                        except Exception:
                            # Fallback if shapes mismatch unexpectedly
                            transition_frames = []
                    else:
                        transition_frames = []
                    last_detected_faces = current_detections
                    last_success_frame = frame_index
                else:
                    pass
                
                # Update next detection frame
                step = 5
                
                if detection_period is not None:
                    if isinstance(detection_period, dict):
                         # If we are targeting 2 faces, we use '2' interval, else '1'
                         key = str(target_faces)
                         val = detection_period.get(key, detection_period.get('1', 0.2))
                         step = max(1, int(val * fps))
                    else:
                         step = max(1, int(detection_period * fps))
                elif target_faces >= 2:
                    step = int(1.0 * fps)
                else:
                    step = int(5) # 5 frames for 1 face
                
            next_detection_frame = frame_index + step

            if len(transition_frames) > 0:
                current_faces = transition_frames[0]
                transition_frames = transition_frames[1:]
            elif last_detected_faces is not None and (frame_index - last_success_frame) <= max_frames_without_detection:
                current_faces = last_detected_faces
            else:
                if no_face_mode == "zoom":
                    result = crop_center_zoom(frame)
                else:
                    result = resize_with_padding(frame)
                coordinate_log.append({"frame": frame_index, "faces": []})
                out.write(result)
                continue

            last_frame_face_positions = current_faces

            if hasattr(current_faces, '__len__') and len(current_faces) >= 2:
                 result = crop_and_resize_multi_faces(
                     frame, current_faces, layout="auto", max_faces=4,
                 )
            else:
                 # Ensure it's list of tuples or single tuple? current_faces is list of tuples from detection
                 # If 1 face: [ (x,y,w,h) ]
                 if hasattr(current_faces, '__len__') and len(current_faces) > 0:
                     f = current_faces[0]
                     result = crop_and_resize_single_face(frame, f)
                 else:
                     if no_face_mode == "zoom":
                         result = crop_center_zoom(frame)
                     else:
                         result = resize_with_padding(frame)
            
            out.write(result)

        cap.release()
        out.release()
        
        if not finalize_video(input_file, output_file, index, fps, project_folder, final_folder):
            raise RuntimeError(f"Could not finalize MediaPipe clip {index}: audio/mux validation failed")

    except Exception as e:
        print(f"Error in MediaPipe processing: {e}")
        raise e # Rethrow to trigger fallback

def generate_short_haar(input_file, output_file, index, project_folder, final_folder, detection_period=None, no_face_mode="padding"):
    """Face detection using OpenCV Haar Cascades."""
    print(f"Processing (Haar Cascade): {input_file}")
    
    # Load Haar Cascade
    cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    face_cascade = cv2.CascadeClassifier(cascade_path)
    if face_cascade.empty():
        print("Error: Could not load Haar Cascade XML. Falling back to center crop.")
        generate_short_fallback(input_file, output_file, index, project_folder, final_folder)
        return

    cap = cv2.VideoCapture(input_file)
    if not cap.isOpened():
        print(f"Error opening video: {input_file}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_file, fourcc, fps, (1080, 1920))
    
    # Logic copied from generate_short_mediapipe
    detection_interval = int(2 * fps) # Default check every 2 seconds
    if detection_period is not None:
        detection_interval = max(1, int(detection_period * fps))
    last_detected_faces = None
    last_frame_face_positions = None
    last_success_frame = -1000
    max_frames_without_detection = int(3.0 * fps)

    transition_duration = int(fps) # 1 second smooth transition
    transition_frames = []

    for frame_index in range(total_frames):
        ret, frame = cap.read()
        if not ret or frame is None:
            break

        if frame_index % detection_interval == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.1, 4)
            
            detections = []
            if len(faces) > 0:
                # Pick largest face
                largest_face = max(faces, key=lambda f: f[2] * f[3])
                # Ensure int type
                detections = [tuple(map(int, largest_face))]

            if detections:
                if last_frame_face_positions is not None:
                    # Simple linear interpolation for smoothing
                    start_faces = np.array(last_frame_face_positions)
                    end_faces = np.array(detections)
                    
                    # Generate transition frames
                    steps = transition_duration
                    transition_frames = []
                    for s in range(steps):
                        t = (s + 1) / steps
                        interp = (1 - t) * start_faces + t * end_faces
                        transition_frames.append(interp.astype(int).tolist()) # Convert back to list of lists/tuples
                else:
                    transition_frames = []
                last_detected_faces = detections
                last_success_frame = frame_index
            else:
                pass

        if len(transition_frames) > 0:
            current_faces = transition_frames[0]
            transition_frames = transition_frames[1:]
        elif last_detected_faces is not None and (frame_index - last_success_frame) <= max_frames_without_detection:
            current_faces = last_detected_faces
        else:
            # No face detected for a while -> Center/Padding fallback
            if no_face_mode == "zoom":
                result = crop_center_zoom(frame)
            else:
                result = resize_with_padding(frame)
            out.write(result)
            continue

        last_frame_face_positions = current_faces
        # haar detections are list containing one tuple (x,y,w,h)
        # current_faces is list of one tuple
        if isinstance(current_faces, list):
             face_bbox = current_faces[0]
        else:
             face_bbox = current_faces # Should be handled

        result = crop_and_resize_single_face(frame, face_bbox)
        out.write(result)

    cap.release()
    out.release()
    
    if not finalize_video(input_file, output_file, index, fps, project_folder, final_folder):
        raise RuntimeError(f"Could not finalize Haar clip {index}: audio/mux validation failed")

def generate_short_insightface(input_file, output_file, index, project_folder, final_folder, face_mode="auto", detection_period=None, filter_threshold=0.35, two_face_threshold=0.60, confidence_threshold=0.30, dead_zone=40, focus_active_speaker=False, active_speaker_mar=0.03, active_speaker_score_diff=1.5, include_motion=False, active_speaker_motion_deadzone=3.0, active_speaker_motion_sensitivity=0.05, active_speaker_decay=2.0, no_face_mode="padding", smoothing=0.55, headroom=0.12):
    """Face detection using InsightFace (SOTA)."""
    print(f"Processing (InsightFace): {input_file} | Mode: {face_mode}")
    
    cap = cv2.VideoCapture(input_file)
    if not cap.isOpened():
        print(f"Error opening video: {input_file}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    # Using mp4v for container, but final mux will fix encoding
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_file, fourcc, fps, (1080, 1920))
    
    # Dynamic Interval Logic
    next_detection_frame = 0
    
    last_detected_faces = None
    last_frame_face_positions = None
    last_success_frame = -1000
    max_frames_without_detection = int(3.0 * fps) # 3 seconds timeout

    # Per-slot EMA smoothers (v7.18): keep the crop box stable between
    # detection frames so the output does not shimmer on talking heads.
    face_smoothers = [SmoothBox(alpha=smoothing) for _ in range(4)]
    smoothing_frames = 0
    smoothing_total = 0

    smoothed_slots = 0  # how many face slots were smoothed last frame (v7.27)
    # Auto-mode face-count grace (v7.27): a 2-face layout survives brief
    # detection blips (2 quick re-detect cycles ~0.2s apart) instead of
    # popping to 1 face and back.
    face_drop_misses = 0
    face_drop_grace_cycles = 2

    transition_duration = 4 # Smooth transition over 4 frames (almost continuous)
    transition_frames = []
    active_speaker_selector = ActiveSpeakerSelector(
        switch_margin=active_speaker_score_diff,
        hold_frames=max(1, int(round(fps * 0.25))),
        max_jump=200.0,
    )

    # Current state of face mode (1..4). Auto keeps the legacy 1/2 behavior.
    current_num_faces_state = 1
    if str(face_mode) in {"2", "3", "4"}:
        current_num_faces_state = int(face_mode)
    elif str(face_mode).lower() in {"multi", "grid"}:
        current_num_faces_state = 4

    frame_1_face_count = 0
    frame_2_face_count = 0
    frame_multi_face_count = 0

    buffered_frame = None
    
    # Timeline tracking: list of (frame_index, mode_str)
    # We will compress this later.
    timeline_frames = [] # Store mode for *every written frame* or at least detection points
    
    timeline_frames = [] # Store mode for *every written frame* or at least detection points
    coordinate_log = [] # Store raw face coordinates frame-by-frame
    
    # For Active Speaker Logic
    # Map of "Face ID" to activity score?
    # Since we don't have ID tracker, we blindly assign score to faces based on proximity to previous frame
    # A list of dictionaries: [{'center': (x,y), 'activity': score}, ...]
    faces_activity_state = [] 
    
    # Advanced Active Speaker: Extract audio energy for the whole segment
    audio_energies = None
    if focus_active_speaker:
        print(f"DEBUG: Extracting audio energy for {input_file}...")
        audio_energies = get_audio_energy(input_file, fps)
        if audio_energies is not None:
            print(f"DEBUG: Audio energy extracted. Mean: {np.mean(audio_energies):.4f}")

    for frame_index in range(total_frames):
        if buffered_frame is not None:
             frame = buffered_frame
             ret = True
             buffered_frame = None
        else:
             ret, frame = cap.read()

        if not ret or frame is None:
            break

        if frame_index >= next_detection_frame and len(transition_frames) == 0:
            # Detect faces
            faces = detect_faces_insightface(frame)
            if faces:
                scores = [f"{f.get('det_score',0):.2f}" for f in faces]
                print(f"DEBUG: Frame {frame_index} | Raw Faces: {len(faces)} | Scores: {scores}")
            else:
                pass # print(f"DEBUG: Frame {frame_index} | No Raw Faces")

            # --- ACTIVITY / SPEAKER DETECTION ---
            # (Feature currently disabled for stability - relying on simple size checks)
            last_raw_faces = faces 
            # ------------------------------------

            # --- INTELLIGENT FILTERING ---
            valid_faces = []
            if faces:
                # 1. Filter by confidence (Using user threshold)
                faces = [f for f in faces if f.get('det_score', 0) > confidence_threshold]
                
                if faces:
                    # Pre-calculate areas and SPEAKER SCORE
                    for f in faces:
                        w = f['bbox'][2] - f['bbox'][0]
                        h = f['bbox'][3] - f['bbox'][1]
                        f['area'] = w * h
                        f['center'] = ((f['bbox'][0] + f['bbox'][2]) / 2, (f['bbox'][1] + f['bbox'][3]) / 2)
                        
                        act = f.get('activity', 0)
                        f['effective_area'] = f['area'] * (1.0 + (act * 0.05))

                    # Find largest face
                    max_area = max(f['area'] for f in faces)
                    
                    # 2. Relative Size Filter
                    valid_faces = [f for f in faces if f['area'] > (filter_threshold * max_area)]
                    
                    if len(valid_faces) < len(faces):
                        print(f"DEBUG: Filtered {len(faces)-len(valid_faces)} small faces. Max Area: {max_area}. Filter Thresh: {filter_threshold}")
                    
                    faces = valid_faces
            
            # --- ACTIVE SPEAKER UPDATE ---
            if faces:
                # 1. Update activity scores for current faces
                # Simple matching to previous state
                current_state_map = []
                
                for f in faces:
                    # Calculate instantaneous openness
                    mar = 0
                    if 'landmark_3d_68' in f:
                        mar = calculate_mouth_ratio(f['landmark_3d_68'])
                    elif 'landmark_2d_106' in f:
                        mar = calculate_mouth_ratio_106(f['landmark_2d_106'])
                    
                    f['mouth_ratio'] = mar
                    # Heuristic: Ratio > 0.05 implies openish, > 0.1 talk.
                    # Adjust thresholds: 0.03 is common for closed mouth, 0.05 is starting to open.
                    
                    # Log raw MAR for debugging
                    # print(f"DEBUG: Frame {frame_index} Face {i} MAR: {mar:.4f}")
                    
                    is_talking = 1.0 if mar > active_speaker_mar else 0.0 
                    

            # --- CROWD MODE LOGIC ---
            # If too many faces, don't even try to track. Fallback to No-Face logic (Zoom/Padding)
            CROWD_THRESHOLD = 7 
            # FIX: Use last_raw_faces (before size filtering) so we count background people too!
            is_crowd = len(last_raw_faces) >= CROWD_THRESHOLD
            if is_crowd:
                print(f"DEBUG: Crowd Mode Active! {len(faces)} faces >= {CROWD_THRESHOLD}. Triggering Fallback (No Face Mode).")
                faces = [] 
                valid_faces = [] # CAUTION: Must clear strict backup too!
                # FORCE RESET HISTORY so it doesn't "stick" to the last face found
                last_detected_faces = None
                transition_frames = []
                faces_activity_state = [] 
            # ---------------------------

            # Update Activity State - Two Pass for Global Motion Compensation
            if focus_active_speaker and faces:
                # Pass 1: Global Motion (Camera Shake) Calculation
                # We calculate motion for ALL confident faces (before size filtering) to get best global estimate
                raw_motions = []
                
                # First, ensure we have a temporary mapping of current faces to history
                # We do this non-destructively just to get motion values
                for f in faces:
                    my_c = f['center']
                    best_dist = 9999
                    if faces_activity_state:
                         for old_s in faces_activity_state:
                             old_c = old_s['center']
                             dist = np.sqrt((my_c[0]-old_c[0])**2 + (my_c[1]-old_c[1])**2)
                             if dist < best_dist:
                                 best_dist = dist
                    
                    if best_dist < 200:
                        f['_raw_motion'] = best_dist
                    else:
                        f['_raw_motion'] = 0.0
                    
                    if include_motion:
                        raw_motions.append(f['_raw_motion'])

                global_motion = 0.0
                if include_motion and len(raw_motions) >= 2:
                    global_motion = min(raw_motions)

                # Pass 2: Update Scores for ALL faces
                current_state_map = []
                for f in faces:
                     # The smoothed mouth ratio is filled after matching this
                     # face to the previous frame.
                     is_talking = False
                     
                     # Calculate Compensated Motion
                     motion_bonus = 0.0
                     if include_motion and faces_activity_state:
                         comp_motion = max(0.0, f.get('_raw_motion', 0.0) - global_motion)
                         f['motion_val'] = comp_motion # Store for debug
                         
                         if comp_motion > active_speaker_motion_deadzone:
                              motion_bonus = min(2.5, (comp_motion - active_speaker_motion_deadzone) * active_speaker_motion_sensitivity)
                     else:
                        f['motion_val'] = 0.0
                     
                     # Accumulate Score
                     matched_score = 0.0
                     
                     # Re-find match to update history
                     my_c = f['center']
                     best_dist = 9999
                     best_idx = -1
                     if faces_activity_state:
                         for i, old_s in enumerate(faces_activity_state):
                             old_c = old_s['center']
                             dist = np.sqrt((my_c[0]-old_c[0])**2 + (my_c[1]-old_c[1])**2)
                             if dist < best_dist:
                                 best_dist = dist
                                 best_idx = i
                     
                     if best_idx != -1 and best_dist < 200:
                         previous_mouth = float(faces_activity_state[best_idx].get('mouth_ratio', f.get('mouth_ratio', 0.0)))
                         current_mouth = float(f.get('mouth_ratio', 0.0))
                         smoothed_mouth = (0.65 * previous_mouth) + (0.35 * current_mouth)
                         f['mouth_ratio_smooth'] = smoothed_mouth
                         is_talking = smoothed_mouth > active_speaker_mar
                         old_val = faces_activity_state[best_idx]['activity']
                         change = -abs(active_speaker_decay)
                         
                         # Advanced Logic: Combine MAR with Audio Energy
                         current_audio = audio_energies[frame_index] if (audio_energies is not None and frame_index < len(audio_energies)) else 0.0
                         
                         # If there is sound, talking is rewarded more. If silence, talking is ignored (likely false positive).
                         if is_talking:
                             # Boost talking score if there's actual audio energy
                             # Heuristic: if energy > 0.1, it's likely real speech
                             audio_boost = 1.0 + (current_audio * 2.0) # up to 3x boost
                             change = 1.5 * audio_boost
                         else:
                             # If not talking but there's high audio energy, someone else might be speaking
                             # Decay faster if there's audio but this face isn't moving
                             if current_audio > 0.3:
                                 change = -abs(active_speaker_decay) * 1.5
                         
                         new_val = old_val + change + motion_bonus
                         # Increased cap to 20.0 to allow motion differences to separate two 'talking' faces
                         matched_score = max(0.0, min(20.0, new_val))
                     else:
                         f['mouth_ratio_smooth'] = float(f.get('mouth_ratio', 0.0))
                         is_talking = f['mouth_ratio_smooth'] > active_speaker_mar
                         matched_score = 1.0 if is_talking else 0.0
                     
                     f['activity_score'] = matched_score
                     current_state_map.append({
                         'center': f['center'],
                         'activity': matched_score,
                         'mouth_ratio': f.get('mouth_ratio_smooth', f.get('mouth_ratio', 0.0)),
                     })
                 
                faces_activity_state = current_state_map
            else:
                faces_activity_state = []

            faces = valid_faces
            if focus_active_speaker and len(faces) >= 2:
                faces, speaker_switched = order_faces_for_crop(
                    faces, focus_active_speaker=True,
                    selector=active_speaker_selector, frame_index=frame_index)
                if speaker_switched:
                    print(f"DEBUG: Active speaker switched at frame {frame_index}")
            
            # v7.27: auto-mode count-down grace (see face_count_hold).
            holding_multi = False
            if str(face_mode).lower() == "auto":
                holding_multi, face_drop_misses = face_count_hold(
                    current_num_faces_state, len(faces) if faces else 0,
                    last_detected_faces, face_drop_misses,
                    face_drop_grace_cycles)

            # Decide how many faces to frame. Explicit multi modes are stable;
            # auto intentionally keeps the legacy 1/2-speaker heuristic.
            target_faces = 1
            mode_name = str(face_mode).lower()
            if mode_name in {"2", "3", "4"}:
                target_faces = int(mode_name)
            elif mode_name in {"multi", "grid"}:
                target_faces = 4
            elif mode_name == "auto":
                if len(faces) >= 2:
                    # Default decision variable
                    decided = False
                    
                    if focus_active_speaker:
                         # EXPERIMENTAL: Decide based on activity
                         f1 = faces[0]
                         f2 = faces[1]
                         score1 = f1.get('activity_score', 0)
                         score2 = f2.get('activity_score', 0)
                         
                         y1 = f1['center'][1]
                         y2 = f2['center'][1]
                         pos1 = "Top" if y1 < y2 else "Bottom"
                         pos2 = "Top" if y2 < y1 else "Bottom"
                         
                         # Debug Active Speaker
                         print(f"DEBUG: Frame {frame_index} | {pos1} (MAR: {f1.get('mouth_ratio',0):.3f}, Mov: {f1.get('motion_val',0):.1f}, Score: {score1:.1f}) | {pos2} (MAR: {f2.get('mouth_ratio',0):.3f}, Mov: {f2.get('motion_val',0):.1f}, Score: {score2:.1f})")


                         # If one is clearly dominant active speaker
                         # Lower threshold to make it more sensitive?
                         # Score difference > 2.0 (approx 2-3 frames of talking difference vs silence)
                         diff = abs(score1 - score2)
                         # Check strict dominance first
                         if diff > active_speaker_score_diff:
                             # Pick the winner
                             target_faces = 1
                             decided = True
                             # Ensure the list is sorted by activity so [0] is the winner
                             if score2 > score1:
                                 # Swap ensures [0] is the active one for later 1-face crop logic which takes [0]
                                 faces = [f2, f1]
                             print(f"DEBUG: Active Speaker Focus Triggered! Diff ({diff:.2f}) > Thresh ({active_speaker_score_diff}). Focusing on Face {'2' if score2 > score1 else '1'}.")
                             
                         elif score1 > 4.0 and score2 > 4.0:
                             # Both talking -> 2 faces
                             # Raised threshold to 4.0 to avoid noise triggering split
                             target_faces = 2
                             decided = True
                             print("DEBUG: Dual Active Speakers! Both scores > 4.0. Forcing Split Mode.")
                         
                         # If scores are low (both silent), fallback to size ratio (decided=False) or force 1 if very silent?
                         # Let's fallback to size.

                    if not decided:
                        # Standard Logic: Check relative sizes (effective area)
                        faces_sorted_temp = sorted(faces, key=lambda f: f.get('effective_area', 0), reverse=True)
                        largest = faces_sorted_temp[0]['effective_area']
                        second = faces_sorted_temp[1]['effective_area']
    
                        # Two-Face Constraint
                        if second > (two_face_threshold * largest):
                            target_faces = 2
                        else:
                            target_faces = 1
                else:
                    target_faces = 1
            
            # If no faces found effectively after filter
            if not faces and not valid_faces:
                 # Logic ensures faces = valid_faces already
                 pass
            
            # -----------------------------

            # (v7.27) During the count-down grace window keep requesting the
            # previous multi-face target so the lookahead gets a chance to
            # re-acquire the momentarily missing face.
            if holding_multi:
                target_faces = max(target_faces, current_num_faces_state)

            # Fallback Lookahead: If detection fails or partial
            # But DO NOT look ahead if we are in Crowd Mode (we explicitly wanted 0 faces)
            if len(faces) < target_faces and not is_crowd:
                # Try 1 frame ahead
                ret2, frame2 = cap.read()
                if ret2 and frame2 is not None:
                     faces2 = detect_faces_insightface(frame2)
                     
                     # --- Apply same filtering to lookahead ---
                     valid_faces2 = []
                     if faces2:
                         faces2 = [f for f in faces2 if f.get('det_score', 0) > 0.50]
                         if faces2:
                             for f in faces2:
                                 w = f['bbox'][2] - f['bbox'][0]
                                 h = f['bbox'][3] - f['bbox'][1]
                                 f['area'] = w * h
                                 f['center'] = ((f['bbox'][0] + f['bbox'][2]) / 2, (f['bbox'][1] + f['bbox'][3]) / 2)
                                 f['effective_area'] = f['area'] # Default for lookahead
                             max_area2 = max(f['area'] for f in faces2)
                             # STRICTER FILTER: threshold of max area
                             valid_faces2 = [f for f in faces2 if f['area'] > (filter_threshold * max_area2)]
                     faces2 = valid_faces2
                     # ----------------------------------------


                     # If lookahead found what we wanted OR found something better than nothing
                     if len(faces2) >= target_faces:
                         faces = faces2 # Use lookahead faces for current frame
                     elif len(faces) == 0 and len(faces2) > 0:
                         faces = faces2 # Better than nothing
                         
                     buffered_frame = frame2 # Store for next iteration

            detections = []
            
            if len(faces) >= target_faces:
                # --- FACE TRACKING / SORTING ---
                # Instead of just Area, we prioritize faces closer to the LAST detected face
                # This prevents switching to a background person if sizes are similar
                
                if focus_active_speaker and target_faces == 1:
                    # ActiveSpeakerSelector already applied hysteresis and put
                    # the selected face first. Do not re-sort it by bbox size or
                    # distance here, otherwise the feature silently becomes a
                    # largest-face crop after every detection interval.
                    faces_sorted = list(faces)
                elif last_detected_faces is not None and len(last_detected_faces) == target_faces:
                   # Define score function: High Area is good, Low Distance to old is good.
                   # But simpler: calculate Intersection over Union (IOU) or Distance to old bbox center
                   
                   # We want to match existing slots.
                   # For 1 face:
                   if target_faces == 1:
                       old_center = get_center_bbox(last_detected_faces[0])
                       
                       def sort_score(f, old_center=old_center):
                           # Distance score (lower is better)
                           dist = np.sqrt((f['center'][0] - old_center[0])**2 + (f['center'][1] - old_center[1])**2)
                           # EFFECTIVE Area score (higher is better)
                           # Weight distance more heavily to keep consistency, but allow activity to swap focus if significant
                           # normalized score?
                           return dist - (f['effective_area'] * 0.0001) 
                       
                       faces_sorted = sorted(faces, key=sort_score)
                   else:
                       # For 2 faces, just sort by effective area for now as proximity sort happens later
                       faces_sorted = sorted(faces, key=lambda f: f['effective_area'], reverse=True)
                else:
                   # No history, sort by effective area
                   if focus_active_speaker and target_faces == 1:
                        # Pick the one with highest activity score
                        faces_sorted = sorted(faces, key=lambda f: f.get('activity_score', 0), reverse=True)
                   else:
                        faces_sorted = sorted(faces, key=lambda f: f.get('effective_area', 0), reverse=True)
                
                if target_faces == 2:
                    # Preserve the legacy proximity-aware two-speaker ordering.
                    f1 = faces_sorted[0]['bbox']
                    f2 = faces_sorted[1]['bbox']
                    if last_detected_faces is not None and len(last_detected_faces) == 2:
                        detections = sort_by_proximity([f1, f2], last_detected_faces, get_center_bbox)
                    else:
                        detections = [f1, f2]
                    current_num_faces_state = 2
                elif target_faces >= 3:
                    # For grids, left-to-right ordering is more stable than area order.
                    selected = [f['bbox'] for f in faces_sorted[:target_faces]]
                    detections = sorted(
                        selected,
                        key=lambda b: ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0),
                    )
                    current_num_faces_state = len(detections)
                else:
                    detections = [faces_sorted[0]['bbox']]
                    current_num_faces_state = 1
            else:
                 # (v7.27) Grace window still active and the face is missing
                 # after lookahead: emit NO detection this cycle so the frame
                 # writer keeps the last known multi-face boxes (frozen for up
                 # to 3s) instead of popping to a 1-face crop.
                 if holding_multi and len(faces) > 0:
                     detections = []
                 # Keep all available faces for an explicit multi mode, otherwise
                 # retain auto's safe single-face fallback.
                 elif len(faces) > 0:
                     faces_sorted = sorted(faces, key=lambda f: f['effective_area'], reverse=True)
                     if mode_name not in {"auto", "1"} and target_faces > 1 and len(faces) >= 2:
                         selected = [f['bbox'] for f in faces_sorted[:target_faces]]
                         detections = sorted(
                             selected,
                             key=lambda b: ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0),
                         )
                         current_num_faces_state = len(detections)
                     else:
                         detections = [faces_sorted[0]['bbox']]
                         current_num_faces_state = 1
                 else:
                     detections = []

            if detections:
                # --- STABILIZATION (DEAD ZONE) ---
                # Check if movement is small enough to ignore
                if last_detected_faces is not None and len(last_detected_faces) == len(detections):
                    is_stable = True
                    for i in range(len(detections)):
                        old_c = get_center_bbox(last_detected_faces[i])
                        new_c = get_center_bbox(detections[i])
                        dist = np.sqrt((old_c[0]-new_c[0])**2 + (old_c[1]-new_c[1])**2)
                        
                        # Threshold: dead_zone variable (pixels)
                        # Reduced jitter for talking heads
                        if dist > dead_zone: 
                            is_stable = False
                            break
                    
                    if is_stable:
                        # Keep old position to prevent "shaky cam"
                        detections = last_detected_faces
                        # Clear transition logic (snap) or keep it empty
                        transition_frames = []
                # ---------------------------------

                if last_frame_face_positions is not None and len(last_frame_face_positions) == len(detections):
                    # Only transition if we decided to MOVE (i.e., not stable)
                    forced_transition = True
                    if last_detected_faces is not None and len(detections) == len(last_detected_faces):
                         # Manual check to avoid numpy ambiguity
                         arrays_equal = True
                         for i in range(len(detections)):
                             if not np.array_equal(detections[i], last_detected_faces[i]):
                                 arrays_equal = False
                                 break
                         if arrays_equal:
                             forced_transition = False

                    if not transition_frames and forced_transition:
                        # Transition
                        start_faces = np.array(last_frame_face_positions)
                        end_faces = np.array(detections)
                        
                        steps = transition_duration
                        transition_frames = []
                        for s in range(steps):
                            t = (s + 1) / steps
                            interp = (1 - t) * start_faces + t * end_faces
                            transition_frames.append(interp.astype(int).tolist())
                        
                        # Optimization removed to avoid "Ambiguous truth value of array" error
                        # if detections == last_detected_faces: caused crash
                    
                else:
                    # Reset transition if face count changed or first detect
                    transition_frames = []
                last_detected_faces = detections
                last_success_frame = frame_index
            else:
                pass


            # Update next detection frame based on NEW state
            step = 5 # Default fallback (very fast)
            
            if detection_period is not None:
                if isinstance(detection_period, dict):
                    # Period depends on state
                    key = str(current_num_faces_state) 
                    # fallback to '1' if key not found (should be there)
                    val = detection_period.get(key, detection_period.get('1', 0.2)) 
                    step = max(1, int(val * fps))
                else:
                    # Legacy float support (should not happen with new main.py but good safety)
                    step = max(1, int(detection_period * fps))
            elif current_num_faces_state == 2:
                step = int(1.0 * fps) # 1s for 2 faces
            else:
                step = 5 # 5 frames for 1 face (~0.16s at 30fps)
            
            if holding_multi:
                # A face is missing inside the grace window: re-check quickly
                # (~0.2s) instead of waiting the full 2-face period (~1s) so
                # recovery is snappy.
                step = min(step, max(1, int(0.2 * fps)))

            next_detection_frame = frame_index + step

        if len(transition_frames) > 0:
            current_faces = transition_frames[0]
            transition_frames = transition_frames[1:]
        elif last_detected_faces is not None and (frame_index - last_success_frame) <= max_frames_without_detection:
            current_faces = last_detected_faces
        else:
            # Fallback for this frame
            if no_face_mode == "zoom":
                result = crop_center_zoom(frame)
            else:
                result = resize_with_padding(frame)
            out.write(result)
            timeline_frames.append((frame_index, "1")) # Fix: Ensure fallback is treated as single face for subs
            
            # Fix XML Log sync (Empty faces for fallback)
            coords_entry = {"frame": frame_index, "src_size": [frame_width, frame_height], "faces": []}
            coordinate_log.append(coords_entry)
            
            continue

        # v7.27: EMA-smooth EVERY active face slot (single AND multi-face
        # layouts). Slots keep identity through the proximity/area ordering of
        # the detection step, so slot i tracks the same physical face across
        # cycles; the dead zone already ignores sub-pixel noise.
        if smoothing > 0 and len(current_faces) > 0:
            smoothing_total += 1
            current_faces, smoothed_slots = smooth_boxes_per_slot(
                current_faces, face_smoothers, smoothing, smoothed_slots,
                frame_width, frame_height)
            if smoothed_slots > 0:
                smoothing_frames += 1
        else:
            for smoother in face_smoothers:
                smoother.reset()

        last_frame_face_positions = current_faces
        
        target_len = len(current_faces)
        
        if target_len >= 2:
             frame_2_face_count += 1
             if target_len > 2:
                 frame_multi_face_count += 1
             rects = [
                 (f[0], f[1], f[2] - f[0], f[3] - f[1])
                 for f in current_faces[:4]
             ]
             result = crop_and_resize_multi_faces(
                 frame, rects, layout="auto", max_faces=4,
             )
             timeline_frames.append((frame_index, str(min(target_len, 4))))
        else:
             frame_1_face_count += 1
             result = crop_and_resize_insightface(frame, current_faces[0],
                                                  headroom=headroom)
             timeline_frames.append((frame_index, "1"))
             
        # Capture Coordinates (Frame-by-Frame)
        coords_entry = {"frame": frame_index, "src_size": [frame_width, frame_height], "faces": []}
        try:
            # We want to store [x1, y1, x2, y2, rh] for each face
            if isinstance(current_faces, (list, tuple)):
                processed_faces_log = []
                for f in current_faces:
                    f_list = list(map(int, f[:4])) # Standard bbox
                    # Calculate rh (relative height)
                    face_h = f_list[3] - f_list[1]
                    rh = face_h / float(frame_height)
                    f_list.append(float(f"{rh:.4f}")) # Append as 5th element
                    processed_faces_log.append(f_list)
                coords_entry["faces"] = processed_faces_log
                
            elif isinstance(current_faces, np.ndarray):
                # Similar logic for numpy
                processed_faces_log = []
                for f in current_faces:
                    f_list = f[:4].astype(int).tolist()
                    face_h = f_list[3] - f_list[1]
                    rh = face_h / float(frame_height)
                    f_list.append(float(f"{rh:.4f}"))
                    processed_faces_log.append(f_list)
                coords_entry["faces"] = processed_faces_log
        except: pass
        coordinate_log.append(coords_entry)

        out.write(result)

    cap.release()
    out.release()
    
    # Compress timeline into segments
    # [(start_time, end_time, mode), ...]
    compressed_timeline = []
    if timeline_frames:
        curr_mode = timeline_frames[0][1]
        start_f = timeline_frames[0][0]
        
        for i in range(1, len(timeline_frames)):
            frame_idx, mode = timeline_frames[i]
            if mode != curr_mode:
                # End current segment
                # Convert frame to seconds
                end_f = timeline_frames[i-1][0]
                compressed_timeline.append({
                    "start": float(start_f) / fps,
                    "end": float(end_f) / fps, # or frame_idx / fps for continuity
                    "mode": curr_mode
                })
                # Start new
                curr_mode = mode
                start_f = frame_idx
        
        # Add last
        end_f = timeline_frames[-1][0]
        compressed_timeline.append({
             "start": float(start_f) / fps,
             "end": (float(end_f) + 1) / fps,
             "mode": curr_mode
        })
    
    # Save timeline JSON
    timeline_file = output_file.replace(".mp4", "_timeline.json")
    try:
        import json
        with open(timeline_file, "w") as f:
            json.dump(compressed_timeline, f)
        print(f"Timeline saved: {timeline_file}")
    except Exception as e:
        print(f"Error saving timeline: {e}")

    # Save Coords JSON
    coords_file = output_file.replace(".mp4", "_coords.json")
    try:
        with open(coords_file, "w") as f:
            json.dump(coordinate_log, f)
        print(f"Face Coordinates saved: {coords_file}")
    except Exception as e:
        print(f"Error saving coords: {e}")

    if not finalize_video(input_file, output_file, index, fps, project_folder, final_folder):
        raise RuntimeError(f"Could not finalize InsightFace clip {index}: audio/mux validation failed")
    
    # Return dominant mode while keeping the historical 15% threshold.
    mode_counts = {}
    for _, mode in timeline_frames:
        try:
            mode_int = int(mode)
        except (TypeError, ValueError):
            continue
        mode_counts[mode_int] = mode_counts.get(mode_int, 0) + 1
    if mode_counts:
        dominant_mode = max(mode_counts, key=mode_counts.get)
        if dominant_mode > 1 and mode_counts[dominant_mode] > (total_frames * 0.15):
            return str(min(dominant_mode, 4))
    return "1"


def edit(project_folder="tmp", face_model="insightface", face_mode="auto", detection_period=None, filter_threshold=0.35, two_face_threshold=0.60, confidence_threshold=0.30, dead_zone=40, focus_active_speaker=False, active_speaker_mar=0.03, active_speaker_score_diff=1.5, include_motion=False, active_speaker_motion_deadzone=3.0, active_speaker_motion_sensitivity=0.05, active_speaker_decay=2.0, segments_data=None, no_face_mode="padding", smoothing=0.55, headroom=0.12):
    # Lazy init solutions only when needed to avoid AttributeError if import failed partially
    mp_face_detection = None
    mp_face_mesh = None
    mp_pose = None
    
    index = 0
    cuts_folder = os.path.join(project_folder, "cuts")
    final_folder = os.path.join(project_folder, "final")
    os.makedirs(final_folder, exist_ok=True)
    
    face_modes_log = {}
    
    # Priority: User Choice -> Fallbacks
    
    insightface_working = False
    
    # Only init InsightFace if selected or default
    if INSIGHTFACE_AVAILABLE and (face_model == "insightface"):
        try:
            print("Initializing InsightFace...")
            init_insightface()
            insightface_working = True
            print("InsightFace Initialized Successfully.")
        except Exception as e:
            print(f"WARNING: InsightFace Initialization Failed ({e}). Will try MediaPipe.")
            insightface_working = False

    mediapipe_working = False
    use_haar = False
    
    # If insightface failed OR user chose mediapipe, init mediapipe
    should_use_mediapipe = (face_model == "mediapipe") or (face_model == "insightface" and not insightface_working)
    
    if should_use_mediapipe:
        try:
            # Check if solutions is available (it might not be if import failed silently or partial)
            if not hasattr(mp, 'solutions'):
                raise ImportError("mediapipe.solutions not found")
                
            mp_face_detection = mp.solutions.face_detection
            mp_face_mesh = mp.solutions.face_mesh
            mp_pose = mp.solutions.pose
            
            # Try to init with model_selection=0 (Short Range) as a smoketest
            with mp_face_detection.FaceDetection(model_selection=0, min_detection_confidence=0.5):
                pass
            mediapipe_working = True
            print("MediaPipe Initialized Successfully.")
        except Exception as e:
            print(f"WARNING: MediaPipe Initialization Failed ({e}). Switching to OpenCV Haar Cascade.")
            mediapipe_working = False
            use_haar = True
    
    # Logic for MediaPipe replaced by dynamic pass
    # mp_num_faces = 2 if face_mode == "2" else 1  

    import glob
    found_files = sorted(glob.glob(os.path.join(cuts_folder, "*_original_scale.mp4")))

    if not found_files:
        print(f"No files found in {cuts_folder}.")
        # Try finding lookahead in case listdir failed? No, glob is fine.
        return

    for input_file in found_files:
        input_filename = os.path.basename(input_file)
        
        # Extract Index
        index = 0
        try:
             parts = input_filename.split('_')
             if parts[0].isdigit(): index = int(parts[0])
             elif input_filename.startswith("output"): # output000
                 idx_str = input_filename[6:9]
                 if idx_str.isdigit(): index = int(idx_str)
        except: pass
        
        output_file = os.path.join(final_folder, f"temp_video_no_audio_{index}.mp4")

        # Determine Final Name (Title)
        base_name_final = input_filename.replace("_original_scale.mp4", "")
        # If legacy name, try to improve it
        if input_filename.startswith("output") and segments_data and index < len(segments_data):
             title = segments_data[index].get("title", f"Segment_{index}")
             safe_title = "".join([c for c in title if c.isalnum() or c in " _-"]).strip().replace(" ", "_")[:60]
             base_name_final = f"{index:03d}_{safe_title}"

        if os.path.exists(input_file):
            success = False
            detected_mode = "1" # Default if detection fails or fallback

            # 1. Try InsightFace
            if insightface_working:
                try:
                    # Capture returned mode
                    res = generate_short_insightface(input_file, output_file, index, project_folder, final_folder, face_mode=face_mode, detection_period=detection_period, 
                                                     filter_threshold=filter_threshold, two_face_threshold=two_face_threshold, confidence_threshold=confidence_threshold, dead_zone=dead_zone, focus_active_speaker=focus_active_speaker,
                                                     active_speaker_mar=active_speaker_mar, active_speaker_score_diff=active_speaker_score_diff, include_motion=include_motion,
                                                     active_speaker_motion_deadzone=active_speaker_motion_deadzone,
                                                     active_speaker_motion_sensitivity=active_speaker_motion_sensitivity,
                                                     active_speaker_decay=active_speaker_decay,
                                                     no_face_mode=no_face_mode,
                                                     smoothing=smoothing, headroom=headroom)
                    if res: detected_mode = res
                    success = True
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    print(f"InsightFace processing failed for {input_filename}: {e}")
                    print("Falling back to MediaPipe/Haar...")
            
            # 2. Try MediaPipe if InsightFace failed or not available
            if not success and mediapipe_working:
                try:
                    with mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.2) as face_detection, \
                         mp_face_mesh.FaceMesh(static_image_mode=False, max_num_faces=2, refine_landmarks=True, min_detection_confidence=0.2, min_tracking_confidence=0.2) as face_mesh, \
                         mp_pose.Pose(static_image_mode=False, min_detection_confidence=0.5, min_tracking_confidence=0.5) as pose:
                        
                        generate_short_mediapipe(input_file, output_file, index, face_mode, project_folder, final_folder, face_detection, face_mesh, pose, detection_period=detection_period, no_face_mode=no_face_mode)
                        # We don't easily know detected mode here without return, assuming '1' or '2' based on last frame? 
                        # Ideally function should return as well.
                        detected_mode = "1" # Placeholder, user didn't complain about stats.
                        # detected_mode = str(mp_num_faces) # Error fix: mp_num_faces not defined
                        if face_mode == "2":
                            detected_mode = "2"
                    success = True
                except Exception as e:
                     print(f"MediaPipe processing failed (fallback): {e}")
            
            # 3. Try Haar if others failed
            if not success and (use_haar or (not mediapipe_working and not insightface_working)):
                 try:
                    print("Attempts with Haar Cascade...")
                    generate_short_haar(input_file, output_file, index, project_folder, final_folder, detection_period=detection_period, no_face_mode=no_face_mode)
                    success = True
                 except Exception as e2:
                    print(f"Haar fallback also failed: {e2}")

            # 4. Last Resort: Center Crop
            if not success:
                generate_short_fallback(input_file, output_file, index, project_folder, final_folder, no_face_mode=no_face_mode)
                detected_mode = "1"
                success = True
            
            # Save mode
            face_modes_log[f"output{str(index).zfill(3)}"] = detected_mode

        if success:
             try:
                 new_mp4_name = f"{base_name_final}.mp4"
                 new_mp4_path = os.path.join(final_folder, new_mp4_name)
                 
                 # Source is what finalize_video created
                 # finalize_video creates `final-output{index}_processed.mp4`
                 generated_mp4_name = f"final-output{str(index).zfill(3)}_processed.mp4"
                 generated_mp4_path = os.path.join(final_folder, generated_mp4_name)
                 
                 # 1. Rename MP4
                 if os.path.exists(generated_mp4_path):
                     if os.path.exists(new_mp4_path): os.remove(new_mp4_path)
                     os.rename(generated_mp4_path, new_mp4_path)
                     print(f"Renamed Output to Title: {new_mp4_name}")
                     
                     # 2. Rename JSON Subtitle (if exists and hasn't been renamed by cut_segments)
                     subs_folder = os.path.join(project_folder, "subs")
                     
                     # Check if legacy name exists
                     old_json_name = f"final-output{str(index).zfill(3)}_processed.json"
                     old_json_path = os.path.join(subs_folder, old_json_name)
                     
                     new_json_name = f"{base_name_final}_processed.json"
                     new_json_path = os.path.join(subs_folder, new_json_name)
                     
                     if os.path.exists(old_json_path):
                         if os.path.exists(new_json_path): os.remove(new_json_path)
                         os.rename(old_json_path, new_json_path)
                         print(f"Renamed Subtitles to Title: {new_json_name}")
                         
                     # 3. Rename Timeline JSON
                     # Timeline is temp_video_no_audio_{index}_timeline.json (created by generate_short...)
                     old_timeline_name = f"temp_video_no_audio_{index}_timeline.json"
                     old_timeline_path = os.path.join(final_folder, old_timeline_name)
                     
                     new_timeline_name = f"{base_name_final}_timeline.json"
                     new_timeline_path = os.path.join(final_folder, new_timeline_name)
                     
                     if os.path.exists(old_timeline_path):
                         if os.path.exists(new_timeline_path): os.remove(new_timeline_path)
                         os.rename(old_timeline_path, new_timeline_path)
                         print(f"Renamed Timeline to Title: {new_timeline_name}")
                         
                     # 4. Rename Coords JSON
                     old_coords_name = f"temp_video_no_audio_{index}_coords.json"
                     old_coords_path = os.path.join(final_folder, old_coords_name)
                     
                     new_coords_name = f"{base_name_final}_coords.json"
                     new_coords_path = os.path.join(final_folder, new_coords_name)
                     
                     if os.path.exists(old_coords_path):
                         if os.path.exists(new_coords_path): os.remove(new_coords_path)
                         os.rename(old_coords_path, new_coords_path)
                         print(f"Renamed Coords to Title: {new_coords_name}")
                         
             except Exception as e:
                 print(f"Warning: Could not rename file with title: {e}") 
        
    # Save a durable explanation of which tracking backend actually ran.
    tracking_file = os.path.join(project_folder, "tracking_report.json")
    tracking_report = {
        "requested_active_speaker": bool(focus_active_speaker),
        "requested_face_model": str(face_model),
        "backend": "insightface" if insightface_working else ("mediapipe" if mediapipe_working else "haar"),
        "active_speaker_applied": bool(focus_active_speaker and insightface_working),
        "face_tracking_applied": bool(insightface_working or mediapipe_working),
        "smoothing": smoothing,
        "headroom": headroom,
        "status": "active" if focus_active_speaker and insightface_working else ("face_tracking_only" if focus_active_speaker else "not_requested"),
        "warning": None if not focus_active_speaker or insightface_working else "Active speaker needs InsightFace; fallback backend only stabilizes face tracking.",
    }
    try:
        with open(tracking_file, "w", encoding="utf-8") as stream:
            json.dump(tracking_report, stream, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"Warning: could not save tracking report: {exc}")

    # Save Face Modes to JSON for subtitle usage
    modes_file = os.path.join(project_folder, "face_modes.json")
    try:
        with open(modes_file, "w") as f:
            json.dump(face_modes_log, f)
        print(f"Detect Stats saved: {modes_file}")
    except Exception as e:
        print(f"Error saving face modes: {e}")

if __name__ == "__main__":
    edit()