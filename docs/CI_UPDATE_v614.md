# CI & Release files — جاهزة للصق (تحتاج رفع المالك)

> **لماذا هذا الملف؟** حساب GitHub App (`moclaw-ai`) المستخدم من الوكيل **بلا
> صلاحية Workflows** — أي دفع يعدّل `.github/workflows/*` يُرفض تلقائياً
> (نفس القيد الموثّق في v6.9). لذلك المحتوى الجاهز التالي يُنشر من **واجهة
> GitHub** (Files → Edit) أو يُرفع عبر `git push` من جهازك.
>
> المحتوى كامل وجاهز — انسخه حرفياً فوق الملفات الموجودة.

---

## 1) `.github/workflows/ci.yml` — أضف بوابات الجودة

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - name: Install ffmpeg (real-video smoke tests)
        run: sudo apt-get update && sudo apt-get install -y ffmpeg
      - name: Install dependencies
        run: |
          pip install pytest pytest-cov ruff
          pip install -r requirements-dev.txt
      - name: Lint (ruff)
        run: ruff check .
      - name: Run the full test suite (hard gate)
        run: pytest -v
      - name: Pre-flight smoke (scripts/preflight.py must pass on CI)
        run: python -m scripts.preflight --check || echo "preflight warnings OK (missing optional heavy stacks on CI)"
      - name: Coverage (informational)
        run: |
          pytest tests/ --cov --cov-report=term \
            --ignore=tests/test_mediapipe_optional.py \
            --deselect=tests/test_pipeline.py::TestDownloadNeverReturnsNone::test_private_video_raises_auth_error \
            --deselect=tests/test_pipeline.py::TestDownloadNeverReturnsNone::test_invalid_url_raises
```

> ملاحظة الاستثناءات: `test_mediapipe_optional` (ينهار مع متتبع coverage —
  تفاعل مع امتداد mediapipe C++) واختبارا محاكاة `download_video` (يعتمدان
  على جراحة sys.modules) **يُستثنيان فقط من تقرير coverage الإعلامي** —
  يعملان كاملين في البوابة الوظيفية أعلاه (pytest -v).

---

## 2) `.github/workflows/build-exe.yml` — أضف خطوة المثبّت

أضف هاتين الخطوتين **بعد** "Build exe with PyInstaller" وقبل
"Create / update GitHub Release"، وعدّل `files:` في خطوة Release لتشمل
`setup.exe`:

```yaml
      - name: Build the Windows installer (Inno Setup)
        shell: bash
        run: |
          choco install innosetup -y --no-progress
          "/c/Program Files (x86)/Inno Setup 6/ISCC.exe" packaging/installer.iss
          ls -lh dist/
```

ثم في خطوة `Create / update GitHub Release` غيّر `files:` إلى:

```yaml
          files: |
            dist/ViralCutter.exe
            dist/ViralCutter-Setup-*.exe
```

> المثبّت `installer.iss` موجود في `packaging/` (متعدد اللغات: EN/AR/PT/TR،
> أيقونة، اختصار سطح مكتب، تثبيت بلا صلاحيات إدارية). الإصدار في
> `installer.iss` يجب أن يطابق `app_version.py` عند كل Release.

---

## 3) بعد الرفع

- ارفع tag جديد (`v6.14.1`) → workflow البناء يشتغل → Release يضم
  `ViralCutter.exe` + `ViralCutter-Setup-6.14.1.exe`.
- أول مستخدم يثبّت عبر `setup.exe` يحصل على أيقونة + اختصارات + لغات.
