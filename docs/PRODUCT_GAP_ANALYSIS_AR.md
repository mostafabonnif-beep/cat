# تحليل فجوات OUSSAMA Cutter — 26 أغسطس 2026

## الخلاصة

المشروع الحالي قوي في pipeline الأساسي: اختيار المقاطع، منع تكرار النوافذ، حارس السلامة، القص، Professional Polish، الموسيقى وauto-duck، B-roll الاختياري، الصور المصغرة، كشف المشاهد، رفع YouTube الآمن، الطابور الدائم، والتقارير. لذلك لا ينبغي دمج مشاريع جديدة تكرر هذه الأجزاء قبل تحسين نقاط الاختناق التي ظهرت في التشغيل الواقعي على Windows.

| الفجوة | الدليل من المصدر الحالي | الأولوية | المسار المقترح |
|---|---|---:|---|
| توقف التفريغ عند تعطل WhisperX أو Torch | `transcribe_video.py` يوقف المسار إذا كان `whisperx` أو `torch` غير قابل للاستيراد، والبديل الحالي placeholder للاختبار فقط | عالية جداً | fallback اختياري مستقل مثل faster-whisper، ثم whisper.cpp كخطة إنقاذ محمولة |
| تتبع المتحدث | `ActiveSpeakerSelector` يختار الوجه من `activity_score` وMAR مع hysteresis، لكنه لا يربط الصوت بالوجه ولا يستخدم diarization | عالية | backend اختياري لـpyannote أو face/voice association، مع fallback الحالي وتقرير صريح |
| كشف الصمت | `jump_cuts.py` يستعمل FFmpeg `silencedetect` وtranscript filler logic؛ الميزة موجودة وليست ناقصة | متوسطة | معايرة VAD اختيارية مثل Silero فقط إذا أثبتت benchmark العربية تحسناً |
| جودة الصوت | يوجد mixing وducking وlimiter، لكن لا يظهر فحصاً مركزياً لـLUFS/true peak/report قابل للبوابة | عالية | إضافة audio QC محلي عبر FFmpeg filters، لا حاجة لمشروع ثقيل في البداية |
| تصدير timeline | يوجد Premiere XML، ولا يوجد OTIO ظاهر في المصدر | متوسطة | OpenTimelineIO اختياري لتبادل timeline وResolve/أدوات أخرى |
| B-roll والموسيقى والبصمة | `broll_engine.py` و`background_music.py` و`music_fingerprint.py` موجودة | منخفضة حالياً | لا ندمج مشروعاً مكرراً؛ نركز على QC وحقوق الاستخدام |
| الاختبار الواقعي | الاختبارات الحالية unit-heavy ولا تستبدل اختبار فيديو قصير على Windows RTX 3060 أو Telegram/YouTube الحقيقي | عالية | إضافة fixtures MP4 صغيرة واختبار قبول Windows يدوي موثق، دون أسرار |
| التوزيع والاعتمادات | WhisperX/Torch ثقيلة وحساسة لإصدارات Hugging Face/CUDA؛ `uv.lock` وdiagnostics موجودان | عالية | optional profiles وفحص preflight يختار backend ويشرح سبب fallback |

## قرار مبدئي

أفضل قيمة مباشرة هي فصل طبقة التفريغ عن WhisperX بإضافة fallback اختياري، ثم إضافة audio QC، ثم OTIO. لا يُنصح حالياً بجعل pyannote أو Demucs أو YOLO اعتماداً أساسياً؛ الأولى تحتاج نماذج وسياسة telemetry/ترخيص لكل نموذج، والثانية أرشيفية وثقيلة، والثالثة قد تفرض تعقيدات وترخيصاً غير مناسباً. أي backend جديد يجب أن يكون opt-in، قابلاً للإزالة، ولا يسمح بتخطي safety gate أو upload gate.

## ما لم يتم فعله بعد

لم يتم تثبيت أو نسخ أي مشروع خارجي بناءً على هذا التحليل، ولم يتم تنزيل نماذج أو تعديل مسار الإنتاج. ستُراجع صفحات المشاريع الرسمية والتراخيص والإصدارات قبل تنفيذ أي دمج، ثم ستضاف اختبارات deterministic وfixtures صغيرة قبل رفع commit جديد.
