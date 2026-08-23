"""Secure local storage and validation for YouTube OAuth client secrets."""
from __future__ import annotations

import json
import os
import secrets as _secrets
import stat
import tempfile
from pathlib import Path

DEFAULT_DIR = Path.home() / ".viralcutter" / "youtube"
DEFAULT_CLIENT_SECRETS = DEFAULT_DIR / "client_secrets.json"
DEFAULT_TOKEN_FILE = DEFAULT_DIR / "token.json"
UPLOAD_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
FULL_SCOPES = ("https://www.googleapis.com/auth/youtube",)
REQUIRED_SCOPES = UPLOAD_SCOPES


class OAuthScopesError(RuntimeError):
    """Raised when a stored token cannot perform the requested YouTube action."""


def scopes_for_access(full_access=False):
    return list(FULL_SCOPES if full_access else UPLOAD_SCOPES)


def missing_scopes(granted_scopes, full_access=False):
    """Return required OAuth scopes absent from a stored credential."""
    granted = {str(scope).strip() for scope in (granted_scopes or []) if str(scope).strip()}
    return [scope for scope in scopes_for_access(full_access) if scope not in granted]


def require_scopes(credentials, full_access=False):
    """Validate credential scopes before making a YouTube API request."""
    missing = missing_scopes(getattr(credentials, "scopes", None), full_access)
    if missing:
        raise OAuthScopesError(
            "OAuth token lacks required YouTube permissions: {}. "
            "The old token must be revoked and YouTube login repeated.".format(
                ", ".join(missing)))
    return credentials


def _secure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, stat.S_IRWXU)
    except OSError:
        pass
    return path


def _atomic_private_write(path: Path, data: bytes) -> Path:
    _secure_directory(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=".youtube-", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            else:
                os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return path
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _read_json(path) -> dict:
    with open(path, "r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("OAuth file must contain a JSON object")
    return value


def validate_client_secrets_payload(payload: dict) -> dict:
    """Validate an installed-app or web OAuth client JSON without logging secrets."""
    if not isinstance(payload, dict):
        raise ValueError("OAuth file must contain a JSON object")
    client = payload.get("installed") or payload.get("web")
    if not isinstance(client, dict):
        raise ValueError("OAuth JSON must contain an 'installed' or 'web' client")
    required = ("client_id", "client_secret", "auth_uri", "token_uri")
    missing = [key for key in required if not str(client.get(key) or "").strip()]
    if missing:
        raise ValueError("OAuth JSON is missing: {}".format(", ".join(missing)))
    return {
        "client_type": "installed" if payload.get("installed") else "web",
        "client_id": str(client["client_id"]),
        "scopes": list(REQUIRED_SCOPES),
    }


def store_client_secrets(source_path, destination=None) -> dict:
    """Copy a user-selected OAuth JSON into private storage and report changes."""
    source = Path(str(source_path or "")).expanduser()
    if not source.is_file():
        raise FileNotFoundError("Selected OAuth JSON file was not found")
    if source.suffix.lower() != ".json":
        raise ValueError("YouTube OAuth file must have a .json extension")
    payload = _read_json(source)
    metadata = validate_client_secrets_payload(payload)
    target = Path(destination or os.getenv("YT_CLIENT_SECRETS_FILE") or DEFAULT_CLIENT_SECRETS).expanduser()
    previous = None
    if target.is_file():
        try:
            previous = _read_json(target)
        except Exception:
            previous = None
    changed = previous != payload
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    _atomic_private_write(target, data)
    metadata.update({"path": str(target), "stored": True, "changed": changed})
    return metadata


def invalidate_token(destination=None) -> bool:
    """Remove the stored OAuth token and report whether a file was removed."""
    token = Path(destination or os.getenv("YT_TOKEN_FILE") or DEFAULT_TOKEN_FILE).expanduser()
    try:
        existed = token.exists()
        if existed:
            token.unlink()
        return existed
    except OSError:
        return False


def replace_client_secrets(source_path, destination=None, invalidate_token=True) -> dict:
    """Install a new OAuth client JSON and optionally remove its old token.

    A token belongs to the previous OAuth client configuration in many Google
    Cloud setups. Explicit replacement therefore invalidates the stored token
    by default, forcing a fresh consent flow instead of reusing stale auth.
    """
    metadata = store_client_secrets(source_path, destination=destination)
    if invalidate_token:
        token = Path(token_path())
        try:
            existed = token.exists()
            if existed:
                token.unlink()
            metadata["token_invalidated"] = existed
        except OSError as exc:
            metadata["token_invalidated"] = False
            metadata["token_invalidation_error"] = str(exc)
    return metadata


def store_token_json(token_json, destination=None) -> str:
    """Store OAuth refresh-token JSON with private permissions and atomic replace."""
    target = Path(destination or os.getenv("YT_TOKEN_FILE") or DEFAULT_TOKEN_FILE).expanduser()
    if isinstance(token_json, str):
        data = token_json.encode("utf-8")
    else:
        data = json.dumps(token_json, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_private_write(target, data + (b"\n" if not data.endswith(b"\n") else b""))
    return str(target)


def client_secrets_path() -> str:
    configured = os.getenv("YT_CLIENT_SECRETS_FILE")
    return str(Path(configured).expanduser()) if configured else str(DEFAULT_CLIENT_SECRETS)


def token_path() -> str:
    configured = os.getenv("YT_TOKEN_FILE")
    return str(Path(configured).expanduser()) if configured else str(DEFAULT_TOKEN_FILE)


def status(full_access=False) -> dict:
    client = Path(client_secrets_path())
    token = Path(token_path())
    return {
        "client_secrets_present": client.is_file(),
        "token_present": token.is_file(),
        "client_secrets_path": str(client),
        "token_path": str(token),
        "scope": scopes_for_access(full_access)[0],
    }


def new_state_token() -> str:
    """Generate a non-secret correlation token for UI OAuth operations."""
    return _secrets.token_urlsafe(24)
