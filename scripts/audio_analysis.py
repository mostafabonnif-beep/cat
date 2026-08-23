import subprocess

import numpy as np


def get_audio_energy(video_path, fps, window_size_sec=0.1):
    """
    Extract robust, normalized audio energy mapped to frame indices.

    A percentile-based noise floor is used instead of dividing by one absolute
    peak, because a click or a music transient should not make all speech look
    silent to the active-speaker tracker.
    """
    # Extract raw PCM data (16-bit LE, mono, 16000Hz)
    cmd = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-i', video_path,
        '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'
    ]
    
    try:
        if not fps or float(fps) <= 0:
            return None
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        raw_audio, _ = process.communicate()

        if process.returncode not in (0, None) or not raw_audio:
            return None

        audio_data = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32)

        # Calculate samples per frame
        samples_per_second = 16000
        samples_per_frame = samples_per_second / float(fps)
        
        total_frames = int(len(audio_data) / samples_per_frame)
        energies = np.zeros(total_frames)
        
        # Window size in samples
        window_samples = int(window_size_sec * samples_per_second)
        
        for i in range(total_frames):
            center_sample = int(i * samples_per_frame)
            start = max(0, center_sample - window_samples // 2)
            end = min(len(audio_data), center_sample + window_samples // 2)
            
            if start < end:
                chunk = audio_data[start:end]
                # RMS Energy
                rms = np.sqrt(np.mean(chunk**2))
                energies[i] = rms
        
        # Robust normalization: remove a low percentile noise floor and use a
        # high percentile as speech scale, avoiding one transient dominating.
        if len(energies) > 0:
            noise_floor = float(np.percentile(energies, 15))
            speech_scale = float(np.percentile(energies, 95))
            if speech_scale <= noise_floor:
                speech_scale = float(np.max(energies))
            span = max(speech_scale - noise_floor, 1e-6)
            energies = np.clip((energies - noise_floor) / span, 0.0, 1.0)

        return energies
        
    except Exception as e:
        print(f"[audio] Error extracting energy: {e}")
        return None
