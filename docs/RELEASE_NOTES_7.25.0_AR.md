# ملاحظات إصدار OUSSAMA Cutter 7.25.0-pro

## ما الجديد

تضيف هذه النسخة مسار تفريغ صوتي احتياطياً مستقلاً عبر `faster-whisper` فوق مسار WhisperX + Torch الأساسي. الوضع الافتراضي `auto` يفضّل WhisperX عندما يكون جاهزاً، ثم ينتقل تلقائياً إلى faster-whisper إذا كان WhisperX أو Torch غير قابل للاستيراد. يمكن للمستخدم فرض backend صراحةً عبر `VIRALCUTTER_TRANSCRIPTION_BACKEND=whisperx` أو `faster-whisper`.

لا يستورد OUSSAMA نموذج fallback أثناء إقلاع WebUI ولا ينزّل نموذجاً سراً؛ الاستيراد lazy، ويُنزّل النموذج عند أول تفريغ فقط بعد تثبيت profile الاختياري. يكتب backend مخرجات SRT وTSV وJSON متوافقة مع pipeline، يحفظ cache، ويرفض النتيجة إذا لم تحتوي timestamps صالحة. يبقى placeholder مغلقاً افتراضياً ولا يصلح للإنتاج.

## تثبيت Windows

إذا كان WhisperX يعمل، استمر باستخدام `install_dependencies.bat full` أو `install_dependencies.bat gpu full`. عند تعطل WhisperX أو تضارب Hugging Face، يمكن تثبيت المسار المستقل فقط:

```powershell
cd D:\SS
.\.venv\Scripts\python.exe -m scripts.transcription_diagnostics --repair-fallback
```

أو باستخدام مثبت Windows:

```powershell
.\install_dependencies.bat fallback
```

لجهاز NVIDIA يمكن تجربة `gpu fallback`، لكن إذا لم تتوفر مكتبات CTranslate2/CUDA المتوافقة سيستخدم backend CPU عند `device=auto`. لا يلزم إدخال Token أو API key لهذا المسار.

## التشخيص

يعرض `scripts.transcription_diagnostics` حالات `primary_ready` و`fallback_ready` و`backend`، بينما يعرض `scripts/windows_diagnostics.py` فحص `Faster-whisper fallback` بشكل اختياري. يحافظ الفحص على تشغيل OUSSAMA الأساسي حتى عند غياب كل مكونات التفريغ، ويشرح الإصلاح بدلاً من إعادة المحاولة العمياء.

## الأمان والحدود

faster-whisper مكتبة محلية اختيارية؛ لا ترسل الفيديو أو الصوت إلى خدمة خارجية. يجب مراجعة مصدر النماذج وسياساته داخل بيئة المستخدم، وعدم وضع النماذج داخل Git أو ZIP. لا يزيل fallback حارس المحتوى أو بوابة الرفع، ولا يجعل YouTube أو CUDA مضمونين على كل جهاز.

## التحقق

أضيفت اختبارات لتطبيع segments والكلمات، كتابة SRT/TSV/JSON، اختيار backend، cache، الإصلاح الاختياري، وتشخيص Windows. تم اختبار المسار باستخدام FakeModel/Fake backend في Linux، ولم يُنزّل نموذج فعلي ولم تُنفذ معالجة فيديو حقيقية أو OAuth أو رفع YouTube أثناء الاختبار.

## مشاريع مؤجلة عمداً

بقي OpenTimelineIO وSilero VAD وpyannote.audio وwhisper.cpp في خارطة الطريق. لن تصبح هذه المشاريع اعتمادات أساسية قبل benchmark عربي، فحص ترخيص النماذج، واختبار Windows RTX 3060؛ كما لم يُدمج Demucs لأنه ثقيل والمستودع الرسمي مؤرشف، ولأن music mixing وcopyright fingerprint موجودان أصلاً في OUSSAMA.
