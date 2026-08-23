# -*- coding: utf-8 -*-
"""Persistent background render queue for ViralCutter.

The queue is deliberately dependency-free so it can be used by the Gradio
WebUI, CLI helpers, and tests. State is written atomically after every
transition. A process restart re-queues jobs that were running when the
process stopped; completed jobs remain available for the project library.
"""
from __future__ import annotations

import json
import os
import queue as queue_module
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, Optional

TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
ACTIVE_STATES = {"queued", "running", "retrying", "cancelling"}


@dataclass
class RenderJob:
    id: str
    plan: dict
    status: str = "queued"
    created: float = 0.0
    started: float | None = None
    finished: float | None = None
    output: str | None = None
    error: str | None = None
    attempts: int = 0
    max_retries: int = 0
    priority: int = 0
    progress: int = 0
    message: str = ""
    cancel_requested: bool = False
    metadata: dict = field(default_factory=dict)


class RenderQueue:
    """A crash-safe queue with an optional in-process worker pool.

    A runner passed to :meth:`start` must accept
    ``runner(job, cancel_event, progress_callback)`` and may return an output
    path or any JSON-serializable result. The callback accepts
    ``progress(percent, message)``. Existing callers that only use ``add``,
    ``cancel`` and ``pending`` remain compatible.
    """

    def __init__(self, state_path, runner: Optional[Callable] = None, max_workers=1):
        self.state_path = str(state_path)
        self.jobs: Dict[str, RenderJob] = {}
        self.runner = runner
        self.max_workers = max(1, int(max_workers or 1))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._work = queue_module.PriorityQueue()
        self._sequence = 0
        self._queued_ids = set()
        self._paused = False
        self._threads = []
        self._shutdown = threading.Event()
        self._events: Dict[str, threading.Event] = {}
        self.load_warning = ""
        self._load()

    # ------------------------------------------------------------------
    # Durable state
    # ------------------------------------------------------------------
    def _load(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            records = raw.get("jobs", {}) if isinstance(raw, dict) else {}
            self._paused = bool(raw.get("paused", False)) if isinstance(raw, dict) else False
            loaded = {}
            valid_fields = set(RenderJob.__dataclass_fields__)
            for job_id, data in records.items():
                if not isinstance(data, dict):
                    continue
                values = {k: v for k, v in data.items() if k in valid_fields}
                values.setdefault("id", str(job_id))
                values.setdefault("plan", {})
                try:
                    loaded[str(job_id)] = RenderJob(**values)
                except (TypeError, ValueError):
                    continue
            self.jobs = loaded
            # A process cannot still be running after a crash. Requeue it so a
            # future worker can continue instead of silently losing the job.
            changed = False
            for job in self.jobs.values():
                if job.status in {"running", "cancelling"}:
                    job.status = "queued"
                    job.cancel_requested = False
                    job.message = "Recovered after previous process stopped"
                    changed = True
            if changed:
                self._save()
        except (OSError, ValueError, TypeError) as exc:
            self.jobs = {}
            self.load_warning = "تعذر قراءة ملف حالة الطابور: {}".format(str(exc)[:500])
            if os.path.isfile(self.state_path):
                corrupt_path = "{}.corrupt-{}".format(self.state_path, int(time.time()))
                try:
                    os.replace(self.state_path, corrupt_path)
                    self.load_warning += "؛ حُفظت النسخة التالفة في {}".format(corrupt_path)
                except OSError:
                    pass

    def _save(self):
        directory = os.path.dirname(os.path.abspath(self.state_path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": 2, "updated": time.time(),
                     "paused": self._paused,
                     "jobs": {k: asdict(v) for k, v in self.jobs.items()}},
                    f, ensure_ascii=False, indent=2, default=str,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _notify(self):
        self._condition.notify_all()

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def _enqueue(self, job_id):
        job_id = str(job_id)
        if job_id in self._queued_ids:
            return False
        self._sequence += 1
        job = self.jobs.get(job_id)
        priority = int(job.priority if job else 0)
        self._queued_ids.add(job_id)
        self._work.put((-priority, self._sequence, job_id))
        return True

    def add(self, plan, max_retries=0, job_id=None, metadata=None, priority=0):
        with self._lock:
            jid = str(job_id or uuid.uuid4().hex)
            if jid in self.jobs:
                raise ValueError("duplicate render job: " + jid)
            self.jobs[jid] = RenderJob(
                id=jid,
                plan=dict(plan or {}),
                created=time.time(),
                max_retries=max(0, int(max_retries or 0)),
                priority=int(priority or 0),
                metadata=dict(metadata or {}),
            )
            self._save()
            self._notify()
            if self._threads and self.runner and not self._paused:
                self._enqueue(jid)
            return jid

    def get(self, job_id):
        with self._lock:
            return self.jobs.get(str(job_id))

    def snapshot(self, job_id=None):
        with self._lock:
            if job_id is not None:
                job = self.jobs.get(str(job_id))
                return asdict(job) if job else None
            return {jid: asdict(job) for jid, job in self.jobs.items()}

    def mark_started(self, job_id):
        with self._lock:
            job = self.jobs[str(job_id)]
            if job.status in TERMINAL_STATES:
                return job.status
            job.status = "running"
            job.started = job.started or time.time()
            job.attempts = max(1, int(job.attempts or 0))
            job.message = job.message or "Processing"
            self._save()
            self._notify()
            return job.status

    def update_progress(self, job_id, percent, message=""):
        with self._lock:
            job = self.jobs[str(job_id)]
            if job.status in TERMINAL_STATES:
                return job.status
            job.progress = max(0, min(100, int(float(percent))))
            if message:
                job.message = str(message)[:500]
            self._save()
            self._notify()
            return job.status

    def update_metadata(self, job_id, **changes):
        with self._lock:
            job = self.jobs[str(job_id)]
            job.metadata.update(changes)
            self._save()
            self._notify()
            return dict(job.metadata)

    def mark_finished(self, job_id, output=None, result=None):
        with self._lock:
            job = self.jobs[str(job_id)]
            if job.cancel_requested or job.status == "cancelled":
                job.status = "cancelled"
                job.finished = job.finished or time.time()
            else:
                job.status = "succeeded"
                job.progress = 100
                job.output = output if output is not None else result
                job.finished = time.time()
                job.message = "Completed"
            self._save()
            self._notify()
            return job.status

    def mark_failed(self, job_id, error):
        with self._lock:
            job = self.jobs[str(job_id)]
            if job.cancel_requested or job.status == "cancelled":
                job.status = "cancelled"
                job.message = "Cancelled"
            else:
                job.status = "failed"
                job.error = str(error)[:4000]
                job.finished = time.time()
                job.message = "Failed"
            self._save()
            self._notify()
            return job.status

    def cancel(self, job_id):
        with self._lock:
            job = self.jobs[str(job_id)]
            if job.status == "queued":
                job.status = "cancelled"
                job.cancel_requested = True
                job.finished = time.time()
                job.message = "Cancelled before start"
            elif job.status in {"running", "retrying"}:
                job.cancel_requested = True
                job.status = "cancelling"
                job.message = "Cancellation requested"
                self._events.setdefault(str(job_id), threading.Event()).set()
            self._save()
            self._notify()
            return job.status

    def retry(self, job_id):
        with self._lock:
            job = self.jobs[str(job_id)]
            if job.status not in {"failed", "cancelled"}:
                raise ValueError("only failed or cancelled jobs can be retried")
            job.status = "queued"
            job.started = None
            job.finished = None
            job.output = None
            job.error = None
            job.progress = 0
            job.cancel_requested = False
            job.message = "Queued for retry"
            self._save()
            self._notify()
            if self._threads and self.runner and not self._paused:
                self._enqueue(job.id)
            return job.id

    @property
    def state_warning(self):
        """Return a non-secret warning captured while loading durable state."""
        with self._lock:
            return self.load_warning

    @property
    def paused(self):
        with self._lock:
            return self._paused

    def pause_all(self):
        """Pause starting queued jobs; a currently running runner remains cooperative."""
        with self._lock:
            self._paused = True
            self._save()
            self._notify()
            return True

    def resume_all(self):
        """Resume queued jobs after a durable pause."""
        with self._lock:
            self._paused = False
            if self._threads and self.runner:
                for job in self.pending():
                    self._enqueue(job.id)
            self._save()
            self._notify()
            return True

    def retry_failed(self, job_ids=None):
        """Requeue failed/cancelled jobs and return the IDs actually reset."""
        with self._lock:
            ids = list(job_ids) if job_ids is not None else list(self.jobs)
            retried = []
            for job_id in ids:
                job = self.jobs.get(str(job_id))
                if job and job.status in {"failed", "cancelled"}:
                    self.retry(job.id)
                    retried.append(job.id)
            return retried

    def pending(self):
        with self._lock:
            return [job for job in self.jobs.values() if job.status == "queued"]

    def active(self):
        with self._lock:
            return [job for job in self.jobs.values() if job.status in ACTIVE_STATES]

    def prune(self, max_age_days=14, keep_terminal=200):
        """Remove old terminal jobs while keeping a bounded audit trail."""
        cutoff = time.time() - max(1, int(max_age_days or 1)) * 86400
        with self._lock:
            terminal = sorted(
                (job for job in self.jobs.values() if job.status in TERMINAL_STATES),
                key=lambda job: job.finished or job.created or 0,
                reverse=True,
            )
            keep_ids = {job.id for job in terminal[:max(0, int(keep_terminal or 0))]}
            removed = 0
            for job in list(terminal):
                stamp = job.finished or job.created or 0
                if job.id not in keep_ids and stamp < cutoff:
                    self.jobs.pop(job.id, None)
                    self._events.pop(job.id, None)
                    removed += 1
            if removed:
                self._save()
                self._notify()
            return removed

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------
    def start(self, runner=None, max_workers=None):
        with self._lock:
            if runner is not None:
                self.runner = runner
            if self.runner is None:
                raise ValueError("a runner callable is required")
            if self._threads:
                return
            if max_workers is not None:
                self.max_workers = max(1, int(max_workers))
            self._shutdown.clear()
            self._threads = [
                threading.Thread(target=self._worker, name="viralcutter-render-%d" % i, daemon=True)
                for i in range(self.max_workers)
            ]
            for thread in self._threads:
                thread.start()
            if not self._paused:
                for job in self.pending():
                    self._enqueue(job.id)

    def stop(self, wait=True, timeout=5):
        self._shutdown.set()
        with self._condition:
            self._condition.notify_all()
        for _ in self._threads:
            self._sequence += 1
            self._work.put((10**9, self._sequence, None))
        if wait:
            deadline = time.time() + max(0, float(timeout or 0))
            for thread in self._threads:
                remaining = max(0, deadline - time.time())
                thread.join(remaining)
        with self._lock:
            self._threads = []

    def wait(self, job_id, timeout=None):
        deadline = None if timeout is None else time.time() + float(timeout)
        with self._condition:
            while True:
                job = self.jobs.get(str(job_id))
                if job is None:
                    return None
                if job.status in TERMINAL_STATES:
                    return job.status
                if deadline is None:
                    self._condition.wait(0.25)
                else:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return job.status
                    self._condition.wait(min(0.25, remaining))

    def _worker(self):
        while not self._shutdown.is_set():
            try:
                _priority, _sequence, job_id = self._work.get(timeout=0.25)
            except queue_module.Empty:
                continue
            try:
                if job_id is None:
                    return
                with self._condition:
                    while self._paused and not self._shutdown.is_set():
                        self._condition.wait(0.25)
                    if self._shutdown.is_set():
                        return
                job = self.get(job_id)
                if job is None or job.status not in {"queued", "retrying"}:
                    with self._lock:
                        self._queued_ids.discard(str(job_id))
                    continue
                with self._lock:
                    self._queued_ids.discard(str(job_id))
                    job = self.jobs[job_id]
                    job.attempts += 1
                    job.started = job.started or time.time()
                    job.status = "running"
                    job.message = "Processing"
                    job.cancel_requested = False
                    self._events[job_id] = threading.Event()
                    cancel_event = self._events[job_id]
                    self._save()
                    self._notify()

                def progress(percent, message="", _job_id=job_id):
                    self.update_progress(_job_id, percent, message)

                try:
                    result = self.runner(job, cancel_event, progress)
                    if cancel_event.is_set() or self.get(job_id).cancel_requested:
                        self.cancel(job_id)
                        self.mark_failed(job_id, "cancelled")
                    else:
                        output = result if isinstance(result, str) else None
                        self.mark_finished(job_id, output=output, result=result)
                except Exception as exc:
                    with self._lock:
                        current = self.jobs[job_id]
                        can_retry = (not current.cancel_requested and
                                     current.attempts <= current.max_retries)
                        if can_retry:
                            current.status = "retrying"
                            current.error = str(exc)[:4000]
                            current.message = "Retrying"
                            self._save()
                            self._notify()
                            if not self._paused:
                                self._enqueue(job_id)
                        else:
                            self.mark_failed(job_id, "{}\n{}".format(
                                exc, traceback.format_exc(limit=6)))
            finally:
                self._work.task_done()
