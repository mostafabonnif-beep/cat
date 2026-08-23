# OUSSAMA Cutter 7.20.0-pro — تحميل البث المباشر تلقائياً

# إصدار 7.20.0-pro — تحميل البث المباشر تلقائياً بعد انتهائه

تاريخ الإصدار: 2026-08-23

## 🔴 تحميل البث المباشر (جديد)

- **`scripts/download_live.py`**: وحدة جديدة كاملة لروابط البث المباشر
  بصيغة `https://youtube.com/live/ID`:
  - **`--check`**: يعرض حالة البث الحالية فقط (مجدول / مباشر / انتهى / عادي).
  - **`wait_until_ended`**: يفحص الحالة كل `--poll` ثانية حتى ينتهي البث —
    يعمل للبث المجدول (ينتظر البدء ثم النهاية) وللبث المباشر (ينتظر
    النهاية فقط)، مع مهلة `--max-wait` وتراجع ذكي عند أخطاء الشبكة.
  - **`download_when_live_ends`**: بعد الانتهاء يُحمَّل البث كفيديو عادي
    (VOD) بنفس الرابط — مع كل المزايا المعتادة (الجودة، الكوكيز، الترجمة،
    SponsorBlock).
- **CLI**: `python scripts/download_live.py <URL> [--check] [--poll 60] [--max-wait 21600] [--quality best|1080p|720p|480p]`
- **التكامل مع الواجهة**: خيار `--live-wait MINUTES` في `main_improved.py`
  وWebUI (حقل "انتظر نهاية البث بالدقائق") — ضع رابط البث وسينتظر البرنامج
  انتهاءه ثم يكمّل المعالجة كاملة (تقطيع + ترجمات + نشر).
- تصنيف ذكي لأخطاء yt-dlp المعروفة للبث المجدول ("This live event will
  begin in a few moments" → upcoming).

## ✅ الجودة

- **782 اختباراً ناجحاً** (+18 اختباراً جديداً للبث المباشر والتوصيلات).

---

# OUSSAMA Cutter 7.19.0-pro — المعجم العربي، SponsorBlock، إصلاحات

# إصدار 7.19.0-pro — المعجم العربي المسيء، SponsorBlock، وإصلاحات

تاريخ الإصدار: 2026-08-23

## 📚 قاعدة بيانات جديدة: المعجم العربي المسيء (تتحدث تلقائياً)

- **أداة `scripts/arabic_lexicon_importer.py`**: تسحب المعجم الأكاديمي للكلمات
  المسيئة العربية (إعداد محمد عطية — Google Research، مبني على مجموعات
  بيانات موسومة يدوياً) من مستودعه مباشرة، وتستخرج منه فئات الهوية والتحقير
  فقط (لا الفحش العام).
- **148 مصطلحاً عربياً جديداً** انضمّت إلى القائمة المدمجة — القاعدة الكنسية
  الآن **296 مصطلحاً** (الإصدار v4): 107 شتائم هوية عالية الخطورة + 74
  تحقيراً متوسط الخطورة + 12 مضايقة.
- **تقسيم ذكي للمتغيرات**: "زنجي / زنوج" تُستورد كلتا الصورتين.
- **إصلاح التطابق مع البادئات العربية**: و/ف اللاصقتان (وخولات، فاذبحوهم)
  لم تعودا تخفيان الكلمات عن الفلتر — بدون رفع إيجابيات كاذبة على الكلام
  العادي (ب/ك/ل بقيت كما هي لتفادي "بكلب").
- **سير عمل `arabic-lexicon-freshness.yml`**: يفحص المصدر كل اثنين ويفتح
  تذكيراً تلقائياً عند تغيّره — فالقاعدة تتحدث **بشكل مستمر**.
- الملف `safety_terms_arabic.json` يحفظ بصمة المصدر (`upstream_sha256`) مع
  الإسناد الكامل.

## 🚫 SponsorBlock — تخطي الإعلانات المدمجة عند التقطيع

- **خيار `--sponsorblock`** جديد في CLI وWebUI: يزيل مقاطع الرعاية
  (sponsor/intro/outro/selfpromo/...) من الفيديو **وقت التنزيل** عبر دعم
  yt-dlp المدمج (بدون أي اعتماد جديد) — فالمقاطع القصيرة الناتجة لا تحتوي
  أبداً على قراءة إعلان.
- قائمة منسدلة في الواجهة: Off / رعاية فقط / رعاية+مقدمة+ختام / الكل.

## 🐛 إصلاحات

- **إصلاح خيط التحديث الخلفي في الواجهة**: خطأ `NameError` كان يمنع مؤقّت
  تحديث قاعدة الأمان (كل 6 ساعات) من الانطلاق — الآن يعمل فعلاً.
- **إصلاح `i18n/scan_i18n.py` و`locale_diff.py`**: كانا يشيران إلى
  `zh_CN.json` المحذوف وينهاران؛ الآن يستخدمان `en_US.json` كمرجع، والمسح
  للقراءة فقط (لا يكتب فوق القاموس الكنسي).
- **`packaging/installer.iss`**: رابط التطبيق يشير للمستودع الجديد.
- **`pyproject.toml`**: الإصدار → 7.18.0+pro، و`yt-dlp[default]`، وتثبيت
  `onnxruntime<1.24` متطابق مع requirements.txt.

## ✅ الجودة

- **760 اختباراً ناجحاً** (+13 اختباراً جديداً للمعجم وSponsorBlock).

---

# OUSSAMA Cutter 7.18.0-pro — التقطيع، التتبع، قاعدة الأمان متعددة المصادر، ومحرك الأصالة

# إصدار 7.18.0-pro — تحسينات التقطيع والتتبع وحماية المحتوى المكرر

تاريخ الإصدار: 2026-08-23

## 🎬 تحسينات تقطيع المقطع (Cutting)

- **التقطيع على حدود الجمل (Pause-Aware Snapping):** تُحسب "كتل الكلام" من
  فترات الصمت في النص المفرَّغ، وتُثبَّت نقطتا البداية والنهاية لكل مقطع على
  حدود الجمل — فلا يبدأ المقطع ولا ينتهي في منتصف كلمة. فعّلت في
  `process_segments` (افتراضياً) عبر الدالة الجديدة `snap_segment_boundaries`.
- الحفاظ على جميع الضمانات القديمة: أقل/أقصى مدة، التصفية حسب الخطورة،
  وإزالة النوافذ المكررة.

## 🎥 تحسينات التتبع (Tracking)

- **تمليس كاميرا EMA (SmoothBox):** مُنعّم إحصائي جديد للصندوق المقتطع
  يزيل الاهتزاز ("Shaky Cam") بين إطارات الكشف — خيار `--face-smoothing`
  (0.05..1.0، الافتراضي 0.55) في CLI وWebUI.
- **Headroom احترافي (وضع المتحدث):** يُزيح الإطار المقتطع للأعلى بحيث يقع
  الوجه في الثلث العلوي من الشاشة الرأسية — خيار `--face-headroom`
  (0.0..0.35، الافتراضي 0.12).
- تسجيل قيم `smoothing` و`headroom` في `tracking_report.json` لكل مشروع.

## 🛡️ قاعدة بيانات الأمان — متعددة المصادر ومستمرة التحديث

- **`safety_updater.py` أصبح متعدد المصادر:** يدمج الحزم من المصدر الرسمي
  + مرايا المجتمع (`REMOTE_SOURCES`)، وأعلى خطورة تفوز لكل كلمة/لغة
  (`merge_packs`). الترقيم مضمون: فقط الحزم الأحدث تستبدل الكاش.
- **وضع المراقبة المستمرة:** `python scripts/safety_updater.py --watch 6`
  يعيد الفحص كل 6 ساعات؛ وتشغّل WebUI خيطاً خلفياً يحدّث القائمة وسجلّ
  سياسات يوتيوب كل 6 ساعات ما دامت الواجهة مفتوحة
  (يمكن تعطيله بـ `VIRALCUTTER_DISABLE_SAFETY_WATCHER=1`).
- **مُراقب سياسات يوتيوب (جديد):** `scripts/youtube_policy_watch.py` يفحص
  صفحات يوتيوب الرسمية (خطاب الكراهية، المضايقة، المحتوى العنيف، إرشادات
  المجتمع) ويحفظ بصمة لكل صفحة في `youtube_policy_feed.json`. عند تغيّر أي
  صفحة يفتح Workflow جديد (`youtube-policy-watch.yml`) تذكيراً تلقائياً
  لتحديث القائمة.
- **كلمات جديدة:** أُضيفت ~22 كلمة/عبارة (عربية وإنجليزية) وُثّقت في
  مراجعات سياسات يوتيوب 2026؛ الحزمة الكنسية الآن v2 (159 مصطلحاً).

## 🚫 محرك الأصالة — حماية المحتوى المكرر (جديد)

- **بصمة بصرية إدراكية:** `scripts/originality.py` يختزل أي مقطع إلى بصمة
  (d-hashes) لـ 16 إطاراً. المقارنة بين مقطعين تُظهر نسبة التشابه
  (`compare`)، والمحتوى المتطابق بصرياً (حتى بعد إعادة الترميز) يُمنع
  تلقائياً.
- **حارس المحتوى يستخدمها:** `content_guard.assess_clip` يفحص الآن بصرياً
  كل مقطع مقابل المقاطع المنشورة سابقاً لنفس المصدر، ويحظر
  `perceptual_near_duplicate` عند تشابه ≥ 80% — يكتب البصمة في
  `visual_fingerprint` عند كل نشر.
- **تحويلات حتمية (Presets):** `transform_with_seed` يطبق تغييرات تحريرية
  حقيقية (سرعة دقيقة، قلب أفقي، إزاحة قصّ، تدرّج لوني خفيف) بحسب بذرة
  ثابتة — لإعادة قصّ المقطع نفسه بشكل مختلف تماماً لكل منصة.
- تقرير `originality_report.json` لكل مشروع يلخص المكرر/المتشابه/المختلف.

## ⚙️ تحسينات أخرى

- `main_improved.py` + WebUI: خيارات `--face-smoothing` و`--face-headroom`
  في CLI وWebUI (تبويب إعدادات الوجه المتقدمة) و`build_command`.
- WebUI: لوحة الأمان تعرض الآن حالة قائمة الكلمات + حالة مراقب سياسات
  يوتيوب، مع زر تحديث يفحص المصادر كلها دفعة واحدة.
- `safety-blocklist-freshness.yml` يعمل مرتين أسبوعياً (الاثنين والخميس)
  بدل مرة واحدة.

---

# Changelog

## 🔧 v6.16.1 — إصلاحات الجودة: توحيد الإصدارات + الرخصة + بناء الـ exe + i18n (2026-08-10)

### Fix — توحيد الإصدارات (6.16.0 في كل مكان)
- `pyproject.toml` كان 6.14.0، `installer.iss` كان 6.14.1، `README_ar.md` كان
  6.12.0 — كلها الآن **6.16.0** مطابقة لـ `app_version.py` (قاعدة "الإصدار"
  في CONTRIBUTING.md).

### Fix — تناقض الرخصة
- `pyproject.toml` كان يصرّح **MIT** بينما ملف `LICENSE` هو **GPL-3.0** — الآن
  GPL-3.0 في الاثنين (وGitHub API).

### Fix — بناء Windows EXE كان يفشل دائماً
- `packaging/viralcutter.spec`: سطر 73 كان يستعمل `binaries += ...` قبل
  تعريف المتغير — `NameError: name 'binaries' is not defined` أوقف كل
  PyInstaller build. التعريف انتقل قبل أول `+=` (مع تعليق يشرح السبب).

### Fix — أمان: إزالة shell=True
- `scripts/transcribe_cuts.py`: `subprocess.run(command, shell=True)` مع
  قائمة وسيطات — حذف الـ shell (نفس السلوك، بلا خطر حقن أوامر من أسماء
  الملفات).

### Fix — تناسق ملفات الترجمة
- `ar_SA.json` كان يحمل **103 مفاتيح يتيمة** (نصوص عربية مكتوبة حرفياً في
  الواجهة لا تمر عبر `i18n()`) — حُذفت؛ اللغات الأربع الآن **578 مفتاحاً**
  متطابقة تماماً.
- `i18n/i18n.py`: سلسلة سقوط جديدة — اللغة المختارة ← en_US ← المفتاح الخام
  (لا مزيد من المفاتيح الخام أو النصوص الغريبة عندما تتأخر ملفات لغة).

### Tests
- +5 (حارس اللامتماثل ×3 لغات + سقوط en_US + أولوية اللغة) → **539 خضراء**
  وruff نظيف.

### i18n — توجيه كل نصوص الواجهة عبر نظام الترجمة (2026-08-10، الجولة الثانية)
- **~110 نصاً عربياً حرفياً** في `webui/*.py` (تبويب "إنشاء جديد"، كل التسميات،
  أكورديونات، رؤوس الأقسام، جداول المراجعة/الطابور، رسائل الأخطاء الـ 22،
  بطاقات الحالة، دليل البدء السريع) → كلها الآن عبر `i18n()` بمفاتيح إنجليزية.
- **60 مفتاحاً جديداً** بأربع لغات (ar/en/pt/tr)؛ اللغات الأربع الآن **638
  مفتاحاً متطابقاً**. تحقق فعلي ببناء الواجهة: وضع `en_US` يعرض **صفر**
  نصوص عربية.

### Tests — أول تغطية لمسار CLI
- `tests/test_cli_main.py` (+26): دوال main_improved النقية (parse intervals،
  load_json، subtitle config، cleanup، emit_progress)، مسارات الإقلاع
  (بدون وسيطات ← WebUI، --help، علم غير معروف)، و`run_safety_stage` المستخرجة
  (6 سيناريوهات: تخطي، فلترة، حفظ، خروج عند الحجب الكلي، نجاة من انهيار الفلتر).
  الاعتماديات الثقيلة (cv2/mediapipe/torch) تُحاكى في sys.modules.
- **572 اختباراً أخضر** وruff نظيف.

### Refactor
- استخراج مرحلة الأمان من `main()` العملاقة إلى `run_safety_stage()` —
  دالة نقية قابلة للاختبار (main تقلّصت ~75 سطراً).


## 🚀 v6.16.0 — الاكتمال الوظيفي: بطاقة المخاطر في الواجهة + توليد مقاطع جديد (2026-08-09)

### New — 🛡️ بطاقة المخاطر داخل الواجهة (تبويب المراجعة)
- زر "🛡️ بطاقة المخاطر": يعرض تقرير الامتثال لكل مقطع مباشرة في الواجهة —
  مستوى الخطر (منخفض/متوسط/مرتفع/خطير بألوان)، درجة أول 7 ثوانٍ، نسبة
  تشابه المصدر، الفحص البصري، وحالة النشر (⛔ محجوب / ✅ مسموح) + عدّادات
  الملخص. لم يعد الامتثال JSON في الطرفية فقط.
- زر "حفظ تقرير HTML في المشروع": يكتب `risk_report.html` قابل للفتح/الطباعة
  بجانب `risk_scorecard.json`.
- `scripts/risk_scorecard.py`: `build_scorecard_html(report)` (نقي وقابل
  للاختبار) + `render_html_report(project_folder)` + علم CLI
  `--html-report` (للاستخدام من سطر الأوامر أيضاً).

### New — 🔄 توليد مقاطع جديدة من الواجهة
- خانة "توليد مقاطع جديدة (تجاهل المحفوظة)" في تبويب الإنشاء: كانت
  الواجهة تعيد استخدام `viral_segments.txt` المحفوظة صامتاً (وضع
  skip-prompts) — الآن يمكن إجبار التحليل من جديد من الصفر (باستهلاك
  رصيد API). تصل للـ CLI عبر `--force-new-segments`، وتعمل في التشغيل
  الفردي والجماعي، وتُحفظ كتفضيل دائم.

### Tests
- +5 (build_command flag ×2، build_scorecard_html، render_html_report ×2) →
  **534**، وتحقق تفاعلي حقيقي بالمتصفح (زر البطاقة يعرض البيانات + حفظ
  الملف ينجح).
# Changelog

## 🎨 v6.15.1 — الواجهة: زر فحص مباشر + نص حالة صحيح + فصل CSS (2026-08-09)

### New — تبويب الرئيسية
- **زر "🔄 إعادة الفحص"**: يعيد تشغيل الفحص المسبق مباشرة من الواجهة ويحدّث
  بطاقة حالة النظام فوراً (كانت تبنى مرة واحدة عند الإقلاع فقط).
- فصل البطاقة عن دليل البدء: `home_quickstart()` + `env_status_html(force)`
  في `webui/header.py` مع كاش قابل للإبطال.

### Fix — نص الحالة الافتراضي
- لوحات التقدم/المهام كانت تعرض "جار التحميل..." قبل أي تشغيل — وهي حالة
  انتظار لا تحميل. الآن: **"بانتظار التشغيل..."** (مفتاح i18n جديد ×4 لغات).

### Refactor — فصل CSS (تنظيم)
- الستايل الكامل انتقل من `webui/app.py` إلى **`webui/style.py`** (وحدة
  `style.CSS`) — app.py أصغر وأنظف؛ الوحدة الجديدة مضافة للـ spec (تعمل
  في الـ exe).

### Fix — بوابة ruff
- استثناء `*.ipynb` كان يُفقد لأن `ruff check --exclude .venv` في سطر
  الأوامر يستبدل قائمة الإعداد — الأوامر الموثّقة الآن `ruff check .`
  (الإعداد يتولى الاستثناءات).

### Tests
- 529 خضراء + HTTP 200 + تحقق تفاعلي عبر المتصفح (زر إعادة الفحص يحدّث
  البطاقة + نص "بانتظار التشغيل" يظهر).
# Changelog

## 🎨 v6.15.0 — الواجهة: ترتيب كامل + شعار + لوحة رئيسية + حالة النظام (2026-08-09)

### New — ترتيب التبويبات بمنطق سير العمل
- ترتيب جديد (9 تبويبات): **🏠 الرئيسية ← 📥 إنشاء ← 👀 مراجعة ← ✍️ محرر
  الترجمات ← 🚀 رفع ونشر ← 🗂️ المكتبة ← 📋 طابور ← 🧠 علّم الأداة ← 📈 الأداء**
  (قبل: الرفع والمكتبة ومحرر الترجمات مبعثرة؛ الآن تسلسل عمل طبيعي).

### New — تبويب "🏠 الرئيسية"
- **ابدأ خلال دقيقة**: 3 خطوات سريعة (رابط ← إعدادات ← بدء المعالجة).
- **حالة النظام الحية**: بطاقة فحص مسبق تُبنى من `scripts.preflight` عند
  الإقلاع — ✅ كل شيء في مكانه / ⚠️ تحذيرات اختيارية / ❌ مشاكل حرجة، مع
  عدد الفحوصات (مثلاً 26 ناجحاً · 7 تحذيرات) — "كل شيء في مكانه" أصبح
  مرئياً في الواجهة لا مجرد سجل طرفية.

### New — هوية بصرية
- **الشعار الحقيقي** (نفس أيقونة الـ exe) في رأس الواجهة بدل الإيموجي —
  مضمّن كـ data URI (يعمل من المصدر ومن الـ exe).
- CSS محدّث: تبويبات على شكل كبسولات مع تمييز البرتقالي للمحدد، شريط
  الإجراءات كبطاقة زجاجية، لوحات التقدم/المهام/الأخطاء كبطاقات، زر
  "بدء المعالجة" بتدرج برتقالي، سكرولبار أنيق للسجل.

### Fix
- مفتاحا i18n ناقصان "Home" و"Performance" أُضيفا للغات الأربع (كانت
  تظهر بالإنجليزية في شريط التبويبات).

### Tests
- 529 خضراء + فحص إقلاع حقيقي (HTTP 200) + لقطة شاشة موثّقة.
# Changelog

## 🚀 v6.14.1 — إصلاحات الجودة الشاملة: lint نظيف + خلل mediapipe قاتل + بوابات CI (2026-08-09)

### Fix — خلل حقيقي قاتل
- **`scripts/edit_video.py`**: `generate_short_mediapipe` كان يستعمل
  `coordinate_log` (تسجيل إطارات بلا وجه في وضع padding الافتراضي) **بلا
  تعريف** — كان ينهار بـ NameError في منتصف المعالجة على أي مقطع mediapipe
  بلا وجه. أُصلح (اكتشفه فحص `ruff` الجديد: F821).

### Quality — أول بوابة lint في تاريخ المشروع
- **ruff** في `pyproject.toml` (قواعد: E/F/I/B — أخطاء حقيقية + ترتيب
  استيراد؛ تجاهلات موثّقة: E501/E701/E722 للأسلوب المتعمد).
- **إصلاح 250+ ملاحظة** عبر المشروع: إزالة 20+ متغيراً ميتاً، `raise ...
  from None`، ربط closures بحلقات (B023)، إصلاح تظليل متغير في
  music_fingerprint (B020)، mutable default في adjust_subtitles (B006)،
  تحويل lambdas إلى def، تنظيف استيرادات.
- **`ruff check .` → All checks passed.** (كان 1011 ملاحظة قبل الضبط).

### Quality — تغطية + بوابة CI جاهزة
- `[tool.coverage]` في pyproject + تشغيل محلي: **39% تغطية** (إعلامية).
- `docs/CI_UPDATE_v614.md`: محتوى `ci.yml` كامل (ruff + pytest + coverage +
  فحص preflight) و`build-exe.yml` (خطوة مثبّت Inno Setup) — **جاهز للصق من
  المالك** (التطبيق بلا صلاحية Workflows كما هو موثّق).
- استثناءات coverage موثّقة (test_mediapipe_optional ينهار مع متتبع
  coverage؛ اختبارا محاكاة download يعتمدان على جراحة sys.modules) —
  يستثنيان فقط من التقرير الإعلامي، ويعملان كاملين في البوابة الوظيفية.

### New — ملفات المشروع الاحترافية
- `CONTRIBUTING.md` (القواعد الصارمة: i18n ×4، الإصدار، lint، حرمة
  منظومة الأمان، حماية حلقة القص).
- `SECURITY.md` (إبلاغ خاص، نطاق: طبقة الحماية من الضربات أولاً).
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).
- `.editorconfig` + قوالب Issues (bug/feature) في `.github/ISSUE_TEMPLATE/`.

### New — مثبّت Windows
- `packaging/installer.iss` (Inno Setup 6): متعدد اللغات (EN/AR/PT/TR)،
  أيقونة، اختصارات، تثبيت بلا صلاحيات إدارية — يُبنى في CI بعد لصق
  التحديث من `docs/CI_UPDATE_v614.md`.

### Tests
- 529 (كلها خضراء) + ruff نظيف.
# Changelog

## 🚀 v6.14.0 — الاحترافية: أزرار WebUI + أيقونة exe + قفل اعتماديات (2026-08-09)

### New — WebUI: التعلّم والأداء والأبعاد (المستوى 1 الاحترافي)
- **تبويب "🧠 علّم الأداة"**: أضف كلمة (حظر) / اسمح (إيجابية كاذبة) / احذف،
  استخراج أنماط من مشروع محجوب (مع تطبيق)، عرض القائمة المخصصة ويوميات
  التعلم — كلها من الواجهة، بلا سطر أوامر.
- **تبويب "📈 الأداء"**: ملخص القناة / أفضل المقاطع / المشاهدات اليومية من
  YouTube Analytics (قراءة فقط) — أزرار مباشرة في الواجهة.
- **إعداد "📐 تأطير الإخراج"** في إعدادات المونتاج (9:16/4:5/1:1/16:9 + وضع
  القص/الحشو) — يُحفظ تلقائياً مثل كل حقول الواجهة، ويعمل في التشغيل
  الفردي والتجميعي (batch).
- `webui/learn_panel.py` جديد (معالجات داخل العملية — تعمل في الـ exe أيضاً).
- i18n: +39 مفتاحاً × 4 لغات.

### New — أيقونة exe احترافية
- أيقونة "مقص يقطع شريط فيديو" (تدرج بنفسجي→وردي) مولّدة ومحولة لـ
  `packaging/icon.ico` (16→256px) + `icon.png`؛ الـ spec يمررها للـ exe —
  لا مزيد من أيقونة PyInstaller الافتراضية.

### New — قفل الاعتماديات (Reproducible installs)
- `pyproject.toml` (PEP 621): وصف حديث + الحزم الأساسية + اختياريات
  `transcribe`/`upload`/`dev` (التورch/whisperx يبقون اختياريين عمداً —
  يعتمدون على GPU).
- `uv.lock` (408 حزم): `uv sync` يعيد تثبيتاً مطابقاً تماماً؛ قيد
  `onnxruntime<1.24` (الإصدارات الأحدث أسقطت wheels لـ Python 3.10).
- تدفق requirements.txt الكلاسيكي (مع معالج GPU) يعمل كما هو.

### Fix
- مسار WebUI للرفع الجماعي الآن ينقل إعداد الأبعاد أيضاً.

### Tests
- +14 (learn_panel 10، pipeline aspect 4) → **529**.
# Changelog

## 🚀 v6.13.0 — إكمال الخارطة: حلقة التعلم من الضربات + أبعاد الإخراج + تحليلات الأداء (2026-08-09)

### New — 5.1 حلقة تغذية راجعة من الضربات (Strike Feedback Loop)
- **`scripts/strike_feedback.py`**: "علّم الأداة" — مقطع أخذ ضربة/claim؟ علّمها:
  - `add --term "الكلمة" --reason "..."` → يضيف للقائمة المخصصة `safety_terms.json`
    (الفلتر يقرأها تلقائياً في كل تشغيل لاحق — لا ربط إضافي).
  - `allow --term` → استثناء من القائمة المدمجة (تصحيح الإيجابيات الكاذبة).
  - `remove` / `list` / `stats` / `export` (json|txt).
  - `from-scorecard --project VIRALS/x [--apply]` → يستخرج الأنماط من
    `safety_report.json` + `risk_scorecard.json` للمقاطع المحجوبة ويقترح تعلمها.
  - يوميات كل حدث في `strike_feedback.json` (ذاكرة الأداة: ماذا تعلّمت ومتى ولماذا).
  - **التكامل**: بعد بطاقة المخاطر، إذا وُجدت مقاطع محجوبة → تلميح تعلّم مباشر،
    و`--auto-learn-blocked` يعلّمها تلقائياً.
- **WebUI**: التعليمات في docs (الواجهة الكاملة في جولة لاحقة).

### New — أبعاد إخراج إضافية (غير 9:16) — نسخة آمنة
- **`scripts/reframe.py`**: بعد حرق الترجمات، يحوّل المقاطع النهائية إلى
  `--output-aspect 9:16|4:5|1:1|16:9` بتمريرة ffmpeg واحدة (crop=قص مركزي
  للـ 4:5/1:1، pad=تمويه للـ 16:9) — دون لمس منطق القص/تتبع الوجه (السبب
  الجذري لمشكلة الـ desync في v6.6).
- الاستبدال ذري + نسخة `*.orig.mp4` احتياطية؛ بطاقة المخاطر ترى الملف النهائي.
- `--platform yt_standard` يضبط 16:9 تلقائياً (قالبه يقول 16:9 وكان يخرج 9:16!).
- اختبار حقيقي: 9:16 → 4:5 (1080x1350) و→ 16:9 (1920x1080) ✅.

### New — 5.4 تحليلات الأداء (Performance Analytics)
- **`scripts/analytics.py`**: YouTube Analytics API (OAuth للقراءة فقط —
  لا يعدّل شيئاً): `--summary` (مشاهدات/مشاهدة/تفاعل/مشتركين)، `--top N`
  (أفضل المقاطع بعناوينها)، `--trends` (يومي)، `--export report.json`،
  `--check` (تحقق الإعداد). يحتاج تفعيل YouTube Data API v3 +
  YouTube Analytics API في جوجل كونسول (إرشادات في docstring الوحدة).

### Fix
- `--platform yt_standard` الآن يخرج فعلاً 16:9 (كان 9:16 مع قالب 16:9).

### Tests
- +34 (strike_feedback 15، reframe 10، analytics 9) → **515**.
# Changelog

## 🚀 v6.12.0 — الفحص المسبق الشامل: "كل شيء في مكانه قبل التشغيل" (2026-08-09)

### New — preflight: فحص كل شيء + تثبيت تلقائي لكل ما هو ناقص
- **`scripts/preflight.py` جديد**: قبل أن يعمل البرنامج يفحص كل شيء —
  Python, ffmpeg/ffprobe, كل الاعتماديات من `requirements*.txt`, إعداد
  `api_config.json`, الملفات المرفقة (خطوط Montserrat, قائمة الأمان,
  الترجمات i18n), مجلد `models/`, مساحة القرص والصلاحيات — **ويصلّح/يثبّت
  تلقائياً كل ما هو ناقص** (تثبيت الحزم الأساسية عبر pip، إعادة إنشاء
  `api_config.json` من القالب، إنشاء المجلدات، خفض numpy إلى <2 عند
  المخالفة). مكتبة stdlib خالصة — تعمل حتى على جهاز بلا أي تثبيت.
- **أكواد خروج قابلة للقراءة آلياً**: 0 = جاهز، 1 = مشاكل حرجة باقية،
  2 = تحذيرات فقط (البرنامج سيعمل). `--json` لتقرير آلي.
- **أوضاع**: `--check` (قراءة فقط)، `--fix` (إصلاح تفاعلي)، `--auto-fix`
  (تلقائي بالكامل — تستخدمه سكربتات التشغيل)، `--off` (تجاوز).
- **التكامل في كل نقاط الدخول**: `main_improved.py` (وضع `--preflight
  auto|check|off`، الافتراضي auto، يعمل قبل أي معالجة وقبل إقلاع الويب)،
  `webui/app.py` (عند التشغيل المباشر)، `run.bat` / `run_webui.bat` /
  `run.sh` (أول خطوة قبل الإقلاع: تثبيت كل ناقص ثم تشغيل البرنامج).
- **الوضع المجمّد (exe)**: يتحقق من الأدوات المدمجة (ffmpeg/ffprobe في
  sys._MEIPASS) بدل محاولة تثبيت — لا pip داخل الـ exe.
- **الاختياري الثقيل محترم**: حزم التفريغ الصوتي (~2GB) والرفع لا تُثبَّت
  بصمت أبداً في الوضع التلقائي — فقط بالطلب الصريح (`--fix` بسؤال، أو يدوياً).
- **مخرج هروب**: `VIRALCUTTER_SKIP_PREFLIGHT=1` أو `--preflight off`.
- اختبارات: +28 → **481** (كلها خضراء).

### Fix
- `doctor.py` القديم بقي للتوافق (يُستدعى عبر `python -m scripts.doctor`) —
  `preflight` هو الخليفة الشامل.
# Changelog

## 🚀 v6.11.5 — whisperx + torch مدمجان في الـ exe (تفريغ صوتي يعمل من الصندوق) (2026-08-09)

### New — النسخة الكاملة تشمل خط التفريغ الصوتي
- كان الـ exe يفتح الواجهة لكن التفريغ (whisperx/torch) غير متوفر لأن هذه
  المكوّنات (~2GB) كانت مستثناة. الآن **تُدمج في الـ exe**: torch (CPU)
  + whisperx + faster-whisper + ctranslate2 (مكتبات أصلية).
- الـ spec يجمع submodules + dynamic libs لسلسلة whisperx.
- `--self-check` جديد: يستورد كل المكوّنات الثقيلة داخل الـ exe ويبلغ —
  والـ CI يشغّله قبل كل Release، فأي نقص في الحزمة يمنع الإصدار.
- ملاحظة: الـ exe يكبر إلى ~2GB+ (ثمن التفريغ المدمج)؛ الواجهة تبقى سريعة
  الإقلاع لأن torch يُستورد lazily عند التفريغ فقط.
- الإصدار 6.11.5.

## 🚀 v6.11.4 — نسخة سطح المكتب الكاملة: ffmpeg مدمج + اختبار إقلاع تلقائي (2026-08-09)

### New — الـ exe يعمل من الصفر بدون أي تثبيت خارجي
- **ffmpeg/ffprobe يُدمجان في الـ exe** (من BtbN FFmpeg-Builds) — معالجة
  الفيديو تعمل فوراً، لا حاجة لـ install_ffmpeg_windows.bat أو winget.
- `main_improved.py`: في الوضع المجمّد يضع مجلد الحزمة (sys._MEIPASS) في
  PATH حتى تجدها subprocesses باسم "ffmpeg"/"ffprobe".
- **اختبار إقلاع حقيقي في CI**: قبل نشر أي Release، يبدأ الـ exe وضع الواجهة
  ويشترط استجابة HTTP 200 من http://127.0.0.1:7860 — أي كسر في الحزمة
  (ملفات بيانات ناقصة، hidden imports) يوقف الإصدار تلقائياً بدل وصوله
  للمستخدم معطوباً (هذا كان سيكشف مشاكل version.txt في v6.11.2/6.11.3).
- الاختبار كشف فعلاً مشكلة ثانية: gradio يقرأ **ملفاته المصدرية** (`.py`)
  عند الإقلاع (component_meta → blocks_events.py) وPyInstaller يستبعدها →
  أُصلح بـ `collect_data_files("gradio", include_py_files=True)`.
- **الجذر الحقيقي لكل إخفاقات الإقلاع**: كود تشغيل الخادم في `webui/app.py`
  كان داخل `if __name__ == "__main__":` — فعند استيراده كوحدة كان يخرج
  البرنامج بصمت (exit 0) بدون خادم. أُعيدت هيكلته إلى `_launch(argv)` تُستدعى
  صراحة من `main_improved._launch_webui` → **تحقق محلي: HTTP 200** ✅.
- الإصدار 6.11.4.

## 🚀 v6.11.3 — حل عام لأخطاء version.txt في الـ exe (groovy + أي باقة مستقبلية) (2026-08-09)

### Fix — WebUI يفشل في الـ exe: groovy/version.txt
- بعد إصلاح safehttpx (v6.11.2) ظهر نفس النمط في باقة ثانية: `groovy`
  تقرأ `version.txt` عند الاستيراد → FileNotFoundError.
- **الحل العام**: `viralcutter.spec` يفحص الآن كل الباقات المثبتة و يجمع
  بيانات أي باقة فيها `version.txt`/`VERSION` في جذرها — يغطي safehttpx و
  groovy وأي باقة مستقبلية من هذا النوع دفعة واحدة (بدل حزمة-حزمة).
- الإصدار 6.11.3.

## 🚀 v6.11.2 — إصلاح إقلاع الواجهة في الـ exe (ملف بيانات safehttpx مفقود) (2026-08-09)

### Fix — WebUI يفشل عند الإقلاع داخل الـ exe
- **السبب**: `gradio` (v6) يستورد `safehttpx` الذي يقرأ `version.txt` من مجلد
  الحزمة عند الاستيراد، وPyInstaller ما جمعش هذا الملف (لا يوجد hook له).
- **الحل**: `packaging/viralcutter.spec` يجمع الآن بيانات `safehttpx`
  (و`gradio` دفاعياً) صراحةً → الواجهة تفتح في المتصفح من الـ exe.
- الإصدار 6.11.2.

## 🚀 v6.11.1 — الـ exe يفتح الواجهة الرسومية عند النقر المزدوج + إصلاح تشغيل pipeline من داخل الـ exe (2026-08-09)

### Fix — النقر على الـ exe يفتح نافذة سوداء تغلق فوراً
- **السبب**: الـ exe كان مبني على CLI (`main_improved.py`) — لا يفتح الواجهة. والواجهة
  (`webui/app.py`) تشغّل خط المعالجة كـ subprocess بـ `python main_improved.py`،
  وهذا الملف غير موجود داخل حزمة الـ exe (كل شيء مدمج في الملف الواحد) → فشل فوري.
- **الحل**:
  - التشغيل بدون وسائط (نقر مزدوج) أو بـ `--webui` → يفتح الواجهة الرسومية تلقائياً
    في المتصفح على `http://localhost:7860` (النافذة السوداء تبقى مفتوحة — هي سجل
    البرنامج، لا تُغلق).
  - `webui/runtime.py` جديد: في الـ exe المجمّد يُعيد البرنامج تشغيل نفسه
    (`sys.executable`) بدل `python main_improved.py`، ومجلد المشاريع (VIRALS)
    يصير بجانب الـ exe بدل مجلد مؤقت.
  - `export_xml` (تصدير Premiere) أصبح يُنفَّذ داخل العملية بدل subprocess — يعمل
    في الوضعين.
  - عند فشل الإقلاع: يُكتب `crash_report.log` بجانب التطبيق وتنتظر النافذة ضغطة
    Enter بدل أن تغلق في ثوانٍ (تعرف السبب بدل الصمت).
- الـ spec: يجمع وحدات webui + `webui/preview.json` + وحدات export_xml صراحةً.

### Tests
- اختباران جديدان لحالة الـ exe المجمّد (لا script path في الأمر، والعكس في
  المصدر). **453 passed**.

## 🚀 v6.11.0 — حواجز الاستخدام الواسع: exe مبني تلقائياً + فحص موسيقى يخدم فعلاً + رفع انستغرام يشتغل بدون استضافة يدوية (2026-08-08)

### New — الـ exe يُبنى تلقائياً على GitHub Actions (يزيل أكبر حاجز)
- PyInstaller لا يعمل cross-compile، فكان الـ Release يتطلب ويندوز يدوياً.
  الآن `.github/workflows/build-exe.yml` يبني `ViralCutter.exe` على runner
  ويندوز رسمي: على أي `tag v*` يُرفع الـ exe تلقائياً إلى Release، وعلى
  `workflow_dispatch` يبني الفرع الحالي ويتركه artifact للاختبار.
- الـ exe يأتي و **fpcalc.exe مدمج** بداخله — فحص الموسيقى يشتغل من أول تشغيل
  (المستخدم العادي لا يحتاج تحميل أي شيء).

### Fix — فحص الموسيقى لم يعد "بلا أسنان" بدون إعداد
- **`--install-fpcalc`**: أمر واحد يحمّل fpcalc.exe تلقائياً (من
  releases الرسمية لـ chromaprint) ويضعه بجانب التطبيق أو في
  `~/.viralcutter/bin` — بدل إرشاد يدوي طويل.
- **استعلام AcoustID بدون pyacoustid**: بصمة `fpcalc -raw` تُرمَّز الآن
  تلقائياً (encode 32-bit) وتُرسل إلى `api.acoustid.org` — سابقاً كان
  مسار fpcalc وحده لا يستطيع الاستعلام إطلاقاً.
- `fpcalc_available()`/`fingerprint_file()` يبحثان الآن بجانب الـ exe
  (sys._MEIPASS) ثم `~/.viralcutter/bin` ثم PATH — يعمل مع النسخة المعبأة
  وبدونها.
- **صدق في التقرير**: `music_fingerprint.json` يعلن `backend`
  (pyacoustid/fpcalc/none) + `coverage_note` صريحة أن AcoustID لا يغطي
  الموسيقى العربية/غير المسجّلة جيداً، ويوصي بقاعدة مرجعية محلية
  (`--build-local-db`) لهذا النوع — فالمستخدم يعرف بالضبط قوة الفحص لا وهمه.
- CLI يطبع تحذيراً واضحاً عندما يكون `no_fpcalc > 0` مع خطوة الإصلاح.

### Fix — الرفع لانستغرام يشتغل بدون استضافة يدوية
- Graph API لا يقبل ملفاً محلياً (يلزم رابط HTTPS عام) — كان هذا يقتل
  الميزة للمستخدم العادي. الآن `host_media_file()` يرفع المقطع تلقائياً إلى
  مضيف مجاني مجهول (catbox.moe، و0x0.st احتياطاً) ويمرر الرابط إلى
  Graph API. عطّل ذلك بـ `IG_HOST_DISABLE=1` أو اربط `IG_VIDEO_URL` بنفسك.
- مكالمات Graph API (media/media_publish) أصبحت form-encoded
  (application/x-www-form-urlencoded) كما يتوقعها API فعلياً — كانت JSON.

### Fix — باك حقيقي في رفع تيك توك
- بايتات الفيديو كانت تُقرأ في الذاكرة و**لا تُرسل أبداً** في PUT
  (`data=None`) — ينجح مع mock ولا ينجح ضد API الحقيقي. أُصلح: الـ body
  الآن هو البايتات نفسها مع `Content-Type: video/mp4`.
- أخطاء الأذونات/الموافقة في تيك توك تُرفق تلميحاً صريحاً
  (الموافقة على Content Posting API تستغرق أياماً/أسابيع) بدل رسالة غامضة.

### New — تشخيص قبل الرفع
- `python -m scripts.upload_gate --check <youtube|tiktok|instagram>`: يفحص
  بدون شبكة — المفاتيح، التوكنات، وما لا يمكن التحقق منه محلياً (موافقة
  تيك توك) يُعلَن بوضوح. `--project` لم يعد مطلوباً لهذا الأمر.

### Tests
- +~74 اختباراً: باك الـ PUT، ترميز البصمة، تلقائي المضيف، `--check`،
  اكتشاف fpcalc، إلخ. **451 passed** محلياً.

## 🚀 v6.10.0 — ربط TikTok/Instagram + بصمة الموسيقى Chromaprint + أزرار رفع من الواجهة (2026-08-08)

### New — TikTok Content Posting API (Roadmap 2.2)
- **رفع حقيقي لتيك توك** في `scripts/upload_gate.py`: OAuth2 كامل
  (authorization-code + local callback + refresh token) عبر
  `python -m scripts.upload_gate --auth tiktok`، ثم init → PUT upload →
  status polling على `open.tiktokapis.com/v2/post/publish/...`.
- الخصوصية الآمنة افتراضياً `SELF_ONLY` (مسودة خاصة)؛ غيّرها بـ `TIKTOK_PRIVACY`
  عندما تقصد النشر فعلاً. المتطلبات: تطبيق مطوّر TikTok مع صلاحية
  Content Posting API + `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`.
- التوكن يُحفظ في `~/.viralcutter/tiktok_token.json` ويُجدَّد تلقائياً.

### New — Instagram Graph API Reels (Roadmap 2.2)
- رفع Reels بخطوتين (`/media` → `/media_publish`) مع توكن طويل الأمد
  (`IG_ACCESS_TOKEN` + `IG_USER_ID`) ودعم تبادل التوكن القصير
  (`--auth instagram` مع `IG_CLIENT_ID/SECRET`).
- ملاحظة صريحة في الكود: Graph API يتطلب **رابطاً عاماً HTTPS** للفيديو
  (لا يوجد رفع ملفات خام لـ Reels) — مرّر `--video-url` أو `IG_VIDEO_URL`.

### New — بصمة الموسيقى Chromaprint (Roadmap 2.3)
- `scripts/music_fingerprint.py`: بصمة محلية عبر `pyacoustid` أو `fpcalc`,
  كشف عبر AcoustID (مفتاح عام مدمج + `ACOUSTID_API_KEY`)، وقاعدة مرجعية
  محلية دون إنترنت (`--build-local-db` + مطابقة n-gram).
- النتيجة في `music_fingerprint.json` لكل مقطع + ملخص في النهاية.
- بوابة الرفع تستهلك التقرير: `--music-gate warn` (افتراضي، تحذير لا يمنع) /
  `block` (يرفض الرفع) / `off`. في الواجهة: زر "فحص بصمة الموسيقى".
- انحدار كامل: بدون fpcalc/pyacoustid لا ينكسر شيء — يُكتب `no_fpcalc`.

### New — WebUI: أزرار تشغيل/ترجمة/رفع لكل مقطع (بدل CLI فقط)
- تبويب جديد **"🚀 رفع ونشر"**: اختر مشروعاً ثم مقطعاً من القائمة →
  **تشغيل مباشر** في مشغّل فيديو، اقتراح عنوان/وصف من `viral_segments.txt`.
- **ترجمة** ترجمات المقطع الواحد (deep-translator) مع معاينة النص.
- **رفع** عبر بوابة الأمان (يوتيوب/تيك توك/انستغرام) مع تجربة (dry-run)
  افتراضية، وسجل رفع حي في الواجهة، وخيار بوابة الموسيقى.
- وحدة قابلة للاختبار `webui/publish_panel.py` (لا تعتمد على gradio).

### Fixes
- `check_clip`/`gate_upload` يدعمان `music_gate`؛ تحذيرات الموسيقى لا تمنع
  الرفع وحدها (المانع يبقى للخطورة العالية).
- `_BaseUploader` يقبل `video_url` و`music_gate` — واجهة موحدة لكل المنصات.

### Tests
- +53 اختباراً (TikTok flow/refresh/status، Instagram two-step، OAuth
  URLs، Chromaprint decode/مطابقة محلية/AcoustID، بوابة الموسيقى،
  publish_panel كاملاً). — يشمل الاختبارات السابقة.

### ملاحظة Windows (المستخدم)
- لا يمكن بناء `ViralCutter.exe` من لينكس (PyInstaller ليس cross-compiler) —
  شغّل `packaging/build_windows.bat` على جهازك (الخطوات في
  `docs/RELEASE_CHECKLIST_WINDOWS.md`).
- للتحقق الكامل من الرفع والتيك توك: أنت وحدك تملك بيانات الحسابات —
  كل الكود مكتوب ومُختبَر (mocks)؛ التشغيل الحي يتطلب مفاتيحك.

## 💾 v6.9.2 — كل إعدادات الواجهة تُحفظ تلقائياً (2026-08-07)

### New
- **الحفظ الكامل للواجهة**: امتداد لميزة v6.9 — ليس المفتاح فقط، بل **كل حقول
  النموذج** تُتذكَّر الآن تلقائياً بين الجلسات: جودة الفيديو، الترجمة الهدف،
  ترجمات يوتيوب، فلتر الأمان ومراجعته، قالب المنصة، بوابة الميتاداتا، لغة
  العناوين، التلميع، الموسيقى، اللوجو، الكوكيز، نموذج Whisper، وطريقة العمل.
  يُحفظ الملف محلياً `webui_settings.json` (غير متتبَّع في git).
- الآلية: `.change` على كل حقل → حفظ ذري؛ `demo.load` → استعادة كل الحقول.
- 4 اختبارات جديدة (round-trip، تجاهل None، ملف تالف، ملف مفقود).

## 🛠️ v6.9.1 — mediapipe اختياري + خطوط Montserrat مضمّنة + CI ffmpeg (2026-08-07)

### Fixes
- **mediapipe أصبح اختيارياً** في `scripts/edit_video.py` / `one_face.py` /
  `two_face.py`: كان `import mediapipe` مكشوفاً فتنهار المعالجة في منتصفها على
  أي جهاز بلا الحزمة. الآن انحدار تلقائي إلى OpenCV Haar Cascade (كان جاهزاً
  في موقع الاستخدام — فقط الاستيراد كان يقتل الوحدة).
- **خطوط Montserrat مضمّنة** في `fonts/` (Regular/Bold/ExtraBold + OFL):
  `burn_subtitles.py` يمرّر `:fontsdir=` لـ ffmpeg فتُحفظ ترجمات "Hormozi" بخطها
  الصحيح حتى بدون تثبيت الخط على النظام (كان ffmpeg يستبدله بصمت). أُضيفت
  الخطوط إلى `packaging/viralcutter.spec` أيضاً.
- **CI**: خطوة `apt-get install ffmpeg` في `.github/workflows/ci.yml` جاهزة
  لكنها **محجوبة بالصلاحيات**: GitHub يرفض دفع تعديلات workflows من تطبيق
  moclaw-ai بلا صلاحية Workflows. المحتوى كاملاً موثّق في
  `docs/REMAINING_AFTER_V6_9.md` البند 1 ليُرفع بعد منح الصلاحية.
- **تقرير تسليم جديد**: `docs/REMAINING_AFTER_V6_9.md` — ما أنجزناه وما تبقى
  وكيف يُنفَّذ كل بند (تفعيل Actions، أول Release، OAuth، Chromaprint...).

### Tests
- `tests/test_mediapipe_optional.py` (2) + `tests/test_fonts.py` (4).
- الإجمالي: **373** (367 + 6).

## 💾 v6.9 — Persistent AI settings: save the Gemini key once, never retype it (2026-08)

### New
- **Auto-saved AI settings (the headline request)**: the WebUI now loads the
  saved Gemini key / backend / model / chunk size at startup and auto-saves
  them on every change (plus an explicit 💾 Save button). One paste, remembered
  forever — no more re-entering the key each session.
- **🔌 Test Connection button**: validates the Gemini key instantly from the UI
  (SDK or REST fallback) instead of discovering a bad key mid-processing.
- **Settings status card**: shows the masked key (`AIza********wxyz`) and where
  it came from (env var / encrypted store / api_config.json).
- Resolution order mirrors the CLI: `GEMINI_API_KEY` env → encrypted store →
  `api_config.json`. Env keys are never copied into the file.

### Error handling
- **Gemini key errors fail loudly**: `call_gemini` used to return `"{}"` on an
  invalid key, so runs died later on the confusing "no viral segments" error.
  It now raises a clear bilingual error naming the real cause.
- New Arabic hints for: invalid key, quota exhausted, PERMISSION_DENIED,
  generativelanguage errors, empty AI responses.
- **WebUI preflight**: missing-key runs fail fast with an actionable message;
  keys that don't look like Gemini keys (`AIza…`) log a warning.

### Security
- **API key no longer leaks into the visible log**: the echoed command line now
  masks `--api-key <value>` before printing.
- `app_version.py` bumped 0.9.0 → 6.9.0 (it had drifted from the changelog, so
  the auto-updater compared the wrong version).

### Tests
- 24 new tests (settings round-trip, atomic writes, env precedence, masking,
  connection-test guards, loud key errors, new hints). Total: 367.

## 🛠️ v6.8.1 — WebUI bug fixes, tests green on clean CI, dark theme (2026-08)

### Fixes
- **WebUI parameter order bug (critical)**: `run_viral_cutter` tail signature
  `(platform, polish, music, logo, metadata_gate, cookies, title_language)` did
  not match the `inputs=[...]` order sent by all three callers (Start, Review
  Segments render, Batch Queue). Effect: polish ran with `--music auto`,
  cookies/title-language selections were silently ignored. Signature now matches
  the UI order.
- **Stop button**: `kill_process` returned 6 values for 5 outputs (missing
  progress panel) — Gradio raised on every Stop click. Fixed.
- **Duplicate template handlers**: Save/Apply template buttons were wired twice
  (flat + nested payload formats) so both fired on one click. Consolidated to a
  single nested-format handler pair.
- **Subtitle Editor tab**: file list update was written into a status Textbox
  (Dropdown update into Textbox) and `current_json_path` was never set — "Render
  Selected" could never work. Added a real file Dropdown wired to
  project/subs/*_processed.json.
- **Gemini dual-SDK detection**: `import google.generativeai` required the parent
  `google` namespace package; now uses `importlib.import_module` so sys.modules
  fakes / hermetic environments resolve correctly.
- **yt-dlp optional import** in `scripts/download_video.py`: module imports
  without yt-dlp (friendly-error helpers still work); a clear RuntimeError is
  raised only when a download is attempted.
- **YouTube uploader**: missing-credentials error now raised *before* importing
  the optional google libs, so the actionable message appears in minimal envs.
- **on_source_change** no longer calls `refresh_projects()` twice.

### WebUI polish
- Rich header (version badge, feature list) actually rendered (was dead code).
- Full dark theme: Gradio 6 compatible (theme/css routed per version;
  `is_custom_theme` set for the mount path), dark blocks/inputs/tabs/radios.
- Progress/tasks panels restyled for the dark surface; dead duplicate
  `render_error_html` removed; orphaned headings removed from the log row.

### Tests
- All 343 tests pass with only `pytest` installed (CI parity), including the
  6 that previously failed on a clean environment.

## 📊 Risk Scorecard + Reused-Content Protection (v4)

### Novidades
- **Per-clip YouTube Risk Scorecard** (`scripts/risk_scorecard.py`): after every render, each clip gets a compliance report — `risk_scorecard.json` with axes: **reuse** (how identical the final clip still is to the raw source window, via dHash frame comparison — >70% = "reused content" risk), **first7s** (profanity inside the first 7 seconds = limited ads), **visual** (letterbox detection + local ONNX model hook), **overall** (low/medium/high/danger).
- **Publish gate**: `--risk-gate warn` (default) writes `publish_blocklist.json` listing clips that must NOT be uploaded; `--risk-gate block` stops the run. Standalone: `python scripts/risk_scorecard.py --project X --exit-on-blocked`.
- **Reused-content guide** in README_ar.md: practical rules to keep clips "transformative" (commentary, cropping, shortening, source choice).
- **13 testes novos** (test_risk_scorecard.py, incl. real ffmpeg dHash similarity and pillarbox detection). Total: 196 testes.

## 🔄 Auto-Updating Hate-Speech Word List

### Novidades
- **Lista de bloqueio com auto-atualização** (`scripts/safety_updater.py`): a lista oficial versionada (`safety_blocklist.json` no repositório) é baixada **automaticamente 1x por dia** durante o processamento (e por botão na WebUI). Novas palavras chegam ao usuário sem atualizar o programa.
- **Offline-safe**: falha de rede → usa o cache anterior (ou a lista embutida) e o pipeline continua.
- Merge automático das palavras remotas no filtro e no modo Bleep; `allow_terms` continua funcionando sobre elas.
- Flag `--safety-autoupdate on|off` (padrão: on). Cache local é git-ignored.
- Script de manutenção `scripts/export_blocklist_pack.py --version N` para publicar novas palavras a todos os usuários.
- **16 testes novos** (test_safety_updater.py, incluindo throttle diário e fallback offline). Total: 183 testes.

## 🔇 Bleep Mode + AI Policy Review (Safety Filter v2)

### Novidades
- **Modo `censor` (Bleep)**: em vez de remover o segmento inteiro, o ViralCutter agora **silencia apenas as palavras que violam políticas** (`volume=0` via ffmpeg na janela exata da palavra) e as mascara como `████` nas legendas — o clipe viral sobrevive. Mapa completo em `censor_map.json`.
- **Revisão contextual por IA (`--safety-ai`, padrão: on)**: os segmentos sobreviventes são enviados ao Gemini/G4F para uma segunda verificação de política do YouTube — captura discurso de ódio contextual sem palavras proibidas (ex.: "essa gente não merece existir"). Nunca quebra o pipeline: falha na API → filtro de palavras permanece.
- **Allowlist**: `safety_terms.json` agora aceita `allow_terms` para excluir falsos positivos da lista embutida (ex.: canal de história dizendo "منغولي").
- **Aba Review mostra segurança**: nova coluna "الأمان" (✅ / ⚠️ / 🔇 / 🤖⚠️) com o status de cada segmento.
- **WebUI**: seletor do modo Bleep + checkbox da revisão por IA.
- **29 testes novos** (`test_censor_engine.py`, `test_safety_ai.py`) incluindo teste real de muting com ffmpeg. Total: 167 testes.

## 🛡️ Safety Filter — YouTube Hate-Speech Shield

### Novidades
- **Filtro de segurança anti-strike (`scripts/safety_filter.py`)**: novo módulo que analisa o texto transcrito de cada segmento viral e **bloqueia antes do corte** os clipes com discurso de ódio, incitação à violência, xingamentos e assédio — a principal causa de strikes do YouTube ("الكلام الذي يحضّ على الكراهية").
- **100% local e multilíngue**: lista de termos em árabe (fusha + dialetos, incl. magrebino/argelino), inglês, português, francês, espanhol e turco. Normalização robusta contra evasões: diacríticos/tatweel árabe, dobra de alef/yá/taa-marbuta, remoção do artigo "ال" colado, leetspeak (@→a, 3→ع) e letras repetidas.
- **3 modos** (`--safety-mode`): `block` (padrão — remove o segmento), `flag` (mantém e anota para revisão), `off`. Severidade mínima configurável (`--safety-min-severity`).
- **Relatório detalhado** `safety_report.json` por projeto: veredito por segmento, termos encontrados, categoria, severidade e timestamp aproximado.
- **Termos personalizados**: arquivo `safety_terms.json` (raiz ou pasta do projeto) para estender a lista — ver `safety_terms.example.json`.
- **Prompt anti-violação**: `prompt.txt` agora instrui o LLM a nunca selecionar segmentos com discurso de ódio/violência (prevenção na fonte).
- **WebUI**: novo seletor "🛡️ Safety filter (hate speech)" com os 3 modos; CLI standalone: `python scripts/safety_filter.py --project <pasta> --mode block --in-place`.
- **i18n**: 14 novas chaves traduzidas (ar/en/pt/tr) + 25 testes novos (`tests/test_safety_filter.py`).

## Fixes for Manual/Raw JSON Input

### Core Functionality
- **Raw Segment Repair**: Implemented automatic detection and repair of segments that lack timestamp information (e.g. manually crafted JSON with just reference tags). The system now recalculates start/end times using the transcript alignment logic.
- **Duration Constraint Hardening**: The timestamp alignment logic now strictly enforces the user-defined `min_duration`, effectively extending segments that the AI might have outputted as too short.

## Suporte a GGUF e Ajustes de Link

### Novidades
- **Suporte a GGUF**: colocado suporte a gguf para llm local.
- **Link Público**: ajustado diretórios de link público.

## Melhorias de Qualidade de Vídeo, Legendas e Processamento

### Novidades

- **Aprimoramento de prompt para LLM**: melhorias no prompt para permitir que o modelo de linguagem compreenda melhor o contexto do conteúdo.
- **Aprimoramento na detecção facial**: melhorias na identificação de rostos quando várias pessoas estão falando simultaneamente.
- **Seleção de Qualidade de Vídeo**: agora é possível escolher a qualidade desejada para download de vídeos (Melhor, 1080p, 720p, 480p) diretamente pela WebUI ou CLI, permitindo otimizar entre velocidade e uso de armazenamento.
- **Controle de Legendas do YouTube**: adicionada a opção de ignorar o download de legendas oficiais do YouTube, permitindo forçar uma nova transcrição via Whisper, se desejado.
- **Suporte a VTT**: o script de transcrição foi aprimorado para oferecer suporte a arquivos de legenda `.vtt` para alinhamento, garantindo maior compatibilidade.
- **Tradução de legendas em JSON com destaque palavra por palavra**: adicionada a tradução de legendas no formato JSON, permitindo highlight e sincronização word-by-word em outro idioma durante a exibição.

### Melhorias e Otimizações

- **yt-dlp mais robusto**: corrigidos problemas em que downloads de vídeo estavam sendo salvos como “Unknown_Video” e exibiam progresso incorreto. Também foram adicionados logs de progresso mais precisos e suporte aprimorado ao download de legendas.
- **Otimização de Legendas do YouTube**: quando legendas do YouTube estão disponíveis, o sistema agora faz o download automático e as utiliza apenas para alinhamento, pulando o processo pesado e demorado de transcrição. Isso acelera significativamente o processamento de vídeos que já possuem legendas.


## Active Speaker & Face Controls

### Controles Avançados de Face e Falante Ativo
- **Filtros de Face**: Controle granular para ignorar rostos pequenos, definir limite de confiança minimiza falsos positivos e "Zona Morta" para estabilizar a câmera.
- **Experimental: Active Speaker**: Novo modo experimental que tenta focar na pessoa que está falando (detecção de boca aberta e movimento), em vez de sempre dividir a tela.
- **Legendas**: Opção para remover pontuação automaticamente.

## Editor de Legenda JSON

### Funcionalidades
- **Editor de Legendas**: Adicionado um editor de legendas simples, dentro das limitações do Gradio, para corrigir erros de ortografia ocorridos durante o uso do WhisperX.

### Correções
- **Geral**: Alguns Fix Colab e melhorias na geração de viral segments.

## Gradio WebUI & UV Installation

### Nova Interface Web (Gradio)
- **OpusClip Inspired**: Nova interface gráfica construída com Gradio, inspirada no design do OpusClip, oferecendo uma experiência de usuário moderna e intuitiva.
- **Funcionalidades da UI**: Ajustes completos para garantir que todas as funcionalidades da ferramenta estejam acessíveis e operantes através da nova interface.

### Instalação e Infraestrutura
- **Instalação via UV**: Criação de script `.bat` para instalação otimizada de dependências utilizando o `uv`, acelerando o processo de setup.
- **Fixes Gerais**: Correções em diversos componentes que estavam quebrados ou instáveis, garantindo maior estabilidade na execução via UI.

## WebUI 2.0 & Enhanced Configuration

### WebUI Overhaul
- **Dark & Modern UI**: Interface completamente redesenhada com tema escuro e layout em grid responsivo (estilo Opus.pro) para a galeria de vídeos.
- **Dynamic Configuration**: Componentes da interface agora reagem dinamicamente à escolha do Backend de IA, atualizando automaticamente a lista de modelos disponíveis e o tamanho sugerido de chunk.
- **Improved Controls**: Controle granular sobre `Face Detect Interval`, `Skip Prompts`, e `Chunk Size` diretamente na interface web.
- **Refactoring**: Código da WebUI refatorado e modularizado (`library.py` separado do `app.py`) para melhor manutenção.

### Core & CLI
- **Arguments Expansion**: `main_improved.py` agora aceita argumentos de linha de comando para `--chunk-size` e `--ai-model-name`, permitindo override total da configuração.
- **Script Update**: `create_viral_segments.py` atualizado para respeitar os parâmetros passados via CLI, priorizando-os sobre o arquivo de configuração.

## Fix 2 faces

### Melhorias na Detecção Facial e Layout
- **Consistência Visual (2 Faces)**: Implementada lógica para "travar" a identidade dos rostos nas posições superior e inferior, impedindo que os participantes troquem de lugar durante o vídeo.
- **Lógica de Fallback Inteligente**: Caso o rosto não seja detectado no frame atual, o sistema agora tenta recuperar a posição baseada no frame anterior, posterior ou na última coordenada válida conhecida.
- **Intervalo de Detecção Personalizável**: Adicionada configuração para o usuário escolher a frequência da varredura facial, permitindo otimizar o tempo de renderização.

### Correções de Legendas
- **Correção de Sobreposição**: Resolvido bug onde legendas apareciam sobrepostas em momentos de fala rápida.
- **Refinamento de Centralização (2 Faces)**: Ajustes adicionais no cálculo de posição para garantir que a legenda fique perfeitamente centralizada no modo dividido.

## Atualizações Anteriores

### Refatoração e Melhorias de Código
- **Refatoração do Script Principal**: Criação e aprimoramento do `main_improved.py` para melhorar a estrutura e manutenibilidade do pipeline de processamento.
- **Padronização de Código (Inglês)**: Tradução completa de nomes de variáveis, funções e comentários internos para inglês, visando compatibilidade com padrões internacionais e colaboração open-source, mantendo logs de saída com suporte a i18n (`en_US`/`pt_BR`).
- **Ajuste de Diretórios**: Reorganização da estrutura de pastas e caminhos de saída para maior organização dos arquivos gerados.

### Configuração e IA
- **Integração Multi-LLM**: Implementação de suporte ao **g4f** (GPT-4 Free) e **Google Gemini**.
- **API Config**: Centralização das chaves e seleção de modelos no novo arquivo `api_config.json`, permitindo troca rápida de provedor de IA sem alterar o código.
- **Gerenciamento de Prompts**: Criação do arquivo `prompt.txt` para edição fácil do prompt do sistema.

### Legendas e Transcrição (Whisper)
- **Correções no Whisper**: Solução robusta para erros de `unpickling`, conflitos de DLLs (`libprotobuf`, `torchaudio`) e detecção de GPU.
- **Otimização do Fluxo (Slicing)**: O vídeo original é transcrito apenas uma vez. Os cortes reutilizam o JSON original, eliminando a re-transcrição e acelerando o processo.
- **Posicionamento de Legendas**: Correção da lógica de alinhamento para centralização no modo "2-face".

### Processamento de Vídeo e Detecção Facial
- **Novo Motor: InsightFace**: Adição da biblioteca `InsightFace` como motor de detecção facial de alta precisão.
- **MediaPipe**: Manutenção e correção de erros no fallback para o MediaPipe.
- **Limpeza de Logs**: Redução da verbosidade dos logs do FFmpeg no console.
## ⚙️ v6 — Distribution + Visual Safety + Pro Editing + Reliability (`f37e007`)

### Novidades
- **Pacote único (Roadmap 1.1)**: `packaging/viralcutter.spec` (PyInstaller onefile) + scripts de build Windows/Linux/macOS.
- **Auto-update (1.2)**: `scripts/auto_updater.py` verifica GitHub Releases; versão central em `app_version.py` (0.9.0); `--check-updates`.
- **Instaladores Linux/macOS (1.3)**: `install_linux.sh`, `install_macos.sh`, `run.sh`.
- **Verificação visual ONNX (2.1)**: `scripts/visual_check.py` (NudeNet-lite) integrado ao hook `visual_model_path` do risk scorecard — frames reais por clipe, score 0-100, `--auto-download-visual`.
- **Porta de publicação obrigatória (2.2)**: `scripts/upload_gate.py` recusa upload de clipes em publish_blocklist / safety_report / metadata inválida; adapters YouTube/TikTok/Instagram já passam pela porta (SDKs a ligar).
- **Metadata compliance (2.4)**: `scripts/metadata_compliance.py` (hashtags banidas, claims médicas/financeiras, clickbait, keyword stuffing).
- **Edição profissional (3.1–3.4)**: `scripts/polish.py` — jump cuts (silêncio+fillers), punch-in zoom, música de fundo com auto-duck, watermark + intro/outro; legendas re-sincronizadas (retime) e `burn_subtitles` prefere `final_polished/`.
- **Resumo crash-safe (4.2)**: `scripts/checkpoint.py` (`--checkpoint on`).
- **OOM Guard (4.1)**: `scripts/oom_guard.py` cai de modelo automaticamente.
- **Chave API segura (4.4)**: `scripts/secure_config.py` (env → Fernet → plaintext).
- **Crash reports privados (4.5)**: `scripts/crash_report.py` (opt-in).
- **CI real (4.3)**: ffmpeg no workflow + `tests/test_ci_smoke.py` com vídeo real.
- **Títulos A/B (5.3)**: `alt_titles`/`alt_captions` no prompt e nos segmentos.
- **Testes**: 196 → 286 (reais com ffmpeg).

## 🟣 v6.1 — Platform templates + verified build + hardening (2026-08-04)

### Novidades
- **Platform templates (Roadmap 5.2)**: `scripts/platform_templates.py` + `--platform {yt_shorts,tiktok,reels,yt_standard}` — define duration defaults/aspect per platform; saved to process_config.json.
- **Build verificado (1.1)**: `dist/ViralCutter` onefile (~300 MB) built & tested with PyInstaller 6.21 on Linux. Fixed a spec path bug (`ROOT`).
- **Hardening**: `transcribe_video.py` now imports torch optionally (binary runs without whisperx/torch); `doctor.py` checks onnxruntime/cryptography; requirements.txt += onnxruntime, cryptography.
- **i18n**: new v6 keys translated (ar_SA) + pt_BR/tr_TR synced.
- **WebUI plumbing**: `webui/pipeline.py` supports the v6 flags (Gradio fields still pending).
- **Testes**: 286 → **304**.

## 🟢 v6.2 — Ready-to-run fixes (2026-08-04)

- **Full install**: `requirements-transcribe.txt` (whisperx+torch) + `requirements-upload.txt` (YouTube OAuth); installers ask to install them.
- **Clear failure instead of silent placeholder**: transcription raises actionable ImportError when whisperx/torch missing; `--allow-placeholder-transcription` for testing only.
- **Real YouTube uploader**: full OAuth flow (client_secrets → token in ~/.viralcutter/yt_token.json), resumable upload, default privacyStatus=private.
- **WebUI fixed (was crashing on startup)**: implemented render_progress_html/render_tasks_html/render_error_html, GEMINI_MODELS/G4F_MODELS/get_local_models, apply_face_preset/apply_experimental_preset, template_choices/save_template/load_templates, subtitle-editor buttons + current_json_path; added visible v6 fields (platform/polish/music/logo/metadata gate).
- **Auto-update armed**: falls back to latest git tag when no Release exists; tag v0.9.0 pushed.
- doctor.py checks whisperx/torch; README_ar quickstart "3 steps to full pipeline".
- Testes: 304 → 309.

## 🔐 v6.3 — YouTube download UX (private / age-restricted videos)

- `--cookies-from-browser chrome|firefox|edge|...` + `--cookies file.txt` (yt-dlp auth) for private/age-restricted downloads.
- Friendly error messages instead of raw tracebacks: private video / age-restricted / unavailable / removed / invalid URL → actionable guidance + clean exit(1).
- Tests: 309 → 313.

## 🔐 v6.3b — Interactive cookies retry for private videos

- CLI: when a download fails as "private / age-restricted" and the user runs interactively, ViralCutter now ASKS "Retry using your Chrome browser cookies? (yes/no)" and retries automatically with --cookies-from-browser chrome. (TTY-only — the WebUI never hangs on a prompt.)
- WebUI: new "🔒 YouTube login (cookies)" dropdown (Chrome/Edge/Firefox) wired through build_command.
- Tests: 313 → 314.

## 🔧 v6.3c — Windows crash fixes (Chrome cookie noise + input_video=None)

- download_video: title extraction no longer forces Chrome cookies (removed the
  "Could not copy Chrome cookie database" noise on Windows, yt-dlp#7271) —
  cookies are used only when the user asks (--cookies-from-browser/--cookies).
- download_video: safety net — after all attempts, if the video file is missing/empty,
  fail loudly instead of returning a bogus path.
- main_improved: guard against input_video=None after a failed download → clean
  error message + exit(1) instead of `os.path.dirname(None)` TypeError.
- Tests: 314.

## 🐛 v6.3d — CRITICAL: fix download() returning None (Windows crash root cause)

- The v6.3 helper insertion accidentally nested the main download block inside
  `_print_friendly_and_exit` — download() silently returned None and the pipeline
  crashed at `os.path.dirname(None)`. download_video.py fully rewritten with the
  correct structure.
- Regression tests added: private video → AuthNeededError (never None); invalid URL → SystemExit.
- main_improved: guard placed BEFORE os.path.dirname + version banner at startup.
- Tests: 314 → 316.

## 🎨 v6.4 — Arabic WebUI: organization + performance + error reports

- **Error report display**: raw 30-line traceback tails are now summarized into
  scannable cards — title (ERROR line) + Arabic friendly hint (private video /
  whisperx / ffmpeg / 429 / OOM / cookies…) + collapsible technical details +
  exit code badge. (webui/utils.py summarize_error + render_error_html).
- **Performance**: logs list capped at 1000 lines (was unbounded O(n²) joins).
- **Organization**: v6 settings grouped into a labeled Accordion
  "✨ المونتاج الاحترافي والمنصات (v6)" with sub-sections (platform/publishing,
  editing quality, YouTube login).
- **Arabic**: all new WebUI labels translated to ar_SA (27 keys) + pt_BR/tr_TR synced.
- Tests: 316 → 322.

## 🤖 v6.5 — Gemini SDK fix (both libraries supported)

- create_viral_segments: works with EITHER `google-generativeai` (classic) or
  `google-genai` (new SDK) — auto-detects; requirements.txt now installs both.
- Actionable ImportError message (Arabic hint added to the WebUI error cards).
- WebUI error hints now match the real error line first (no more wrong hints
  from older log lines).
- Tests: 322 → 325.

## 🇩🇿 v6.5b — CRITICAL: Arabic video titles erased (folder collapse)

- sanitize_filename stripped non-Latin scripts (cp1252/ascii fallback) → Arabic
  titles became "" → every Arabic-titled project collapsed into VIRALS/ and
  overwrote input.mp4. Now keeps Unicode letters (Arabic/CJK/Latin), strips only
  reserved chars/emojis; empty → "Unknown_Video". + regression tests.
- Tests: 325 → 329.

## 🇩🇿🎬 v6.6 — Arabic titles + A/V sync fix + developer report

- **Arabic titles**: `--title-language ar` forces ALL AI output (titles, alt_titles,
  reasoning, captions) into Arabic regardless of video language; WebUI dropdown
  "🌐 لغة العناوين والكابشن" (auto/ar/en/fr/es/pt/de/tr).
- **A/V sync fix**: edit_video mux now uses -shortest + aresample=async=1 (audio
  follows the OpenCV-processed video timeline) + fps guard for VFR/broken metadata.
- **docs/ROADMAP_REPORT.md section 9**: developer handover report — what was fixed,
  prioritized remaining work (mediapipe guard, fonts, first Release, CI permission,
  TikTok/IG OAuth, deeper sync rework).
- Tests: 329 → 335.

## 🎙️ v6.7 — Whisper model fallback (large-v3-turbo unsupported on older faster-whisper)

- transcribe_video: resolve_model_candidates() — if the requested model (e.g.
  large-v3-turbo) is rejected as an invalid size, fall back to large-v3 → medium
  with a clear console note, instead of crashing.
- WebUI error hint for "invalid model size" (Arabic): update faster-whisper or
  pick large-v3/medium — and hints now correctly prefer the real error line.
- Tests: 335 → 339.

## 🛡️ v6.7b — Broken optional stack no longer kills the WebUI

- whisperx/torch import guards widened (any Exception, not just ModuleNotFoundError):
  a transformers/tokenizers version conflict no longer crashes the whole app/WebUI.
- subtitle_editor imports main_improved lazily (faster WebUI startup, less fragile).
- Tests: 339 → 341.

## 🔧 v6.7c — numpy<2 pin (whisperx/pyannote break on NumPy 2.x)

- requirements.txt + requirements-transcribe.txt now pin numpy<2 (np.NaN was
  removed in NumPy 2.0 → old pyannote.audio crashes).
- Arabic WebUI hint for the numpy conflict.
- Tests: 341 → 342.

## 🛠️ v6.8 — 403 handling + specific whisperx/torch diagnostics

- download_video: on HTTP 403 (Forbidden) → clear Arabic-adjacent guidance AND an
  automatic retry with alternative YouTube player clients (android/tv/web_safari).
- transcribe_video: the "stack missing" error now names WHICH import failed
  (whisperx vs torch) with a check command for each.
- WebUI hints: 403/Forbidden → update yt-dlp / use cookies / retry later.
- Tests: 342 → 343.


## 🛡️ v7.13.0-pro — الحماية الآلية من تكرار ومخاطر YouTube (2026-08-22)

### New — حارس المحتوى وقاعدة provenance
- أضيف `scripts/content_guard.py` بقاعدة SQLite محلية تحفظ بصمات الملفات، هوية المصدر، نافذة المقطع، المنصة، حالة النشر، والعنوان دون تخزين الوسائط أو أسرار OAuth.
- تُحجب نافذة المصدر المنشورة سابقاً، وملف الفيديو المنشور سابقاً، والتداخل الشديد بين مرشحي الدفعة، وحدّ الإنتاج الآلي البالغ 8 مقاطع ناجحة من المصدر نفسه خلال 24 ساعة.
- تُحجب حالات `block` و`review` الدلالية التي تحتوي مؤشرات تحريض أو إقصاء أو تشبيه مهين قبل القص، مع تقرير `content_guard_report.json`.

### New — قاطع دائرة القناة
- تُسجل أخطاء YouTube الصريحة المتعلقة بإرشادات المنتدى أو الإنذارات أو تعليق/إغلاق القناة في `channel_incidents`.
- عند وجود حادثة مقفلة يتوقف الرفع قبل OAuth في المحاولات التالية. لا تُقفل القناة تلقائياً بسبب 429 أو أخطاء المصادقة العادية.
- تظهر حالة القاطع في بطاقة OAuth وتقرير المشروع ورسائل الرفع.

### Tests and documentation
- أضيفت اختبارات للتكرار الحرفي وعبر المشاريع ونافذة المصدر والتداخل الدلالي وحد النشر وقاطع القناة والتوقف قبل OAuth.
- أضيف `docs/AUTOMATIC_YOUTUBE_SAFETY_AR.md` و`docs/RELEASE_NOTES_7.13.0_PRO_AR.md`.
- اجتاز regression الكامل **691 اختباراً**، مع `ruff` و`compileall`.
