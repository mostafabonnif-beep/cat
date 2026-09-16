# دليل البدء السريع للمساهمة

مرحباً بك في **cat** (OUSSAMA Cutter / ViralCutter). هذا الدليل يوصلك من صفر إلى أول Pull Request في دقائق.

## 1. التهيئة

```bash
git clone https://github.com/mostafabonnif-beep/cat.git
cd cat
python3 -m venv .venv && source .venv/bin/activate
make dev-install
cp .env.example .env      # ثم املأ المفاتيح المطلوبة
```

> ملف `.env.example` يوثّق 61 متغيّراً بيئياً مُستخرَجاً آلياً من الكود، مقسّمة إلى أقسام مع شرح لكل متغيّر.
> **لا تضع أي مفتاح حقيقي في `.env.example`** — المفاتيح تبقى في `.env` المحلي فقط.

## 2. الأوامر اليومية

| الأمر | الوظيفة |
|---|---|
| `make help` | عرض كل الأوامر المتاحة |
| `make fmt` | تنسيق الكود وإصلاح الملاحظات تلقائياً |
| `make lint` | فحص الكود دون تعديله (نفس ما يفحصه CI) |
| `make test` | تشغيل الاختبارات |
| `make cov` | الاختبارات + تقرير التغطية |
| `make audit` | تدقيق أمني للاعتماديات |
| `make clean` | تنظيف الملفات المؤقتة |

## 3. معايير الكود

- **Ruff** هو المرجع الوحيد للتنسيق والفحص (`ruff.toml`): طول السطر 100، اقتباس مزدوج، نهايات أسطر LF.
- **pre-commit** يعمل تلقائياً قبل كل commit ويشمل: تنظيف المسافات، فحص YAML/TOML/JSON، منع الملفات الضخمة، وكشف الأسرار عبر **gitleaks**.
- إن فشل الخطّاف وعدّل ملفات، أضِف التعديلات (`git add -A`) ثم أعِد الـ commit.

## 4. سير العمل

```bash
git checkout -b feat/وصف-قصير
make fmt && make lint && make test
git commit -m "feat: وصف واضح للتغيير"
git push -u origin HEAD
```

ثم افتح Pull Request نحو `main`.

## 5. الملفات الحسّاسة

أي تعديل على منطق السلامة (`safety_filter.py`, `upload_gate.py`, `censor_engine.py`) أو على ملفات `.github/workflows/` يستدعي مراجعة إلزامية بحسب `.github/CODEOWNERS`. اشرح في وصف الـ PR سببَ التغيير وأثرَه.

## 6. قواعد الأمان

- لا تضع مفتاحاً أو توكن في الكود أو في وصف الـ PR.
- لا تُلغِ خطّافات pre-commit بـ `--no-verify`.
- إن تسرّب مفتاح: ألغِه من لوحة المزوّد فوراً ثم أنشئ بديلاً.
