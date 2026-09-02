import ast
import hashlib
import io
import json
import os
import re
import sys
import time

# Configura stdout para evitar erros de encoding no Windows (substitui caracteres inválidos por ?)
# Aplicado apenas no Windows — em Linux/macOS (e no CI/pytest) o stdout nativo já é UTF-8
# e substituí-lo quebraria o capture do pytest.
if sys.platform == "win32" and sys.stdout and hasattr(sys.stdout, 'buffer'):
    try:
        # Mantém encoding original mas ignora erros (substitui por ?)
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding=sys.stdout.encoding or 'utf-8', errors='replace', line_buffering=True)
    except:
        pass

# Tenta importar bibliotecas de IA opcionalmente
# Gemini SDK support: legacy `google.generativeai` (pip install
# google-generativeai) OR the new `google.genai` (pip install google-genai).
# v6.4: previously only the legacy import existed while requirements.txt listed
# the new package → runtime ImportError. Now either library works.
# importlib.import_module is used instead of `import google.X` so a module
# already present in sys.modules resolves even when the parent `google`
# namespace package is not installed (keeps tests/hermetic envs working).
import importlib

try:
    # Prefer the maintained unified SDK. The legacy package is only used when
    # google-genai is unavailable, which avoids its deprecation warning on
    # normal installations while preserving older environments.
    genai = importlib.import_module("google.genai")
    HAS_GEMINI = True
    GEMINI_SDK = "new"
except Exception:
    try:
        genai = importlib.import_module("google.generativeai")
        HAS_GEMINI = True
        GEMINI_SDK = "legacy"
    except Exception:
        HAS_GEMINI = False
        GEMINI_SDK = None

try:
    import g4f
    HAS_G4F = True
except ImportError:
    HAS_G4F = False

try:
    from llama_cpp import Llama
    HAS_LLAMA_CPP = True
except ImportError:
    HAS_LLAMA_CPP = False

def clean_json_response(response_text):
    """
    Limpa a resposta focando em encontrar o objeto JSON que contém a chave "segments".
    Estratégia: 
    1. Busca a palavra "segments", encontra o '{' anterior e usa raw_decode.
    2. Fallback: Parsear lista de segmentos item a item (recuperação de JSON truncado).
    """
    if not isinstance(response_text, str):
        response_text = str(response_text)
    
    if not response_text:
        return {"segments": []}

    # 1. Limpeza preliminar
    # Remove tags de pensamento (DeepSeek R1)
    response_text = re.sub(r'<think>.*?</think>', '', response_text, flags=re.DOTALL)
    # Models often wrap valid JSON in ```json fences or add a language tag
    # with different casing. Removing the wrapper before raw_decode makes the
    # parser deterministic while the existing fragment fallback handles
    # genuinely truncated replies.
    response_text = re.sub(r'```(?:json)?', '', response_text, flags=re.IGNORECASE)
    response_text = response_text.replace('```', '')
    
    # Normaliza escapes excessivos (\n virando \\n) e aspas se parecer necessário
    try:
        if "\\n" in response_text or "\\\"" in response_text:
             # Tenta um decode básico de escapes
             response_text = response_text.replace("\\n", "\n").replace("\\\"", "\"").replace("\\'", "'")
    except:
        pass

    # 2. Busca pela palavra-chave "segments"
    # Procura índices de todas as ocorrências de 'segments'
    matches = [m.start() for m in re.finditer(r'segments', response_text)]
    
    if not matches:
        # Se não achou segments, retorna vazio
        return {"segments": []}

    # Tenta extrair JSON válido a partir de cada ocorrência
    for match_idx in matches:
        # Procura o '{' mais próximo ANTES de "segments"
        # Limita busca a 5000 chars para trás para performance
        start_search = max(0, match_idx - 5000)
        snippet_before = response_text[start_search:match_idx]
        
        # Encontra o ÚLTIMO '{' no snippet
        last_open_rel = snippet_before.rfind('{')
        
        if last_open_rel != -1:
            real_start = start_search + last_open_rel
            candidate_text = response_text[real_start:]
            
            # Tentativa A: json.raw_decode
            try:
                decoder = json.JSONDecoder()
                obj, _ = decoder.raw_decode(candidate_text)
                if 'segments' in obj and isinstance(obj['segments'], list):
                    return obj
            except:
                pass
            
            # Tentativa B: ast.literal_eval
            try:
                balance = 0
                in_string = False
                escape = False
                found_end = -1
                
                for i, char in enumerate(candidate_text):
                    if escape:
                        escape = False
                        continue
                    if char == '\\':
                        escape = True
                        continue
                    if char == "'" or char == '"':
                        in_string = not in_string
                        continue
                        
                    if not in_string:
                        if char == '{':
                            balance += 1
                        elif char == '}':
                            balance -= 1
                            if balance == 0:
                                found_end = i
                                break
                
                if found_end != -1:
                    clean_cand = candidate_text[:found_end+1]
                    obj = ast.literal_eval(clean_cand)
                    if 'segments' in obj and isinstance(obj['segments'], list):
                        return obj
            except:
                pass

    # 3. Fallback: Extração bruta de markdown
    try:
        match = re.search(r"```json(.*?)```", response_text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
    except:
        pass
        
    # 4. LAST RESORT: Fragment Parser (Para JSON truncado/incompleto)
    # Procura por "segments": [ e tenta parsear item por item
    try:
        match_list = re.search(r'"segments"\s*:\s*\[', response_text)
        if match_list:
            start_pos = match_list.end()
            current_pos = start_pos
            found_segments = []
            decoder = json.JSONDecoder()
            
            while True:
                while current_pos < len(response_text) and response_text[current_pos] in ' \t\n\r,':
                    current_pos += 1
                
                if current_pos >= len(response_text):
                    break
                    
                if response_text[current_pos] == ']':
                    break
                
                try:
                    obj, end_pos = decoder.raw_decode(response_text[current_pos:])
                    if isinstance(obj, dict):
                        found_segments.append(obj)
                    current_pos += end_pos
                except json.JSONDecodeError:
                    break
                    
            if found_segments:
                print(f"[INFO] Recuperado {len(found_segments)} segmentos de JSON truncado.")
                return {"segments": found_segments}
    except:
        pass

    return {"segments": []}


def preprocess_transcript_for_ai(segments):
    """
    Concatenates transcript segments into a single string with embedded time tags.
    """
    if not segments:
        return ""

    full_text = ""
    last_tag_time = -100  # Force first tag
    
    # Try to start with (0s) based on first segment
    first_start = segments[0].get('start', 0)
    full_text += f"({int(first_start)}s) "
    last_tag_time = first_start

    for seg in segments:
        text = seg.get('text', '').strip()
        end_time = seg.get('end', 0)
        
        full_text += text + " "
        
        if end_time - last_tag_time >= 4:
            full_text += f"({int(end_time)}s) "
            last_tag_time = end_time

    return full_text.strip()

def _gemini_generate(model_name, prompt, api_key):
    """Generate via whichever Gemini SDK is installed. Returns text."""
    if GEMINI_SDK == "legacy":
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        return model.generate_content(prompt).text
    # new SDK
    client = genai.Client(api_key=api_key)
    return client.models.generate_content(model=model_name, contents=prompt).text


def _is_key_error(error_str):
    """True when the Gemini failure is a key/auth problem, not a transient one."""
    low = error_str.lower()
    return any(token in low for token in (
        "api key not valid", "api_key_invalid", "permission_denied",
        "401", "403", "unauthenticated", "invalid api key",
    ))


_GEMINI_KEY_CURSOR = 0


def _normalise_gemini_keys(api_key):
    values = api_key if isinstance(api_key, (list, tuple)) else [api_key]
    result = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in result:
            result.append(value)
    return result[:3]


def _ordered_gemini_keys(keys):
    """Return selected or round-robin key order without exposing secrets."""
    global _GEMINI_KEY_CURSOR
    if len(keys) <= 1:
        return keys
    mode = os.getenv("VIRALCUTTER_GEMINI_KEY_MODE", "auto").strip().lower()
    if mode in {"1", "2", "3"}:
        index = int(mode) - 1
        return [keys[index]] if index < len(keys) else [keys[0]]
    start = _GEMINI_KEY_CURSOR % len(keys)
    _GEMINI_KEY_CURSOR += 1
    return keys[start:] + keys[:start]


def call_gemini(prompt, api_key, model_name='gemini-2.5-flash-lite-preview-09-2025'):
    if not HAS_GEMINI:
        raise ImportError(
            "Gemini SDK is not installed. Install one of:\n"
            "    pip install google-generativeai   (classic)\n"
            "    pip install google-genai          (new SDK)\n"
            "or re-run install_dependencies.bat / install_linux.sh which install them.")

    keys = _ordered_gemini_keys(_normalise_gemini_keys(api_key))
    if not keys:
        raise RuntimeError("No Gemini API key is configured.")
    max_retries = 3
    base_wait = 30
    last_error = None

    for key_index, candidate_key in enumerate(keys):
        for attempt in range(max_retries):
            try:
                return _gemini_generate(model_name, prompt, candidate_key)
            except Exception as e:
                last_error = e
                error_str = str(e)
                quota_error = "429" in error_str or "quota exceeded" in error_str.lower()
                if quota_error:
                    if key_index < len(keys) - 1:
                        print("[Gemini] Key quota reached; switching to the next configured key.", flush=True)
                        break
                    wait_time = base_wait * (attempt + 1)
                    match = re.search(r"retry in (\d+(\.\d+)?)s", error_str)
                    if match:
                        wait_time = float(match.group(1)) + 5.0
                    print(f"[429] Quota exceeded. Waiting {wait_time:.2f}s before retry {attempt+1}/{max_retries}...", flush=True)
                    time.sleep(wait_time)
                    continue
                if _is_key_error(error_str):
                    if key_index < len(keys) - 1:
                        print("[Gemini] Invalid key; switching to the next configured key.", flush=True)
                        break
                    raise RuntimeError(
                        "Gemini API key error (API key not valid): check the configured key(s) at "
                        "aistudio.google.com/apikey. مفتاح Gemini غير صالح.") from e
                print(f"Gemini API error (non-fatal, returning empty): {e}")
                return "{}"

    raise RuntimeError("Gemini API failed after trying the configured key(s).") from last_error

def call_g4f(prompt, model_name="gpt-4o-mini"):
    if not HAS_G4F:
        raise ImportError("A biblioteca 'g4f' não está instalada. Instale com: pip install g4f")
    
    max_retries = 3
    base_wait = 5
    
    for attempt in range(max_retries):
        try:
            response = g4f.ChatCompletion.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            
            if isinstance(response, dict):
                if 'error' in response:
                    raise Exception(f"API Error: {response['error']}")
                if 'choices' in response and isinstance(response['choices'], list):
                    if len(response['choices']) > 0:
                         content = response['choices'][0].get('message', {}).get('content', '')
                         if content:
                             return content
                if not response:
                     raise ValueError("Empty Dict response")

                return json.dumps(response)

            if not response:
                print(f"[WARN] G4F retornou resposta vazia. Tentativa {attempt+1}/{max_retries}")
                time.sleep(base_wait)
                continue
            
            if isinstance(response, str):
                return response

            try:
                return json.dumps(response, ensure_ascii=False)
            except:
                return str(response)
            
        except Exception as e:
            print(f"[WARN] Erro na API do G4F (Tentativa {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_time = base_wait * (2 ** attempt)
                time.sleep(wait_time)
            
    print(f"Falha crítica após {max_retries} tentativas no G4F.")
    return "{}"

def _parse_tsv_transcript(tsv_path):
    """Parse a WhisperX TSV (header + rows of start_ms<TAB>end_ms<TAB>text)."""
    segments = []
    try:
        with open(tsv_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()[1:]  # skip header
            for line in lines:
                parts = line.strip().split('\t')
                if len(parts) >= 3:
                    try:
                        start_ms = float(parts[0])
                        end_ms = float(parts[1])
                    except (TypeError, ValueError):
                        continue
                    segments.append({
                        'start': start_ms / 1000.0,
                        'end': end_ms / 1000.0,
                        'text': parts[2],
                    })
    except Exception as e:
        print("Error parsing TSV {}: {}".format(tsv_path, e))
    return segments


def _parse_srt_transcript(srt_path):
    """Parse a standard SRT file into transcript segments."""
    segments = []
    try:
        with open(srt_path, 'r', encoding='utf-8') as f:
            srt_content = f.read()
        pattern = re.compile(r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n((?:(?!\n\n).)*)', re.DOTALL)
        matches = pattern.findall(srt_content)

        def srt_time_to_seconds(t_str):
            h, m, s = t_str.replace(',', '.').split(':')
            return int(h) * 3600 + int(m) * 60 + float(s)

        for m in matches:
            start_sec = srt_time_to_seconds(m[1])
            end_sec = srt_time_to_seconds(m[2])
            text = m[3].replace('\n', ' ')
            segments.append({'start': start_sec, 'end': end_sec, 'text': text})
    except Exception as e:
        print("Error parsing SRT {}: {}".format(srt_path, e))
    return segments


def load_transcript(project_folder):
    """Parses input.tsv / input.srt from the project folder.

    Falls back to any top-level transcript file: local/external videos keep
    their original basename (transcription artifacts are not named input.*),
    so older projects must still be processable.
    """
    input_tsv = os.path.join(project_folder, 'input.tsv')
    input_srt = os.path.join(project_folder, 'input.srt')

    # Try to load TSV first (more reliable time)
    transcript_segments = _parse_tsv_transcript(input_tsv) if os.path.exists(input_tsv) else []

    # Fallback to SRT parser if TSV empty/failed
    if not transcript_segments and os.path.exists(input_srt):
        transcript_segments = _parse_srt_transcript(input_srt)

    # Last resort: any top-level transcript artifact (older broken projects
    # whose transcription was written under the video's own basename).
    if not transcript_segments:
        try:
            candidates = sorted(os.listdir(project_folder))
        except OSError:
            candidates = []
        for name in candidates:
            lower = name.lower()
            if lower.endswith('.tsv') and not lower.startswith('input.'):
                transcript_segments = _parse_tsv_transcript(os.path.join(project_folder, name))
                if transcript_segments:
                    break
        if not transcript_segments:
            for name in candidates:
                lower = name.lower()
                if lower.endswith('.srt') and not lower.startswith('input.'):
                    transcript_segments = _parse_srt_transcript(os.path.join(project_folder, name))
                    if transcript_segments:
                        break

    if not transcript_segments:
        raise ValueError("Could not parse transcript from TSV or SRT.")

    return transcript_segments

def _bounded_score(value, default=0.0):
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


def _title_quality_score(title):
    """Small deterministic quality heuristic for title review, not a safety verdict."""
    text = str(title or "").strip()
    if not text:
        return 0.0
    score = 55.0
    if 18 <= len(text) <= 72:
        score += 20.0
    elif len(text) > 110:
        score -= 15.0
    if text.endswith(("!", "؟", "?")):
        score += 5.0
    if text.count("!") > 2 or text.count("؟") > 2 or text.count("?") > 2:
        score -= 12.0
    if text.isupper() and any(char.isalpha() for char in text):
        score -= 20.0
    return round(max(0.0, min(100.0, score)), 1)


def _choose_recommended_title(segment):
    """Select the strongest safe-looking title candidate for default publishing.

    Safety filtering still runs later; this helper only ranks readability and
    does not approve a title for publication.
    """
    candidates = [segment.get("title", "")]
    alternatives = segment.get("alt_titles") or []
    if isinstance(alternatives, str):
        alternatives = [alternatives]
    candidates.extend(alternatives)
    clean = []
    seen = set()
    for candidate in candidates:
        value = str(candidate or "").strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            clean.append(value)
    if not clean:
        return "Viral Segment"
    return max(enumerate(clean), key=lambda item: (_title_quality_score(item[1]), -item[0]))[1]


def _selection_score(segment):
    """Compute a transparent editorial score without hiding the AI score.

    The original ``score`` remains untouched. This second score rewards a
    strong hook, narrative completeness, clarity and novelty, while keeping
    the model's virality estimate as the largest component.
    """
    virality = _bounded_score(segment.get("score"), 0)
    hook = _bounded_score(segment.get("hook_strength"), virality)
    completeness = _bounded_score(segment.get("narrative_completeness"), virality)
    clarity = _bounded_score(segment.get("clarity_score"), virality)
    novelty = _bounded_score(segment.get("novelty_score"), virality)
    value = (0.45 * virality + 0.20 * hook + 0.20 * completeness
             + 0.10 * clarity + 0.05 * novelty)
    return round(max(0.0, min(100.0, value)), 1), {
        "virality": round(virality, 1),
        "hook": round(hook, 1),
        "completeness": round(completeness, 1),
        "clarity": round(clarity, 1),
        "novelty": round(novelty, 1),
    }


def _rank_segments_with_diversity(segments, limit=None):
    """Greedy ranking that prefers new topics and editorial angles.

    This is deliberately deterministic. It does not delete viable segments;
    it only changes which candidates appear first when a limit is requested.
    """
    remaining = list(segments)
    ranked = []
    topic_counts = {}
    while remaining and (limit is None or len(ranked) < limit):
        best_index = 0
        best_value = float("-inf")
        for index, candidate in enumerate(remaining):
            topic = str(candidate.get("topic") or "").strip().lower()
            angle = str(candidate.get("angle") or "").strip().lower()
            repeat_penalty = topic_counts.get(topic, 0) * 8.0 if topic else 0.0
            angle_penalty = 3.0 if angle and any(
                str(item.get("angle") or "").strip().lower() == angle for item in ranked[-3:]
            ) else 0.0
            value = float(candidate.get("selection_score", candidate.get("score", 0)) or 0)
            value -= repeat_penalty + angle_penalty
            if value > best_value:
                best_index, best_value = index, value
        chosen = remaining.pop(best_index)
        chosen["candidate_rank"] = len(ranked) + 1
        topic = str(chosen.get("topic") or "").strip().lower()
        if topic:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
        ranked.append(chosen)
    return ranked


def _parse_segment_time(value, default=0.0):
    """Parse seconds from AI timestamps without treating a missing ref as zero."""
    if value is None or value == "":
        return float(default)
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip().lower()
    match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?", text)
    if ":" in text:
        parts = text.split(":")
        try:
            if len(parts) == 3:
                return max(0.0, int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
            if len(parts) == 2:
                return max(0.0, int(parts[0]) * 60 + float(parts[1]))
        except (TypeError, ValueError):
            return float(default)
    if match:
        try:
            return max(0.0, float(match.group(1)))
        except (TypeError, ValueError):
            pass
    return float(default)


def _segment_window_fingerprint(start_time, end_time):
    """Stable identity for a source window, independent of its title."""
    payload = "{:.3f}:{:.3f}".format(float(start_time), float(end_time))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def segments_manifest_fingerprint(segments):
    """Fingerprint ordered source windows and titles used to create cuts."""
    payload = []
    for item in list(segments or []):
        if not isinstance(item, dict):
            continue
        payload.append({
            "start_time": round(_parse_segment_time(item.get("start_time"), 0.0), 3),
            "end_time": round(_parse_segment_time(item.get("end_time"), 0.0), 3),
            "title": str(item.get("title") or item.get("recommended_title") or ""),
        })
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def deduplicate_segments(segments):
    """Keep the highest-scoring candidate for each source window."""
    ordered = sorted(
        list(segments or []),
        key=lambda item: float(item.get("selection_score", item.get("score", 0)) or 0),
        reverse=True,
    )
    unique = []
    for candidate in ordered:
        if not isinstance(candidate, dict) or "start_time" not in candidate or "end_time" not in candidate:
            continue
        if any(_windows_are_near_duplicates(candidate, existing) for existing in unique):
            print("[DEBUG] Dropping duplicate source window: {}".format(candidate.get("title", "Untitled")))
            continue
        unique.append(candidate)
    return _rank_segments_with_diversity(unique)


def _windows_are_near_duplicates(left, right):
    """Return True when two candidates contain substantially the same source."""
    try:
        left_start, left_end = float(left["start_time"]), float(left["end_time"])
        right_start, right_end = float(right["start_time"]), float(right["end_time"])
    except (KeyError, TypeError, ValueError):
        return False
    intersection = max(0.0, min(left_end, right_end) - max(left_start, right_start))
    if intersection <= 0.0:
        return False
    left_duration = max(0.1, left_end - left_start)
    right_duration = max(0.1, right_end - right_start)
    overlap_ratio = intersection / min(left_duration, right_duration)
    # Exact/near-identical windows and small shifts are one clip, even when
    # titles differ. The second rule preserves genuinely different partial
    # overlaps such as two 15s windows sharing only 7s.
    return overlap_ratio >= 0.75 or (
        abs(left_start - right_start) <= 1.0 and overlap_ratio >= 0.50
    )


# A pause of this length (seconds) between transcript lines marks a
# sentence boundary. Cuts snapped to these boundaries never split a word.
PAUSE_BOUNDARY_SECONDS = 0.35


def _speech_blocks(transcript_segments):
    """Split transcript lines into speech blocks separated by real pauses.

    Returns a list of (start, end) blocks. Lines closer together than
    ``PAUSE_BOUNDARY_SECONDS`` belong to the same sentence; the first line
    after a longer gap opens a new block. Used to snap cut points so they
    never land in the middle of a word.
    """
    blocks = []
    if not transcript_segments:
        return blocks
    ordered = sorted(
        transcript_segments, key=lambda s: float(s.get("start", 0.0)))
    block_start = float(ordered[0].get("start", 0.0))
    block_end = float(ordered[0].get("end", block_start))
    for seg in ordered[1:]:
        start = float(seg.get("start", block_end))
        end = float(seg.get("end", start))
        if start - block_end >= PAUSE_BOUNDARY_SECONDS:
            blocks.append((block_start, block_end))
            block_start, block_end = start, end
        else:
            block_end = max(block_end, end)
    blocks.append((block_start, block_end))
    return blocks


def snap_segment_boundaries(start_time, end_time, transcript_segments):
    """Snap a raw window to the speech block containing it.

    Returns (start, end) aligned to sentence boundaries when the raw window
    overlaps a detected block; otherwise the raw values are returned
    unchanged (transcript may be word-level, missing, or AI-only).
    """
    start_time = max(0.0, float(start_time))
    end_time = max(start_time + 0.1, float(end_time))
    if not transcript_segments:
        return start_time, end_time
    blocks = _speech_blocks(transcript_segments)
    if not blocks:
        return start_time, end_time
    for block_start, block_end in blocks:
        # Window substantially inside this block -> align both edges.
        if block_start - 0.05 <= start_time <= block_end:
            return block_start, max(block_end, end_time)
    # No containing block (window starts inside a long pause or the
    # transcript has sparse lines). Never jump the start forward past the
    # hook; keep the raw window so the AI-selected moment is preserved.
    return start_time, end_time


def process_segments(raw_segments, transcript_segments, min_duration, max_duration, output_count=None, snap_to_boundaries=True):
    """
    Aligns raw AI segments (with reference tags) to actual transcript timestamps.
    Applies constraints, validation, and deduplication.

    ``snap_to_boundaries`` (default True) aligns final cut points to sentence
    boundaries derived from transcript pauses, so cuts never split a word.
    """
    
    all_segments = raw_segments
    tempo_minimo = min_duration
    tempo_maximo = max_duration
    
    # Sort segments by score (descending)
    try:
        all_segments.sort(key=lambda x: int(x.get('score', 0)), reverse=True)
    except:
        pass

    # --- POST-PROCESSING: Match Text to Timestamps ---
    processed_segments = []
    
    print(f"[DEBUG] Matching {len(all_segments)} raw segments to timestamps...")
    
    for seg in all_segments:
        try:
            # 1. Parse Reference Time
            ref_time_str = seg.get('start_time_ref')
            # Some providers return numeric start_time instead of the
            # documented ``start_time_ref``. Never silently convert that case
            # to (0s), otherwise every title is cut from the first seconds.
            if ref_time_str in (None, "", "(0s)") and seg.get("start_time") not in (None, ""):
                ref_time_str = seg.get("start_time")
            ref_time_val = _parse_segment_time(ref_time_str, default=0.0)

            # Find segment index closest to ref_time
            start_idx = 0
            min_diff = 999999
            for i, s in enumerate(transcript_segments):
                diff = abs(s['start'] - ref_time_val)
                if diff < min_diff:
                    min_diff = diff
                    start_idx = i
                if s['start'] > ref_time_val + 10: 
                    break
            
            # Backtrack
            start_idx = max(0, start_idx - 5)
            
            # 2-3. Prefer explicit numeric timestamps. They are the only
            # reliable identity when an LLM returns several different titles
            # for the same transcript phrase. Text matching remains a fallback
            # for the documented start_text/end_text contract.
            explicit_start = seg.get("start_time") not in (None, "")
            explicit_end = seg.get("end_time") not in (None, "")
            if explicit_start:
                final_start_time = _parse_segment_time(seg.get("start_time"), default=ref_time_val)
                match_start_idx = start_idx
            else:
                start_text_target = seg.get('start_text', '').lower().strip()
                start_text_target = re.sub(r'[^\w\s]', '', start_text_target)
                final_start_time = -1
                match_start_idx = -1
                search_limit = min(len(transcript_segments), start_idx + 50)
                for i in range(start_idx, search_limit):
                    s_text = transcript_segments[i]['text'].lower()
                    s_text = re.sub(r'[^\w\s]', '', s_text)
                    if start_text_target and (start_text_target in s_text or s_text in start_text_target):
                        final_start_time = transcript_segments[i]['start']
                        match_start_idx = i
                        break
                if final_start_time == -1:
                    final_start_time = transcript_segments[start_idx]['start'] if start_idx < len(transcript_segments) else ref_time_val
                    match_start_idx = start_idx

            if explicit_end:
                final_end_time = _parse_segment_time(
                    seg.get("end_time"), default=final_start_time + tempo_minimo)
            else:
                end_text_target = seg.get('end_text', '').lower().strip()
                end_text_target = re.sub(r'[^\w\s]', '', end_text_target)
                final_end_time = -1
                if match_start_idx != -1:
                    search_end_limit = min(len(transcript_segments), match_start_idx + 200)
                    for i in range(match_start_idx, search_end_limit):
                        s_text = transcript_segments[i]['text'].lower()
                        s_text = re.sub(r'[^\w\s]', '', s_text)
                        if end_text_target and (end_text_target in s_text or s_text in end_text_target):
                            final_end_time = transcript_segments[i]['end']
                            break
                if final_end_time == -1:
                    final_end_time = final_start_time + tempo_minimo

            # Calculate Duration
            duration = final_end_time - final_start_time
            
            # Validate Duration (Min)
            if duration < tempo_minimo: 
                print(f"[WARN] Segmento menor que duration min ({duration:.2f}s < {tempo_minimo}s). Estendendo para {tempo_minimo}s.")
                duration = tempo_minimo
                final_end_time = final_start_time + duration
            
            # Validate Duration (Max)
            if duration > tempo_maximo:
                print(f"[WARN] Segmento excede max duration ({duration:.2f}s > {tempo_maximo}s). Cortando para {tempo_maximo}s.")
                final_end_time = final_start_time + tempo_maximo
                duration = tempo_maximo

            # Professional cut refinement: snap both edges to sentence
            # boundaries (transcript pauses) so the clip never starts or
            # ends mid-word. Applied after duration clamping so the
            # min/max guarantees above are never violated.
            if snap_to_boundaries:
                snapped_start, snapped_end = snap_segment_boundaries(
                    final_start_time, final_end_time, transcript_segments)
                # Keep the snap only when it stays inside the allowed window.
                if snapped_start >= 0 and (snapped_end - snapped_start) >= tempo_minimo:
                    if (snapped_end - snapped_start) <= tempo_maximo:
                        final_start_time = snapped_start
                        final_end_time = snapped_end
                        duration = final_end_time - final_start_time
                # A snap that overshoots min duration is still better than a
                # mid-word cut: fall back to the raw (clamped) window.

            # Construct Final Segment
            hashtags = seg.get('hashtags', [])
            if isinstance(hashtags, str):
                hashtags = [h.strip().lstrip('#') for h in re.split(r'[,\s]+', hashtags) if h.strip()]
            processed_segments.append({
                "title": seg.get('title', 'Viral Segment'),
                "start_time": final_start_time,
                "end_time": final_end_time,
                "hook": seg.get('title', ''),
                "reasoning": seg.get('reasoning', ''),
                "score": seg.get('score', 0),
                "duration": duration,
                "caption": seg.get('caption', ''),
                "topic": seg.get('topic', ''),
                "angle": seg.get('angle', ''),
                "hook_type": seg.get('hook_type', ''),
                "hook_strength": seg.get('hook_strength', seg.get('score', 0)),
                "narrative_completeness": seg.get('narrative_completeness', seg.get('score', 0)),
                "clarity_score": seg.get('clarity_score', seg.get('score', 0)),
                "novelty_score": seg.get('novelty_score', seg.get('score', 0)),
                "hashtags": hashtags,
                # A/B titles/captions (Roadmap 5.3): kept when the AI
                # returned them, otherwise fall back to the main title.
                "alt_titles": seg.get('alt_titles') or [seg.get('title', '')],
                "alt_captions": seg.get('alt_captions') or [seg.get('caption', '')],
                "recommended_title": seg.get('recommended_title') or _choose_recommended_title(seg),
                "title_quality_score": _title_quality_score(seg.get('recommended_title') or _choose_recommended_title(seg)),
                "window_fingerprint": _segment_window_fingerprint(final_start_time, final_end_time),
            })

        except Exception as e:
            print(f"[WARN] Error processing segment {seg}: {e}")
            continue

    # Add a transparent score before de-duplication and ranking.
    for candidate in processed_segments:
        candidate["selection_score"], candidate["selection_breakdown"] = _selection_score(candidate)

    # Deduplication: keep one title for each substantially identical source window.
    all_segments = deduplicate_segments(processed_segments)
    print(f"[DEBUG] Finished processing. {len(all_segments)} segments valid.")

    if output_count and len(all_segments) > output_count:
        print(f"Filtrando os top {output_count} segmentos de {len(all_segments)} candidatos encontrados nos chunks.")
        all_segments = _rank_segments_with_diversity(all_segments, output_count)
    else:
        all_segments = _rank_segments_with_diversity(all_segments)

    final_result = {"segments": all_segments}
    
    # Validação básica de que temos start_time
    validated_segments = []
    for seg in final_result['segments']:
        if 'start_time' in seg:
             validated_segments.append(seg)
    
    final_result['segments'] = validated_segments
    
    return final_result


def segment_titles(segment):
    """A/B test titles for a segment: alt_titles + the main title (Roadmap 5.3).

    Returns a de-duplicated list; the main title is always last as the
    safe default choice.
    """
    seen, out = set(), []
    for t in list(segment.get("alt_titles") or []) + [segment.get("title", "")]:
        t = (t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out or ["Viral Segment"]


def segment_captions(segment):
    """A/B test captions for a segment: alt_captions + the main caption."""
    seen, out = set(), []
    for c in list(segment.get("alt_captions") or []) + [segment.get("caption", "")]:
        c = (c or "").strip()
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out or [""]



LANG_NAMES = {
    "auto": "the same language as the transcript",
    "ar": "Arabic (العربية)",
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "de": "German",
    "tr": "Turkish",
    "ru": "Russian",
    "hi": "Hindi",
}


def language_instruction(title_language):
    """STRICT output-language rule injected into the prompt (v6.6).

    Default "auto" keeps the old behaviour (match the transcript).
    Any other code (e.g. "ar") forces ALL generated text (titles, alt_titles,
    reasoning, captions) into that language regardless of the transcript.
    """
    code = str(title_language or "auto").strip().lower()
    if code == "auto" or code not in LANG_NAMES:
        return ""
    return (
        "\nLANGUAGE RULE (STRICT, OVERRIDES ANYTHING ELSE):\n"
        "Output EVERYTHING \u2014 title, alt_titles, reasoning, caption, alt_captions \u2014 "
        "in {} regardless of the transcript language.\n"
        "Hashtags stay in English (or the requested language).".format(LANG_NAMES[code])
    )


def _safe_chunk_size(chunk_size_arg, default):
    """Parse a --chunk-size CLI value defensively.

    A malformed value (e.g. "abc") used to raise ValueError deep inside the
    pipeline and trigger a full re-run; fall back to the configured default
    with a warning instead.
    """
    if not chunk_size_arg:
        return default
    try:
        value = int(chunk_size_arg)
    except (TypeError, ValueError):
        print(f"Aviso: chunk-size inválido '{chunk_size_arg}', usando {default}.")
        return default
    return value if value > 0 else default


def create(num_segments, viral_mode, themes, tempo_minimo, tempo_maximo, ai_mode="manual", api_key=None, project_folder="tmp", chunk_size_arg=None, model_name_arg=None, title_language="auto"):
    quantidade_de_virals = max(1, int(num_segments or 1))
    # Ask for spare candidates so safety filtering and de-duplication can still
    # leave the user with the requested number of safe, distinct clips.
    candidate_target = max(quantidade_de_virals * 2, quantidade_de_virals + 3)

    # 1. Load Transcript
    transcript_segments = load_transcript(project_folder)

    # 2. Pre-process Content
    formatted_content = preprocess_transcript_for_ai(transcript_segments)
    content = formatted_content

    # Load Config and Prompt
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(base_dir, 'api_config.json')
    prompt_path = os.path.join(base_dir, 'prompt.txt')

    config = {
        "selected_api": "gemini",
        "gemini": {
            "api_key": "",
            "api_keys": [],
            "key_mode": "auto",
            "model": "gemini-2.5-flash-lite-preview-09-2025",
            "chunk_size": 15000
        },
        "g4f": {
            "model": "gpt-4o-mini",
            "chunk_size": 2000
        }
    }

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
                if "gemini" in loaded_config: config["gemini"].update(loaded_config["gemini"])
                config["gemini"]["api_keys"] = config["gemini"].get("api_keys") or ([config["gemini"].get("api_key")] if config["gemini"].get("api_key") else [])
                if "g4f" in loaded_config: config["g4f"].update(loaded_config["g4f"])
                if "selected_api" in loaded_config: config["selected_api"] = loaded_config["selected_api"]
        except Exception as e:
            print(f"Erro ao ler api_config.json: {e}")

    # Config Vars
    current_chunk_size = 15000
    model_name = ""
    
    if ai_mode == "gemini":
        cfg_chunk = config["gemini"].get("chunk_size", 15000)
        current_chunk_size = _safe_chunk_size(chunk_size_arg, cfg_chunk)
        cfg_model = config["gemini"].get("model", "gemini-2.5-flash-lite-preview-09-2025")
        model_name = model_name_arg if model_name_arg else cfg_model
        env_raw = os.getenv("VIRALCUTTER_GEMINI_KEYS", "").strip()
        env_keys = []
        if env_raw:
            try:
                parsed = json.loads(env_raw)
                env_keys = parsed if isinstance(parsed, list) else []
            except Exception:
                env_keys = [item.strip() for item in re.split(r"[,;\n]+", env_raw) if item.strip()]
        configured_keys = config["gemini"].get("api_keys") or []
        if env_keys:
            api_key = env_keys[:3]
        elif configured_keys:
            api_key = configured_keys[:3]
        elif not api_key:
            api_key = config["gemini"].get("api_key", "")
            
    elif ai_mode == "g4f":
        cfg_chunk = config["g4f"].get("chunk_size", 2000)
        current_chunk_size = _safe_chunk_size(chunk_size_arg, cfg_chunk)
        cfg_model = config["g4f"].get("model", "gpt-4o-mini")
        model_name = model_name_arg if model_name_arg else cfg_model

    elif ai_mode == "local":
        current_chunk_size = _safe_chunk_size(chunk_size_arg, 3000)
        model_name = model_name_arg if model_name_arg else ""

    system_prompt_template = ""
    if os.path.exists(prompt_path):
        with open(prompt_path, 'r', encoding='utf-8') as f:
            system_prompt_template = f.read()
    else:
        print("Aviso: prompt.txt não encontrado. Usando prompt interno.")
        system_prompt_template = """You are a World-Class Viral Video Editor.
{context_instruction}
Analyze the transcript below with time tags (XXs). Find {amount} viral segments.
Constraints: Each segment MUST be between {min_duration} seconds and {max_duration} seconds.
IMPORTANT: Output "Title", "Hook", and "Reasoning" in the SAME LANGUAGE as the transcript (e.g., if transcript is Portuguese, output Portuguese).
TRANSCRIPT:
{transcript_chunk}
OUTPUT JSON ONLY:
{json_template}"""


    json_template = '''
            { "segments" :
                [
                    {
                        "start_text": "Exact first 5-10 words of the segment",
                        "end_text": "Exact last 5-10 words of the segment",
                        "start_time_ref": "Value of closest (XXs) tag",
                        "title": "Viral Hook Title (Same Language as Transcript)",
                        "alt_titles": ["3 alternative A/B titles, different hooks, same language"],
                        "reasoning": "Why this is viral? Hook? Value? (Same Language as Transcript)",
                        "topic": "short neutral topic label",
                        "angle": "lesson|surprise|story|opinion|mistake|result|question|warning",
                        "hook_type": "question|bold_claim|story|problem|result|contrast|quote",
                        "hook_strength": 85,
                        "narrative_completeness": 85,
                        "clarity_score": 85,
                        "novelty_score": 85,
                        "score": 95,
                        "caption": "Publish-ready caption for this clip, 1-2 catchy sentences (Same Language as Transcript)",
                        "alt_captions": ["3 alternative captions for A/B testing"],
                        "hashtags": ["3-5 relevant hashtags without the # symbol"]
                    }
                ]
            }
        '''

    # Chunking
    chunk_size = int(current_chunk_size)
    overlap_size = max(1000, int(chunk_size * 0.1))
    
    chunks = []
    start = 0
    content_len = len(content)

    print(f"[DEBUG] Chunking content (Size: {content_len}) with Chunk Size: {chunk_size} and Overlap: {overlap_size}")

    while start < content_len:
        end = min(start + chunk_size, content_len)
        if end < content_len:
            last_space = content.rfind(' ', start, end)
            if last_space != -1 and last_space > start:
                end = last_space
        chunk_text = content[start:end]
        if chunk_text.strip():
            chunks.append(chunk_text)
        if end >= content_len:
            break
        next_start = max(start + 1, end - overlap_size)
        safe_space = content.rfind(' ', start, next_start)
        if safe_space != -1:
            start = safe_space + 1
        else:
            start = next_start

    if viral_mode:
        virality_instruction = f"""analyze the segment for potential virality and identify up to {candidate_target} candidate segments; the final export will select {quantidade_de_virals} safe, distinct clips"""
    else:
        virality_instruction = f"""analyze the segment for potential virality and identify up to {candidate_target} candidate segments based on the list of themes {themes}; the final export will select {quantidade_de_virals} safe, distinct clips."""

    output_texts = []
    for i, chunk in enumerate(chunks):
        context_instruction = ""
        if len(chunks) > 1:
            context_instruction = f"Part {i+1} of {len(chunks)}. "
        
        try:
            prompt = system_prompt_template.format(
                context_instruction=context_instruction,
                virality_instruction=virality_instruction,
                min_duration=tempo_minimo,
                max_duration=tempo_maximo,
                transcript_chunk=chunk,
                json_template=json_template,
                amount=candidate_target
            )
        except KeyError:
            prompt = system_prompt_template
            prompt = prompt.replace("{context_instruction}", context_instruction)
            prompt = prompt.replace("{virality_instruction}", virality_instruction)
            prompt = prompt.replace("{min_duration}", str(tempo_minimo))
            prompt = prompt.replace("{max_duration}", str(tempo_maximo))
            prompt = prompt.replace("{transcript_chunk}", chunk)
            prompt = prompt.replace("{json_template}", json_template)
            prompt = prompt.replace("{amount}", str(candidate_target))

        prompt += language_instruction(title_language)
        output_texts.append(prompt)

    try:
        full_prompt_path = os.path.join(project_folder, "prompt_full.txt")
        full_prompt = system_prompt_template
        full_prompt = full_prompt.replace("{context_instruction}", "Full Video Transcript Analysis")
        full_prompt = full_prompt.replace("{virality_instruction}", virality_instruction)
        full_prompt = full_prompt.replace("{min_duration}", str(tempo_minimo))
        full_prompt = full_prompt.replace("{max_duration}", str(tempo_maximo))
        full_prompt = full_prompt.replace("{transcript_chunk}", content) 
        full_prompt = full_prompt.replace("{json_template}", json_template)
        full_prompt = full_prompt.replace("{amount}", str(quantidade_de_virals))
        
        full_prompt += language_instruction(title_language)
        with open(full_prompt_path, "w", encoding="utf-8") as f:
            f.write(full_prompt)
    except Exception as e:
        print(f"[WARN] Could not save prompt_full.txt: {e}")

    all_raw_segments = []

    print(f"Processando {len(output_texts)} chunks usando modo: {ai_mode.upper()}")

    local_llm_instance = None
    if ai_mode == "local":
        if not HAS_LLAMA_CPP:
            print("Error: llama-cpp-python not installed. Please install it to use Local mode.")
            return {"segments": []}
            
        models_dir = os.path.join(base_dir, 'models')
        model_path = os.path.join(models_dir, model_name)
        if not os.path.exists(model_path):
             if os.path.exists(model_name):
                 model_path = model_name
             else:
                 print(f"Error: Model not found at {model_path}")
                 return {"segments": []}
        
        print(f"[INFO] Loading Local Model: {os.path.basename(model_path)} (This may take a while)...")
        try:
            local_llm_instance = Llama(
                model_path=model_path,
                n_gpu_layers=-1, 
                n_ctx=8192,
                verbose=False
            )
        except Exception as e:
            print(f"Failed to load model: {e}")
            return {"segments": []}

    for i, prompt in enumerate(output_texts):
        response_text = ""
        manual_prompt_path = os.path.join(project_folder, f"prompt_part_{i+1}.txt")
        try:
            with open(manual_prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)
        except Exception as e:
            print(f"[ERRO] Falha ao salvar prompt.txt: {e}")
        
        if ai_mode == "manual":
            print(f"\n[INFO] O prompt foi salvo em: {manual_prompt_path}")
            print("\n" + "="*60)
            print(f"CHUNK {i+1}/{len(output_texts)}")
            print("="*60)
            print("COPIE O PROMPT ABAIXO (OU DO ARQUIVO GERADO) E COLE NA SUA IA PREFERIDA:")
            print("-" * 20)
            print(prompt)
            print("-" * 20)
            print("="*60)
            print("Cole o JSON de resposta abaixo e pressione ENTER.")
            print("Dica: Se o JSON tiver múltiplas linhas, tente colar tudo de uma vez ou minificado.")
            print("Se preferir, digite 'file' para ler de um arquivo 'tmp/response.json'.")
            
            user_input = input("JSON ou 'file': ")
            
            if user_input.lower() == 'file':
                try:
                    response_json_path = os.path.join(project_folder, 'response.json')
                    with open(response_json_path, 'r', encoding='utf-8') as rf:
                        response_text = rf.read()
                except FileNotFoundError:
                    print(f"Arquivo {response_json_path} não encontrado.")
            else:
                response_text = user_input
                if response_text.strip().startswith("{") and not response_text.strip().endswith("}"):
                    print("Parece incompleto. Cole o resto e dê Enter (ou Ctrl+C para cancelar):")
                    try:
                        rest = sys.stdin.read() 
                        response_text += rest
                    except:
                        pass

        elif ai_mode == "gemini":
            print(f"Enviando chunk {i+1} para o Gemini (Model: {model_name})...")
            response_text = call_gemini(prompt, api_key, model_name=model_name)
        elif ai_mode == "g4f":
            print(f"Enviando chunk {i+1} para o G4F (Model: {model_name})...")
            response_text = call_g4f(prompt, model_name=model_name)
        elif ai_mode == "local" and local_llm_instance:
            print(f"Processing chunk {i+1} with Local LLM...")
            try:
                output = local_llm_instance.create_chat_completion(
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant that outputs only JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=4096,
                    temperature=0.7
                )
                response_text = output['choices'][0]['message']['content']
            except Exception as e:
                print(f"Error evaluating local model: {e}")
                response_text = "{}"

        # --- Save RAW Response for Debugging ---
        try:
            raw_response_path = os.path.join(project_folder, f"response_raw_part_{i+1}.txt")
            with open(raw_response_path, "w", encoding="utf-8") as f:
                f.write(response_text)
            print(f"[DEBUG] Raw response saved to: {raw_response_path}")
        except Exception as e:
            print(f"[WARN] Failed to save raw response: {e}")

        # Processar resposta
        try:
            data = clean_json_response(response_text)
            chunk_segments = data.get("segments", [])
            print(f"Encontrados {len(chunk_segments)} segmentos neste chunk.")
            all_raw_segments.extend(chunk_segments)
        except json.JSONDecodeError:
            print("Erro: Resposta inválida.")
        except Exception as e:
            print(f"Erro desconhecido ao processar chunk: {e}")

    # Call the alignment / processing logic
    return process_segments(
        all_raw_segments, 
        transcript_segments, 
        tempo_minimo, 
        tempo_maximo, 
        output_count=candidate_target
    )