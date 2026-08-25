# قائمة إصدار Windows — OUSSAMA Cutter 7.24.0-pro

> هذه القائمة تفصل بين ما يمكن اختباره آلياً على خادم Linux وما يجب اختباره على جهاز Windows الحقيقي. لا ترسل مفاتيح Gemini أو `client_secrets.json` أو `token.json` عند طلب المساعدة.

## 1. تجهيز مجلد المشروع على D:

يفضل وضع المشروع في `D:\SS` حتى لا يستهلك استخراج Torch وnumpy مساحة `C:`. افتح PowerShell داخل مجلد المشروع وشغّل:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd D:\SS
.\setup_on_d.ps1 -Mode Full -Transcription gpu
```

لجهاز لا يملك CUDA استخدم:

```powershell
.\setup_on_d.ps1 -Mode Full -Transcription cpu
```

المثبت لا يفترض نجاحاً إذا فشل FFmpeg أو OAuth. عند خطأ المساحة، نظف cache المؤقتة على `C:` وأعد المحاولة، ثم اقرأ التقرير الناتج بدلاً من إعادة التثبيت عشوائياً.

## 2. تشخيص قابل للإرسال

```powershell
.\.venv\Scripts\python.exe -m scripts.windows_diagnostics --json --output windows_diagnostics.json
Get-Content .\windows_diagnostics.json
```

يجب أن يعرض التقرير، عند اختيار GPU، مسار Python داخل `D:\SS\.venv`، مساحة كافية، `ffmpeg` و`ffprobe`، وDeno إن كان مطلوباً، و`torch.cuda.is_available() = true`، واسم NVIDIA RTX 3060، ونجاح `torchaudio` و`whisperx` ومكتبات OAuth. لا يحتوي التقرير على مفاتيح أو محتوى token.

## 3. تشغيل الواجهة وفحصها

```powershell
.\run_webui.bat
```

افتح `http://127.0.0.1:7860` وتحقق من عنوان **OUSSAMA Cutter**، ثم اختبر بالترتيب:

| الاختبار | المتوقع |
|---|---|
| بطاقة حالة الجهاز | تعرض CUDA واسم GPU أو تعرض CPU بوضوح |
| زر فحص الإعدادات | يعرض الأخطاء دون تنزيل أو تسجيل دخول |
| فيديو محلي | يستخدم المسار الأصلي ولا ينسخه مرة أخرى إلى `VIRALS` |
| طابور المعالجة | يظهر priority وpause وresume وcancel وretry failed وrefresh |
| المكتبة | تظهر backup/restore مع استبعاد الأسرار افتراضياً |
| رفع ونشر | تظهر client secrets وDry Run وتأكيد Public، ويُحفظ `publish_batch_report.json` بحالة كل ملف |
| تدقيق الجاهزية | زر «تدقيق جاهزية الرفع قبل البدء» يعرض عدد segments والملفات وPolish وtracking وOAuth وقاطع القناة دون رفع |
| تقرير المشروع | يعرض المرحلة النشطة وآخر خطأ وسجل الرفع وحالة content_guard وملخص polish والدفعة الأخيرة وtracking backend |
| قاعدة المحتوى | ينشئ/يقرأ `.oussama_content_registry.sqlite3` داخل `VIRALS` ولا يخزن أسراراً |
| قاطع القناة | يعرض الحالة المقفولة عند حادثة سياسة، ويوقف الرفع قبل OAuth |
| preflight قبل القص | يتحقق من client secrets والتوكن والقناة والخصوصية ووقت البداية قبل التفريغ والقص | 
| Professional Polish | ينشئ `polish_report.json`، ويميز enhanced/partial/fallback/failed ولا يعلن fallback نجاحاً | 
| جدولة الدفعة | ستة مقاطع أو أكثر تحصل على أوقات مستقلة وفق الفاصل؛ لا توجد رسالة نجاح شاملة عند الفشل |
| استئناف الرفع | `retry_failed_only` يعيد العناصر الفاشلة فقط ولا يعيد الناجح أو scheduled |
| تنويع المقاطع | لا تتكرر `start_time/end_time` أو نافذة المصدر بين الملفات؛ العنوان وحده لا يُعد مقطعاً جديداً |
| تتبع المتحدث | عند تفعيله يظهر InsightFace و`active_speaker_applied=true` في `tracking_report.json`؛ MediaPipe/Haar يوضحان face-tracking-only |
| سلامة إعادة القص | وجود `cuts_manifest.json` مطابق لقائمة المقاطع، وعدم بقاء ملفات final أو polish قديمة عند تغيير القائمة |
| فشل القص | تجربة segment فاشل توقف المرحلة ولا تنتج رسالة نجاح أو ترفع دفعة ناقصة |
| حالة الطابور التالفة | عند تلف `.batch_queue.json` تُحفظ نسخة `queue.json.corrupt-*` ويظهر تحذير في ملخص الطابور |
| Telegram Control Center | عند تركه معطلاً لا يبدأ polling؛ وعند تفعيله تظهر بطاقة ready وعدد Chat IDs فقط، ولا يظهر Token |
| أوامر Telegram | `/status` و`/projects` و`/audit` وpause/resume/retry/cancel تعمل للطابور المحلي فقط؛ الإلغاء الجماعي يحتاج تأكيداً خلال 60 ثانية |
| إشعارات Telegram | تبقى معطلة افتراضياً؛ عند تفعيلها يصل status قصير للمهام الجديدة فقط دون ملفات أو مسارات أو logs |

## 4. اختبار معالجة محلية آمنة

ابدأ بمقطع قصير تملكه أو تملك حق استخدامه. اترك `Dry Run` مفعلاً، واستخدم `private` أو لا تستخدم الرفع إطلاقاً. راجع `project_report.html` و`risk_scorecard.json` و`polish_report.json` وملفات `final_polished` قبل أي نشر. إذا ظهر fallback أو failed، صحح السبب أو ارفع `final` بدلاً من polished.

لا تستخدم `VIRALCUTTER_ALLOW_PLACEHOLDER=1` في إنتاج حقيقي؛ هذا الخيار للاختبارات التي لا تحتاج تفريغاً صوتياً كاملاً فقط.

## 5. ربط YouTube دون رفع

من تبويب «رفع ونشر» اختر ملف `client_secrets.json` من جهازك، ثم استخدم «حفظ/التحقق من الملف» و«تسجيل الدخول إلى YouTube». يجب أن تظهر القناة قبل تشغيل المعالجة التي تشترط الربط. إذا ظهر نقص authentication scopes، استبدل الملف أو أعد OAuth؛ لا تشارك رابط localhost أو code أو token.

يبدأ النشر افتراضياً بوضع `private` وDry Run. لا تختبر `Public` الحقيقي إلا بعد مراجعة الفيديو، والتأكد من القناة، وتفعيل تأكيد النشر العام يدوياً. حتى مع التأكيد يظل فحص السلامة ومنع تكرار البصمة ونافذة المصدر وقاطع القناة فعالين. لا يحاول النظام التحايل على Content ID أو أنظمة رصد YouTube؛ القص أو عكس الصورة أو تغيير السرعة ليس وسيلة مسموحة لتجاوز الرصد.

## 6. اختبار الرفع الحقيقي الاختياري

بعد نجاح Dry Run ومراجعة المشروع، ارفع ملفاً واحداً قصيراً تملكه. تحقق من أن `content_guard_report.json` لا يحتوي قرار حجب، وأن حالة القناة غير مقفولة قبل بدء OAuth. راقب `publish_history.jsonl` و`project_manifest.json` و`publish_batch_report.json` وتأكد من حفظ `video_id` أو الرابط. أعد تشغيل الدفعة للتأكد من أن الناجح يظهر `skipped_duplicate` ولا يُرفع مجدداً، ثم اختبر retry للفاشل فقط بعد إصلاح السبب.

لا يكرر البرنامج تلقائياً أخطاء 401 أو 403. أما 429 و500 و502 و503 و504 فلها محاولات محدودة بانتظار متزايد. إذا استمرت المشكلة، افحص الحصة والصلاحيات والاتصال بدلاً من تكرار الطلبات بسرعة.

## 7. Telegram محلياً دون تعريض الجهاز

أنشئ Bot من [@BotFather](https://telegram.me/BotFather) على حسابك أنت، ثم انسخ `telegram_control.example.ps1` إلى `telegram_control.local.ps1` خارج Git وZIP وشغّله من `D:\SS`. خزّن Token وChat IDs في User Environment على Windows فقط، ولا ترسلهما في هذه المحادثة أو الدعم. أرسل `/help` للبوت من المحادثة المسموح بها، ثم تحقق من بطاقة Telegram في WebUI ومن `scripts.windows_diagnostics`.

يجب أن يكون OUSSAMA Cutter مفتوحاً حتى يعمل polling. لا يوجد webhook أو منفذ عام، ولا يقبل البوت `client_secrets.json` أو OAuth tokens أو ملفات أو أوامر shell، ولا ينفذ `/upload` أو `/publish`. نفّذ اختبار Telegram بالمحاكاة في Linux، والاختبار الواقعي برسالة واحدة من جهاز Windows بعد مراجعة allowlist. إذا اشتبهت بتسرب Token، ألغِه من BotFather وولّد Token جديداً قبل إعادة التشغيل.

## 8. النسخ الاحتياطي والتحديث

أنشئ نسخة من المكتبة قبل أي تحديث. افحص ZIP وتأكد من عدم وجود `token.json` أو `client_secrets.json` أو cache أو `.pyc`. الاستعادة تنشئ مشروعاً جديداً ولا تكتب فوق المشروع الأصلي.

لا تطبق تحديثاً على جهاز الإنتاج قبل الاحتفاظ بالنسخة السابقة وقراءة release notes. التحديث التلقائي لا ينبغي أن يشغل OAuth أو يرفع ملفات من تلقاء نفسه.

## 9. نتيجة القبول

يُعتبر تثبيت Windows مقبولاً عندما ينجح التشخيص، وتظهر CUDA فعلياً على RTX 3060 أو يظهر CPU كخيار صريح، وتعمل معالجة محلية قصيرة، ويعمل preflight قبل القص، ويُنشأ `polish_report.json` و`tracking_report.json` صالحان، ولا توجد نوافذ مصدر مكررة في `viral_segments.txt`, ويعمل Dry Run على كل الملفات، وتُحفظ أوقات جدولة مستقلة لأكثر من خمسة مقاطع، ويُنشأ backup آمن، ولا تظهر أخطاء batch من نوع `was unexpected` أو `not recognized` أو مسارات cache غير مقصودة على `C:`.
