<!-- Thanks for contributing! Please keep the CI green: it enforces all of the below. -->

## What does this PR change?

<!-- Short summary — what + why. Link the issue if one exists. -->

## Checklist (the CI will enforce these)

- [ ] **i18n parity** — every new UI string was added to all 4 locales (`i18n/locale/*.json`), and no locale has keys beyond `en_US` (`tests/test_i18n_completeness.py`)
- [ ] **Version rule** — if this is a release: `app_version.py` matches the latest `changelog.md` entry
- [ ] **Lint** — `ruff check .` passes
- [ ] **Tests** — `python -m pytest tests/` is green, and new logic has tests
- [ ] **Safety untouched** — I did not modify the face-crop loop in `scripts/edit_video.py` (framing changes go through `scripts/reframe.py`)
- [ ] **No secrets** — no API keys / tokens / personal paths in the diff

## Screenshots (WebUI changes)

<!-- Before/after if the UI changed. -->
