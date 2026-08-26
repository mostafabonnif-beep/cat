import json
import os
import sys

try:
    import torch  # optional: only required for the real whisperx transcription path
except Exception as _torch_err:
    torch = None
    _TORCH_IMPORT_ERROR = str(_torch_err)
else:
    _TORCH_IMPORT_ERROR = ""
import time

try:
    import whisperx
except Exception as _whisperx_err:
    # NOT just ModuleNotFoundError: a broken optional stack (e.g. a
    # transformers/tokenizers version conflict) must never kill the WebUI
    # or the rest of the pipeline — transcription degrades with a clear error.
    whisperx = None
    _WHISPERX_IMPORT_ERROR = str(_whisperx_err)
else:
    _WHISPERX_IMPORT_ERROR = ""
import gc
import re
from concurrent.futures import ThreadPoolExecutor

from i18n.i18n import I18nAuto
from scripts.transcription_diagnostics import (
    TranscriptionUnavailableError,
    build_error_message,
)

i18n = I18nAuto()


def _run_with_heartbeat(label, fn, stage="transcribe", start_percent=25, end_percent=85, interval=15):
    """Run a blocking WhisperX call while emitting visible WebUI heartbeats."""
    started = time.time()
    print(f"PROGRESS|{stage}|{start_percent}|{label}", flush=True)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisperx") as executor:
        future = executor.submit(fn)
        last_percent = start_percent
        while not future.done():
            time.sleep(interval)
            if future.done():
                break
            elapsed = int(time.time() - started)
            last_percent = min(end_percent - 1, last_percent + 1)
            print(f"PROGRESS|{stage}|{last_percent}|{label} — {elapsed}ث", flush=True)
        result = future.result()
    elapsed = int(time.time() - started)
    print(f"PROGRESS|{stage}|{end_percent}|{label} — اكتملت خلال {elapsed}ث", flush=True)
    return result


def _transcription_cache_path(output_folder):
    return os.path.join(output_folder, "transcription_cache.json")


def _read_transcription_cache(cache_path):
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _build_transcription_cache(input_file, model_name, srt_file, tsv_file, json_file, device="auto"):
    stat = os.stat(input_file)
    return {
        "input_file": os.path.abspath(input_file),
        "input_mtime_ns": stat.st_mtime_ns,
        "input_size": stat.st_size,
        "model_name": model_name,
        "device": str(device or "auto"),
        "outputs": {
            "srt": os.path.abspath(srt_file),
            "tsv": os.path.abspath(tsv_file),
            "json": os.path.abspath(json_file),
        },
    }


def _cache_matches(cache, input_file, model_name, srt_file, tsv_file, json_file, device="auto"):
    if not cache:
        return False
    try:
        stat = os.stat(input_file)
        outputs = cache.get("outputs", {})
        return (
            cache.get("input_file") == os.path.abspath(input_file)
            and cache.get("input_mtime_ns") == stat.st_mtime_ns
            and cache.get("input_size") == stat.st_size
            and cache.get("model_name") == model_name
            and cache.get("device", "auto") == str(device or "auto")
            and outputs.get("srt") == os.path.abspath(srt_file)
            and outputs.get("tsv") == os.path.abspath(tsv_file)
            and outputs.get("json") == os.path.abspath(json_file)
            and os.path.exists(srt_file)
            and os.path.exists(tsv_file)
            and os.path.exists(json_file)
        )
    except Exception:
        return False


def _save_transcription_cache(cache_path, input_file, model_name, srt_file, tsv_file, json_file, device="auto"):
    try:
        cache = _build_transcription_cache(input_file, model_name, srt_file, tsv_file, json_file, device=device)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Aviso: não foi possível salvar cache da transcrição: {e}")

def apply_safe_globals_hack():
    """
    Keep WhisperX/pyannote model loading working WITHOUT permanently disabling
    PyTorch's deserialization guard.

    We register the legacy classes as safe globals and wrap torch.load so it
    first tries the default safe path (weights_only=True) and only retries the
    unsafe path (weights_only=False) when the safe one actually fails AND the
    user has explicitly opted in via VIRALCUTTER_ALLOW_UNSAFE_LOAD=1.

    A poisoned/corrupted model file can execute arbitrary code during
    weights_only=False loading — the opt-in keeps that hole closed by default
    while remaining a documented escape hatch for genuinely legacy models.
    """
    try:
        import omegaconf
        if hasattr(torch.serialization, 'add_safe_globals'):
            torch.serialization.add_safe_globals([
                omegaconf.listconfig.ListConfig,
                omegaconf.dictconfig.DictConfig,
                omegaconf.base.ContainerMetadata,
                omegaconf.base.Node,
            ])
            print("Registrados safe globals do Omegaconf.")
    except Exception as e:
        print(f"Aviso ao registrar safe globals: {e}")

    allow_unsafe = os.environ.get(
        "VIRALCUTTER_ALLOW_UNSAFE_LOAD", "0"
    ).strip().lower() in ("1", "true", "yes", "on")
    try:
        original_load = torch.load

        def safe_load(*args, **kwargs):
            # Respect an explicit weights_only decision made by the caller.
            if kwargs.get("weights_only") is not None:
                return original_load(*args, **kwargs)
            try:
                return original_load(*args, weights_only=True, **kwargs)
            except Exception as safe_err:
                if not allow_unsafe:
                    raise RuntimeError(
                        "torch.load failed under weights_only=True ({!r}). If you "
                        "trust this model file and need legacy loading, set "
                        "VIRALCUTTER_ALLOW_UNSAFE_LOAD=1.".format(safe_err)
                    ) from safe_err
                print("WARNING: weights_only=True failed ({!r}); retrying with "
                      "weights_only=False (VIRALCUTTER_ALLOW_UNSAFE_LOAD=1)."
                      .format(safe_err))
                return original_load(*args, weights_only=False, **kwargs)

        torch.load = safe_load
    except Exception as e:
        print(f"Aviso ao aplicar wrapper de torch.load: {e}")

    try:
        import torchaudio
        if not hasattr(torchaudio, 'list_audio_backends'):
            torchaudio.list_audio_backends = lambda: []
            print("Aplicado monkeypatch em torchaudio.list_audio_backends para PyTorch >= 2.4.")
    except Exception:
        pass

def parse_srt(srt_path):
    """
    Parses an SRT file into a list of segments expected by WhisperX alignment.
    [{'start': float, 'end': float, 'text': str}, ...]
    """
    print(f"Parsing SRT: {srt_path}")
    segments = []
    try:
        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        content = content.replace('\r\n', '\n')
        blocks = content.strip().split('\n\n')
        
        def time_to_seconds(t_str):
            # SRT: 00:00:00,000
            t_str = t_str.replace(',', '.')
            parts = t_str.split(':')
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            elif len(parts) == 2:
                m, s = parts
                return int(m) * 60 + float(s)
            return 0.0

        for block in blocks:
            lines = block.split('\n')
            # Busca linha de tempo
            for i, line in enumerate(lines):
                if '-->' in line:
                    start_str, end_str = line.split(' --> ')
                    text_lines = lines[i+1:]
                    text = " ".join(text_lines).strip()
                    text = re.sub(r'<[^>]+>', '', text) # Remove tags
                    
                    if text:
                        start = time_to_seconds(start_str.strip())
                        end = time_to_seconds(end_str.strip())
                        segments.append({
                            "start": start,
                            "end": end,
                            "text": text
                        })
                    break
    except Exception as e:
        print(f"Error parsing SRT {srt_path}: {e}")
        return None
    return segments

def parse_vtt(vtt_path):
    """
    Parses a VTT file (WebVTT) into valid segments for WhisperX.
    """
    print(f"Parsing VTT: {vtt_path}")
    segments = []
    try:
        with open(vtt_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        def vtt_time_to_seconds(t_str):
            # VTT: 00:00:00.000 or 00:00.000
            t_str = t_str.strip()
            parts = t_str.split(':')
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            elif len(parts) == 2:
                m, s = parts
                return int(m) * 60 + float(s)
            return 0.0

        current_entry = {"text": []}
        
        for line in lines:
            line = line.strip()
            if not line:
                # Fim de bloco, salva se tiver tempo e texto
                if "start" in current_entry and current_entry["text"]:
                    full_text = " ".join(current_entry["text"]).strip()
                    # Limpeza extra VTT
                    full_text = re.sub(r'<[^>]+>', '', full_text)
                    full_text = re.sub(r'&[^;]+;', '', full_text)
                    
                    if full_text:
                        segments.append({
                            "start": current_entry["start"],
                            "end": current_entry["end"],
                            "text": full_text
                        })
                current_entry = {"text": []}
                continue
            
            if line.startswith("WEBVTT") or line.startswith("X-TIMESTAMP-MAP") or line.startswith("NOTE"):
                continue

            # Timestamp line: 00:00:05.000 --> 00:00:10.000 (pode ter settings depois)
            if "-->" in line:
                times = line.split("-->")
                start_str = times[0].strip()
                end_str = times[1].strip().split(" ")[0] # remove settings
                current_entry["start"] = vtt_time_to_seconds(start_str)
                current_entry["end"] = vtt_time_to_seconds(end_str)
            else:
                # É texto (se já tivermos timestamps)
                if "start" in current_entry:
                     current_entry["text"].append(line)
                     
        # Salva ultimo bloco se existir
        if "start" in current_entry and current_entry["text"]:
            full_text = " ".join(current_entry["text"]).strip()
            full_text = re.sub(r'<[^>]+>', '', full_text)
            if full_text:
                segments.append({
                    "start": current_entry["start"],
                    "end": current_entry["end"],
                    "text": full_text
                })

    except Exception as e:
        print(f"Error parsing VTT {vtt_path}: {e}")
        return None
    return segments

def _placeholder_allowed():
    """Escape hatch for testing stages without the transcription stack.

    Enable with env VIRALCUTTER_ALLOW_PLACEHOLDER=1 (or --allow-placeholder-transcription
    in the CLI). Default OFF: the pipeline fails fast with clear instructions
    instead of silently producing garbage segments from a fake transcript.
    """
    return os.getenv("VIRALCUTTER_ALLOW_PLACEHOLDER", "").strip().lower() in {
        "1", "true", "yes", "on"}


def resolve_model_candidates(model_name):
    """Ordered model candidates for transcription (v6.7).

    Some faster-whisper builds reject "large-v3-turbo"/"turbo" as invalid model
    sizes. Return the requested name first, then supported fallbacks, so the
    loader can degrade gracefully instead of crashing.
    """
    name = str(model_name or "large-v3").strip()
    candidates = [name]
    if name in ("large-v3-turbo", "turbo"):
        candidates += ["large-v3", "medium"]
    if name not in ("large-v3",) and "large-v3" not in candidates:
        candidates.append("large-v3")
    return candidates


def _transcription_backend_preference():
    """Return a validated backend preference without importing either stack."""
    preference = os.getenv("VIRALCUTTER_TRANSCRIPTION_BACKEND", "auto").strip().lower()
    return preference if preference in {"auto", "whisperx", "faster-whisper"} else "auto"


def _run_faster_whisper_fallback(
    input_file,
    model_name,
    project_folder,
    device,
    srt_file,
    tsv_file,
    json_file,
    cache_path,
):
    """Run the optional independent backend and persist pipeline-compatible outputs."""
    from scripts import transcription_fallback

    probe = transcription_fallback.availability()
    if not probe.get("ok"):
        raise ImportError(probe.get("error") or "faster-whisper is unavailable")
    result = _run_with_heartbeat(
        "جاري التفريغ الاحتياطي عبر faster-whisper",
        lambda: transcription_fallback.transcribe(
            input_file,
            model_name=model_name,
            device=device,
            progress=None,
        ),
        stage="transcribe",
        start_percent=25,
        end_percent=94,
    )
    transcription_fallback.write_outputs(result, srt_file, tsv_file, json_file)
    _save_transcription_cache(
        cache_path,
        input_file,
        model_name,
        srt_file,
        tsv_file,
        json_file,
        device=device,
    )
    print("[transcribe] Backend: faster-whisper fallback", flush=True)
    return srt_file, tsv_file


def transcribe(input_file, model_name='large-v3', project_folder='tmp', device='auto'):
    backend_preference = _transcription_backend_preference()
    primary_missing = whisperx is None or torch is None
    use_fallback = backend_preference == "faster-whisper" or (
        backend_preference == "auto" and primary_missing
    )
    if primary_missing or use_fallback:
        output_folder = project_folder or os.path.dirname(input_file) or 'tmp'
        os.makedirs(output_folder, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        srt_file = os.path.join(output_folder, f"{base_name}.srt")
        tsv_file = os.path.join(output_folder, f"{base_name}.tsv")
        json_file = os.path.join(output_folder, f"{base_name}.json")
        cache_path = _transcription_cache_path(output_folder)
        cache = _read_transcription_cache(cache_path)
        if _cache_matches(cache, input_file, model_name, srt_file, tsv_file, json_file, device=device):
            print("Transcrição de fallback já em cache. Reutilizando JSON/SRT/TSV existentes.")
            return srt_file, tsv_file
        if use_fallback:
            try:
                return _run_faster_whisper_fallback(
                    input_file,
                    model_name,
                    output_folder,
                    device,
                    srt_file,
                    tsv_file,
                    json_file,
                    cache_path,
                )
            except Exception as error:
                print(f"[transcribe] faster-whisper fallback indisponível: {error}")
                msg = build_error_message(
                    whisperx_error=_WHISPERX_IMPORT_ERROR,
                    torch_error=_TORCH_IMPORT_ERROR,
                    base_dir=project_folder,
                )
                msg += "\nمحاولة faster-whisper الاحتياطية فشلت: {}".format(error)
        else:
            msg = build_error_message(
                whisperx_error=_WHISPERX_IMPORT_ERROR,
                torch_error=_TORCH_IMPORT_ERROR,
                base_dir=project_folder,
            )
        if not _placeholder_allowed():
            raise TranscriptionUnavailableError(msg)
        print("[transcribe] WARNING: " + msg)
        print("[transcribe] Placeholder subtitles will be generated \u2014 viral-segment "
              "selection will NOT work; only downstream tooling can be tested.")
        placeholder = {"segments": [{"start": 0.0, "end": 2.0, "text": "WhisperX not installed. Install it for full transcription."}], "language": "en"}
        with open(srt_file, 'w', encoding='utf-8') as f:
            f.write("1\n00:00:00,000 --> 00:00:02,000\nWhisperX not installed. Install it for full transcription.\n")
        with open(tsv_file, 'w', encoding='utf-8') as f:
            f.write("start\tend\ttext\n0.0\t2.0\tWhisperX not installed. Install it for full transcription.\n")
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(placeholder, f, ensure_ascii=False, indent=2)
        _save_transcription_cache(cache_path, input_file, model_name, srt_file, tsv_file, json_file, device=device)
        return srt_file, tsv_file
    print(i18n(f"Iniciando transcrição de {input_file}..."))
    if torch is None:
        raise ImportError(
            "torch is required for transcription (pip install torch). "
            "The rest of ViralCutter works without it.")
    
    # Diagnóstico de Ambiente
    print(f"DEBUG: Python: {sys.executable}")
    print(f"DEBUG: Torch: {torch.__version__}")
    
    start_time = time.time()
    
    if project_folder is None:
        project_folder = os.path.dirname(input_file)
        if not project_folder:
            project_folder = 'tmp'

    output_folder = project_folder
    os.makedirs(output_folder, exist_ok=True)
    
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    srt_file = os.path.join(output_folder, f"{base_name}.srt")
    tsv_file = os.path.join(output_folder, f"{base_name}.tsv")
    json_file = os.path.join(output_folder, f"{base_name}.json")

    cache_path = _transcription_cache_path(output_folder)
    cache = _read_transcription_cache(cache_path)

    if _cache_matches(cache, input_file, model_name, srt_file, tsv_file, json_file, device=device):
        print("Transcrição já em cache. Reutilizando JSON/SRT/TSV existentes.")
        return srt_file, tsv_file

    # Verifica se os arquivos já existem
    if os.path.exists(srt_file) and os.path.exists(tsv_file) and os.path.exists(json_file):
        print("Os arquivos SRT, TSV e JSON já existem. Pulando a transcrição.")
        _save_transcription_cache(cache_path, input_file, model_name, srt_file, tsv_file, json_file)
        return srt_file, tsv_file

    # Device setup: auto detects CUDA; explicit CPU/CUDA follows the user's choice.
    requested_device = str(device or "auto").strip().lower()
    if requested_device not in {"auto", "cpu", "cuda"}:
        requested_device = "auto"
    if requested_device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was selected, but no usable NVIDIA CUDA device is available. Choose CPU or Auto.")
    device = "cuda" if requested_device == "cuda" or (requested_device == "auto" and torch.cuda.is_available()) else "cpu"
    print(f"[transcribe] Device: {device} (requested: {requested_device})")
    compute_type = "float16" if device == "cuda" else "float32"

    try:
        apply_safe_globals_hack()
        
        # 1. Carregar Áudio (sempre necessário)
        print(f"Carregando áudio: {input_file}")
        audio = whisperx.load_audio(input_file)
        
        # 2. Verificar se existem legendas baixadas para Alignment Only
        # Procurar por *.srt E *.vtt na pasta que comecem com input (ou o nome base)
        if os.path.exists(os.path.join(output_folder, "input.srt")):
            potential_subs = [os.path.join(output_folder, "input.srt")]
        elif os.path.exists(os.path.join(output_folder, "input.vtt")):
            potential_subs = [os.path.join(output_folder, "input.vtt")]
        else:
            potential_subs = []
        
        start_segments = None
        alignment_only = False
        
        # Default blind guess if we have no info
        detected_language = "en" 

        if potential_subs:
            sub_path = potential_subs[0]
            print(f"Usando legenda fornecida: {sub_path}")
            
            if sub_path.endswith('.srt'):
                parsed = parse_srt(sub_path)
            elif sub_path.endswith('.vtt'):
                parsed = parse_vtt(sub_path)
            else:
                parsed = None

            if parsed and len(parsed) > 0:
                start_segments = parsed
                alignment_only = True
                
                # Forçar EN conforme solicitado pelo usuário para alinhamento
                detected_language = 'en'
                print(f"Idioma forçado para alinhamento: {detected_language}")
                
                print("--- MODO ALINHAMENTO RÁPIDO ATIVADO ---")
        
        result = None
        
        if alignment_only and start_segments:
            # Pular Transcrição, ir direto para Alinhamento
            print("--- MODO ALINHAMENTO RÁPIDO ATIVADO ---")
            # Estrutura que o align espera: {'segments': [...], 'language': ...}
            # Mas o align recebe segments como lista.
            pass 
        else:
            # 3. Transcrever (Caminho Normal)
            print("Nenhuma legenda válida encontrada. Realizando transcrição completa (WhisperX)...")
            print(f"Carregando modelo {model_name}...")
            model = None
            last_load_err = None
            for candidate in resolve_model_candidates(model_name):
                try:
                    model = _run_with_heartbeat(
                        f"جاري تحميل نموذج WhisperX {candidate} وVAD — قد يستغرق ذلك وقتاً أول مرة",
                        lambda candidate=candidate: whisperx.load_model(
                            candidate,
                            device,
                            compute_type=compute_type,
                            asr_options={"hotwords": None}
                        ),
                        stage="transcribe",
                        start_percent=25,
                        end_percent=45,
                    )
                    if candidate != model_name:
                        print("[transcribe] '{}' not supported by this faster-whisper "
                              "build — falling back to '{}'.".format(model_name, candidate))
                    break
                except ValueError as load_err:
                    if "Invalid model size" in str(load_err) or "expected one of" in str(load_err):
                        last_load_err = load_err
                        continue
                    raise
                except Exception as load_err:
                    conflict = str(load_err).lower()
                    if "huggingface-hub" in conflict or "huggingface_hub" in conflict:
                        raise TranscriptionUnavailableError(
                            build_error_message(whisperx_error=str(load_err), base_dir=project_folder)
                        ) from load_err
                    raise
            if model is None:
                raise last_load_err or ValueError(
                    "No usable Whisper model size for '{}'".format(model_name))

            result = _run_with_heartbeat(
                "جاري التفريغ الصوتي على الجهاز المحدد",
                lambda: model.transcribe(audio, batch_size=16, chunk_size=10),
                stage="transcribe",
                start_percent=45,
                end_percent=72,
            )
            
            detected_language = result["language"]
            start_segments = result["segments"]
            
            # Limpar modelo de transcrição
            if device == "cuda":
                del model
                gc.collect()
                torch.cuda.empty_cache()

        # 4. Alinhar (Sempre executado, seja com subs parsed ou transcritos)
        print(f"Alinhando transcrição (Idioma: {detected_language}) para obter timestamps precisos...", flush=True)
        print("PROGRESS|transcribe|74|جاري محاذاة الكلمات العربية — قد يستغرق ذلك وقتاً", flush=True)
        # Usa o modelo específico solicitado pelo usuário: WAV2VEC2_ASR_LARGE_LV60K_960H
        # Mas o whisperx.load_align_model escolhe automaticamente baseado na linguagem.
        # Se for inglês, ele usa wav2vec2-large-960h-lv60-self geralmente.
        # Não podemos forçar facilmente o modelo exato sem hackear o whisperx, mas o padrão é bom.
        
        try:
            model_a, metadata = _run_with_heartbeat(
                "جاري تحميل نموذج محاذاة الكلمات",
                lambda: whisperx.load_align_model(language_code=detected_language, device=device),
                stage="transcribe",
                start_percent=74,
                end_percent=82,
            )
            
            aligned_result = _run_with_heartbeat(
                "جاري محاذاة الكلمات والتوقيتات",
                lambda: whisperx.align(start_segments, model_a, metadata, audio, device, return_char_alignments=False),
                stage="transcribe",
                start_percent=82,
                end_percent=94,
            )
            
            # aligned_result agora contém "segments" com word timestamps
            result = aligned_result
            result["language"] = detected_language
            
            if device == "cuda":
                 del model_a
                 torch.cuda.empty_cache()
                 
        except Exception as e:
            print(f"Erro durante alinhamento: {e}. ")
            if alignment_only:
                 print("Falha crítica no alinhamento de legendas externas. Abortando usage de legendas externas.")
                 # Opcional: Fallback para transcrição normal se falhar? Seria complexo aqui pois já limpamos memória.
                 # Vamos apenas salvar o que temos (timestamps da legenda original podem não bater com áudio perfeitamente se não alinhar)
                 result = {"segments": start_segments, "language": detected_language}
            else:
                 print("Continuando com transcrição bruta.")

        # 5. Salvar Resultados
        print("Salvando resultados...")
        from whisperx.utils import get_writer
        
        save_options = {
            "highlight_words": False,
            "max_line_count": None,
            "max_line_width": None
        }
        
        # Se veio do alignment_only, result é {'segments': [...], ...}
        # Se o alinhamento falhou, result tem segments originais.
        
        # WhisperX writers esperam um dicionário result com chaves 'segments', 'language'.
        
        writer_srt = get_writer("srt", output_folder)
        writer_srt(result, input_file, save_options)
        
        writer_tsv = get_writer("tsv", output_folder)
        writer_tsv(result, input_file, save_options)
        
        writer_json = get_writer("json", output_folder)
        writer_json(result, input_file, save_options)

        _save_transcription_cache(cache_path, input_file, model_name, srt_file, tsv_file, json_file)
        
        end_time = time.time()
        elapsed = end_time - start_time
        print(f"Processamento concluído em {int(elapsed//60)}m {int(elapsed%60)}s.")

    except Exception as e:
        print(f"ERRO CRÍTICO na transcrição: {e}")
        import traceback
        traceback.print_exc()
        raise

    if not os.path.exists(srt_file):
        print(f"AVISO: Arquivo SRT {srt_file} não encontrado após execução.")
    
    return srt_file, tsv_file