import json
import os


def _clamp_time(value):
    try:
        return max(0.0, float(value))
    except Exception:
        return 0.0


def process_segments(data, start_time, end_time):
    new_segments = []
    start_time = _clamp_time(start_time)
    end_time = max(start_time, _clamp_time(end_time))

    for segment in data.get('segments', []):
        seg_start = _clamp_time(segment.get('start', 0))
        seg_end = _clamp_time(segment.get('end', 0))

        if seg_end <= start_time or seg_start >= end_time:
            continue

        new_seg_start = max(0, seg_start - start_time)
        new_seg_end = min(end_time, seg_end) - start_time

        new_words = []
        if 'words' in segment:
            for word in segment['words']:
                w_start = _clamp_time(word.get('start', 0))
                w_end = _clamp_time(word.get('end', 0))
                if w_end > start_time and w_start < end_time:
                    new_w_start = max(0, w_start - start_time)
                    new_w_end = min(end_time, w_end) - start_time
                    word_copy = word.copy()
                    word_copy['start'] = new_w_start
                    word_copy['end'] = new_w_end
                    new_words.append(word_copy)

        if new_words or (new_seg_end > new_seg_start):
            new_segment = segment.copy()
            new_segment['start'] = new_seg_start
            new_segment['end'] = new_seg_end
            if 'words' in segment:
                new_segment['words'] = new_words
            new_segments.append(new_segment)

    return {'segments': new_segments}


def cut_json_transcript(input_json_path, output_json_path, start_time, end_time):
    """Read WhisperX JSON and write a clipped version with adjusted timestamps."""
    if not os.path.exists(input_json_path):
        print(f"Warning: {input_json_path} not found. Unable to generate cut JSON.")
        return

    try:
        with open(input_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        new_data = process_segments(data, start_time, end_time)

        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, indent=2, ensure_ascii=False)

        print(f"Subtitle JSON generated: {output_json_path}")
    except Exception as e:
        print(f"Error cutting JSON: {e}")
