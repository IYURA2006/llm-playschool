# Annotation Question Set

**Generated — do not edit by hand.** Edit `questions.py` and run:

```bash
python questions.py --markdown > question_set.md
```

Covers the 8 game families with transcripts under `GAMES_DIR=games_study`. Anything a game does not override falls back to the Shared Core below.

---

## Shared Core

Asked on every AI turn unless the game replaces them.

- **Q1 — Prior Information Use**
  Did the AI correctly use information from earlier in the game?
  **1** None · **2** Partial · **3** Good · **4** Excellent
- **Q2 — Sensible Next Step**
  Did this move make sense as a next step?
  **1** Nonsensical · **2** Poor · **3** Reasonable · **4** Strong

Conditional pair — shown only where the game asks the model to explain its move:

- **Q3 — Reasoning Clarity · conditional**
  How clearly does the AI explain its move?
  **1** Unclear · **2** Confused · **3** Clear · **4** Transparent · **N/A**

Ticks (optional, tick all that apply):

- Repeated a move that already failed
- Invented or got a game fact wrong
- Noticed and fixed an earlier mistake
- Explanation does not match the move *(with Q3 only)*

---

## End of every game

- **G1 — Strategic coherence** — **1** No plan · **2** Rigid · **3** Adaptive · **4** Strategic
- **G2 — Overall game quality** — slider 1 (Broken) to 7 (Flawless)
- **G3 — This game specifically** — where the game defines one; listed per game below.

---

## Per game

### codenames

*52 transcript(s).*

**Role: ClueGiver**

- **Q1 — Clue Safety**
  Could this clue plausibly lead the Guesser toward the assassin or a wrong word?
  **1** Very risky · **2** Somewhat risky · **3** Mostly safe · **4** Fully safe

**Role: Guesser**

- **Q2 — Clue Match**
  Does the guess match what the clue was actually pointing to?
  **1** No match · **2** Weak match · **3** Good match · **4** Strong match
- **Bolt-on — Valid Guess**
  Is this guess a real word that is present and still available on the board?
  **Yes** · **No** *(stored as `guess_on_board`)*
- **Bolt-on — Invalid Selection**
  Did the Guesser select the clue word itself, or a word that had already been guessed?
  **Yes** · **No** *(stored as `guessed_clue_or_used_word`)*

**Q3 — Reasoning Clarity:** not shown

**Ticks:** the Shared Core set

**G3 — this game's whole-game question(s):**

- **Whole game — How consistently safe were the ClueGiver's clues throughout the game?**
  **1** Very risky · **2** Somewhat risky · **3** Mostly safe · **4** Fully safe *(stored as `clue_safety_overall`)*

### dond

*52 transcript(s).*

**Role: DealOrNoDealPlayer**

- **Q1 — Prior Information Use**
  Did the AI correctly use information from earlier in the game?
  **1** None · **2** Partial · **3** Good · **4** Excellent *(Shared Core)*
- **Q2 — Sensible Next Step**
  Did this move make sense as a next step?
  **1** Nonsensical · **2** Poor · **3** Reasonable · **4** Strong *(Shared Core)*

**Q3 — Reasoning Clarity:** shown

**Ticks:**

- Repeated a move that already failed
- Invented or got a game fact wrong
- Noticed and fixed an earlier mistake
- Explanation does not match the move
- Revealed its own secret item values in the open chat

**G3 — this game's whole-game question(s):**

- **Whole game — Did each player's final secret proposal match the agreement reached in open chat?**
  **Both matched** · **Only Player 1 matched** · **Only Player 2 matched** · **Neither matched** · **No clear agreement was reached** *(stored as `proposals_match`)*
- **Whole game — How collaborative and coherent was the negotiation before the final proposals?**
  **1** no real negotiation · **2** · **3** · **4** · **5** · **6** · **7** fully collaborative *(stored as `negotiation_quality`)*

### guesswhat

*52 transcript(s).*

**Role: Answerer**

- *Not rated — shown for context only.*

**Role: Guesser**

- **Q1 — Prior Information Use**
  Did the AI correctly use information from earlier in the game?
  **1** None · **2** Partial · **3** Good · **4** Excellent *(Shared Core)*
- **Q2 — Sensible Next Step**
  Did this move make sense as a next step?
  **1** Nonsensical · **2** Poor · **3** Reasonable · **4** Strong *(Shared Core)*

**Q3 — Reasoning Clarity:** not shown

**Ticks:** the Shared Core set

**G3 — this game's whole-game question(s):**

- **Whole game — Did the Guesser's questioning use its turns efficiently, or were many turns spent without meaningfully narrowing the possibilities?**
  **1** turns mostly wasted · **2** · **3** · **4** · **5** · **6** · **7** every question narrowed it *(stored as `turn_efficiency`)*

### imagegame

*52 transcript(s).*

**Role: Instruction Follower**

- **Q1 — Backend Knowledge**
  Did the grid actually change to match this instruction?
  **1** No — didn't change, or changed wrongly · **2** Partly — changed but doesn't match · **3** Mostly — close, minor mismatch · **4** Yes — matches the instruction exactly

**Role: Instruction Giver**

- **Q1 — Backend Knowledge**
  Does this instruction correctly describe one cell / row / column of the real target grid? *(Pick N/A on the final "DONE" turn.)*
  **1** Wrong · **2** Partly right · **3** Mostly right · **4** Fully right · **N/A**
- **Q2 — Conversation Cohesion**
  Is this instruction a good next step for the shape, based on how much has been built so far?
  **1** Doesn't fit — feels like it's starting something new, not continuing the shape · **2** Barely fits — technically continues the shape, but an odd or inefficient choice right now · **3** Fits well — a sensible next step, even if not the most efficient one · **4** Fits perfectly — a clear, smart next step for the shape

**Q3 — Reasoning Clarity:** not shown

**Ticks:**

- The grid did not change to match the instruction just given
- The Giver noticed the grid was wrong or had not updated, and said so

**G3 — this game's whole-game question(s):**

- **Whole game — Did the Giver's plan correctly track toward the real target, regardless of small execution slips?**
  **1** plan itself was confused / didn't make sense · **2** · **3** · **4** · **5** · **6** · **7** clear and correct the whole way through *(stored as `giver_plan`)*

### privateshared

*52 transcript(s).*

**Role: Answerer**

- **Q1 — Knowledge & Disclosure Tracking**
  Does this answer correctly reflect what the model actually knows and what it has (or hasn't) already told the other party?
  **1** Wrong on both · **2** Wrong on one · **3** Mostly right · **4** Fully correct

**Q3 — Reasoning Clarity:** not shown

**Ticks:**

- Got its own private fact wrong or forgot it
- Lost track of what it had already revealed to the other party
- Format was wrong but the underlying answer was correct

**G3 — this game's whole-game question(s):**

- **Whole game — Where did most of the model's errors come from?**
  **Mostly forgot or got facts wrong** · **Mostly lost track of what it had revealed** · **A mix of both roughly equally** · **Neither — performance was clean throughout** *(stored as `error_source`)*

### referencegame

*52 transcript(s).*

**Role: InstructionFollower**

- **Q2 — Matches Every Detail**
  Does the chosen grid match every detail in the description, checked piece by piece?
  **1** No match · **2** Partial match · **3** Mostly matches · **4** Fully matches

**Role: InstructionGiver**

- **Q1 — Distinguishing Description**
  Was the description specific enough to tell this grid apart from the others?
  **1** Not specific · **2** Weak · **3** Mostly specific · **4** Fully specific

**Q3 — Reasoning Clarity:** not shown

**Ticks:** the Shared Core set

**G3 — this game's whole-game question(s):**

- **Whole game — Taken together, how well did the Giver's description and the Follower's pick work as a pair?**
  **1** vague description, wrong pick · **2** · **3** · **4** one side carried it · **5** · **6** · **7** precise description, pick matched every detail *(stored as `description_pick_pair`)*

### ta_frozen_lake

*52 transcript(s).*

**Role: Player 0**

- **Q1 — Prior Information Use**
  Did the AI correctly use information from earlier in the game?
  **1** None · **2** Partial · **3** Good · **4** Excellent *(Shared Core)*
- **Q2 — Sensible Next Step**
  Did this move make sense as a next step?
  **1** Nonsensical · **2** Poor · **3** Reasonable · **4** Strong *(Shared Core)*

**Q3 — Reasoning Clarity:** not shown

**Ticks:** the Shared Core set

**G3 — this game's whole-game question(s):**

- **Whole game — How coherent and goal-directed was the complete route taken by the model?**
  **1** aimless wandering · **2** · **3** · **4** · **5** · **6** · **7** direct and purposeful *(stored as `route_coherence`)*

### wordle-crazy_withclue

*52 transcript(s).*

**Role: WordGuesser**

- **Q1 — Prior Information Use**
  Did the AI correctly use information from earlier in the game?
  **1** None · **2** Partial · **3** Good · **4** Excellent *(Shared Core)*
- **Q2 — Sensible Next Step**
  Did this move make sense as a next step?
  **1** Nonsensical · **2** Poor · **3** Reasonable · **4** Strong *(Shared Core)*
- **Bolt-on — Clue Use *(opening guess only)***
  Does this first guess reflect the meaning of the clue given at the start?
  **1** Ignores the clue · **2** Loosely related · **3** Mostly reflects it · **4** Clearly reflects it *(first turn of this role only; `first_guess_uses_clue`)*

**Q3 — Reasoning Clarity:** shown

**Ticks:** the Shared Core set

**G3 — this game's whole-game question(s):**

- **Whole game — How consistently did the model combine the clue and letter feedback throughout the game?**
  **1** ignored both · **2** · **3** · **4** · **5** · **6** · **7** used both on every guess *(stored as `clue_feedback_integration`)*
