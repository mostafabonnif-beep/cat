# Development Notes

## Done
- ✅ Test suite: 73 unit tests (`tests/`) covering i18n, subtitle helpers, JSON cutting, saving, and WebUI utils. Run with `pytest` (install `requirements-dev.txt`).
- ✅ i18n overhaul: `ar_SA.json` fully covers every UI/CLI string; `en_US.json` cleaned (had 71 Arabic values); `pt_BR`/`tr_TR` completed with English fallback. Coverage guarded by `tests/test_i18n_completeness.py`.
- ✅ Locale loading no longer depends on the current working directory.
- ✅ Language is configurable: `VIRALCUTTER_LANG` env var (default `ar_SA`).
- ✅ `webui/app.py`: duplicate `AR_LABELS` dict removed; pure helpers extracted to `webui/utils.py`; RTL layout + Arabic font stack added.

## Remaining production work
- The heavy transcription and AI stages still depend on the user's installed WhisperX/Torch/Gemini stack; the test suite uses deterministic mocks for those external services.
- YouTube OAuth v3 publishing is available through the Publish tab and CLI; TikTok/Instagram remain separate integrations. Upload is still opt-in and defaults to dry-run/private for safety.
- Dependency lock files and platform-specific installers should be generated on the target deployment machines after selecting the desired GPU/Torch build.

---

## v6 — جولة التطوير الكبرى (2026-08-04)

كل ما نُفّذ موثّق بالتفصيل في `docs/ROADMAP_REPORT.md` (القسم السادس). الملخص:

- **التوزيع**: `packaging/viralcutter.spec` (PyInstaller onefile) + سكربتات بناء
  (`build_windows.bat`/`build_linux.sh`/`build_macos.sh`) + `install_linux.sh`/
  `install_macos.sh`/`run.sh` + تحديث تلقائي (`scripts/auto_updater.py`، نسخة من `app_version.py`).
- **الحماية**: فحص بصري ONNX حقيقي (`scripts/visual_check.py` — مدمج في `risk_scorecard`)،
  بوابة رفض إجبارية (`scripts/upload_gate.py` — SDKs المنصات جاهزة للربط)،
  فحص كابشن/عنوان (`scripts/metadata_compliance.py`).
- **المونتاج**: `scripts/polish.py` يشغّل السلسلة (jump cuts → punch zoom → موسيقى مع Auto-Duck
  → ووترمارك + intro/outro) على `final/` → `final_polished/` مع إعادة توقيت الترجمة.
  فعّل عبر `--polish on`.
- **الموثوقية**: `scripts/checkpoint.py` (استئناف ذكي)، `scripts/oom_guard.py` (تراجع نموذج عند OOM)،
  `scripts/secure_config.py` (مفتاح API مشفر/env)، `scripts/crash_report.py` (تقارير خصوصية).
- **CI**: `.github/workflows/ci.yml` يثبّت ffmpeg و `tests/test_ci_smoke.py` يختبر pipeline حقيقياً.
- **الاختبارات**: 196 → **286**.

### أعلام CLI جديدة في `main_improved.py`
`--polish on` (+ `--polish-stages/--music/--music-volume/--logo/--intro/--outro/--zoom-keywords`)،
`--checkpoint on|off` (افتراضي on)، `--check-updates`، `--metadata-gate warn|block|off` (افتراضي warn)،
`--auto-download-visual`.

### بقي للجولة القادمة (موثّق كـ TODO في الكود)
ربط OAuth الفعلي لـ TikTok/Instagram، بصمة الموسيقى (2.3)، حلقة تغذية الضربات (5.1)،
قوالب المنصات (5.2)، وتحليلات الأداء (5.4). YouTube OAuth v3 موثق ومتاح في Publish وCLI.

## YouTube API v3 publishing — 2026-08-12

The Publish tab now accepts a Google OAuth client-secrets JSON file, validates it, and copies it into `~/.viralcutter/youtube/` with restrictive permissions. OAuth refresh tokens are stored atomically as `token.json` with mode `600` where supported. The normal scope is `https://www.googleapis.com/auth/youtube.upload`, which is sufficient for uploading and scheduling videos; the advanced **Full YouTube OAuth access** checkbox/`--full-youtube-access` option requests `https://www.googleapis.com/auth/youtube` only when explicitly selected.

A YouTube upload passes through the existing safety gate before any API call. The title is trimmed to YouTube's limit, the description is combined with normalized hashtags, and the privacy selector supports `private`, `unlisted`, and `public`. Scheduling requires a future ISO-8601 timestamp with an explicit timezone and always forces `privacyStatus=private`, as required by the YouTube Data API. Completed uploads and failures are appended to the project publish history without storing client secrets or refresh tokens.

### WebUI workflow

1. Open **Publish**, select a rendered clip, and enter the title, description, and comma-separated hashtags.
2. Select the YouTube platform, attach the OAuth client-secrets JSON exported from Google Cloud, and click **Validate OAuth file**.
3. Keep **Dry run** enabled for a rehearsal. For a real upload, choose `private`, `unlisted`, or `public`; for a scheduled release, enter a future value such as `2026-08-15T18:30:00+01:00` and keep privacy `private`.
4. Click **Upload / Schedule**. The safety gate checks blocklists, safety reports, metadata compliance, music policy, and the rendered file before the API request.

### CLI workflow

```bash
python -m scripts.upload_gate --project VIRALS/my-project \\
  --upload youtube --video VIRALS/my-project/final/000_clip.mp4 \\
  --client-secrets /path/to/client_secrets.json \\
  --title "Short title" --caption "Description" \\
  --hashtags "shorts, youtube" --privacy private \\
  --publish-at 2026-08-15T18:30:00+01:00 --no-dry-run
```

Use `--auth youtube` to complete OAuth consent without uploading. Use `--full-youtube-access` only if the account workflow requires the broader YouTube scope. The first consent flow may open a local browser; no real upload happens while `--no-dry-run` is omitted.

### Safety and content policy

The upload path is not an automated guarantee that a video will avoid every copyright, Community Guidelines, or advertiser-suitability issue. ViralCutter's safety reports and metadata gate can block detected high-risk clips, including hate-related or otherwise disallowed material, but the creator remains responsible for reviewing the source, transcript, music rights, title, description, and final preview before publishing.

## Maintenance fixes — 2026-08-12

- ✅ WebUI imports now support both `python webui/app.py` and package mode (`python -m webui.app` / `import webui.app`).
- ✅ Added package initializers for `webui`, `scripts`, and `i18n` so editable and built installations discover all modules reliably.
- ✅ Added console commands from `pyproject.toml`: `viralcutter` and `viralcutter-webui`.
- ✅ Corrected the distribution version to valid PEP 440 syntax (`7.0.1+pro`) while preserving the UI version label `7.0.1-pro`.
- ✅ Hardened the optional MediaPipe test for Python 3.12 and environments where MediaPipe is installed.

### Verified commands

```bash
python -m compileall -q .
ruff check .
pytest -q  # full suite passes; run this command for the current collected count
python -m webui.app --help
viralcutter --help
viralcutter-webui --help
```


## v7 Pro — Production workflow upgrade (2026-08-12)

- ✅ **Project manifest and audit log**: each project now has `project_manifest.json` and `project_events.jsonl`, preserving source metadata, settings, status, outputs, and recent events. Legacy folders remain readable.
- ✅ **Crash-safe background queue**: `webui/render_queue.py` supports durable jobs, worker threads, progress callbacks, cancellation, retry limits, recovery of interrupted jobs, and retention pruning. Batch Queue stores plans without the API key and resumes them after a WebUI restart.
- ✅ **Safe project paths**: project selection and gallery paths are constrained to `VIRALS`, preventing path traversal through crafted dropdown values or API calls.
- ✅ **Transactional project editing**: segment selection and restoration use atomic JSON replacement; editor transforms validate bounds and roll back when invalid.
- ✅ **Output validation**: `scripts/media_validation.py` uses `ffprobe` to reject missing, empty, stream-less, too-short, or wrong-aspect outputs. It validates the newest render layer so intermediate horizontal cuts do not invalidate a final vertical export.
- ✅ **Reliable settings**: malformed configuration JSON, unknown backends, and invalid chunk sizes now recover to safe defaults; writes flush and `fsync` before replacement.
- ✅ **SDK compatibility**: the maintained `google-genai` SDK is preferred while `google-generativeai` remains a controlled fallback; both paths are covered by tests.
- ✅ **URL and queue hygiene**: Batch Queue accepts supported YouTube URL forms only, exposes clear invalid-input messages, and prunes old terminal jobs.
- ✅ **CLI parity**: CLI runs update the same project manifest lifecycle (`processing`, `completed`, `failed`) as WebUI runs.
- ✅ **Compatibility diagnostics**: Doctor now reports FFmpeg version and free disk space; i18n no longer relies on deprecated `locale.getdefaultlocale`.
- ✅ **Subtitle safety**: subtitle edits, segment selections, checkpoint files, settings, and platform config use atomic replacement where applicable.

### Production verification

```bash
ruff check .
python -m compileall -q .
pytest -q
python -m webui.app --help
python main_improved.py --help
```

The queue file is `VIRALS/.batch_queue.json`; it contains execution plans and status metadata, but the WebUI removes the API-key field before persisting a batch plan. API credentials continue to be resolved from the encrypted store or environment variables.


## v7 Pro — AI editing extensions — 2026-08-14

### Multi-face reframing

`--face-mode` يدعم الآن `auto` و`1` و`2` و`3` و`4` و`multi` و`grid`. وضعا `3` و`4` ينظمان الوجوه المرئية في شبكة عمودية ثابتة، بينما `multi/grid` يحتفظ بما يصل إلى أربعة متحدثين عند توفرهم. وضع `2` يحافظ على الترتيب القريب من المتحدثين السابقين، ومسار MediaPipe يستفيد من نفس التخطيط عند fallback. الواجهة تعرض هذه الخيارات في **Advanced Face Settings**.

### B-Roll

أضيف `scripts/broll_engine.py` لاستخراج كلمات مفتاحية عربية/إنجليزية من transcript، بناء خطة زمنية، البحث الاختياري في Pexels، تنزيل الأصل مع حد للحجم، وحفظ بيانات attribution. لا تحدث أي مكالمة شبكة دون `PEXELS_API_KEY` صريح؛ ويمكن استخدام ملف فيديو محلي عبر `--broll`. يتطلب استخدام Pexels إظهار رابط Pexels والاحتفاظ ببيانات المصور، لذلك يحفظ التقرير `provider` و`url` و`photographer` و`photographer_url`.

مثال:

```bash
PEXELS_API_KEY=... python main_improved.py --project-path VIRALS/demo \
  --polish on --polish-stages visual_hooks,broll,branding \
  --broll-query "technology business" --broll-opacity 0.28
```

يمكن كذلك تمرير `--broll /path/to/local.mp4` دون API. إذا لم يوجد أصل محلي أو مفتاح Pexels، تسجل المرحلة `no_asset_or_pexels_key` وتتابع دون فشل.

### Visual Hooks وAuto SFX

تكتشف `scripts/visual_hooks.py` كلمات hook والعاطفة وتضيف إطار accent ورفعًا طفيفًا للسطوع/التباين في النوافذ الزمنية، مع الحفاظ على punch-zoom كمرحلة مستقلة. وتستخدم `scripts/auto_sfx.py` ملفات `pop.wav` و`whoosh.wav` و`impact.wav` المحلية لمزامنة مؤثرات خفيفة مع الكلمات، دون تنزيل أصوات خارجية. ضع الأصول في مجلد واحد ثم استخدم `--sfx-dir` و`--sfx-volume`.

```bash
python main_improved.py --project-path VIRALS/demo --polish on \
  --polish-stages jump_cuts,punch_zoom,visual_hooks,auto_sfx,branding \
  --sfx-dir assets/sfx --sfx-volume 0.22
```

في WebUI توجد حقول B-Roll ومجلد Auto SFX ومستوى الصوت داخل إعدادات **Editing quality**. كل المراحل اختيارية، وتنسخ الفيديو الأصلي عند غياب الأصل أو فشل FFmpeg بدل فقدان الناتج.

### Dynamic Captions وAuto-Emoji وActive Speaker Switching

- **الترجمات الحركية (Dynamic Captions):** باستخدام `--caption-animation [pop|scale|pop_scale|bounce]` أو اختيارها من **Subtitle Editor**، تطبق الحزمة تأثيرات ASS حركية متوقتة على الكلمات الفردية لزيادة الاحتفاظ بالمشاهد (Retention).
- **الإيموجي التلقائي (Auto-Emoji):** باستخدام `--auto-emoji` أو تفعيل الخيار في محرر الترجمات، تُضاف رموز تعبيرية خفيفة متوافقة مع الكلمات العاطفية والمفتاحية دون الإخلال بمهنية النص.
- **تبديل المتحدث النشط (Active Speaker Switching):** باستخدام `--focus-active-speaker` مع InsightFace، تراقب خوارزمية الـ hysteresis وحركة الفم والصوت المتحدث النشط لتبديل مركز القص بدقة مستقرة دون تذبذب عشوائي بين الإطارات.
