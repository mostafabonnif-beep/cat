# إصدار 7.23.0-pro — الصور المصغرة + كشف المشاهد (دمج مشاريع مفتوحة)

تاريخ الإصدار: 2026-08-23

## 🖼️ مولّد الصور المصغرة (جديد — كان أكبر فجوة)

- **`scripts/thumbnail_generator.py`**: صور مصغرة احترافية 1280x720 من أي
  مقطع أو صورة:
  - **تأطير ذكي للوجه**: يكشف الوجه (OpenCV FaceDetectorYN / Haar) ويضعه
    في الثلث العلوي — تركيبة الصور المصغرة الكلاسيكية على يوتيوب.
  - **نص عربي كامل**: خط **Cairo** (Google Fonts، رخصة OFL) للنصوص العربية
    + Montserrat للاتينية — مع التفاف ذكي للأسطر.
  - **تصميم احترافي**: شارة الخطاف (hook) في الأعلى، العنوان في الأسفل
    بحدود سوداء، تدرّج خلفي للقراءة، شريط لون مميز قابل للتخصيص.
  ```
  python scripts/thumbnail_generator.py video.mp4 --title "كسب المال من الانترنت" --hook "سر!" --out thumb.png
  ```
- أُضيف `Pillow` إلى المتطلبات (OpenCV موجود أصلاً).

## 🎬 كشف المشاهد (دمج PySceneDetect — BSD-3، 5000+ نجمة)

- **`scripts/scene_detect.py`**: يكشف حدود المشاهد/القطع عبر **PySceneDetect**
  (المكتبة القياسية) مع **fallback بـ OpenCV** عند غيابها:
  ```
  python scripts/scene_detect.py video.mp4 --list
  ```
- **`--scene-snap`** (CLI): يثبّت حدود كل مقطع على أقرب مشهد عند القص —
  **لا مقطع يبدأ أو ينتهي في منتصف لقطة متحركة**:
  ```
  python main_improved.py --url ... --scene-snap
  ```
- أُضيف `scenedetect` إلى المتطلبات (اختياري — fallback يعمل بدونه).

## ✅ لماذا هذان المشروعان تحديداً (نتيجة البحث)

| المشروع المكتشف | الحكم |
|---|---|
| PySceneDetect | ✅ **دُمج** — BSD-3 متوافق، معيار الصناعة لكشف المشاهد |
| مولّدات الصور المصغرة (عدة مشاريع) | ✅ **بُنيت ميزة أصلية** مستوحاة من أفضل ممارساتها (وجه + عربي + خطاف) |
| stable-ts / whisper-timestamped | ⏸️ المشروع يستخدم WhisperX أصلاً (توقيت كلمات أدق) — فائدة هامشية |
| LibreTranslate | ⏸️ المشروع يستخدم deep-translator؛ LibreTranslate يحتاج خادماً مستقلاً |
| youtube-analyzer / dashboards | ✅ التحليلات موجودة أصلاً (`scripts/analytics.py`) |
| B-roll APIs (Pexels/Pixabay) | ✅ Pexels مدمج أصلاً (`scripts/broll_engine.py`) |
| OpenMontage / supoclip / clippyme | ❌ مكررة لما نملكه أو ثقيلة (React/Docker مكدس كامل) |

## ✅ الجودة

- **825 اختباراً ناجحاً** (+11 اختباراً جديداً).
