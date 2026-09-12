# ViralCutter — Clip-Quality Technical Diagnosis (v7.40)

_Date: 2026-09-12 · Scope: clip selection, timestamps, titles, Arabic support, cache staleness_

## 1. Current flow (video → final clips)

```
input video
  └─ scripts/transcribe_video.py        WhisperX → input.srt / input.tsv / input.json
                                        (input.json HAS word-level timestamps; SRT/TSV do not)
  └─ scripts/create_viral_segments.py   load_transcript() reads TSV/SRT (segment-level only!)
                                        → chunk transcript with (XXs) tags → LLM
                                        (gemini | g4f | local | manual paste) with prompt.txt
                                        → LLM returns candidates {start_text,end_text,
                                          start_time_ref,title,alt_titles,score,...}
                                        → process_segments() aligns text→timestamps,
                                          clamps min/max, snaps to pause-based speech blocks,
                                          scores, gates, dedups, ranks
  └─ main_improved.py                   saves viral_segments.txt (+source_meta fingerprints),
                                        runs safety stages (content_guard, safety_filter,
                                        safety_ai), finalize_top_segments()
  └─ scripts/cut_segments.py            ffmpeg cuts (+ optional scene_snap), cut_json.py
                                        produces per-clip subtitle JSON (word-level)
  └─ subtitles / reframe / polish / export / upload_gate / publish stages
```

## 2. Root causes (mapped to the reported problems)

### P1+P2 — Poor choices, mid-sentence starts/ends
- **`_selection_score` trusts the LLM's self-reported `score`** (weight 0.40) and every
  missing editorial component falls back to that same number, so ranking is effectively
  the raw LLM score. No deterministic signal (real transcript window, silence, boundary
  completeness) ever influences the ranking.
- Boundary snapping uses **pause-based speech blocks** (`_speech_blocks`,
  `PAUSE_BOUNDARY_SECONDS = 0.35`), not real sentence boundaries: a 0.35 s pause inside a
  sentence splits it, and a quick sentence chain is glued together.
- **Word-level timestamps are never used.** They exist in `input.json` (WhisperX) but
  `load_transcript` only reads TSV/SRT, so "never cut inside a word" is only approximated.
- **Fully explicit numeric windows are trusted byte-exact** with no mid-sentence check
  (documented contract — kept, but there was no repair path for text-matched windows).
- No Arabic connector handling: clips can start with لكن / لذلك / ثم / فإذا … or end on a
  preposition/conjunction.
- No pre-roll / post-roll configuration existed at all.

### P3 — Weak / incomplete context
- `_window_text_from_transcript` existed (good) but was only used for title relevance.
  No check whether a clip *starts* mid-idea (connector openers, unresolved references).

### P4 — Repetitive clips
- `deduplicate_segments` only detects **temporal** overlap (`_windows_are_near_duplicates`,
  ≥60 % of the shorter window). Two clips saying the same thing in different words at
  different timestamps were both kept.

### P5+P6 — Inaccurate / generic / hallucinated / Arabic titles
- Titles come from the LLM over a **whole transcript chunk**, not the exact clip window.
- `_choose_recommended_title` *ranks* candidates by quality + relevance but never
  **validates factual support**: invented numbers, names, results survive.
- `scripts/seo_titles.py` templates (e.g. "النتيجة صدمتني", "لا أحد يخبرك بهذا") are
  clickbait-shaped and topic-only; they are not wired into segment titles but set the tone.
- No language-match validation between title and clip content.

### P7 — Stale results reused
- `_segment_settings_fingerprint` only covered {segments, min/max duration, chunk_size,
  title_language}. Changing **viral mode, AI backend, model, scene snapping, selection
  weights or the prompt** silently reused old windows.

### P8 — JSON output
- Segments carried no `transcript_text`, no per-factor `score_breakdown`, no
  `quality_flags` / `rejected_reasons`, no `title_data` — nothing to audit a choice.

## 3. Existing safeguards that MUST NOT break (and were preserved)

- Anchoring: unanchorable candidates are dropped; reversed windows swapped not relocated.
- Clamp-aware sentence snapping + `snap_segment_boundaries` contract (byte-exact explicit
  windows stay untouched).
- Quality gate (`apply_quality_gate`) with `quality_missing` transparency flags.
- Source-video + config fingerprints on reuse; `_clear_downstream_artifacts` on re-cut.
- content_guard → safety_filter → safety_ai → upload_gate chain.
- Arabic orthography normalization (hamza carriers, ta marbuta, tatweel, tashkeel) with
  alif-maqsura kept distinct; Arabic-Indic digit timestamp parsing.
- `title_text.fit_publish_title` word-boundary truncation; script-match checks.

## 4. Fix plan (implemented in this change)

| Area | Fix |
|---|---|
| Scoring | New `scripts/clip_scoring.py`: one `DEFAULT_SELECTION_WEIGHTS` object, 11 factors, `final_score = Σ w·f − repetition_penalty − safety_penalty`, env-overridable. |
| Windows | New `scripts/transcript_window.py`: sentence-unit splitting (punctuation + pauses), window analysis (first/last sentence, context before/after, silence, coverage), word-level edge snapping when `input.json` words exist, connector-start and dangling-ending repair, configurable pre/post-roll. |
| Titles | New `scripts/title_factual.py`: number/name hallucination checks vs the exact window text, clickbait pattern penalties, 8-part title score, language-match validation, deterministic conservative fallback titles (low confidence) when the LLM title is unsupported. |
| Dedup | Semantic dedup on normalized window transcript (threshold 0.75) + repetition penalty gradient (0.55–0.75) feeding `repetition_penalty`. |
| JSON | Additive fields: `transcript_text`, `hook_text`, `completion_status`, `score_breakdown`, `quality_flags`, `rejected_reasons`, `title_data`, `selection_version`. Legacy files keep loading (all readers use `.get`). |
| Cache | `_segment_settings_fingerprint` extended with viral mode, AI backend, AI model, transcription model, scene-snap, scoring version and prompt version; `--force-regenerate` CLI alias added. |
| Prompt | `prompt.txt` gains strict factual-title rules, clip-vs-full-transcript distinction, JSON-only and reject-instead-of-guess instructions (placeholders unchanged). |
| Normalization | Shared `scripts/arabic_text.py` (canonical implementation re-exported by `create_viral_segments` for backwards compatibility). |
