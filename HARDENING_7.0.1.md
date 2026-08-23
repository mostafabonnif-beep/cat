# ViralCutter 7.0.1-pro — ملخص التعديلات الأمنية والتحسينات

هذه النسخة مبنية على النسخة المرسلة `7.0.0-pro` + جولة تقوية شاملة (مراجعة أمنية + إصلاحات موثوقية + تنظيف جودة).

## 🔴 أمن (الأهم)

### 1. الواجهة لم تعد تخدم جذر المشروع
- قبل: `allowed_paths` كان يخدم `VIRALS/ + جذر المشروع + المجلد الحالي` → أي شخص على الشبكة يقدر يجيب `api_config.json` (فيه مفتاح Gemini) وملفات السجلات والتوكنات عبر `/file/...`.
- بعد: `_allowed_dirs()` يخدم `VIRALS/` فقط. لإضافة مجلدات: `VIRALCUTTER_EXTRA_STATIC_DIRS` (مفصولة بـ `;` أو `:`).

### 2. الربط على localhost افتراضياً
- قبل: الواجهة كانت تربط على `0.0.0.0:7860` بلا أي حماية → أي جهاز على الشبكة المحلية يفتحها.
- بعد: افتراضياً `127.0.0.1`. للتغيير: `VIRALCUTTER_HOST`.
- حماية إضافية اختيارية بكلمة مرور: `VIRALCUTTER_WEBUI_USER` + `VIRALCUTTER_WEBUI_PASSWORD` (تعمل في مساري Gradio و Uvicorn معاً).

### 3. `/export_xml_api` — منع الهروب من المجلد
- معامل `project` يُقصّ ويُتحقق منه أنه داخل `VIRALS/` (commonpath) — لا مزيد من `../` أو مسارات مطلقة.

### 4. إصلاح XSS في معرض المقاطع (library.py)
- كل النصوص القادمة من المستخدم/الملفات (العناوين، الدرجات، أسماء الملفات، رسائل الخطأ) تُمرر عبر `html.escape`.
- لم تعد تظهر روابط `/file/` بمسارات مطلقة لملفات خارج المجلدات المسموحة.

### 5. إصلاح كسر مفتاح Gemini (مهم جداً)
- النسخة pro أزالت `--api-key` من الـ argv — صحيح أمنياً — لكنها نسيت توصيل المفتاح للـ CLI إطلاقاً → **مفتاح Gemini ما كانش يوصل للـ CLI من الويب (خطأ صامت)**.
- الحل: الواجهة الآن تحقن المفتاح في بيئة العملية الفرعية عبر `VIRALCUTTER_GEMINI_KEY` (بدون ما تلغي مفتاحاً صدّره المستخدم بنفسه).

### 6. التخزين المشفر مفضّل في الواجهة
- عند ضبط `VIRALCUTTER_CONFIG_PASSPHRASE`: المفتاح يُحفظ مشفراً (`api_config.secure.json`) و`api_config.json` يبقى نظيفاً.
- أُصلح **bug في 7.0.0-pro**: `_encrypt_blob` كان يستدعي `_xor_encrypt` المحذوفة → NameError. الآن يفشل بأمان برسالة واضحة (fail-closed حقيقي).

### 7. `api_config.json` أصبح gitignored
- المفاتيح الحقيقية لا يجب أن تصل إلى git. أُضيف `api_config.example.json` للنسخ.
- ⚠️ عند الدمج في مستودعك الحالي نفّذ: `git rm --cached api_config.json`

### 8. المحدّث التلقائي (auto-updater) يتحقق من التحميلات
- قبل: يحمّل وينفذ أي exe من Releases بدون أي تحقق (خطر supply-chain).
- بعد: يرفض التثبيت ما لم يطابق الملف `checksums.txt`/`SHA256SUMS` المنشور مع الإصدار. بديل صريح: `VIRALCUTTER_ALLOW_UNSIGNED_UPDATE=1` (غير موصى به). أُزيل الالتقاط الأعمى لـ"أول أصل".

### 9. `torch.load` لم يعد يعطّل حماية PyTorch
- قبل: monkeypatch عالمي يفرض `weights_only=False` → نموذج مسموم = تنفيذ كود عشوائي.
- بعد: يحاول `weights_only=True` + safe globals أولاً؛ المسار غير الآمن يتطلب `VIRALCUTTER_ALLOW_UNSAFE_LOAD=1` صراحة.

## 🟡 موثوقية

### 10. ffmpeg لم يعد يفشل بصمت
- `generate_short_fallback`: موت الأنبوب (pipe) يوقف التغذية، ويُفحص كود الخروج، وعند الفشل يُرمى خطأ واضح مع آخر أسطر stderr بدل فيديو مبتور "ناجح".

### 11. إصلاح نطاق `api_config` في main_improved.py
- قبل: عند استئناف مشروع موجود (`viral_segments.txt`) كان `api_config` غير معرّف → `--ai-backend` يُتجاهل بصمت، والبيانات المسجلة خاطئة.
- بعد: يُحمَّل مرة واحدة بلا شرط.

### 12. إعادة المحاولة الذكية
- أخطاء المدخلات (`ValueError`/`TypeError` مثل `--chunk-size` خاطئ) لا تعيد تشغيل البايبلاين كامل — تخرج فوراً. `_safe_chunk_size` يمنع الانهيار أصلاً.

### 13. escaping صحيح لمسارات الفلاتر (subtitles)
- `'` و `:` تُهرب الآن في `subtitles=` — مشروع باسم فيه علامة اقتباس ما عادش يكسر الترجمة.

## 🧪 جودة واختبارات

### 14. الاختبارات: 572 ناجح / 0 فاشل (كان: 2 فاشل)
- `test_preflight.py`: أصبح حتمياً (لا يعتمد على numpy المثبت في البيئة).
- `test_pipeline.py`: حُدّث ليأكد أن المفتاح لا يظهر في argv إطلاقاً.
- `auto_updater` tests: 3 اختبارات جديدة (رفض بلا checksum، رفض عند تطابق خاطئ، عدم التقاط أصول منصات أخرى).
- شارات README حُدّثت للرقم الصحيح (572).

### 15. ruff نظيف على كامل المشروع
- أُصلحت مشاكل lint الموجودة مسبقاً في ملفات pro الجديدة (pipeline_engine, editor_core, render_queue, test_pro_components, secure_config) — كانت مخالفة لمعيار المشروع نفسه.

### 16. توحيد الإصدار
- `app_version.py` + `pyproject.toml` + `uv.lock` = `7.0.1-pro` (كان: 7.0.0-pro / 6.16.1 / 6.14.0 — ثلاثة أرقام مختلفة!).

### 17. الإسناد (Attribution)
- أُضيف الائتمان للمشروع الأصلي Rafael Godoy / RafaelGodoyEbert في README.md وREADME_en وREADME_ar وREADME_PRO — واجب روح GPL-3.0.

---

## متغيرات البيئة الجديدة
| المتغير | الوظيفة | الافتراضي |
|---|---|---|
| `VIRALCUTTER_HOST` | عنوان الربط | `127.0.0.1` |
| `VIRALCUTTER_EXTRA_STATIC_DIRS` | مجلدات إضافية للعرض | (فارغ) |
| `VIRALCUTTER_WEBUI_USER` / `_PASSWORD` | دخول بسيط للواجهة | (معطّل) |
| `VIRALCUTTER_ALLOW_UNSIGNED_UPDATE` | قبول تحديثات بلا checksum | (معطّل) |
| `VIRALCUTTER_ALLOW_UNSAFE_LOAD` | السماح بتحميل نماذج torch غير آمن | (معطّل) |
| `VIRALCUTTER_CONFIG_PASSPHRASE` | تشفير المفتاح عند الحفظ | (فارغ) |

## عند الدمج في المستودع
1. `git rm --cached api_config.json` (المفتاح الحقيقي يجب ألا يُتتبع).
2. إنشاء release جديد بنسخة 7.0.1-pro **مع ملف `checksums.txt`** (وإلا المحدّث التلقائي سيرفض التحديث).
3. مراجعة سجل التغييرات `CHANGELOG_PRO.md` ثم `git commit` بـ `7.0.1-pro: security hardening`.
