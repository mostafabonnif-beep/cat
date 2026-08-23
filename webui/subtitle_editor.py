import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

# Import scripts for direct processing
import scripts.adjust_subtitles as adjust
import scripts.burn_subtitles as burn


# Helper to format seconds to HH:MM:SS,mmm
def format_timestamp(seconds):
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value):
        value = 0.0
    total_millis = max(0, int(round(value * 1000)))
    whole_seconds, millis = divmod(total_millis, 1000)
    mins, secs = divmod(whole_seconds, 60)
    hrs, mins = divmod(mins, 60)
    return f"{hrs:02}:{mins:02}:{secs:02},{millis:03}"

def _atomic_write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# Helper to parse HH:MM:SS,mmm back to seconds
def parse_timestamp(ts_str):
    try:
        # Handle different formats just in case
        ts_str = ts_str.replace(',', '.')
        parts = ts_str.split(':')
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return 0.0
    except:
        return 0.0

def load_transcription_for_editor(json_path):
    """
    Loads `final-outputXXX_processed.json` and flattens it for the Dataframe editor.
    Returns a list of lists: [[Start, End, Text], ...]
    """
    if not os.path.exists(json_path):
        return []

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        segments = data.get('segments', [])
        editor_data = [] # List of [Start, End, Text]

        # We display segments. Each segment has 'words'. 
        # But users want to edit at segment level (the full sentence).
        for seg in segments:
            start_fmt = format_timestamp(seg.get('start', 0))
            end_fmt = format_timestamp(seg.get('end', 0))
            text = seg.get('text', '').strip()
            editor_data.append([start_fmt, end_fmt, text])
            
        return editor_data
    except Exception as e:
        print(f"Error loading JSON for editor: {e}")
        return []

def resolve_video_candidate(json_full_path):
    if not os.path.exists(json_full_path):
        return None
    project_folder = os.path.dirname(os.path.dirname(json_full_path))
    filename = os.path.basename(json_full_path)
    base_name = os.path.splitext(filename)[0]
    video_folder = os.path.join(project_folder, "final")
    candidates = [
        os.path.join(video_folder, f"{base_name}.mp4"),
        os.path.join(video_folder, f"{base_name.replace('_processed', '')}.mp4"),
        os.path.join(project_folder, "burned_sub", f"{base_name}_subtitled.mp4"),
        os.path.join(project_folder, "burned_sub", f"{base_name.replace('_processed', '')}_subtitled.mp4"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    if os.path.exists(video_folder):
        match = re.search(r"(\d{3})", base_name)
        if match:
            idx = match.group(1)
            for folder in [os.path.join(project_folder, "burned_sub"), video_folder]:
                if os.path.exists(folder):
                    for f in sorted(os.listdir(folder)):
                        if f.endswith(".mp4") and idx in f:
                            return os.path.join(folder, f)
    return None


def build_preview_clip(json_full_path, row_index):
    if not os.path.exists(json_full_path):
        return None, "JSON not found."
    try:
        with open(json_full_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        segments = data.get('segments', [])
        if row_index < 0 or row_index >= len(segments):
            return None, "Row out of range."
        seg = segments[row_index]
        start_sec = parse_timestamp(format_timestamp(seg.get('start', 0)).replace(',', '.'))
        end_sec = parse_timestamp(format_timestamp(seg.get('end', 0)).replace(',', '.'))
        if end_sec <= start_sec:
            end_sec = start_sec + 1.5
        source_video = resolve_video_candidate(json_full_path)
        if not source_video:
            return None, "Source video not found."
        project_folder = os.path.dirname(os.path.dirname(json_full_path))
        preview_dir = os.path.join(project_folder, "preview_clips")
        os.makedirs(preview_dir, exist_ok=True)
        key = hashlib.md5(f"{json_full_path}|{row_index}|{start_sec}|{end_sec}|{os.path.getmtime(source_video)}".encode()).hexdigest()[:12]
        preview_path = os.path.join(preview_dir, f"preview_{key}.mp4")
        if not os.path.exists(preview_path):
            duration = max(0.5, end_sec - start_sec)
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{start_sec:.3f}", "-i", source_video,
                "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-movflags", "+faststart",
                preview_path,
            ]
            subprocess.run(cmd, check=True)
        return preview_path, f"Preview ready: row {row_index + 1}"
    except Exception as e:
        return None, f"Preview error: {e}"


def save_editor_changes(json_path, new_data):
    """
    Reconstructs the complex JSON from the simplified Dataframe edits.
    Smartly redistributes word timestamps if text content changed.
    """
    if not os.path.exists(json_path):
        return "Error: Original file not found."

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            original_json = json.load(f)
        
        original_segments = original_json.get('segments', [])
        
        # new_data is list of [Start, End, Text] from Dataframe
        
        updated_segments = []
        
        for i, row in enumerate(new_data or []):
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                return "Error: Invalid subtitle row."
            start_str, end_str, new_text = row[:3]
            start_sec = parse_timestamp(start_str)
            end_sec = parse_timestamp(end_str)
            if start_sec < 0 or end_sec <= start_sec:
                return "Error: Subtitle timing must have end > start."
            new_text = str(new_text or "").strip()
            
            # Get original segment to recycle word timings if possible
            if i < len(original_segments):
                orig_seg = original_segments[i]
                orig_words = orig_seg.get('words', [])
            else:
                orig_seg = {}
                orig_words = []
            
            # 1. Update Segment Level
            new_segment = {
                "start": start_sec,
                "end": end_sec,
                "text": new_text
            }
            
            # 2. Reconstruct Words
            # Split new text into words
            new_word_list = new_text.split()
            reconstructed_words = []
            
            if not new_word_list:
                updated_segments.append({**new_segment, "words": []})
                continue

            # Strategy:
            # - If word count matches exactly, assign original timings 1:1.
            # - If mismatch, distribute time proportionally.
            
            if len(new_word_list) == len(orig_words):
                 # Easy mode: Just replace the "word" text, keep timing
                 for j, w_text in enumerate(new_word_list):
                     orig_w = orig_words[j]
                     reconstructed_words.append({
                         "word": w_text,
                         "start": orig_w.get("start", start_sec),
                         "end": orig_w.get("end", end_sec),
                         "score": orig_w.get("score", 0.99)
                     })
            else:
                # Hard mode: Linear Interpolation
                duration = end_sec - start_sec
                if duration <= 0: duration = 0.1
                
                word_duration = duration / len(new_word_list)
                
                current_time = start_sec
                for w_text in new_word_list:
                    w_end = current_time + word_duration
                    reconstructed_words.append({
                        "word": w_text,
                        "start": round(current_time, 3),
                        "end": round(w_end, 3),
                        "score": 0.99
                    })
                    current_time = w_end
            
            new_segment["words"] = reconstructed_words
            updated_segments.append(new_segment)
            
        # Update final JSON structure
        original_json["segments"] = updated_segments

        # Save through an atomic replacement so a disk interruption cannot
        # destroy the only editable subtitle source.
        _atomic_write_json(json_path, original_json)
        return "Success: Subtitles updated."

        
    except Exception as e:
        return f"Error saving changes: {e}"

def list_editable_files(project_dir):
    """
    Scans VIRALS/{project_name}/subs/ for json files.
    """
    if not os.path.exists(project_dir):
        return []
    
    subs_dir = os.path.join(project_dir, 'subs')
    if not os.path.exists(subs_dir):
        return []
        
    # Look for files matching 'final-output...processed.json'
    files = [f for f in os.listdir(subs_dir) if f.endswith('_processed.json')]
    return sorted(files)

def export_all_segments(project_folder):
    if not project_folder or not os.path.exists(project_folder):
        return "Project not found."
    subs_dir = Path(project_folder) / "subs"
    if not subs_dir.exists():
        return "No subtitles folder found."
    json_files = sorted(subs_dir.glob("*_processed.json"))
    if not json_files:
        return "No segment subtitles found to export."
    results = []
    for json_file in json_files:
        match = re.search(r"(\d{3})", json_file.stem)
        if not match:
            continue
        idx = int(match.group(1))
        try:
            from scripts.export_xml_lib.exporter import export_pack
            zip_path = export_pack(project_folder, idx, "premiere")
            if zip_path:
                results.append(zip_path)
        except Exception as e:
            results.append(f"{json_file.name}: {e}")
    return results or "Nothing exported."

def render_specific_video(json_full_path):
    """
    1. Regenerate ASS for this specific JSON file.
    2. Burn ASS into the corresponding Video file.
    """
    if not json_full_path or not os.path.exists(json_full_path):
        return "Error: JSON file not found."

    project_folder = os.path.dirname(os.path.dirname(json_full_path)) # ../../ from subs/file.json
    
    # Identify key paths
    filename = os.path.basename(json_full_path)
    base_name = os.path.splitext(filename)[0] # final-output000_processed
    
    # Assuming standard structure
    ass_path = os.path.join(project_folder, "subs_ass", f"{base_name}.ass")
    os.makedirs(os.path.dirname(ass_path), exist_ok=True)
    
    # Video Path?
    # burn_subtitles iterates 'final' folder and matches name.
    # The JSON is "final-output000_processed.json".
    # The video in 'final' usually is "fina-output000.mp4" or similar?
    # Wait, edit_video generates "final-output000_processed.mp4"?
    # Let's assume the name matches exactly the JSON name.
    
    # Try finding the video file
    video_folder = os.path.join(project_folder, "final")
    video_candidate = os.path.join(video_folder, f"{base_name}.mp4")
    
    if not os.path.exists(video_candidate):
        # Try stripping "_processed" (common suffix for subtitle files)
        if base_name.endswith("_processed"):
             clean_name = base_name.replace("_processed", "")
             candidate_2 = os.path.join(video_folder, f"{clean_name}.mp4")
             if os.path.exists(candidate_2):
                 video_candidate = candidate_2
        
        # If still not found, try regex strategies
        if not os.path.exists(video_candidate):
            # Strategy A: 'output123' pattern
            match = re.search(r"output(\d+)", base_name)
            
            # Strategy B: '000_Name' pattern (digits at start)
            if not match:
                match = re.search(r"^(\d+)_", base_name)
            
            if match:
                vid_id = match.group(1)
                # Look for file containing this ID
                files = os.listdir(video_folder)
                found = None
                for f in files:
                    # Match ID in filename (either outputID or ID_Name)
                    # We check if 'output{vid_id}' or '{vid_id}_' is in the file
                    # Be careful not to match '100' with '00'
                    if (f"output{vid_id}" in f or f.startswith(f"{vid_id}_")) and f.endswith(".mp4") and "subtitled" not in f:
                         found = os.path.join(video_folder, f)
                         break
                if found:
                    video_candidate = found
                else:
                    return f"Error: Could not find video file for ID {vid_id} (from {base_name}) in {video_folder}"
            else:
                 return f"Error: Could not determine video ID from {base_name}"
    
    # Output path
    burned_folder = os.path.join(project_folder, "burned_sub")
    os.makedirs(burned_folder, exist_ok=True)
    output_video_path = os.path.join(burned_folder, f"{base_name}_subtitled.mp4")

    # Load Config
    try:
        # Try to load temp config from root, else default
        # .. from VIRALS/proj -> VIRALS -> root? No.
        # project_folder is VIRALS/proj.
        # root is ../../
        root_dir = os.path.dirname(os.path.dirname(project_folder))
        # actually project_folder is c:\...\VIRALS\proj.
        # root is c:\...\
        
        # Safer: use main_improved working dir if imported from there or app
        config_path = os.path.join(root_dir, "temp_subtitle_config.json")
        if not os.path.exists(config_path):
             config_path = None
        
        import main_improved  # lazy: heavy import, only when needed (v6.7)
        config = main_improved.get_subtitle_config(config_path)
        # print(f"DEBUG: Loaded subt config: H={config.get('highlight_color')} B={config.get('base_color')}")
        # Ensure 'uppercase' exists as it's not in default config of main_improved
        config['uppercase'] = config.get('uppercase', False)
        
        # Load Face Modes
        face_modes = {}
        modes_file = os.path.join(project_folder, "face_modes.json")
        if os.path.exists(modes_file):
            with open(modes_file, "r") as f:
                face_modes = json.load(f)
        
        # 1. Generate ASS
        adjust.generate_ass_from_file(json_full_path, ass_path, project_folder, **config, face_modes=face_modes)
        
        # 2. Burn Video
        success, msg = burn.burn_video_file(video_candidate, ass_path, output_video_path)
        
        if success:
             return f"Success! Rendered: {os.path.basename(output_video_path)}"
        else:
             return f"Render Failed: {msg}"

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Critical Error: {e}"
