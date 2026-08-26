"""Safe local Telegram control for OUSSAMA Cutter.

The bot is deliberately disabled unless all three environment settings are
present: ``VIRALCUTTER_TELEGRAM_ENABLED=1``,
``VIRALCUTTER_TELEGRAM_BOT_TOKEN``, and ``VIRALCUTTER_TELEGRAM_CHAT_IDS``.
It uses Telegram long polling from the Windows machine, so no public inbound
port is required. It never accepts OAuth/client secrets or arbitrary files and
never performs a real YouTube upload; sensitive publishing remains behind the
WebUI confirmation gates.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping

try:
    import requests
except ImportError:  # Optional feature: core WebUI remains usable without it.
    requests = None

MAX_MESSAGE_LENGTH = 3900
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_LONG_POLL_TIMEOUT = 20


class TelegramControlError(RuntimeError):
    """Base error for the local Telegram control service."""


class TelegramAPIError(TelegramControlError):
    """An error returned by Telegram or the network client."""


@dataclass(frozen=True)
class TelegramConfig:
    """Validated non-secret settings for the optional bot service."""

    enabled: bool = False
    token: str = field(default="", repr=False)
    allowed_chat_ids: frozenset[str] = frozenset()
    poll_interval: float = DEFAULT_POLL_INTERVAL
    long_poll_timeout: int = DEFAULT_LONG_POLL_TIMEOUT
    notify_terminal: bool = False

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.token and self.allowed_chat_ids)


def _is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def parse_chat_ids(value: str | Iterable[object] | None) -> frozenset[str]:
    """Parse integer Telegram chat IDs and discard malformed values."""
    if isinstance(value, str):
        values = re.split(r"[\s,;]+", value.strip()) if value.strip() else []
    else:
        values = list(value or [])
    result: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if re.fullmatch(r"-?\d+", text):
            result.add(text)
    return frozenset(result)


def status_html(environ: Mapping[str, str] | None = None) -> str:
    """Return a secret-free status card for the WebUI home page."""
    config = config_from_env(environ)
    if not config.enabled:
        return "<div class='vc-validation vc-validation-warn'>ℹ️ Telegram Control Center غير مفعّل. لا يتم تشغيل أي polling.</div>"
    if not config.token or not config.allowed_chat_ids:
        return "<div class='vc-validation vc-validation-error'>⚠️ Telegram مفعّل لكن الإعداد غير مكتمل؛ أضف Bot Token وChat ID مسموحاً.</div>"
    return "<div class='vc-validation vc-validation-ok'>✅ Telegram Control Center مفعّل محلياً — {} محادثة مصرح بها — لا تُقبل ملفات OAuth ولا يُنفذ رفع YouTube مباشرة.</div>".format(len(config.allowed_chat_ids))


def config_from_env(environ: Mapping[str, str] | None = None) -> TelegramConfig:
    """Read opt-in configuration without ever printing the bot token."""
    env = environ if environ is not None else os.environ
    try:
        poll_interval = float(env.get("VIRALCUTTER_TELEGRAM_POLL_INTERVAL", DEFAULT_POLL_INTERVAL))
    except (TypeError, ValueError):
        poll_interval = DEFAULT_POLL_INTERVAL
    poll_interval = max(1.0, min(60.0, poll_interval))
    try:
        long_poll_timeout = int(env.get("VIRALCUTTER_TELEGRAM_LONG_POLL_TIMEOUT", DEFAULT_LONG_POLL_TIMEOUT))
    except (TypeError, ValueError):
        long_poll_timeout = DEFAULT_LONG_POLL_TIMEOUT
    long_poll_timeout = max(0, min(50, long_poll_timeout))
    return TelegramConfig(
        enabled=_is_true(env.get("VIRALCUTTER_TELEGRAM_ENABLED")),
        token=str(env.get("VIRALCUTTER_TELEGRAM_BOT_TOKEN", "") or "").strip(),
        allowed_chat_ids=parse_chat_ids(env.get("VIRALCUTTER_TELEGRAM_CHAT_IDS", "")),
        poll_interval=poll_interval,
        long_poll_timeout=long_poll_timeout,
        notify_terminal=_is_true(env.get("VIRALCUTTER_TELEGRAM_NOTIFY_TERMINAL")),
    )


def redact(text: object) -> str:
    """Remove common Telegram/API token forms before a message is emitted."""
    value = str(text or "")
    value = re.sub(r"(VIRALCUTTER_TELEGRAM_BOT_TOKEN\s*[=:]\s*)[^\s,;]+", r"\1<redacted>", value, flags=re.I)
    value = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "<telegram-token-redacted>", value)
    return value


def _chunks(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    text = redact(text).strip() or "(لا توجد تفاصيل)"
    return [text[index:index + limit] for index in range(0, len(text), limit)] or [text]


class TelegramAPI:
    """Small requests-based Telegram Bot API client.

    The token is kept only in the process and is never included in exception
    messages. Tests can inject a fake session and base URL.
    """

    def __init__(self, token: str, *, session=None, base_url: str = "https://api.telegram.org", timeout: float = 20.0):
        token = str(token or "").strip()
        if not token:
            raise ValueError("Telegram bot token is required")
        if session is None and requests is None:
            raise TelegramControlError("requests is required to enable Telegram control")
        self._base_url = base_url.rstrip("/") + "/bot" + token
        self._session = session or requests.Session()
        self._timeout = max(1.0, float(timeout))

    def call(self, method: str, payload: Mapping[str, object] | None = None):
        try:
            response = self._session.post(
                self._base_url + "/" + str(method).strip(),
                json=dict(payload or {}),
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise TelegramAPIError("Telegram API request failed: {}".format(redact(exc))) from None
        if not isinstance(data, dict) or not data.get("ok"):
            description = data.get("description", "unknown Telegram error") if isinstance(data, dict) else "invalid response"
            raise TelegramAPIError("Telegram API rejected request: {}".format(redact(description)))
        return data.get("result")

    def get_updates(self, offset: int | None = None, timeout: int = DEFAULT_LONG_POLL_TIMEOUT):
        payload = {
            "timeout": max(0, min(50, int(timeout))),
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = int(offset)
        result = self.call("getUpdates", payload)
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: str, text: str):
        return self.call("sendMessage", {
            "chat_id": str(chat_id),
            "text": redact(text),
            "disable_web_page_preview": True,
        })


@dataclass(frozen=True)
class CommandContext:
    chat_id: str
    username: str = ""
    message_id: str = ""


Handler = Callable[[CommandContext, str], str | None]


class TelegramCommandRouter:
    """Authorize chats and dispatch a small, explicit command allow-list."""

    def __init__(self, api: TelegramAPI, allowed_chat_ids: Iterable[object], handlers: Mapping[str, Handler] | None = None):
        self.api = api
        self.allowed_chat_ids = parse_chat_ids(allowed_chat_ids)
        self.handlers = {str(key).lower().lstrip("/"): value for key, value in (handlers or {}).items()}

    def _help(self) -> str:
        return (
            "أوامر OUSSAMA Cutter الآمنة:\n"
            "/status — حالة الطابور\n"
            "/projects — عرض المشاريع المحلية\n"
            "/audit <project> — ملخص التقارير المحلية\n"
            "/pause — إيقاف بدء مهام جديدة\n"
            "/resume — استئناف الطابور\n"
            "/retry_failed — إعادة الفاشل فقط\n"
            "/cancel <job_id> — إلغاء مهمة محددة\n"
            "/cancel_all — طلب تأكيد إلغاء كل المهام\n"
            "/confirm_cancel_all — تأكيد الإلغاء الجماعي\n"
            "/help — عرض هذه القائمة\n\n"
            "لا يستقبل البوت client_secrets أو OAuth ولا ينفذ رفع YouTube حقيقياً مباشرة."
        )

    def _send(self, chat_id: str, text: str | None):
        if text is None:
            return
        for part in _chunks(text):
            self.api.send_message(chat_id, part)

    def handle_update(self, update: Mapping[str, object]) -> bool:
        if not isinstance(update, Mapping):
            return False
        message = update.get("message")
        if not isinstance(message, Mapping):
            return False
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, Mapping):
            return False
        chat_id = str(chat.get("id", "")).strip()
        if chat_id not in self.allowed_chat_ids:
            # Do not answer unknown chats: this avoids confirming that the bot
            # is active and prevents an attacker from probing the interface.
            return False
        text = str(message.get("text", "") or "").strip()
        if not text.startswith("/"):
            return False
        command_line = text[1:].split(None, 1)
        command = command_line[0].split("@", 1)[0].lower()
        args = command_line[1].strip() if len(command_line) > 1 else ""
        context = CommandContext(
            chat_id=chat_id,
            username=str((sender or {}).get("username", "")) if isinstance(sender, Mapping) else "",
            message_id=str(message.get("message_id", "")),
        )
        if command in {"start", "help"}:
            self._send(chat_id, self._help())
            return True
        handler = self.handlers.get(command)
        if handler is None:
            self._send(chat_id, "الأمر غير معروف. استخدم /help.")
            return True
        try:
            self._send(chat_id, handler(context, args))
        except Exception as exc:
            self._send(chat_id, "❌ فشل تنفيذ الأمر محلياً: {}".format(redact(exc)[:700]))
        return True


class TelegramControlService:
    """Background long-polling service for the local Windows process."""

    def __init__(self, config: TelegramConfig, router: TelegramCommandRouter, *, api=None, queue=None, logger=print):
        if not config.ready:
            raise ValueError("Telegram service requires enabled, token, and allowed chat IDs")
        self.config = config
        self.router = router
        self.api = api or router.api
        self.queue = queue
        self.notify_terminal = bool(config.notify_terminal and queue is not None)
        self.logger = logger
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset: int | None = None
        self._notified_terminal: set[tuple[str, str, str]] = set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.running:
            return self
        self._seed_terminal_notifications()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="oussama-telegram-control", daemon=True)
        self._thread.start()
        return self

    def _seed_terminal_notifications(self):
        """Mark existing terminal jobs as seen so restart does not spam history."""
        if not self.notify_terminal or self.queue is None:
            return
        try:
            snapshots = self.queue.snapshot() or {}
        except Exception:
            return
        for job_id, snapshot in snapshots.items():
            if not isinstance(snapshot, Mapping):
                continue
            status = str(snapshot.get("status", ""))
            if status in {"succeeded", "failed", "cancelled"}:
                self._notified_terminal.add((str(job_id), status, str(snapshot.get("finished", ""))))

    def stop(self, timeout: float = 5.0):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(max(0.0, float(timeout)))
        self._thread = None

    def _notify_terminal_jobs(self):
        """Send optional, metadata-free completion notices to authorized chats."""
        if not self.notify_terminal or self.queue is None:
            return
        try:
            snapshots = self.queue.snapshot() or {}
        except Exception as exc:
            self.logger("[telegram] notification snapshot skipped: {}".format(redact(exc)[:300]))
            return
        terminal = {"succeeded", "failed", "cancelled"}
        for job_id, snapshot in snapshots.items():
            if not isinstance(snapshot, Mapping):
                continue
            status = str(snapshot.get("status", ""))
            if status not in terminal:
                continue
            key = (str(job_id), status, str(snapshot.get("finished", "")))
            if key in self._notified_terminal:
                continue
            label = {
                "succeeded": "نجحت",
                "failed": "فشلت",
                "cancelled": "أُلغيت",
            }.get(status, status)
            text = "🔔 انتهت مهمة OUSSAMA Cutter: {} — المعرّف {}".format(label, str(job_id)[:12])
            delivered = True
            for chat_id in self.config.allowed_chat_ids:
                try:
                    self.api.send_message(chat_id, text)
                except Exception as exc:
                    delivered = False
                    self.logger("[telegram] terminal notification skipped: {}".format(redact(exc)[:300]))
            if delivered:
                self._notified_terminal.add(key)

    def _run(self):
        self.logger("[telegram] local control enabled for {} authorized chat(s)".format(len(self.config.allowed_chat_ids)))
        while not self._stop.is_set():
            try:
                updates = self.api.get_updates(self._offset, self.config.long_poll_timeout)
                for update in updates:
                    if isinstance(update, Mapping) and update.get("update_id") is not None:
                        self._offset = int(update["update_id"]) + 1
                    self.router.handle_update(update)
                self._notify_terminal_jobs()
            except Exception as exc:
                self.logger("[telegram] polling paused: {}".format(redact(exc)[:500]))
                self._stop.wait(self.config.poll_interval)


def _queue_counts(queue) -> dict[str, int]:
    snapshots = list((queue.snapshot() or {}).values())
    counts: dict[str, int] = {}
    for snapshot in snapshots:
        status = str(snapshot.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def build_queue_handlers(queue, project_root: str | None = None) -> dict[str, Handler]:
    """Create safe local handlers; no upload or secret/file handler exists."""
    pending_cancel_all: dict[str, float] = {}

    def _project_path(name: str):
        if not project_root:
            return None
        raw_name = str(name or "").strip()
        if (not raw_name or raw_name in {".", ".."}
                or "/" in raw_name or "\\" in raw_name):
            return None
        name = os.path.basename(raw_name)
        if not name or name in {".", ".."}:
            return None
        root = os.path.abspath(project_root)
        path = os.path.abspath(os.path.join(root, name))
        try:
            if os.path.commonpath([root, path]) != root or not os.path.isdir(path):
                return None
        except ValueError:
            return None
        return path

    def _read_json(path):
        try:
            import json
            with open(path, "r", encoding="utf-8") as stream:
                value = json.load(stream)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}
    def status(_context: CommandContext, _args: str):
        counts = _queue_counts(queue)
        warning = getattr(queue, "state_warning", "")
        active = [job for job in queue.active() if job]
        lines = [
            "حالة OUSSAMA Cutter:",
            "queued={} · running={} · retrying={} · succeeded={} · failed={} · cancelled={}".format(
                counts.get("queued", 0), counts.get("running", 0), counts.get("retrying", 0),
                counts.get("succeeded", 0), counts.get("failed", 0), counts.get("cancelled", 0)),
            "الطابور: {}".format("متوقف مؤقتاً" if queue.paused else "قيد التشغيل"),
        ]
        if active:
            lines.append("المهام النشطة: {}".format(", ".join(str(job.id)[:12] for job in active[:8])))
        if warning:
            lines.append("⚠️ {}".format(warning))
        return "\n".join(lines)

    def projects(_context: CommandContext, _args: str):
        if not project_root or not os.path.isdir(project_root):
            return "ℹ️ لا يوجد مجلد مشاريع محلي متاح."
        names = []
        try:
            for name in sorted(os.listdir(project_root)):
                path = _project_path(name)
                if path:
                    names.append(name)
        except OSError as exc:
            return "❌ تعذر قراءة المشاريع: {}".format(redact(exc)[:300])
        if not names:
            return "ℹ️ لا توجد مشاريع محلية."
        return "المشاريع المحلية:\n" + "\n".join("• " + name for name in names[:50])

    def audit(_context: CommandContext, args: str):
        path = _project_path(args)
        if not path:
            return "❌ اسم المشروع غير صحيح أو خارج المجلد المحلي. أرسل اسماً بعد /audit واستخدم /projects أولاً."
        polish = _read_json(os.path.join(path, "polish_report.json"))
        tracking = _read_json(os.path.join(path, "tracking_report.json"))
        batch = _read_json(os.path.join(path, "publish_batch_report.json"))
        summary = polish.get("summary") or {}
        counts = batch.get("summary") or {}
        lines = ["تدقيق المشروع: {}".format(os.path.basename(path))]
        if summary:
            lines.append("Polish: enhanced={} · partial={} · fallback={} · failed={}".format(
                summary.get("enhanced", 0), summary.get("partial", 0),
                summary.get("fallback", 0), summary.get("failed", 0)))
        else:
            lines.append("Polish: لا يوجد تقرير صالح")
        if tracking:
            lines.append("Tracking: backend={} · active_speaker_applied={}".format(
                tracking.get("backend", "unknown"), tracking.get("active_speaker_applied", False)))
        else:
            lines.append("Tracking: لا يوجد tracking_report.json")
        if counts:
            lines.append("Publish: uploaded={} · scheduled={} · failed={} · blocked={}".format(
                counts.get("uploaded", 0), counts.get("scheduled", 0),
                counts.get("failed", 0), counts.get("blocked", 0)))
        else:
            lines.append("Publish: لا يوجد publish_batch_report.json")
        return "\n".join(lines)

    def pause(_context: CommandContext, _args: str):
        queue.pause_all()
        return "⏸️ تم إيقاف بدء المهام الجديدة؛ الطابور متوقف مؤقتاً. المهمة الجارية لا تُقتل قسراً."

    def resume(_context: CommandContext, _args: str):
        queue.resume_all()
        return "▶️ تم استئناف الطابور مع منع إدخال المهمة نفسها أكثر من مرة."

    def retry_failed(_context: CommandContext, _args: str):
        ids = queue.retry_failed()
        return "🔁 تمت إعادة {} مهمة فاشلة/ملغاة فقط.".format(len(ids))

    def cancel(_context: CommandContext, args: str):
        job_id = str(args or "").strip()
        if not job_id or job_id.lower() in {"all", "*"}:
            return "⚠️ استخدم أمر الإلغاء الجماعي: /cancel_all ثم /confirm_cancel_all."
        job = queue.get(job_id)
        if not job:
            return "❌ المهمة غير موجودة: {}".format(job_id[:80])
        if job.status in getattr(queue, "TERMINAL_STATES", {"succeeded", "failed", "cancelled"}):
            return "ℹ️ المهمة نهائية بالفعل: {}".format(job.status)
        return "🛑 {}: {}".format(job_id[:40], queue.cancel(job_id))

    def cancel_all(context: CommandContext, _args: str):
        pending_cancel_all[context.chat_id] = time.monotonic() + 60.0
        return "⚠️ هذا سيلغي كل المهام غير النهائية. للتأكيد، إذا كنت متأكداً أرسل /confirm_cancel_all خلال دقيقة."

    def confirm_cancel_all(context: CommandContext, _args: str):
        deadline = pending_cancel_all.pop(context.chat_id, 0.0)
        if deadline < time.monotonic():
            return "ℹ️ لا يوجد طلب إلغاء جماعي مؤكد أو انتهت مهلة الدقيقة. أرسل /cancel_all أولاً."
        cancelled = 0
        for job in list(queue.active()):
            try:
                queue.cancel(job.id)
                cancelled += 1
            except Exception:
                continue
        return "🛑 طُلب إلغاء {} مهمة نشطة.".format(cancelled)

    return {
        "status": status,
        "projects": projects,
        "audit": audit,
        "pause": pause,
        "resume": resume,
        "retry_failed": retry_failed,
        "cancel": cancel,
        "cancel_all": cancel_all,
        "confirm_cancel_all": confirm_cancel_all,
    }


def start_from_environment(queue, *, project_root=None, session=None, logger=print):
    """Start only when explicit, complete environment configuration exists."""
    config = config_from_env()
    if not config.enabled:
        return None
    if not config.token or not config.allowed_chat_ids:
        logger("[telegram] disabled: set BOT_TOKEN and at least one CHAT_ID; token was not printed")
        return None
    api = TelegramAPI(config.token, session=session)
    project_root = project_root or os.environ.get("VIRALCUTTER_VIRALS_DIR", "").strip() or None
    router = TelegramCommandRouter(
        api,
        config.allowed_chat_ids,
        build_queue_handlers(queue, project_root=project_root),
    )
    return TelegramControlService(config, router, api=api, queue=queue, logger=logger).start()
