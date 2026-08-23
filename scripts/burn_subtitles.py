import os
import subprocess


def _detect_best_encoder():
    try:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
        output = result.stdout or ""
        if "h264_nvenc" in output:
            return "h264_nvenc", "p1"
        if "h264_amf" in output:
            return "h264_amf", "quality"
        if "h264_qsv" in output:
            return "h264_qsv", "veryfast"
        if "h264_videotoolbox" in output:
            return "h264_videotoolbox", "default"
    except Exception:
        pass
    return "libx264", "ultrafast"


def _fonts_dir():
    """Absolute path of the bundled fonts/ dir (repo root), or None."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fonts_dir = os.path.join(root, "fonts")
    return fonts_dir if os.path.isdir(fonts_dir) else None


def _ffmpeg_filter_value(path):
    """Escape a path for use inside an ffmpeg filtergraph value.

    The filtergraph parser treats `:` as an option separator and `'` as a
    quote character — a raw one in a project/asset path silently breaks or
    re-scopes the filter. Backslashes are converted to forward slashes
    first (Windows paths).
    """
    return (path or "").replace("\\", "/").replace("'", "\\'").replace(":", "\\:")


def _subtitles_filter(subtitle_path):
    """Build the ffmpeg `subtitles=` filter string.

    Adds `:fontsdir=<fonts/>` when the bundled Montserrat fonts are
    present, so the Hormozi-style subtitle font resolves even when the
    font is NOT installed system-wide (v6.9.1 — before, ffmpeg silently
    substituted a default font and the videos looked off-brand).
    """
    subtitle_file_ffmpeg = _ffmpeg_filter_value(subtitle_path)
    vf = "subtitles='{}'".format(subtitle_file_ffmpeg)
    fonts_dir = _fonts_dir()
    if fonts_dir:
        fonts_dir_ffmpeg = _ffmpeg_filter_value(fonts_dir)
        vf += ":fontsdir='{}'".format(fonts_dir_ffmpeg)
    return vf


def burn_video_file(video_path, subtitle_path, output_path, prefer_hardware_acceleration=None):
    """Burn subtitles into a single video file with safe fallback handling."""
    def run_ffmpeg(encoder, preset, additional_args=None):
        additional_args = additional_args or []
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-hide_banner",
            "-i", video_path,
            "-vf", _subtitles_filter(subtitle_path),
            "-c:v", encoder,
            "-preset", preset,
            "-b:v", "5M",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            output_path,
        ] + additional_args
        subprocess.run(cmd, check=True, capture_output=True)

    try:
        if prefer_hardware_acceleration is False:
            raise subprocess.CalledProcessError(1, ["ffmpeg"])

        encoder, preset = _detect_best_encoder()
        if encoder == "libx264":
            raise subprocess.CalledProcessError(1, ["ffmpeg"])
        run_ffmpeg(encoder, preset)
        return True, f"{encoder} Success"
    except subprocess.CalledProcessError as e:
        print(f"Hardware encoder failed ({str(e)}). Trying CPU libx264...")
        try:
            run_ffmpeg("libx264", "ultrafast")
            return True, "CPU Success"
        except subprocess.CalledProcessError as e2:
            err_msg = f"Fatal error burning subtitles for {os.path.basename(video_path)}: {e2}"
            if getattr(e2, 'stderr', None):
                err_msg += f" | FFmpeg Log: {e2.stderr.decode('utf-8', errors='replace')}"
            print(err_msg)
            return False, err_msg
    except Exception as e:
        return False, str(e)


def burn(project_folder="tmp", prefer_hardware_acceleration=None):
    if project_folder and not os.path.isabs(project_folder):
        project_folder_abs = os.path.abspath(project_folder)
    else:
        project_folder_abs = project_folder

    subs_folder = os.path.join(project_folder_abs, 'subs_ass')
    polished_folder = os.path.join(project_folder_abs, 'final_polished')
    videos_folder = os.path.join(project_folder_abs, 'final')
    if os.path.isdir(polished_folder) and any(f.endswith(('.mp4', '.mkv', '.avi')) for f in os.listdir(polished_folder)):
        videos_folder = polished_folder  # prefer the polished pass (jump cuts / zoom / music / branding)
    output_folder = os.path.join(project_folder_abs, 'burned_sub')

    os.makedirs(output_folder, exist_ok=True)

    if not os.path.exists(videos_folder):
        print(f"Final video folder not found: {videos_folder}")
        return

    files = os.listdir(videos_folder)
    if not files:
        print("No files found in final folder for subtitle burning.")
        return

    for video_file in files:
        if video_file.endswith(('.mp4', '.mkv', '.avi')):
            if "temp_video_no_audio" in video_file:
                continue

            video_name = os.path.splitext(video_file)[0]
            subtitle_file = os.path.join(subs_folder, f"{video_name}.ass")
            if not os.path.exists(subtitle_file):
                subtitle_file_processed = os.path.join(subs_folder, f"{video_name}_processed.ass")
                if os.path.exists(subtitle_file_processed):
                    subtitle_file = subtitle_file_processed

            if os.path.exists(subtitle_file):
                output_file = os.path.join(output_folder, f"{video_name}_subtitled.mp4")
                print(f"Burning: {video_name}...")
                success, msg = burn_video_file(os.path.join(videos_folder, video_file), subtitle_file, output_file, prefer_hardware_acceleration=prefer_hardware_acceleration)
                if success:
                    print(f"Done: {output_file}")
                else:
                    print(f"Fail: {msg}")
            else:
                print(f"Subtitle not found for: {video_name} at {subtitle_file}")
