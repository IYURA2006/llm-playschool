"""Every annotation question in the study, and nothing else.

This file is data. It holds the questions annotators are asked and the scales
they are asked on; the code that renders them, stores them and exports them
lives in annotation.py and export_annotations.py and does not need editing to
change a question.

Three commands, none of which needs a database or a browser:

    python questions.py --show dond      # exactly what an annotator will see
    python questions.py --check          # validate every entry before it ships
    python questions.py --markdown       # regenerate question_set.md

--check is the safety net. The only other thing that verifies an edit is an
assertion that fires when an annotator presses Submit, which is far too late.

--markdown is why question_set.md must not be hand-edited: a document written
out by hand goes stale silently, which is exactly how the practice screen came
to name the wrong game and the wrong turn count for weeks.


HOW AN ENTRY IS SHAPED
----------------------

BESPOKE_QUESTIONS maps a game family (the folder name under games/, e.g.
"dond") to what that game overrides. Anything left out falls back to the
Shared Core: GENERIC_Q1, GENERIC_Q2, the SC_TICKS, and the conditional
GENERIC_Q3 / SC_TICK_D pair. A family with no entry here is pure Shared Core.

    "dond": {
        "roles": {                        # per-turn questions, keyed by the
            "DealOrNoDealPlayer": {       # role name in the transcript
                "q1": "generic",          # "generic" -> the Shared Core question
                "q2": None,               # None      -> not asked of this role
                # or a (label_md, choices) tuple -> this game's own question
            },
        },
        "flags": SC_TICKS + [...],        # None -> the Shared Core ticks
                                          # []   -> no ticks at all
        "reasoning_clarity": True,        # show Q3 + the explanation tick
        "whole_game": [...],              # asked once, on the verdict screen
    }

Choices are [(display, stored_value), ...]. Use the helpers rather than
writing them out: _scale4(["None", "Partial", "Good", "Excellent"]) for a 1-4
scale, _scaleN(7, {1: "low anchor", 7: "high anchor"}) for anything else. A
display of "3\nGood" renders as a numbered button; a bare "N/A" renders as an
escape hatch and stores "NA".

Whole-game entries are (id, label_md, choices). The id is what the answer is
stored under, so it must never change once data exists -- reordering the list
is safe, renaming an id is not.

Two extras, both per-role: "bolt_ons" adds a question to every turn of that
role, and "bolt_ons_first_turn" adds one to that role's opening turn only.

Every question label is "**Title**\n\nThe question?" -- plain_label() splits
on that shape to build the screen-reader name, so keep it.


BEFORE YOU CHANGE A QUESTION MID-STUDY
--------------------------------------

Each saved annotation carries a fingerprint of the exact question set it was
collected under (annotation.question_spec_hash). Editing a game that already
has rows does not corrupt them -- the export still decodes them correctly --
but it does split that game across two question sets, and an annotator with a
half-finished transcript is asked to redo it. Adding a family that has no data
yet costs nothing.
"""

import os


def _scale4(labels):
    """4-choice 1..4 scale radio, e.g. _scale4(["None","Partial","Good","Excellent"])."""
    return [(f"{i + 1}\n{lbl}", str(i + 1)) for i, lbl in enumerate(labels)]


def _scaleN(n, ends=None):
    """Plain-number 1..n scale radio; `ends={1: 'lo', n: 'hi'}` adds end anchors.
    Used for the bespoke whole-game "specific overall" questions (1-7 / 1-4)."""
    ends = ends or {}
    return [((f"{i}\n{ends[i]}" if i in ends else str(i)), str(i))
            for i in range(1, n + 1)]


# The universal per-turn questions, shaped like a bespoke (label_md, choices)
# entry so the render loop and export_annotations.py read both the same way.
# Q3 is conditional — hidden games get a preset-"NA" invisible radio instead.
GENERIC_Q1 = (
    "**Q1 — Prior Information Use**\n\nDid the AI correctly use information from earlier in the game?",
    [("1\nNone", "1"), ("2\nPartial", "2"), ("3\nGood", "3"), ("4\nExcellent", "4")],
)
GENERIC_Q2 = (
    "**Q2 — Sensible Next Step**\n\nDid this move make sense as a next step?",
    [("1\nNonsensical", "1"), ("2\nPoor", "2"), ("3\nReasonable", "3"), ("4\nStrong", "4")],
)
GENERIC_Q3 = (
    "**Q3 — Reasoning Clarity** · conditional\n\nHow clearly does the AI explain its move?",
    [("1\nUnclear", "1"), ("2\nConfused", "2"), ("3\nClear", "3"),
     ("4\nTransparent", "4"), ("N/A", "NA")],
)

# The Shared-Core ticks, as constants rather than a literal in load_game, so a
# bespoke set can EXTEND them (dond adds a secrecy tick) instead of restating
# the three strings — a copy would drift the moment one of them is reworded.
# SC_TICK_D is the conditional half of the explanation pair: it is shown with
# SC_Q3 and only where the game asks the model to explain its move.
SC_TICKS = [
    "Repeated a move that already failed",
    "Invented or got a game fact wrong",
    "Noticed and fixed an earlier mistake",
]
SC_TICK_D = "Explanation does not match the move"


# bbh/cladder/mmlu_pro are auto-scored one-shot answers, so the only useful
# human question is whether the REASONING behind the answer is sound.
_QA_REASONING_ROLE = {
    "roles": {
        "Answerer": {
            "q1": (
                "**Q1 — Reasoning Soundness**\n\nIs the reasoning that leads to "
                "the answer logically valid?",
                _scale4(["Flawed", "Weak", "Mostly sound", "Fully sound"]),
            ),
            "q2": None,
        },
    },
}


# Per-game question sets used only in hybrid mode (see BLOCK_TO_TYPE). A
# role/slot missing here falls back to the generic Q1/Q2 widget.
BESPOKE_QUESTIONS = {
    # QA reasoning benchmarks — see _QA_REASONING_ROLE above.
    "bbh": _QA_REASONING_ROLE,
    "cladder": _QA_REASONING_ROLE,
    "mmlu_pro": _QA_REASONING_ROLE,
    "codenames": {
        "flags": None,  # keep the generic 3 (+ reasoning-mismatch) flags
        "roles": {
            "ClueGiver": {
                "q1": (
                    "**Q1 — Clue Safety**\n\nCould this clue plausibly lead the "
                    "Guesser toward the assassin or a wrong word?",
                    _scale4(["Very risky", "Somewhat risky", "Mostly safe", "Fully safe"]),
                ),
                "q2": None,
            },
            "Guesser": {
                "q1": None,
                "q2": (
                    "**Q2 — Clue Match**\n\nDoes the guess match what the clue "
                    "was actually pointing to?",
                    _scale4(["No match", "Weak match", "Good match", "Strong match"]),
                ),
                "bolt_ons": [
                    (
                        "guess_on_board",
                        "**Bolt-on — Valid Guess**\n\nIs this guess a real word "
                        "that is present and still available on the board?",
                        [("Yes", "yes"), ("No", "no")],
                    ),
                    (
                        "guessed_clue_or_used_word",
                        "**Bolt-on — Invalid Selection**\n\nDid the Guesser "
                        "select the clue word itself, or a word that had already "
                        "been guessed?",
                        [("Yes", "yes"), ("No", "no")],
                    ),
                ],
            },
        },
        "whole_game": [
            (
                "clue_safety_overall",
                # scale_1_4, NOT the 1-7 the other games use — the spec asks for
                # the same four-point scale as the per-turn safety question so
                # the two are directly comparable.
                "**Whole game — How consistently safe were the ClueGiver's "
                "clues throughout the game?**",
                _scale4(["Very risky", "Somewhat risky", "Mostly safe", "Fully safe"]),
            ),
        ],
    },
    "taboo": {
        "flags": [
            "Guesser repeated a guess it already made",
            "Describer gave the same (or same-meaning) clue as an earlier turn",
        ],
        "roles": {
            "WordDescriber": {
                # Clarity only — the game engine already checks forbidden-word use.
                "q1": (
                    "**Q1 — Clue Clarity**\n\nWas this clue clear enough for the "
                    "Guesser to work out the word? *(forbidden-word use is checked "
                    "automatically — ignore that here)*",
                    _scale4([
                        "Not clear at all", "Barely clear, very vague",
                        "Mostly clear, minor ambiguity", "Fully clear, easy to guess from",
                    ]),
                ),
                "q2": None,
            },
            "WordGuesser": {
                "q1": None,
                "q2": (
                    "**Q2 — Guess Match**\n\nDid the guess match what the clue "
                    "was pointing to?",
                    _scale4([
                        "No connection", "Weak, a stretch",
                        "Reasonable, not exact", "Directly matches",
                    ]),
                ),
            },
        },
        "whole_game": [
            (
                "**Whole game — Did the Describer adjust its clues based on what "
                "the Guesser got wrong?**",
                _scaleN(7, {1: "kept repeating the same approach",
                            7: "clearly adjusted each time"}),
            ),
        ],
    },
    "hot_air_balloon": {
        "flags": [
            "Offer contradicted the player's own stated reasoning",
            "Contradicted something the player said earlier about its values",
            "Offer exceeded the fixed effort limit",
        ],
        "roles": {
            # Both seats share the "Negotiator" role — same swap applies to either.
            "Negotiator": {
                "q1": (
                    "**Q1 — Explanation Clarity**\n\nSetting aside whether you "
                    "agree with the outcome, was the player's explanation "
                    "logical and easy to follow?",
                    _scale4(["Confusing", "Partly clear", "Mostly clear", "Fully clear"]),
                ),
                "q2": "generic",  # no ground-truth Q1 here, but Q2 is unaffected
            },
        },
    },
    "textmapworld_graphreasoning": {
        # Ring colours here match the map legend in _MAP_LEGEND_HTML.
        "flags": [
            "Spatial hallucination — tried to go somewhere that doesn't exist, or invented a room",
            "Self-correction — a room it drew wrong earlier (red) is later corrected (green) and matches its updated map",
            "Final map doesn't match its own moves — a move it made is missing from, or contradicts, its final map",
        ],
        "render_graph": True,
        "roles": {
            "PathGuesser": {
                "q1": (
                    "**Q1 — Map Self-Consistency**\n\nLooking only at the map "
                    "the AI has drawn so far — does this move make sense, even "
                    "if the map turns out to be wrong? *You are not "
                    "checking if the AI is right, only if it is consistent "
                    "with what it believes.*",
                    [
                        ("1\nMakes no sense — contradicts its own map", "1"),
                        ("2\nDoesn't quite fit — hard to explain from its own map", "2"),
                        ("3\nMostly makes sense — reasonable, small issues", "3"),
                        ("4\nMakes perfect sense — smart, logical given its own map", "4"),
                    ],
                ),
                "q2": None,
            },
        },
        "whole_game": [
            (
                "**Whole game — Did it build an accurate, consistent map, and "
                "recognize when it was done?**",
                _scaleN(7),
            ),
        ],
    },
    "dond": {
        # design_type: shared_core, plus the conditional explanation pair
        # (SC_Q3 / SC_TICK_D) — dond responses carry reasoning.
        # SC_TICK_A/B/C + SC_TICK_D, extended with the one hazard the core
        # cannot express: the item values are private, and weaker models put
        # them straight into the open chat.
        "flags": SC_TICKS + [
            SC_TICK_D,
            "Revealed its own secret item values in the open chat",
        ],
        "reasoning_clarity": True,  # show SC_Q3 + the explanation-mismatch tick
        "roles": {
            # Both seats share the "DealOrNoDealPlayer" role.
            "DealOrNoDealPlayer": {"q1": "generic", "q2": "generic"},
        },
        "whole_game": [
            (
                "proposals_match",
                "**Whole game — Did each player's final secret proposal match "
                "the agreement reached in open chat?**",
                [
                    ("Both matched", "both"),
                    ("Only Player 1 matched", "p1"),
                    ("Only Player 2 matched", "p2"),
                    ("Neither matched", "neither"),
                    ("No clear agreement was reached", "no_agreement"),
                ],
            ),
            (
                "negotiation_quality",
                # Deliberately says nothing about whether the split was FAIR or
                # close to best value — the spec excludes that judgement, and
                # the previous wording ("close to the best value for both") was
                # exactly it. Communication quality is kept separate from
                # whether the proposals matched, which is the question above.
                "**Whole game — How collaborative and coherent was the "
                "negotiation before the final proposals?**",
                _scaleN(7, {1: "no real negotiation",
                            7: "fully collaborative"}),
            ),
        ],
    },
    "clean_up": {
        "flags": [
            "Repeated a proposal already rejected",
            "Misstated its own object's position",
            "Declared success without re-checking actual positions",
        ],
        "roles": {
            # Both seats share the "GridCleaner" role.
            "GridCleaner": {
                "q1": (
                    "**Q1 — Uses Stated Positions**\n\nDid this turn correctly use "
                    "positions either player already stated?",
                    _scale4([
                        "Ignored/contradicted one", "Used some, missed one",
                        "Mostly consistent", "Fully correct",
                    ]),
                ),
                "q2": (
                    "**Q2 — Sensible Next Step**\n\nDid this proposal make sense "
                    "as a next step?",
                    _scale4([
                        "Nonsensical", "Wastes a turn",
                        "Reasonable", "Efficient, well-targeted",
                    ]),
                ),
            },
        },
        # Two separate whole-game scores on purpose: clarity and efficiency are
        # different (a game can be clear-but-slow or confusing-but-fast).
        "whole_game": [
            (
                "**Whole game (1 of 2) — Did they communicate clearly, without "
                "confusion?**",
                _scaleN(4),
            ),
            (
                "**Whole game (2 of 2) — Did they reach agreement without "
                "unnecessary repetition?**",
                _scaleN(4),
            ),
        ],
    },
    "imagegame": {
        # Giver and Follower are different roles, so each gets its own question set.
        # The core ticks don't fit a game whose whole failure mode is an
        # instruction that never lands on the grid, so this is its own pair.
        # The first overlaps the Follower's Q1 by design: Q1 grades how well the
        # grid matched, the tick records the discrete "it didn't move at all"
        # event, which is the one the Giver is then supposed to notice.
        "flags": [
            "The grid did not change to match the instruction just given",
            "The Giver noticed the grid was wrong or had not updated, and said so",
        ],
        # whole_game_only was True, which hid G1/G2 entirely. The study keeps
        # the generic overall pair on every game so there is one cross-game
        # comparable measure, so the flag is gone.
        "roles": {
            "Instruction Giver": {
                "q1": (
                    "**Q1 — Backend Knowledge**\n\nDoes this instruction correctly "
                    "describe one cell / row / column of the real target grid? "
                    "*(Pick N/A on the final \"DONE\" turn.)*",
                    [
                        ("1\nWrong", "1"),
                        ("2\nPartly right", "2"),
                        ("3\nMostly right", "3"),
                        ("4\nFully right", "4"),
                        ("N/A", "NA"),
                    ],
                ),
                "q2": (
                    "**Q2 — Conversation Cohesion**\n\nIs this instruction a good "
                    "next step for the shape, based on how much has been built so far?",
                    _scaleN(4, {
                        1: "Doesn't fit — feels like it's starting something "
                           "new, not continuing the shape",
                        2: "Barely fits — technically continues the shape, but "
                           "an odd or inefficient choice right now",
                        3: "Fits well — a sensible next step, even if not the "
                           "most efficient one",
                        4: "Fits perfectly — a clear, smart next step for the shape",
                    }),
                ),
            },
            "Instruction Follower": {
                "q1": (
                    "**Q1 — Backend Knowledge**\n\nDid the grid actually change "
                    "to match this instruction?",
                    _scale4([
                        "No — didn't change, or changed wrongly",
                        "Partly — changed but doesn't match",
                        "Mostly — close, minor mismatch",
                        "Yes — matches the instruction exactly",
                    ]),
                ),
                "q2": None,
            },
        },
        "whole_game": [
            (
                "giver_plan",
                "**Whole game — Did the Giver's plan correctly track toward the "
                "real target, regardless of small execution slips?**",
                _scaleN(7, {1: "plan itself was confused / didn't make sense",
                            7: "clear and correct the whole way through"}),
            ),
            # The spec asks for ONE overall here. Follower execution is already
            # captured per turn by the Follower's Q1 ("did the grid actually
            # change to match this instruction?"), so a whole-game duplicate of
            # it bought nothing.
        ],
    },

    # 13 families brought in from the test_newgames bundle (see games/<name>/
    # for the transcript data). cryptolect, ta_blackjack, and the data-less
    # bbh/cladder/mmlu_pro placeholders above are deliberately excluded.

    "eqbench": {
        # Auto-scored against a reference distribution, so the human check is
        # just whether the emotional read is plausible, not exact-match accuracy.
        "flags": [],
        "roles": {
            "Answerer": {
                "q1": (
                    "**Q1 — Emotional Plausibility**\n\nEven if it doesn't "
                    "match the reference numbers exactly, is this a "
                    "plausible, well-grounded read of the character's likely "
                    "emotions given the dialogue?",
                    _scale4(["Implausible", "Some stretch", "Mostly plausible", "Fully plausible"]),
                ),
                "q2": None,
            },
        },
    },
    "ifeval": {
        # Format compliance is auto-checked; the human check is whether the
        # response also still makes sense, not just whether it complies.
        "flags": [],
        "roles": {
            "InstructionFollower": {
                "q1": (
                    "**Q1 — Instruction Compliance**\n\nDoes the response "
                    "follow the stated instruction/constraint, while still "
                    "being sensible, on-topic content?",
                    _scale4(["Ignores it", "Partly follows", "Mostly follows", "Fully follows"]),
                ),
                "q2": None,
            },
        },
    },
    "chronicle": {
        "flags": [
            "Narrator's sentence contradicts an earlier clue",
            "Narrator came close to directly naming the event",
            "Detective's guess ignores or contradicts the clues given so far",
        ],
        "roles": {
            "ChronicleNarrator": {
                "q1": (
                    "**Q1 — Clue Safety**\n\nDoes this sentence add "
                    "genuinely new, accurate information without giving away "
                    "the event directly?",
                    _scale4(["Gives it away", "Risky", "Mostly indirect", "Fully indirect"]),
                ),
                "q2": None,
            },
            "ChronicleDetective": {
                "q1": None,
                "q2": (
                    "**Q2 — Guess Groundedness**\n\nDoes the guess/analysis "
                    "logically follow from the clues given so far?",
                    _scale4(["Ignores them", "Weak link", "Mostly grounded", "Fully grounded"]),
                ),
            },
        },
        "whole_game": [
            (
                "**Whole game — Did the pair converge on the correct event "
                "through genuine deduction?**",
                _scaleN(7),
            ),
        ],
    },
    "get_to_the_point": {
        # Codenames-shaped: same clue-safety / guess-match split as ClueGiver/Guesser.
        "flags": [
            "Clue directly reveals (or nearly reveals) the target word",
            "Guess ignores the clue actually given",
            "Repeated a guess already made",
        ],
        "roles": {
            "Helper": {
                "q1": (
                    "**Q1 — Clue Indirection**\n\nIs this clue genuinely "
                    "indirect (doesn't give the target word away) while "
                    "still being useful?",
                    _scale4(["Gives it away", "Risky", "Mostly indirect", "Fully indirect"]),
                ),
                "q2": None,
            },
            "Seeker": {
                "q1": None,
                "q2": (
                    "**Q2 — Guess Match**\n\nDoes the guess sensibly follow "
                    "from the clue that was actually given?",
                    _scale4(["No match", "Weak match", "Good match", "Strong match"]),
                ),
            },
        },
        "whole_game": [
            (
                "**Whole game — Did the pair converge on the target "
                "efficiently, each clue narrowing it down?**",
                _scaleN(7),
            ),
        ],
    },
    "st_clean_up": {
        # Same collaborative move-objects mechanic as clean_up, just on a bigger grid.
        "flags": [
            "Repeated a proposal already rejected",
            "Misstated its own object's position",
            "Declared success without re-checking actual positions",
        ],
        "roles": {
            "GridCleaner": {
                "q1": (
                    "**Q1 — Uses Stated Positions**\n\nDid this turn "
                    "correctly use positions either player already stated?",
                    _scale4([
                        "Ignored/contradicted one", "Used some, missed one",
                        "Mostly consistent", "Fully correct",
                    ]),
                ),
                "q2": (
                    "**Q2 — Sensible Next Step**\n\nDid this proposal make "
                    "sense as a next step?",
                    _scale4([
                        "Nonsensical", "Wastes a turn",
                        "Reasonable", "Efficient, well-targeted",
                    ]),
                ),
            },
        },
        "whole_game": [
            (
                "**Whole game (1 of 2) — Did they communicate clearly, "
                "without confusion?**",
                _scaleN(4),
            ),
            (
                "**Whole game (2 of 2) — Did they reach agreement without "
                "unnecessary repetition?**",
                _scaleN(4),
            ),
        ],
    },
    "ta_frozen_lake": {
        # design_type: shared_core. This family logs the role as plain
        # "Player 0", not a descriptive name.
        "flags": None,  # SC_TICK_A/B/C — note SC_TICK_A ("repeated a move that
                        # already failed") is exactly the hazard this game
                        # needs, so the custom list added nothing the core
                        # didn't already cover.
        "roles": {
            "Player 0": {"q1": "generic", "q2": "generic"},
        },
        "whole_game": [
            (
                "route_coherence",
                "**Whole game — How coherent and goal-directed was the complete "
                "route taken by the model?**",
                _scaleN(7, {1: "aimless wandering",
                            7: "direct and purposeful"}),
            ),
        ],
    },
    "ta_mastermind": {
        "flags": [
            "Guess contradicts the feedback from an earlier guess",
            "Repeated a combination already ruled out",
            "Ignored a peg count it could have deduced from prior feedback",
        ],
        "roles": {
            "Codebreaker": {
                "q1": (
                    "**Q1 — Feedback Consistency**\n\nDoes this guess stay "
                    "consistent with the black/white-peg feedback from every "
                    "earlier guess?",
                    _scale4(["Contradicts it", "Partly consistent", "Mostly consistent", "Fully consistent"]),
                ),
                "q2": (
                    "**Q2 — Information Gain**\n\nIs this guess a genuine "
                    "attempt to narrow down the code, not a redundant "
                    "repeat?",
                    _scale4(["Redundant", "Weak", "Reasonable", "Well-targeted"]),
                ),
            },
        },
        "whole_game": [
            (
                "**Whole game — Did it deduce the code efficiently from the "
                "feedback it received?**",
                _scaleN(7),
            ),
        ],
    },
    "ta_sokoban": {
        # Same "Player 0" role-key convention as ta_frozen_lake.
        "flags": [
            "Misread the board (wrong wall/box/target position)",
            "Pushed a box somewhere it can no longer be recovered from (deadlock)",
            "Repeated a move that already failed or made no progress",
        ],
        "roles": {
            "Player 0": {
                "q1": (
                    "**Q1 — Board Reading**\n\nDoes the move correctly "
                    "reflect the actual current positions of walls, boxes, "
                    "and targets?",
                    _scale4(["Misreads it", "Partly right", "Mostly right", "Fully right"]),
                ),
                "q2": (
                    "**Q2 — Progress Without Deadlock**\n\nDoes this move "
                    "make progress toward solving the puzzle without risking "
                    "an unrecoverable deadlock?",
                    _scale4(["Creates a deadlock", "Risky", "Safe, some progress", "Safe, clear progress"]),
                ),
            },
        },
        "whole_game": [
            (
                "**Whole game — Did it solve the puzzle through genuine "
                "planning, not trial-and-error?**",
                _scaleN(7),
            ),
        ],
    },
    "toh_multi_turn": {
        "flags": [
            "Move violates the 'no larger disk on a smaller one' rule",
            "Move repeats or immediately undoes the previous move (no progress)",
            "Misreads which disk is on top of a peg",
        ],
        "roles": {
            "PegHopper": {
                "q1": (
                    "**Q1 — Move Legality**\n\nIs the submitted move "
                    "consistent with the actual current peg configuration "
                    "and the game's legality rule?",
                    _scale4(["Illegal / misreads state", "Legal but confused", "Mostly sound", "Fully sound"]),
                ),
                "q2": (
                    "**Q2 — Plan Efficiency**\n\nDoes this move look like "
                    "part of an efficient plan toward solving in the minimum "
                    "number of steps, rather than a wasted or backtracking "
                    "move?",
                    _scale4(["Wasted/backtracks", "Inefficient", "Reasonable", "Efficient"]),
                ),
            },
        },
        "whole_game": [
            (
                "**Whole game — Did it solve the puzzle with a coherent, "
                "efficient plan?**",
                _scaleN(7),
            ),
        ],
    },
    "clockwork_courier": {
        "flags": [
            "Misread the map or a guard/gate position",
            "Plan ignores the guard/gate schedule it was just told",
            "Wasted a full round making no delivery progress",
        ],
        "roles": {
            "Courier": {
                "q1": (
                    "**Q1 — Schedule Reading**\n\nDoes the courier's stated "
                    "plan correctly account for the map and the guard/gate "
                    "schedule as known at this point?",
                    _scale4(["Ignores it", "Partly accounts for it", "Mostly correct", "Fully correct"]),
                ),
                "q2": (
                    "**Q2 — Delivery Progress**\n\nIs this round's plan a "
                    "sensible step toward pickups/deliveries given the "
                    "remaining time?",
                    _scale4(["Not sensible", "Weak", "Reasonable", "Efficient"]),
                ),
            },
        },
        "whole_game": [
            (
                "**Whole game — Was the overall route efficient and "
                "schedule-aware?**",
                _scaleN(7),
            ),
        ],
    },
}

# wordle-crazy scrambles the feedback colors (see _WD_TILE_RE) and defines the
# mapping only in that episode's own rules, so this asks whether the model
# applied THAT key correctly, not just whether it guessed well.
_WORDLE_CRAZY_Q1 = (
    "**Q1 — Rule Application**\n\nDoes this guess correctly use what the "
    "PREVIOUS feedback colors meant according to *this episode's own* rules "
    "(the color key is scrambled — don't assume standard Wordle green/yellow)?",
    _scale4(["Misapplies it", "Partly right", "Mostly right", "Fully right"]),
)
_WORDLE_CRAZY_FLAGS = [
    "Guess ignores or contradicts a color clue from an earlier guess",
    "Reused a letter already ruled out by this episode's own rules",
]
_WORDLE_CRAZY_WHOLE_GAME = [
    (
        "color_key_tracking",
        "**Whole game — Did it correctly track and apply this episode's "
        "scrambled color key across guesses?**",
        _scaleN(7),
    ),
]

BESPOKE_QUESTIONS.update({
    "wordle-crazy": {
        "flags": _WORDLE_CRAZY_FLAGS,
        "roles": {
            "WordGuesser": {
                "q1": _WORDLE_CRAZY_Q1,
                "q2": "generic",  # standard "sensible next guess" still applies
            },
        },
        "whole_game": _WORDLE_CRAZY_WHOLE_GAME,
    },
    # design_type: shared_core, plus the conditional explanation pair. This is
    # the STUDY variant, so it follows the spec's locked phrasing; the other
    # wordle-crazy entries are out of scope and keep their tailored sets.
    "wordle-crazy_withclue": {
        "flags": None,              # SC_TICK_A/B/C
        "reasoning_clarity": True,  # SC_Q3 + the explanation-mismatch tick
        "roles": {
            "WordGuesser": {
                "q1": "generic", "q2": "generic",
                # Asked on the opening guess only: the clue is given once, at
                # the start, and by the second guess there is letter feedback
                # to reason from, so "did it use the clue" stops being a clean
                # question. See first_turn_of_role().
                "bolt_ons_first_turn": [
                    (
                        "first_guess_uses_clue",
                        "**Bolt-on — Clue Use** *(opening guess only)*\n\nDoes "
                        "this first guess reflect the meaning of the clue given "
                        "at the start?",
                        _scale4(["Ignores the clue", "Loosely related",
                                 "Mostly reflects it", "Clearly reflects it"]),
                    ),
                ],
            },
        },
        "whole_game": [
            (
                "clue_feedback_integration",
                "**Whole game — How consistently did the model combine the clue "
                "and letter feedback throughout the game?**",
                _scaleN(7, {1: "ignored both",
                            7: "used both on every guess"}),
            ),
        ],
    },
    "wordle-crazy_withcritic": {
        "flags": _WORDLE_CRAZY_FLAGS + [
            "Critic's comment doesn't engage with the actual feedback",
        ],
        "roles": {
            "ReflectingWordGuesser": {
                "q1": _WORDLE_CRAZY_Q1,
                "q2": "generic",
            },
            "WordCritic": {
                "q1": None,
                "q2": (
                    "**Q2 — Critique Quality**\n\nDoes the critique "
                    "correctly evaluate the guess against the actual "
                    "feedback, rather than generic praise or criticism?",
                    _scale4(["Generic/wrong", "Superficial", "Mostly grounded", "Fully grounded"]),
                ),
            },
        },
        "whole_game": [
            (
                "**Whole game — Did the guesser and critic correctly track "
                "this episode's scrambled color key across guesses?**",
                _scaleN(7),
            ),
        ],
    },
})


# Shared Q1/Q2 for plain Wordle and its clue variant — same guessing mechanic.
_WORDLE_Q1 = (
    "**Q1 — Uses All Feedback**\n\nDid the AI correctly use letter feedback "
    "from every earlier guess, not just the last one?",
    _scale4(["Ignored it", "Partial use", "Mostly consistent", "Fully consistent"]),
)
_WORDLE_Q2 = (
    "**Q2 — Strategic Next Step**\n\nDid this guess make strategic sense as "
    "a next step — including using the clue's meaning, where one was given?",
    _scale4(["Nonsensical", "Weak", "Reasonable", "Strong"]),
)

# Games that used to fall back to the generic Q1/Q2 now get bespoke wording.
BESPOKE_QUESTIONS.update({
    "guesswhat": {
        # design_type: shared_core. The study spec locks SC_Q1/SC_Q2 phrasing —
        # game-specific wording belongs in the supplementary overall question,
        # not in a rewritten per-turn prompt.
        "roles": {
            "Guesser": {"q1": "generic", "q2": "generic"},
            # The Answerer's forced yes/no has no strategy worth judging;
            # the spec says not to evaluate it as a separate role.
            "Answerer": {"q1": None, "q2": None},
        },
        "flags": None,  # SC_TICK_A/B/C — the default set
        "whole_game": [
            (
                "turn_efficiency",
                "**Whole game — Did the Guesser's questioning use its turns "
                "efficiently, or were many turns spent without meaningfully "
                "narrowing the possibilities?**",
                _scaleN(7, {1: "turns mostly wasted",
                            7: "every question narrowed it"}),
            ),
        ],
    },
    "matchit_ascii": {
        "roles": {
            # Both seats share the "MatchItPlayer" role.
            "MatchItPlayer": {
                "q1": (
                    "**Q1 — Grid Accuracy**\n\nDoes this description or "
                    "answer correctly match what's actually in the player's "
                    "own grid?",
                    _scale4(["Wrong", "Partly right", "Mostly right", "Fully right"]),
                ),
                "q2": "generic",
            },
        },
        "flags": [
            "Uses a symbol that doesn't exist in this game's format",
            "Contradicts something the same player already said earlier",
        ],
        "whole_game": [
            (
                "**Whole game — How accurate were the claims that led to "
                "the final decision?**",
                _scaleN(7),
            ),
        ],
    },
    "privateshared": {
        # Player 2 ("Questioner") is a scripted bot, not a real AI seat.
        "roles": {
            "Answerer": {
                "q1": (
                    "**Q1 — Knowledge & Disclosure Tracking**\n\nDoes this "
                    "answer correctly reflect what the model actually knows "
                    "and what it has (or hasn't) already told the other "
                    "party?",
                    _scale4(["Wrong on both", "Wrong on one", "Mostly right", "Fully correct"]),
                ),
                "q2": None,
            },
        },
        "flags": [
            "Got its own private fact wrong or forgot it",
            "Lost track of what it had already revealed to the other party",
            "Format was wrong but the underlying answer was correct",
        ],
        "whole_game": [
            (
                "error_source",
                "**Whole game — Where did most of the model's errors come from?**",
                [
                    ("Mostly forgot or got facts wrong", "facts"),
                    ("Mostly lost track of what it had revealed", "tracking"),
                    ("A mix of both roughly equally", "mixed"),
                    ("Neither — performance was clean throughout", "clean"),
                ],
            ),
        ],
    },
    # A bespoke entry must set reasoning_clarity itself, or Q3 gets dropped in
    # hybrid mode — having ANY bespoke entry here overrides the g.has_reasoning check.
    "wordle": {
        "reasoning_clarity": True,
        "roles": {"WordGuesser": {"q1": _WORDLE_Q1, "q2": _WORDLE_Q2}},
        # No flags/whole_game override — the generic flag set already fits,
        # and the outcome (Win/Lose, Closeness Score) is already exact.
    },
    "wordle_withclue": {
        "reasoning_clarity": True,
        "roles": {"WordGuesser": {"q1": _WORDLE_Q1, "q2": _WORDLE_Q2}},
    },
    "wordle_withcritic": {
        # Own game_key: its roles are ReflectingWordGuesser/WordCritic, not WordGuesser.
        "reasoning_clarity": True,
        "roles": {
            "ReflectingWordGuesser": {"q1": _WORDLE_Q1, "q2": _WORDLE_Q2},
            "WordCritic": {
                "q1": None,
                "q2": (
                    "**Q2 — Critique Quality**\n\nDoes this critique point at "
                    "something real and specific about the guess, not just a "
                    "vague comment?",
                    _scale4(["Vague/generic", "Somewhat specific", "Mostly specific", "Fully specific"]),
                ),
            },
        },
        "flags": ["Guesser ignored a valid critic objection without explanation"],
        # No whole_game — sample size too thin for a confident whole-game claim.
    },
    "referencegame": {
        "roles": {
            "InstructionGiver": {
                "q1": (
                    "**Q1 — Distinguishing Description**\n\nWas the "
                    "description specific enough to tell this grid apart "
                    "from the others?",
                    _scale4(["Not specific", "Weak", "Mostly specific", "Fully specific"]),
                ),
                "q2": None,
            },
            "InstructionFollower": {
                "q1": None,
                "q2": (
                    "**Q2 — Matches Every Detail**\n\nDoes the chosen grid "
                    "match every detail in the description, checked piece "
                    "by piece?",
                    _scale4(["No match", "Partial match", "Mostly matches", "Fully matches"]),
                ),
            },
        },
        # No flags override — the episode is always exactly 2 turns.
        "whole_game": [
            (
                "description_pick_pair",
                # The per-turn pair scores each side alone; the failure this
                # game actually shows is a clear description followed by a
                # wrong pick, which only reads as a pair.
                "**Whole game — Taken together, how well did the Giver's "
                "description and the Follower's pick work as a pair?**",
                # A mid anchor as well as the two ends: 4 is the specific case
                # this question exists to capture, and it is not the midpoint
                # of "bad to good" that annotators would otherwise assume.
                _scaleN(7, {
                    1: "vague description, wrong pick",
                    4: "one side carried it",
                    7: "precise description, pick matched every detail",
                }),
            ),
        ],
    },
    "adventuregame": {
        "roles": {
            "Adventurer": {
                "q1": (
                    "**Q1 — Useful Progress**\n\nWas this step useful "
                    "progress toward the goal, or a wasted detour?",
                    _scale4(["Wasted detour", "Weak", "Reasonable", "Clearly useful"]),
                ),
                "q2": "generic",
            },
        },
        "flags": [
            "Repeated a move already shown to fail",
            "Claimed something false about the game world",
            "Ignored an object's stated features (open/closed, location, capacity) before acting",
        ],
    },
    "textmapworld_specificroom": {
        # Model only emits "GO: <dir>"/"DONE", so render_path draws the path instead of a map.
        "render_path": True,
        "roles": {
            "PathGuesser": {
                "q1": (
                    "**Q1 — New Ground**\n\nDoes this move use what the "
                    "model has already learned about the map, or repeat "
                    "ground it already covered?",
                    [("1\nRepeats already-covered ground", "1"),
                     ("2\nUses new information", "2")],
                ),
                "q2": None,
            },
        },
        "flags": ["Tried to move through a connection that doesn't exist "
                  "on the model's own explored map"],
        "whole_game": [
            (
                "**Whole game — Did it navigate to the target efficiently, "
                "without unnecessary backtracking?**",
                _scaleN(7),
            ),
        ],
    },
})


# ---------------------------------------------------------------------------
# CLI — `python questions.py --check` / `--show <game>`.
#
# annotation.py is imported lazily inside these functions, not at module level:
# it imports this file, and a top-level import here would be a cycle. Both
# commands reuse the app's own functions (show_q3_for, first_turn_of_role,
# question_spec) rather than reimplementing the render rules, so a preview can
# never quietly disagree with what an annotator is actually shown.
# ---------------------------------------------------------------------------

_ENTRY_KEYS = {"roles", "flags", "whole_game", "whole_game_only",
               "reasoning_clarity", "render_graph", "render_path"}
_ROLE_KEYS = {"q1", "q2", "bolt_ons", "bolt_ons_first_turn"}
_BOOL_KEYS = {"whole_game_only", "reasoning_clarity", "render_graph", "render_path"}


def _families():
    """{family: [transcript path, …]} for everything discoverable, so a check
    can tell "this role never occurs" from "there are no transcripts to tell"."""
    import annotation
    out = {}
    for _name, path in annotation.GAMES:
        out.setdefault(annotation.game_key(path), []).append(path)
    return out


def _check_choices(where, choices, err, warn):
    if not isinstance(choices, (list, tuple)) or not choices:
        err(f"{where}: choices must be a non-empty list")
        return
    values, numbered, plain = [], [], []
    for j, pair in enumerate(choices):
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            err(f"{where}: choice {j} is not a (display, value) pair")
            continue
        display, value = pair
        if not isinstance(display, str) or not isinstance(value, str):
            err(f"{where}: choice {j} has a non-string display or value")
            continue
        values.append(value)
        head = display.split("\n", 1)[0].strip()
        if head.isdigit():
            numbered.append(int(head))
        elif value != "NA":
            plain.append(display)
    if len(set(values)) != len(values):
        err(f"{where}: duplicate stored values {sorted(values)}")
    # An all-numbered list is a scale and an all-plain one is a set of named
    # options (Yes/No, "which error dominated"); both are fine. A list that
    # mixes them is the smell — half the options carry an implied ordering.
    if numbered and plain:
        err(f"{where}: mixes numbered scale points with unnumbered options "
            f"{plain} — a scale should be numbered throughout")
    # A scale the annotator reads left to right must run 1, 2, 3, … — a gap or
    # a restart means the stored number no longer means what the label says.
    if numbered and numbered != list(range(1, len(numbered) + 1)):
        err(f"{where}: numbered choices are {numbered}, expected 1..{len(numbered)}")
    if "NA" in values and values[-1] != "NA":
        warn(f"{where}: N/A is not the last choice")


def _check_label(where, md, err, warn):
    if not isinstance(md, str) or not md.strip():
        err(f"{where}: label must be a non-empty string")
    elif "**" not in md:
        warn(f"{where}: label has no **Title** — plain_label builds the "
             f"screen-reader name from that shape")


def _check_slot(where, cfg, err, warn):
    """q1/q2: "generic", None, or a (label_md, choices) tuple."""
    if cfg == "generic" or cfg is None:
        return
    if not (isinstance(cfg, tuple) and len(cfg) == 2):
        err(f"{where}: must be \"generic\", None, or a (label_md, choices) "
            f"tuple — got {type(cfg).__name__}")
        return
    _check_label(where, cfg[0], err, warn)
    _check_choices(where, cfg[1], err, warn)


def _check_bolt_ons(where, entries, keys_seen, err, warn):
    for i, e in enumerate(entries or []):
        if not (isinstance(e, (list, tuple)) and len(e) == 3):
            err(f"{where}[{i}]: must be (key, label_md, choices)")
            continue
        key, md, choices = e
        if not isinstance(key, str) or not key.isidentifier():
            # The key becomes an "extra:<key>" column in the CSV export.
            err(f"{where}[{i}]: key {key!r} must be a valid identifier")
        elif key in keys_seen:
            err(f"{where}[{i}]: key {key!r} is already used by this role")
        else:
            keys_seen.add(key)
        _check_label(f"{where}[{i}]", md, err, warn)
        _check_choices(f"{where}[{i}]", choices, err, warn)


def check(verbose=False):
    """Validate every entry. Returns (errors, warnings) as lists of strings."""
    import annotation
    errors, warnings = [], []
    err, warn = errors.append, warnings.append
    fams = _families()
    # Entries whose family has no transcripts under the current GAMES_DIR.
    # Normal and expected when running against a curated tree, so they are
    # summarised on one line rather than repeated per game.
    unreachable = []

    for game in sorted(BESPOKE_QUESTIONS):
        entry = BESPOKE_QUESTIONS[game]
        if not isinstance(entry, dict):
            err(f"{game}: entry must be a dict")
            continue
        for k in sorted(set(entry) - _ENTRY_KEYS):
            err(f"{game}: unknown key {k!r} — expected one of "
                f"{', '.join(sorted(_ENTRY_KEYS))}")
        for k in sorted(set(entry) & _BOOL_KEYS):
            if not isinstance(entry[k], bool):
                err(f"{game}.{k}: must be True or False")

        if game not in fams:
            unreachable.append(game)

        # roles ------------------------------------------------------------
        roles = entry.get("roles") or {}
        if not isinstance(roles, dict):
            err(f"{game}.roles: must be a dict of role name -> questions")
            roles = {}
        real = set()
        for path in fams.get(game, []):
            try:
                g = annotation.load_game(path)
            except Exception as exc:                       # unreadable transcript
                warn(f"{game}: could not read {os.path.basename(path)} ({exc})")
                continue
            real |= {g.role(s) for s in g.ai_ids}
        for role, cfg in roles.items():
            where = f"{game}.roles[{role!r}]"
            if not isinstance(cfg, dict):
                err(f"{where}: must be a dict")
                continue
            for k in sorted(set(cfg) - _ROLE_KEYS):
                err(f"{where}: unknown key {k!r} — expected one of "
                    f"{', '.join(sorted(_ROLE_KEYS))}")
            # The quiet killer: a role name that no transcript uses means the
            # whole entry is skipped and the game silently falls back to the
            # Shared Core, with nothing on screen to say so.
            if real and role not in real:
                err(f"{where}: no turn in this game has that role — "
                    f"transcripts use {sorted(real)}")
            _check_slot(f"{where}.q1", cfg.get("q1", "generic"), err, warn)
            _check_slot(f"{where}.q2", cfg.get("q2", "generic"), err, warn)
            keys_seen = set()
            _check_bolt_ons(f"{where}.bolt_ons", cfg.get("bolt_ons"),
                            keys_seen, err, warn)
            _check_bolt_ons(f"{where}.bolt_ons_first_turn",
                            cfg.get("bolt_ons_first_turn"), keys_seen, err, warn)

        # flags --------------------------------------------------------------
        flags = entry.get("flags")
        if flags is not None:
            if not isinstance(flags, list):
                err(f"{game}.flags: must be None (the Shared Core ticks), "
                    f"[] (no ticks), or a list of strings")
            else:
                if any(not isinstance(f, str) or not f.strip() for f in flags):
                    err(f"{game}.flags: every tick must be a non-empty string")
                if len(set(flags)) != len(flags):
                    err(f"{game}.flags: duplicate ticks")

        # whole game ---------------------------------------------------------
        wg = entry.get("whole_game")
        if wg is not None:
            if not isinstance(wg, list):
                err(f"{game}.whole_game: must be a list")
            else:
                ids = []
                for i, e in enumerate(wg):
                    where = f"{game}.whole_game[{i}]"
                    if not (isinstance(e, (list, tuple)) and len(e) in (2, 3)):
                        err(f"{where}: must be (id, label_md, choices)")
                        continue
                    if len(e) == 2:
                        # _wg_id digests the text, so a reworded question
                        # silently becomes a different question to the export.
                        warn(f"{where}: no explicit id — add one so the answer "
                             f"keeps its meaning if the wording changes")
                        md, choices = e
                        ids.append(annotation._wg_id(md))
                    else:
                        qid, md, choices = e
                        if not isinstance(qid, str) or not qid.isidentifier():
                            err(f"{where}: id {qid!r} must be a valid identifier")
                        ids.append(qid)
                    _check_label(where, md, err, warn)
                    _check_choices(where, choices, err, warn)
                if len(set(ids)) != len(ids):
                    err(f"{game}.whole_game: duplicate ids {ids} — answers are "
                        f"stored by id, so one would overwrite the other")
        if entry.get("whole_game_only") and not wg:
            err(f"{game}: whole_game_only hides the generic verdict pair, but "
                f"there are no whole_game questions to replace them with")

    if unreachable:
        warn(f"{len(unreachable)} entr{'y has' if len(unreachable) == 1 else 'ies have'} "
             f"no transcripts under GAMES_DIR="
             f"{os.environ.get('GAMES_DIR', 'games')} and cannot be reached "
             f"from here: {', '.join(unreachable)}")

    # The strongest check available: build the spec for every real transcript
    # and hold it against what the render loop would wire up.
    checked = 0
    for game, paths in sorted(fams.items()):
        for path in paths:
            try:
                g = annotation.load_game(path)
                show_q3 = annotation.show_q3_for(g, "hybrid")
                spec = annotation.question_spec(g, "hybrid", show_q3)
                annotation._assert_spec_matches_render(
                    spec, _would_render(g, show_q3), g)
                checked += 1
            except Exception as exc:
                err(f"{game}: {os.path.basename(os.path.dirname(path))} — {exc}")
    if verbose:
        print(f"  {checked} transcripts rendered and cross-checked")
    return errors, warnings


def _would_render(g, show_q3):
    """The (turn, field) list the annotation screen would wire up for this
    transcript — the same walk the render loop does, so _assert_spec_matches_render
    can be run without a browser."""
    import annotation
    bespoke = BESPOKE_QUESTIONS.get(g.game_key) or {}
    first_turn = annotation.first_turn_of_role(g)
    out = []
    for i in range(g.n_turns):
        role = g.role(g.ai_turns[i]["from"])
        cfg = (bespoke.get("roles") or {}).get(role, {})
        if cfg.get("q1", "generic") is not None:
            out.append((i, "q1"))
        if cfg.get("q2", "generic") is not None:
            out.append((i, "q2"))
        bolts = list(cfg.get("bolt_ons") or [])
        if first_turn.get(role) == i:
            bolts += list(cfg.get("bolt_ons_first_turn") or [])
        for key, _md, _ch in bolts:
            out.append((i, "extra", key))
        out.append((i, "q3"))
    return out


def _one_line(md):
    import annotation
    return annotation.plain_label(md)


def _fmt_choices(choices):
    return " | ".join(c[0].replace("\n", " ") for c in choices)


def show(game, transcript=None):
    """Print, turn by turn, what an annotator is shown for one game."""
    import annotation
    paths = _families().get(game)
    if not paths:
        known = ", ".join(sorted(_families()))
        print(f"No transcripts for {game!r}.\nFamilies found: {known}")
        return 1
    path = transcript or sorted(paths)[0]
    g = annotation.load_game(path)
    entry = BESPOKE_QUESTIONS.get(game)
    show_q3 = annotation.show_q3_for(g, "hybrid")
    first_turn = annotation.first_turn_of_role(g)

    print(f"{game}  —  {annotation.game_slug(path)}")
    print(f"{'Shared Core only (no entry in BESPOKE_QUESTIONS)' if not entry else 'bespoke entry'}"
          f", {g.n_turns} rateable turn(s), {len(paths)} transcript(s) in this family")
    print(f"(showing one transcript; other transcripts of {game} can differ in "
          f"turn count and roles)\n")

    bespoke = entry or {}
    for i in range(g.n_turns):
        role = g.role(g.ai_turns[i]["from"])
        cfg = (bespoke.get("roles") or {}).get(role, {})
        print(f"── Turn {i + 1}   [{role}]")
        for slot, generic in (("q1", GENERIC_Q1), ("q2", GENERIC_Q2)):
            c = cfg.get(slot, "generic")
            if c is None:
                print(f"   {slot.upper()}  (not asked of this role)")
                continue
            md, choices = generic if c == "generic" else c
            tag = "generic" if c == "generic" else "bespoke"
            # plain_label already starts with the slot name on most questions
            # ("Q1 — Prior Information Use — …"), so don't print it twice.
            text = _one_line(md)
            if text.upper().startswith(slot.upper()):
                text = text[len(slot):].lstrip(" —-")
            print(f"   {slot.upper()}  {text}   [{tag}]")
            print(f"        {_fmt_choices(choices)}")
        bolts = list(cfg.get("bolt_ons") or [])
        if first_turn.get(role) == i:
            bolts += list(cfg.get("bolt_ons_first_turn") or [])
        for key, md, choices in bolts:
            print(f"   +    {_one_line(md)}   [extra:{key}]")
            print(f"        {_fmt_choices(choices)}")
        if show_q3:
            q3 = _one_line(GENERIC_Q3[0])
            print(f"   Q3  {q3[len('Q3'):].lstrip(' —-')}   [generic]")
            print(f"        {_fmt_choices(GENERIC_Q3[1])}")
        else:
            print("   Q3  (hidden — stored as N/A)")
        _bf = bespoke.get("flags")
        ticks = _bf if isinstance(_bf, list) else g.flag_choices
        if ticks:
            print("   Ticks:")
            for t in ticks:
                print(f"        [ ] {t}")
        else:
            print("   Ticks: (none for this game)")
        print("        optional turn comment")
        print()

    print("── End of game (verdict screen)")
    wg = annotation.whole_game_questions(path, "hybrid")
    if annotation.whole_game_only(path, "hybrid"):
        print("   G1/G2 hidden (whole_game_only)")
    else:
        print(f"   G1  Strategic coherence   "
              f"{' | '.join(n for _v, n, _d in _verdict().COHERENCE)}")
        print(f"   G2  Overall game quality  "
              f"slider 1–{len(_verdict().OVERALL_RATINGS)}")
    for qid, md, choices in wg:
        print(f"   G3  {_one_line(md)}   [{qid}]")
        print(f"        {_fmt_choices(choices)}")
    if not wg:
        print("   G3  (none — this game adds no whole-game question)")
    print("        optional overall comment")
    return 0


def _verdict():
    """annotation_verdict holds the two generic end-of-game scales; imported
    here only so --show can print the full verdict screen."""
    import annotation_verdict
    return annotation_verdict


def _md_choices(choices):
    """Choices as a readable one-liner: "1 None · 2 Partial · …"."""
    out = []
    for display, _value in choices:
        head, _, tail = display.partition("\n")
        out.append(f"**{head}** {tail}".strip() if tail else f"**{head}**")
    return " · ".join(out)


def _md_question(md, choices, indent=""):
    title, _, prompt = (md or "").partition("\n\n")
    title = title.replace("**", "").strip()
    lines = [f"{indent}- **{title}**"]
    if prompt:
        lines.append(f"{indent}  {prompt.strip()}")
    lines.append(f"{indent}  {_md_choices(choices)}")
    return "\n".join(lines)


def markdown():
    """The current question set as a document, built from this file.

    Written out rather than maintained by hand: the practice screen spent
    weeks telling participants it was a different game with a different turn
    count, because two strings were updated in one place and not the other.
    """
    import annotation
    fams = _families()
    out = [
        "# Annotation Question Set",
        "",
        "**Generated — do not edit by hand.** Edit `questions.py` and run:",
        "",
        "```bash",
        "python questions.py --markdown > question_set.md",
        "```",
        "",
        f"Covers the {len(fams)} game families with transcripts under "
        f"`GAMES_DIR={os.environ.get('GAMES_DIR', 'games')}`. Anything a game "
        "does not override falls back to the Shared Core below.",
        "",
        "---",
        "",
        "## Shared Core",
        "",
        "Asked on every AI turn unless the game replaces them.",
        "",
        _md_question(*GENERIC_Q1),
        _md_question(*GENERIC_Q2),
        "",
        "Conditional pair — shown only where the game asks the model to "
        "explain its move:",
        "",
        _md_question(*GENERIC_Q3),
        "",
        "Ticks (optional, tick all that apply):",
        "",
    ]
    out += [f"- {t}" for t in SC_TICKS]
    out += [f"- {SC_TICK_D} *(with Q3 only)*", "", "---", "",
            "## End of every game", ""]
    v = _verdict()
    out.append("- **G1 — Strategic coherence** — "
               + " · ".join(f"**{n}** {name}" for n, name, _d in v.COHERENCE))
    lo, hi = v.OVERALL_RATINGS[0], v.OVERALL_RATINGS[-1]
    out.append(f"- **G2 — Overall game quality** — slider "
               f"1 ({lo[1]}) to {len(v.OVERALL_RATINGS)} ({hi[1]})")
    out.append("- **G3 — This game specifically** — where the game defines "
               "one; listed per game below.")
    out += ["", "---", "", "## Per game", ""]

    for fam in sorted(fams):
        entry = BESPOKE_QUESTIONS.get(fam)
        n = len(fams[fam])
        out.append(f"### {fam}")
        out.append("")
        out.append(f"*{n} transcript(s).*"
                   + ("" if entry else " Shared Core only — no overrides."))
        out.append("")
        if not entry:
            out.append("")
            continue

        roles = entry.get("roles") or {}
        for role in sorted(roles):
            cfg = roles[role] or {}
            out.append(f"**Role: {role}**")
            out.append("")
            asked = False
            for slot, generic in (("q1", GENERIC_Q1), ("q2", GENERIC_Q2)):
                c = cfg.get(slot, "generic")
                if c is None:
                    continue
                md, choices = generic if c == "generic" else c
                tag = " *(Shared Core)*" if c == "generic" else ""
                out.append(_md_question(md, choices) + tag)
                asked = True
            for key, md, choices in (cfg.get("bolt_ons") or []):
                out.append(_md_question(md, choices) + f" *(stored as `{key}`)*")
                asked = True
            for key, md, choices in (cfg.get("bolt_ons_first_turn") or []):
                out.append(_md_question(md, choices)
                           + f" *(first turn of this role only; `{key}`)*")
                asked = True
            if not asked:
                out.append("- *Not rated — shown for context only.*")
            out.append("")

        out.append("**Q3 — Reasoning Clarity:** "
                   + ("shown" if entry.get("reasoning_clarity") else "not shown"))
        _bf = entry.get("flags")
        out.append("")
        if not isinstance(_bf, list):
            out.append("**Ticks:** the Shared Core set")
        elif not _bf:
            out.append("**Ticks:** none")
        else:
            out.append("**Ticks:**")
            out.append("")
            out += [f"- {t}" for t in _bf]
        out.append("")
        wg = annotation.normalise_whole_game(entry.get("whole_game"))
        if wg:
            out.append("**G3 — this game's whole-game question(s):**")
            out.append("")
            for qid, md, choices in wg:
                out.append(_md_question(md, choices) + f" *(stored as `{qid}`)*")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(
        description="Inspect and validate the study's annotation questions.")
    p.add_argument("--check", action="store_true",
                   help="validate every entry; exits non-zero on any error")
    p.add_argument("--show", metavar="GAME",
                   help="print what an annotator sees for one game family")
    p.add_argument("--list", action="store_true",
                   help="list every game family and whether it has an entry")
    p.add_argument("--markdown", action="store_true",
                   help="print the whole question set as markdown (for question_set.md)")
    args = p.parse_args(argv)

    if args.list:
        fams = _families()
        for fam in sorted(set(fams) | set(BESPOKE_QUESTIONS)):
            n = len(fams.get(fam, []))
            entry = BESPOKE_QUESTIONS.get(fam)
            what = "Shared Core" if not entry else ", ".join(
                k for k in ("roles", "flags", "whole_game") if entry.get(k) is not None)
            print(f"  {fam:32} {n:4} transcript(s)   {what}")
        return 0

    if args.markdown:
        print(markdown(), end="")
        return 0

    if args.show:
        return show(args.show)

    if args.check:
        errors, warnings = check(verbose=True)
        for w in warnings:
            print(f"  warning: {w}")
        for e in errors:
            print(f"  ERROR:   {e}")
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
        return 1 if errors else 0

    p.print_help()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
