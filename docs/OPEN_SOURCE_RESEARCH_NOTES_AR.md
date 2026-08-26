# ملاحظات بحث مشاريع مفتوحة المصدر — 26 أغسطس 2026

هذه ملاحظات داخلية مؤقتة لمرحلة تقييم مشاريع يمكن دمجها في OUSSAMA Cutter.

## OpenTimelineIO

المستودع الرسمي: https://github.com/AcademySoftwareFoundation/OpenTimelineIO

توضح الصفحة الرسمية أن OpenTimelineIO صيغة تبادل وواجهة API لمعلومات timeline التحريرية، تحفظ ترتيب المقاطع ومددها ومراجع الوسائط الخارجية، وليست حاوية فيديو. الصفحة تعرض ترخيص Apache-2.0 ودعم Python 3.9–3.12 وإصدارات حديثة من المستودع. هذا يجعله مرشحاً جيداً لإضافة تصدير timeline متوافق مع برامج المونتاج، مع بقاء ملفات الفيديو خارج ملف OTIO.

## Demucs

المستودع الرسمي: https://github.com/facebookresearch/demucs

توضح الصفحة الرسمية أن Demucs يفصل الموسيقى إلى vocals وdrums وbass وباقي accompaniment، وأن المستودع يعلن ترخيص MIT. كما ظهر تنبيه أن المستودع مؤرشف للقراءة فقط منذ 1 يونيو 2025، ولذلك لا ينبغي جعله اعتماداً إلزامياً في مسار الإنتاج. يمكن اعتباره إضافة اختيارية لميزة فصل الموسيقى/الكلام أو تنظيف الخلفية بعد اختبار توافق PyTorch وCUDA وحجم النماذج.

## حدود هذه الملاحظات

لم يتم تثبيت أي مشروع خارجي أو تنزيل نموذج أو تغيير كود OUSSAMA بناءً على هذه الملاحظات. يلزم فحص LICENSE ونسخ الإصدارات والمتطلبات من المصادر الرسمية لكل مرشح قبل الدمج، مع اختبار Windows RTX 3060 ومسار CPU fallback.

## Silero VAD

المستودع الرسمي: https://github.com/snakers4/silero-vad

يعرض المستودع ترخيص MIT ومتطلبات Python 3.8+ وذاكرة 1GB+ على x86-64. يذكر أن الاستخدام يمكن أن يكون عبر ONNX Runtime مع ضرورة تنفيذ I/O وpost-processing عند عدم استخدام Torch. هذا مرشح عملي لطبقة كشف الكلام والصمت قبل القص، خصوصاً إذا استُخدم ONNX Runtime الموجود في المشروع، لكن يجب اختبار دقة العربية والضوضاء وعدم قص الكلمات.

## pyannote.audio

المستودع الرسمي: https://github.com/pyannote/pyannote-audio

يقدم building blocks للتقسيم بين المتحدثين واكتشاف النشاط وتغير المتحدث والكلام المتداخل وspeaker embeddings. الصفحة تعرض MIT license للمكتبة، لكنها توضح وجود telemetry اختيارية عند تحميل pipelines، كما أن نماذج Hugging Face وسياساتها قد تكون منفصلة عن ترخيص الكود. لذلك هو مرشح قوي لإصلاح diarization، لكنه ليس إضافة أولى بسيطة: يحتاج تثبيتاً وتجربة نماذج محلية وسياسة واضحة لتعطيل telemetry والتحقق من ترخيص كل نموذج.

## faster-whisper

المستودع الرسمي: https://github.com/SYSTRAN/faster-whisper

يعتمد faster-whisper على CTranslate2 ويقدم تفريغاً أسرع وأقل استهلاكاً للذاكرة من openai-whisper وفق وصف المستودع، مع دعم خيارات VAD. يوضح README أن PyAV يحزم مكتبات FFmpeg، وأن GPU يحتاج cuBLAS لـCUDA 12 وcuDNN 9 في الإصدارات الحديثة من CTranslate2، مع وجود مسار CPU. هذا أفضل مرشح لمسار fallback Python بعد WhisperX، لكن يجب تثبيت نسخة CTranslate2 متوافقة مع CUDA على Windows واختبار اللغة العربية والـtimestamps.

## whisper.cpp

المستودع الرسمي: https://github.com/ggml-org/whisper.cpp

يعلن المشروع ترخيص MIT ودعم CPU وCUDA وVulkan وOpenVINO وWindows، ويعمل أساساً عبر binary ونماذج ggml خارج Python. هو ممتاز كطبقة إنقاذ عند تعطل Torch/WhisperX، لكنه يحتاج إدارة binary والنماذج وربما بناء CUDA؛ لذلك هو مسار استقرار مستقل لاحق، وليس أول دمج داخل Python.

## تحقق إضافي من الترخيص والمتطلبات

الصفحات الرسمية المعروضة تشير إلى أن faster-whisper يحمل MIT license، ويدعم Python 3.9+، ويحتاج cuBLAS CUDA 12 وcuDNN 9 في تشغيل GPU الحديث، مع أمثلة CPU و8-bit. ويعلن Silero VAD MIT وعدم وجود telemetry أو مفاتيح أو تسجيل.

كما تعرض صفحة pyannote.audio MIT license للمكتبة وتوثق أن telemetry اختيارية ويمكن تعطيلها عبر `PYANNOTE_METRICS_ENABLED` أو API، لكن نماذج Hugging Face تبقى مكوّناً منفصلاً يجب فحص ترخيصه وشروطه. وتعرض صفحة OpenTimelineIO Apache-2.0 ودعم Python 3.9–3.12.

مصادر الصفحات الرسمية:

- https://github.com/SYSTRAN/faster-whisper
- https://github.com/snakers4/silero-vad
- https://github.com/pyannote/pyannote-audio
- https://github.com/AcademySoftwareFoundation/OpenTimelineIO
- https://github.com/ggml-org/whisper.cpp
