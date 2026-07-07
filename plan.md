# Implementation Plan: Annotation Pilot Branch
**For: Claude Code**
**Branch: `pilot` (off `main`) — do not modify `main` at any point in this task.**

## Context (what should already exist in this repo)

- A Gradio annotation app (likely `annotation.py`) with a 3-screen flow: welcome → per-turn annotation (Q1 Prior Info, Q2 Strategic Logic, conditional Q3 Reasoning Clarity, flags, comments) → verdict (G1 Strategic Coherence, G2 Overall Quality 1–7).
- Game data loaded from `games/**/interactions.json`, covering all 17 clembench game types.
- A `.game-select`-style dropdown and game-discovery logic already exist in the CSS/UI, but the render path currently loads one hardcoded `DEFAULT_GAME` at build time (this is the known gap — see Non-Goals below, we are **not** fixing this properly in this branch).
- Persistence in SQLite (`annotations.db`), with a `UNIQUE(game_slug, annotator_id)`-style constraint supporting resume/upsert, plus a best-effort backup/restore to a private Hugging Face dataset.
- The current participant identifier is read from the URL (Prolific PID pattern).
- Deployment is a Hugging Face Space with a `sync_to_hub.yml`-style workflow.

**Before making any change below, locate and confirm the actual current implementation of each piece** (exact file, exact schema, exact URL-parsing code). Do not assume the snippets in this document are literal — they are illustrative of intent. If what you find differs from this description, adapt the plan to match reality and note the discrepancy in your summary.

---

## Objective

Make the minimum set of changes needed to run a 2-day internal pilot that compares:
- **Question design:** `universal` (same 2 questions everywhere) vs. `hybrid` (universal + bespoke add-ons)
- **Workload:** one-game-per-session vs. mixed-game sessions

...without building any of the permanent, production-grade features that don't matter for two days of internal testing.

## Locked study design (final, 2026-07-03 — supersedes the workload comparison above)

The operational protocol lives in RUNBOOK.md; this is the implementation-facing summary:

- **Day 1 — 4 games, general vs. specific, between groups.** Two fixed groups.
  Group A does all 4 games with general (`universal`) questions only; Group B
  with specific (`hybrid`) questions only. 4 games = 4 task types, one
  transcript each, each containing a known confirmed bug: Deal or No Deal
  (secret proposal ≠ verbal agreement), Clean Up short transcript (false
  double victory declaration), TextMapWorld Graph Reasoning (endless loop),
  Taboo (clue repeated 3× unflagged). Blocked order, one game at a time.
- **Day 2 — one unseen game, conditions flipped, pairs.** Group A → specific,
  Group B → general, on 2–3 transcripts of one game (default: Imagegame;
  its transcripts and bespoke question set must be added first). Pairs work
  identical material for a same-condition agreement check; pairing is
  organised offline.
- **Measures per transcript:** known-bug caught (coordinator judges from the
  export), time (automatic per-game timer), 1–5 confidence (in-app
  micro-survey). Per day: the "how many more could you do" capacity question;
  Day 2 adds the general-vs-specific preference question.
- **Implementation consequences (all done on this branch):** Tasks 8–10
  below, plus `make_assignments.py` regenerated around fixed groups instead
  of per-transcript rotation.

---

## Task 1 — Branch setup

- [ ] Create branch `pilot` from `main` (or confirm it exists and is up to date with `main`).
- [ ] All subsequent commits go on this branch only.

## Task 2 — URL parameters: `annotator`, `block`, `game`

Extend the existing URL-param reading (currently used for the Prolific PID) to also read:

- `annotator` — free-text name or short ID (e.g. `alice`)
- `block` — one of a fixed, validated set: `day1_universal`, `day1_hybrid`, `day2_mixed`
- `game` — the game slug to load for this session (e.g. `taboo`, `wordle`, `textmapworld_graphreasoning`)

Example target URL shape:
```
https://<space-url>/?annotator=alice&block=day1_hybrid&game=taboo
```

Requirements:
- [ ] If any of the three params is missing or `block` isn't one of the three valid values, show a clear in-app message (not a crash/traceback) explaining the link is malformed, and stop before rendering the annotation UI.
- [ ] Store `annotator`, `block`, and `game` in whatever session/state object the app already uses to carry the Prolific PID, so they're available to every downstream screen and to the DB-write step.

## Task 3 — Game loading driven by the `game` param

- [ ] Replace the static `DEFAULT_GAME` load path with a load keyed on the `game` URL param from Task 2.
- [ ] Do **not** build the real assignment/selection algorithm (see Non-Goals). The human running the session picks the game by sending the right link — that's sufficient for two days.
- [ ] If `game` doesn't match any discovered game slug, show a clear error rather than falling back silently to the old default.

## Task 4 — Conditional question sets: `universal` vs `hybrid`

This is the core of the pilot.

- [ ] Introduce a config/data structure mapping `(game_slug, block_type)` → which questions to render, where `block_type` is derived from `block` (`day1_universal` → `universal`; `day1_hybrid` and `day2_mixed` → `hybrid`).
- [ ] `universal` mode: render only the existing universal Q1 (Prior-Info Use) + Q2 (Strategic Logic) + the 3 standard flags + conditional Q3, on **every** game in the pilot set — including Taboo, Hot Air Balloon, and TextMapWorld (Graph Reasoning), even though we expect/want these to feel wrong. That's the point of the comparison.
- [ ] `hybrid` mode: render the universal core **plus** the bespoke add-on question(s) for whichever games need them:
  - **Codenames** — add the one bolt-on question (board-aware clue/guess check)
  - **Taboo** — replace with its own describer/guesser question pair
  - **Hot Air Balloon** — swap in the reasoning-clarity primary question (no ground-truth Q1)
  - **TextMapWorld (Graph Reasoning)** — swap in the inverted map-consistency question, its 3 supporting ticks, and the map renderer if one already exists in the codebase; if the renderer doesn't exist yet, render the raw JSON map state as plain text instead of blocking the pilot on building it
- [ ] Wordle and Deal or No Deal need no branching — same questions in both modes. Confirm this by checking the existing per-turn question logic for these two games; don't duplicate code if the current implementation already asks the right thing regardless of mode.
- [ ] Reuse whatever conditional-rendering pattern already exists for Q3 (shown only when a game requires explanation) rather than inventing a second pattern.

## Task 5 — Database: add a `condition` column

- [ ] First, inspect the actual current schema and unique constraint on the annotations table — confirm whether the existing key is per-transcript or per-game, since this affects whether the pilot needs additional uniqueness fields (e.g. a `transcript_id`) alongside `condition`.
- [ ] Add a `condition` column (the `block` value from Task 2) to the annotations table via a migration, e.g.:
  ```sql
  ALTER TABLE annotations ADD COLUMN condition TEXT;
  ```
- [ ] Update the insert/upsert path to always write `condition` alongside the existing fields.
- [ ] Do not change the production schema on `main` — this migration lives only on `pilot`.

## Task 6 — Backup: separate pilot dataset repo, shorter interval

- [ ] Locate the existing HF dataset backup/sync logic.
- [ ] Add a config value (env var, e.g. `HF_PILOT_DATASET_REPO`) so this branch pushes to a **separate** Hugging Face dataset repo, not the real one.
- [ ] Shorten the backup trigger for the pilot: push on every submission, or on a timer no longer than 10–15 minutes — whichever fits the existing backup mechanism with the least new code.
- [ ] If creating the new pilot dataset repo requires an HF token with write access that isn't available in this environment, stop and flag it as a manual step rather than guessing at credentials.

## Task 7 — Debrief export script

- [ ] Write a small standalone script (does not need to be part of the Gradio app) that reads the SQLite DB and outputs a CSV with columns: `annotator, game, condition, timestamp, scores (per question), comments`.
- [ ] This can be a single throwaway script — it does not need to become the permanent admin/export feature.

## Task 8 — Timing: per game + per session

- [x] Per game (already existed, kept): `started_at` is stamped when a game's
  annotation actually starts (leaving welcome/training, or "Next game →") and
  its duration is `verdict_at − started_at`, computed at export time.
- [x] Per session (new): `session_started_at` is stamped **once per sitting**
  at the Start click on the welcome page (so it includes the practice round)
  and stored on every annotation row of that sitting. `export_pilot_csv.py`
  writes a second file, `pilot_sessions.csv`, with one row per
  (annotator, day): games started/completed, `wall_clock_seconds`
  (session start → last verdict) and `active_seconds` (sum of per-game
  durations).
- [x] No visible clock for annotators — timing is measured silently so it
  doesn't create time pressure or change behaviour.

## Task 9 — Mandatory per-turn answers

- [x] "Submit All" refuses to save (and stays on the annotation page) until
  **every rendered question on every turn** is answered — Q1, Q2, Q3, and any
  bespoke bolt-on. Only the flag checkboxes and free-text comments are
  optional. The error message lists the incomplete turn numbers.
- [x] The client-side "X of N turns rated" counter and the green turn chips
  use the same rule (all rendered radio questions answered), so annotators
  can see which turns still block submission.

## Task 10 — Confidence micro-survey

- [x] The per-game pilot survey (verdict page) gains a mandatory third radio:
  "How confident are you in the ratings you just gave?" on a 1–5 scale
  (Guessing → Certain). Stored in `survey_confidence`, exported per row —
  this is the "Avg confidence" column of the debrief table.

## Task 11 — Severe-bug fixes from the 2026-07-04 evaluation

All six confirmed severe bugs fixed and verified end-to-end with scripted
Playwright probes (32 assertions):

- [x] **Cross-game widget value leak** (Gradio 6.15.2 `@gr.render` carried
  Radio/Textbox values from game N into game N+1; `key=` doesn't prevent it):
  fixed via a blank intermediate render driven by a new `clearing_state` —
  "Next game →" and Start are now two chained events (blank unmount, then
  mount fresh). See `app.py`'s `clearing_state` comment.
- [x] **URL playlist links never resumed** (reopening `?annotator=x&day=n`
  restarted at game 1 and overwrote completed work): both the page-load path
  and the Start click now resume at the first game without a verdict, with a
  friendly all-done banner; the name/day form outranks a stale playlist so
  switching days always works.
- [x] **Timing anchors kept the FIRST attempt** (`COALESCE` order): redone
  games now re-anchor `started_at`/`session_started_at` to the new attempt, so
  durations no longer span abandonment gaps.
- [x] **Production-dataset fallback removed**: `db.py` reads only
  `HF_PILOT_DATASET_REPO`; unset ⇒ backup/restore disabled loudly. The pilot
  branch can no longer touch the production dataset.
- [x] **Concurrency**: `busy_timeout=10000` (no more lost saves on
  simultaneous submits) + serialized, snapshot-based HF backup (no torn
  uploads).
- [x] **G2 Overall Quality** is now a mandatory 7-point radio (the slider's
  silent default of 4 used to be recorded as a real judgment).
- [x] 0-turn transcripts get a Back button instead of a dead end.
- [x] (found in the 2026-07-05 QA pass) Q3/Reasoning Clarity now triggers by
  GAME for the Wordle family + Deal or No Deal, per question_set.md's
  conditional table — the marker heuristic alone missed Dond (1 of 4 turns
  hit the markers; threshold needs half), so Dond annotators were never
  asked Q3 or shown the Reasoning-Action Mismatch flag. Heuristic kept as
  catch-all for other games.

## Task 13 — DuplicateBlockError fix: no keyed components in @gr.render (2026-07-05)

- [x] Removed every `key=` from the components built inside
  `_render_annotation` (the annot-col Column, per-turn Groups, radios,
  flags, comments). Keyed blocks route through Gradio 6's `key_to_id_map`,
  which reuses block ids across render passes and intermittently raised
  `DuplicateBlockError: A block with id N has already been rendered` in real
  sessions — always at a keyed component. The keys provided no protection
  anyway (they provably do not stop the cross-game value leak; the
  `clearing_state` blank render in Task 11 is that fix), so unkeyed
  fresh-id components are strictly safer.
- [x] A clembench-transcript left-column view was prototyped the same day
  and reverted at the team's call — the custom rebuild is the keeper (it's
  condensed; the official render duplicates full GM prompts to both players
  and bookkeeping messages). The episode dirs under `games/` still ship
  clembench's `transcript.html` untouched if this is ever revisited.

Known content gap (not a code fix): Imagegame/Clean Up have no bespoke
question sets, so `condition="hybrid"` renders universal questions there —
Day 2's contrast needs an Imagegame bespoke set (see RUNBOOK limitations).

## Task 12 — TextMapWorld (Graph Reasoning) map renderer (2026-07-04)

- [x] The renderer question_set.md calls "required, not optional" is built:
  in hybrid mode each turn card draws the model's CLAIMED map as an SVG
  graph — green ring = room claimed correctly, red ring = claimed wrongly
  (stays red until fixed), red dashed line = asserted connection the walk
  never verified, blue dashed ring = current position. Compass-true stable
  layout from the GM's `move` records; each turn is validated only against
  what the walker had revealed *by that turn*. A legend row sits above the
  transcript. Universal mode still deliberately shows the raw JSON.
- [x] Fixed en passant: the models' graph JSON uses Python tuples (invalid
  JSON), so the old pretty-print path never actually fired — parsing now
  falls back to a Python-literal parse. The GM's per-turn echo of the
  model's own JSON is hidden in hybrid mode (the map replaces it).

---

## Non-Goals (explicitly do not build these in this branch)

- The real fill-the-gap / priority-weighted game-assignment algorithm. Game selection for the pilot is 100% driven by the `game` URL param from Task 3.
- The Prolific completion redirect.
- Resume-partial-work UI improvements beyond whatever already exists.
- A polished admin dashboard — Task 7's script is enough.
- Any change to `main` or to the production HF Space/dataset.

## Manual steps outside Claude Code (flag these back to the team, don't attempt them)

- Duplicating the Hugging Face Space in the dashboard and pointing the duplicate at the `pilot` branch.
- Setting the duplicated Space to private and adding teammates as collaborators.
- Creating the new pilot HF dataset repo, unless a suitably-scoped HF token is already available in this environment.
- Generating and sending the actual per-person, per-block URLs to teammates before each session.

---

## Verification before Day 1

- [ ] Load the pilot Space with a test URL for each of the 3 `block` values, on at least 2 games each (one universal-fit game, one bespoke game) — confirm the correct question set renders every time.
- [ ] Submit one test annotation per condition; confirm the `condition` column is populated correctly in SQLite.
- [ ] Confirm a backup push reaches the **pilot** HF dataset repo (not the real one) within the shortened interval.
- [ ] Run the Task 7 export script against the test data; confirm the CSV has all expected columns and rows.
- [ ] Confirm a malformed URL (missing or invalid `block`) shows the error message from Task 2 instead of crashing.