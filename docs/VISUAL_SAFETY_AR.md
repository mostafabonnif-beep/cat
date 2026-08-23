# الفحص البصري للمحتوى الحساس في OUSSAMA Cutter

## ما الذي أُضيف؟

أصبح OUSSAMA Cutter يملك طبقة فحص بصري محلية تعمل بعد إخراج المقاطع النهائية وقبل بوابة النشر. تستخرج الطبقة عدداً من الإطارات من كل مقطع، تمررها إلى نموذج ONNX محلي، وتحفظ نتيجة كل إطار داخل `risk_scorecard.json`. لا تُرسل الصور أو الفيديو إلى خدمة خارجية أثناء الفحص المحلي.

> النسخة الحالية من النموذج المدمج اختيارياً مخصصة لاكتشاف المحتوى الجنسي/العري والمحتوى الرسومي من خلال ملف ONNX متوافق مع تعريفاته. لا ينبغي اعتبارها كاشفاً كاملاً للعنف أو الكراهية أو السياق الساخر؛ لذلك تبقى مراجعة النص والصورة النهائية ضرورية.

## العلاقة مع خط الأمان

ترتيب التنفيذ هو:

```text
الفيديو → WhisperX والنص → فلتر السلامة النصي والدلالي → القص والمونتاج
        → إخراج المقاطع النهائية → الفحص البصري + بطاقة المخاطر
        → بوابة الرفع → YouTube أو المنصة المختارة
```

تكتب بطاقة المخاطر المحاور التالية لكل مقطع: مخاطر النص، أول سبع ثوانٍ، تشابه المصدر، الفحص البصري، والميتاداتا. إذا ارتفعت نتيجة الفحص البصري فوق العتبة، يظهر سبب الحجب في `risk_scorecard.json` و`risk_report.html` و`publish_blocklist.json`. وتستخدم `upload_gate.py` هذه الملفات قبل أي طلب رفع.

عند اختيار الوضع الإلزامي `on` مع سياسة `block`، فإن غياب النموذج أو فشل تحميله يؤدي إلى **فشل مغلق**؛ أي يتوقف مسار النشر بدلاً من الادعاء بأن الفيديو فُحص. أما الوضع `auto` فيفحص عندما يكون النموذج موجوداً، ويُسجل تحذيراً واضحاً إذا لم يكن موجوداً.

## التثبيت الأول على Windows

من PowerShell داخل مجلد المشروع:

```powershell
cd D:\SS
.\.venv\Scripts\python.exe -m scripts.visual_check --download
```

ينزل الأمر النموذج إلى:

```text
D:\SS\models\nudenet_lite.onnx
```

لا تستخدم التنزيل التلقائي في بيئة إنتاج مغلقة إلا بعد مراجعة مصدر النموذج ورخصته. حزمة NudeNet المنشورة على GitHub تحمل رخصة AGPL-3.0، ولذلك يجب مراجعة التزامات الرخصة قبل توزيع نسخة تجارية أو مدمجة مع البرنامج [1].

## الاختبار اليدوي قبل تشغيل خط كامل

اختبر مقطعاً واحداً أولاً:

```powershell
.\.venv\Scripts\python.exe -m scripts.visual_check `
  --video D:\SS\VIRALS\demo\final\000_clip.mp4 `
  --frames 6 --json
```

يجب أن ترى `available: true` و`graphic_score` ونتيجة كل إطار. إذا ظهر `available: false` فلا يعتبر ذلك فحصاً ناجحاً؛ راجع مسار النموذج و`onnxruntime` وFFmpeg.

## التفعيل من CLI

للتشغيل المرن الذي يفحص عند وجود النموذج:

```powershell
.\.venv\Scripts\python.exe main_improved.py `
  --project-path D:\SS\VIRALS\demo `
  --risk-scorecard on `
  --visual-check auto `
  --visual-gate warn `
  --visual-frames 6
```

للنشر المحافظ، استخدم الوضع الإلزامي مع الحظر:

```powershell
.\.venv\Scripts\python.exe main_improved.py `
  --project-path D:\SS\VIRALS\demo `
  --risk-scorecard on `
  --visual-check on `
  --visual-gate block `
  --visual-frames 6 `
  --risk-gate block
```

يمكن تحديد نموذج مختلف متوافق مع ONNX وملف metadata جانبي:

```powershell
.\.venv\Scripts\python.exe main_improved.py `
  --project-path D:\SS\VIRALS\demo `
  --visual-check on `
  --visual-model D:\Models\safety_classifier.onnx
```

ويجب أن يكون الملف الجانبي بجانب النموذج بالاسم نفسه مع `.json`، مثلاً:

```json
{
  "input_size": 320,
  "classes": ["neutral", "sensitive", "graphic"],
  "graphic": ["sensitive", "graphic"]
}
```

## التفعيل من WebUI

افتح قسم **الأمان** ثم لوحة **الفحص البصري للمحتوى الحساس**. الإعدادات المقترحة هي:

| الحقل | القيمة المقترحة | الوظيفة |
|---|---|---|
| حالة الفحص البصري | `تلقائي` بعد تثبيت النموذج، أو `تشغيل إلزامي` للنشر المحافظ | يحدد هل يعمل الفحص وهل يفشل المسار عند غياب النموذج |
| سياسة نتيجة الفحص | `تحذير` أثناء الاختبار، ثم `حظر` قبل النشر العام | يحدد هل تستمر المعالجة عند نتيجة مرتفعة |
| عدد الإطارات | 4 للمقاطع العادية، و6–8 للمشاهد السريعة | يوازن بين الزمن وتغطية المقطع |
| مسار النموذج | فارغ لاستخدام `models/nudenet_lite.onnx` أو مسار مخصص | يسمح باستخدام نموذج محلي متوافق |
| تنزيل النموذج | مغلق افتراضياً | يمنع التنزيل الشبكي غير المقصود |

بعد المعالجة، افتح **Risk Scorecard** في تبويب المراجعة. لا ترفع مقطعاً يحمل `high` أو `danger` أو `manual_review`، ولا ترفع عند ظهور تنبيه أن الفحص البصري غير متاح.

## حدود الدقة والسياسة

الفحص البصري عينة من الإطارات وليس مشاهدة بشرية لكل فريم، وقد يفوّت مشهداً سريعاً أو يعطي إنذاراً كاذباً. كما أن سياسة YouTube تتعلق بالسياق والنية، لا بالتصنيف البصري وحده؛ فسياسة المحتوى العنيف تمنع المحتوى الرسومي المقصود به الصدمة أو التشجيع على العنف، مع وجود استثناءات سياقية محدودة [2]. وتمنع سياسة العري المحتوى الصريح المخصص للإثارة الجنسية، وقد تنطبق على المحتوى الحقيقي والمصطنع والرسوم والألعاب [3]. لذلك يعمل الفحص البصري كـ **إشارة قرار محافظة** وليس كضمان قبول من YouTube.

## فحوص القبول قبل الاعتماد

يجب اختبار النموذج على مجموعة محلية متنوعة تشمل محتوى محايداً، مشاهد ليلية، انتقالات سريعة، فيديو عمودي، صوراً ثابتة، مقابلات متعددة الأشخاص، ومشاهد حساسة معروفة. سجّل النتائج في جدول يدوي واحسب الإنذارات الكاذبة والمشاهد الفائتة قبل تفعيل `visual-gate block` على القناة الرئيسية.

تشغيل الاختبارات البرمجية:

```bash
python3 -m pytest -q tests/test_visual_check.py tests/test_visual_policy.py tests/test_risk_scorecard.py tests/test_upload_gate.py
ruff check .
python3 -m compileall -q .
```

## المراجع

[1]: https://github.com/orgs/notAI-tech/packages/container/package/nudenet "NudeNet على GitHub — الوصف والرخصة"

[2]: https://support.google.com/youtube/answer/2802008?hl=en "YouTube — Violent or graphic content policies"

[3]: https://support.google.com/youtube/answer/2802002?hl=en-GB "YouTube — Nudity and sexual content policy"

[4]: https://onnxruntime.ai/docs/get-started/with-python.html "ONNX Runtime — Get started with Python"
