# -*- coding: utf-8 -*-
"""Reliable, resumable pipeline orchestration for ViralCutter.

This module is intentionally dependency-free. It provides stage registration,
dependency ordering, retry policy, cancellation, atomic state persistence and
output validation hooks. Existing legacy checkpoint.json remains compatible.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

STATES = {"pending", "running", "retrying", "success", "failed", "cancelled"}

@dataclass
class Stage:
    name: str
    deps: List[str]
    action: Callable[..., Any]
    validator: Optional[Callable[[Any], bool]] = None
    retries: int = 0

class CancellationToken:
    def __init__(self):
        self._cancelled = False
    def cancel(self):
        self._cancelled = True
    @property
    def cancelled(self):
        return self._cancelled
    def raise_if_cancelled(self):
        if self._cancelled:
            raise RuntimeError("pipeline cancelled")

def fingerprint_files(paths):
    h = hashlib.sha256()
    for p in sorted(str(x) for x in paths):
        h.update(p.encode())
        if os.path.isfile(p):
            st = os.stat(p)
            h.update(str(st.st_size).encode())
            h.update(str(st.st_mtime_ns).encode())
    return h.hexdigest()

class PipelineEngine:
    def __init__(self, state_path, token=None, sleep=time.sleep):
        self.state_path = str(state_path)
        self.stages: Dict[str, Stage] = {}
        self.token = token or CancellationToken()
        self.sleep = sleep
        self.state = self._load()

    def _load(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {"stages": {}, "updated": None}
        except Exception:
            return {"stages": {}, "updated": None}

    def register(self, name, action, deps=None, validator=None, retries=0):
        if name in self.stages:
            raise ValueError("duplicate stage: " + name)
        self.stages[name] = Stage(name, list(deps or []), action, validator, max(0, int(retries)))

    def _atomic_save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.state_path)), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        finally:
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except OSError: pass

    def order(self):
        visiting, visited, out = set(), set(), []
        def visit(n):
            if n in visiting: raise ValueError("dependency cycle at " + n)
            if n in visited: return
            if n not in self.stages: raise KeyError("unknown dependency: " + n)
            visiting.add(n)
            for d in self.stages[n].deps: visit(d)
            visiting.remove(n)
            visited.add(n)
            out.append(n)
        for n in self.stages: visit(n)
        return out

    def invalidate_downstream(self, changed):
        changed = set(changed)
        for n in self.order():
            if any(d in changed for d in self.stages[n].deps):
                self.state.setdefault("stages", {}).pop(n, None)
                changed.add(n)
        self.state["updated"] = time.time()
        self._atomic_save()

    def run(self, targets=None, context=None):
        context = context or {}
        names = self.order()
        if targets:
            needed = set()
            def add(n):
                needed.add(n)
                for d in self.stages[n].deps: add(d)
            for n in targets: add(n)
            names = [n for n in names if n in needed]
        results = {}
        for name in names:
            self.token.raise_if_cancelled()
            stage = self.stages[name]
            rec = self.state.setdefault("stages", {}).setdefault(name, {})
            if rec.get("status") == "success":
                results[name] = rec.get("result")
                continue
            # Dependencies must have succeeded in this run or persisted state.
            for dep in stage.deps:
                if self.state["stages"].get(dep, {}).get("status") != "success":
                    raise RuntimeError(f"dependency not successful: {dep} -> {name}")
            for attempt in range(stage.retries + 1):
                self.token.raise_if_cancelled()
                rec.update({"status": "running" if attempt == 0 else "retrying",
                            "attempt": attempt + 1, "started": time.time()})
                self.state["updated"] = time.time()
                self._atomic_save()
                try:
                    result = stage.action(context, results)
                    if stage.validator and not stage.validator(result):
                        raise ValueError(f"output validation failed: {name}")
                    rec.update({"status": "success", "finished": time.time(), "result": result})
                    self.state["updated"] = time.time()
                    self._atomic_save()
                    results[name] = result
                    break
                except Exception as exc:
                    rec.update({"error_type": type(exc).__name__, "error": str(exc)})
                    if attempt < stage.retries:
                        self.sleep(min(30, 2 ** attempt))
                    else:
                        rec.update({"status": "failed", "finished": time.time()})
                        self.state["updated"] = time.time()
                        self._atomic_save()
                        raise
        return results
