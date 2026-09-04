# -*- coding: utf-8 -*-
"""
Upload Gate — the mandatory last safety barrier before anything is published.

Roadmap item 2.2 ("الرفع المباشر + بوابة رفض إجبارية"). The gate itself is
fully implemented and enforced here; the platform SDK clients (YouTube /
TikTok / Instagram) plug into it so *no* upload path can bypass the check.

The gate refuses (raises / exits non-zero) when a clip:
    1. is on the publish_blocklist written by risk_scorecard
       (reused-content / high overall risk),
    2. was blocked by the safety filter (safety_report.json),
    3. fails the live metadata compliance check (title / caption / hashtags),
    4. is missing its final rendered video.

Design notes
------------
* Pure stdlib, no network calls inside the gate itself → unit-testable.
* `check_clip()` returns a structured verdict; `gate_upload()` is the
  enforcement wrapper (raises `UploadGateError`).
* Platform uploaders are thin adapters that MUST call `gate_upload()` first.
* YouTube (OAuth + Data API v3), TikTok (OAuth2 + Content Posting API) and
  Instagram (Graph API Reels) are fully implemented; run the OAuth flow once
  with `--auth <platform>` to obtain/store tokens (see --auth below).
* The optional music_fingerprint.json report (Roadmap 2.3) is consulted via
  `music_gate`: "warn" flags matched audio, "block" refuses the upload.
"""

import datetime as _datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from scripts import content_guard
from scripts.metadata_compliance import check_metadata, summarize_metadata

PUBLISH_BLOCKLIST = "publish_blocklist.json"
SAFETY_REPORT = "safety_report.json"
SCORECARD = "risk_scorecard.json"


class UploadGateError(Exception):
    """Raised when a clip must not be published. Carries structured reasons."""

    def __init__(self, reasons):
        self.reasons = reasons  # list of {"source": str, "detail": str, "severity": str}
        joined = "; ".join("{}: {}".format(r["source"], r["detail"]) for r in reasons)
        super().__init__("Upload refused by ViralCutter safety gate: " + joined)


# ---------------------------------------------------------------------------
# Evidence loaders
# ---------------------------------------------------------------------------

def _load_json(project_folder, name):
    path = os.path.join(project_folder, name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _blocklist_reasons(project_folder, index):
    reasons = []
    scorecard = _load_json(project_folder, SCORECARD)
    summary = (scorecard or {}).get("summary") or {}
    if summary.get("visual_gate_failed"):
        reasons.append({
            "source": "visual_safety",
            "detail": "visual safety scan was required but no usable local model was available",
            "severity": "high",
        })
    if summary.get("ocr_gate_failed"):
        reasons.append({
            "source": "ocr_safety",
            "detail": "OCR safety scan was required but no usable Tesseract installation was available",
            "severity": "high",
        })
    data = _load_json(project_folder, PUBLISH_BLOCKLIST)
    if not data:
        return reasons
    for entry in data.get("blocked", []):
        if index is not None and entry.get("index") != index:
            continue
        score = (entry.get("axes") or {}).get("reuse", {}).get("score")
        why = "high overall risk" if score is None else "reused-content score ~{:.0f}%".format(score)
        reasons.append({
            "source": "publish_blocklist",
            "detail": "clip #{} is BLOCKED for publish ({})".format(
                entry.get("index", "?"), why),
            "severity": "high",
        })
    return reasons


def _safety_report_reasons(project_folder, index):
    reasons = []
    data = _load_json(project_folder, SAFETY_REPORT)
    if not data:
        return reasons

    legacy_blocked = data.get("blocked", [])
    entries = legacy_blocked
    if isinstance(entries, dict):
        entries = entries.get("segments", [])
    if not isinstance(entries, list):
        entries = []
    legacy_list_mode = isinstance(legacy_blocked, list)

    # New reports store every segment under `segments` and use statuses such as
    # `blocked`, `ai_blocked`, and `manual_review`. Older reports may store only
    # a `blocked` list; both shapes remain supported.
    entries.extend(data.get("segments", []) if isinstance(data.get("segments"), list) else [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_index = entry.get("index")
        if index is not None and entry_index not in (index, None):
            continue
        status = str(entry.get("status", "")).lower()
        semantic = entry.get("semantic") or {}
        is_manual_review = status == "manual_review" or bool(
            semantic.get("action") == "review"
        )
        is_legacy_blocked = legacy_list_mode and (
            status == "" and ("reason" in entry or entry.get("blocked") is True)
        )
        is_blocked = status in {"blocked", "semantic_blocked", "ai_blocked", "censor"} or is_legacy_blocked or bool(
            entry.get("blocked") is True
        )
        if not (is_blocked or is_manual_review):
            continue
        if is_manual_review:
            detail = "segment requires manual safety review before publishing: {}".format(
                semantic.get("explanation") or entry.get("title", "?"))
        else:
            detail = "segment blocked by safety filter: {}".format(
                entry.get("reason", entry.get("title", "?")))
        reasons.append({
            "source": "semantic_safety" if is_manual_review else "safety_report",
            "detail": detail,
            "severity": "high",
        })
    return reasons


def _provenance_reasons(project_folder, index):
    data = _load_json(project_folder, "provenance_report.json") or {}
    if str(data.get("policy", "warn")).lower() != "block":
        return []
    reasons = []
    for item in data.get("clips", []) or []:
        if not isinstance(item, dict) or (index is not None and item.get("index") != index):
            continue
        if item.get("action") != "block":
            continue
        reasons.append({
            "source": "provenance",
            "detail": "rights or meaningful transformation evidence is incomplete for clip #{}: {}".format(
                item.get("index", "?"), "; ".join(item.get("reasons", []) or ["review required"])),
            "severity": "high",
        })
    return reasons


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def check_clip(project_folder, index=None, title="", caption="", hashtags=None,
               extra_rules_path=None, require_video=False, music_gate=None,
               video_path=None, platform="youtube"):
    """Evaluate one clip against every safety barrier.

    Returns {"allowed": bool, "reasons": [...], "metadata": {...}}.
    Never raises — callers decide what to do with the verdict.

    `music_gate`: "warn" (default, flagged), "block" (refused) or "off"
    (ignored) — controls how music_fingerprint.json matches are treated.
    """
    reasons = []
    guard_verdict = {"allowed": True, "reasons": [], "evidence": {}}
    try:
        guard_verdict = content_guard.assess_clip(
            project_folder, index, title=title, video_path=video_path,
            platform=platform,)
        if not guard_verdict.get("allowed", True):
            reasons.extend(guard_verdict.get("reasons", []))
    except Exception as exc:
        # The legacy safety gate remains authoritative if the optional local
        # registry cannot be opened; record the diagnostic without publishing
        # a false block caused by a filesystem problem.
        guard_verdict = {"allowed": True, "reasons": [], "evidence": {"error": str(exc)}}
    reasons += _blocklist_reasons(project_folder, index)
    reasons += _provenance_reasons(project_folder, index)
    reasons += _safety_report_reasons(project_folder, index)

    # Audio copyright fingerprint report (Roadmap 2.3) — optional module.
    try:
        from scripts.music_fingerprint import music_gate_reasons
        reasons += music_gate_reasons(project_folder, index, gate=music_gate)
    except Exception:
        pass  # never let an optional check crash the gate

    meta = check_metadata(title, caption, hashtags or [], extra_rules_path)

    # Defense in depth: a caller that skipped the preprocessing pipeline must
    # still pass a local semantic check on publish metadata. This never sends
    # text to the network and treats ambiguous policy language as review-needed.
    try:
        from scripts.semantic_safety import analyze_text
        semantic = analyze_text(" ".join([
            str(title or ""), str(caption or ""),
            " ".join(str(tag) for tag in (hashtags or [])),
        ]))
        if semantic.get("action") in {"block", "review"}:
            reasons.append({
                "source": "semantic_safety",
                "detail": "publish metadata requires safety review: {}".format(
                    semantic.get("explanation", "policy pattern detected")),
                "severity": "high",
            })
    except Exception:
        pass

    if not meta["ok"]:
        reasons.append({
            "source": "metadata_compliance",
            "detail": summarize_metadata(meta),
            "severity": meta["severity"],
        })

    if require_video and index is not None:
        found = video_path if video_path and os.path.isfile(video_path) else _find_clip_video(project_folder, index)
        if not found:
            reasons.append({
                "source": "missing_video",
                "detail": "no rendered video found for clip #{}".format(index),
                "severity": "high",
            })
        else:
            try:
                from scripts.media_validation import validate_media_file
                media = validate_media_file(found, min_duration=0.5, require_audio=False)
                if not media.get("ok"):
                    reasons.append({
                        "source": "media_validation",
                        "detail": "rendered video failed validation: {}".format(
                            "; ".join(media.get("errors", [media.get("error", "invalid media")]))),
                        "severity": "high",
                    })
            except Exception as exc:
                reasons.append({
                    "source": "media_validation",
                    "detail": "could not validate rendered video: {}".format(exc),
                    "severity": "high",
                })

    # Only high-severity reasons block; music "warn" flags are advisory and
    # never stop an upload on their own (metadata/safety/blocklist unchanged).
    blocking = [r for r in reasons
                if r["severity"] == "high"
                or (r["severity"] == "medium" and r["source"] != "music_fingerprint")]

    return {
        "allowed": not blocking,
        "reasons": reasons,
        "metadata": meta,
        "content_guard": guard_verdict,
    }


def _find_clip_video(project_folder, index):
    import glob
    patterns = [
        os.path.join(project_folder, "final_polished", "*{0:03d}*.mp4".format(index)),
        os.path.join(project_folder, "final", "*{0:03d}*.mp4".format(index)),
        os.path.join(project_folder, "final", "final-output{0:03d}_processed.mp4".format(index)),
        os.path.join(project_folder, "cuts", "{0:03d}_*_original_scale.mp4".format(index)),
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


def gate_upload(project_folder, index=None, title="", caption="", hashtags=None,
                extra_rules_path=None, require_video=False, music_gate=None,
                video_path=None, platform="youtube"):
    """Enforcement wrapper: raises UploadGateError when the clip must not go out."""
    verdict = check_clip(project_folder, index, title, caption, hashtags,
                         extra_rules_path, require_video, music_gate,
                         video_path=video_path, platform=platform)
    if not verdict["allowed"]:
        raise UploadGateError(verdict["reasons"])
    return verdict


def audit_project(project_folder, extra_rules_path=None):
    """Check every scored clip in the project folder. Returns (allowed, blocked)."""
    scorecard = _load_json(project_folder, SCORECARD)
    allowed, blocked = [], []
    segments = (scorecard or {}).get("segments", [])
    for entry in segments:
        idx = entry.get("index")
        verdict = check_clip(project_folder, idx,
                             title=entry.get("title", ""),
                             require_video=False,
                             extra_rules_path=extra_rules_path)
        if verdict["allowed"]:
            allowed.append(idx)
        else:
            blocked.append({"index": idx, "title": entry.get("title", ""),
                            "reasons": verdict["reasons"]})
    return allowed, blocked


# ---------------------------------------------------------------------------
# Platform upload adapters (all MUST pass through the gate)
# ---------------------------------------------------------------------------

class _BaseUploader:
    """Thin adapter contract. Subclasses implement _do_upload()."""

    platform = "base"

    def __init__(self, project_folder, dry_run=False, extra_rules_path=None,
                 video_url=None, music_gate=None, client_secrets_path=None,
                 token_path=None, privacy_status=None, publish_at=None,
                 oauth_full_access=False):
        self.project_folder = project_folder
        self.dry_run = dry_run
        self.extra_rules_path = extra_rules_path
        self.video_url = video_url
        self.music_gate = music_gate
        self.client_secrets_path = client_secrets_path
        self.token_path = token_path
        self.privacy_status = privacy_status
        self.publish_at = publish_at
        self.oauth_full_access = bool(oauth_full_access)

    def upload(self, video_path, title, caption="", hashtags=None, index=None,
               privacy_status=None, publish_at=None):
        """Gate first, then upload. Raises UploadGateError when blocked."""
        effective_privacy = privacy_status or self.privacy_status
        effective_publish_at = publish_at or self.publish_at
        if (self.platform == "youtube" and effective_publish_at
                and str(effective_privacy or "private").lower() != "private"):
            raise ValueError("YouTube scheduled videos must use private privacy_status")
        gate_upload(self.project_folder, index, title, caption, hashtags,
                    self.extra_rules_path,
                    require_video=bool(getattr(self, "validate_video", False)),
                    music_gate=self.music_gate, video_path=video_path,
                    platform=self.platform)
        if self.dry_run:
            print("[{}] DRY-RUN would upload '{}' → {}".format(
                self.platform, title, video_path))
            return {"status": "dry-run", "platform": self.platform}
        upload_kwargs = {}
        if self.platform == "youtube":
            upload_kwargs = {
                "privacy_status": privacy_status or self.privacy_status,
                "publish_at": publish_at or self.publish_at,
            }
        try:
            result = self._do_upload(video_path, title, caption, hashtags, **upload_kwargs)
        except Exception as exc:
            try:
                content_guard.record_platform_error(
                    self.project_folder, self.platform, exc)
            except Exception:
                pass
            raise
        if isinstance(result, dict) and result.get("status") in content_guard.SUCCESS_STATUSES:
            try:
                content_guard.record_publish(
                    self.project_folder, self.platform, video_path, title=title,
                    status=result.get("status"), index=index, result=result)
            except Exception as exc:
                # The upload already succeeded; retain the result and make the
                # missing local audit record visible for the next run.
                print("[content-guard] registry write failed: {}".format(exc))
        return result

    def _do_upload(self, video_path, title, caption, hashtags, **kwargs):
        raise NotImplementedError

    # Convenience for adapters: fail loudly with a clear setup hint.
    @staticmethod
    def _missing_credentials(platform, env_vars):
        raise RuntimeError(
            "{} upload requires OAuth credentials via env vars: {}. "
            "See docs/ROADMAP_REPORT.md (2.2) for setup.".format(platform, ", ".join(env_vars)))


class YouTubeUploader(_BaseUploader):
    """YouTube Data API v3 uploader with real OAuth (Roadmap 2.2).

    Setup (once):
      1. pip install -r requirements-upload.txt
      2. Google Cloud console → enable "YouTube Data API v3" →
         create OAuth 2.0 Client ID (Desktop app) → save JSON as client_secrets.json
      3. Run any upload: the first run opens the browser for consent and
         stores the token in ~/.viralcutter/yt_token.json.

    The safety gate runs BEFORE any API call (see _BaseUploader.upload).
    Privacy: default privacyStatus is "private" (safe) — set YT_PRIVACY=public
    only when you intend to publish.
    """
    platform = "youtube"
    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

    def auth(self):
        """Run the OAuth consent flow and save the token (no upload)."""
        self._load_or_create_token()
        print("[youtube] ✅ token saved → {}".format(self._token_path()))
        return self._token_path()

    def ensure_authenticated(self):
        """Validate the existing YouTube connection without opening a browser."""
        token_path = self._token_path()
        secrets = self._client_secrets_path()
        if not os.path.isfile(secrets):
            raise RuntimeError("YouTube channel is not connected: client_secrets.json is missing")
        if not os.path.isfile(token_path):
            raise RuntimeError("YouTube channel is not connected: press 'تسجيل الدخول إلى YouTube' first")
        try:
            return self._load_existing_token()
        except Exception as exc:
            detail = str(exc)
            if "OAuth token lacks required YouTube permissions" in detail:
                from webui.youtube_credentials import invalidate_token
                invalidate_token(token_path)
                raise RuntimeError(
                    "نطاقات OAuth غير كافية للتحقق من القناة. تم حذف التوكن القديم؛ "
                    "اضغط تسجيل الدخول إلى YouTube ووافق على صلاحية رفع الفيديو وقراءة القناة."
                ) from exc
            raise RuntimeError("YouTube connection is invalid or expired: {}".format(detail[:400])) from exc

    def verify_channel(self):
        """Verify that the token can access a YouTube channel and return identity."""
        creds = self.ensure_authenticated()
        try:
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError("YouTube channel verification needs google-api-python-client") from None
        try:
            service = build("youtube", "v3", credentials=creds, cache_discovery=False)
            response = service.channels().list(part="id,snippet", mine=True).execute()
        except Exception as exc:
            detail = str(exc)
            lower = detail.lower()
            insufficient = (
                "insufficientpermissions" in lower
                or "insufficient permission" in lower
                or "insufficient authentication scopes" in lower
                or "insufficient authentication" in lower
                or "403" in lower and "permission" in lower
            )
            if insufficient:
                from webui.youtube_credentials import invalidate_token
                invalidate_token(self._token_path())
                raise RuntimeError(
                    "نطاقات OAuth غير كافية للتحقق من القناة. تم حذف التوكن القديم؛ "
                    "اضغط تسجيل الدخول إلى YouTube ووافق على صلاحية رفع الفيديو وقراءة القناة."
                ) from exc
            raise RuntimeError("تعذر التحقق من قناة YouTube عبر API: {}".format(detail[:400])) from exc
        items = response.get("items") or []
        if not items:
            raise RuntimeError("حساب OAuth صالح، لكن لا توجد قناة YouTube متاحة لهذا الحساب")
        item = items[0] or {}
        snippet = item.get("snippet") or {}
        return {
            "id": str(item.get("id") or ""),
            "title": str(snippet.get("title") or "قناة YouTube"),
        }

    def _token_path(self):
        return self.token_path or os.getenv("YT_TOKEN_FILE") or os.path.join(
            os.path.expanduser("~"), ".viralcutter", "youtube", "token.json")

    def _client_secrets_path(self):
        return self.client_secrets_path or os.getenv("YT_CLIENT_SECRETS_FILE") or os.path.join(
            os.path.expanduser("~"), ".viralcutter", "youtube", "client_secrets.json")

    @staticmethod
    def _normalize_publish_at(value):
        if not value:
            return None
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = _datetime.datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("publish_at must be ISO 8601, e.g. 2026-08-15T18:30:00+00:00") from exc
        if parsed.tzinfo is None:
            raise ValueError("publish_at must include a timezone, e.g. +00:00 or Z")
        if parsed <= _datetime.datetime.now(_datetime.timezone.utc):
            raise ValueError("publish_at must be in the future")
        return parsed.astimezone(_datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    def _load_existing_token(self):
        """Load and refresh a stored token only; never start interactive OAuth."""
        import google.auth.transport.requests as g_requests
        from google.oauth2.credentials import Credentials

        from webui.youtube_credentials import (
            require_scopes,
            scopes_for_access,
            store_token_json,
        )
        token_path = self._token_path()
        scopes = scopes_for_access(self.oauth_full_access)
        creds = Credentials.from_authorized_user_file(token_path, scopes)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(g_requests.Request())
            try:
                store_token_json(creds.to_json(), token_path)
            except Exception:
                pass
        if not creds or not creds.valid:
            raise RuntimeError("stored OAuth token is not valid")
        return require_scopes(creds, self.oauth_full_access)

    def _load_or_create_token(self):
        """Return credentials; run the OAuth consent flow on first use."""
        # Check credentials BEFORE importing the optional google libraries so a
        # missing client_secrets.json always yields the clear, actionable error
        # (and never a raw ModuleNotFoundError in minimal environments).
        token_path = self._token_path()
        secrets = self._client_secrets_path()
        if not os.path.exists(secrets) and not os.path.exists(token_path):
            self._missing_credentials(self.platform, ["YT_CLIENT_SECRETS_FILE"])

        import google.auth.transport.requests as g_requests
        from google.oauth2.credentials import Credentials

        from webui.youtube_credentials import require_scopes, scopes_for_access

        scopes = scopes_for_access(self.oauth_full_access)
        if os.path.exists(token_path):
            try:
                os.chmod(token_path, 0o600)
            except OSError:
                pass
            creds = Credentials.from_authorized_user_file(token_path, scopes)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(g_requests.Request())
            if creds and creds.valid:
                try:
                    return require_scopes(creds, self.oauth_full_access)
                except Exception:
                    from webui.youtube_credentials import invalidate_token
                    invalidate_token(token_path)
        # The consent flow needs client_secrets.json.
        if not os.path.exists(secrets):
            self._missing_credentials(self.platform, ["YT_CLIENT_SECRETS_FILE"])
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(secrets, scopes)
        creds = flow.run_local_server(port=0, prompt="consent")
        try:
            from webui.youtube_credentials import store_token_json
            store_token_json(creds.to_json(), token_path)
        except Exception:
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            try:
                os.chmod(token_path, 0o600)
            except OSError:
                pass
        return creds

    def _do_upload(self, video_path, title, caption, hashtags, privacy_status=None,
                   publish_at=None):
        if not os.path.exists(video_path):
            raise FileNotFoundError("video not found: {}".format(video_path))
        creds = self._load_or_create_token()
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            raise RuntimeError(
                "youtube upload needs: pip install -r requirements-upload.txt") from None

        tags = [str(h).lstrip("#") for h in (hashtags or []) if str(h).strip()]
        description = caption or ""
        if tags:
            description += "\n\n" + " ".join("#" + t for t in tags)
        privacy = (privacy_status or os.getenv("YT_PRIVACY", "private")).strip().lower()
        if privacy not in {"private", "public", "unlisted"}:
            raise ValueError("privacy_status must be private, public, or unlisted")
        normalized_publish_at = self._normalize_publish_at(publish_at)
        if normalized_publish_at and privacy != "private":
            raise ValueError("YouTube scheduled videos must use private privacy_status")
        status = {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        }
        if normalized_publish_at:
            status["publishAt"] = normalized_publish_at
        body = {
            "snippet": {
                "title": (title or "").strip()[:100],
                "description": description[:5000],
                "tags": tags,
                "categoryId": os.getenv("YT_CATEGORY_ID", "22"),  # 22 = People & Blogs
            },
            "status": status,
        }
        service = build("youtube", "v3", credentials=creds)
        media = MediaFileUpload(video_path, chunksize=8 * 1024 * 1024, resumable=True)
        request = service.videos().insert(part="snippet,status", body=body,
                                          media_body=media)
        response = None
        transient_attempts = 0
        while response is None:
            try:
                status, response = request.next_chunk()
                transient_attempts = 0
            except Exception as exc:
                status_code = getattr(getattr(exc, "resp", None), "status", None)
                if status_code in {429, 500, 502, 503, 504} and transient_attempts < 3:
                    delay = min(30, 2 ** transient_attempts)
                    transient_attempts += 1
                    print("[youtube] temporary API error {} — retry {} in {}s".format(
                        status_code, transient_attempts, delay), flush=True)
                    time.sleep(delay)
                    continue
                if status_code in {401, 403}:
                    raise RuntimeError(
                        "YouTube upload permission denied (HTTP {}). Reconnect the channel and "
                        "confirm YouTube Data API v3 and upload scope.".format(status_code)
                    ) from exc
                raise
            if status:
                print("[youtube] uploaded {:.0f}%".format(
                    status.progress() * 100 if status.progress() else 0), flush=True)
        video_id = response.get("id")
        print("[youtube] uploaded '{}' → https://youtu.be/{}".format(title, video_id))
        return {"status": "scheduled" if normalized_publish_at else "uploaded", "platform": "youtube",
                "video_id": video_id, "url": "https://youtu.be/{}".format(video_id),
                "privacy_status": privacy, "publish_at": normalized_publish_at}


# ---------------------------------------------------------------------------
# Shared HTTP helpers (stdlib only — no extra pip deps for the upload stack)
# ---------------------------------------------------------------------------

def _http_json(url, data=None, headers=None, method=None, timeout=60, retries=3,
               form=False):
    """JSON/form/raw-bytes HTTP request via urllib with a 429/5xx retry loop.

    - data dict + form=False  → JSON body (Content-Type: application/json)
    - data dict + form=True   → urlencoded body (application/x-www-form-urlencoded,
                                what the Instagram Graph API expects)
    - data bytes              → raw body sent as-is (TikTok video PUT)
    Returns the parsed JSON body. Raises RuntimeError with a readable message
    on transport errors and on API error payloads ({"error": {...}}).
    """
    body = None
    if data is not None:
        if isinstance(data, (bytes, bytearray)):
            body = bytes(data)
        elif form:
            body = urllib.parse.urlencode(data).encode("utf-8")
        else:
            body = json.dumps(data).encode("utf-8")
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if body is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if form else "application/json")

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(raw) if raw else {}
                except ValueError:
                    return {"raw": raw}
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            last_err = _api_error_message(e.code, raw)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * attempt)
                continue
            raise RuntimeError(last_err) from None
        except Exception as e:  # network errors, timeouts
            last_err = str(e)
            if attempt < retries:
                time.sleep(2 * attempt)
                continue
            raise RuntimeError("network error talking to {}: {}".format(url, e)) from None
    raise RuntimeError("request failed: {}".format(last_err))


def _api_error_message(status, raw):
    """Best-effort extraction of a human-readable API error message."""
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        payload = {}
    err = payload.get("error") or {}
    if isinstance(err, dict):
        code = err.get("code") or err.get("status")
        msg = err.get("message") or err.get("description") or err.get("detail")
        if msg:
            return "API error {}: {}".format(code or status, msg)
    msg = payload.get("message") or payload.get("error_description") or payload.get("reason")
    if msg:
        return "API error {}: {}".format(status, msg)
    return "API error {}: {}".format(status, raw[:200])


TIKTOK_APPROVAL_HINT = (
    "TikTok only lets an app upload AFTER its Content Posting API permission "
    "(scope video.publish) is APPROVED — app review typically takes days to "
    "weeks. Check https://developers.tiktok.com → your app → Permissions."
)


def _with_tiktok_hint(msg):
    """Append the app-approval hint when a TikTok error smells like permissions."""
    lower = msg.lower()
    if any(k in lower for k in (
            "permission", "approve", "approval", "scope", "unauthorized",
            "forbidden", "not allowed", "no permission", "40100", "43201",
            "access_token")):
        return msg + "\n" + TIKTOK_APPROVAL_HINT
    return msg


def _token_file(platform):
    env_map = {
        "tiktok": "TIKTOK_TOKEN_FILE",
        "instagram": "IG_TOKEN_FILE",
        "youtube": "YT_TOKEN_FILE",
    }
    return os.getenv(env_map[platform]) or os.path.join(
        os.path.expanduser("~"), ".viralcutter", "{}_token.json".format(platform))


def _save_token(platform, payload):
    path = _token_file(platform)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def _load_token(platform):
    path = _token_file(platform)
    if not os.path.exists(path):
        return None, path
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), path
    except Exception:
        return None, path


# ---------------------------------------------------------------------------
# Anonymous video hosting (bridges the Instagram Graph API "public URL" gap)
# ---------------------------------------------------------------------------

def _multipart_body(fields, file_path, file_field, boundary):
    """Build a multipart/form-data body (stdlib only)."""
    lines = []
    for k, v in (fields or {}).items():
        lines.append("--" + boundary)
        lines.append('Content-Disposition: form-data; name="{}"'.format(k))
        lines.append("")
        lines.append(str(v))
    lines.append("--" + boundary)
    lines.append('Content-Disposition: form-data; name="{}"; filename="{}"'.format(
        file_field, os.path.basename(file_path)))
    lines.append("Content-Type: video/mp4")
    lines.append("")
    head = "\r\n".join(lines).encode("utf-8") + b"\r\n"
    with open(file_path, "rb") as f:
        file_data = f.read()
    tail = "\r\n--{}--\r\n".format(boundary).encode("utf-8")
    return head + file_data + tail


def host_media_file(video_path, timeout=300):
    """Upload a local video to a free anonymous host; return the public https URL.

    Why: Instagram's Graph API has NO raw-file upload for Reels — it needs a
    public HTTPS video_url. This closes that gap for desktop users: the clip
    goes to catbox.moe (200 MB limit), falling back to 0x0.st, and the
    returned URL is fed to the Graph API automatically.

    Caveats (documented honestly):
      * Hosted copies are anonymous and temporary — they may be removed later,
        but the Instagram post keeps the video regardless.
      * For sensitive content, host on your own server instead and pass
        --video-url / IG_VIDEO_URL (or IG_HOST_DISABLE=1 to force that path).

    Returns the https URL. Raises RuntimeError with a readable message.
    """
    import uuid
    if not os.path.exists(video_path):
        raise FileNotFoundError("video not found: {}".format(video_path))
    size = os.path.getsize(video_path)
    if size <= 0:
        raise ValueError("video file is empty: {}".format(video_path))
    if size > 200 * 1024 * 1024:
        raise RuntimeError(
            "auto-host supports files up to 200 MB (got {:.1f} MB). Host this "
            "clip yourself and pass --video-url or IG_VIDEO_URL.".format(
                size / (1024 * 1024)))

    hosts = [
        {"name": "catbox.moe", "url": "https://catbox.moe/user/api.php",
         "fields": {"reqtype": "fileupload", "userhash": ""}},
        {"name": "0x0.st", "url": "https://0x0.st",
         "fields": {"secret": ""}},
    ]
    last_err = None
    for host in hosts:
        boundary = "----ViralCutter{}".format(uuid.uuid4().hex)
        body = _multipart_body(host["fields"], video_path, "fileToUpload"
                               if host["name"] == "catbox.moe" else "file",
                               boundary)
        req = urllib.request.Request(
            host["url"], data=body, method="POST",
            headers={"Content-Type": "multipart/form-data; boundary={}".format(boundary),
                     "User-Agent": "ViralCutter/6.11 (desktop upload helper)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                url = resp.read().decode("utf-8", errors="replace").strip()
            if url and url.startswith("https://") and not any(
                    token in url.lower() for token in ("error", "<html")):
                return url
            last_err = "host {} replied: {}".format(host["name"], url[:120] or "(empty)")
        except Exception as e:
            last_err = "host {} failed: {}".format(host["name"], e)
            continue
    raise RuntimeError(
        "auto-hosting failed on all providers (catbox.moe, 0x0.st). "
        "Last error: {}. Host the clip yourself and pass --video-url or "
        "IG_VIDEO_URL.".format(last_err))


class _OAuthCallbackServer:
    """Tiny local HTTP server that captures one OAuth redirect (?code=...).

    Used by the TikTok authorization-code flow: we open the browser, the
    platform redirects to http://localhost:<port>/?code=...&state=..., and
    this server hands the code back to the caller.
    """

    def __init__(self, port, expected_state, timeout=180):
        self.port = port
        self.expected_state = expected_state
        self.timeout = timeout
        self.code = None
        self.error = None
        self.state_ok = False

    def _handler(self):
        expected_state = self.expected_state
        holder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                if query.get("error"):
                    holder.error = query.get("error_description", query.get("error"))[0]
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"ViralCutter: OAuth error received. You may close this tab.")
                    return
                code = (query.get("code") or [None])[0]
                state = (query.get("state") or [None])[0]
                if not code:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"ViralCutter: no code in redirect. You may close this tab.")
                    return
                holder.code = code
                holder.state_ok = state == expected_state
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    b"ViralCutter: authorization received. You may close this tab and return to the app.")

            def do_HEAD(self):
                self.send_response(200)
                self.end_headers()

        return Handler

    def run(self):
        server = HTTPServer(("127.0.0.1", self.port), self._handler())
        deadline = time.time() + self.timeout
        while self.code is None and self.error is None and time.time() < deadline:
            server.handle_request()
        server.server_close()
        if self.error:
            raise RuntimeError("OAuth denied: {}".format(self.error))
        if self.code is None:
            raise RuntimeError(
                "OAuth timed out after {}s — no redirect received on http://localhost:{}/".format(
                    self.timeout, self.port))
        if not self.state_ok:
            raise RuntimeError("OAuth state mismatch (CSRF guard) — please retry.")
        return self.code


class TikTokUploader(_BaseUploader):
    """TikTok Content Posting API adapter with real OAuth2 (Roadmap 2.2).

    Setup (once) — requires a TikTok Developer app:
      1. https://developers.tiktok.com → create an app → enable the
         "Content Posting API" permission (scope `video.publish`).
      2. Add the redirect URI (default http://localhost:8431/) to the app.
      3. Set env vars TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET, then run
         `python -m scripts.upload_gate --auth tiktok` — a browser opens,
         you approve, and the token is stored in ~/.viralcutter/tiktok_token.json.

    The safety gate runs BEFORE any API call (see _BaseUploader.upload).
    Privacy: default privacyLevel is SELF_ONLY (draft, safe) — set
    TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE only when you intend to publish.
    """
    platform = "tiktok"
    AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
    TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
    VIDEO_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    VIDEO_UPLOAD_URL = "https://open.tiktokapis.com/v2/post/publish/video/upload/"
    STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
    SCOPES = "user.info.basic,video.publish"
    CALLBACK_PORT = 8431

    def __init__(self, project_folder, dry_run=False, extra_rules_path=None,
                 video_url=None):
        super().__init__(project_folder, dry_run=dry_run, extra_rules_path=extra_rules_path)
        self.video_url = video_url

    # -- OAuth -----------------------------------------------------------------

    def _redirect_uri(self):
        return os.getenv("TIKTOK_REDIRECT_URI", "http://localhost:{}/".format(self.CALLBACK_PORT))

    def auth(self):
        """Run the full OAuth consent flow (browser + local callback). No upload."""
        client_key = os.getenv("TIKTOK_CLIENT_KEY", "")
        client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "")
        if not client_key or not client_secret:
            self._missing_credentials(
                self.platform, ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"])
        redirect_uri = self._redirect_uri()
        import secrets
        state = secrets.token_urlsafe(16)
        params = {
            "client_key": client_key,
            "scope": self.SCOPES,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
        }
        auth_url = "{}?{}".format(self.AUTH_URL, urllib.parse.urlencode(params))
        print("[tiktok] opening browser for consent…")
        print("[tiktok] if the browser does not open, visit:\n  {}".format(auth_url))
        try:
            import webbrowser
            webbrowser.open(auth_url)
        except Exception:
            pass
        code = _OAuthCallbackServer(self.CALLBACK_PORT, state).run()
        token = self._exchange_code(code, redirect_uri, client_key, client_secret)
        path = _save_token("tiktok", token)
        print("[tiktok] ✅ token saved → {}".format(path))
        return path

    def _exchange_code(self, code, redirect_uri, client_key, client_secret):
        form = urllib.parse.urlencode({
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }).encode("utf-8")
        req = urllib.request.Request(self.TOKEN_URL, data=form, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(_api_error_message(e.code, raw)) from None
        if payload.get("error"):
            raise RuntimeError("TikTok OAuth failed: {}".format(
                payload.get("error_description") or payload.get("error")))
        payload["expires_at"] = time.time() + int(payload.get("expires_in", 0) or 0)
        return payload

    def _refresh_token(self, token):
        client_key = os.getenv("TIKTOK_CLIENT_KEY", "")
        client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "")
        if not client_key or not client_secret or not token.get("refresh_token"):
            self._missing_credentials(
                self.platform, ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"])
        form = urllib.parse.urlencode({
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        }).encode("utf-8")
        req = urllib.request.Request(self.TOKEN_URL, data=form, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if payload.get("error"):
            raise RuntimeError("TikTok token refresh failed: {}".format(payload.get("error")))
        new_token = dict(token)
        for key in ("access_token", "refresh_token", "expires_in", "scope", "open_id"):
            if payload.get(key) is not None:
                new_token[key] = payload[key]
        if payload.get("expires_in"):
            new_token["expires_at"] = time.time() + int(payload["expires_in"])
        _save_token("tiktok", new_token)
        return new_token

    def _ensure_token(self):
        """Return a valid access token; run OAuth on first use; refresh if expired."""
        token, path = _load_token("tiktok")
        if not token:
            self._missing_credentials(
                self.platform, ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"])
        expires_at = token.get("expires_at") or 0
        if expires_at and time.time() > expires_at - 60:
            if not token.get("refresh_token"):
                self._missing_credentials(
                    self.platform, ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"])
            token = self._refresh_token(token)
        return token["access_token"]

    # -- Upload ----------------------------------------------------------------

    def _do_upload(self, video_path, title, caption, hashtags):
        # Credentials first (keeps the "clear setup hint" contract), then file.
        access_token = self._ensure_token()
        if not os.path.exists(video_path):
            raise FileNotFoundError("video not found: {}".format(video_path))
        size = os.path.getsize(video_path)
        if size <= 0:
            raise ValueError("video file is empty: {}".format(video_path))

        headers = {"Authorization": "Bearer {}".format(access_token)}
        display_title = (title or caption or "ViralCutter clip")[:150]
        init_payload = {
            "post_info": {
                "title": display_title,
                "privacy_level": os.getenv("TIKTOK_PRIVACY", "SELF_ONLY"),
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            },
        }
        try:
            init = _http_json(self.VIDEO_INIT_URL, init_payload, headers=headers)
        except RuntimeError as e:
            raise RuntimeError(_with_tiktok_hint(str(e))) from None
        publish_id = ((init.get("data") or {}).get("publish_id"))
        if not publish_id:
            raise RuntimeError(_with_tiktok_hint(
                "TikTok init returned no publish_id: {}".format(init)))

        # Upload the file bytes (single chunk) — the actual PUT body. The
        # endpoint returns an empty 2xx body, so we ignore the JSON result.
        with open(video_path, "rb") as f:
            blob = f.read()
        upload_url = "{}?video_size={}".format(
            self.VIDEO_UPLOAD_URL + str(publish_id) + "/", size)
        _http_json(upload_url, data=blob, headers={
            "Authorization": "Bearer {}".format(access_token),
            "Content-Type": "video/mp4",
        }, method="PUT", timeout=600)

        # Poll publish status (PUBLISH_COMPLETE / FAILED / PROCESSING...).
        for _ in range(30):
            status = _http_json(self.STATUS_URL, {"publish_id": publish_id},
                                headers=headers)
            state = ((status.get("data") or {}).get("status") or "").upper()
            if state == "PUBLISH_COMPLETE":
                print("[tiktok] uploaded '{}' (publish_id {})".format(
                    display_title, publish_id))
                return {"status": "uploaded", "platform": "tiktok",
                        "publish_id": publish_id, "url": "https://www.tiktok.com/"}
            if state == "FAILED":
                fail = ((status.get("data") or {}).get("fail_reason")
                        or status.get("error") or "unknown")
                raise RuntimeError(_with_tiktok_hint(
                    "TikTok publish failed: {}".format(fail)))
            time.sleep(5)
        raise RuntimeError(
            "TikTok publish is still processing (publish_id {}) — check later "
            "on tiktok.com".format(publish_id))


class InstagramUploader(_BaseUploader):
    """Instagram Graph API Reels adapter with real tokens (Roadmap 2.2).

    How it works (Instagram Graph API, documented constraints):
      * Two-step publish: POST /{ig-user-id}/media (media_type=REELS) →
        creation_id, then POST /{ig-user-id}/media_publish.
      * The API requires a **public HTTPS video_url** for the clip (there is
        no raw-file upload endpoint for IG Reels). ViralCutter closes that
        gap automatically: with no --video-url / IG_VIDEO_URL, the local clip
        is uploaded to a free anonymous host (catbox.moe, 0x0.st fallback)
        and the resulting URL is used. Set IG_HOST_DISABLE=1 to force the
        manual path (host on your own server, pass --video-url).
      * The account must be a Business/Creator account linked to a Facebook
        Page, and the Facebook app needs the `instagram_content_publish` and
        `pages_show_list` permissions.

    Token setup (once):
      1. Create a Facebook app → add Instagram Graph API.
      2. Get a short-lived user token, then exchange it for a long-lived one:
         `python -m scripts.upload_gate --auth instagram` (or set
         IG_ACCESS_TOKEN + IG_USER_ID env vars directly).
      3. Token is stored in ~/.viralcutter/ig_token.json.

    The safety gate runs BEFORE any API call (see _BaseUploader.upload).
    """
    platform = "instagram"
    GRAPH = "https://graph.facebook.com/v21.0"
    TOKEN_EXCHANGE = "https://graph.facebook.com/v21.0/oauth/access_token"

    def __init__(self, project_folder, dry_run=False, extra_rules_path=None,
                 video_url=None):
        super().__init__(project_folder, dry_run=dry_run, extra_rules_path=extra_rules_path)
        self.video_url = video_url

    def auth(self):
        """Print how to get a long-lived IG token (no browser flow needed)."""
        token = os.getenv("IG_ACCESS_TOKEN")
        if token:
            # Exchange a short-lived user token for a long-lived one.
            client_id = os.getenv("IG_CLIENT_ID")
            client_secret = os.getenv("IG_CLIENT_SECRET")
            if client_id and client_secret:
                url = "{}?grant_type=fb_exchange_token&client_id={}&client_secret={}&fb_exchange_token={}".format(
                    self.TOKEN_EXCHANGE, urllib.parse.quote(client_id),
                    urllib.parse.quote(client_secret), urllib.parse.quote(token))
                try:
                    payload = _http_json(url)
                except RuntimeError as e:
                    raise RuntimeError(
                        "IG long-lived exchange failed ({}). Set IG_ACCESS_TOKEN "
                        "directly to the long-lived token instead.".format(e)) from None
                token = payload.get("access_token", token)
            path = _save_token("instagram", {
                "access_token": token, "expires_at": 0})
            print("[instagram] ✅ token saved → {}".format(path))
            return path
        raise RuntimeError(
            "Instagram token setup: 1) generate a long-lived IG user access "
            "token (Graph API explorer) and set IG_ACCESS_TOKEN + IG_USER_ID, "
            "or 2) set IG_CLIENT_ID + IG_CLIENT_SECRET and run "
            "`--auth instagram` with a short-lived token to exchange it.")

    def _ensure_token(self):
        token, _path = _load_token("instagram")
        if not token:
            env_token = os.getenv("IG_ACCESS_TOKEN")
            if not env_token:
                self._missing_credentials(
                    self.platform, ["IG_ACCESS_TOKEN", "IG_USER_ID"])
            token = {"access_token": env_token, "expires_at": 0}
        return token["access_token"]

    def _do_upload(self, video_path, title, caption, hashtags):
        access_token = self._ensure_token()
        ig_user_id = os.getenv("IG_USER_ID") or ""
        if not ig_user_id:
            self._missing_credentials(self.platform, ["IG_USER_ID"])

        video_url = self.video_url or os.getenv("IG_VIDEO_URL", "")
        if not video_url:
            if os.getenv("IG_HOST_DISABLE", "").lower() in ("1", "true", "yes"):
                raise RuntimeError(
                    "Instagram Graph API requires a PUBLIC https video_url for "
                    "the clip (no raw-file upload exists for IG Reels). Host "
                    "the clip and pass --video-url or set IG_VIDEO_URL "
                    "(IG_HOST_DISABLE=1 disabled auto-hosting).")
            if not os.path.exists(video_path):
                raise FileNotFoundError("video not found: {}".format(video_path))
            print("[instagram] no IG_VIDEO_URL — auto-hosting the clip on a "
                  "free anonymous host (catbox.moe, 0x0.st fallback)…")
            video_url = host_media_file(video_path)
            print("[instagram] hosted → {}".format(video_url))
            print("[instagram] note: the hosted copy is temporary; the post "
                  "keeps the video. Set IG_VIDEO_URL or IG_HOST_DISABLE=1 to "
                  "control hosting yourself.")

        caption_text = (title or "").strip()
        if caption:
            caption_text = (caption_text + "\n\n" + caption).strip()
        if hashtags:
            tags = " ".join("#" + str(h).lstrip("#") for h in hashtags if str(h).strip())
            caption_text = (caption_text + "\n\n" + tags).strip()
        caption_text = caption_text[:2200]  # IG caption limit

        base = self.GRAPH
        params = {
            "access_token": access_token,
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption_text,
            "share_to_feed": os.getenv("IG_SHARE_TO_FEED", "true").lower() in ("1", "true", "yes"),
        }
        # Graph API media/media_publish endpoints take FORM-encoded params.
        created = _http_json("{}/{}/media".format(base, ig_user_id), params,
                             form=True)
        creation_id = created.get("id")
        if not creation_id:
            raise RuntimeError("Instagram media creation failed: {}".format(created))

        published = _http_json("{}/{}/media_publish".format(base, ig_user_id),
                               {"creation_id": creation_id,
                                "access_token": access_token}, form=True)
        media_id = published.get("id")
        print("[instagram] uploaded '{}' (media_id {})".format(title, media_id))
        return {"status": "uploaded", "platform": "instagram",
                "media_id": media_id, "url": "https://www.instagram.com/"}


UPLOADERS = {
    "youtube": YouTubeUploader,
    "tiktok": TikTokUploader,
    "instagram": InstagramUploader,
}


def check_platform_setup(platform):
    """Diagnose what's missing for a platform BEFORE any upload. No network.

    Returns a list of {"ok": bool, "item": str, "detail": str}. Helps users
    (and the support flow) see at a glance which barrier is theirs: missing
    env vars, missing token, or an external approval we cannot verify.
    """
    checks = []
    env = os.environ

    if platform == "youtube":
        secrets = env.get("YT_CLIENT_SECRETS_FILE") or os.path.join(
            os.getcwd(), "client_secrets.json")
        checks.append({
            "ok": os.path.exists(secrets),
            "item": "OAuth client secrets (client_secrets.json)",
            "detail": secrets + (" — found" if os.path.exists(secrets)
                                 else " — MISSING. Get one from Google Cloud "
                                 "Console → APIs & Services → Credentials → "
                                 "OAuth 2.0 Client ID (Desktop app).")})
        token = _token_file("youtube")
        checks.append({
            "ok": os.path.exists(token),
            "item": "Saved YouTube token",
            "detail": token + (" — found" if os.path.exists(token)
                               else " — missing. Run --auth youtube once.")})
    elif platform == "tiktok":
        ck, cs = env.get("TIKTOK_CLIENT_KEY"), env.get("TIKTOK_CLIENT_SECRET")
        checks.append({
            "ok": bool(ck and cs),
            "item": "TikTok app credentials (TIKTOK_CLIENT_KEY/SECRET)",
            "detail": "set" if ck and cs else
                      "MISSING — create an app at developers.tiktok.com and "
                      "set both env vars."})
        token = _token_file("tiktok")
        checks.append({
            "ok": os.path.exists(token),
            "item": "Saved TikTok token",
            "detail": token + (" — found" if os.path.exists(token)
                               else " — missing. Run --auth tiktok once "
                               "(opens the browser consent).")})
        checks.append({
            "ok": None,
            "item": "Content Posting API approval",
            "detail": "CANNOT be verified from here — TikTok approves apps "
                      "manually (days/weeks). Even with perfect code, uploads "
                      "only work after approval. Check developers.tiktok.com "
                      "→ your app → Permissions → video.publish."})
    elif platform == "instagram":
        token, path = _load_token("instagram")
        env_tok = env.get("IG_ACCESS_TOKEN")
        checks.append({
            "ok": bool(token or env_tok),
            "item": "Instagram access token",
            "detail": path + (" — found" if token else
                              " — missing. Set IG_ACCESS_TOKEN (long-lived "
                              "user token) or run --auth instagram.")})
        uid = env.get("IG_USER_ID")
        checks.append({
            "ok": bool(uid),
            "item": "IG_USER_ID",
            "detail": "set" if uid else "MISSING — set IG_USER_ID (your IG "
                      "Business/Creator account id)."})
        url = env.get("IG_VIDEO_URL")
        checks.append({
            "ok": None,
            "item": "Video URL source",
            "detail": ("IG_VIDEO_URL is set — Graph API will use it." if url
                       else "not set — ViralCutter will AUTO-HOST the clip on "
                       "a free anonymous host (catbox.moe / 0x0.st) at upload "
                       "time. Set IG_VIDEO_URL or IG_HOST_DISABLE=1 to host "
                       "yourself.")})
    else:
        checks.append({"ok": False, "item": "unknown platform",
                       "detail": platform})
    return checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="ViralCutter upload gate — refuses publishing blocked clips.")
    parser.add_argument("--project", default=None, help="Project folder (not needed for --check)")
    parser.add_argument("--index", type=int, default=None,
                        help="Clip index to check (default: audit all scored clips)")
    parser.add_argument("--title", default="", help="Title (checked live)")
    parser.add_argument("--caption", default="", help="Caption (checked live)")
    parser.add_argument("--hashtags", default="", help="Comma-separated hashtags")
    parser.add_argument("--extra-rules", default=None, help="Extra metadata rules JSON")
    parser.add_argument("--require-video", action="store_true",
                        help="Also require a rendered video file for the clip")
    parser.add_argument("--upload", choices=list(UPLOADERS), default=None,
                        help="Platform to upload to (dry-run by default)")
    parser.add_argument("--video", default=None, help="Video file to upload (with --upload)")
    parser.add_argument("--video-url", default=None,
                        help="Public HTTPS video URL (required by Instagram Graph API for Reels)")
    parser.add_argument("--client-secrets", default=None,
                        help="YouTube OAuth client secrets JSON (copied to secure local storage)")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"], default=None,
                        help="YouTube privacy status (scheduled uploads must be private)")
    parser.add_argument("--publish-at", default=None,
                        help="Schedule YouTube publication as ISO 8601 with timezone")
    parser.add_argument("--full-youtube-access", action="store_true",
                        help="request the broader YouTube OAuth scope")
    parser.add_argument("--no-dry-run", action="store_true",
                        help="Actually call the platform SDK (needs credentials)")
    parser.add_argument("--auth", choices=list(UPLOADERS), default=None,
                        help="Run the OAuth consent flow for a platform and save the token (no upload)")
    parser.add_argument("--check", choices=list(UPLOADERS), default=None,
                        help="Diagnose a platform's setup (env vars, tokens, known external barriers) — no upload, no network")
    parser.add_argument("--music-gate", choices=["warn", "block", "off"], default=None,
                        help="How to treat music_fingerprint.json matches (default: warn)")
    args = parser.parse_args()

    if args.check:
        print("setup check for {}:".format(args.check))
        ok = True
        for c in check_platform_setup(args.check):
            mark = "✅" if c["ok"] is True else ("ℹ️" if c["ok"] is None else "❌")
            print("  {} {}".format(mark, c["item"]))
            print("      {}".format(c["detail"]))
            if c["ok"] is False:
                ok = False
        return 0 if ok else 1

    if args.auth:
        if not args.project:
            parser.error("--project is required with --auth")
        client_secrets_path = args.client_secrets
        if client_secrets_path and args.auth == "youtube":
            from webui.youtube_credentials import store_client_secrets
            client_secrets_path = store_client_secrets(client_secrets_path)["path"]
        uploader = UPLOADERS[args.auth](args.project, dry_run=True,
                                        extra_rules_path=args.extra_rules,
                                        video_url=args.video_url,
                                        music_gate=args.music_gate,
                                        client_secrets_path=client_secrets_path,
                                        privacy_status=args.privacy,
                                        publish_at=args.publish_at,
                                        oauth_full_access=args.full_youtube_access)
        try:
            uploader.auth()
        except UploadGateError as e:
            print(str(e))
            return 3
        return 0

    if args.upload:
        if not args.project:
            parser.error("--project is required with --upload")
        client_secrets_path = args.client_secrets
        if client_secrets_path and args.upload == "youtube":
            from webui.youtube_credentials import store_client_secrets
            client_secrets_path = store_client_secrets(client_secrets_path)["path"]
        uploader = UPLOADERS[args.upload](args.project, dry_run=not args.no_dry_run,
                                          extra_rules_path=args.extra_rules,
                                          video_url=args.video_url,
                                          music_gate=args.music_gate,
                                          client_secrets_path=client_secrets_path,
                                          privacy_status=args.privacy,
                                          publish_at=args.publish_at,
                                          oauth_full_access=args.full_youtube_access)
        try:
            uploader.upload(args.video or _find_clip_video(args.project, args.index) or "",
                            args.title, args.caption,
                            [h for h in args.hashtags.split(",") if h.strip()],
                            index=args.index, privacy_status=args.privacy,
                            publish_at=args.publish_at)
        except UploadGateError as e:
            print(str(e))
            return 3
        return 0

    if not args.project:
        parser.error("--project is required for gate checks/audits (or use --check)")

    if args.index is not None:
        verdict = check_clip(args.project, args.index, args.title, args.caption,
                             [h for h in args.hashtags.split(",") if h.strip()],
                             args.extra_rules, args.require_video)
        if verdict["allowed"]:
            print("clip #{}: ALLOWED".format(args.index))
            return 0
        print("clip #{}: BLOCKED".format(args.index))
        for r in verdict["reasons"]:
            print("  - [{}] {}: {}".format(r["severity"], r["source"], r["detail"]))
        return 3

    allowed, blocked = audit_project(args.project, args.extra_rules)
    print("audit: {} allowed, {} blocked".format(len(allowed), len(blocked)))
    for b in blocked:
        print("  BLOCKED #{} '{}' — {}".format(
            b["index"], b["title"], b["reasons"][0]["detail"] if b["reasons"] else ""))
    return 3 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
