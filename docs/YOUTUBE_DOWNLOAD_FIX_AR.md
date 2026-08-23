# إصلاح تنزيل YouTube في OUSSAMA Cutter 7.11.4-pro

## التشخيص

السجل المرفق لا يشير إلى مشكلة في Torch أو WhisperX أو RTX 3060. الفشل حدث قبل التفريغ الصوتي داخل yt-dlp:

| الرسالة | المعنى |
|---|---|
| `No supported JavaScript runtime could be found` | YouTube يحتاج Runtime لجافاسكربت الحديث، والأفضل Deno. |
| `HTTP 429` عند subtitles | YouTube رفض طلب ملف الترجمة بسبب rate limit. |
| `HTTP 403` عند video data | YouTube رفض صيغة أو Player client المستخدم، أو يحتاج cookies/تحققاً إضافياً. |
| `google-auth-oauthlib missing` | تحذير خاص برفع YouTube OAuth، وليس سبب فشل التنزيل. |
| `pyacoustid missing` | تحذير خاص ببصمة الموسيقى، وليس سبب فشل التنزيل. |
| مفتاح Gemini لا يبدأ بـ `AIza` | المفتاح الحالي ليس مفتاح Gemini صالحاً غالباً، وسيؤثر على تحليل المقاطع بعد نجاح التنزيل. |

## ما تم إصلاحه داخل البرنامج

أصبح البرنامج ينزّل الفيديو أولاً دون ربطه بالترجمة. بعد نجاح الفيديو يحاول تنزيل الترجمة كعملية اختيارية منفصلة. إذا ظهر 429 أو 403 في الترجمة يبقى الفيديو صالحاً، ويستطيع WhisperX تفريغه من الصوت.

وأضيف إصلاح خاص لحالة ظهرت في السجل: yt-dlp طبع `Download concluído` عند 100% لكنه كتب الملف باسم `input` بلا امتداد، بينما كان النظام يبحث فقط عن `input.mp4`. الإصدار 7.11.4-pro يبحث عن `input` وامتدادات الفيديو المعروفة، ثم يعتمد الملف أو يحوله إلى `input.mp4` باستخدام FFmpeg بدلاً من إعلان فشل كاذب أو إعادة تنزيل الفيديو.

عند 403 في الفيديو يجرب البرنامج بالتتابع صيغاً وواجهات Player clients بديلة، ثم يعرض رسالة واضحة إذا فشلت كل المحاولات. كما يمرر مسار Deno تلقائياً إذا كان موجوداً في PATH أو في `VIRALCUTTER_DENO_PATH`.

## الإصلاح الموصى به على Windows

من PowerShell داخل `D:\SS`:

```powershell
cd D:\SS
uv pip install --python .\.venv\Scripts\python.exe --upgrade "yt-dlp[default]"
```

ثبّت Deno 2.3 أو أحدث من الموقع الرسمي، ثم أغلق PowerShell وافتحه من جديد وتحقق:

```powershell
deno --version
.\.venv\Scripts\python.exe -c "import yt_dlp; print(yt_dlp.version.__version__)"
```

إذا كان Deno مثبتاً في مكان غير موجود داخل PATH، عرّف مساره قبل تشغيل التطبيق:

```powershell
$env:VIRALCUTTER_DENO_PATH = "C:\Tools\deno.exe"
.\run_webui.bat
```

أو استخدم الملف المحسن:

```powershell
.\install_dependencies.bat upload
```

وإذا أردت التفريغ الكامل مع NVIDIA:

```powershell
.\install_dependencies.bat gpu full upload
```

## عند ظهور 403 بعد تثبيت Deno

قد يكون الفيديو خاصاً أو مقيداً بالعمر أو أن YouTube طلب جلسة موثقة. في هذه الحالة اختر كوكيز المتصفح من WebUI، أو شغّل من PowerShell:

```powershell
.\.venv\Scripts\python.exe main_improved.py `
  --url "رابط الفيديو" `
  --cookies-from-browser chrome
```

لا تستخدم cookies إلا من حسابك وبطريقة قانونية، ولا تضع ملف cookies داخل Git أو الأرشيف أو ترسله إلى أي جهة.

## مفتاح Gemini

السجل يوضح أن المفتاح الحالي لا يشبه مفتاح Gemini المعتاد. من WebUI افتح إعدادات الذكاء الاصطناعي وأدخل مفتاحاً صحيحاً من Google AI Studio، أو استخدم `manual`/`local` مؤقتاً إذا لم ترد استعمال Gemini:

```powershell
# اختبار المتغير قبل التشغيل، دون طباعة المفتاح نفسه
if ($env:GEMINI_API_KEY -notmatch '^AIza') { Write-Host "Gemini key needs replacement" }
```

## الحدود

لا يستطيع fallback تجاوز فيديو خاص بلا صلاحية، أو حظراً شبكياً دائماً، أو rate limit مستمراً من YouTube. في هذه الحالات يكون الحل الصحيح هو انتظار فترة قصيرة، استخدام cookies لحساب يملك صلاحية، أو استعمال ملف فيديو محلي. البرنامج الآن لا يعيد محاولة subtitles بطريقة تجعل فشلها يفسد تنزيل الفيديو.

## مراجع رسمية

توضح وثائق yt-dlp أن YouTube يحتاج Runtime خارجياً مثل Deno لتحديات JavaScript، وأن حزمة PyPI تحتاج `yt-dlp[default]` أو `yt-dlp-ejs` لتوفير سكربتات EJS [1].

[1]: https://github.com/yt-dlp/yt-dlp/wiki/EJS "yt-dlp EJS official setup guide"
