# تثبيت وتشغيل OUSSAMA Cutter على القرص D

## الحالة التي لديك

المشروع موجود في `D:\SS`، والقرص C ممتلئ تقريباً. لذلك لا يكفي أن يكون المشروع على D، لأن Windows وuv قد يستخدمان `%TEMP%` و`%LOCALAPPDATA%\uv\cache` على C أثناء فك ضغط الحزم.

## الحل الموصى به

أضفت مشغلين PowerShell ينقلان ملفات التثبيت المؤقتة وكاش uv إلى D:

```powershell
cd D:\SS
Set-ExecutionPolicy -Scope Process Bypass
.\setup_on_d.ps1 -Mode Light -CleanUvCache
```

هذا الأمر يزيل كاش uv القديم من C إن كان موجوداً، ثم ينشئ temp وكاش داخل مجلد المشروع على D، ويثبت الوضع الخفيف الذي لا ينزل PyTorch أو WhisperX.

بعد انتهاء التثبيت شغل الواجهة من D:

```powershell
.\run_webui_on_d.ps1
```

## أوضاع التثبيت

| الأمر | الاستخدام |
|---|---|
| `.\setup_on_d.ps1 -Mode Light` | WebUI الأساسي، وهو الوضع الموصى به |
| `.\setup_on_d.ps1 -Mode Full` | إضافة PyTorch وWhisperX، ويحتاج مساحة كبيرة |
| `.\setup_on_d.ps1 -Mode Gpu` | PyTorch CUDA وWhisperX لأجهزة NVIDIA، ويحتاج مساحة أكبر |
| `.\setup_on_d.ps1 -Mode Upload` | إضافة حزم YouTube OAuth فقط |

في حال وجود كاش قديم على C استخدم `-CleanUvCache` مرة واحدة فقط:

```powershell
.\setup_on_d.ps1 -Mode Light -CleanUvCache
```

## إذا أردت استخدام BAT مباشرة

يمكن أيضاً تشغيل:

```powershell
cd D:\SS
.\install_dependencies.bat
.\run_webui.bat
```

لكن مشغلات PowerShell الموجهة إلى D أفضل في حال امتلاء C لأنها تضبط `TEMP` و`TMP` و`UV_CACHE_DIR` قبل بدء العملية.

## المساحة المطلوبة

الوضع الخفيف يحتاج مساحة أقل، لكن يجب توفير عدة جيجابايت حرة على D للمتطلبات والملفات المؤقتة. تثبيت PyTorch وWhisperX الكامل يحتاج مساحة كبيرة، لذلك لا تستخدم `Full` أو `Gpu` قبل التأكد من توفر مساحة كافية.

## التحقق

```powershell
.\.venv\Scripts\python.exe --version
Test-Path .\.venv\Scripts\python.exe
.\.venv\Scripts\python.exe -c "import numpy, gradio; print(numpy.__version__); print(gradio.__version__)"
```

يجب أن تكون Python ضمن 3.9 إلى 3.12، وأن يعمل استيراد NumPy وGradio دون خطأ. عدم وجود Gemini أو WhisperX لا يمنع تشغيل WebUI الخفيف.

## إصلاح التفريغ الصوتي في OUSSAMA Cutter

إذا ظهر `No module named 'whisperx'` أو `No module named 'torch'` فهذا يعني أن وضع Lightweight يعمل، لكن حزمة التفريغ الكامل لم تُثبت بعد. لا تعِد تشغيل المعالجة بشكل متكرر ولا تستخدم الترجمة الوهمية لفيديو إنتاجي.

من مجلد المشروع على القرص D نفّذ وضع CPU الكامل:

```powershell
cd D:\SS
Set-ExecutionPolicy -Scope Process Bypass
.\setup_on_d.ps1 -Mode Full -Transcription cpu
.\run_webui_on_d.ps1
```

لجهاز NVIDIA المتوافق:

```powershell
.\setup_on_d.ps1 -Mode Full -Transcription gpu
```

للتشخيص فقط دون تثبيت:

```powershell
.\.venv\Scripts\python.exe -m scripts.transcription_diagnostics --json
```

ولإصلاح التفريغ مباشرة من البيئة المحلية:

```powershell
.\.venv\Scripts\python.exe -m scripts.transcription_diagnostics --repair cpu
```

يتحقق النظام من المساحة قبل التنزيل، ويحفظ تقريراً باسم `transcription_diagnostic.json` داخل مجلد المشروع عند فشل مسار التفريغ. تعمل ميزات المونتاج والأمان بدون Torch وWhisperX، بينما يحتاج تحويل فيديو جديد إلى مقاطع مبنية على التفريغ إلى وضع Full.

## الفيديو الموجود مسبقاً على الكمبيوتر: بدون نسخة إضافية

عند اختيار **Upload Video / رفع فيديو** في WebUI، لا يقوم OUSSAMA Cutter بعد الآن بنسخ الفيديو الكبير إلى `VIRALS\اسم_المشروع\input.mp4`. يحفظ المشروع مرجعاً للمسار الأصلي داخل `project_manifest.json`، ثم يستخدم الفيديو نفسه مباشرة أثناء التفريغ والقص وتحليل المخاطر. لذلك تبقى ملفات `VIRALS` مخصصة للنتائج والبيانات الصغيرة، ولا تتضاعف مساحة الفيديو.

مثال على المرجع المحفوظ:

```json
{
  "source": {
    "type": "local",
    "path": "D:\\Videos\\recording.mp4",
    "managed": false
  }
}
```

إذا نُقل الفيديو أو أُعيدت تسميته بعد إنشاء المشروع، افتح `project_manifest.json` وعدّل قيمة `source.path` إلى المسار الجديد، أو أعد اختيار الفيديو من WebUI. لا تحذف الملف الأصلي قبل انتهاء جميع المعالجات والتصديرات التي تعتمد عليه. المشاريع القديمة التي تحتوي `input.mp4` داخل مجلدها تستمر في العمل كما هي.

> الملاحظة المهمة: رفع ملف من متصفح بعيد قد ينشئ نسخة مؤقتة أثناء انتقاله إلى الخادم؛ هذا جزء من عملية الرفع نفسها. الإصلاح يمنع النسخة الدائمة الإضافية داخل `VIRALS`، ويمنع إعادة نسخ الملف في كل تشغيل للمشروع.


ولتشغيل ملف محلي من سطر الأوامر دون نسخه:

```powershell
python .\main_improved.py --local-video "D:\Videos\recording.mp4" --skip-prompts --workflow 1
```

ينشئ هذا الأمر مجلد مشروع صغيراً داخل `VIRALS` لحفظ النتائج والـ JSON والترجمات، بينما يبقى الفيديو الأصلي في مكانه.

## إصلاح خطأ cmd.exe في run_webui.bat

إذا ظهرت رسائل مثل `'not' is not recognized` أو ظهرت أجزاء مبتورة من كلمات `preflight` و`folder`، فالمشكلة تكون في قراءة ملف BAT بترميز غير مناسب، وليست خطأً في Python. النسخة الجديدة من `run_webui.bat` مكتوبة بمحارف ASCII فقط وبنهايات أسطر Windows CRLF، لذلك لا تعتمد على عرض العربية داخل مفسر الأوامر.

بعد استبدال الملف، شغّله من مجلد المشروع:

```powershell
cd D:\SS
.\run_webui.bat
```

يفحص المشغل البيئة أولاً، ثم يبدأ WebUI إذا كانت النتيجة ناجحة أو تحذيرات اختيارية. أما الخطأ الحرج فيوقف التشغيل برسالة قصيرة بدلاً من تنفيذ أسطر مكسورة.

## ملاحظة مهمة حول تحذيرات WhisperX

ظهور `System ready` مع تحذيرات `torch` و`torchaudio` و`whisperx` أمر متوقع في **Lightweight mode**؛ هذا الوضع يشغّل WebUI والمونتاج الأساسي لكنه لا يثبت حزمة التفريغ الثقيلة تلقائياً. لذلك يجب تنفيذ التثبيت التالي مرة واحدة قبل معالجة فيديو جديد يحتاج تفريغاً صوتياً:

```powershell
cd D:\SS
Set-ExecutionPolicy -Scope Process Bypass
.\setup_on_d.ps1 -Mode Full -Transcription cpu -CleanUvCache
```

بعد النجاح يجب أن يعرض الفحص علامات نجاح للمكوّنات الثلاثة. إذا لم ينجح التثبيت، أصبحت النسخة الجديدة من `install_dependencies.bat` توقف العملية برسالة فشل واضحة بدلاً من عرض `Setup finished` مع WhisperX ناقص. شغّل التشخيص عند الحاجة:

```powershell
.\.venv\Scripts\python.exe -m scripts.transcription_diagnostics --json
```


## OUSSAMA Cutter 7.5.0-pro: مفاتيح Gemini واختيار الجهاز

أصبح تبويب الذكاء الاصطناعي يدعم حتى ثلاثة مفاتيح Gemini API. أدخل المفتاح الأول والثاني والثالث اختيارياً، ثم اختر **تدوير تلقائي عند انتهاء الحصة** أو ثبّت الاستخدام على المفتاح 1 أو 2 أو 3. عند التدوير التلقائي ينتقل المحرك إلى المفتاح التالي عند ظهور quota أو خطأ مصادقة، ولا يطبع قيمة المفاتيح في سجل التشغيل.

إذا كان المتغير `VIRALCUTTER_CONFIG_PASSPHRASE` مضبوطاً، تُحفظ المفاتيح في المخزن المشفر. وبدونه يستمر التوافق مع `api_config.json` القديم، مع توصية استخدام عبارة مرور للتخزين الآمن.

تحت خيار نموذج Whisper ستظهر بطاقة **حالة التفريغ والجهاز**. اختر `Auto` لاكتشاف NVIDIA CUDA تلقائياً، أو `CPU` لإجبار المعالجة على المعالج، أو `NVIDIA GPU / CUDA` لإجبار بطاقة NVIDIA. عند اختيار CUDA دون وجود بطاقة قابلة للاستخدام يتوقف التشغيل برسالة واضحة بدلاً من traceback طويل. كما يراعي cache التفريغ اختيار الجهاز حتى لا تختلط نتائج CPU وGPU.

يظهر اختيار الجهاز أيضاً في التشغيل من CLI:

```powershell
python .\main_improved.py --local-video "D:\Videos\recording.mp4" --transcription-device cpu --skip-prompts --workflow 1
```

وعلى جهاز NVIDIA:

```powershell
python .\main_improved.py --local-video "D:\Videos\recording.mp4" --transcription-device cuda --skip-prompts --workflow 1
```


## عرض الجهاز الفعلي أثناء المعالجة

أثناء الضغط على **بدء المعالجة** تظهر حالة الجهاز داخل لوحة التقدم نفسها عند مرحلة **التفريغ الصوتي**. إذا كان العمل على المعالج ستظهر عبارة `يعمل الآن بواسطة CPU`. وإذا كان على كرت NVIDIA ستظهر عبارة `يعمل الآن بواسطة NVIDIA GPU / CUDA` مع اسم البطاقة عند توفره. تظهر الرسالة أيضاً في **سجل التشغيل**.

هذه الحالة هي الجهاز الفعلي الذي اختاره WhisperX، وليست مجرد قيمة القائمة. إذا اخترت `Auto` فستظهر النتيجة الحقيقية بعد اكتشاف Torch لـ CUDA. وإذا اخترت CUDA دون بطاقة مناسبة فستظهر رسالة عدم توفر CUDA ويتوقف التشغيل بأمان.


## تفعيل NVIDIA RTX 3060 بدلاً من Torch CPU

إذا عرضت الواجهة `Torch 2.8.0+cpu` فهذا يعني أن WhisperX يعمل على المعالج حتى لو كان الجهاز يحتوي على RTX 3060. أغلق WebUI ثم نفّذ من PowerShell داخل مجلد المشروع:

```powershell
cd D:\SS
Set-ExecutionPolicy -Scope Process Bypass -Force
.\setup_on_d.ps1 -Mode Gpu -Transcription gpu -CleanUvCache
```

أصبح مثبت GPU يعيد تثبيت Torch CUDA بعد تثبيت WhisperX، لأن WhisperX قد يحاول جلب نسخة Torch CPU من PyPI. إذا لم تصبح CUDA جاهزة، يتوقف المثبت برسالة واضحة بدلاً من إعلان نجاح زائف.

تحقق من النتيجة قبل تشغيل WebUI:

```powershell
.\.venv\Scripts\python.exe -c "import torch, torchaudio, whisperx; print('Torch:', torch.__version__); print('CUDA build:', torch.version.cuda); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"
```

المطلوب أن تكون `CUDA available: True` وأن يظهر اسم بطاقة NVIDIA RTX 3060. بعد ذلك شغّل:

```powershell
.\run_webui.bat
```

واختر من الواجهة `Auto` أو `NVIDIA GPU / CUDA`. إذا بقيت `Torch ...+cpu`، فهذا يعني أن أمر GPU لم يكتمل أو أن تعريف NVIDIA غير ظاهر لـ Windows، وليس مشكلة في WhisperX نفسه.


## إصلاح فشل Preflight بسبب NumPy 2.5.2

إذا ظهر في Preflight أن `numpy 2.5.2` يخالف شرط WhisperX، فلا تعِد تشغيل WebUI فقط. الإصدار الصحيح لهذا المشروع هو NumPy 1.26.4، وأصبح `preflight --auto-fix` يستخدم تثبيتاً حتمياً عبر `numpy==1.26.4` مع تعطيل cache حتى لا يبقى الإصدار 2.x.

كما أصبح `run_webui.bat` ينشئ `.runtime-tmp` ويضبط `TEMP` و`TMP` و`UV_CACHE_DIR` على القرص D قبل تشغيل Python، لتقليل تحذير `Failed to set cwd to temp dir` ومشاكل امتلاء القرص C.

بعد تحديث الملفات، أغلق WebUI ثم شغّل:

```powershell
cd D:\SS
.\run_webui.bat
```

يجب أن يظهر `numpy 1.26.4`، مع بقاء `torch 2.5.1+cu124` و`torchaudio 2.5.1+cu124` و`whisperx 3.8.6` جاهزة. تحذيرات `google-auth-oauthlib` و`pyacoustid` اختيارية ولا تمنع التفريغ أو استخدام RTX 3060.


## إصلاح رسالة `No module named pip`

قد تنشئ `uv venv` بيئة `.venv` صحيحة للتشغيل لكنها لا تحتوي الأمر `pip` داخلها. لذلك فإن الأمر اليدوي `python -m pip install ...` قد يفشل رغم أن Torch وCUDA يعملان.

في هذه الحالة استخدم النسخة المحدثة من المشروع، لأن `preflight` و`transcription_diagnostics --repair` أصبحا يستخدمان `uv pip --python .venv\Scripts\python.exe` تلقائياً عند غياب pip. ويمكن تنفيذ الإصلاح المباشر بهذه الصيغة:

```powershell
cd D:\SS
uv pip install --python .\.venv\Scripts\python.exe --force-reinstall --no-cache numpy==1.26.4
```

لا تحذف البيئة ولا تعِد تثبيت Torch CUDA؛ النتيجة السابقة تؤكد أن `Torch 2.5.1+cu124` و`CUDA available: True` و`RTX 3060` تعمل بشكل صحيح. المشكلة الحالية محصورة في أداة تثبيت NumPy داخل venv.


## إصلاح تعارض huggingface-hub مع WhisperX

إذا ظهر الخطأ `huggingface-hub>=0.34.0,<1.0 is required ... but found huggingface-hub==1.27.0`، فهذا تعارض إصدارات داخل طبقة Transformers وليس مشكلة CUDA أو RTX 3060. الإصدار الجديد يثبت القيد `huggingface-hub>=0.34.0,<1.0` بعد WhisperX، ويستخدم `uv pip` تلقائياً إذا لم يوجد pip داخل venv.

للإصلاح الفوري من PowerShell:

```powershell
cd D:\SS
uv pip install --python .\.venv\Scripts\python.exe --upgrade "huggingface-hub>=0.34.0,<1.0"
```

ثم تحقق من الاستيراد:

```powershell
.\.venv\Scripts\python.exe -c "import huggingface_hub, transformers, whisperx, torch; print('huggingface-hub:', huggingface_hub.__version__); print('transformers:', transformers.__version__); print('WhisperX: READY'); print('CUDA:', torch.cuda.is_available())"
```

يجب أن يكون إصدار `huggingface-hub` أقل من 1.0، وأن تبقى `CUDA: True`. لا تستخدم `pip` داخل هذه البيئة إذا ظهر `No module named pip`؛ استخدم `uv pip --python` كما في الأمر أعلاه. رسالة الفيديو المقيد عمرياً منفصلة، وتعالج عبر كوكيز المتصفح بعد نجاح توافق الحزم.


## عندما يبدو التفريغ متوقفاً بعد اكتشاف اللغة

إذا ظهر السجل:

```text
[الجهاز] يعمل الآن بواسطة NVIDIA GPU / CUDA — NVIDIA GeForce RTX 3060
Detected language: ar
Alinhando transcrição (Idioma: ar)...
```

فهذا يعني أن CUDA وWhisperX وVAD نجحت، وأن العملية انتقلت إلى **محاذاة الكلمات والتوقيتات**. هذه المرحلة قد تستغرق وقتاً أطول من مجرد اكتشاف اللغة، خصوصاً عند أول تنزيل لنموذج المحاذاة العربية.

أضيف إلى الإصدار الجديد heartbeat مرئياً داخل لوحة WebUI، وستظهر رسائل مثل:

```text
جاري تحميل نموذج WhisperX وVAD — قد يستغرق ذلك وقتاً أول مرة — 45ث
جاري التفريغ الصوتي على الجهاز المحدد — 90ث
جاري محاذاة الكلمات العربية — قد يستغرق ذلك وقتاً — 120ث
جاري محاذاة الكلمات والتوقيتات — 150ث
```

لا تُغلق العملية ما دامت هذه الرسائل تتحدث. إذا احتجت الإيقاف، استخدم زر الإيقاف في WebUI؛ لا تغلق PowerShell قسراً حتى تُحفظ الملفات المؤقتة بأمان.


## إصلاح طلب 3 مقاطع وخروج مقطع واحد

أصبح OUSSAMA Cutter يطلب مرشحين احتياطيين من الذكاء الاصطناعي، لأن بعض المرشحين قد يُحذف بسبب التداخل أو بوابة الأمان. بعد ذلك يختار العدد المطلوب من المقاطع الآمنة والمتميزة فقط. لا يتم تصدير المقاطع المحجوبة لمجرد الوصول إلى العدد.

كما أصبح النظام يقارن عدد ملفات القص القديمة بعدد المقاطع الآمنة الحالية. إذا كان مجلد `cuts` يحتوي على مقطع واحد بينما القائمة الحالية تحتوي ثلاثة، فإنه يفرض إعادة قص نظيفة بدلاً من إعادة استخدام النتيجة القديمة. يتم تسجيل النتيجة في `delivery_manifest.json`، ويتضمن `requested_count` و`safe_selected_count` و`rendered_count` ومسارات الملفات النهائية.

ابحث في سجل التشغيل عن:

```text
Final selection: exporting 3 of 5 safe candidates.
Delivery audit: 3 rendered file(s) for 3 selected segment(s).
```

إذا كان العدد أقل من المطلوب بسبب الأمان، سيذكر السجل ذلك صراحة مع بقاء المقاطع المحجوبة خارج التصدير.
