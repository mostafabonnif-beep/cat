import json
import os
import sys
import tempfile

from i18n.i18n import I18nAuto

i18n = I18nAuto()


def _atomic_write_json(path, data):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".viral_segments.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=4)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def save_viral_segments(segments_data=None, project_folder="tmp", overwrite=False):
    output_txt_file = os.path.join(project_folder, "viral_segments.txt")

    # Sobrescrita explícita (usado pelo filtro de segurança)
    if overwrite and segments_data is not None:
        _atomic_write_json(output_txt_file, segments_data)
        print(i18n("Viral segments saved to {}").format(output_txt_file) + "\n")
        return

    # Verifica se o arquivo já existe
    if not os.path.exists(output_txt_file):
        if segments_data is None:
            # Never block automation: without an interactive terminal there
            # is nobody to answer the prompt, so skip instead of hanging.
            if not sys.stdin.isatty():
                print(i18n("No segments data provided and no interactive input available. Skipping save."))
                return
            # Solicita ao usuário que insira o JSON caso o arquivo não exista e os segmentos não estejam definidos
            while True:
                user_input = input(i18n("\nPlease enter the JSON in the desired format:\n"))
                try:
                    # Tenta carregar o JSON inserido
                    segments_data = json.loads(user_input)

                    # Valida se o formato está correto
                    if "segments" in segments_data and isinstance(segments_data["segments"], list):
                        # Salva os dados em um arquivo JSON
                        _atomic_write_json(output_txt_file, segments_data)
                        print(i18n("Viral segments saved to {}").format(output_txt_file))
                        break
                    else:
                        print(i18n("Invalid format. Make sure the structure is correct."))
                except json.JSONDecodeError:
                    print(i18n("Error decoding JSON. Please check the formatting."))
                print(i18n("Please try again."))
        else:
            # Caso os segmentos tenham sido gerados, salva automaticamente
            _atomic_write_json(output_txt_file, segments_data)
            print(i18n("Viral segments saved to {}").format(output_txt_file) + "\n")
    else:
        print(i18n("The file {} already exists. No additional input needed.").format(output_txt_file))
