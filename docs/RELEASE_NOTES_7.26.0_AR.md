# ملاحظات إصدار OUSSAMA Cutter 7.26.0-pro

## فحص جودة الصوت الاحترافي

يضيف هذا الإصدار Audio QC محلياً باستخدام أدوات FFmpeg الموجودة أصلاً في المشروع. بعد اكتمال حرق الترجمات وإعادة التأطير، يفحص الملفات النهائية القابلة للنشر ويكتب `audio_qc_report.json` داخل مجلد المشروع.

يقيس التقرير وجود المسار الصوتي، مدة الملف، integrated loudness عبر `loudnorm`، true peak، ونسبة الصمت عبر `silencedetect`. لكل ملف حالة واضحة:

| الحالة | المعنى |
|---|---|
| `pass` | القياسات الأساسية متاحة ولا توجد ملاحظة تحتاج مراجعة |
| `review` | الصوت موجود لكن loudness أو true peak أو الصمت يحتاج مراجعة بشرية |
| `block` | الملف ناقص أو بلا صوت أو تعذر التحقق منه |

تبقى المعالجة المحلية مستمرة في الوضع الافتراضي `--audio-qc-gate warn`، لكن publish_panel يمنع الرفع الحقيقي للملف الذي لا يملك نتيجة `pass`. يسمح Dry Run بالتجربة دون رفع فعلي. يمكن جعل الفحص صارماً وإيقاف الـpipeline عند أول نتيجة غير ناجحة عبر:

```powershell
python main_improved.py --audio-qc on --audio-qc-gate block
```

لإيقاف الفحص صراحةً في تشغيل محلي خاص:

```powershell
python main_improved.py --audio-qc off
```

## تشغيل الفحص يدوياً

```powershell
cd D:\SS
.\.venv\Scripts\python.exe -m scripts.audio_qc --project "D:\SS\VIRALS\اسم_المشروع"
```

يعيد الأمر رمز خروج `0` عند `pass` و`2` عند `review` أو `block`، ويطبع مسار التقرير. يمكن تثبيت entry point نفسه من package باسم `oussama-cutter-audio-qc` عند استخدام تثبيت editable أو package مناسب.

## جاهزية النشر

يظهر Audio QC داخل `project_report.json` و`project_report.html` وداخل شاشة تدقيق جاهزية النشر في WebUI. عند الرفع الحقيقي يُعاد فحص الملف إذا لم يوجد تقرير حديث له، ثم يوقف الحاجز الملف قبل OAuth أو YouTube API إذا كانت النتيجة `review` أو `block`. لا يؤثر ذلك في تصفح الملفات أو Dry Run.

عند إعادة القص، يمسح النظام `audio_qc_report.json` مع artifacts اللاحقة حتى لا تُستخدم قياسات قديمة مع قائمة مقاطع جديدة.

## التحقق

أضيفت اختبارات parsing وthresholds والكتابة الذرية والتقرير وcommand builder وحاجز الرفع. نجحت اختبارات Audio QC والتقرير والرفع والـpipeline، كما تم اختبار ملف MP4 فعلي مولد بـFFmpeg على الخادم وكانت النتيجة `pass` مع قياسات loudness وtrue peak حقيقية. يبقى اختبار Windows RTX 3060 الكامل مطلوباً على جهاز المستخدم.
