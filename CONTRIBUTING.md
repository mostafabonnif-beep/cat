# Contributing to ViralCutter

Thanks for helping make the open-source Opus Clip alternative better! 🚀

## Before you start

- Read [`docs/DEVELOPER_HANDOVER.md`](docs/DEVELOPER_HANDOVER.md) — it explains
  what was built, how the safety pipeline works, and the gotchas.
- Read [`docs/REMAINING_AFTER_V6_9.md`](docs/REMAINING_AFTER_V6_9.md) — the
  living list of what's done and what's left.

## Hard rules (the CI will enforce these)

1. **i18n parity** — every new English UI string must be added to all 4
   locales (`i18n/locale/en_US.json`, `ar_SA.json`, `pt_BR.json`, `tr_TR.json`,
   indent=4) or `tests/test_i18n_completeness.py` fails.
2. **Version rule** — `app_version.py` must match the latest `changelog.md`
   entry, and every release needs a tag with the same number (the auto-updater
   compares tags).
3. **Lint** — `ruff check .` must pass (config in `pyproject.toml`).
4. **Tests** — the full suite must stay green: `python -m pytest tests/`.
5. **Never touch the face-crop loop in `scripts/edit_video.py`** for aspect
   changes — that path was the root cause of the v6.6 A/V-sync fix. New
   framing formats go through `scripts/reframe.py` (post-stage).
6. **Safety is sacred** — this tool exists so channels don't get strikes.
   Never weaken the blocklist/censor/scorecard defaults without discussion.

## Development loop

```bash
uv sync            # reproducible dev env (or install_dependencies.bat)
ruff check .       # lint
python -m pytest tests/   # full suite
python -m scripts.preflight --check   # environment sanity
```

New features need tests (we're at 500+). Test in a hermetic way — the SDK
tests must pass both with and without the full dependency stack installed.

## Submitting

- Small, focused PRs.
- Update `changelog.md` (top entry) + `docs/REMAINING_AFTER_V6_9.md` table
  when you complete a roadmap item.
- `.github/workflows/*` changes can only be merged by the repo owner (the
  agent app lacks Workflows permission) — note it in the PR.
