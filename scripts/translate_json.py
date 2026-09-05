import asyncio
import json
import os
from pathlib import Path

import requests
import tqdm.asyncio


class GoogleTranslator:
    """Small requests-based adapter for Google's public translation endpoint."""

    endpoint = "https://translate.googleapis.com/translate_a/single"

    def __init__(self, source="auto", target="en", timeout=30):
        self.source = source or "auto"
        self.target = target
        self.timeout = timeout

    def translate(self, text):
        response = requests.get(
            self.endpoint,
            params={
                "client": "gtx",
                "sl": self.source,
                "tl": self.target,
                "dt": "t",
                "q": text,
            },
            headers={"User-Agent": "OUSSAMA-Cutter/7.26"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
            raise ValueError("translation service returned an invalid response")
        translated = "".join(
            str(part[0])
            for part in payload[0]
            if isinstance(part, list) and part and part[0]
        )
        if not translated.strip():
            raise ValueError("translation service returned empty text")
        return translated

RTL_LANGS = {"ar", "he", "fa", "ur"}


def is_rtl_language(target_lang: str) -> bool:
    return (target_lang or "").split("-")[0].lower() in RTL_LANGS


def apply_rtl_text(text: str) -> str:
    if not text:
        return text
    return f"\u202B{text}\u202C"


def should_render_rtl(target_lang: str) -> bool:
    return is_rtl_language(target_lang)

# Lista de idiomas alvo
target_languages = ['en']

# Dicionário de substituições por idioma
substituicoes_por_idioma = {
    'en': {
        # 'Original': 'Translation'
    },
}

# Configurações de tradução
sentence_endings = ['.', '!', '?', ')', 'よ', 'ね', 'の', 'さ', 'ぞ', 'な', 'か', '！', '。', '」', '…']
separator = " ◌ "
separator_unjoin = separator.replace(' ', '')
chunk_max_chars = 4999

def substituir_texto(text, substituicoes):
    """Função para substituir texto."""
    for old, new in substituicoes.items():
        text = text.replace(old, new)
    return text

async def translate_chunk(index, chunk, target_lang, max_attempts=4, retry_delay=5):
    """Translate one chunk with bounded retries and original-text fallback."""
    attempts = max(1, int(max_attempts))
    last_error = None
    for attempt in range(attempts):
        try:
            translator = GoogleTranslator(source='auto', target=target_lang)
            translated_chunk = await asyncio.get_running_loop().run_in_executor(None, translator.translate, chunk)
            await asyncio.sleep(0)

            if translated_chunk is None or len(translated_chunk.replace(separator.strip(), '').split()) == 0:
                return chunk

            return translated_chunk
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            delay = max(0, float(retry_delay)) * (attempt + 1)
            print(f"\r[chunk {index}]: Exception: {exc}. Retrying in {delay:.1f}s...", flush=True)
            await asyncio.sleep(delay)
    print(f"\r[chunk {index}]: Translation failed after {attempts} attempts; keeping source text: {last_error}", flush=True)
    return chunk

def join_sentences(texts, max_chars):
    joined_texts = []
    current_chunk = ""

    for text in texts:
        if not text or text is None:
            text = 'ㅤ'

        if len(current_chunk) + len(text) + len(separator) <= max_chars:
            current_chunk += text + separator
            if any(text.endswith(ending) for ending in sentence_endings):
                joined_texts.append(current_chunk)
                current_chunk = ""
        else:
            if current_chunk:
                joined_texts.append(current_chunk)
                current_chunk = ""
            if len(text) + len(separator) <= max_chars:
                current_chunk += text + separator
            else:
                end_index = text.rfind(' ', 0, max_chars - (1 + len(separator)))
                if end_index == -(1 + len(separator)) or end_index < 0:
                    end_index = max_chars - (1 + len(separator))
                joined_texts.append((text[:end_index] + '…' + separator)[:max_chars])

    if current_chunk:
        joined_texts.append(current_chunk)

    return joined_texts

def unjoin_sentences(original_sentence: str, modified_sentence: str, separator: str):
    if original_sentence is None:
        return ' '

    original_texts = original_sentence.split(separator)
    original_texts = [s.strip() for s in original_texts if s.strip()]

    if modified_sentence is None:
        return original_texts or ' '

    modified_sentence = modified_sentence.replace(f"{separator_unjoin} ", f"{separator_unjoin}").replace(f" {separator_unjoin}", f"{separator_unjoin}").replace(
        f"{separator_unjoin}.", f".{separator_unjoin}").replace(f"{separator_unjoin},", f",{separator_unjoin}")

    modified_texts = modified_sentence.split(separator_unjoin)
    modified_texts = [s.strip() for s in modified_texts if s.strip()]

    if original_texts == "..." or original_texts == "…":
        return original_texts

    if len(original_texts) == len(modified_texts):
        return modified_texts

    original_word_count = sum(len(text.split()) for text in original_texts)
    modified_word_count = len(' '.join(modified_texts).split())
    
    if original_word_count == 0 or modified_word_count == 0:
        return original_sentence.replace(separator, ' ').strip()

    modified_words_proportion = modified_word_count / original_word_count
    modified_words = ' '.join(modified_texts).split()

    new_modified_texts = []
    current_index = 0

    for original_text in original_texts:
        num_words = max(1, int(round(len(original_text.split()) * modified_words_proportion)))
        text_words = modified_words[current_index:current_index + num_words]
        new_modified_texts.append(' '.join(text_words))
        current_index += num_words

    if current_index < len(modified_words):
        new_modified_texts[-1] += ' ' + ' '.join(modified_words[current_index:])

    return new_modified_texts or original_texts or ' '

def adjust_segments(segments):
    for i, current_segment in enumerate(segments):
        next_segment = segments[i + 1] if i < len(segments) - 1 else None

        text_words = str(current_segment.get("text") or "").split()
        if not text_words:
            current_segment["words"] = []
            continue

        start = float(current_segment.get("start", 0))
        end = float(current_segment.get("end", start))
        duration = max(0, end - start)
        current_segment["words"] = [
            {
                "word": word,
                "start": start + (idx * duration / len(text_words)),
                "end": start + ((idx + 1) * duration / len(text_words)),
                "score": 1.0,
            }
            for idx, word in enumerate(text_words)
        ]

        last_word = current_segment["words"][-1]
        if next_segment:
            next_start = float(next_segment.get("start", last_word["start"]))
            extended_end = min(next_start, last_word["start"] + 2)
        else:
            extended_end = min(end + 2, last_word["start"] + 2)
        last_word["end"] = max(last_word["start"], extended_end)
        current_segment["end"] = last_word["end"]

        if next_segment and next_segment.get("words"):
            next_segment["words"][0]["start"] = next_segment.get("start", 0)

    return segments

async def translate_json_file(json_file_path: Path, translated_json_path: Path, target_lang):
    with open(json_file_path, 'r', encoding='utf-8') as file:
        data = json.load(file)

    segments = data.get('segments') or []
    if not isinstance(segments, list):
        raise ValueError("subtitle JSON must contain a segments list")
    texts_to_translate = [segment.get('text', '') for segment in segments if segment.get('text')]
    words_to_translate = [
        word.get('word', '')
        for segment in segments
        for word in (segment.get('words') or [])
        if isinstance(word, dict)
    ]

    all_texts = texts_to_translate + words_to_translate
    chunks = join_sentences(all_texts, chunk_max_chars)
    translated_chunks = [None] * len(chunks)

    tasks = []
    semaphore = asyncio.Semaphore(7)

    async def translate_async():
        async def run_translate(index, chunk, lang):
            while True:
                try:
                    async with semaphore:
                        result = await asyncio.wait_for(translate_chunk(index, chunk, lang), 120)
                    translated_chunks[index] = result
                    break
                except Exception:
                    await asyncio.sleep(3)

        for index, chunk in enumerate(chunks):
            task = asyncio.create_task(run_translate(index, chunk, target_lang))
            tasks.append(task)

        for tsk in tqdm.asyncio.tqdm_asyncio.as_completed(tasks, total=len(tasks), desc="Translating", unit="chunks", unit_scale=False, leave=True, bar_format="{desc} {percentage:3.0f}% | {n_fmt}/{total_fmt} | ETA: {remaining} | ⏱: {elapsed}"):
            await tsk

    await translate_async()

    print('Processing translation...', end='')

    unjoined_texts = [unjoin_sentences(chunk, translated_chunks[i], separator_unjoin) for i, chunk in enumerate(chunks)]
    unjoined_texts = [text for sublist in unjoined_texts for text in sublist if text]

    translated_texts = unjoined_texts[:len(texts_to_translate)]
    translated_words = unjoined_texts[len(texts_to_translate):]

    word_index = 0
    text_index = 0
    for segment in segments:
        text = segment.get('text') or ''
        if text:
            segment['text'] = translated_texts[text_index] if text_index < len(translated_texts) else text
            text_index += 1
        for word in segment.get('words') or []:
            if not isinstance(word, dict) or not word.get('word'):
                continue
            if word_index < len(translated_words):
                word['word'] = translated_words[word_index]
                word_index += 1
            else:
                print(f"\nWarning: Not enough translated words. Keeping original word: {word['word']}")

    # Ajusta os segmentos após a tradução
    segments = adjust_segments(segments)

    data['segments'] = segments

    os.makedirs(translated_json_path.parent, exist_ok=True)
    with open(translated_json_path, 'w', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    print('\r                         ', end='\r')

    return data
    
async def main():
    folder_path = './JSON/'

    for filename in os.listdir(folder_path):
        if filename.endswith('.json'):
            base_name = os.path.splitext(filename)[0]

            for lang in target_languages:
                output_filename = f'{base_name}_{lang}.json'
                output_file_path = os.path.join(folder_path, output_filename)
                
                if not os.path.exists(output_file_path):
                    print(f'Traduzindo para {lang}: {filename}')
                    translated_data = await translate_json_file(Path(os.path.join(folder_path, filename)), Path(output_file_path), lang)
                    
                    if lang in substituicoes_por_idioma:
                        for segment in translated_data['segments']:
                            segment['text'] = substituir_texto(segment['text'], substituicoes_por_idioma[lang])
                            for word in segment['words']:
                                word['word'] = substituir_texto(word['word'], substituicoes_por_idioma[lang])
                    
                    with open(output_file_path, 'w', encoding='utf-8') as file:
                        json.dump(translated_data, file, ensure_ascii=False, indent=2)

            # Realiza as substituições no arquivo original JSON após todas as traduções
            original_file_path = os.path.join(folder_path, filename)
            with open(original_file_path, 'r', encoding='utf-8') as file:
                original_data = json.load(file)
            
            for segment in original_data['segments']:
                segment['text'] = substituir_texto(segment['text'], substituicoes_por_idioma['en'])
                for word in segment['words']:
                    word['word'] = substituir_texto(word['word'], substituicoes_por_idioma['en'])
            
            with open(original_file_path, 'w', encoding='utf-8') as file:
                json.dump(original_data, file, ensure_ascii=False, indent=2)

    print('Traduções e substituições concluídas.')

async def translate_project_subs(project_folder: str, target_lang: str):
    """
    Translates all _processed.json files in the 'subs' folder of the project.
    Creates a backup of the original as _original.json.
    """
    subs_folder = Path(project_folder) / "subs"
    if not subs_folder.exists():
        print(f"Subtitle folder not found: {subs_folder}")
        return

    # Look for files ending in _processed.json
    json_files = list(subs_folder.glob("*_processed.json"))
    
    if not json_files:
        print("No subtitle files found to translate.")
        return

    print(f"Found {len(json_files)} subtitle files to translate to '{target_lang}'...")

    for json_file in json_files:
        # Backup logic
        backup_file = json_file.with_name(json_file.stem + "_original" + json_file.suffix)
        
        source_file = json_file
        if backup_file.exists():
             print(f"Using existing backup for {json_file.name} as source.")
             source_file = backup_file
        else:
             print(f"Backing up original to {backup_file.name}...")
             try:
                # Rename current to backup
                json_file.rename(backup_file)
                source_file = backup_file
             except Exception as e:
                 print(f"Error creating backup for {json_file.name}: {e}")
                 continue
        
        # Translate source (backup) -> target (original filename)
        # effectively replacing the file read by the next step
        print(f"Translating {source_file.name} -> {json_file.name} ({target_lang})...")
        try:
            await translate_json_file(source_file, json_file, target_lang)
            
            # Apply language specific substitutions if any
            if target_lang in substituicoes_por_idioma:
                 with open(json_file, 'r', encoding='utf-8') as f:
                     data = json.load(f)
                 
                 modified = False
                 for segment in data.get('segments', []):
                    # Text
                    new_text = substituir_texto(segment['text'], substituicoes_por_idioma[target_lang])
                    if new_text != segment['text']:
                        segment['text'] = new_text
                        modified = True
                    
                    # Words
                    for word in segment.get('words', []):
                        w_text = word.get('word', '')
                        new_w_text = substituir_texto(w_text, substituicoes_por_idioma[target_lang])
                        if new_w_text != w_text:
                            word['word'] = new_w_text
                            modified = True
                 
                 if modified:
                     with open(json_file, 'w', encoding='utf-8') as f:
                         json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"Error translating {json_file.name}: {e}")
            # If failed and output doesn't exist, try to restore backup?
            if not json_file.exists() and backup_file.exists():
                print("Restoring backup due to failure...")
                backup_file.rename(json_file)

    print("Translation batch finished.")

if __name__ == "__main__":
    asyncio.run(main())