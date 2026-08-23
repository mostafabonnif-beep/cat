# إصدار 7.21.0-pro — Docker، المعالجة الدفعية، تقرير HTML محسّن

تاريخ الإصدار: 2026-08-23

## 🐳 Docker — تشغيل بضغطة واحدة

- **`Dockerfile` + `docker-compose.yml`**: حاوية كاملة (Python 3.11 + ffmpeg +
  yt-dlp + Whisper CPU + OpenCV + Gradio). الأمر:
  ```
  docker compose up --build
  ```
  ثم افتح http://localhost:7860. مجلد `./VIRALS` مربوط للتخزين الدائم،
  و`./models` لنماذج LLM المحلية، مع فحص صحة (healthcheck) تلقائي.

## ⚡ المعالجة الدفعية (Batch) — قائمة روابط بأمر واحد

- **`scripts/batch_process.py`**: ملف نصي فيه رابط لكل سطر → يعالج الجميع
  تسلسلياً (تحميل ← تقطيع ← ترجمات ← أمان ← رفع اختياري):
  ```
  python scripts/batch_process.py urls.txt --segments 4 --live-wait 360 --upload --dry-run
  ```
- يدعم: `--viral`, `--themes`, `--sponsorblock`, `--quality`,
  `--safety-mode`, `--upload/--dry-run/--privacy`, `--stop-on-error`,
  وتمرير أعلام إضافية عبر `--extra key=value`.
- يكتب **`batch_report.json`** بنتيجة كل رابط (نجاح/فشل، المدة، الرفع).

## 📊 تقرير HTML احترافي موسّع

- `project_report.html` يضم الآن ثلاثة أقسام جديدة:
  - **التتبع**: الخلفية، المتحدث النشط، التمليس، Headroom، التحذيرات.
  - **حماية المحتوى المكرر**: المحجوب/المُبقى، حالة قاطع دائرة القناة.
  - **النشر**: نجاحات/إخفاقات الرفع وآخر دفعة.

## ✅ اختبارات جديدة لوحدات لم تكن مغطاة

- `audio_analysis` (استخراج طاقة الصوت — مع اختبار ffmpeg حقيقي).
- `organize_output` (تنظيم الملفات النهائية + تعقيم الأسماء).
- `batch_process` (قراءة الروابط، بناء الأوامر، الرفع بلا مقاطع).
- `Dockerfile`/`docker-compose.yml` (الوجود والمحتوى).
- `project_report` HTML (الأقسام الجديدة).

## ✅ الجودة

- **793 اختباراً ناجحاً** (+11 جديدة).
