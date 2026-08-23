"""Publish panel — per-clip play / translate / music-check / upload.

Pure logic extracted from the WebUI so it stays unit-testable:
- list rendered clips of a project (final/ first, then cuts/)
- find the subtitle JSON for a clip
- translate one clip's subtitles (reuses scripts/translate_json)
- run the music fingerprint check (scripts/music_fingerprint)
- upload one clip through the safety gate (scripts/upload_gate)

No gradio imports in this module.
"""
import asyncio
import datetime
import json
import math
import os
import queue
import sys
import threading

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Clip discovery
# ---------------------------------------------------------------------------

CLIP_SOURCES = ("auto", "final_polished", "final", "cuts", "specific_file")


def _mp4_files(folder):
    if not folder or not os.path.isdir(folder):
        return []
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith(".mp4") and os.path.isfile(os.path.join(folder, name))
    )


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _usable_polished_clips(project_path, clips):
    """Return polished clips only when the persisted report validates them."""
    report = _load_json(os.path.join(project_path, "polish_report.json"))
    if not report:
        # Legacy projects have no report; keep the old read-only behavior.
        return clips
    entries = report.get("clips") or []
    by_output = {os.path.abspath(str(item.get("output"))): item for item in entries
                  if isinstance(item, dict) and item.get("output")}
    by_name = {str(item.get("video")): item for item in entries if isinstance(item, dict)}
    usable = []
    for path in clips:
        item = by_output.get(os.path.abspath(path)) or by_name.get(os.path.basename(path))
        if not item:
            return []
        status = item.get("quality_status")
        if (not item.get("media_validated") or item.get("fallback_used")
                or item.get("failed_stages")
                or status not in {"enhanced", "partial"}):
            return []
        usable.append(path)
    return usable


def list_clips(project_path, source="auto", selected_file=None):
    """Return rendered clips from an explicit source.

    ``auto`` keeps ``final_polished`` → ``final`` → ``cuts`` but falls back to
    the next source when the polish report marks any polished output as
    fallback, failed, invalid or missing. Explicit ``final_polished`` remains
    available for inspection; the upload worker blocks an unsafe real upload.
    """
    if not project_path or not os.path.isdir(project_path):
        return []
    source = str(source or "auto").strip().lower()
    if source == "specific_file":
        path = os.path.abspath(os.fspath(selected_file or "")) if selected_file else ""
        return [path] if path.lower().endswith(".mp4") and os.path.isfile(path) else []
    names = ["final_polished", "final", "cuts"] if source == "auto" else [source]
    for name in names:
        clips = _mp4_files(os.path.join(project_path, name))
        if not clips:
            continue
        if name == "final_polished" and source == "auto":
            clips = _usable_polished_clips(project_path, clips)
            if not clips:
                continue
        return clips
    return []


def list_clips_by_source(project_path):
    """Return all source buckets for UI diagnostics without changing precedence."""
    return {name: _mp4_files(os.path.join(project_path, name))
            for name in ("final_polished", "final", "cuts")}


def clip_index(video_path):
    """Leading digits of the filename → segment index (for gate checks)."""
    import re
    m = re.match(r"(\d+)", os.path.basename(video_path))
    return int(m.group(1)) if m else None


def _subtitle_files_for_clip(project_path, video_path):
    """Candidate subtitle JSONs for a clip (subs/<stem>*_processed.json etc)."""
    stem = os.path.splitext(os.path.basename(video_path))[0]
    subs_dir = os.path.join(project_path, "subs")
    if not os.path.isdir(subs_dir):
        return []
    candidates = []
    for name in sorted(os.listdir(subs_dir)):
        if not name.endswith(".json"):
            continue
        base = os.path.splitext(name)[0]
        if base.startswith(stem):
            candidates.append(os.path.join(subs_dir, name))
    return candidates


def find_subs_for_clip(project_path, video_path):
    """Best subtitle JSON for a clip (prefer *_processed.json)."""
    candidates = _subtitle_files_for_clip(project_path, video_path)
    if not candidates:
        return None
    processed = [c for c in candidates if "_processed" in os.path.basename(c)]
    return (processed or candidates)[0]


def segments_for_project(project_path):
    """The viral_segments.txt segments list (for title/caption suggestions)."""
    path = os.path.join(project_path, "viral_segments.txt")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        segments = data.get("segments", [])
        return segments if isinstance(segments, list) else []
    except Exception:
        return []


def clip_suggestion(project_path, video_path):
    """Suggested title+caption for a clip from its viral segment entry."""
    idx = clip_index(video_path)
    segments = segments_for_project(project_path)
    if idx is None or idx >= len(segments):
        return "", ""
    seg = segments[idx]
    return (seg.get("recommended_title") or seg.get("title") or ""), (seg.get("caption") or "")


# ---------------------------------------------------------------------------
# Per-clip translate
# ---------------------------------------------------------------------------

def translate_clip(project_path, video_path, target_lang):
    """Translate one clip's subtitles to target_lang (deep-translator).

    Returns (ok: bool, message: str). Writes <stem>_<lang>.json in subs/.
    """
    if not video_path or not os.path.exists(video_path):
        return False, "Clip not found."
    if not target_lang or target_lang.strip() == "":
        return False, "Target language is required (e.g. en, ar, fr)."

    src = find_subs_for_clip(project_path, video_path)
    if not src:
        return False, "No subtitle file for this clip."
    lang = target_lang.strip().lower().split("-")[0]
    dst = os.path.join(project_path, "subs",
                       "{}_translated_{}.json".format(
                           os.path.splitext(os.path.basename(src))[0], lang))
    try:
        from scripts.translate_json import translate_json_file
    except Exception as e:
        return False, ("Translation unavailable — install deps first: "
                       "pip install deep-translator tqdm ({})".format(e))
    try:
        data = asyncio.run(translate_json_file(src, dst, lang))
        count = len(data.get("segments", []))
        return True, "Copied: {} (translation for {})".format(
            os.path.basename(dst), lang) + " — {} segments".format(count)
    except Exception as e:
        return False, "Translation failed: {}".format(str(e)[:300])


def clip_subtitle_preview(project_path, video_path):
    """First lines of the clip's subtitle text for the UI preview."""
    src = find_subs_for_clip(project_path, video_path)
    if not src:
        return ""
    try:
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        texts = [s.get("text", "") for s in data.get("segments", []) if s.get("text")]
        return " | ".join(texts[:8])
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Music check
# ---------------------------------------------------------------------------

def run_music_check(project_path, local_db_path=""):
    """Run the Chromaprint check on the project. Returns a readable report."""
    try:
        from scripts import music_fingerprint as mf
    except Exception as e:
        return "Music fingerprint module unavailable: {}".format(e)

    local_db = None
    if local_db_path and os.path.isdir(local_db_path):
        cache = os.path.join(os.path.expanduser("~"), ".viralcutter", "music_db.json")
        try:
            local_db = mf.build_local_db(local_db_path, cache_path=cache)
        except Exception as e:
            return "Local DB build failed: {}".format(e)
    elif local_db_path:
        local_db = mf.load_local_db(local_db_path)

    try:
        report = mf.analyze_project(project_path, local_db=local_db, gate="warn")
    except Exception as e:
        return "Music check failed: {}".format(e)

    s = report["summary"]
    lines = ["Music check: {} clips checked, {} matched.".format(
        s.get("checked", 0), s.get("matched", 0))]
    if s.get("no_fpcalc"):
        lines.append("⚠️ {} clips: Chromaprint not installed (see docs).".format(
            s["no_fpcalc"]))
    for clip in report.get("clips", []):
        verdict = clip.get("verdict", "?")
        mark = {"clean": "✅", "acoustid_match": "🎵⚠️", "local_match": "🎵⚠️",
                "no_fpcalc": "⚠️", "error": "❌"}.get(verdict, "?")
        lines.append("  {} #{} {} — {}".format(
            mark, clip.get("index", "?"),
            os.path.basename(clip.get("video", "")), verdict))
        if clip.get("suggestion"):
            lines.append("      ↳ {}".format(clip["suggestion"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Upload through the safety gate (streaming)
# ---------------------------------------------------------------------------


def _publish_result(status, video_path, title="", publish_at=None, **extra):
    result = {
        "status": status,
        "video": os.path.basename(video_path or ""),
        "video_path": os.path.abspath(video_path) if video_path else "",
        "title": title or "",
        "publish_at": publish_at,
    }
    result.update({key: value for key, value in extra.items() if value is not None})
    return result


def _polish_upload_allowed(project_path, video_path):
    """Explicit final_polished selection must not bypass a failed report."""
    normal = os.path.normcase(os.path.abspath(video_path or ""))
    if os.path.normcase(os.sep + "final_polished" + os.sep) not in normal:
        return True, ""
    report = _load_json(os.path.join(project_path, "polish_report.json"))
    if not report:
        return True, ""
    for item in report.get("clips", []) or []:
        if (os.path.abspath(str(item.get("output") or "")) == os.path.abspath(video_path)
                or item.get("video") == os.path.basename(video_path)):
            safe = (item.get("media_validated") and not item.get("fallback_used")
                    and not item.get("failed_stages")
                    and item.get("quality_status") in {"enhanced", "partial"})
            if safe:
                return True, ""
            return False, "final_polished output is fallback/failed/invalid according to polish_report.json"
    return False, "final_polished output has no matching entry in polish_report.json"


def _upload_worker(project_path, platform, video_path, title, caption,
                   hashtags, dry_run, music_gate, client_secrets_path,
                   privacy_status, publish_at, out_queue, oauth_full_access=False,
                   require_existing_auth=False, public_confirm=False):
    final_result = None

    def emit(msg):
        out_queue.put({"type": "log", "message": str(msg)})

    def finish(result):
        nonlocal final_result
        final_result = result
        out_queue.put({"type": "result", "result": result})

    try:
        polish_ok, polish_detail = _polish_upload_allowed(project_path, video_path)
        if not polish_ok and not dry_run:
            emit("⛔ تم منع رفع final_polished غير الموثوق: {}".format(polish_detail))
            finish(_publish_result("blocked", video_path, title, publish_at,
                                   error=polish_detail, reason="polish_report"))
            return
        if platform == "youtube" and not dry_run:
            from scripts import content_guard
            channel_state = content_guard.channel_status(project_path, "youtube")
            if channel_state.get("locked"):
                emit("⛔ تم إيقاف الرفع قبل OAuth: قاطع دائرة القناة مقفول بسبب حادثة سياسة مسجلة.")
                finish(_publish_result("blocked", video_path, title, publish_at,
                                       error="channel circuit breaker locked", reason="channel_circuit_breaker"))
                return
        if platform == "youtube" and str(privacy_status or "private").lower() == "public" and not dry_run and not public_confirm:
            emit("❌ تم إيقاف الرفع العام: فعّل تأكيد النشر العام أولاً.")
            finish(_publish_result("blocked", video_path, title, publish_at,
                                   error="public confirmation required", reason="public_confirmation"))
            return
        from scripts import upload_gate as ug
        from webui import publish_history
        if platform == "youtube" and client_secrets_path:
            from webui.youtube_credentials import (
                replace_client_secrets,
                store_client_secrets,
            )
            stored = store_client_secrets(client_secrets_path)
            if stored.get("changed"):
                stored = replace_client_secrets(client_secrets_path, invalidate_token=True)
            client_secrets_path = stored["path"]
            emit("[oauth] client secrets validated and stored securely")
        emit("[gate] running safety checks for #{} ...".format(clip_index(video_path)))
        uploader_kwargs = {"dry_run": dry_run, "music_gate": music_gate}
        upload_kwargs = {}
        if platform == "youtube":
            if client_secrets_path:
                uploader_kwargs["client_secrets_path"] = client_secrets_path
            if oauth_full_access:
                uploader_kwargs["oauth_full_access"] = True
            if privacy_status and privacy_status != "private":
                uploader_kwargs["privacy_status"] = privacy_status
                upload_kwargs["privacy_status"] = privacy_status
            if publish_at:
                uploader_kwargs["publish_at"] = publish_at
                upload_kwargs["publish_at"] = publish_at
        prior = None
        if not dry_run:
            prior = publish_history.find_success(
                project_path, platform=platform, video_path=video_path)
        if prior:
            prior_id = prior.get("video_id") or prior.get("url") or "سجل سابق"
            emit("⚠️ تم تخطي الرفع: هذا الملف رُفع سابقاً بنجاح ({})".format(prior_id))
            finish(_publish_result("skipped_duplicate", video_path, title, publish_at,
                                   prior_id=prior_id, reason="publish_history"))
            return
        uploader = ug.UPLOADERS[platform](project_path, **uploader_kwargs)
        if require_existing_auth and platform == "youtube":
            uploader.ensure_authenticated()
            emit("[oauth] قناة YouTube متصلة والتوكن صالح قبل الرفع")
        # WebUI uploads must validate the actual rendered file before any API call.
        uploader.validate_video = True
        result = uploader.upload(video_path, title, caption, hashtags,
                                 index=clip_index(video_path), **upload_kwargs)
        publish_history.record(project_path, platform=platform, video_path=video_path,
                               title=title, result=result,
                               privacy_status=privacy_status, publish_at=publish_at)
        try:
            from webui import project_store
            project_store.update_manifest(
                project_path,
                publish_status=result.get("status", "uploaded"),
                last_publish={
                    "platform": platform,
                    "video": os.path.basename(video_path),
                    "video_id": result.get("video_id"),
                    "url": result.get("url"),
                    "privacy_status": privacy_status,
                    "publish_at": publish_at,
                },
            )
        except Exception:
            pass
        status = str((result or {}).get("status") or ("scheduled" if publish_at else "uploaded"))
        if status == "dry-run":
            status = "dry_run"
        base_publish_at = (result or {}).get("publish_at") or publish_at
        extras = {key: value for key, value in (result or {}).items()
                  if key not in {"status", "video", "video_path", "title", "publish_at"}}
        normalized = _publish_result(status, video_path, title, base_publish_at, **extras)
        emit("✅ {}".format(json.dumps(normalized, ensure_ascii=False)))
        finish(normalized)
    except Exception as e:
        if hasattr(e, "reasons"):
            source_labels = {
                "publish_blocklist": "قائمة منع النشر",
                "safety_report": "تقرير الأمان",
                "semantic_safety": "الأمان الدلالي",
                "metadata_compliance": "بيانات النشر",
                "missing_video": "الفيديو النهائي",
                "media_validation": "فحص ملف الفيديو",
                "music_fingerprint": "بصمة الموسيقى",
                "visual_safety": "الفحص البصري للمحتوى الحساس",
                "content_guard": "حارس المصدر ومنع التكرار",
            }
            emit("❌ تم منع الرفع بواسطة بوابة الأمان قبل الاتصال بالمنصة.")
            for reason in getattr(e, "reasons", []):
                label = source_labels.get(reason.get("source"), reason.get("source", "فحص"))
                emit("  • {}: {}".format(label, reason.get("detail", "راجع التقرير")))
            finish(_publish_result("blocked", video_path, title, publish_at,
                                   error=str(e)[:1000], reason="safety_gate",
                                   reasons=getattr(e, "reasons", [])))
        try:
            from webui import publish_history
            publish_history.record(project_path, platform=platform, video_path=video_path,
                                   title=title, error=e,
                                   privacy_status=privacy_status, publish_at=publish_at)
            try:
                from webui import project_store
                project_store.update_manifest(
                    project_path, publish_status="failed",
                    last_publish_error=str(e)[:1000],
                )
            except Exception:
                pass
        except Exception:
            pass
        if final_result is None:
            finish(_publish_result("failed", video_path, title, publish_at,
                                   error=str(e)[:1000]))
        emit("❌ {}".format(e))
    finally:
        if final_result is None:
            finish(_publish_result("failed", video_path, title, publish_at,
                                   error="upload worker ended without a result"))
        out_queue.put({"type": "done"})


def stream_upload(project_path, platform, video_path, title, caption,
                  hashtags, dry_run, music_gate, client_secrets_path=None,
                  privacy_status="private", publish_at=None, oauth_full_access=False,
                  require_existing_auth=False, public_confirm=False):
    """Yield log lines and return one structured result to a batch caller."""
    out_queue = queue.Queue()
    if not video_path or not os.path.exists(video_path):
        result = _publish_result("failed", video_path, title, publish_at,
                                 error="Clip not found")
        yield "Clip not found."
        return result
    thread = threading.Thread(
        target=_upload_worker,
        args=(project_path, platform, video_path, title, caption,
              hashtags, dry_run, music_gate, client_secrets_path, privacy_status,
              publish_at, out_queue, oauth_full_access, require_existing_auth, public_confirm),
        daemon=True,
    )
    thread.start()

    lines = []
    result = None
    while True:
        try:
            event = out_queue.get(timeout=0.5)
        except queue.Empty:
            if not thread.is_alive():
                break
            yield "\n".join(lines)
            continue
        if isinstance(event, dict) and event.get("type") == "done":
            break
        if isinstance(event, dict) and event.get("type") == "result":
            result = event.get("result")
            continue
        message = event.get("message") if isinstance(event, dict) else str(event)
        lines.append(str(message))
        yield "\n".join(lines)
    result = result or _publish_result("failed", video_path, title, publish_at,
                                       error="upload worker returned no structured result")
    lines.append("Upload finished. [{}]".format(result.get("status", "failed")))
    yield "\n".join(lines)
    return result


def _write_batch_report(project_path, report):
    path = os.path.join(project_path, "publish_batch_report.json")
    temp = path + ".tmp"
    try:
        with open(temp, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    except OSError:
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass


def _batch_summary(items):
    counts = {status: 0 for status in (
        "uploaded", "scheduled", "dry_run", "skipped_duplicate", "blocked", "failed")}
    for item in items:
        status = str(item.get("status") or "failed")
        counts[status] = counts.get(status, 0) + 1
    return {"total": len(items), "counts": counts,
            "successful": counts["uploaded"] + counts["scheduled"],
            "failed": counts["failed"], "blocked": counts["blocked"],
            "skipped_duplicate": counts["skipped_duplicate"]}


def stream_upload_batch(project_path, platform, video_paths, dry_run, music_gate,
                        client_secrets_path=None, privacy_status="private",
                        publish_at=None, oauth_full_access=False,
                        require_existing_auth=False, public_confirm=False,
                        schedule_interval_minutes=60, retry_failed_only=False):
    """Upload every selected clip and persist exact per-clip outcomes."""
    all_paths = [os.path.abspath(os.fspath(path)) for path in (video_paths or [])
                 if path and os.path.isfile(path) and str(path).lower().endswith(".mp4")]
    previous = _load_json(os.path.join(project_path, "publish_batch_report.json")) or {}
    if retry_failed_only:
        failed_paths = {
            os.path.abspath(str(item.get("video_path")))
            for item in previous.get("items", []) or []
            if isinstance(item, dict) and item.get("status") == "failed" and item.get("video_path")
        }
        paths = [path for path in all_paths if path in failed_paths]
    else:
        paths = all_paths
    if not paths:
        if retry_failed_only:
            yield "✅ لا توجد عناصر فاشلة في آخر دفعة تحتاج إلى retry."
        else:
            yield "❌ لم يتم العثور على ملفات MP4 صالحة لمصدر الرفع."
        return
    try:
        interval_raw = 60 if schedule_interval_minutes is None else schedule_interval_minutes
        interval = float(interval_raw)
        if not math.isfinite(interval) or interval < 1 or interval > 10080:
            raise ValueError("invalid interval")
    except (TypeError, ValueError):
        yield "❌ الفاصل يجب أن يكون بين 1 و10080 دقيقة؛ أوقف النظام هذه الدفعة قبل الرفع."
        return
    schedule_start = None
    if publish_at:
        try:
            schedule_start = datetime.datetime.fromisoformat(
                str(publish_at).strip().replace("Z", "+00:00"))
            if schedule_start.tzinfo is None:
                raise ValueError("missing timezone")
        except (TypeError, ValueError):
            yield "❌ وقت بداية الجدولة أو الفاصل غير صالح؛ أوقف النظام هذه الدفعة قبل الرفع."
            return
    report = {
        "version": 1,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "platform": platform,
        "dry_run": bool(dry_run),
        "retry_failed_only": bool(retry_failed_only),
        "requested_paths": all_paths,
        "retry_paths": paths if retry_failed_only else [],
        "resumed_from": "publish_batch_report.json" if retry_failed_only else None,
        "items": list(previous.get("items", []) or []) if retry_failed_only else [],
    }
    _write_batch_report(project_path, report)
    yield "[upload] تجهيز {} مقطعاً من المصدر المحدد...".format(len(paths))
    if schedule_start and len(paths) > 1:
        yield "[schedule] جدولة تلقائية: البداية {} — الفاصل {} دقيقة — {} مقاطع.".format(
            schedule_start.isoformat(), int(interval) if interval.is_integer() else interval, len(paths))
    for number, path in enumerate(paths, 1):
        title, caption = clip_suggestion(project_path, path)
        title = title or os.path.splitext(os.path.basename(path))[0][:100]
        yield "\n[upload] ({}/{}) {}".format(number, len(paths), os.path.basename(path))
        item_publish_at = None
        if schedule_start:
            item_publish_at = (schedule_start + datetime.timedelta(
                minutes=interval * (number - 1))).isoformat()
        result = yield from stream_upload(
            project_path, platform, path, title, caption, [], dry_run, music_gate,
            client_secrets_path, privacy_status, item_publish_at, oauth_full_access,
            require_existing_auth, public_confirm,
        )
        result = result or _publish_result("failed", path, title, item_publish_at,
                                           error="missing structured result")
        replaced = False
        for index, previous_item in enumerate(report["items"]):
            if previous_item.get("video_path") == result.get("video_path"):
                report["items"][index] = result
                replaced = True
                break
        if not replaced:
            report["items"].append(result)
        report["summary"] = _batch_summary(report["items"])
        _write_batch_report(project_path, report)
        yield "[upload] نتيجة {}: {}".format(os.path.basename(path), result.get("status", "failed"))
    summary = report["summary"]
    if summary["failed"] or summary["blocked"]:
        yield "⚠️ اكتملت الدفعة مع مشاكل: uploaded={} scheduled={} dry_run={} skipped_duplicate={} blocked={} failed={}. أعد المحاولة للفاشل فقط بعد إصلاح السبب.".format(
            summary["counts"].get("uploaded", 0), summary["counts"].get("scheduled", 0),
            summary["counts"].get("dry_run", 0), summary["counts"].get("skipped_duplicate", 0),
            summary["blocked"], summary["failed"])
    elif summary["counts"].get("dry_run", 0) == summary["total"]:
        yield "✅ اكتملت المحاكاة لكل الملفات (Dry Run) — لم يُرفع أي فيديو فعلياً."
    else:
        yield "✅ اكتمل رفع/جدولة كل الملفات المحددة: {} عنصر ناجح.".format(summary["successful"])
