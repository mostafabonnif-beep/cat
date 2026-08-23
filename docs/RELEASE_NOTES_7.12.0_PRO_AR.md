# ملاحظات إصدار OUSSAMA Cutter 7.12.0-pro

## خلاصة الإصدار

تقدم هذه النسخة دورة إنتاج أكثر موثوقية: استئناف بعد الفشل، طابور دائم، رفع آمن غير مكرر، حاجز Public صريح، تشخيص Windows، واجهة عربية منظمة، وتحليل محلي من سجلات النشر.

## أبرز التغييرات

| الفئة | التغيير |
|---|---|
| الاعتمادية | metadata غنية في checkpoint مع active stage وlast error وhistory، مع توافق كامل مع المشاريع القديمة |
| الطابور | PriorityQueue، pause/resume دائم، retry failed، cancel وrecovery بعد إعادة التشغيل |
| النشر | SHA-256 fingerprint، skip للتكرار الناجح، سجل JSONL يتحمل الأسطر الفاسدة، تحديث manifest |
| الأمان | منع Public الحقيقي دون تأكيد صريح في طبقة الرفع، مع بقاء Dry Run افتراضياً |
| YouTube | OAuth محلي، استبدال client secrets، إبطال التوكن القديم، تحقق القناة قبل المعالجة |
| Windows | تشخيص D/TEMP/space/FFmpeg/Deno/Torch/CUDA/WhisperX/OAuth، وإصلاح رسائل batch والمسارات |
| الواجهة | فحص الإعدادات قبل البدء، RTL محسّن، أزرار الطابور، backup/restore، ومحددات الملفات المحلية |
| التحليلات | زر تحليل سجل الرفع المحلي بدون OAuth، إلى جانب تقارير YouTube Analytics للقراءة فقط |
| النسخ الاحتياطي | ZIP ذري خالٍ من الأسرار افتراضياً، مع فحص مسارات آمنة واستعادة إلى مشروع جديد |

## تغييرات توافقية

أضيفت معاملات جديدة في نهاية عقود التشغيل فقط، لذلك بقي ترتيب معاملات `run_viral_cutter` الحالي محفوظاً. الحقل القديم `checkpoint.stages` ما زال boolean، بينما تحفظ metadata في حقول منفصلة. استدعاءات `stream_upload` القديمة تبقى صالحة لأن `public_confirm` اختياري وقيمته الافتراضية آمنة.

## سياسة التشغيل

لا يفتح فحص الإعدادات أي متصفح ولا يرسل أي ملف. لا يتم login أو upload حقيقي من اختبارات WebUI. ابدأ دائماً بـDry Run و`private`، واختبر RTX 3060 على Windows من داخل `.venv` بعد التأكد من ظهور `torch.cuda.is_available() = True`.

## ملفات مهمة

`webui/render_queue.py`، `scripts/checkpoint.py`، `webui/publish_history.py`، `webui/publish_panel.py`، `scripts/upload_gate.py`، `webui/backup.py`، `scripts/windows_diagnostics.py`، `webui/learn_panel.py`، و`webui/app.py`.

## ملاحظات غير قابلة للاستبدال باختبار Linux

إقلاع WebUI واختبارات Python لا تثبت أن تعريف NVIDIA أو CUDA أو WhisperX يعمل على جهاز المستخدم. يجب تنفيذ `windows_diagnostics` ومعالجة محلية قصيرة على Windows قبل اعتبار التثبيت جاهزاً للإنتاج.
