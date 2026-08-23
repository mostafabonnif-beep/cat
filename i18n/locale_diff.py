import json
import os
import sys
from collections import OrderedDict

# en_US is the canonical reference locale (the app falls back to it at
# runtime — i18n/i18n.py). zh_CN was removed in v7.18, so use en_US.
base_dir = os.path.dirname(os.path.abspath(__file__))
standard_file = os.path.join(base_dir, "locale/en_US.json")

# Find all JSON files in the directory
dir_path = os.path.join(base_dir, "locale/")
languages = [
    os.path.join(dir_path, f)
    for f in sorted(os.listdir(dir_path))
    if f.endswith(".json") and f != os.path.basename(standard_file)
]

# Load the standard file
with open(standard_file, "r", encoding="utf-8") as f:
    standard_data = json.load(f, object_pairs_hook=OrderedDict)

# Loop through each language file
for lang_file in languages:
    # Load the language file
    with open(lang_file, "r", encoding="utf-8") as f:
        lang_data = json.load(f, object_pairs_hook=OrderedDict)

    # Find the difference between the language file and the standard file
    diff = set(standard_data.keys()) - set(lang_data.keys())

    miss = set(lang_data.keys()) - set(standard_data.keys())

    # Add any missing keys to the language file (as English fallback text)
    for key in diff:
        lang_data[key] = key

    # Del any extra keys to the language file
    for key in miss:
        del lang_data[key]

    # Sort the keys of the language file to match the order of the standard file
    lang_data = OrderedDict(
        sorted(lang_data.items(), key=lambda x: list(standard_data.keys()).index(x[0]))
    )

    # Save the updated language file
    with open(lang_file, "w", encoding="utf-8") as f:
        json.dump(lang_data, f, ensure_ascii=False, indent=4, sort_keys=True)
        f.write("\n")
    print(f"[locale_diff] {os.path.basename(lang_file)}: "
          f"{len(diff)} keys added, {len(miss)} keys removed")
