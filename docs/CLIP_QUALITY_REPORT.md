# ViralCutter v7.40 — Clip-Quality Implementation Report

_Date: 2026-09-12 · Change set: clip selection, timestamps, titles, Arabic, cache staleness_
_See `docs/CLIP_QUALITY_DIAGNOSIS.md` for the full technical diagnosis._

## 1. Root-cause diagnosis (summary)

| Reported problem | Root cause found in the actual code |
|---|---|
| Poor clip choices | `selection_score` was 40% the LLM's own `score`, and every missing editorial component silently fell back to that same number — no deterministic signal (real window text, silence, boundary completeness) influenced ranking. |
| Mid-sentence starts/ends | Snapping used pause-based speech blocks only (0.35 s), never real sentence units; word-level timestamps in `input.json` were never read; no connector/dangling-ending handling; no pre/post-roll existed. |
| Weak context | Window text was computed but only used for title relevance — never for standalone-context scoring or repair. |
| Repetitive clips | Dedup was temporal-overlap only; same idea in different words produced multiple clips. |
| Inaccurate/generic/hallucinated titles | Titles came from the LLM over a whole transcript chunk and were only *ranked*, never *validated* against the exact clip window; invented numbers/names/results survived. |
| Arabic title issues | No language-match validation; clickbait patterns unpenalized; normalization existed for matching but not for title fact-checking. |
| Stale results reused | `_segment_settings_fingerprint` covered only count/min/max/chunk/title_language — not viral mode, AI backend, models, scene-snap, weights, or prompt version. |

## 2. Exact files modified / added

**Added (4 modules + 2 docs + 1 test file):**
- `scripts/clip_scoring.py` — centralized 11-factor weights + `compute_final_score`, env overrides, editorial floor.
- `scripts/transcript_window.py` — exact window analysis, sentence units, word-boundary snapping, connector/dangling repairs, pre/post-roll, semantic similarity with false-positive guards, word-timing loader.
- `scripts/title_factual.py` — hallucination checks (numbers/names vs exact window text), clickbait penalties, 8-part title scoring, language match, conservative fallbacks, `build_title_data`.
- `scripts/arabic_text.py` — canonical Arabic/digit normalization (re-exported by `create_viral_segments` under its historical private names).
- `tests/test_v740_clip_quality.py` — 74 tests across the 26 required areas.
- `docs/CLIP_QUALITY_DIAGNOSIS.md`, `docs/CLIP_QUALITY_REPORT.md` (this file).

**Modified (6):**
- `scripts/create_viral_segments.py` — boundary refinement in `process_segments`, `_validate_segment_window` with explicit rejection reasons, `_compute_factor_scores`, semantic repetition penalties, two-level `deduplicate_segments`, extended additive JSON fields, `prompt_version_fingerprint`/`SEGMENTS_SCHEMA_VERSION`, `selection_config` block, Arabic helpers delegating to `arabic_text`.
- `main_improved.py` — `_segment_settings_fingerprint` extended (viral, themes, ai_backend, ai_model_name, transcription model, scene_snap, prompt/scoring versions); `--force-regenerate` alias for `--force-new-segments`.
- `prompt.txt` — strict factual-title rules, clip-vs-full-transcript distinction, clean-boundary rules, reject-instead-of-guess (placeholders unchanged).
- `app_version.py`, `pyproject.toml`, `README.md`, `README_en.md`, `README_ar.md`, `changelog.md` — 7.40.0-pro version bump per the repo's version-consistency contract (test_version_consistency passes).

## 3. Important fixes explained

**Centralized scoring.** One documented object `DEFAULT_SELECTION_WEIGHTS` (sums to 1.00) implements the spec formula exactly; `VIRALCUTTER_SELECTION_WEIGHTS` merges a JSON override; `VIRALCUTTER_MIN_FINAL_SCORE` (default 0/off) drops weak candidates so thin videos yield **fewer, stronger clips**. Genuine LLM self-evaluations are used per-component when present; deterministic window heuristics fill every gap.

**Boundaries.** Refinement runs on the same eligibility contract as the existing snapping (text-matched or clamp-adjusted windows; clean explicit AI windows stay byte-exact — the documented legacy contract and its tests are untouched). Order: word-edge snap (WhisperX words when available) → Arabic connector-start repair (include antecedent within 8 s) → dangling preposition/conjunction-end repair (finish the sentence) → configurable pre/post-roll (`VIRALCUTTER_PRE_ROLL` clamped to 0.20–0.50, `VIRALCUTTER_POST_ROLL` to 0.30–0.80, default off for byte-exact backwards compatibility) → duration re-check (roll never pushes past max duration; post-roll shrinks first). Millisecond precision (3-decimal rounding).

**Validation.** `_validate_segment_window` rejects with explicit messages: `end_time <= start_time`, negative/invalid timestamps, under-min duration (unless transcript-limited), insufficient transcript text, excessive silence (<5% speech). Soft issues (mid-sentence edges, connector openers, low speech density, long lead/trail silence) become `quality_flags` and score penalties; `VIRALCUTTER_STRICT_BOUNDARIES=1` upgrades them to rejections.

**Deduplication.** Temporal level unchanged (≥60% of the shorter window). New semantic level: normalized window-transcript similarity ≥0.75 (`VIRALCUTTER_SEMANTIC_DUP_THRESHOLD`) drops the weaker clip; 0.55–0.75 feeds `repetition_penalty`. Guards: ≥6 words required, differing stated numbers ≠ same fact, char-similarity without token overlap ≠ duplicate, temporal near-duplicates are left to the temporal level.

**Titles.** Every candidate title is scored on 8 axes (factual accuracy weighted highest). Numbers and Latin name tokens must appear in the exact window text; clickbait patterns (لن تصدق / سر خطير / اكتشاف صادم / …) are always penalized and rejected when unsupported; title language must match the clip's dominant language; primary is capped at the 100-char publish limit on word boundaries. When all candidates fail, a conservative transcript-derived fallback ships with `title_confidence: 35` and the replaced LLM title preserved in `title_data.llm_title_replaced`.

**Cache.** Saved payloads now fingerprint the full selection-relevant configuration; any change regenerates instead of reusing. `--force-regenerate` forces fresh segments explicitly. No user files are ever deleted.

## 4. Before / after — selected clip (real pipeline run, Arabic fixture)

Candidate from the LLM: `start 6.0 → end 20.0`, title «لن تصدق هذا الاكتشاف الصادم ٧», score 92.

| | Before (v7.36) | After (v7.40) |
|---|---|---|
| Window | `[6.0, 20.0]` byte-exact | `[0.0, 28.0]` — antecedent included (لكن opener), following sentence completed |
| Start | «لكن السر الحقيقي…» (dangling connector) | «مرحبا بكم…» sentence start |
| End | mid-idea | after «…بعمق.» terminal punctuation |
| selection_score | 92.0 (raw LLM self-score) | 83.2 with 11-factor `score_breakdown` |
| completion_status | — (field didn't exist) | `complete` |

## 5. Before / after — Arabic title

| | Before | After |
|---|---|---|
| Shipped title | «لن تصدق هذا الاكتشاف الصادم ٧» (clickbait + invented number ٧) | «السر الحقيقي للنجاح» (the LLM's own factual alternative, confidence 100) |
| Validation | none | `contains_hallucinated_fact: false`, `language_matches_content: true`, `within_length_limit: true` |
| If nothing factual | kept anyway | conservative fallback from the clip's own words, `title_confidence: 35`, original preserved in `llm_title_replaced` |

## 6. Test commands executed

```bash
python3 -m pytest tests -p no:cacheprovider \
  --ignore=tests/test_edit_video_tracking.py --ignore=tests/test_face_crop_geometry.py \
  --ignore=tests/test_face_detection_insightface.py --ignore=tests/test_multi_face_framing.py \
  --ignore=tests/test_ui_safety_gates.py --ignore=tests/test_v730_scene_crop.py \
  --ignore=tests/test_v735_speaker_link.py
python3 -m pytest tests/test_v740_clip_quality.py -q
python3 -m py_compile scripts/arabic_text.py scripts/clip_scoring.py scripts/transcript_window.py \
  scripts/title_factual.py scripts/create_viral_segments.py main_improved.py tests/test_v740_clip_quality.py
# FFmpeg e2e: synthetic 60 s mp4 + Arabic TSV/SRT/JSON fixtures →
#   process_segments → cut_segments.cut → 2 real mp4 clips (28.024 s, 25.024 s) + clipped subtitle JSONs
# WebUI e2e: webui/segments_review load/title-choices/choose/export-publish-metadata on the new payload
```

## 7. Test results

- **CI (GitHub Actions, the authoritative run — Python 3.10 / 3.11 / 3.12): 1317 passed, 0 failed, 0 errors**, `ruff check .` clean, `pip-audit` reports no known vulnerabilities. All 74 new tests pass on every supported interpreter.
- Local sandbox run (fewer optional deps installed): 1203 passed / 14 failed / 29 errors — the failures are byte-identical to the pre-change baseline in that sandbox (missing cv2, requests, PIL, scenedetect), i.e. no regressions.
- The 7 heavy modules that cannot be collected locally (cv2) collect and pass in CI.
- FFmpeg e2e locally produced valid clips within 24 ms of the requested durations, with word-clipped subtitle JSONs and Arabic filenames intact.

## 8. Remaining limitations

- **Semantic dedup is surface-based** (normalized token/sequence similarity with guards). True paraphrases with disjoint vocabulary need embeddings; the project deliberately avoids that dependency. Threshold and penalty are env-tunable.
- **Arabic fact-checking is token-based** (no stemming): «الطريقة… للتدرب» over a transcript saying «تتدرب» counts as weak support and may fall back to a literal quote — conservative by design, never hallucinated.
- **audio_quality is a silence-based proxy**; real loudness QC stays in `scripts/audio_qc.py`. **visual_quality** is a documented neutral 80 (+10 when scene-snapped).
- Pre/post-roll defaults to 0.0 to preserve byte-exact legacy windows; enable via `VIRALCUTTER_PRE_ROLL` / `VIRALCUTTER_POST_ROLL`.
- The LLM output schema itself was left unchanged on purpose: the deterministic downstream breakdowns are more trustworthy than model self-reports; the prompt now enforces the behavioral rules instead.

## 9. Migration instructions

None required. All new JSON fields are additive; legacy `viral_segments.txt` files load and dedup exactly as before (semantic level skips entries without `transcript_text`). Changing any selection-relevant setting regenerates segments automatically on the next `--skip-prompts` run; use `--force-regenerate` to force it manually.

## 10. Verifying in the WebUI

1. Open a project, run creation with *Generate new segments* checked (or `--force-regenerate` from CLI).
2. In **مراجعة المقاطع** the recommended title is now the fact-checked one; the A/B dropdown still offers the LLM's alternatives.
3. `viral_segments.txt` shows `score_breakdown`, `quality_flags`, `completion_status`, `transcript_text` and `title_data` per segment — hover/inspect any clip to audit why it was chosen.
4. Edit `prompt.txt` or change clip count/durations/backend and re-run with skip-prompts: segments regenerate instead of loading stale ones.
