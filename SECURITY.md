# Security Policy

ViralCutter processes your videos **100% locally** — your data never leaves
your machine unless you choose to upload a clip yourself.

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Report privately:

- GitHub: use the repo's "Report a vulnerability" (Security tab), or
- Open a **private** issue, or contact the maintainers via the Discord
  community link in the README.

We'll acknowledge within 5 business days and work on a fix before disclosure.

## Scope

- The YouTube-strike safety layer (`scripts/safety_filter.py`,
  `scripts/censor_engine.py`, `scripts/risk_scorecard.py`,
  `scripts/safety_updater.py`) — weaknesses that could let violating content
  through are top priority.
- Credential handling (`api_config.json`, `secure_config.py`, OAuth tokens in
  `~/.viralcutter/`).
- The pre-flight checker (`scripts/preflight.py`) — anything that could make
  it silently install the wrong thing.

## Out of scope

- The AI providers' own services (Gemini/OpenAI) and their rate limits.
- Known platform API limitations (e.g. Instagram Reels requiring a public
  video URL — documented in `requirements-upload.txt`).
