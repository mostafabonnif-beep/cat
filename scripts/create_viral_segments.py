import ast
import difflib
import hashlib
import io
import json
import os
import re
import sys
import time

try:
    from scripts import performance_weights
except Exception:
    performance_weights = None

# v7.40: centralized clip-quality modules (stdlib-only, deterministic).
# Arabic normalization lives canonically in scripts/arabic_text and is
# re-exported below under the historical private names so existing callers
# and tests keep working unchanged.
try:
    from scripts import arabic_text as _arabic_text
except Exception:
    _arabic_text = None

try:
    from scripts import (
        clip_scoring,
        segment_validator,
        title_factual,
        transcript_window,
    )
    HAS_CLIP_QUALITY = True
except Exception:
    clip_scoring = title_factual = transcript_window = segment_validator = None
    HAS_CLIP_QUALITY = False

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

    Two passes:
    1. Parse the response AS-IS. Valid JSON containing escaped characters
       (e.g. "line\\nbreak" inside an Arabic title) MUST NOT be normalized —
       rewriting \\n to a raw newline corrupts strict JSON and used to drop
       the whole segment list silently.
    2. Only when nothing parsed: apply the legacy over-escape fix (\\\\n →
       newline, \\\" → quote) for double-escaped/manual-paste payloads and
       retry.
    """
    if not isinstance(response_text, str):
        response_text = str(response_text)

    if not response_text:
        return {"segments": []}

    result = _extract_segments_json(response_text)
    if result["segments"]:
        return result

    try:
        if "\\n" in response_text or "\\\"" in response_text:
            normalized = (response_text
                          .replace("\\n", "\n")
                          .replace("\\\"", "\"")
                          .replace("\\'", "'"))
            if normalized != response_text:
                fallback = _extract_segments_json(normalized)
                if fallback["segments"]:
                    return fallback
    except Exception:
        pass
    return result


def _extract_segments_json(response_text):
    """
    Estratégia:
    1. Busca a palavra "segments", encontra o '{' anterior e usa raw_decode.
    2. Fallback: Parsear lista de segmentos item a item (recuperação de JSON truncado).
    """
    # 1. Limpeza preliminar
    # Remove tags de pensamento (DeepSeek R1)
    response_text = re.sub(r'<think>.*?</think>', '', response_text, flags=re.DOTALL)
    # Models often wrap valid JSON in ```json fences or add a language tag
    # with different casing. Removing the wrapper before raw_decode makes the
    # parser deterministic while the existing fragment fallback handles
    # genuinely truncated replies.
    response_text = re.sub(r'```(?:json)?', '', response_text, flags=re.IGNORECASE)
    response_text = response_text.replace('```', '')

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

    # 2b. Recovery: dict-shaped "segments" value (numeric-key object).
    # Some models return {"segments": {"0": {...}, "1": {...}}} — or nest
    # segments deeper — instead of a list. Every attempt above requires
    # isinstance(obj["segments"], list) and the fragment parser below
    # requires '"segments": [' so such a response used to yield zero
    # segments. Decode the object value and, when every value is itself a
    # dict, return its values as the segment list (numeric-looking keys in
    # ascending numeric order, otherwise insertion order). Never crashes:
    # any failure falls through to the existing fallbacks untouched.
    try:
        decoder = json.JSONDecoder()
        for dict_match in re.finditer(r'"segments"\s*:\s*\{', response_text):
            try:
                obj, _ = decoder.raw_decode(response_text[dict_match.end() - 1:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj and all(
                    isinstance(value, dict) for value in obj.values()):
                keys = list(obj.keys())
                if all(re.fullmatch(r"[0-9]+", str(key)) for key in keys):
                    keys.sort(key=int)
                return {"segments": [obj[key] for key in keys]}
    except Exception:
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

    # Defensive ordering: the timestamp-matching and speech-block logic assume
    # chronological lines; a muxed/edited SRT can arrive out of order.
    transcript_segments.sort(key=lambda s: float(s.get('start', 0.0) or 0.0))
    return transcript_segments

def _bounded_score(value, default=0.0):
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


# Arabic orthography unification table (ord -> replacement, None = delete).
# Canonical implementation: scripts/arabic_text.py. Kept here as a thin
# re-export so existing callers/tests of the historical private names
# (`_normalize_arabic_orthography`, `_normalized_match_text`) are untouched.
# Arabic text is routinely written with several spelling variants of the same
# letters: the hamza carriers (أ إ آ ٱ) are the same consonant as plain ا in
# almost every context, ؤ/ئ are و/ي with a hamza seat, ة and ه both mark the
# feminine ending, and tatweel (U+0640) is a scribal stretch mark with no
# phonetic value. Tashkeel/diacritics (U+064B..U+065F) and the superscript
# alef (U+0670) are optional decorations that never change the word.
# Deliberately NOT mapped: alif maqsura ى stays distinct from ي so pairs like
# على (on) / علي (a name) never collide, and ي itself is untouched.
if _arabic_text is not None:
    _ARABIC_ORTHOGRAPHY_TABLE = _arabic_text._ARABIC_ORTHOGRAPHY_TABLE
else:
    _ARABIC_ORTHOGRAPHY_TABLE = {
        0x0623: 0x0627,  # أ hamza above    -> ا
        0x0625: 0x0627,  # إ hamza below    -> ا
        0x0622: 0x0627,  # آ madda          -> ا
        0x0671: 0x0627,  # ٱ wasla          -> ا
        0x0624: 0x0648,  # ؤ hamza on waw   -> و
        0x0626: 0x064A,  # ئ hamza on yaa   -> ي
        0x0629: 0x0647,  # ة ta marbuta     -> ه
        0x0640: None,    # tatweel (stretch mark) — deleted
        0x0670: None,    # superscript alef — deleted
    }
    _ARABIC_ORTHOGRAPHY_TABLE.update({cp: None for cp in range(0x064B, 0x0660)})


def _normalize_arabic_orthography(text):
    """Unify common Arabic spelling variants so matching is orthography-blind.

    Delegates to scripts.arabic_text.normalize_arabic_orthography (canonical).
    Translates hamza carriers (أ إ آ ٱ -> ا, ؤ -> و, ئ -> ي) and the ta
    marbuta (ة -> ه), and removes tatweel (U+0640), the tashkeel block
    (U+064B..U+065F) and the superscript alef (U+0670). Alif maqsura (ى) is
    NOT folded into ي and ي is not folded anywhere, so words like على/علي
    remain distinguishable.

    Pure function with no state: non-Arabic text (English included) passes
    through byte-identical, so this never alters Latin/ASCII output.
    """
    if _arabic_text is not None:
        return _arabic_text.normalize_arabic_orthography(text)
    if text is None:
        return ""
    return str(text).translate(_ARABIC_ORTHOGRAPHY_TABLE)


def _normalized_match_text(value):
    """Lowercase alphanumeric-only text for transcript alignment.

    Arabic orthography is unified first (hamza carriers, ta marbuta, tatweel,
    tashkeel) so spelling variants such as ``الأمور``/``الامور`` compare
    equal, while distinct words such as ``على``/``علي`` stay distinct (alif
    maqsura is not folded). English/Latin output is byte-identical to the
    legacy pipeline: the normalization table only touches Arabic code points.
    """
    return re.sub(r"[^\w\s]", "", _normalize_arabic_orthography(str(value or "").lower())).strip()


def _text_similarity(target, source):
    """Similarity in [0, 1]: containment beats sequence ratio beats token overlap."""
    if not target or not source:
        return 0.0
    if target in source or source in target:
        return 1.0
    ratio = difflib.SequenceMatcher(None, target, source).ratio()
    target_tokens = set(target.split())
    source_tokens = set(source.split())
    union = target_tokens | source_tokens
    overlap = (len(target_tokens & source_tokens) / len(union)) if union else 0.0
    return max(ratio, overlap)


def _window_text_from_transcript(transcript_segments, start_time, end_time):
    """The real words spoken inside ``[start_time, end_time)``.

    Joins (space-separated) the text of every transcript line whose span
    overlaps the half-open window (``line.start < end_time`` AND
    ``line.end > start_time``), normalized for matching. This is the ground
    truth for title relevance: an LLM caption that hallucinates content never
    present in the actual cut footage can no longer inflate the score, and a
    title about mid-clip content finally counts because the mid-clip words
    really are in the window.

    Pure helper: returns '' when the transcript is empty, when the window
    cannot be parsed as numbers, or when no line overlaps.
    """
    if not transcript_segments:
        return ""
    try:
        window_start = float(start_time)
        window_end = float(end_time)
    except (TypeError, ValueError):
        return ""
    overlapping = []
    for line in transcript_segments:
        if not isinstance(line, dict):
            continue
        try:
            line_start = float(line.get("start", 0.0) or 0.0)
            line_end = float(line.get("end", line_start) or line_start)
        except (TypeError, ValueError):
            continue
        if line_start < window_end and line_end > window_start:
            overlapping.append(str(line.get("text", "")))
    return _normalized_match_text(" ".join(overlapping))


def _title_content_relevance(title, segment, window_text=None):
    """Fraction of title words that actually appear in the clip's content.

    Catches titles that are pure bait unrelated to what the segment says.

    When ``window_text`` is provided and non-empty (the real transcript words
    inside the final cut window, e.g. from ``_window_text_from_transcript``)
    it is the primary evidence: words found there count at full weight, and
    words that appear ONLY in the LLM's own start_text/end_text/caption count
    at half weight — a hallucinated caption can no longer make an unrelated
    title score 1.0. The result is capped at 1.0.

    When ``window_text`` is None or empty the legacy formula (LLM text only,
    full weight) is used unchanged, so results stay byte-identical to
    previous releases.
    """
    title_tokens = set(_normalized_match_text(title).split())
    if not title_tokens:
        return 0.0
    if window_text:
        window_words = set(_normalized_match_text(str(window_text)).split())
        llm_words = set(_normalized_match_text(" ".join([
            segment.get("start_text", ""),
            segment.get("end_text", ""),
            segment.get("caption", ""),
        ])).split())
        if not window_words and not llm_words:
            return 0.0
        llm_only_words = llm_words - window_words
        matched_window = len(title_tokens & window_words)
        matched_llm_only = len(title_tokens & llm_only_words)
        return min(1.0, (matched_window + 0.5 * matched_llm_only) / len(title_tokens))
    content = _normalized_match_text(" ".join([
        segment.get("start_text", ""),
        segment.get("end_text", ""),
        segment.get("caption", ""),
    ]))
    if not content:
        return 0.0
    content_tokens = set(content.split())
    return len(title_tokens & content_tokens) / len(title_tokens)


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
    # Word-salad check: a title that repeats the same common word several
    # times reads like keyword stuffing ("حرب حرب الكوكايين تطيح تطيح").
    words = [w for w in re.split(r"\W+", text.casefold()) if w]
    if len(words) >= 4:
        counts = {}
        for word in words:
            if len(word) > 2:
                counts[word] = counts.get(word, 0) + 1
        max_repeat = max(counts.values()) if counts else 0
        if max_repeat >= 2:
            score -= 10.0 * (max_repeat - 1)
    # A question title with no repeated words reads like a crafted
    # curiosity gap rather than a keyword pile-up.
    if len(words) >= 6 and len(set(words)) == len(words) and text.endswith(("؟", "?")):
        score += 3.0
    return round(max(0.0, min(100.0, score)), 1)


def _choose_recommended_title(segment, window_text=None):
    """Select the strongest safe-looking title candidate for default publishing.

    Safety filtering still runs later; this helper only ranks readability and
    does not approve a title for publication. ``window_text`` (optional) is
    the real transcript content of the final cut window: when given, content
    relevance is measured against the actual footage words instead of only
    the LLM's own start/end/caption text.
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

    def rank(item):
        index, value = item
        quality = _title_quality_score(value)
        relevance = _title_content_relevance(value, segment, window_text)
        return (quality + 12.0 * relevance, -index)

    return max(enumerate(clean), key=rank)[1]


def quality_gate_config() -> dict:
    """Editorial quality-gate configuration, read from the environment.

    Returns ``{"enabled": bool, "min": {component: floor}}``. The gate drops
    candidates whose *genuine* self-evaluations fall below the editorial
    floor (weak hooks / incomplete narratives / unclear clips are exactly the
    segments that die in the first seconds on Shorts). Candidates whose
    components were copied from the virality score (quality_missing) are
    never gated — there is nothing genuine to measure.

    * ``VIRALCUTTER_QUALITY_GATE=0`` disables the gate (keep everything).
    * ``VIRALCUTTER_MIN_HOOK`` / ``VIRALCUTTER_MIN_NARRATIVE`` /
      ``VIRALCUTTER_MIN_CLARITY`` override the individual floors.
    """
    def _env_float(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, "").strip())
        except (TypeError, ValueError):
            return default
    enabled = os.getenv("VIRALCUTTER_QUALITY_GATE", "1").strip().lower() not in {
        "0", "false", "no", "off"}
    return {
        "enabled": enabled,
        "min": {
            "hook_strength": _env_float("VIRALCUTTER_MIN_HOOK", 20.0),
            "narrative_completeness": _env_float("VIRALCUTTER_MIN_NARRATIVE", 20.0),
            "clarity_score": _env_float("VIRALCUTTER_MIN_CLARITY", 20.0),
        },
    }


def apply_quality_gate(segments, config=None) -> tuple[list, list]:
    """Drop candidates with genuinely weak editorial self-evaluations.

    Returns ``(kept, dropped)`` where each dropped entry carries
    ``{title, index, reasons: [..]}``. Segments flagged ``quality_missing``
    (the AI shipped no self-evaluation, so the components are unverified
    copies of the virality score) always survive: gating them would be
    guessing, not quality control.
    """
    config = config if config is not None else quality_gate_config()
    if not config.get("enabled"):
        return list(segments), []
    floors = config.get("min") or {}
    kept, dropped = [], []
    for index, seg in enumerate(segments or []):
        if not isinstance(seg, dict):
            kept.append(seg)
            continue
        if seg.get("quality_missing"):
            kept.append(seg)
            continue
        reasons = []
        for component, floor in floors.items():
            raw = seg.get(component)
            if raw is None:
                continue  # nothing shipped for this axis → not gated
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value < float(floor):
                label = {"hook_strength": "hook", "narrative_completeness": "narrative",
                         "clarity_score": "clarity"}.get(component, component)
                reasons.append("{} {:.0f}<{}".format(label, value, float(floor)))
        if reasons:
            dropped.append({"title": str(seg.get("title") or "Untitled"),
                            "index": index, "reasons": reasons})
        else:
            kept.append(seg)
    return kept, dropped


def _selection_score(segment, weights=None):
    """Compute a transparent editorial score without hiding the AI score.

    The original ``score`` remains untouched. This second score rewards a
    strong hook, narrative completeness, clarity, novelty and title quality,
    while keeping the model's virality estimate as the largest component.
    ``weights`` may come from performance_weights.load_weights to nudge
    components based on measured YouTube outcomes. A missing title score
    falls back to the virality estimate so candidates without titles are
    neither rewarded nor punished.
    """
    if isinstance(weights, dict) and isinstance(weights.get("weights"), dict):
        weights = weights["weights"]
    weights = weights or {"virality": 0.40, "hook": 0.20, "completeness": 0.20,
                          "clarity": 0.10, "novelty": 0.05, "title": 0.05}
    virality = _bounded_score(segment.get("score"), 0)
    hook = _bounded_score(segment.get("hook_strength"), virality)
    completeness = _bounded_score(segment.get("narrative_completeness"), virality)
    clarity = _bounded_score(segment.get("clarity_score"), virality)
    novelty = _bounded_score(segment.get("novelty_score"), virality)
    title = _bounded_score(segment.get("title_quality_score"), virality)
    value = (weights.get("virality", 0.40) * virality + weights.get("hook", 0.20) * hook
             + weights.get("completeness", 0.20) * completeness
             + weights.get("clarity", 0.10) * clarity + weights.get("novelty", 0.05) * novelty
             + weights.get("title", 0.05) * title)
    return round(max(0.0, min(100.0, value)), 1), {
        "virality": round(virality, 1),
        "hook": round(hook, 1),
        "completeness": round(completeness, 1),
        "clarity": round(clarity, 1),
        "novelty": round(novelty, 1),
        "title": round(title, 1),
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


if _arabic_text is not None:
    _ARABIC_NUM_TRANSLATION = str.maketrans({})
    _normalize_timestamp_digits = _arabic_text.normalize_digits
else:
    _ARABIC_NUM_TRANSLATION = str.maketrans({
        # Arabic-Indic digits U+0660..U+0669 (٠١٢٣٤٥٦٧٨٩)
        **{ord(src): dst for src, dst in zip("٠١٢٣٤٥٦٧٨٩", "0123456789")},
        # Persian digits U+06F0..U+06F9 (۰۱۲۳۴۵۶۷۸۹)
        **{ord(src): dst for src, dst in zip("۰۱۲۳۴۵۶۷۸۹", "0123456789")},
        # Arabic decimal separator ٫ (U+066B) and the Arabic comma ٬ (U+066C)
        # — Arabic/Darija LLM output routinely uses both as a decimal point.
        0x066B: ".",
        0x066C: ".",
    })

    def _normalize_timestamp_digits(value):
        return str(value).translate(_ARABIC_NUM_TRANSLATION)


def _normalize_timestamp_text(value):
    """Translate Arabic-Indic/Persian digits and Arabic decimal marks to ASCII.

    Arabic-language LLMs often emit timestamps such as ``٩:٥٠`` or ``١٢٫٥``;
    without this translation ``int()``/``float()`` reject them and the
    segment silently falls back to default timings.
    """
    return _normalize_timestamp_digits(str(value))


def _parse_segment_time(value, default=0.0):
    """Parse seconds from AI timestamps without treating a missing ref as zero.

    Arabic-Indic (٠-٩) and Persian (۰-۹) digits are translated to ASCII
    before parsing, and the Arabic decimal separator ٫ (U+066B) / Arabic
    comma ٬ (U+066C) count as decimal points. mm:ss and h:mm:ss both parse
    after translation: ``٩:٥٠`` → 9 min + 50 s = 590 s, ``1:٩٠`` → 1 min +
    90 s = 150 s. Genuinely malformed input still falls back to ``default``.
    """
    if value is None or value == "":
        return float(default)
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = _normalize_timestamp_text(str(value).strip().lower())
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


def source_video_fingerprint(path):
    """Cheap staleness fingerprint of a source video file: sha1("name|size|mtime_ns").

    Uses only ``os.stat`` metadata — no file content is read, so this is safe
    to call on every pipeline stage. Returns None when the path is missing or
    unreadable.

    main_improved.py embeds this value as ``source_meta.source_video_fp`` when
    it saves the AI-generated segment windows, and compares it on reuse: a
    changed/re-exported input video produces a different fingerprint and
    forces regeneration instead of silently reusing stale AI windows against
    new footage.
    """
    try:
        if not path:
            return None
        stat = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    name = os.path.basename(str(path))
    size = getattr(stat, "st_size", 0)
    mtime_ns = getattr(stat, "st_mtime_ns", None)
    if mtime_ns is None:  # tolerate minimal fake stat objects in tests
        mtime_ns = int(getattr(stat, "st_mtime", 0.0) * 1_000_000_000)
    payload = "{}|{}|{}".format(name, size, mtime_ns)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def segments_source_fingerprint(data):
    """Read the embedded source-video fingerprint from a saved segments payload.

    Returns ``data["source_meta"]["source_video_fp"]`` when present, else
    None (missing payload / missing nested dicts are tolerated). Compare this
    against ``source_video_fingerprint(current_input_video)`` before reusing
    previously saved AI windows: a mismatch means the input video changed and
    the old segment list is stale.
    """
    if not isinstance(data, dict):
        return None
    source_meta = data.get("source_meta")
    if not isinstance(source_meta, dict):
        return None
    return source_meta.get("source_video_fp")


# Schema/prompt versioning (v7.40): embedded in the segments config
# fingerprint so changing the selection schema, the scoring weights or the
# prompt template invalidates previously saved results instead of silently
# reusing them.
SEGMENTS_SCHEMA_VERSION = "2.0"


def prompt_version_fingerprint():
    """Stable fingerprint of the ACTIVE prompt template + segment schema.

    sha1 of prompt.txt (the file actually sent to the LLM) combined with the
    selection-schema and scoring versions. Editing prompt.txt, upgrading the
    scoring formula or the JSON schema all change this value → cached segment
    lists are regenerated rather than reused.
    """
    template = ""
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        prompt_path = os.path.join(base_dir, "prompt.txt")
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as handle:
                template = handle.read()
    except Exception:
        template = ""
    scoring_version = getattr(clip_scoring, "SCORING_VERSION", "legacy") if clip_scoring else "legacy"
    title_schema = getattr(title_factual, "TITLE_VALIDATION_SCHEMA_VERSION", "legacy") if title_factual else "legacy"
    payload = "{}|{}|{}|{}".format(
        SEGMENTS_SCHEMA_VERSION, scoring_version, title_schema, template)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def selection_weights_fingerprint():
    """Stable fingerprint of the ACTIVE selection weights (env override aware)."""
    if clip_scoring is None:
        return "legacy"
    try:
        return clip_scoring.weights_fingerprint()
    except Exception:
        return "legacy"


def transcript_fingerprint(transcript_segments):
    """Content fingerprint of the transcript used for a segment run.

    sha1 over the ordered (start, end, text) triples. Any re-transcription
    that changes the words/timings changes this value, so previously saved
    windows/titles are recognised as stale instead of being silently reused.
    Returns None for an empty transcript.
    """
    payload = []
    for line in transcript_segments or []:
        if not isinstance(line, dict):
            continue
        payload.append([
            round(float(line.get("start", 0.0) or 0.0), 3),
            round(float(line.get("end", 0.0) or 0.0), 3),
            str(line.get("text") or ""),
        ])
    if not payload:
        return None
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _load_word_timings(project_folder):
    """Word-level timings [{start,end,word}] for boundary snapping (v7.40).

    Reads the WhisperX input.json via scripts/transcript_window; returns []
    when unavailable so callers fall back to segment-level timings.
    """
    if transcript_window is None:
        return []
    try:
        return transcript_window.load_word_timings(project_folder)
    except Exception:
        return []


def deduplicate_segments(segments, semantic_threshold=None):
    """Keep the highest-scoring candidate for each source window / idea.

    Two levels (v7.40):
    * Temporal — windows sharing >= 60% of the shorter window are the same
      footage (``_windows_are_near_duplicates``).
    * Semantic — when candidates carry ``transcript_text`` (v7.40+), clips
      whose normalized window text is at/above the duplicate threshold
      express the same idea with different wording; only the strongest
      version is kept. Legacy payloads without ``transcript_text`` simply
      skip this level (full backwards compatibility).
    """
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
        if _is_semantic_duplicate(candidate, unique, semantic_threshold):
            print("[DEBUG] Dropping semantically duplicate clip (same idea): {}".format(
                candidate.get("title", "Untitled")))
            continue
        unique.append(candidate)
    return _rank_segments_with_diversity(unique)


def _is_semantic_duplicate(candidate, existing, threshold=None):
    """True when ``candidate`` repeats the idea of an already-kept clip.

    Only compares candidates that BOTH carry transcript_text; title
    differences alone never make two clips different, and missing text never
    makes them duplicates.
    """
    if transcript_window is None:
        return False
    candidate_text = str(candidate.get("transcript_text") or "").strip()
    if not candidate_text:
        return False
    if len(candidate_text.split()) < transcript_window.MIN_SEMANTIC_WORDS:
        return False
    if threshold is None:
        threshold = transcript_window.semantic_duplicate_threshold()
    for other in existing:
        other_text = str(other.get("transcript_text") or "").strip()
        if not other_text or len(other_text.split()) < transcript_window.MIN_SEMANTIC_WORDS:
            continue
        is_dup, _sim = transcript_window.are_semantic_duplicates(
            candidate_text, other_text, threshold)
        if is_dup:
            return True
    return False


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
    # Windows sharing >= 60% of the shorter window are the same footage, and
    # so are near-identical placements (<= 1s shift) that share >= 45% of the
    # shorter window — keeping both would export two clips built from mostly
    # the same seconds. Genuinely different partial overlaps stay distinct:
    # e.g. 0-20 vs 9-29 shares only 11/20 = 0.55 with a 9s shift, and 0-15 vs
    # 10-25 shares 5/15 ≈ 0.33 with a 10s shift.
    return overlap_ratio >= 0.60 or (
        abs(left_start - right_start) <= 1.0 and overlap_ratio >= 0.45
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
    """Snap BOTH cut points to speech-block edges so cuts never split a word.

    * Start → the beginning of the speech block containing it (never jumps
      forward past the hook; a start inside a long pause stays untouched).
    * End → the end of the speech block containing it (finish the sentence),
      or the previous block's end when the raw end lands inside a pause
      (trim the silence instead of cutting the next sentence's first word).

    Previously only the start was snapped — the end could stay mid-word
    whenever the window was produced by duration clamping or a failed text
    match. Returns the raw window unchanged when nothing usable overlaps
    (word-level, missing, or AI-only transcripts).
    """
    start_time = max(0.0, float(start_time))
    end_time = max(start_time + 0.1, float(end_time))
    if not transcript_segments:
        return start_time, end_time
    blocks = _speech_blocks(transcript_segments)
    if not blocks:
        return start_time, end_time

    snapped_start = start_time
    for block_start, block_end in blocks:
        if block_start - 0.05 > start_time:
            break
        if start_time <= block_end:
            snapped_start = block_start
            break

    snapped_end = end_time
    prev_block_end = None
    for block_start, block_end in blocks:
        if abs(end_time - block_start) <= 0.05:
            # The end already sits exactly on a block boundary: it is
            # word-safe, so keep it byte-exact instead of pulling in the next
            # block's first word.
            snapped_end = end_time
            break
        if end_time < block_start:
            # End lands inside a pause: trim back to the sentence that
            # already finished instead of leaking into the next one.
            if prev_block_end is not None:
                snapped_end = prev_block_end
            break
        if block_start < end_time <= block_end:
            # End lands mid-sentence: extend to the sentence end so the
            # punchline is complete (the caller rejects snaps that would
            # violate the max-duration budget).
            snapped_end = block_end
            break
        prev_block_end = block_end
    else:
        # End beyond the last spoken word: trim the trailing silence.
        snapped_end = blocks[-1][1]

    # A snap that destroys the window is worse than no snap at all.
    if snapped_end <= snapped_start + 0.1:
        return start_time, end_time
    return snapped_start, snapped_end


def _has_any_anchor(segment):
    """Return True only when a raw candidate carries at least one placement anchor.

    Anchors are: a non-empty ``start_time_ref`` (other than the LLM "(0s)"
    sentinel, which the pipeline already treats as "no timestamp found"), an
    explicit numeric ``start_time``/``end_time``, or non-empty
    ``start_text``/``end_text``. A candidate with NONE of these has nothing to
    align against — the old code fabricated a window at the video head
    (0, min_duration) and produced bogus clips. A numeric ``0`` IS an anchor
    (an intentional start at the video head must not be discarded).
    """
    if not isinstance(segment, dict):
        return False
    ref = segment.get("start_time_ref")
    if ref not in (None, "", "(0s)") and str(ref).strip():
        return True
    for key in ("start_time", "end_time", "start_text", "end_text"):
        value = segment.get(key)
        if value is not None and str(value).strip():
            return True
    return False


# ---------------------------------------------------------------------------
# v7.41 — boundary policy helpers: text recovery, media duration, extension
# ---------------------------------------------------------------------------

def _align_text_time(transcript_segments, target_text, search_start_idx, *,
                     allow_same=True, min_similarity=0.55):
    """Fuzzy-align ``target_text`` to a transcript line.

    Mirrors the alignment used for the main start/end matching (the AI often
    paraphrases the transcript slightly). Returns ``(index, start_time,
    similarity)`` for the best line at/after ``search_start_idx`` or ``None``
    when nothing reaches ``min_similarity``. Used to RECOVER a reversed
    window from its text anchors instead of silently swapping the numbers.
    """
    target = _normalized_match_text(target_text)
    if not target or not transcript_segments:
        return None
    limit = min(len(transcript_segments), int(search_start_idx) + 200)
    best_index, best_similarity = -1, 0.0
    start_at = int(search_start_idx) + (0 if allow_same else 1)
    for index in range(max(0, start_at), limit):
        similarity = _text_similarity(
            target, _normalized_match_text(transcript_segments[index].get("text")))
        if similarity > best_similarity:
            best_similarity, best_index = similarity, index
        if best_similarity >= 0.999:
            break
    if best_index == -1 or best_similarity < min_similarity:
        return None
    return best_index, float(transcript_segments[best_index].get("start", 0.0)), best_similarity


def _recover_reversed_window(seg, transcript_segments, anchor_idx):
    """Rebuild a reversed (end < start) window from its start_text/end_text.

    Returns ``(start_time, end_time)`` when BOTH text anchors align to real
    transcript lines in a valid order, else ``None``. Recovering the model's
    semantic window from its own text is the only trustworthy repair; the
    numbers themselves are never silently swapped into a different span.
    """
    start_text = seg.get("start_text")
    end_text = seg.get("end_text")
    if not start_text or not end_text:
        return None
    start_hit = _align_text_time(transcript_segments, start_text, anchor_idx)
    if start_hit is None:
        return None
    start_index, start_time, _ = start_hit
    end_hit = _align_text_time(transcript_segments, end_text, start_index, allow_same=True)
    if end_hit is None:
        return None
    end_index, _end_start, _ = end_hit
    end_time = float(transcript_segments[end_index].get("end", _end_start))
    if end_time <= start_time + 0.1:
        return None
    return start_time, end_time


def probe_media_duration(path):
    """Real duration (seconds) of a media file via ffprobe, else ``None``.

    Used to validate that a candidate window lies inside the ACTUAL media,
    not merely inside the transcript. Never raises: missing file, missing
    ffprobe or a failed probe all return None so callers fall back to the
    transcript bounds.
    """
    if not path:
        return None
    try:
        if os.path.isdir(str(path)):
            return None
        import shutil
        import subprocess
        if shutil.which("ffprobe") is None:
            return None
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
            check=False)
        if completed.returncode != 0:
            return None
        value = float(completed.stdout.decode("utf-8", "replace").strip())
        return value if value > 0 else None
    except Exception:
        return None


def _transcript_text_between(transcript_segments, start_time, end_time):
    """Raw text of the transcript lines overlapping ``[start_time, end_time]``."""
    parts = []
    for line in transcript_segments or []:
        try:
            line_start = float(line.get("start", 0.0) or 0.0)
            line_end = float(line.get("end", line_start) or line_start)
        except (TypeError, ValueError):
            continue
        if line_start < end_time and line_end > start_time:
            text = str(line.get("text") or "").strip()
            if text:
                parts.append(text)
    return " ".join(parts).strip()


def _contextually_connected(base_text, added_text, max_gap=1.5, gap=None):
    """True when newly added speech continues the same idea.

    Connected when the added words share a content token with the existing
    window OR the two chunks are contiguous speech (a gap shorter than
    ``max_gap``) rather than a jump into unrelated material.
    """
    if gap is not None:
        try:
            if float(gap) <= float(max_gap):
                return True
        except (TypeError, ValueError):
            pass
    base_tokens = {token for token in _normalized_match_text(base_text).split()
                   if len(token) >= 3}
    added_tokens = {token for token in _normalized_match_text(added_text).split()
                    if len(token) >= 3}
    return bool(base_tokens & added_tokens)


def _extend_to_min_duration(start_time, end_time, min_duration,
                            transcript_segments, transcript_start,
                            transcript_end, max_duration):
    """Sentence-aware recovery for a window shorter than ``min_duration``.

    Searches neighbouring sentence units and accepts the FIRST expansion
    that (a) reaches the minimum, (b) stays inside the max duration and the
    transcript, and (c) is contextually connected to the window (shared
    content word, or contiguous speech). Returns
    ``(start, end, note)`` or ``None`` when no valid connected expansion
    exists — the caller then marks the candidate ``transcript_limited``
    instead of padding it with unrelated speech.
    """
    try:
        start_time = float(start_time)
        end_time = float(end_time)
        min_duration = float(min_duration)
    except (TypeError, ValueError):
        return None
    if end_time - start_time >= min_duration:
        return start_time, end_time, "no_extension_needed"
    if transcript_window is None or not transcript_segments:
        return None
    units = transcript_window.split_sentence_units(transcript_segments)
    if not units:
        return None
    window_text = _transcript_text_between(transcript_segments, start_time, end_time)

    for unit in units:
        unit_start = float(unit["start"])
        unit_end = float(unit["end"])
        if unit_end <= end_time - 0.05:
            continue
        new_end = min(max(unit_end, start_time + min_duration), float(transcript_end))
        if new_end - start_time < min_duration - 0.01:
            continue
        if new_end - start_time > float(max_duration) + 0.01:
            break
        if new_end <= end_time + 0.01:
            continue
        added = _transcript_text_between(transcript_segments, end_time, new_end)
        # Extending within the CURRENT sentence unit is always connected;
        # reaching PAST it (into another unit / silence) requires real added
        # speech that is contextually connected. Otherwise the "extension"
        # would just append dead air or unrelated material.
        extends_sentence = (unit_start <= end_time + 0.05
                            and new_end <= unit_end + 0.01)
        if not extends_sentence and not added:
            continue
        gap = unit_start - end_time
        if extends_sentence or _contextually_connected(window_text, added, gap=gap):
            return start_time, new_end, "min_duration_extended_forward"

    for unit in reversed(units):
        unit_start = float(unit["start"])
        unit_end = float(unit["end"])
        if unit_start >= start_time + 0.05:
            continue
        new_start = max(min(unit_start, end_time - min_duration), float(transcript_start))
        if end_time - new_start < min_duration - 0.01:
            continue
        if end_time - new_start > float(max_duration) + 0.01:
            break
        if new_start >= start_time - 0.01:
            continue
        added = _transcript_text_between(transcript_segments, new_start, start_time)
        extends_sentence = (unit_end >= start_time - 0.05
                            and new_start >= unit_start - 0.01)
        if not extends_sentence and not added:
            continue
        gap = start_time - unit_end
        if extends_sentence or _contextually_connected(window_text, added, gap=gap):
            return new_start, end_time, "min_duration_extended_backward"

    # Last resort: the whole transcript is shorter than the requested minimum,
    # so "the complete idea" IS the full transcript. Use it (still marked
    # transcript_limited by the caller) instead of emitting an arbitrarily
    # short slice of a source that simply has less material than min_duration.
    try:
        full_span = float(transcript_end) - float(transcript_start)
    except (TypeError, ValueError):
        full_span = 0.0
    if 0.0 < full_span <= min_duration + 0.05:
        return (float(transcript_start), float(transcript_end),
                "min_duration_full_transcript")
    return None


# Minimum speech content for a viable clip (v7.40 validation): at least one
# spoken word, and speech must cover more than a dead-air sliver of the
# window. Windows between REJECT and LOW thresholds stay but are flagged and
# scored down through audio/transcript-alignment factors; genuinely thin
# clips lose the ranking to denser ones via information_density.
MIN_WINDOW_WORDS = 1
REJECT_SPEECH_COVERAGE = 0.05
LOW_SPEECH_COVERAGE = 0.35

# Final-validator error codes that make a window unexportable. Everything
# else (mid-sentence truncation forced by max_duration, edge silence, a title
# needing review, safety/duplicate flags) is surfaced as a review flag.
FATAL_VALIDATION_CODES = frozenset({
    "missing_start_time", "missing_end_time", "invalid_start_time",
    "invalid_end_time", "end_not_after_start", "non_positive_duration",
    "duration_below_min", "duration_above_max", "out_of_media_bounds",
    "empty_transcript",
})


def _validate_segment_window(start_time, end_time, min_duration, analysis):
    """Hard validation of one candidate window (v7.40).

    Returns ``(rejected_reasons, quality_flags)`` — explicit, human-readable
    messages explaining why a segment was rejected (or what is suboptimal).
    A segment is REJECTED for: end<=start, negative/invalid timestamps,
    duration below the configured minimum (unless the transcript itself is
    the limit), insufficient transcript text, or excessive silence. Boundary
    issues that refinement could not fix become quality flags (they lower the
    score through the completion/context factors) unless the strict env
    ``VIRALCUTTER_STRICT_BOUNDARIES=1`` upgrades them to rejections.
    """
    rejected = []
    flags = []
    try:
        start_time = float(start_time)
        end_time = float(end_time)
    except (TypeError, ValueError):
        return ["invalid timestamps: not numeric"], flags
    if start_time < 0 or end_time < 0:
        rejected.append("invalid timestamps: negative value")
    if end_time <= start_time:
        rejected.append("end_time <= start_time")
    if rejected:
        return rejected, flags

    duration = end_time - start_time
    transcript_limited = bool(isinstance(analysis, dict) and analysis.get("transcript_limited"))
    if duration < float(min_duration) and not transcript_limited:
        rejected.append("duration {:.2f}s below minimum {:.2f}s".format(duration, float(min_duration)))

    if not isinstance(analysis, dict) or not analysis:
        rejected.append("insufficient transcript text inside window")
        return rejected, flags

    word_count = int(analysis.get("word_count") or 0)
    if word_count < MIN_WINDOW_WORDS:
        rejected.append("insufficient transcript text: {} word(s) inside window".format(word_count))

    coverage = float(analysis.get("speech_coverage") or 0.0)
    if analysis.get("text") and coverage < REJECT_SPEECH_COVERAGE:
        rejected.append("excessive silence: speech covers {:.0%} of the window".format(coverage))
    elif analysis.get("text") and coverage < LOW_SPEECH_COVERAGE:
        flags.append("low speech density: {:.0%} of the window".format(coverage))

    strict = os.getenv("VIRALCUTTER_STRICT_BOUNDARIES", "").strip().lower() in {"1", "true", "yes", "on"}
    if analysis.get("starts_mid_sentence"):
        message = "starts mid-sentence"
        (rejected if strict else flags).append(message)
    if analysis.get("ends_mid_sentence"):
        message = "ends mid-sentence"
        (rejected if strict else flags).append(message)
    if analysis.get("starts_with_connector"):
        flags.append("opens with a conjunction/connector")
    if analysis.get("ends_incomplete"):
        message = "ends on an incomplete phrase (preposition/conjunction)"
        (rejected if strict else flags).append(message)
    if float(analysis.get("leading_silence") or 0.0) > 1.5:
        flags.append("leading silence {:.1f}s".format(float(analysis.get("leading_silence"))))
    if float(analysis.get("trailing_silence") or 0.0) > 1.5:
        flags.append("trailing silence {:.1f}s".format(float(analysis.get("trailing_silence"))))
    return rejected, flags


def _completion_status(analysis):
    """Map a window analysis to the public completion_status label."""
    if not isinstance(analysis, dict) or not analysis.get("text"):
        return "incomplete"
    if analysis.get("complete"):
        return "complete"
    if analysis.get("ends_incomplete") or analysis.get("starts_mid_sentence") or analysis.get("ends_mid_sentence"):
        return "partial"
    return "complete"


def _compute_factor_scores(segment_entry, analysis, *, edge_match=1.0, title_relevance_ratio=0.0,
                           repetition_penalty=0.0, safety_penalty=0.0):
    """The 12-factor 0-100 score set for one candidate (v7.41).

    Genuine AI self-evaluations are preferred for the editorial factors;
    deterministic transcript-window heuristics fill every gap, so a candidate
    whose AI shipped no self-evaluation (quality_missing) is now ranked on
    real measurements instead of copies of its own virality score.
    """
    if clip_scoring is None:
        return {}
    virality = _bounded_score(segment_entry.get("score"), 0.0)

    # Per-component genuineness: a component the AI actually shipped (not
    # None) is used as-is even when other components are missing; only truly
    # missing ones fall back to the deterministic window heuristics. (The
    # all-or-nothing ``quality_missing`` flag stays for gate/UI compat.)
    def _genuine(key, fallback):
        value = segment_entry.get(key)
        if value is None:
            return fallback
        return _bounded_score(value, fallback)

    first_sentence = (analysis or {}).get("first_sentence") or ""
    window_text = (analysis or {}).get("text") or ""
    duration = float(segment_entry.get("duration") or 0.0)

    factors = {
        "hook_strength": _genuine(
            "hook_strength",
            clip_scoring.hook_strength_heuristic(first_sentence, fallback=virality)),
        "standalone_context": clip_scoring.standalone_context_score(analysis),
        "emotional_value": clip_scoring.emotional_value_heuristic(window_text),
        "information_density": clip_scoring.information_density_score(
            (analysis or {}).get("word_count") or 0, duration,
            (analysis or {}).get("unique_ratio")),
        "narrative_completeness": clip_scoring.narrative_completeness_score(analysis),
        "boundary_quality": clip_scoring.boundary_quality_score(analysis),
        "transcript_alignment": clip_scoring.transcript_alignment_score(analysis, edge_match),
        "audio_quality": clip_scoring.audio_quality_proxy(analysis),
        "visual_quality": clip_scoring.visual_quality_score(segment_entry),
        "title_relevance": round(max(0.0, min(100.0, float(title_relevance_ratio or 0.0) * 100.0)), 1),
        "repetition_penalty": round(max(0.0, min(100.0, float(repetition_penalty or 0.0))), 1),
        "safety_penalty": round(max(0.0, min(100.0, float(safety_penalty or 0.0))), 1),
    }
    # narrative_completeness (genuine AI eval) strengthens the narrative
    # factor when it shipped; clarity strengthens standalone context.
    if segment_entry.get("narrative_completeness") is not None:
        factors["narrative_completeness"] = round(
            0.6 * factors["narrative_completeness"]
            + 0.4 * _bounded_score(segment_entry.get("narrative_completeness"), virality), 1)
    if segment_entry.get("clarity_score") is not None:
        factors["standalone_context"] = round(
            0.6 * factors["standalone_context"]
            + 0.4 * _bounded_score(segment_entry.get("clarity_score"), virality), 1)
    return factors


def _apply_semantic_repetition_penalties(segments):
    """Compute repetition_penalty from semantic similarity between candidates.

    For every pair sharing the same idea with different wording (normalized
    window transcript similarity in [0.55, dup_threshold)) the LOWER-scored
    candidate earns a graduated penalty; pairs at/above the duplicate
    threshold are left for deduplicate_segments to drop outright. Order is
    deterministic: candidates are compared in their current list order and
    each candidate is penalized only against higher-ranked ones.
    """
    if transcript_window is None or not segments:
        return
    texts = [str(seg.get("transcript_text") or "") for seg in segments]
    if sum(1 for text in texts if len(text.split()) >= transcript_window.MIN_SEMANTIC_WORDS) < 2:
        return
    threshold = transcript_window.semantic_duplicate_threshold()
    slope = transcript_window.repetition_penalty_slope()
    lower_bound = 0.55
    for index, candidate in enumerate(segments):
        if len(texts[index].split()) < transcript_window.MIN_SEMANTIC_WORDS:
            continue
        worst = 0.0
        for prior in range(index):
            if len(texts[prior].split()) < transcript_window.MIN_SEMANTIC_WORDS:
                continue
            # Temporal near-duplicates are handled by deduplicate_segments;
            # the penalty gradient is for the same IDEA at DIFFERENT times.
            if _windows_are_near_duplicates(candidate, segments[prior]):
                continue
            similarity = transcript_window.guarded_semantic_similarity(texts[index], texts[prior])
            if similarity >= threshold:
                worst = max(worst, slope)
            elif similarity >= lower_bound:
                span = max(0.05, threshold - lower_bound)
                worst = max(worst, slope * (similarity - lower_bound) / span)
        if worst > 0.0:
            factors = candidate.get("score_breakdown") or {}
            factors["repetition_penalty"] = round(
                max(float(factors.get("repetition_penalty") or 0.0), worst), 1)
            candidate["score_breakdown"] = factors
            if clip_scoring is not None:
                candidate["selection_score"] = clip_scoring.compute_final_score(factors)


def process_segments(raw_segments, transcript_segments, min_duration, max_duration, output_count=None, snap_to_boundaries=True, project_folder=None, media_duration=None):
    """
    Aligns raw AI segments (with reference tags) to actual transcript timestamps.
    Applies constraints, validation, and deduplication.

    ``snap_to_boundaries`` (default True) aligns final cut points to sentence
    boundaries derived from transcript pauses, so cuts never split a word.

    ``project_folder`` lets the performance-learning loop
    (scripts/performance_weights) read ``performance_insights.json`` from the
    *project* that owns the clips instead of the process working directory.
    ``None`` keeps the legacy behaviour (current working directory).
    """
    
    all_segments = list(raw_segments)
    tempo_minimo = min_duration
    tempo_maximo = max_duration

    # v7.41: real media duration (when known) is the last word on whether a
    # window is valid — transcript bounds alone cannot catch an out-of-range
    # model timestamp on a shorter source file.
    if media_duration is None and project_folder:
        candidate_media = os.path.join(str(project_folder), "input.mp4")
        if os.path.isfile(candidate_media):
            media_duration = probe_media_duration(candidate_media)
    try:
        media_duration = float(media_duration) if media_duration is not None else None
    except (TypeError, ValueError):
        media_duration = None
    if media_duration is not None and media_duration <= 0:
        media_duration = None

    # v7.40: word-level timings (WhisperX input.json) let the boundary
    # refinement snap cut points to exact word edges; empty list → the
    # segment-level fallback path is used unchanged.
    word_timings = _load_word_timings(project_folder)
    
    # Sort segments by score (descending). Scores arrive as ints, floats OR
    # numeric strings depending on the AI backend — a single non-int value
    # used to kill the whole sort silently (bare except around int()).
    def _sort_score(item):
        try:
            return float(item.get('score', 0) or 0)
        except (TypeError, ValueError):
            return 0.0
    all_segments.sort(key=_sort_score, reverse=True)

    # --- POST-PROCESSING: Match Text to Timestamps ---
    processed_segments = []
    transcript_start_time = min(float(item.get("start", 0.0)) for item in transcript_segments)
    transcript_end_time = max(float(item.get("end", item.get("start", 0.0))) for item in transcript_segments)
    if transcript_end_time <= transcript_start_time:
        raise ValueError("Transcript timestamps must contain a positive duration.")
    
    print(f"[DEBUG] Matching {len(all_segments)} raw segments to timestamps...")
    
    for seg in all_segments:
        if not _has_any_anchor(seg):
            print(f"[WARN] Skipping unanchorable segment (no start/end time or start/end text): "
                  f"'{seg.get('title', 'Untitled')}' — nothing to align, window would be fabricated.")
            continue
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
                # Fuzzy alignment: the AI often paraphrases or slightly alters
                # the transcript wording, so exact containment fails and the
                # segment used to fall back to the wrong cut point.
                start_text_target = _normalized_match_text(seg.get('start_text'))
                final_start_time = -1
                match_start_idx = -1
                search_limit = min(len(transcript_segments), start_idx + 50)
                best_index, best_similarity = -1, 0.0
                for i in range(start_idx, search_limit):
                    similarity = _text_similarity(
                        start_text_target,
                        _normalized_match_text(transcript_segments[i]['text']))
                    if similarity > best_similarity:
                        best_similarity, best_index = similarity, i
                    if best_similarity >= 0.999:
                        break
                if start_text_target and best_similarity >= 0.55:
                    final_start_time = transcript_segments[best_index]['start']
                    match_start_idx = best_index
                if final_start_time == -1:
                    final_start_time = transcript_segments[start_idx]['start'] if start_idx < len(transcript_segments) else ref_time_val
                    match_start_idx = start_idx

            if explicit_end:
                final_end_time = _parse_segment_time(
                    seg.get("end_time"), default=final_start_time + tempo_minimo)
            else:
                end_text_target = _normalized_match_text(seg.get('end_text'))
                final_end_time = -1
                if match_start_idx != -1 and end_text_target:
                    search_end_limit = min(len(transcript_segments), match_start_idx + 200)
                    best_index, best_similarity = -1, 0.0
                    for i in range(match_start_idx, search_end_limit):
                        similarity = _text_similarity(
                            end_text_target,
                            _normalized_match_text(transcript_segments[i]['text']))
                        if similarity > best_similarity:
                            best_similarity, best_index = similarity, i
                        if best_similarity >= 0.999:
                            break
                    if best_index == match_start_idx and best_similarity >= 0.99:
                        # start_text and end_text are the SAME repeated
                        # catchphrase: the start line itself scores 1.0 and
                        # the clip would collapse to a ~0s window before the
                        # blind min-extension drags it past the real ending.
                        # Re-search strictly AFTER the start line for the
                        # intended later occurrence.
                        later_index, later_similarity = -1, 0.0
                        for i in range(match_start_idx + 1, search_end_limit):
                            similarity = _text_similarity(
                                end_text_target,
                                _normalized_match_text(transcript_segments[i]['text']))
                            if similarity > later_similarity:
                                later_similarity, later_index = similarity, i
                            if later_similarity >= 0.999:
                                break
                        if later_index != -1 and later_similarity >= 0.55:
                            final_end_time = transcript_segments[later_index]['end']
                    elif best_similarity >= 0.55:
                        final_end_time = transcript_segments[best_index]['end']
                if final_end_time == -1:
                    final_end_time = final_start_time + tempo_minimo

            # v7.41: a reversed window (end < start) is NOT silently swapped —
            # swapping can point at a completely different semantic span. First
            # try to recover the intended window from start_text/end_text; if
            # that is not reliable, reject the candidate with reason
            # "reversed_window" instead of guessing.
            if final_end_time < final_start_time:
                recovered = _recover_reversed_window(seg, transcript_segments, start_idx)
                if recovered is not None:
                    final_start_time, final_end_time = recovered
                    print("[WARN] Reversed window for '{}': recovered from text anchors "
                          "to [{:.2f}, {:.2f}].".format(
                              seg.get('title', 'Untitled'), final_start_time, final_end_time))
                else:
                    print("[WARN] Rejecting candidate '{}': reversed_window "
                          "(end < start and no reliable text recovery).".format(
                              seg.get('title', 'Untitled')))
                    continue

            # Keep explicit or text-matched windows inside the actual transcript.
            # This prevents malformed AI timestamps from producing empty or out-of-range clips.
            # Snapshot the aligned model edges: the min/max clamp below may move
            # them, and any moved edge must be snapped to a speech boundary too.
            # Effective upper bound: the real media duration (when known) is
            # authoritative — a model timestamp past the end of the actual
            # file must never survive into an ffmpeg cut.
            media_end = transcript_end_time
            if media_duration is not None:
                media_end = min(media_end, media_duration)
            raw_start_time = float(final_start_time)
            if raw_start_time > media_end:
                final_start_time = max(transcript_start_time, media_end - tempo_minimo)
            else:
                final_start_time = min(max(raw_start_time, transcript_start_time), media_end)
            final_end_time = min(max(float(final_end_time), final_start_time + 0.1), media_end)

            # Calculate Duration
            duration = final_end_time - final_start_time
            extension_note = None

            # Validate Duration (Min): sentence-aware, connectivity-checked
            # expansion. Never blindly drags in unrelated speech just to hit
            # the number — if no connected sentence reaches the minimum, the
            # window stays short and is marked transcript_limited below.
            if duration < tempo_minimo:
                expanded = _extend_to_min_duration(
                    final_start_time, final_end_time, tempo_minimo,
                    transcript_segments, transcript_start_time, media_end, tempo_maximo)
                if expanded is not None:
                    final_start_time, final_end_time, extension_note = expanded
                    duration = final_end_time - final_start_time
                    print(f"[DEBUG] Segment extended to satisfy min duration via "
                          f"sentence boundary ({extension_note}): {duration:.2f}s.")

            # Validate Duration (Max)
            if duration > tempo_maximo:
                print(f"[WARN] Segmento excede max duration ({duration:.2f}s > {tempo_maximo}s). Cortando para {tempo_maximo}s.")
                final_end_time = min(final_start_time + tempo_maximo, media_end)
                duration = final_end_time - final_start_time

            # Professional cut refinement (v7.41): snap BOTH edges to real
            # speech boundaries for EVERY window. Explicit numeric AI
            # timestamps are no longer trusted just because they are numeric;
            # a boundary that lands inside a word is always repaired, while an
            # already word-aligned edge in silence stays byte-exact.
            refinement_notes = []
            if extension_note:
                refinement_notes.append(extension_note)
            if snap_to_boundaries:
                snapped_start, snapped_end = snap_segment_boundaries(
                    final_start_time, final_end_time, transcript_segments)
                if snapped_start >= 0 and (snapped_end - snapped_start) >= tempo_minimo:
                    if (snapped_end - snapped_start) <= tempo_maximo:
                        if (snapped_start, snapped_end) != (final_start_time, final_end_time):
                            refinement_notes.append("sentence_boundary_snap")
                        final_start_time = snapped_start
                        final_end_time = snapped_end
                        duration = final_end_time - final_start_time
                    else:
                        # Snapping would break max_duration: record the
                        # conflict and keep the validated clamped window.
                        refinement_notes.append("snap_reverted_max_duration")
                elif (snapped_start, snapped_end) != (final_start_time, final_end_time):
                    refinement_notes.append("snap_reverted_min_duration")

                # v7.41: boundary refinement — word-edge snapping (when word
                # timings exist), Arabic connector openers (include the
                # antecedent sentence), dangling preposition/conjunction
                # endings (finish the sentence), then the configurable
                # pre/post-roll. Duration limits and media bounds are
                # re-enforced inside.
                if transcript_window is not None:
                    refined_start, refined_end, refine_notes = transcript_window.refine_boundaries(
                        final_start_time, final_end_time, transcript_segments,
                        words=word_timings,
                        min_duration=tempo_minimo,
                        max_duration=tempo_maximo,
                        transcript_start=transcript_start_time,
                        transcript_end=media_end)
                    refinement_notes.extend(refine_notes)
                    if (refined_end - refined_start) >= tempo_minimo or duration < tempo_minimo:
                        if (refined_start, refined_end) != (final_start_time, final_end_time):
                            final_start_time, final_end_time = refined_start, refined_end
                            duration = final_end_time - final_start_time
                    if "refinement_reverted_max_duration" in refine_notes:
                        refinement_notes.append("boundary_conflict_max_duration")

            # Word-edge safety net: even when snapping is disabled or an edge
            # was left untouched above, never keep a cut point that lands
            # inside a word when word-level timings are available.
            if word_timings and transcript_window is not None:
                word_start, word_end = transcript_window.snap_edges_to_words(
                    final_start_time, final_end_time, word_timings)
                if ((word_start, word_end) != (final_start_time, final_end_time)
                        and 0 < (word_end - word_start) <= tempo_maximo + 0.01):
                    final_start_time, final_end_time = word_start, word_end
                    duration = final_end_time - final_start_time
                    refinement_notes.append("word_boundary_snap")

            # Construct Final Segment
            hashtags = seg.get('hashtags', [])
            if isinstance(hashtags, str):
                hashtags = [h.strip().lstrip('#') for h in re.split(r'[,\s]+', hashtags) if h.strip()]
            # Title relevance is measured against the words ACTUALLY inside
            # the final cut window (not only the LLM's own start/end/caption,
            # which can hallucinate). Explicit numeric windows still benefit:
            # their window words are the real ones. When the LLM already
            # shipped an explicit recommended_title it is kept verbatim.
            window_text = _window_text_from_transcript(
                transcript_segments, final_start_time, final_end_time)
            recommended = seg.get('recommended_title') or _choose_recommended_title(seg, window_text=window_text)
            segment_entry = {
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
                # v7.41: a missing editorial self-evaluation is stored as None
                # — NEVER back-filled with the general virality score, which
                # would silently present the model's own hype as a verified
                # measurement. Deterministic heuristics rank the candidate;
                # quality_status says whether the AI score was genuine.
                "hook_strength": seg.get('hook_strength'),
                "narrative_completeness": seg.get('narrative_completeness'),
                "clarity_score": seg.get('clarity_score'),
                "novelty_score": seg.get('novelty_score'),
                "hashtags": hashtags,
                # A/B titles/captions (Roadmap 5.3): kept when the AI
                # returned them, otherwise fall back to the main title.
                "alt_titles": seg.get('alt_titles') or [seg.get('title', '')],
                "alt_captions": seg.get('alt_captions') or [seg.get('caption', '')],
                "recommended_title": recommended,
                "title_quality_score": _title_quality_score(recommended),
                "window_fingerprint": _segment_window_fingerprint(final_start_time, final_end_time),
            }
            # Transparency: when the AI shipped no self-evaluation for the
            # editorial components, the values above stay None and the
            # candidate is marked UNVERIFIED (not silently trusted).
            _component_keys = ("hook_strength", "narrative_completeness",
                               "clarity_score", "novelty_score")
            missing_components = [key for key in _component_keys
                                  if seg.get(key) is None]
            if missing_components:
                segment_entry["quality_missing"] = True
                segment_entry["missing_components"] = missing_components
                segment_entry["quality_status"] = "unverified"
            else:
                segment_entry["quality_status"] = "verified"
            if duration < tempo_minimo:
                # The whole transcript is shorter than the requested minimum
                # (or the window is pinned against the transcript edge), so
                # the under-min clip is transcript-limited, not a bug. Flag it
                # so downstream stages can decide instead of silently emitting
                # an under-length clip.
                segment_entry["under_min"] = True
                segment_entry["transcript_limited"] = True

            # --- v7.40: exact-window analysis, validation, factual titles ---
            if transcript_window is not None:
                analysis = transcript_window.analyze_window(
                    transcript_segments, final_start_time, final_end_time)
            else:
                analysis = {"text": window_text, "word_count": len(window_text.split()),
                            "speech_coverage": 1.0, "complete": True}
            analysis["transcript_limited"] = bool(segment_entry.get("transcript_limited"))

            rejected_reasons, quality_flags = _validate_segment_window(
                final_start_time, final_end_time, tempo_minimo, analysis)
            if rejected_reasons:
                print("[WARN] Segment '{}' rejected: {}".format(
                    seg.get('title', 'Untitled'), "; ".join(rejected_reasons)))
                continue

            raw_window_text = str(analysis.get("text") or "")
            hook_text = str(analysis.get("first_sentence") or raw_window_text)
            segment_entry["transcript_text"] = raw_window_text
            segment_entry["hook_text"] = hook_text
            segment_entry["completion_status"] = _completion_status(analysis)
            # Boundary-repair notes are kept for audit; only the ones that
            # mean "the ideal snap conflicted with the duration budget" become
            # quality flags (spec B.8).
            conflict_flags = [note for note in refinement_notes
                              if "reverted" in note or "conflict" in note]
            segment_entry["quality_flags"] = quality_flags + conflict_flags
            segment_entry["boundary_notes"] = list(refinement_notes)
            segment_entry["rejected_reasons"] = []
            segment_entry["window_analysis"] = {
                "before_text": analysis.get("before_text", ""),
                "after_text": analysis.get("after_text", ""),
                "last_sentence": analysis.get("last_sentence", ""),
                "leading_silence": analysis.get("leading_silence", 0.0),
                "trailing_silence": analysis.get("trailing_silence", 0.0),
                "speech_coverage": analysis.get("speech_coverage", 0.0),
                "starts_mid_sentence": analysis.get("starts_mid_sentence", False),
                "ends_mid_sentence": analysis.get("ends_mid_sentence", False),
                "ends_incomplete": analysis.get("ends_incomplete", False),
            }

            if title_factual is not None:
                # The title is validated against the EXACT clip-window text.
                # clip_ratio (window / whole transcript) lets the validator
                # reject a "whole video" framing on a short clip.
                total_span = max(0.001, transcript_end_time - transcript_start_time)
                clip_ratio = max(0.0, min(1.0, duration / total_span))
                content_language = title_factual.detect_content_language(raw_window_text)
                title_data = title_factual.build_title_data(
                    recommended, segment_entry.get("alt_titles") or [],
                    raw_window_text, analysis, content_language,
                    clip_ratio=clip_ratio)
                if title_data.get("fallback_used") and title_data.get("primary_title"):
                    # The LLM title failed factual validation: ship the
                    # conservative transcript-derived title instead and keep
                    # the original visible for audit.
                    title_data["llm_title_replaced"] = recommended
                    segment_entry["recommended_title"] = title_data["primary_title"]
                    segment_entry["title_quality_score"] = _title_quality_score(
                        title_data["primary_title"])
                segment_entry["title_data"] = title_data
                segment_entry["title_validation"] = title_data.get("title_validation") or {}
                # Only factually validated alternatives may be offered to the
                # reviewer/publisher; a hallucinated A/B title is dropped.
                validated_alts = [
                    item.get("text") for item in (title_data.get("alternative_titles") or [])
                    if item.get("text")
                ]
                if validated_alts:
                    segment_entry["alt_titles"] = validated_alts
                if title_data.get("title_review_required"):
                    segment_entry["title_review_required"] = True

            # Unverified editorial scores plus a non-complete/flagged boundary
            # cannot be auto-published: they go to manual review instead of
            # being silently shipped.
            if segment_entry.get("quality_status") == "unverified" and (
                    segment_entry.get("completion_status") != "complete"
                    or conflict_flags):
                segment_entry["requires_review"] = True
            if segment_entry.get("title_review_required"):
                segment_entry["requires_review"] = True
            segment_entry["publish_blocked_reason"] = (
                "manual_review_required" if segment_entry.get("requires_review") else "")
            segment_entry["_analysis_cache"] = analysis
            processed_segments.append(segment_entry)

        except Exception as e:
            print(f"[WARN] Error processing segment {seg}: {e}")
            continue

    # Add a transparent score before de-duplication and ranking.
    # The learning loop lives per-project (performance_insights.json), so
    # read it from the project that owns these clips — os.getcwd() used to
    # silently miss it for CLI/WebUI runs.
    weights_folder = project_folder or os.getcwd()
    perf_weights = performance_weights.load_weights(weights_folder) if performance_weights else None
    selection_weights = clip_scoring.load_selection_weights() if clip_scoring else None
    for candidate in processed_segments:
        candidate["selection_score"], candidate["selection_breakdown"] = _selection_score(candidate, perf_weights)
        # v7.41: the 12-factor editorial score becomes the primary selection
        # score. The legacy six-component breakdown above is kept for
        # backwards compatibility (review UI, performance learning), and the
        # new factor-by-factor breakdown ships as ``score_breakdown``.
        analysis = candidate.pop("_analysis_cache", None)
        if clip_scoring is not None and analysis is not None:
            window_text = candidate.get("transcript_text") or ""
            title_relevance_ratio = _title_content_relevance(
                candidate.get("recommended_title", ""), candidate,
                window_text=window_text)
            explicit_edges = 1.0
            factors = _compute_factor_scores(
                candidate, analysis, edge_match=explicit_edges,
                title_relevance_ratio=title_relevance_ratio)
            candidate["score_breakdown"] = factors
            candidate["selection_score"] = clip_scoring.compute_final_score(
                factors, selection_weights)
            candidate["selection_version"] = clip_scoring.SCORING_VERSION
        if perf_weights:
            # Bounded outcome-driven nudges learned from the channel's own
            # publish history (see performance_weights.py): duration and
            # title-quality correlation with views. Both stay tiny by design
            # so editorial ranking is never dominated by a thin sample.
            duration_bonus = float(perf_weights.get("duration_bonus", 0.0) or 0.0)
            title_boost = float(perf_weights.get("title_boost", 0.0) or 0.0)
            candidate["selection_score"] = round(max(0.0, min(100.0,
                float(candidate["selection_score"]) + duration_bonus + title_boost)), 1)
            candidate["selection_breakdown"]["performance_basis"] = perf_weights.get("basis", "defaults")
            if abs(title_boost) > 1e-9:
                candidate["selection_breakdown"]["title_boost"] = round(title_boost, 4)
            if abs(duration_bonus) > 1e-9:
                candidate["selection_breakdown"]["duration_bonus"] = round(duration_bonus, 4)
            # v7.33.3 — content-style learning: segments whose hook_type /
            # angle / topic / title style measured best on THIS channel get a
            # bounded bonus (performance_weights.style_bonuses).
            style_map = perf_weights.get("style") or {}
            if style_map and performance_weights is not None:
                style_bonus = performance_weights.style_bonus_for(candidate, style_map)
                if abs(style_bonus) > 1e-9:
                    candidate["selection_score"] = round(max(0.0, min(100.0,
                        float(candidate["selection_score"]) + style_bonus)), 1)
                    candidate["selection_breakdown"]["style_bonus"] = round(style_bonus, 2)
                    candidate["selection_breakdown"]["style_basis"] = style_map.get("basis", "content_insights")

    # v7.40: semantic repetition penalties — candidates expressing the same
    # idea as an already stronger candidate in different words lose points
    # BEFORE the editorial gate and de-duplication run.
    _apply_semantic_repetition_penalties(processed_segments)

    # v7.40: optional editorial floor — a thin video yields FEWER clips
    # instead of weak padding (env VIRALCUTTER_MIN_FINAL_SCORE, default off).
    if clip_scoring is not None:
        floor = clip_scoring.min_final_score()
        if floor > 0.0:
            before = len(processed_segments)
            processed_segments = [
                candidate for candidate in processed_segments
                if float(candidate.get("selection_score", 0.0) or 0.0) >= floor]
            dropped_floor = before - len(processed_segments)
            if dropped_floor:
                print("[WARN] Editorial floor {:.0f}: dropped {} weak candidate(s); "
                      "returning fewer, stronger clips.".format(floor, dropped_floor))

    # Editorial quality gate: drop candidates whose *genuine* self-evaluated
    # hook/narrative/clarity sit below the floor (weak clips lose the viewer
    # in the first seconds). Unverified (quality_missing) candidates are kept.
    gated, dropped = apply_quality_gate(processed_segments)
    for drop in dropped:
        print("[WARN] Quality gate dropped '{}' — {}. Choose stronger moments "
              "or raise the AI's editorial standards.".format(
                  drop.get("title"), ", ".join(drop.get("reasons", []))))
    if dropped:
        print("[WARN] Quality gate: kept {} of {} candidates (dropped {}).".format(
            len(gated), len(processed_segments), len(dropped)))
    processed_segments = gated

    # Deduplication: keep one title for each substantially identical source window.
    all_segments = deduplicate_segments(processed_segments)
    print(f"[DEBUG] Finished processing. {len(all_segments)} segments valid.")

    if output_count and len(all_segments) > output_count:
        print(f"Filtrando os top {output_count} segmentos de {len(all_segments)} candidatos encontrados nos chunks.")
        all_segments = _rank_segments_with_diversity(all_segments, output_count)
    else:
        all_segments = _rank_segments_with_diversity(all_segments)

    final_result = {"segments": all_segments}

    # v7.40/v7.41: record the exact selection configuration that produced
    # these segments (weights + schema/prompt versions + transcript content)
    # so staleness checks and the review UI can tell which pipeline generated
    # the list — and so a changed transcript/weight override forces
    # regeneration instead of silently reusing old windows/titles.
    if clip_scoring is not None:
        final_result["selection_config"] = {
            "schema_version": SEGMENTS_SCHEMA_VERSION,
            "scoring_version": clip_scoring.SCORING_VERSION,
            "title_schema_version": getattr(
                title_factual, "TITLE_VALIDATION_SCHEMA_VERSION", "legacy"),
            "prompt_version": prompt_version_fingerprint(),
            "weights": selection_weights or dict(clip_scoring.DEFAULT_SELECTION_WEIGHTS),
            "weights_fingerprint": selection_weights_fingerprint(),
            "transcript_fingerprint": transcript_fingerprint(transcript_segments),
        }

    # v7.41: ONE reusable final validator, called here before the segments can
    # be saved/cut/published. Structured errors (not just a bool) are attached
    # to the payload; only FATAL errors exclude a window from export — a
    # boundary that max_duration forced mid-sentence is a review flag, not a
    # reason to discard the best available window.
    validated_segments = []
    validation_errors = []
    for index, seg in enumerate(final_result['segments']):
        if 'start_time' not in seg:
            continue
        if segment_validator is not None:
            report = segment_validator.validate_final_segment(
                seg,
                min_duration=tempo_minimo,
                max_duration=tempo_maximo,
                media_duration=media_duration,
                require_title=False,
            )
            seg["final_validation"] = report
            fatal = [item for item in report.get("errors", [])
                     if item.get("code") in FATAL_VALIDATION_CODES]
            if not report.get("ok") and fatal:
                validation_errors.append({
                    "index": index,
                    "title": str(seg.get("title") or seg.get("recommended_title") or ""),
                    "errors": fatal,
                })
                print("[WARN] Final validator rejected '{}': {}".format(
                    seg.get("title", "Untitled"),
                    "; ".join(str(item.get("message")) for item in fatal)))
                continue
            if not report.get("ok"):
                seg["requires_review"] = True
                seg["publish_blocked_reason"] = "manual_review_required"
        validated_segments.append(seg)

    final_result['segments'] = validated_segments
    if validation_errors:
        final_result["validation_errors"] = validation_errors

    return final_result


def finalize_top_segments(segments, requested_count):
    """Return the top ``requested_count`` segments in final export/editorial order.

    Ordering contract (used by main_improved.py when persisting the final
    list):
    * Primary: entries carrying a ``candidate_rank`` (the diversity order
      assigned by ``_rank_segments_with_diversity``) sorted by that rank
      ascending.
    * Secondary: entries WITHOUT ``candidate_rank`` (legacy/externally-built
      candidates) come AFTER all ranked entries, ordered by
      ``selection_score`` descending, then ``score`` descending.
    * Ties of any kind keep their original list order (stable).
    Never raises: ``requested_count <= 0`` (or an unparsable count) returns
    ``[]``, and a count larger than the list returns the whole ordered list.
    """
    try:
        count = int(requested_count)
    except (TypeError, ValueError):
        return []
    if count <= 0:
        return []

    ranked, legacy = [], []
    for item in list(segments or []):
        if isinstance(item, dict) and item.get("candidate_rank") is not None:
            ranked.append(item)
        else:
            legacy.append(item)

    def _as_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    # Both sorts are stable, so equal keys keep the original list order.
    ranked.sort(key=lambda item: _as_float(item.get("candidate_rank")))
    legacy.sort(key=lambda item: (
        -_as_float(item.get("selection_score")),
        -_as_float(item.get("score")),
    ))
    return (ranked + legacy)[:count]


def segment_titles(segment):
    """A/B test titles for a segment: alt_titles + the main title (Roadmap 5.3).

    Returns a de-duplicated list ordered by measured title quality; the
    strongest candidate first becomes the recommended default. Ties keep the
    original order (main title last among equals, preserving legacy output).
    """
    seen, out = set(), []
    for t in list(segment.get("alt_titles") or []) + [segment.get("title", "")]:
        t = (t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    if not out:
        return ["Viral Segment"]
    order = {t: i for i, t in enumerate(out)}
    # Sort by quality DESC, original position as the stable tie-breaker.
    # The old key ``(order[t], -quality)`` was a no-op: order[t] is unique
    # per title, so the list always came back in insertion order and the
    # "strongest first" contract never actually held.
    out.sort(key=lambda t: (-_title_quality_score(t), order[t]))
    return out


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
        output_count=candidate_target,
        project_folder=project_folder,
    )