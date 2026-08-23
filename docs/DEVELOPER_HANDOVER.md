# 📋 تقرير تسليم المطور — Developer Handover Report

> **الغرض**: هذا الملف موجّه لأي مطوّر (إنسان أو ذكاء اصطناعي) يريد أن يعرف **ماذا بُني، كيف يعمل، وأين يستمر** — بدون إعادة اكتشاف الكود من الصفر.
>
> **التاريخ**: 2026-08-04 — آخر تحديث: `ff72749` (v4)

---

## 1) لمحة عامة — What This Project Is

**ViralCutter** = بديل مفتوح المصدر لـ Opus Clip: يحوّل فيديو يوتيوب طويل إلى مقاطع قصيرة (Shorts/Reels/TikTok) بالذكاء الاصطناعي:

```
download (yt-dlp) → transcribe (WhisperX) → اختيار المقاطع الفيروسية (LLM)
→ قص → تتبع وجه/قص عمودي → ترجمات ديناميكية → حرق الترجمة → تقارير أمان
```

- **اللغة**: Python (واجهة Gradio + CLI `main_improved.py`).
- **نظام التشغيل المستهدف**: ويندوز (سكربتات `.bat`)، لكن الكود متعدد المنصات.
- **المالك**: يستخدمه على ويندوز، محتواه عربي، أولويته القصوى: **عدم تلقي مخالفات يوتيوب ولا توقيف أرباح**.

---

## 2) ماذا بُني في هذه الجلسة (4 إصدارات، كلها على `main`)

### 🛡️ v1 — `a16e928` — فلتر خطاب الكراهية
| الملف | الدور |
|---|---|
| `scripts/safety_filter.py` | فلتر كلمات محلي 100% (عربي فصحى + لهجات، إنجليزي، برتغالي، فرنسي، إسباني، تركي). تطبيع مضاد للتحايل: تشكيل، تطويل، leetspeak، "ال" الملتصقة، تكرار الحروف. أوضاع: `block/flag/off`. |
| `prompt.txt` | قاعدة 5: تُمنع الـ LLM من اختيار مقاطع خطاب كراهية/عنف |
| `scripts/save_json.py` | أُضيف معامل `overwrite=True` |
| `main_improved.py` | ربط الفلتر بعد محاذاة المقاطع وقبل القص + `--safety-mode` |
| `webui/pipeline.py` + `webui/app.py` | خيار "🛡️ فلتر الأمان" في الواجهة |
| `safety_terms.example.json` | مصطلحات مخصصة |

### 🔇 v2 — `ce6577b` — الكتم Bleep + مراجعة AI + قائمة سماح
| الملف | الدور |
|---|---|
| `scripts/censor_engine.py` | **الكتم**: توقيت الكلمة من `input.json` (بمستوى الكلمة) → كتم الصوت بـ ffmpeg `volume=0:enable='between(t,a,b)'` + إخفاء الكلمة `████` في ترجمات `subs/*.json` → `censor_map.json`. المقطع يبقى! |
| `scripts/safety_ai.py` | **مراجعة سياقية** بـ Gemini/G4F للمقاطع الناجية (`--safety-ai on` افتراضي). تمسك الكراهية بدون كلمات محظورة. لا تكسر المعالجة عند الفشل. |
| `safety_filter.py` | وضع `censor` + `allow_terms` (استثناء الحظر الخاطئ) |
| `webui/segments_review.py` | عمود "الأمان" في تبويب المراجعة (✅/⚠️/🔇/🤖⚠️) |

### 🔄 v3 — `85ee01f` — تحديث تلقائي لقائمة الكلمات
| الملف | الدور |
|---|---|
| `scripts/safety_updater.py` | يسحب القائمة الرسمية `safety_blocklist.json` من GitHub مرة/24ساعة (خانق زمني، آمن دون إنترنت، urllib خالص). |
| `safety_blocklist.json` | القائمة الرسمية (v1 = 137 مصطلحاً). |
| `scripts/export_blocklist_pack.py --version N` | للمطوّر: تصدير القائمة المدمجة للـ repo (مهم: **ارفع الإصدار عند كل تعديل** وإلا تُتجاهل). |
| دمج تلقائي: كاش القائمة يدخل الفلتر و الكتم معاً. | |

### 📊 v4 — `ff72749` — بطاقة المخاطر + حماية المحتوى المُعاد استخدامه
| الملف | الدور |
|---|---|
| `scripts/risk_scorecard.py` | **بطاقة امتثال لكل مقطع** بعد التقديم: محور `reuse` (تطابق dHash بين المقطع النهائي ونافذة المصدر — >70% = خطر مُعاد استخدامه)، `first7s` (كلمة مخالفة في أول 7 ثوانٍ = إعلانات محدودة)، `visual` (كشف الأشرطة السوداء + خطاف لنموذج ONNX)، `overall` (low/medium/high/danger). |
| `main_improved.py` | `--risk-scorecard on` (افتراضي) + `--risk-gate warn\|block\|off` |
| النواتج | `risk_scorecard.json` + `publish_blocklist.json` (قائمة الممنوع رفعه) |

---

## 3) تدفق البيانات داخل "منظومة الأمان" (الأهم للمطوّر القادم)

```
main_improved.py
├─ 3.7  safety_filter.apply_safety_filter(...)      ← فلتر كلمات + censor/flag/block
│        (يستورد load_remote_terms() من كاش التحديث التلقائي)
├─ 3.8  safety_ai.review_segments(...)              ← مراجعة Gemini/G4F (فقط gemini/g4f)
│        (المخالفات السياقية تُدمج في safety_report.json)
├─ 4    cut_segments.cut(...)                       ← cuts/*_original_scale.mp4 + subs/*_processed.json
├─ 4.5  censor_engine.censor_project(...)           ← لو safety_mode == "censor": كتم + إخفاء
├─ 5    edit_video.edit(...)                        ← ينسخ صوت القص (-acodec copy) → الكتم يبقى ✅
├─ 6    adjust_subtitles + burn_subtitles           ← يقرأ subs/*.json (الترجمة المخفاة تبقى ✅)
└─ 6.5  risk_scorecard.analyze_project(...)         ← بطاقة مخاطر + بوابة نشر (بعد الحرق)
```

**قاعدة ذهبية**: الكتم يجب أن يحدث **بعد القص وقبل المونتاج** — لأن `edit_video` ينسخ صوت الملف المقطوع، وأي تغيير لاحق على الترجمة يتم من `subs/*.json`.

---

## 4) خريطة الملفات المهمة

| المسار | ماذا يحتوي |
|---|---|
| `main_improved.py` (918 سطر) | الـ CLI الرئيسي + كل نقاط الربط أعلاه |
| `webui/app.py` (1100+ سطر) | واجهة Gradio العربية (RTL) |
| `scripts/create_viral_segments.py` | استدعاءات LLM + محاذاة المقاطع ⚠️ **يغلف sys.stdout عند الاستيراد — كسر pytest capture** |
| `scripts/cut_segments.py` | القص + توليد `subs/*.json` من `input.json` |
| `scripts/edit_video.py` | تتبع الوجه + القص العمودي + مزج الصوت |
| `i18n/locale/*.json` | ترجمات: `ar_SA` (افتراضي)، `en_US`، `pt_BR`، `tr_TR` |
| `tests/` | 196 اختباراً — كلها تنجح (`python3 -m pytest tests/`) |

---

## 5) قواعد/محاذير حافظ عليها (Gotchas)

1. **لا تستورد `scripts/create_viral_segments.py` داخل اختبارات pytest** — يلتف على `sys.stdout` ويكسر الالتقاط. (لهذا `safety_filter.load_transcript` يكرر محلل TSV/SRT محلياً).
2. **i18n صارم**: كل مفتاح في `en_US.json` يجب أن يوجد في اللغات الثلاث الأخرى (اختبار `test_i18n_completeness.py`)؛ قيم العربية يجب أن تكون عربية؛ حافظ على عدد `{}` في الترجمة.
3. **`save_viral_segments`** لا يكتب فوق ملف موجود إلا بـ `overwrite=True`.
4. **Bleep يتطلب `input.json` بمستوى الكلمة** (Whisper). مع ترجمات يوتيوب الجاهزة لا يوجد `words` → الكتم يُتخطى بصمت (وضع block يعمل دائماً).
5. **قائمة السماح (`allow_terms`)** تُطبَّق بعد كل شيء (مدمجة + محدثة + مخصصة).
6. **`export_blocklist_pack.py`**: ارفع `--version` وإلا لن يقبلها العملاء (المساواة/الأقدم تُرفض).
7. ffmpeg مطلوب؛ الاختبارات الواقعية للفيديو تتخطى نفسها إن لم يوجد.

---

## 6) ما لم يُنفَّذ بعد (Backlog — الأفكار التالية)

| الأولوية | الفكرة | ملاحظات |
|---|---|---|
| 🥇 | **فحص بصري بنموذج حقيقي** (NudeNet-lite / CLIP على فريمات) | البنية جاهزة في `risk_scorecard` (خطاف `visual_model_path`) — ينقص تحميل نموذج ONNX |
| 🥇 | **ملف .exe واحد** (PyInstaller) | ✅ مكتمل — يُبنى تلقائياً في build-exe.yml على أي tag v* |
| 🥈 | **الرفع المباشر لـ YT/TikTok** + بوابة رفض إجبارية | ✅ مكتمل في v6.10.0 (`upload_gate.py`) |
| 🥈 | **حذف الصمت/الحشو (Jump Cuts)** | ✅ مكتمل في مرحلة polish (v6.11) |
| 🥈 | **زوم Punch-in** | ✅ مكتمل في مرحلة polish (v6.11) |
| 🥉 | **حلقة تغذية راجعة من الضربات** | ✅ مكتمل في v6.13.0 (`strike_feedback.py`) |
| 🥉 | **موسيقى خلفية + Auto-Duck** | ✅ مكتمل في polish (`background_music.py`) |
| 🥉 | **أبعاد إخراج غير 9:16** | ✅ مكتمل في v6.13.0 (`reframe.py` — 4:5/1:1/16:9) |
| 🥉 | **تحليلات الأداء (YouTube Analytics)** | ✅ مكتمل في v6.13.0 (`analytics.py` — يحتاج تفعيل Analytics API) |
| ⏳ | **استبدال حلقة OpenCV بـ ffmpeg pipe** | A/V desync جذري — تعديل عالي الخطورة، يُؤجَّل |
| ⏳ | **Demo على Hugging Face Spaces** | يحتاج حساب/هوية HF |

---

## 7) كيف تتحقق من سلامة كل شيء

```bash
python3 -m pytest tests/                  # 196 اختباراً
python scripts/export_blocklist_pack.py --version 2   # عند إضافة كلمات (ارفع الرقم!)
python scripts/safety_filter.py --project tmp/مشروع --mode block --in-place   # فلترة مشروع قديم
python scripts/risk_scorecard.py --project tmp/مشروع --exit-on-blocked        # بطاقة مخاطر يدوياً
```

---

## 8) ملاحظات عن المستخدم (لخدمة أفضل)

- يتحدث العربية (لهجة جزائرية) — **ردّ عليه بالعربية**.
- يستخدم المشروع على **ويندوز**.
- أولويته القصوى: **لا مخالفات ولا توقيف أرباح** على قناته في يوتيوب.
- وافق مسبقاً على التطوير المباشر على `main` + الرفع (لدى الوكيل صلاحية Push كاملة).
- يقدّر المتابعة السريعة والإجابة المباشرة.

---

## English Summary (for non-Arabic readers)

This repo is **ViralCutter**, an open-source Opus Clip alternative. On 2026-08-04 the owner asked for protection against YouTube strikes ("hate speech" + "reused content") on his Shorts channel. Four versioned commits built a complete compliance layer:

1. **v1 (`a16e928`)** — offline multilingual keyword filter (`scripts/safety_filter.py`, modes block/flag/off) + hardened LLM prompt.
2. **v2 (`ce6577b`)** — **bleep mode** (`scripts/censor_engine.py`: word-level mute via ffmpeg + `████` subtitle masking, keeps the clip), **contextual AI review** (`scripts/safety_ai.py`, Gemini/G4F), `allow_terms` false-positive control, safety badges in the Review tab.
3. **v3 (`85ee01f`)** — **auto-updating word list** (`scripts/safety_updater.py`): daily fetch of the versioned `safety_blocklist.json` pack from this repo (offline-safe, throttle, pure urllib); maintainers publish words via `scripts/export_blocklist_pack.py --version N`.
4. **v4 (`ff72749`)** — **per-clip Risk Scorecard** (`scripts/risk_scorecard.py`): `reuse` axis = dHash frame similarity final-vs-source (>70% → reused-content risk → `publish_blocklist.json`), `first7s` profanity = limited ads, letterbox detection + local ONNX visual hook, overall low/medium/high/danger; `--risk-gate warn|block|off`.

**Pipeline integration** lives in `main_improved.py` (steps 3.7 keyword filter → 3.8 AI review → 4 cut → 4.5 bleep → 6 burn → 6.5 scorecard). Key gotchas: don't import `create_viral_segments` in pytest (stdout wrapper), i18n parity across 4 locales, bleep needs word-level `input.json`, bump pack version on export. 196 tests pass. Backlog: real visual model, one-click .exe, direct upload w/ mandatory gate, silence removal, punch-in zoom, strike feedback loop.
