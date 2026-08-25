import json

from webui.telegram_control import (
    TelegramAPI,
    TelegramCommandRouter,
    TelegramControlService,
    build_queue_handlers,
    config_from_env,
    parse_chat_ids,
    redact,
    start_from_environment,
    status_html,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError("http failure")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload=None):
        self.payload = payload or {"ok": True, "result": []}
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


class FakeJob:
    def __init__(self, job_id, status="running"):
        self.id = job_id
        self.status = status


class FakeQueue:
    def __init__(self):
        self.paused = False
        self.state_warning = ""
        self.jobs = {"abc": {"status": "running"}}
        self.did_cancel = []

    def snapshot(self):
        return self.jobs

    def active(self):
        return [FakeJob("abc")]

    def pause_all(self):
        self.paused = True

    def resume_all(self):
        self.paused = False

    def retry_failed(self):
        return ["failed-1"]

    def get(self, job_id):
        return FakeJob(job_id) if job_id == "abc" else None

    def cancel(self, job_id):
        self.did_cancel.append(job_id)
        return "cancelling"


def update(text, chat_id="10"):
    return {
        "update_id": 1,
        "message": {
            "message_id": 2,
            "text": text,
            "chat": {"id": int(chat_id)},
            "from": {"username": "owner"},
        },
    }


def test_config_and_token_redaction_are_fail_closed():
    assert parse_chat_ids("10, -20 bad") == frozenset({"10", "-20"})
    config = config_from_env({
        "VIRALCUTTER_TELEGRAM_ENABLED": "1",
        "VIRALCUTTER_TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh",
        "VIRALCUTTER_TELEGRAM_CHAT_IDS": "10",
    })
    assert config.ready
    assert "123456:" not in redact("token=123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh")
    assert not config_from_env({"VIRALCUTTER_TELEGRAM_ENABLED": "1"}).ready


def test_status_html_is_secret_free_and_reports_configuration_state():
    token = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh"
    ready = status_html({
        "VIRALCUTTER_TELEGRAM_ENABLED": "1",
        "VIRALCUTTER_TELEGRAM_BOT_TOKEN": token,
        "VIRALCUTTER_TELEGRAM_CHAT_IDS": "10, 20",
    })
    assert "مفعّل" in ready
    assert "2" in ready
    assert token not in ready
    assert "غير مفعّل" in status_html({})
    incomplete = status_html({"VIRALCUTTER_TELEGRAM_ENABLED": "1"})
    assert "غير مكتمل" in incomplete
    assert token not in incomplete


def test_unknown_chat_is_ignored_without_response():
    session = FakeSession()
    api = TelegramAPI("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh", session=session, base_url="http://test")
    router = TelegramCommandRouter(api, ["10"], {"status": lambda _ctx, _args: "ok"})
    assert router.handle_update(update("/status", chat_id="99")) is False
    assert session.calls == []


def test_router_dispatches_allowlisted_commands_and_help():
    session = FakeSession()
    api = TelegramAPI("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh", session=session, base_url="http://test")
    router = TelegramCommandRouter(api, ["10"], {"status": lambda _ctx, args: "status:" + args})
    assert router.handle_update(update("/status now")) is True
    assert router.handle_update(update("/help")) is True
    assert len(session.calls) == 2
    assert all("123456:" not in kwargs["json"]["text"] for _url, kwargs in session.calls)


def test_terminal_notifications_are_opt_in_and_deduplicated():
    token = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh"
    config = config_from_env({
        "VIRALCUTTER_TELEGRAM_ENABLED": "1",
        "VIRALCUTTER_TELEGRAM_BOT_TOKEN": token,
        "VIRALCUTTER_TELEGRAM_CHAT_IDS": "10",
        "VIRALCUTTER_TELEGRAM_NOTIFY_TERMINAL": "1",
    })
    session = FakeSession()
    api = TelegramAPI(token, session=session, base_url="http://test")
    queue = FakeQueue()
    queue.jobs["done"] = {"status": "succeeded", "finished": 123.0}
    router = TelegramCommandRouter(api, ["10"], {})
    service = TelegramControlService(config, router, api=api, queue=queue)
    service._seed_terminal_notifications()
    service._notify_terminal_jobs()
    assert session.calls == []
    queue.jobs["new"] = {"status": "failed", "finished": 456.0}
    service._notify_terminal_jobs()
    service._notify_terminal_jobs()
    assert len(session.calls) == 1
    assert "new" in session.calls[0][1]["json"]["text"]
    assert token not in session.calls[0][1]["json"]["text"]


def test_queue_handlers_pause_resume_retry_and_confirm_cancel():
    queue = FakeQueue()
    handlers = build_queue_handlers(queue)
    context = type("Context", (), {"chat_id": "10"})()
    assert "queued=0" in handlers["status"](context, "")
    assert "متوقف" in handlers["pause"](context, "")
    assert queue.paused
    assert "استئناف" in handlers["resume"](context, "")
    assert not queue.paused
    assert "1" in handlers["retry_failed"](context, "")
    assert "تأكيد" in handlers["cancel_all"](context, "")
    assert "1" in handlers["confirm_cancel_all"](context, "")
    assert queue.did_cancel == ["abc"]


def test_projects_and_audit_are_local_and_path_safe(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "polish_report.json").write_text(json.dumps({
        "summary": {"enhanced": 2, "partial": 1, "fallback": 0, "failed": 0},
    }), encoding="utf-8")
    (project / "tracking_report.json").write_text(json.dumps({
        "backend": "insightface", "active_speaker_applied": True,
    }), encoding="utf-8")
    (project / "publish_batch_report.json").write_text(json.dumps({
        "summary": {"uploaded": 2, "scheduled": 1, "failed": 0, "blocked": 0},
    }), encoding="utf-8")
    handlers = build_queue_handlers(FakeQueue(), project_root=str(tmp_path))
    context = type("Context", (), {"chat_id": "10"})()
    assert "demo" in handlers["projects"](context, "")
    report = handlers["audit"](context, "demo")
    assert "insightface" in report
    assert "uploaded=2" in report
    assert "غير صحيح" in handlers["audit"](context, "../demo")


def test_cancel_all_confirmation_expires_and_is_chat_specific(monkeypatch):
    queue = FakeQueue()
    handlers = build_queue_handlers(queue)
    context_a = type("Context", (), {"chat_id": "10"})()
    context_b = type("Context", (), {"chat_id": "11"})()
    clock = {"value": 100.0}
    monkeypatch.setattr("webui.telegram_control.time.monotonic", lambda: clock["value"])
    handlers["cancel_all"](context_a, "")
    assert "لا يوجد طلب" in handlers["confirm_cancel_all"](context_b, "")
    clock["value"] = 161.0
    assert "انتهت" in handlers["confirm_cancel_all"](context_a, "")
    assert queue.did_cancel == []


def test_single_cancel_rejects_unknown_and_cancel_all_argument():
    queue = FakeQueue()
    handlers = build_queue_handlers(queue)
    context = type("Context", (), {"chat_id": "10"})()
    assert "غير موجودة" in handlers["cancel"](context, "missing")
    assert "الإلغاء الجماعي" in handlers["cancel"](context, "all")
    assert "cancelling" in handlers["cancel"](context, "abc")


def test_service_is_disabled_without_explicit_configuration(monkeypatch):
    for key in (
        "VIRALCUTTER_TELEGRAM_ENABLED",
        "VIRALCUTTER_TELEGRAM_BOT_TOKEN",
        "VIRALCUTTER_TELEGRAM_CHAT_IDS",
    ):
        monkeypatch.delenv(key, raising=False)
    assert start_from_environment(FakeQueue()) is None
