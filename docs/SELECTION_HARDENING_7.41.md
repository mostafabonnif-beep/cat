# v7.41.0 — Selection hardening report (clip quality + factual titles)

Scope: complete the mandatory fixes from the clip-selection audit on top of
v7.40.0. Everything below is implemented, tested and pushed to `main`.

Commands run (from the repo root, Python 3.10.12):

```
python3 -m compileall -q .      # OK
python3 -m ruff check .         # All checks passed
python3 -m pytest -o addopts="" -q   # 1405 passed in 28.83s
python3 -m pip_audit --progress-spinner off   # see "Limitations"
```

---

## 1. Root causes found

| # | Root cause | Symptom |
|---|---|---|
| RC1 | `recommended_title` was kept verbatim and `alt_titles` were never fact-checked; only the primary title (and only after a fallback decision) was scored. | Wrong/generic/exaggerated titles reached the WebUI and publishing. |
| RC2 | Explicit numeric `start_time`/`end_time` bypassed boundary snapping entirely (`snap_to_boundaries and (not (explicit_start and explicit_end) or clamp_adjusted)`). | Cuts inside words/sentences whenever the LLM returned numbers. |
| RC3 | A reversed window was silently swapped. | The cut no longer matched the model's semantic intent. |
| RC4 | Missing editorial scores (`hook_strength`, `narrative_completeness`, `clarity_score`, `novelty_score`) were back-filled from the virality `score`, and `quality_missing` candidates were deliberately kept outside the gate. | Unverified self-scores were ranked as if measured. |
| RC5 | A window shorter than `min_duration` was blindly extended to `start + min_duration`, then snapped back to the transcript edge. | Short clips padded with unrelated speech. |
| RC6 | No single reusable final validator; `_validate_segment_window` ran once inside `process_segments` only. | Invalid windows could still be saved, cut or published. |
| RC7 | Weights carried `completion_score` (no `narrative_completeness`/`boundary_quality`), env overrides were not renormalized or fingerprinted, and `process_segments` sorted the caller's list in place. | Spec-weight drift, stale reuse, caller mutation. |
| RC8 | The config fingerprint covered the source video but not the transcript content or runtime selection weights. | Re-transcription / weight override reused stale windows and titles. |

## 2. Files and functions changed

**New**

* `scripts/segment_validator.py` — `validate_final_segment`, `validate_final_segments`, `is_publishable_segment`, `annotate_segment_validation`, `publish_block_reasons`, `FATAL_ERROR_CODES`, `SEGMENT_VALIDATOR_VERSION`.
* `tests/test_v741_title_validation.py` (24 tests), `tests/test_v741_segment_validator.py` (46), `tests/test_v741_selection_hardening.py` (18).

**Changed**

* `scripts/title_factual.py` — `TITLE_VALIDATION_SCHEMA_VERSION`, `unsupported_claims`, `detect_full_video_scope`, `validate_title_vs_clip`, `validate_all_title_candidates`, extended `build_title_data` (adds `title_validation`, `title_review_required`, filters rejected alternatives).
* `scripts/create_viral_segments.py` — `list(raw_segments)` copy; `_align_text_time`, `_recover_reversed_window`, `probe_media_duration`, `_transcript_text_between`, `_contextually_connected`, `_extend_to_min_duration`; reversal recovery/rejection (`reversed_window`); boundary snapping now applies to explicit numeric windows + word-edge safety net; `quality_status` and null (never copied) editorial scores; `requires_review`/`publish_blocked_reason`; `title_validation` + validated `alt_titles`; final validator pass; `selection_weights_fingerprint`, `transcript_fingerprint`, extended `prompt_version_fingerprint`/`selection_config`; `FATAL_VALIDATION_CODES`.
* `scripts/clip_scoring.py` — spec 12 factors (`narrative_completeness`, `boundary_quality`), renormalized `compute_final_score`, `weights_fingerprint`, `LEGACY_FACTOR_ALIASES`, `completion_score` kept as alias.
* `scripts/arabic_text.py` — `to_display`, `to_comparison`, `to_search`, `search_tokens`, `language_profile`, `dominant_language`, `is_rtl`, `rtl_display`.
* `scripts/save_json.py` — annotates/validates with `segment_validator` before writing (overwrite path).
* `scripts/cut_segments.py` — `_has_stored_transcript`; refuses fatal windows after scene snapping and before FFmpeg.
* `scripts/upload_gate.py` — `_load_segment_entry`, `_segment_review_reasons`; refuses `export_blocked` / `requires_review` / `title_review_required` clips.
* `scripts/seo_titles.py` — grounded `generate_titles(..., transcript_text=...)`.
* `webui/segments_review.py` — `choose_title` fact-checks the reviewer's pick against the clip transcript.
* `webui/publish_panel.py` — `clip_metadata` surfaces `requires_review`, `publish_blocked_reason`, `title_validation_status`.
* `main_improved.py` — selection-weights fingerprint in `_segment_settings_fingerprint`; transcript fingerprint stamped in `source_meta` and compared on reuse.
* `tests/test_v733_selection.py`, `tests/test_v740_clip_quality.py` — updated for the mandated new behaviour (reversed rejection/recovery, explicit-window validation, 12-factor weights).
* `prompt.txt`, `app_version.py`, `pyproject.toml`, `changelog.md`, `README{,_en,_ar}.md` — policy line, 7.41.0 release metadata.

## 3. Before/after: a badly cut clip

Arabic transcript, explicit `start_time=11.5`, `end_time=16.5`, min 5s / max 30s.

* **Before**: window kept byte-exact (11.5–16.5) → starts/ends mid-word and mid-sentence; no quality flag; `snap_to_boundaries` skipped it because both timestamps were explicit.
* **After**: word timings (when present) snap the start to the word start; the sentence-boundary pass extends the end to the sentence end; a word-edge safety net runs even with snapping disabled; conflicting snaps are recorded as quality flags; a window outside `media_duration` is rejected.

## 4. Before/after: an inaccurate Arabic title

Clip transcript: `مرحبا بكم في حلقة جديدة من برنامجنا.` (`clip_ratio ≈ 0.1`).

* **Before**: `recommended_title = "ربحت 5000 دولار في يوم"` was stored and published as-is; `alt_titles` were never checked.
* **After**: `validate_title_vs_clip` returns `status="rejected"` with
  `contains_unsupported_number=true` (and separately rejects unsupported
  entities/claims, whole-video framing, wrong language and excessive
  clickbait). `build_title_data` ships a conservative transcript-derived title
  instead, records the original under `title_data.rejected_titles`, keeps only
  validated alternatives in `alternative_titles`, and sets
  `title_review_required` when nothing verifies — which blocks auto-publishing.

## 5. Tests added

* `tests/test_v741_title_validation.py` — 24 tests (structured schema, unsupported number/entity/claim, whole-video scope, Arabic + mixed language, alt-title filtering, optional LLM entailment with deterministic fallback).
* `tests/test_v741_segment_validator.py` — 46 tests (missing end_time, empty transcript, end≤start, min/max, media bounds, truncation, edge silence, title/safety/duplicate gates, aggregation, no mutation).
* `tests/test_v741_selection_hardening.py` — 18 tests (spec items 1–5, 9–11, 13–15 plus save/cut/publish integration and provenance fingerprints).

## 6. Full command results

* `python3 -m compileall -q .` → success.
* `python3 -m ruff check .` → `All checks passed!`
* `python3 -m pytest -o addopts="" -q` → **1405 passed in 28.83s** (baseline
  1317 + 88 new; 0 failures).
* `python3 -m pip_audit --progress-spinner off` → no advisories for the
  project's pinned dependencies; the only findings are Ubuntu **system**
  packages (`urllib3 1.26.5`, `wheel 0.37.1`, `zipp 1.0.0`) that are not in
  `requirements*.txt`.

## 7. Limitations

* `pip-audit` findings above come from the base OS image, not the project; CI
  installs `requirements-dev.txt` into a clean environment.
* `unsupported_claims` is intentionally conservative: a paraphrased claim in a
  different morphological form can be treated as unsupported and triggers the
  fallback/review path (never a silent fabrication).
* Semantic dedup remains lexical (normalized token/sequence similarity), not
  embedding-based, by design (no embeddings dependency).
* Transcript-based cache invalidation is compared when the transcript exists
  at reuse time; on the very first run it is stamped for the next run.
* Legacy segments with no stored transcript are validated structurally but the
  missing-transcript check is not fatal at the cut stage.

## 8. Migration

* No data migration required: JSON is additive and legacy files still load.
* Regeneration happens automatically when the transcript, source video,
  selection weights, prompt/schema versions or settings change. Force it with
  `--force-regenerate` (= `--force-new-segments`).
* New optional env vars: none required. Existing
  `VIRALCUTTER_SELECTION_WEIGHTS`, `VIRALCUTTER_MIN_FINAL_SCORE`,
  `VIRALCUTTER_PRE_ROLL`, `VIRALCUTTER_POST_ROLL`,
  `VIRALCUTTER_SEMANTIC_DUP_THRESHOLD` keep working; weight overrides are now
  renormalized for scoring and fingerprinted for invalidation.

## 9. Verify in the WebUI

1. Generate segments for a project (`run_webui` → Viral Segments).
2. Open the segment review table: each row now carries `title_validation`
   (`verified`/`review`/`rejected`), `quality_status`, and a
   `requires_review` / `publish_blocked_reason` marker when applicable.
3. Edit/adjust a title and save: a title unsupported by the clip transcript is
   refused with a reason; a supported one is persisted with its validation.
4. Try to publish a flagged clip: the upload gate refuses it
   (`source: segment_validation`) until it passes review.
