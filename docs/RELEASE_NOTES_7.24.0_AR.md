# إصدار OUSSAMA Cutter 7.24.0-pro — مركز التحكم المحلي عبر Telegram

تضيف هذه النسخة مركز **Telegram Control Center** إلى أساس OUSSAMA Cutter 7.23.0-pro الذي يتضمن مولد الصور المصغرة وكشف حدود المشاهد. الدمج انتقائي؛ لم تُحذف تحسينات 7.23 أو محرك كشف المشاهد أو حماية الأصالة ومنع التكرار.

## مركز التحكم

يعمل المركز اختيارياً داخل عملية OUSSAMA Cutter على Windows باستخدام اتصال HTTPS صادر وlong polling. لا يستخدم webhook ولا يفتح منفذاً عاماً ولا ينقل الفيديوهات أو ملفات `VIRALS` أو تقارير المشروع إلى Telegram. لا يبدأ إلا عند ضبط `VIRALCUTTER_TELEGRAM_ENABLED=1` وBot Token وChat IDs صحيحة.

تدعم الخدمة `/help` و`/status` و`/projects` و`/audit <project>` و`/pause` و`/resume` و`/retry_failed` و`/cancel <job_id>`. يحتاج `/cancel_all` إلى `/confirm_cancel_all` خلال 60 ثانية ومن نفس Chat ID. لا يوجد `/start` أو `/upload` أو `/publish` من Telegram، ولا يقبل المركز ملفات أو OAuth credentials أو مفاتيح Gemini أو أوامر shell.

أضيفت إشعارات lifecycle اختيارية عند انتهاء المهمة بالحالات succeeded أو failed أو cancelled. تبقى الإشعارات مغلقة افتراضياً، ولا ترسل backlog القديم عند إعادة التشغيل، وتحتوي على الحالة ومعرّف المهمة المختصر دون مسار كامل أو log أو ملف وسائط.

## الحماية والتشخيص

يُقرأ Bot Token من environment فقط. تعرض WebUI حالة disabled أو configuration incomplete أو ready وعدد Chat IDs فقط، ولا تعرض Token. الدردشة غير الموجودة في allowlist لا تتلقى رداً. يظل requests اعتماداً صريحاً في `requirements.txt` و`pyproject.toml`، بينما Telegram غير حرج لتشغيل القص والواجهة إذا كان غير مفعّل أو ناقص الإعداد.

أضيف `check_telegram_config` إلى preflight و`_telegram_check` إلى Windows diagnostics. كلاهما يميز disabled وmissing token وmissing allowlist وready دون طباعة القيمة السرية. كما أضيفت اختبارات لحالات التشخيص واختبارات الوحدة للخدمة والأوامر والتأكيدات والإشعارات.

## إعداد Windows

استخدم `telegram_control.example.ps1` كنموذج، وانسخه إلى `telegram_control.local.ps1` خارج Git وZIP. أنشئ البوت من [@BotFather](https://telegram.me/BotFather)، واستخرج Chat ID محلياً من جهازك، ولا ترسل Token في المحادثة أو الدعم. يجب أن تبقى عملية OUSSAMA Cutter مفتوحة حتى يستمر polling. التفاصيل الكاملة في `docs/TELEGRAM_CONTROL_AR.md` و`docs/RELEASE_CHECKLIST_WINDOWS.md`.

## التحقق والحدود

أثبت regression الكامل **839 اختباراً مجمعاً** بعد الدمج، مع نجاح `ruff` و`compileall` وفحص `uv.lock`.
 الاختبارات المحلية تستخدم FakeSession وFakeQueue؛ لم تُرسل رسالة Telegram حقيقية ولم يُنفذ OAuth أو رفع YouTube حقيقي. اختبار RTX 3060/CUDA وWhisperX وBot Token وقناة YouTube يجب تنفيذه على جهاز Windows المستخدم.

وفق وثائق Telegram، لا يعمل `getUpdates` مع webhook مفعّل، ولذلك تعتمد هذه النسخة على long polling فقط ولا تحتاج منفذاً عاماً [1]. وتوضح وثائق Telegram أن Token اعتماد سري يجب حمايته [2].

## المراجع

[1]: https://core.telegram.org/bots/api "Telegram Bot API — getUpdates and webhooks"

[2]: https://core.telegram.org/bots/tutorial "Telegram — From BotFather to Hello World"
