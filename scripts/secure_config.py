# -*- coding: utf-8 -*-
"""
Secure API config — stop storing Gemini keys in plaintext.

Roadmap item 4.4 ("مفتاح API مشفّر"). Priority order for resolving the
Gemini key:

    1. env VIRALCUTTER_GEMINI_KEY / GEMINI_API_KEY   (recommended, CI-safe)
    2. encrypted store (api_config.secure.json, Fernet AES)   [new]
    3. legacy plaintext api_config.json (with a warning)

Encryption: uses `cryptography` (Fernet) when installed
otherwise falls
back to a scrypt-derived XOR obfuscation and warns that it is NOT
real encryption — install `cryptography` for real protection. The
passphrase never touches the file
it is asked once interactively or via
the VIRALCUTTER_CONFIG_PASSPHRASE env var.

API: resolve_api_key(), set_key(), get_key(), load_api_config() (returns
the merged config dict the rest of the app already expects).
"""

import base64
import hashlib
import json
import os
import re
import tempfile

SECURE_CONFIG = "api_config.secure.json"
LEGACY_CONFIG = "api_config.json"

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


def _base_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def secure_config_path(base_dir=None):
    return os.path.join(base_dir or _base_dir(), SECURE_CONFIG)


def legacy_config_path(base_dir=None):
    return os.path.join(base_dir or _base_dir(), LEGACY_CONFIG)


def _derive_key(passphrase, salt):
    """32-byte key from passphrase + salt (scrypt)."""
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)



def _encrypt_blob(plain: bytes, passphrase: str) -> str:
    # SECURITY: the insecure XOR "obfuscation" format (v1) was removed in
    # 7.0.0-pro — credential storage now fails closed: without real
    # encryption available we refuse to write anything.
    if not HAS_CRYPTOGRAPHY:
        raise RuntimeError("cryptography is required for secure credential storage")
    salt = os.urandom(16)
    key = base64.urlsafe_b64encode(_derive_key(passphrase, salt))
    token = Fernet(key).encrypt(plain)
    return json.dumps({"v": 2, "salt": base64.b64encode(salt).decode(),
                       "token": token.decode()})


def _decrypt_blob(payload: str, passphrase: str) -> bytes:
    data = json.loads(payload)
    salt = base64.b64decode(data["salt"])
    if data.get("v") == 2:
        if not HAS_CRYPTOGRAPHY:
            raise RuntimeError("this config needs 'cryptography' (pip install cryptography)")
        key = base64.urlsafe_b64encode(_derive_key(passphrase, salt))
        return Fernet(key).decrypt(data["token"].encode())
    raise RuntimeError("insecure legacy credential format is not supported")


def _clean_keys(keys):
    """Return up to three unique, non-empty key strings."""
    if isinstance(keys, str):
        keys = [keys]
    result = []
    for value in keys or []:
        value = str(value or "").strip()
        if value and value not in result:
            result.append(value)
        if len(result) >= 3:
            break
    return result


def set_keys(api_keys, passphrase=None, base_dir=None):
    """Store up to three Gemini keys encrypted. Returns the secure path."""
    if not passphrase:
        passphrase = os.getenv("VIRALCUTTER_CONFIG_PASSPHRASE", "").strip()
    if not passphrase:
        raise ValueError("a passphrase is required (or set VIRALCUTTER_CONFIG_PASSPHRASE)")
    if not HAS_CRYPTOGRAPHY:
        raise RuntimeError("cryptography is required for secure credential storage")
    keys = _clean_keys(api_keys)
    path = secure_config_path(base_dir)
    data = {"gemini": {"api_keys": [_encrypt_blob(key.encode("utf-8"), passphrase) for key in keys]}}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try: os.chmod(path, 0o600)
        except OSError: pass
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
    return path


def set_key(api_key, passphrase=None, base_dir=None):
    """Backward-compatible single-key wrapper."""
    return set_keys([api_key], passphrase=passphrase, base_dir=base_dir)


def get_keys(passphrase=None, base_dir=None):
    """Read encrypted Gemini keys, including the legacy single-key format."""
    path = secure_config_path(base_dir)
    if not os.path.exists(path):
        return []
    if not passphrase:
        passphrase = os.getenv("VIRALCUTTER_CONFIG_PASSPHRASE", "").strip()
    if not passphrase:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        gemini = data.get("gemini") or {}
        encrypted = gemini.get("api_keys")
        if encrypted is None and gemini.get("api_key"):
            encrypted = [gemini["api_key"]]
        return _clean_keys(_decrypt_blob(item, passphrase).decode("utf-8") for item in (encrypted or []))
    except Exception:
        return []


def get_key(passphrase=None, base_dir=None):
    """Backward-compatible first encrypted key."""
    keys = get_keys(passphrase=passphrase, base_dir=base_dir)
    return keys[0] if keys else None


def _legacy_keys(base_dir=None):
    path = legacy_config_path(base_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        gemini = data.get("gemini") or {}
        values = gemini.get("api_keys") or [gemini.get("api_key", "")]
        return [key for key in _clean_keys(values) if key not in ("SUA_KEY_AQUI", "YOUR_KEY_HERE")]
    except Exception:
        return []


def _env_keys():
    raw = os.getenv("VIRALCUTTER_GEMINI_KEYS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return _clean_keys(parsed)
        except Exception:
            pass
        return _clean_keys(re.split(r"[,;\n]+", raw))
    for env in ("VIRALCUTTER_GEMINI_KEY", "GEMINI_API_KEY"):
        val = os.getenv(env, "").strip()
        if val:
            return [val]
    return []


def resolve_api_keys(base_dir=None, warn=True):
    """Resolve up to three keys: env → encrypted store → legacy file."""
    keys = _env_keys()
    if keys:
        return keys
    keys = get_keys(base_dir=base_dir)
    if keys:
        return keys
    keys = _legacy_keys(base_dir)
    if keys and warn:
        print("[secure-config] WARNING: reading Gemini key(s) from plaintext "
              "api_config.json. Set VIRALCUTTER_CONFIG_PASSPHRASE and save again "
              "to move them to the encrypted store.")
    return keys


def resolve_api_key(base_dir=None, warn=True):
    """Backward-compatible first-key resolver."""
    keys = resolve_api_keys(base_dir=base_dir, warn=warn)
    return keys[0] if keys else None


def load_api_config(base_dir=None):
    """Merged config dict (same shape api_config.json users expect), with the
    resolved key injected so downstream code needs no changes."""
    path = legacy_config_path(base_dir)
    config = {
        "selected_api": "gemini",
        "gemini": {"api_key": "", "model": "gemini-2.5-flash-lite-preview-09-2025",
                   "chunk_size": 20000},
        "g4f": {"model": "gpt-4o-mini", "chunk_size": 2000},
    }
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    if loaded.get("selected_api"):
                        config["selected_api"] = loaded["selected_api"]
                    if isinstance(loaded.get("gemini"), dict):
                        config["gemini"].update(loaded["gemini"])
                    if isinstance(loaded.get("g4f"), dict):
                        config["g4f"].update(loaded["g4f"])
        except Exception:
            pass
    keys = resolve_api_keys(base_dir)
    config["gemini"]["api_keys"] = keys
    config["gemini"]["api_key"] = keys[0] if keys else ""
    return config


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ViralCutter secure API config.")
    parser.add_argument("--set", metavar="KEY", help="store an API key (encrypted)")
    parser.add_argument("--get", action="store_true", help="print the resolved key")
    parser.add_argument("--passphrase", default=None,
                        help="passphrase (or VIRALCUTTER_CONFIG_PASSPHRASE env)")
    parser.add_argument("--no-warn", action="store_true")
    args = parser.parse_args()
    if args.set:
        path = set_key(args.set, args.passphrase)
        print("key stored encrypted in {}".format(path))
        if not HAS_CRYPTOGRAPHY:
            print("WARNING: 'cryptography' not installed — using obfuscation only. "
                  "Run: pip install cryptography")
    elif args.get:
        key = resolve_api_key(warn=not args.no_warn)
        print(key or "(no key configured)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
