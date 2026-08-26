# خطة دمج مشاريع مفتوحة المصدر في OUSSAMA Cutter

## الهدف

الهدف ليس جمع أكبر عدد من المشاريع، بل إزالة نقاط الفشل التي ظهرت في العمل الحقيقي على Windows مع الحفاظ على الاستقرار والأمان ووضوح الترخيص. ستبقى كل الإضافات اختيارية أو خلف بوابات واضحة، ولن يسمح أي backend جديد بتجاوز `content_guard` أو `upload_gate` أو موافقة النشر.

## ترتيب التنفيذ

| المرحلة | الحل | ما يحلّه | المفاضلة | شرط القبول |
|---|---|---|---|---|
| 1 | واجهة backend موحّدة + faster-whisper fallback | يمنع توقف التفريغ الكامل عند تعطل Torch/WhisperX أو تعارض Hugging Face | يحتاج CTranslate2 وcuBLAS/cuDNN على GPU؛ CPU fallback أبطأ | ينجح استخراج SRT/TSV/JSON مع timestamps من fake backend، وتبقى WhisperX أولوية عندما تكون جاهزة |
| 2 | Audio QC محلي عبر FFmpeg | يكشف silence غير المقصود، فرق loudness، clipping، ومسار صوت مفقود قبل polish/النشر | يحتاج معايرة target ولا يثبت قبول YouTube | تقرير JSON قابل للقراءة، لا يمنع المسار إلا عند غياب الصوت أو فشل probe الصريح |
| 3 | OpenTimelineIO كـoptional export | يوسّع Premiere XML الحالي إلى timeline قابل للتبادل ويشير إلى الوسائط بدلاً من تضمينها | اعتماد اختياري وصيغة لا تحمل الفيديو | export صحيح عند تثبيت OTIO، ورسالة واضحة عند عدم تثبيته، مع اختبار مسارات آمنة |
| 4 | Silero VAD لتحسين jump cuts | يقلل أخطاء `silencedetect` في الضوضاء واللهجات | توجد jump cuts حالياً؛ يلزم benchmark عربي قبل اعتماده | لا يغيّر القرار التلقائي قبل مقارنة precision/recall على fixtures عربية |
| 5 | pyannote.audio أو face/voice association | يحسن Active Speaker من heuristic إلى ربط صوتي/زمني | نماذج منفصلة الشروط، اعتماد ثقيل، telemetry اختيارية يجب تعطيلها | benchmark multi-speaker، تقرير backend، fallback تلقائي، وعدم إرسال صوت للخارج |
| 6 | whisper.cpp كخطة إنقاذ محمولة | يعمل خارج بيئة Python/Torch عند انهيارها | binary ونماذج وإدارة Windows إضافية | spike مستقل لا يدخل release قبل اختبار binary/model/signature على Windows |
| 7 | Demucs | فصل الموسيقى والكلام | المستودع الرسمي مؤرشف، والنماذج ثقيلة، والموسيقى موجودة أصلاً | يبقى خارج core إلى أن يثبت فائدة في حالات صوتية حقيقية وترخيص النماذج |

## القرار التنفيذي

تبدأ النسخة التالية بالمرحلتين 1 و2، لأنهما تعالجان أكثر مشكلتين إيلاماً للمستخدم: توقف التفريغ وجودة المخرج غير المقاسة. تضاف المرحلة 3 في نفس الدفعة إن بقيت optional ولا تغيّر المسار الافتراضي. لن يضاف pyannote أو Demucs أو YOLO إلى الاعتماد الأساسي قبل benchmark وترخيص النماذج.

## ضوابط الأمان

لا تُنقل الفيديوهات أو الصوت إلى خدمة خارجية بسبب هذه الإضافات. تُستبعد النماذج وملفات cache وtokens من ZIP، ويُحفظ كل تقرير داخل المشروع. يفشل fallback مغلقاً إذا لم يستطع استخراج timestamps صالحة، ولا يسمح placeholder بالمرور في إنتاج حقيقي. أي telemetry غير ضرورية تُعطّل افتراضياً، وأي نموذج له ترخيص مستقل يُراجع قبل التوزيع.

## المصادر الرسمية

المعلومات المتعلقة بالمشاريع مأخوذة من مستودعاتها الرسمية: faster-whisper يعلن MIT ويدعم CPU/GPU عبر CTranslate2 مع متطلبات CUDA الحديثة [1]، Silero VAD يعلن MIT ويدعم ONNX Runtime [2]، pyannote.audio يعلن MIT للمكتبة ويوثق telemetry الاختيارية [3]، OpenTimelineIO يعلن Apache-2.0 ويدعم Python 3.9–3.12 [4]، whisper.cpp يعلن MIT ودعم Windows وCUDA/CPU [5]، وDemucs يعلن MIT لكنه مؤرشف للقراءة فقط [6].

## المراجع

[1]: https://github.com/SYSTRAN/faster-whisper "SYSTRAN/faster-whisper"

[2]: https://github.com/snakers4/silero-vad "snakers4/silero-vad"

[3]: https://github.com/pyannote/pyannote-audio "pyannote/pyannote-audio"

[4]: https://github.com/AcademySoftwareFoundation/OpenTimelineIO "AcademySoftwareFoundation/OpenTimelineIO"

[5]: https://github.com/ggml-org/whisper.cpp "ggml-org/whisper.cpp"

[6]: https://github.com/facebookresearch/demucs "facebookresearch/demucs"
