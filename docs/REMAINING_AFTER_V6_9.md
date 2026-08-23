# 📋 تقرير "ما تبقى" — بعد جولتي v6.9 و v6.9.1

> **الغرض**: هذا الملف موجّه لأي مطوّر (إنسان أو ذكاء اصطناعي) يَلي العمل على
> المشروع — **ماذا أُنجز، وماذا تبقّى، وكيف يُنفَّذ كل بند**. لا تُعِد تنفيذ ما
> هو مُنجز، ولا تكتشف من جديد ما هو موثّق هنا.
>
> **التاريخ**: 2026-08-09 | **آخر إصدار**: v6.13.0 | **الاختبارات**: 515

---

## ✅ ما أُنجز في v6.13.0 (إكمال الخارطة: تعلم + أبعاد + تحليلات)

- **5.1 حلقة الضربات Strike Feedback**: `scripts/strike_feedback.py` —
  add/allow/remove/list/stats/export + `from-scorecard --project X [--apply]`
  يستخرج أنماط المقاطع المحجوبة من safety_report + risk_scorecard ويعلّمها
  للأداة (تكتب `safety_terms.json` التي يقرأها الفلتر تلقائياً). يوميات في
  `strike_feedback.json`. تكامل في `main_improved.py`: تلميح تعلّم بعد أي حجب +
  `--auto-learn-blocked`.
- **أبعاد إخراج إضافية**: `scripts/reframe.py` — `--output-aspect
  9:16|4:5|1:1|16:9` بعد الحرق وقبل بطاقة المخاطر (crop للـ 4:5/1:1، blur-pad
  للـ 16:9). استبدال ذري + نسخة .orig.mp4. `--platform yt_standard` → 16:9
  تلقائياً (كان يخرج 9:16 رغم قالب 16:9).
- **5.4 تحليلات الأداء**: `scripts/analytics.py` — YouTube Analytics API
  (قراءة فقط): summary/top/trends/export/check.
- **الاختبارات**: +34 → **515** خضراء.

---

## ✅ ما أُنجز في v6.12.0 (جولة الفحص المسبق الشامل)

- **`scripts/preflight.py`**: قبل أي تشغيل يفحص كل شيء (Python، ffmpeg/ffprobe،
  كل الاعتماديات من `requirements*.txt`، `api_config.json`، الأصول المرفقة:
  الخطوط/قائمة الأمان/الترجمات، مجلد `models/`، مساحة القرص والصلاحيات) و
  **يثبّت/يصلّح تلقائياً كل ما هو ناقص** — الحزم الأساسية عبر pip، إعادة
  إنشاء `api_config.json` من قالب (مع نسخة احتياطية)، إنشاء المجلدات، وخفض
  numpy إلى <2 عند المخالفة. stdlib خالصة.
- **أكواد خروج آليّة**: 0 جاهز / 1 حرج باقٍ / 2 تحذيرات فقط + `--json`.
- **مربوط في كل نقاط الدخول**: `--preflight auto|check|off` في
  `main_improved.py` (الافتراضي auto — يفحص ويصلّح قبل أي عمل وقبل إقلاع
  الويب)، `webui/app.py`، و`run.bat`/`run_webui.bat`/`run.sh` كأول خطوة.
- **الـ exe المجمّد**: يتحقق من الأدوات المدمجة بدل pip؛ الحزم الاختيارية
  الثقيلة (~2GB) لا تُثبَّت صامتة أبداً.
- **مخرج هروب**: `VIRALCUTTER_SKIP_PREFLIGHT=1` أو `--preflight off`.
- **الاختبارات**: +28 → **481** (خضراء على 3.10/3.11/3.12).

---

## ✅ ما أُنجز في v6.10.0 (جولة ربط المنصات + الموسيقى)

- **ربط TikTok كاملاً** (Roadmap 2.2): OAuth2 (authorization-code + callback
  محلي + refresh) ورفع حقيقي init → PUT upload → status polling عبر
  Content Posting API. `--auth tiktok`؛ خصوصية آمنة افتراضياً SELF_ONLY.
- **ربط Instagram كاملاً**: Reels بخطوتين (media → media_publish) بتوكن
  طويل الأمد + تبادل توكن قصير؛ يتطلب `video_url` عاماً (قيد Graph API موثّق).
- **بصمة الموسيقى Chromaprint** (Roadmap 2.3): `scripts/music_fingerprint.py`
  — pyacoustid/fpcalc + AcoustID + قاعدة محلية دون إنترنت + تقرير
  `music_fingerprint.json` + بوابة `--music-gate warn|block|off`.
- **WebUI**: تبويب "🚀 رفع ونشر" — تشغيل مباشر لكل مقطع + ترجمة ترجماته +
  رفع عبر بوابة الأمان مع dry-run، وزر فحص الموسيقى. `webui/publish_panel.py`.
- `main_improved.py`: `--music-check auto|on|off`، `--music-gate`،
  `--music-local-db`، `--acoustid-key`.
- **اختبارات**: +53 → **429** (كلها خضراء على 3.10/3.11/3.12).
- تفاصيل اختبار ويندوز/الـ exe: `docs/RELEASE_CHECKLIST_WINDOWS.md`.

---

---

## ✅ ما أُنجز في v6.9 (PR #1 — مدمج)

- **حفظ إعدادات Gemini تلقائياً** (`webui/settings_store.py`): المفتاح/المحرك/النموذج/حجم
  الجزء يُحمَّلون عند فتح الواجهة ويُحفظون تلقائياً عند كل تعديل — لا إعادة كتابة
  المفتاح بعد اليوم.
- **🔌 زر اختبار الاتصال** في إعدادات الذكاء الاصطناعي (SDK أو REST احتياطياً).
- **أخطاء Gemini تصبح صريحة**: `call_gemini` يرفع خطأً واضحاً عند مفتاح غير صالح
  (كان يُرجع `{}` بصمت → "no viral segments" مضلل).
- **تلميحات عربية جديدة** في `webui/utils.py` (مفتاح غير صالح، حصة منتهية، PERMISSION_DENIED...).
- **فحص مسبق** قبل التشغيل: مفتاح مفقود → رسالة فورية؛ مفتاح لا يبدأ بـ `AIza` → تحذير.
- **أمني**: `--api-key` يُقنَّع في سجل الواجهة (كان يتسرب في لقطات الشاشة).
- **`app_version.py`**: 0.9.0 → 6.9.0 (كان منحرفاً عن changelog فيكسر التحديث التلقائي).
- 17 مفتاح ترجمة × 4 لغات + 24 اختباراً جديداً.

## ✅ ما أُنجز في v6.9.1 (PR #2 — مدمج)

- **`mediapipe` أصبح اختيارياً**: كان `import mediapipe` مكشوفاً في
  `scripts/edit_video.py` و`one_face.py` و`two_face.py` → أي جهاز بلا mediapipe
  كان ينهار في منتصف المعالجة. الآن import محمي + انحدار تلقائي إلى OpenCV Haar.
- **خطوط Montserrat مضمّنة**: `fonts/` (Regular + Bold + ExtraBold + رخصة OFL)
  و`burn_subtitles.py` يمرّر `:fontsdir=` لـ ffmpeg — ترجمات "Hormozi" تظهر بخطها
  الصحيح حتى دون تثبيت الخط على النظام. أُضيفت أيضاً إلى `packaging/viralcutter.spec`.
- **CI**: أُضيفت خطوة تثبيت `ffmpeg` في `.github/workflows/ci.yml` (اختبارات
  الفيديو الحقيقية كانت تُتخطى بصمت على CI). **⚠️ لم تُرفع**: GitHub يرفض دفع
  أي تعديل على `.github/workflows/*` لأن تطبيق `moclaw-ai` بلا صلاحية
  **Workflows**. المحتوى الجاهز للتطبيق في البند 1 أدناه.
- اختبارات: `tests/test_mediapipe_optional.py` (2) + `tests/test_fonts.py` (4).

## ✅ ما أُنجز في v6.9.2 (PR #3 — مدمج)

- **كل حقول الواجهة تُحفظ تلقائياً**: امتداد لميزة v6.9 — جودة الفيديو، الترجمة
  الهدف، ترجمات يوتيوب، فلتر الأمان + المراجعة AI، قالب المنصة، بوابة الميتاداتا،
  لغة العناوين، التلميع، الموسيقى، اللوجو، الكوكيز، نموذج Whisper، طريقة العمل.
  الملف `webui_settings.json` محلي وغير متتبَّع في git + كتابة ذرّية.
- **READMEs (ar/en/pt)**: الإصدار صُحّح 0.9.0 → 6.9.2 (كان منحرفاً).
- اختبارات: +4 → **377**.

---

## 🔴 أولويات حرجة — تمنع اكتمال "الحلقة"

### 1. تفعيل GitHub Actions + صلاحية Workflows — ✅ مكتمل (أخضر)
- المالك فعّل Actions وعدّل `ci.yml` (Commit `803ba18` خطوة ffmpeg ثم `2a2dca6`
  سطر `pip install -r requirements-dev.txt`).
- أول تشغيلة حقيقية كشفت نقص numpy في `test_visual_check` → أُصلح
  (`requirements-dev.txt`: `numpy<2`, Commit `923274a`).
- **النتيجة الآن**: CI أخضر على Python 3.10/3.11/3.12 (Run `31259166047`).
- ملاحظة للمستقبل: أي تعديل على `.github/workflows/*` من تطبيق `moclaw-ai`
  يُرفض (بلا صلاحية Workflows) — يرفعه المالك من واجهة الويب.

- **`ci.yml` النهائي الجاهز للصق:**
- **محتوى `ci.yml` الجاهز (مع خطوة ffmpeg):**
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
        - name: Install test dependencies
          run: pip install pytest
        - name: Run unit tests
          run: pytest -v
  ```
- إن رُفض رفع ملفات `ci.yml` مستقبلاً من التطبيق، فهذا السبب.

### 2. إنشاء أول GitHub Release — يكمل التحديث التلقائي
- الـ tag `v6.9.1` مرفوع. `scripts/auto_updater.py` يفحص Releases ثم يتراجع
  لأحدث tag — أول Release رسمي يفعل الحلقة كاملة.
- الخطوات:
  1. ابنِ ملف ويندوز: `packaging/build_windows.bat` (يتطلب PyInstaller +
     `pip install -r requirements.txt` على جهاز Windows).
  2. GitHub ← repo ← **Releases ← Create a new release**.
  3. Tag: `v6.9.1` | العنوان: `v6.9.1 — حفظ الإعدادات + خطوط مضمّنة + أخطاء أذكى`.
  4. ارفق `dist/ViralCutter.exe` + لقطتي شاشة للواجهة.
- ملاحظة: حتى Release بدون exe يفيد — سيرى المستخدمون وجود تحديث.

---

## 🟡 ميزات موثّقة في `docs/ROADMAP_REPORT.md` لم تُنفَّذ بعد

| # | البند | الحالة | أين |
|---|-------|--------|-----|
| 1 | **ربط OAuth TikTok/Instagram** | ✅ مكتمل في v6.10.0 (اختبار حي يحتاج بياناتك) | `scripts/upload_gate.py` |
| 2 | **بصمة الموسيقى Chromaprint (2.3)** | ✅ مكتمل في v6.10.0 | `scripts/music_fingerprint.py` |
| 3 | **حلقة الضربات Strike Feedback (5.1)** | ✅ مكتمل في v6.13.0 | `scripts/strike_feedback.py` |
| 4 | **تحليلات الأداء (5.4)** | ✅ مكتمل في v6.13.0 (يحتاج تفعيل Analytics API) | `scripts/analytics.py` |
| 5 | **استبدال حلقة OpenCV بـ ffmpeg pipe** | A/V desync جذري على فيديوهات معينة | `scripts/edit_video.py` |
| 6 | **أبعاد إخراج إضافية (غير 9:16)** | ✅ مكتمل في v6.13.0 (post-stage آمن: crop/pad) | `scripts/reframe.py` + `--output-aspect` |
| 7 | **WebUI: أزرار polish/gate لكل مشروع** | ✅ تبويب "🚀 رفع ونشر" في v6.10.0 (تشغيل/ترجمة/رفع/موسيقى) | `webui/app.py` + `webui/publish_panel.py` |

## 🟢 ملاحظات تشغيلية

- **التثبيت الكامل** يحتاج: `requirements.txt` + `requirements-transcribe.txt`
  (whisperx/torch) + `requirements-upload.txt` — المثبّتون يسألون عنها.
- **فحص البيئة**: `python -m scripts.preflight` (فحص شامل + تثبيت تلقائي
  للناقص عبر `--auto-fix`؛ `python -m scripts.doctor` للتوافق القديم).
- **قاعدة i18n**: أي مفتاح إنجليزي جديد يجب إضافته للغات الأربع
  (`en_US`/`ar_SA`/`pt_BR`/`tr_TR`) وإلا تفشل `tests/test_i18n_completeness.py`.
  ملفات locale بمسافة بادئة 4 (indent=4).
- **قاعدة الإصدار**: `app_version.py` يجب أن يطابق changelog دائماً؛ وارفع tag
  بنفس الرقم عند كل إصدار (التحديث التلقائي يعتمد على المقارنة).
- **الخطوط**: أي خط جديد يوضع في `fonts/` مع رخصة OFL ويُضاف للـ spec.

## 🧪 الاختبارات

| قبل v6.9 | بعد v6.9 | بعد v6.9.2 | بعد v6.10.0 |
|---|---|---|---|
| 343 | 367 | 377 | 429 |
