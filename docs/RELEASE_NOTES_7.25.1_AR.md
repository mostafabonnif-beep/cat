# ملاحظات إصدار OUSSAMA Cutter 7.25.1-pro

## إصلاح transcription output validation

يعالج هذا الإصدار حالة استئناف مشروع قديم تكون فيه ملفات التفريغ موجودة ومعلّمة كمكتملة في `checkpoint.json`، لكنها تحتوي على إدخالات ذات timestamps صحيحة ظاهرياً ونص فارغ. كانت هذه الحالة توقف العملية برسالة مثل:

```text
Transcription output validation failed: entry 1176 has empty text
```

أصبح OUSSAMA الآن ينظف الإدخالات الفارغة فقط من SRT وTSV وJSON باستخدام كتابة ذرية، ويترك timestamps التالفة ظاهرة للتحقق بدلاً من إخفائها. إذا بقي الملف غير صالح مع checkpoint مكتمل، يمسح marker التفريغ والـcache ويعيد التفريغ مرة واحدة، ثم يتوقف بأمان إذا بقيت النتيجة غير صالحة.

## التوافق

يعمل الإصلاح مع مسار WhisperX + Torch الأساسي ومع مسار `faster-whisper` الاختياري. لا يغير وحدات timestamps أو يخفف حارس المحتوى أو بوابة منع التكرار والرفع. كما لا يعيد نقل الفيديو المحلي أو ينسخ المصدر الموجود مسبقاً إلى مجلد آخر.

## الإجراء على Windows

بعد استبدال ملفات المشروع بالإصدار الجديد، شغّل من PowerShell:

```powershell
cd D:\SS
.\.venv\Scripts\python.exe -m scripts.transcription_diagnostics
```

ثم أعد تشغيل `run_webui.bat` وأعد المحاولة على المشروع نفسه. إذا بقي checkpoint القديم بسبب إيقاف غير متوقع، احذف أو أعد تسمية `checkpoint.json` داخل مجلد المشروع المتأثر فقط، ولا تحذف `input.mp4` إلا إذا طلب البرنامج إعادة التنزيل.

إذا ظهر نقص في مسار التفريغ، أصلح fallback الاختياري عبر:

```powershell
.\.venv\Scripts\python.exe -m scripts.transcription_diagnostics --repair-fallback
```

## التحقق

نجح regression الكامل بعد الإصلاح بعدد **847 اختباراً**، ونجح `ruff check .` و`compileall` و`uv lock --check` و`git diff --check`. لم يُنفذ نموذج WhisperX أو faster-whisper فعلياً على جهاز Windows في بيئة التطوير، لذلك يبقى اختبار RTX 3060 المحلي مطلوباً.
