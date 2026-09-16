# دليل الإسهام السريع — Quick Contributing

> التفاصيل الكاملة في [CONTRIBUTING.md](CONTRIBUTING.md). هذا ملخّص عملي.

## 1. التهيئة (مرة واحدة)

```bash
git clone https://github.com/mostafabonnif-beep/cat.git
cd cat
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
make dev-install        # التبعيات + pre-commit hooks
cp .env.example .env    # ثم املأ المفاتيح
```

## 2. قبل كل commit

```bash
make fmt     # تنسيق تلقائي
make lint    # فحص
make test    # الاختبارات
```

الـ hooks تعمل تلقائياً عند git commit وتمنع: مفاتيح مسرّبة، ملفات ضخمة، print منسي، أخطاء تنسيق.

## 3. قواعد ذهبية للمشروع

| القاعدة | السبب |
|---|---|
| **الكتم (censor) قبل حرق الترجمة** | وإلا ظهر النص المحظور في الفيديو |
| **لا تعطّل upload_gate** | البوابة الأخيرة قبل النشر — تمنع مخالفات يوتيوب |
| **كل ميزة = اختبار** | tests/test_<الميزة>.py |
| **لا مفاتيح في الكود** | استخدم .env أو api_config.local.json |
| **الملفات الضخمة** | webui/app.py و create_viral_segments.py ضخمة — أضف الجديد في وحدة منفصلة |

## 4. رسائل الـ commit

```
feat(subtitles): إضافة تأثير bounce
fix(upload): معالجة انتهاء التوكن
test(safety): تغطية allow_terms
docs(readme): تحديث التثبيت
chore(deps): ترقية yt-dlp
```

## 5. فتح PR

1. فرع: git checkout -b feat/اسم-الميزة
2. make lint && make test — يجب أن تنجح
3. املأ قالب PR واذكر الاختبارات المضافة
4. CI يفحص على Python 3.10 / 3.11 / 3.12
