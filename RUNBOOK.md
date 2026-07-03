# Internal A/B Pilot — Runbook

Goal: with ~6 team members over 2 days, get real numbers on three things
before the Prolific study: (1) **time per study** — how long a game and a
session actually take, (2) **how many games one annotator can do per task**,
and (3) **general vs. specific questions** — which design catches known bugs
and which leaves annotators guessing.

## The design in one paragraph

The team splits into two fixed groups. **Day 1**: 4 different games (4 task
types), one transcript each, every transcript containing a known, confirmed
bug — Group A annotates all 4 with the **general** (universal) questions
only, Group B with the **specific** (game-bespoke) questions only. **Day 2**:
conditions flip (A → specific, B → general) on 2–3 transcripts of **one**
game nobody saw on Day 1, worked in pairs on identical material so
same-condition agreement can be checked. The app records per-game and
per-session timing automatically, forces an answer to every per-turn question
(flags and comments stay optional), and ends every game with a micro-survey
(question fit, fatigue, and a 1–5 confidence rating).

## Day 1 — 4 different games, general vs. specific

| Group | Condition (`assignments.json` value) |
|---|---|
| **Group A** | General questions only — the same 2 questions (+3 flags) on every game (`universal`) |
| **Group B** | Specific questions only — the bespoke set built for that game (`hybrid`) |

4 games, chosen to cover different task types, 1 transcript each, same
transcripts for both groups, done **one game at a time** (not mixed):

| Game | Task type | Confirmed bug in the transcript |
|---|---|---|
| **Deal or No Deal** | Negotiation | Secret proposal doesn't match the verbal agreement |
| **Clean Up** (short snippet, not full length) | Agreement / coordination | Both players declared victory while actually far apart |
| **TextMapWorld (Graph Reasoning)** | Map / world model | Model loops forever, never realizes it's mapped everything |
| **Taboo** | Communication | Same clue repeated 3 times, unflagged |

**What to record per transcript** — and where it comes from:

- **Did the annotator flag the known bug (yes/no)?** Coordinator judges this
  from the export (`flags`, low turn scores, and comments on the bug turn).
- **How long it took** — automatic: `duration_seconds` per game in
  `pilot_export.csv` (started → verdict submitted).
- **1–5 confidence** — automatic: the `survey_confidence` question every game
  ends with.

After the 4th game, ask everyone: *"how many more of these could you
comfortably do right now?"* — write the answers down; they're the
games-per-task budget.

**Estimated time:** Group A (general only) ~20–24 min for all 4 games;
Group B (specific only) ~24–30 min, since specific questions need more
context-reading per game.

## Day 2 — one game, pairs of 2, conditions flipped

**Game:** **Imagegame** — unseen on Day 1, and it has the most vivid
confirmed bug in the set (the grid silently failed to update for a whole
episode; the automatic score alone never caught it). Fallback if its
transcripts aren't ready: Taboo (fast, well-documented — but it repeats a
Day 1 game type; `make_assignments.py` will refuse the overlap unless the
Day 1 list is changed too).

| Group | Day 1 was | Day 2 is |
|---|---|---|
| Group A | General | **Specific** |
| Group B | Specific | **General** |

**Pairs of 2** (3 pairs for 6 people; 3 pairs + 1 trio for 7). Each pair
works through the **same 2–3 transcripts**. Pairing on identical material
under the same condition is the reliability check: if two people using the
same questions on the same transcript disagree, the question leaves too much
to individual judgment. Pairing is organised in the room — the app gives
everyone the same playlist regardless.

**Same recording as Day 1** (bug caught, time, confidence — all captured the
same way). Plus, since everyone has now tried both conditions, ask:
*"between general and specific, which did you find easier to answer
confidently, and which made you feel like you were guessing?"*

**Estimated time:** ~10–15 min for 2–3 transcripts per person, plus pair
discussion before the group debrief.

## Debrief (~15–20 min, whole group)

One shared table, filled in live from `pilot_export.csv` +
`pilot_sessions.csv` + the two spoken questions:

| | Day 1 hit rate | Day 2 hit rate | Avg confidence | Avg time/transcript |
|---|---|---|---|---|
| General | | | | |
| Specific | | | | |

Plus the capacity answers ("how many more could you do") and the direct
preference question from Day 2.

**What this gets us, against the three goals:** Day 1 + Day 2 timing per game
and per condition multiplies out to a real task length; the capacity answers
give games-per-task at face value; hit rate + confidence give general vs.
specific, replicated within-person via the Day 2 flip.

**One honest limitation:** Day 2 tests the crossover on only one game, so a
surprising Day 2 result could be about the game or about the condition —
keep that in mind when reading results; it's not necessarily worth adding
more games to fix.

## Deploying the pilot Space (coordinator, once)

1. On huggingface.co, **duplicate the production Space**
   (`yuriiilnytskyi/lm-playschool`) and name the copy
   **`yuriiilnytskyi/lm-playschool-pilot`** (the name the sync workflow
   pushes to). Public or private-with-team-access — either works.
2. In the pilot Space's *Settings → Repository secrets*, set:
   - `HF_TOKEN` — a write-scoped token (same as production uses)
   - `HF_PILOT_DATASET_REPO` — a **separate throwaway dataset repo** (e.g.
     `yuriiilnytskyi/playschool-pilot-annotations`; create it first) so test
     data never lands in the production dataset.
3. Push the `pilot` branch to GitHub — `.github/workflows/sync_to_hub.yml`
   auto-deploys `pilot` → the pilot Space (and `main` → production, as before).
   No GitHub? Push directly:
   `git push --force https://yuriiilnytskyi:<HF_TOKEN>@huggingface.co/spaces/yuriiilnytskyi/lm-playschool-pilot pilot:main`

## Before day 1 (coordinator, ~15 min)

1. Edit `make_assignments.py`:
   - Put the real names into `ANNOTATORS_GROUP_A` / `ANNOTATORS_GROUP_B`
     (these become the welcome page's name dropdown).
   - Check each `DAY1_GAMES` slug points at the transcript with the
     **confirmed bug** from the table above — swap instances if not.
   - Clean Up: the shortest transcript in `games/` is 12 AI turns; if that's
     too long, add a trimmed 8–10-turn snippet under `games/` and point the
     slug at it.
2. Run `python make_assignments.py` — it writes `assignments.json` and
   validates the slugs and the Day 1/Day 2 game-type separation.
3. Commit & push `assignments.json` on the `pilot` branch — the Space
   redeploys automatically.
4. Open the Space yourself, pick your name + Day 1, and click through a full
   game end-to-end (practice round → game → verdict + survey → next game).
   Check a row lands in the pilot HF dataset and that Submit All refuses an
   unanswered question.
5. Share **one link with everyone**: the pilot Space URL. Each person picks
   their name on the welcome page. (Per-person `?annotator=name&day=1` links
   also work.)

## Before day 2 (coordinator)

1. Make sure 2–3 **Imagegame** transcripts are in `games/` — right now there
   are only two, and `compact_grids` has a single AI turn. Pull 1–2 more
   episodes from clembench-runs (ideally the grid-freeze one). If that's not
   possible, decide the fallback game and update `DAY2_GAMES` (and, if it
   repeats a Day 1 type, the Day 1 list).
2. Re-run `python make_assignments.py`, commit & push.
3. Decide the pairs (who compares notes with whom) — offline, no app change.

## Running a session (each annotator)

1. Open the pilot Space URL, pick **your name** and **the day's session**,
   press **Start Annotation**. The note under the form shows how many games
   you've completed; if you got interrupted, Start resumes where you left off.
2. Fresh session: do the **practice round** (rate 3 Wordle turns, press
   *Check my ratings*, read the explanations). Day 2: feel free to press
   *Skip practice*.
3. Annotate each game: **every question on every turn must be answered** —
   Submit All will tell you which turns are incomplete (flags and comments
   are optional). Then give the overall verdict, answer the **pilot
   feedback** questions honestly (fit, fatigue, and how confident you were —
   they're about the questions/app, not the AI), **Submit Verdict**, then
   **Next game →**.
4. Work in one sitting if possible — timing is part of the data. The clock
   starts when you press Start and runs per game; you don't see it, so don't
   rush — work at the pace you'd actually work at. (Closing the tab mid-game
   is fine: finished games are never redone, but an unfinished game restarts
   from its first turn, and the session's wall-clock time becomes unreliable.)
5. Note anything broken/confusing in the per-game feedback box (preferred)
   or the shared doc.

## After each day (coordinator)

1. Pull the DB from the pilot HF dataset (or run locally), then
   `python export_pilot_csv.py` — writes `pilot_export.csv` (per-turn rows +
   per-game `duration_seconds`) and `pilot_sessions.csv` (per person × day:
   `wall_clock_seconds` incl. practice, `active_seconds` = sum of game
   durations).
2. Mark the bug-caught column: for each (annotator, game), did the flags /
   scores / comments on the bug turn actually catch the known bug?
3. Fill in the debrief table and run the debrief while it's fresh. Day 1
   extra: the capacity question. Day 2 extra: the general-vs-specific
   preference question, and where people felt they were guessing.

## Decision criteria

- **Per game, keep specific questions only where they earn it**: specific
  wins on bug hit rate AND confidence for that game. If general does as
  well, keep general — fewer bespoke questions to maintain and explain.
- **Games per session** = the capacity answers, sanity-checked against where
  per-game durations inflate across the playlist — rounded down, minus one
  for Prolific participants (externals will be slower than the team).
- **UI/wording**: any confusion mentioned by ≥2 people gets fixed before the
  Prolific study.

## Known limitations (accepted for an internal pilot)

- n=6 → everything is directional; these are design decisions, not
  significance claims.
- Day 2's crossover runs on a single game — game and condition are
  confounded there (see above).
- Hybrid falls back to the universal questions on games with no bespoke set:
  Deal or No Deal and Clean Up render identically in both conditions (by
  design for DonD; Clean Up simply has no bespoke set yet — Group B is
  effectively "general" on that game and the debrief should treat it so).
  Imagegame currently has no bespoke set either — one must be added before
  Day 2 for the flip to mean anything on it.
- Everyone sees games in the same order, so order effects are shared, not
  eliminated.
