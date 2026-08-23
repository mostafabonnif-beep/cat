---
name: Bug report
about: Something is broken — help us fix it
title: "[Bug] "
labels: bug
assignees: ''
---

**Describe the bug**
A clear and concise description.

**Steps to reproduce**
1. Command used / WebUI steps: `python main_improved.py --url ...`
2. Expected vs actual behavior

**Environment**
- OS: Windows 11 / Linux / macOS
- ViralCutter version: (`python -c "from app_version import VERSION; print(VERSION)"` or the release tag)
- Source run or .exe?
- GPU: NVIDIA / AMD / CPU only

**Logs**
- Paste the relevant console output, or the `crash_report.log` / project logs.
- If a project was involved, mention which stage failed (download /
  transcribe / cut / edit / subtitles / scorecard / upload).

**Did the pre-flight pass?**
Run `python -m scripts.preflight --check` and paste the result — it tells us
if the environment itself is the problem.
