# Per-Game Annotation Question Set

## Executive Summary

This report analyses all 17 clembench games to decide which to annotate, what to ask, and why. Evidence comes from two sources: direct inspection of real transcripts from 14 different models (weak to strong) in the clembench-runs v3.0 dataset, checked one game at a time, plus a review of what each game's automatic (clemscore) metrics already measure.

---

## Master Table

| Game | Players | Avg turns | Abort rate | Split by questions/games | Why | Annotate or not |
|---|---|---|---|---|---|---|
| **Wordle / +Clue** | 1 (WordGuesser) | 3.5 / 1.5 | — | No | Single role | **YES** — letter feedback is unambiguous and visible. Any annotator, expert or not, can check it. No design needed. |
| **Taboo** | 2 (Describer, Guesser) | 3.5 | 4.8% | Yes | Roles quite different | **YES** — clue and guess are short, plain text, fully visible. But the game itself is very short, so it can potentially load 4–5 scripts per annotator. |
| **Codenames** | 2 (ClueGiver, Guesser) | 7.7 | 35.2% | Yes | Different jobs; board renderer required | **YES** |
| **Imagegame** | 2 (Giver, Follower) | 5.7 | 42.9% (highest) | Yes | Different jobs | **YES** |
| **Deal or No Deal (Dond)** | 2 (symmetric) | 4.8 | 10.7% | No | Same job, short | Yes |
| **Guess What** | 2 (Guesser, Answerer) | 9.6 | 10.7% | Yes | Different roles | **YES** |
| **Matchit ASCII** | 2 (symmetric) | 15.2 | 12.5% | No — verify IAA | Length is the cost driver | Yes |
| **Wordle + Critic** | 2 (Guesser, Critic) | 7.5 | — | Yes | Different skills | Yes |
| **TextMapWorld (+ Specific Room)** | 1 (PathGuesser) | 7.1 / 1.5 | 7.1% / 0% | No | Single role | Maybe keep |
| **Clean Up** | 2 (symmetric) | 25.2 (up to 46 seen) | 16.7% | Yes, if long | One side may carry it | Yes |
| **Private Shared** | 1 (Answerer) | 6.8 (3–4 questions per round) | 17.1% | No | Single role, low value | Potentially cut — not much to check, most of it is already checked automatically (there is sometimes a wrong type but correct answer, which is what we can check) |
| **Hot Air Balloon** | 2 (symmetric) | 6.8 | 32.1% | Only if long | Same model, both seats | Keep, but judge reasoning, not the outcome — too heavy for a human to check the outcome itself |
| **Adventuregame** | 1 (Adventurer) | 35.4 | 41.4% | No | Extracts, not split | Potentially cut, maybe keep as small snippets (new word/object categories are impossible to rate consistently, and there isn't much to judge per turn) |
| **TextMapWorld (Graph Reasoning)** | 1 (PathGuesser) | 7.3 | 14.3% | No | Single role | Keep |
| **Referencegame** | 2 (Giver, Follower) | 2.0 | 4.3% | No | Too short | Yes — automatic score is not enough |

---

## The Shared Core, Asked on Every Game

| Item | Type | Why it is safe to ask |
|---|---|---|
| **Q1 — Did the AI correctly use information from earlier in the game?** | Scale 1–4 | Visible on screen, no expert knowledge needed |
| **Q2 — Did this move make sense as a next step?** | Scale 1–4 | Needs only common sense and visible state; never asks for the "best" move |
| **Tick A — Repeated a move that already failed** | True / false | A discrete event, cheaper as a tick |
| **Tick B — Invented or got a game fact wrong** | True / false | Needs the real fact visible on screen to check |
| **Tick C — Noticed and fixed an earlier mistake** | True / false | Needs the mistake and the fix both visible |

| Conditional pair | Type | Shown when |
|---|---|---|
| **Q3 — How clearly does the AI explain its move?** | Scale 1–4 + N/A | Game requires an explanation (Wordle family, Dond) |
| **Tick D — Explanation does not match the move** | True / false | Same trigger as Q3 |

---

## Per-Game Sheets

One version per game — the most recent, evidence-checked set. Where a game got a later, more detailed revision, that revision is included as its own section further down (Clean Up, TextMapWorld Graph Reasoning, and Hot Air Balloon all have an updated treatment after the main sheets below).

### Wordle / Wordle + Clue

**What the game is:** The model guesses a 5-letter word in up to 6 tries, with green/yellow/red letter feedback after each guess. The Clue variant adds one extra hint word at the very start.

**What's measured automatically:** Closeness Score, Strategy Score, Guess Repetitions — all rise cleanly with good play in the one real episode checked (0 → 10 → 30 across three turns).

**Verified finding:** Confirmed real — abort rate cannot currently be verified, since the 14-model dataset has zero episode files for this game family. Treat any abort-rate figure for Wordle as unverified, not measured.

**Per-turn questions**
- Did the AI correctly use letter feedback from every earlier guess, not just the last one? (Scale 1–4)
- Did this guess make strategic sense as a next step? (Scale 1–4)
- How clearly does the explanation justify the guess? (Scale 1–4) — always shown, explanation is mandatory
- (Clue variant, turn 1 only) Did the first guess reflect the clue's meaning? (Scale 1–4)

**Ticks (per-turn)**
- Reasoning–action mismatch
- Self-corrected after an earlier mistake

**Overall (whole-game) question:** None needed — outcome is already exact (Win/Lose, Closeness Score).

**Split by player:** Not applicable — single role.

**Separate design needed:** No. Letter-coloured tiles are a nice-to-have for reading speed, not required for any question to be answerable.

---

### Taboo (extremely short)

**What the game is:** A Describer gives a clue for a secret word without using forbidden words; a Guesser tries to guess the word. Most games are 1 exchange, some run to 3 rounds.

**What's measured automatically:** Accuracy, Repetition-Guesser, Repetition-Describer.

**Verified finding:** Confirmed real and important — three separate episodes show the Describer giving the identical clue word-for-word three times in a row ("Not here; opposite of near" × 3), but Repetition-Describer reads 0 in every one. The automatic repetition flag exists, but it does not catch exact-text repetition reliably — that's the gap.

**Per-turn questions**
- Describer — Was this clue clear enough to guess from, without a forbidden word? (Scale 1–4)
- Guesser — Did the guess match what the clue was pointing to? (Scale 1–4)

**Ticks (per-turn)**
- Guesser ignored its own previous wrong guess
- Describer repeated the same clue

**Overall (whole-game) question:** Across the whole game, did the Describer adjust its clues based on what the Guesser got wrong, or keep trying the same approach? (Scale 1–7)

**Split by player:** Yes — inventing a clue and guessing from one are different jobs.

**Separate design needed:** No. Clue and guess are short, plain text.

---

### Codenames

**What the game is:** A ClueGiver gives a one-word clue pointing to several hidden "team" words on a board. A Guesser picks words, avoiding the opponent's words and one "assassin" word.

**What's measured automatically:** ClueGiver/Guesser Team Precision/Recall/F1, Episode Negative Recall, whether the game ended through the assassin word.

**Per-turn questions**

- **ClueGiver — Could this clue plausibly lead the Guesser toward the assassin or a wrong word?** (Scale 1–4, needs the board shown)
  *TD-Eval Backend Knowledge Consistency — does the ClueGiver correctly use what it actually knows (the full board) when choosing a safe clue.*
- **Guesser — Does the guess match what the clue was actually pointing to?** (Scale 1–4, needs the board shown)
  *TD-Eval Conversation Cohesion — does the response logically follow from what was just said.*
- **Guesser — Is this guess a real word that exists on the board at all?** (Yes/No)
  *Directly needed by the confirmed finding above. This is the one question that catches what automatic scoring currently misses entirely.*

**Overall (whole-game) question:** Did the ClueGiver's clues stay consistently safe across the whole game, or did risk increase as the board filled in? (Scale 1–4)

**Separate design needed:** No.

---

### Imagegame

**What the game is:** An Instruction Giver describes a 5×5 grid pattern in words. An Instruction Follower tries to recreate the exact grid. Some versions give the whole pattern at once; others build it cell by cell, showing the Follower's current grid back after every command.

**What's measured automatically:** The game checks the final grid against the real target, cell by cell:
- **Precision** — of the cells the Follower filled in, how many were actually correct (punishes adding things that shouldn't be there)
- **Recall** — of the cells that should be filled in, how many the Follower got (punishes missing things that should be there)
- **F1** — combines both into one score
- **Changed Cell Count** — how many cells changed that turn

**Verified finding:** Confirmed real and serious — a 29-turn cell-by-cell episode ended with F1 = 33.0 specifically because the Giver said "put an R in fifth row fifth column," the Follower's grid never actually updated, and the Giver moved on without noticing.

**Per-turn questions**

- **Giver — Does this instruction correctly describe one cell/row/column of the real target grid?** (Scale 1–4, needs the target grid shown)
  *TD-Eval Backend Knowledge Consistency — checking if the instruction correctly uses the real information it was given.*
- **Giver — Does this instruction still make sense given everything filled in so far, or does it suggest the Giver has lost track of the shape?** (Scale 1–4)
  *TD-Eval Conversation Cohesion, applied across turns. One instruction looks fine alone; only watching several in a row reveals the Giver is building the wrong shape.*

**Ticks (per-turn)**
- Did the Follower's grid match the instruction just given? (True/false) — a simple before/after comparison, no judgment needed
- Did the Giver notice the grid didn't update, and say something about it? (True/false) — this is the Giver's job

**Overall (whole-game) question:** Did the Giver's plan correctly track toward the real target, regardless of small execution slips? (Scale 1–7)

**Separate design needed:** Yes — rendering the grid visually rather than as plain text rows would be beneficial, but not mandatory.

---

### Deal or No Deal (Dond)

**What the game is:** Two players each have private item values, negotiate openly, then each separately submits a secret, locked-in proposal for the split.

**What's measured automatically:**
- **Sum of Points** — combined score both players earned from the final accepted split
- **Pareto Optimal** — whether the final split was the most efficient possible, given both players' true values
- **Aborted / Success / Lose** — episode outcome flags
- **Request Count** — format checks

**Finding:** Confirmed real and recurring across every failed episode checked (5 of 5, including GPT-5.2): players verbally agree on a split in open chat, then submit secret proposals that don't quite match what was just agreed. One checked episode (Llama-3.1-8B) also aborted after this exact pattern. This is the dominant, repeated cause of failure in this game — not poor reasoning about values. In some weaker models, the proposal is sent on the very first turn, also resulting in a failed game.

**Per-turn questions**

- **Q1 — Does this offer make sense given what the player values?** (Scale 1–4)
  *TD-Eval Backend Knowledge Consistency — does the player's stated value (e.g. "I only care about the bolts") get correctly carried into its own proposal. This catches Q1 violations within a single player's own statements.*
- **Q2 — Does this message correctly build on what was agreed earlier in the same negotiation?** (Scale 1–4)
  *TD-Eval Conversation Cohesion.*

**Ticks (per-turn)**
- Did the player reveal its own secret value function in the open chat? (weaker models sometimes misunderstand the secrecy rule)
- Does the player's secret proposal match the agreement just reached in open chat?

**Overall (whole-game) question:** Did the players reach an agreement that was both genuinely collaborative and close to the best possible value for both sides? (Scale 1–7)

**Split by player:** No — same job on both sides, short enough to read as one exchange.

**Separate design needed:** No. Explicitly *not* asked: was the split fair to both sides? That's still excluded — fairness needs both players' secret values, which the annotator never fully sees.

---

### Guess What (focus on the Guesser)

**What the game is:** A Guesser asks yes/no questions to identify a secret target from a list. An Answerer responds yes or no.

**What's measured automatically:**

| Metric | What it actually measures |
|---|---|
| Accuracy (per turn) | Reads 0 on every question turn and only becomes 1 (or stays 0) on the final `GUESS:` turn — it's really the outcome flag, just attached to the last turn instead of the episode level |
| Speed | How many turns it took to reach a guess |
| Invalid format/content (Guesser/Answerer response) | Pure format checks |

**Finding:** Across many episodes, Teuken-7B asked six questions in a row that were all the same idea with one detail swapped ("eagle head + man body" → "eagle head + horse body" → "eagle head + lion body"...), never broadening even as every answer kept coming back "no." This is not exact repetition — each question is different text, so the existing repetition tick alone wouldn't catch it.

**Per-turn questions (Guesser only)**
- Q1 — Was this question a useful, sharp choice given everything learned so far? *(TD-Eval Backend Knowledge Consistency — does this turn correctly integrate the accumulated answers into its next move.)*

**Ticks (per-turn)**
- Tick A — Repeated a question already asked and answered (exact-text or near-exact repetition)

**Overall (whole-game) question:** Did the Guesser's questioning use its turns efficiently, or were many turns spent without meaningfully narrowing the possibilities? (Scale 1–7)

**Split by player:** Yes — the Guesser's questions need real judgement; the Answerer's yes/no is probably best left out.

**Separate design needed:** No.

---

### Matchit ASCII

**What the game is:** Two players each have a grid of X's and empty squares. They describe and ask questions about their own grid to work out if both grids match.

**What's measured automatically:**

| Metric | What it actually checks |
|---|---|
| Aborted | Did the episode end early due to a format failure |
| Lose / Success | Binary — did the episode count as a loss or win |
| Main Score | The headline score, derived from Success/Lose |
| Player Score | Same as Main Score in practice — whether the final same/different decision matched ground truth |
| Request Count | Total number of messages sent |
| Parsed Request Count | How many of those messages were in valid, parseable format |
| Violated Request Count | How many messages broke the required format |

None of this checks whether a claim about the grid is actually true.

**Verified finding:** Confirmed real — one player described its own grid as having "X's and O's alternating," inventing a symbol ("O") that does not exist anywhere in this game's actual format, which only ever uses X and ▢.

**Per-turn questions**

- **Q1 — Does this description or answer correctly match what is actually in the player's own grid?** (Scale 1–4)
  *TD-Eval Backend Knowledge Consistency — does the speaker accurately integrate and report factual details about information they have direct access to.*

**Ticks (per-turn)**
- **Tick A** — Description uses a symbol that doesn't exist in this game's format *(confirmed real from the finding above)*
- **Tick B** — This claim contradicts something the same player already said earlier in the game *(TD-Eval Backend Knowledge Consistency, the self-consistency case)*

**Overall (whole-game) question:** How accurate were the claims that led to that decision? (Scale 1–7 — 1 = multiple false claims, decision may have been right by chance; 7 = every claim checked was accurate throughout)

**Tick:** Did the two players reach the same final decision as each other (both said "same" or both said "different")?

**Split by player:** No — same job on both sides.

**Separate design needed:** Maybe render both grids visually, but it's already good as-is.

---

### Wordle with Critic

**What the game is:** A Guesser proposes a word with reasoning; a Critic checks the reasoning and agrees or disagrees before the guess is locked in.

**What's measured automatically:** Repetition-Guesser-On-Critic-Agreement/Disagreement.

**Verified finding:** Honest data gap, confirmed directly — the 14-model dataset has zero episode files for this game, the same gap as plain Wordle. Everything here relies on a single available episode from a separate, smaller dataset. Treat as provisional until more data exists.

**Per-turn questions**
- Same Q1/Q2/Q3 as plain Wordle, for the Guesser role.
- Critic — Does this critique point at something real and specific, not just a vague comment? (Scale 1–4)

**Ticks (per-turn)**
- Guesser ignored a valid critic objection without explanation
- Reasoning–action mismatch (Guesser's stated logic doesn't match the guess actually made)

**Overall (whole-game) question:** None added — game is short and the available data is too thin (n=1) to support a confident whole-game claim.

**Split by player:** Yes — fact-checking (Critic) is a different skill from letter logic (Guesser).

**Separate design needed:** No, beyond the optional letter tiles already suggested for Wordle.

---

### TextMapWorld / TextMapWorld (Specific Room)

**What the game is:** The model navigates a house room by room, trying to visit every room (or one specific target room) using the fewest moves.

**What's measured automatically:** efficiency, exploration, loops, number_visited.

**Per-turn question:** Did this move use what the model had already learned about the map, or repeat ground it had already covered? (1 = repeated ground with no new reason; 2 = fully used what it knew, moved into genuinely new territory)

**Ticks (per-turn)**
- Tried to move through a connection that doesn't exist on the model's own explored map

**Overall (whole-game) question:** None added — efficiency and exploration already give an exact automatic number; the two per-turn items above cover what a human can add that the numbers can't. (A separate "how efficiently did the model explore the map" 1–7 read is also usable if wanted.)

**Separate design needed:** Yes, required. Without a map shown, neither per-turn question above is answerable — very difficult for annotators to trace otherwise.

![TextMapWorld reference map — rooms connect via solid lines once confirmed; a dashed line marks a connection not yet fully verified, and the ring marks current position.](media/image1.png)

---

### Referencegame (maybe more than 5 per annotator)

**What the game is:** An Instruction Giver describes one grid out of several so a Follower can pick out the correct one. Always exactly 2 turns.

**What's measured automatically:** Success, Generated Expression Length.

**Verified finding:** Confirmed real across 6 of 6 failed episodes checked — every one had a clear, detailed, grammatically correct description, but the Follower still picked wrong every time. This points to the failure being mostly in the Follower's execution, not the Giver's clarity — a simple right/wrong tick on the Follower's pick would not separate that out.

**Per-turn questions**
- Giver — Was the description specific enough to tell this grid apart from the others? (Scale 1–4)
- Follower — Does the chosen grid actually match every detail in the description, checked piece by piece? (Scale 1–4, not just a tick, so a close miss can be told apart from a pick that ignored the description)

**Overall (whole-game) question:** None needed.

**Split by player:** No — too short for a split to produce a meaningful pattern.

**Separate design needed:** No, though rendering the candidate grids small and side by side would make the Follower question faster to check.

---

## Clean Up (updated, detailed version)

**What the game is about:** Two AI players each see different layouts. Each has a list of objects with positions, and they must agree on where everything should end up to match a shared goal layout. They send messages back and forth, stating their own object positions and proposing a plan to move objects, until both agree on a final layout.

**What clemscore already measures automatically**
- **Distance Score** — how close the final layout is to the goal
- **Consistency Score** — whether both players ended up agreeing on the same arrangement
- **Coverage Score** — how many objects ended up correctly placed
- **Penalty Score** — deductions for invalid moves

**Q2 — Did the AI correctly use information already stated, by either player?**
1. **None** — ignores positions or proposals already stated
2. **Partial** — uses some, misses an established position
3. **Good** — consistent with what's been said, though not perfect
4. **Excellent** — fully and precisely uses everything stated so far

**Q3 — Did this proposal make sense as a next step toward the end goal?**
1. **Nonsensical** — random or clearly counterproductive
2. **Poor** — wastes a turn or moves away from agreement
3. **Reasonable** — logical, even if not the best possible move
4. **Strong** — efficient, well-targeted, clearly advances toward a deal

**Tick — Repeated a move already rejected:** True/false. Re-proposing something the other player already turned down.

**Tick — Stated a fact that contradicts the transcript:** True/false. Claiming an object position that doesn't match what was said earlier.

**One overall question, asked once after all turns — Did the two players understand each other clearly while reaching their final arrangement?**
1. **Not clear** — messages were confusing or hard to follow
2. **Partly clear** — some confusion, but they worked it out
3. **Mostly clear** — easy to follow, with minor rough patches
4. **Fully clear** — every message was easy to understand

**Did the players reach an agreement without unnecessary repetition?** *(rate separately from clarity above — this is not about confusion, it's about wasted turns)*
1. **Many wasted turns** — far more back-and-forth than the goal needed
2. **Some wasted turns** — a few repeated or unnecessary proposals
3. **Reasonable pace** — reached agreement without major delay
4. **Direct path** — reached agreement as quickly as possible, no wasted turns

![Clean Up transcript example — GridCleaner proposing a target configuration, with a Game Master parse error and penalty shown.](media/image3.png)

---

## TextMapWorld (Graph Reasoning) — updated, detailed version

**What the game is:** The model explores a house with connected rooms, one move at a time (`GO: east`, `GO: south`...). At the same time, it must keep an updated map of what it has learned, written as JSON — listing every room it knows about and how they connect, organized by compass direction.

**What's measured automatically**

| Metric | What it checks |
|---|---|
| efficiency / exploration | How directly it moved, how much of the map it covered — same as plain TextMapWorld |
| graph_similarity (episode) | How close the model's final claimed graph is to the real one |
| similarity (per turn) | The same comparison, computed turn by turn |

These metrics can't detect when a model perfectly maps an area but lacks the understanding to stop, resulting in infinite loops that only end when a hard turn limit is enforced.

**Why a design is required, not optional:** If nothing is built to support this, there's no point asking these questions at all — annotators would have to manually track 8+ rooms and a growing JSON object across many turns, in their head. That's exactly the failure mode that made this game hard in the first place. The questions below only make sense paired with the renderer described next.

**The proposed renderer:** A map that builds up gradually, matching what the model itself has learned — not the full map shown from the start. Each room appears on screen only once the model claims it, colored immediately:
- **Green** — claimed correctly
- **Red** — claimed wrongly, and stays red until the model explicitly fixes it
- A neutral marker (a ring, not a fill color) shows where the model currently is, separate from the green/red correctness color, so "currently here" and "got this wrong" can be seen at the same time without conflicting

**Per-turn question:** Looking only at the map the AI has drawn so far — does its move make sense, even if the map itself turns out to be wrong? *You are not checking if the AI is right. You are checking if it is being consistent with what it believes.*
- **4 — Makes perfect sense:** given its own map, this was a smart, logical move
- **3 — Mostly makes sense:** a reasonable move, small issues
- **2 — Doesn't quite fit:** the move is hard to explain using its own map
- **1 — Makes no sense:** the move directly contradicts what the AI's own map says

**Tick — Spatial Hallucination / Invalid Move:** Tick this if the AI tries to go somewhere that doesn't exist, or makes up a room that was never there.

**Tick — Successful Self-Correction:** Tick this if a room turned red earlier (the AI got it wrong), and on a later turn, the AI fixes its mistake and the room turns green.

**Tick — Final Map Matches Own Movement History:** Check whether every move the AI actually made appears as a matching edge in its final graph, in both directions if relevant.

**Overall (whole-game) question:** Looking at the whole game, did the model build an accurate and consistent picture of the map, and recognize when it was done? (Scale 1–7)

![TextMapWorld Graph Reasoning renderer mockup — green outline rooms are claimed correctly, red is claimed wrong, and the dashed ring marks current position.](media/image4.png)

---

## Hot Air Balloon — updated, detailed version

> There is no missing picture to show — there is simply no single correct ranking of survival items. No interface can create a correct answer that does not exist.

**What the game is:** Two AI players are stuck in a hot air balloon that's losing height. Each has a private list of items with an effort cost and an importance value. They negotiate which items to throw overboard, trying to keep their own total importance high while staying under a shared effort limit. There's no single "correct" division — different people would reasonably split it differently.

**What's measured automatically**

| Metric | What it checks |
|---|---|
| Normalized Utility (per player) | How close each player's final result is to their own best possible outcome |
| Harmonic Mean Score | A balanced score across both players — rewards fair outcomes, not just one player winning |
| Violations (per player) | Format or rule-breaking errors |
| Aborted / Success | Whether the negotiation actually finished |

**Q1 — Setting aside whether you agree with the outcome, was the player's explanation logical and easy to follow?**
1. **Confusing** — the math or logic doesn't hold together
2. **Partly clear** — some steps make sense, others don't follow
3. **Mostly clear** — logical, with only minor gaps
4. **Fully clear** — every step is easy to follow and justified

The automatic scores (Normalized Utility, Harmonic Mean) are outcome-only and say nothing about whether the reasoning behind a move made sense. This question targets exactly that gap.

**Tick A** — Did the player's offer match what it had just explained, or did the offer contradict its own reasoning? *(Source: TD-Eval Backend Knowledge Consistency. Why needed: a player can reason clearly in text and then propose something unrelated — Q1 alone can't catch this, since the reasoning itself can be clear even when ignored.)*

**Tick B** — Did the player contradict something it said earlier about what it valued? *(TD-Eval Backend Knowledge Consistency)*

**Tick C** — Did the player's offer go over the fixed effort limit it was given? *(TD-Eval Backend Knowledge Consistency)*

**Overall Q1 — To what extent did the two players engage with one another and work constructively toward a final agreement?**
1. **No engagement** — players showed little genuine interaction; the negotiation ended without meaningful progress toward agreement
2. **Limited engagement** — some interaction occurred, but positions were largely repeated without real advancement
3. **Constructive engagement** — players responded substantively to one another, with steady movement toward resolution
4. **Full engagement** — players engaged thoroughly throughout, reaching a clear agreement through genuine negotiation

---

## Private Shared

**What the game is:** The Answerer has hidden facts. The Game Master asks questions; the Answerer must reveal facts accurately and consistently.

**What's measured automatically:**
- **Slot-Filling Accuracy** — did the real fact eventually get revealed correctly
- **Accuracy** — per-turn correctness of the self-tracking "does X know Y yet" answers

**1. Backend knowledge** — Did the model correctly recall the actual hidden fact when asked directly? (Tick — true/false, only asked on questioner/recruiter/waiter-style turns) *TD-Eval Backend Knowledge Consistency, the literal case — does the model accurately integrate and return the external information it was given at the start.*

**2. Self-tracking** — Did the model's answer about "does the other party know X" correctly reflect what had actually been revealed so far? (Tick — true/false, only asked from Player 2) *Confirmed real and severe across every episode checked — this is the dominant, currently-unexplained cause of lost games despite perfect factual recall.*

**3. Format vs. correctness** — Was the format wrong but the underlying answer correct? (Yes / No / N/A) — partially a policy-compliance check from TD-Eval, even though it's automatically detected.

**Overall (whole-game) question — Looking at the whole game, where did most of the model's errors come from?**
1. Mostly forgot or got facts wrong
2. Mostly lost track of what it had already revealed
3. A mix of both, roughly equally
4. Neither — performance was clean throughout

![Private Shared transcript example — the model answers "no" to whether the waiter knows the main dish, illustrating the self-tracking failure mode.](media/image2.png)

**Split by player:** No.

**Separate design needed:** No.

---

## Adventuregame (Home Delivery + Potion Brewing only — main concern is length)

**What the game is:** The model explores a house, picks up and uses objects, trying to complete a multi-step household task.

**What's measured automatically:** achieved_goal_ratio, plan_followed_ratio, epistemic/pragmatic action counts, and many format-level checks.

**Per-turn question:** Was this step useful progress toward the goal, or a wasted detour? (Scale 1–4, shown with the game's own best path as reference, on short extracts only.) *This is TD-Eval's general critique that outcome-only scoring misses process — pairing the question with the optimal path lets an annotator judge each move without needing personal expertise in the game's vocabulary.*

**Ticks (per-turn) — unchanged, still confirmed necessary**
- Repeated a move already shown to fail
- Claimed something false about the game world (an object or room that doesn't exist)
- Did the model correctly use an object's stated features (closed/open, location, capacity) before acting on it?

**Split by player:** No — single role.

**Separate design needed:** Only a short-extract view, not a full renderer — show a few turns plus the known best path, never the whole transcript at once.

---

## Open for Discussion

1. Have one general question set for the 7–8 suitable games, or have specific questions for each game?
2. How many annotators, and how many tasks does each do at once (one game, or many)?