import gradio as gr
import ast
import functools
import glob
import hashlib
import html
import json
import os
import re

import db

# Games are found automatically from games/ — nothing here is hardcoded.

_dir = os.path.dirname(os.path.abspath(__file__))
# GAMES_DIR lets the study run off a curated tree (games_study/, built by
# build_study_set.py) without disturbing the original games/ pool. Discovery,
# game_slug and game_key are all depth-agnostic, so the extra model level in
# the study tree needs no other change: game_key takes the 4th-from-last part,
# which is still the game family.
_games_dir = os.path.join(_dir, os.environ.get("GAMES_DIR", "games"))


def _discover_games():
    """Return [(label, path), …] for every transcript under games/, sorted."""
    games = []
    pattern = os.path.join(_games_dir, "**", "interactions.json")
    for path in glob.glob(pattern, recursive=True):
        parts = os.path.relpath(path, _games_dir).split(os.sep)
        # parts = [game, variant, instance, "interactions.json"]
        label = " · ".join(parts[:-1])
        games.append((label, path))
    games.sort(key=lambda t: t[0].lower())
    return games


GAMES = _discover_games()

# Default to the original hardcoded game when present, else the first found.
_DEFAULT = os.path.join(
    _games_dir, "hot_air_balloon",
    "air_balloon_survival_en_complexity_easy", "instance_00000",
    "interactions.json",
)
DEFAULT_GAME = _DEFAULT if any(p == _DEFAULT for _, p in GAMES) else (
    GAMES[0][1] if GAMES else _DEFAULT
)


def game_slug(game_path):
    """Stable, filesystem-free identifier for a game transcript (unique across
    the whole tree — used for output filenames and URL params)."""
    rel = os.path.relpath(game_path, _games_dir)
    return rel.replace(os.sep, "__").replace("interactions.json", "").strip("_")


def game_key(game_path):
    """Game-family name (taboo, mmlu_pro, …) used to look up BESPOKE_QUESTIONS —
    the 4th-from-last path part, since that's always the family regardless of
    how many folders sit above it."""
    parts = os.path.relpath(game_path, _games_dir).split(os.sep)
    return parts[-4] if len(parts) >= 4 else parts[0]


_SLUG_TO_PATH = {game_slug(path): path for _, path in GAMES}


def slug_to_path(slug):
    """Resolve a `game` URL param to its transcript path, or None if unknown."""
    return _SLUG_TO_PATH.get(slug)


# Maps a block/condition value to its question-set MODE. This is a decode
# table, not an allow-list: the day1_*/day2_* entries are retired internal-pilot
# condition names, kept solely so an export can still resolve rows collected
# under them. Dropping them would not be inert — BLOCK_TO_TYPE is read via
# .get(condition, "universal"), so a removed "day1_hybrid" would silently start
# decoding as universal and re-label data that was collected as hybrid.
BLOCK_TO_TYPE = {
    "day1_universal": "universal",
    "day1_hybrid": "hybrid",
    "day2_mixed": "hybrid",
    "universal": "universal",
    "hybrid": "hybrid",
}

# What a session URL is allowed to ask for, which is a strictly smaller set.
# app.py validates ?block= against this. The retired names above are
# decode-only: accepting "day1_universal" from a hand-built link would route a
# study transcript to the generic question set and store it under a condition
# assignment.py never issues (it only ever assigns CONDITION = "hybrid").
VALID_BLOCKS = {"universal", "hybrid"}


def _scale4(labels):
    """4-choice 1..4 scale radio, e.g. _scale4(["None","Partial","Good","Excellent"])."""
    return [(f"{i + 1}\n{lbl}", str(i + 1)) for i, lbl in enumerate(labels)]


def _scaleN(n, ends=None):
    """Plain-number 1..n scale radio; `ends={1: 'lo', n: 'hi'}` adds end anchors.
    Used for the bespoke whole-game "specific overall" questions (1-7 / 1-4)."""
    ends = ends or {}
    return [((f"{i}\n{ends[i]}" if i in ends else str(i)), str(i))
            for i in range(1, n + 1)]


def plain_label(md):
    """Markdown question text -> a flat string for a control's accessible name.

    Every question in the codebase is "**Title**\\n\\nBody", so one pass covers
    all of them and the name can never drift from the visible text. Used for
    label= on controls that also render the markdown visibly; the label is
    sr-only (show_label=False), so this changes nothing on screen.
    """
    s = re.sub(r"[*_`#]", "", md or "")
    s = s.replace("\n\n", " — ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()


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
                _scaleN(7),
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
                _scaleN(7),
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
                _scaleN(7),
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
                _scaleN(7),
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


def _wg_id(md):
    """Fallback identifier for a whole-game question that has no explicit one:
    a short digest of its text. Stable under REORDERING (which is the failure
    that matters), though not under rewording — hence the explicit ids on the
    study games below."""
    flat = re.sub(r"\s+", " ", re.sub(r"[*_`#]", "", md or "")).strip().lower()
    return "q" + hashlib.sha256(flat.encode()).hexdigest()[:10]


def normalise_whole_game(entries):
    """Accept both (question, choices) and (id, question, choices) and always
    return the 3-tuple form.

    Answers used to be stored positionally ({"0": "3"}), so reordering a game's
    whole-game list silently transposed every stored answer for it — imagegame
    has two questions across 52 transcripts, and the export's only guard was a
    count check that a swap passes. Keying by id removes the failure entirely.
    """
    out = []
    for e in entries or []:
        if len(e) == 3:
            qid, md, choices = e
        else:
            md, choices = e
            qid = _wg_id(md)
        out.append((qid, md, choices))
    return out


def whole_game_questions(game_path, block):
    """Bespoke whole-game questions as [(question_id, question_markdown, choices), …],
    or [] when there are none. Hybrid mode only, like the per-turn bespoke set."""
    if BLOCK_TO_TYPE.get(block, "universal") != "hybrid":
        return []
    return normalise_whole_game(
        BESPOKE_QUESTIONS.get(game_key(game_path), {}).get("whole_game"))


def whole_game_only(game_path, block):
    """True when the verdict should hide the generic Coherence + Overall pair
    and show only this game's bespoke whole-game questions."""
    if BLOCK_TO_TYPE.get(block, "universal") != "hybrid":
        return False
    return bool(BESPOKE_QUESTIONS.get(game_key(game_path), {}).get("whole_game_only"))


class _Game:
    """Lightweight container for everything a screen needs about one game."""


def _detect_outcome(turns, data=None):
    """One game outcome — "won"/"lost"/"aborted"/"ended" — read from each
    family's own end-of-game signals, not by guessing from substring matches."""
    # Top-level Success/Lose/Aborted flags are the authoritative verdict when
    # present; games without them fall through to the per-event scan below.
    if isinstance(data, dict):
        if str(data.get("Aborted", "")).strip() in ("1", "True"):
            return "aborted"
        if str(data.get("Success", "")).strip() in ("1", "True"):
            return "won"
        if str(data.get("Lose", "")).strip() in ("1", "True"):
            return "lost"

    acts = [m["action"] for turn in turns for m in turn]

    def first(t):
        return next((a for a in acts if a.get("type") == t), None)

    # Explicit success flag (clean_up, and any family that logs one).
    a = first("success")
    if a and isinstance(a.get("content"), str):
        return "won" if a["content"].strip().lower() == "true" else "lost"
    # clean_up's stats block: '* success: True / * lose: False / * aborted: …'.
    a = first("game_finished")
    if a and isinstance(a.get("content"), str):
        c = a["content"].lower()
        if re.search(r"aborted:\s*true", c):
            return "aborted"
        m = re.search(r"success:\s*(true|false)", c)
        if m:
            return "won" if m.group(1) == "true" else "lost"
    # adventuregame's result dict.
    a = first("game_result")
    if a and isinstance(a.get("content"), dict):
        v = a["content"].get("game_successfully_finished")
        if isinstance(v, bool):
            return "won" if v else "lost"
    # dond: a logged agreement means the deal went through.
    if first("successful agreement"):
        return "won"
    # wordle family: 'correct guess' carries an explicit 'game_result = WIN/LOSS'.
    for a in acts:
        if a.get("type") == "correct guess" and isinstance(a.get("content"), str):
            m = re.search(r"game_result\s*=\s*(win|loss)", a["content"], re.I)
            if m:
                return "won" if m.group(1).lower() == "win" else "lost"
    # codenames: 'game end' prose names the winner — 'opponent has won' is a
    # LOSS for the evaluated team, so the opponent check must come first.
    a = first("game end")
    if a and isinstance(a.get("content"), str):
        c = a["content"].lower()
        if "abort" in c:
            return "aborted"
        if "opponent" in c:
            return "lost"
        if "won" in c or "win" in c:
            return "won"
    # hot_air_balloon: 'info' events ('game successful', 'end game').
    for a in acts:
        if a.get("type") == "info" and isinstance(a.get("content"), str):
            c = a["content"].strip().lower()
            if "unsuccessful" in c or "fail" in c:
                return "lost"
            if c == "game successful":
                return "won"
    # matchit: win only if BOTH players' decisions were right.
    dec = [a.get("content") for a in acts
           if str(a.get("type", "")).startswith("Decision Player")]
    if dec:
        return ("won" if all(str(d).strip().lower() == "success" for d in dec)
                else "lost")
    # referencegame: the guesser's answer was checked against the target.
    if first("parse_correct"):
        return "won"
    if first("parse_wrong") or first("parse_incorrect"):
        return "lost"
    # taboo/guesswhat: a 'correct guess' event is only ever logged when the
    # guess actually matched — a loss runs out of turns without one.
    if first("correct guess"):
        return "won"
    # Aborted episodes: an explicit abort (textmapworld's 'abort game'), or the
    # transcript cutting off on an unparseable reply (imagegame).
    if first("aborted"):
        return "aborted"
    if acts and acts[-1].get("type") == "invalid format":
        return "aborted"
    return "ended"


@functools.lru_cache(maxsize=None)
def load_game(path):
    with open(path) as f:
        data = json.load(f)

    meta = data["meta"]
    players = data["players"]
    turns = data["turns"]

    # Excludes the GM and programmatic/scripted bots, not just non-AI players.
    def _is_ai_player(pid):
        info = players.get(pid, {})
        if pid == "GM":
            return False
        model = (info.get("model_name") or "").lower()
        return model != "programmatic" and model != ""

    ai_ids = {pid for pid in players if _is_ai_player(pid)}

    def _role(pid):
        return players.get(pid, {}).get("game_role", pid)

    # Some games (e.g. Codenames) put a non-string metadata dict as the first
    # message, so we cannot assume turns[0][0] is the rules text. Find the first
    # GM message with string content that looks like an instruction prompt.
    rules = None
    for turn in turns:
        for msg in turn:
            if msg["from"] == "GM":
                c = msg["action"].get("content")
                if isinstance(c, str) and len(c) > 80:
                    rules = c
                    break
        if rules:
            break
    if not rules:
        for turn in turns:
            for msg in turn:
                c = msg["action"].get("content")
                if isinstance(c, str) and c.strip():
                    rules = c
                    break
            if rules:
                break
    rules = rules or "(no rules text found)"

    # When a format error forces a retry, clembench logs both the rejected and
    # accepted response as label=="response" — keep the fuller original, drop the retry.
    def _retry_dupe_ids():
        flat = [m for turn in turns for m in turn]
        resp = [i for i, m in enumerate(flat)
                if m["from"] in ai_ids and m["action"].get("label") == "response"]
        drop = set()
        for k in range(1, len(resp)):
            prev, cur = flat[resp[k - 1]], flat[resp[k]]
            if prev["from"] != cur["from"]:
                continue
            window = flat[resp[k - 1] + 1:resp[k]]
            if any(w["from"] == "GM" and w["action"].get("type") == "parse_error"
                   for w in window):
                drop.add(id(cur))
        return drop

    skip_ids = _retry_dupe_ids()

    ai_turns = []
    for turn in turns:
        for msg in turn:
            if (msg["from"] in ai_ids and msg["action"].get("label") == "response"
                    and id(msg) not in skip_ids):
                ai_turns.append(msg)

    # Q3 (Reasoning Clarity) is only meaningful when the AI produces reasoning.
    def _detect_reasoning(ts):
        if not ts:
            return False
        markers = ("explanation:", "because", "since ", "reasoning:", "i'll ", "i will ")
        hits = 0
        for msg in ts:
            c = str(msg["action"].get("content", "")).lower()
            if len(c) > 60 and any(m in c for m in markers):
                hits += 1
        return hits >= max(1, len(ts) // 2)

    g = _Game()
    g.path = path
    g.data = data
    g.meta = meta
    g.players = players
    g.ai_ids = ai_ids
    g.role = _role
    g.rules = rules
    g.ai_turns = ai_turns
    g.n_turns = len(ai_turns)
    g.outcome = _detect_outcome(turns, data)
    # Response messages to NOT render as turn cards — kept in sync with the
    # ai_turns filter above so transcript turn_counter matches ai_turns indices.
    g.skip_msg_ids = skip_ids
    # Dond's negotiation prose rarely hits the marker-word heuristic below,
    # so its game is forced into the reasoning set by name instead.
    g.game_key = game_key(path)
    _REASONING_GAMES = {"wordle", "wordle_withclue", "wordle_withcritic", "dond"}
    g.has_reasoning = g.game_key in _REASONING_GAMES or _detect_reasoning(ai_turns)
    g.multi_role = len(ai_ids) > 1
    g.flag_choices = list(SC_TICKS)
    if g.has_reasoning:
        g.flag_choices.append(SC_TICK_D)
    g.slug = game_slug(path)
    g.source_path = os.path.relpath(path, _dir)
    return g


# Map renderer for graph-reasoning: green ring = claimed correctly, red =
# claimed wrongly, dashed blue ring = current position.

_DIR_VEC = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
_DIR_OPP = {"north": "south", "south": "north", "east": "west", "west": "east"}


def _parse_map_response(content):
    """Parse the model's `{"action": …, "graph": …}` reply, or None. Models
    write edges as Python tuples, not valid JSON, so try JSON then a Python-literal parse."""
    if not isinstance(content, str) or "graph" not in content:
        return None
    for parse in (json.loads, ast.literal_eval):
        try:
            d = parse(content.strip())
        except (ValueError, SyntaxError):
            continue
        if isinstance(d, dict) and isinstance(d.get("graph"), dict):
            return d
    return None


def _map_truth_and_layout(g):
    """Walk the full transcript once; return (snapshots, positions).

    snapshots[i] is what the model could actually know just before its i-th
    response: rooms visited, edges walked, current room. positions is one
    stable grid layout so rooms don't jump between turn cards."""
    snapshots = []
    visited, edges = set(), set()
    current = None
    moves = []          # (old, dir_or_None, new) in walk order
    resp_dir = None     # direction of the response in the current round

    for round_msgs in g.data["turns"]:
        for msg in round_msgs:
            a = msg["action"]
            if msg["from"] in g.ai_ids and a.get("label") == "response":
                d = _parse_map_response(a.get("content"))
                m = re.search(r"GO:\s*(north|south|east|west)",
                              str((d or {}).get("action", "")), re.I)
                resp_dir = m.group(1).lower() if m else None
                if current is None and d:
                    nodes = d["graph"].get("nodes") or []
                    if nodes:  # the env told the model its start room
                        current = str(nodes[0])
                        visited.add(current)
                snapshots.append({"visited": set(visited),
                                  "edges": set(edges),
                                  "current": current})
            elif msg["from"] == "GM" and a.get("type") == "move":
                try:
                    mv = json.loads(a.get("content", ""))
                except (ValueError, TypeError):
                    continue
                old, new = str(mv.get("old")), str(mv.get("new"))
                visited.update((old, new))
                if resp_dir:
                    edges.add((old, resp_dir, new))
                    edges.add((new, _DIR_OPP[resp_dir], old))
                moves.append((old, resp_dir, new))
                current = new

    pos = {}

    def _place(room, cand):
        while cand in pos.values():          # two rooms claim the same cell
            cand = (cand[0] + 0.45, cand[1] + 0.35)
        pos[room] = cand

    if moves:
        _place(moves[0][0], (0, 0))
    elif current:
        _place(current, (0, 0))
    for old, d, new in moves:
        if old not in pos:
            _place(old, (0, 0))
        if new not in pos:
            vec = _DIR_VEC.get(d, (1, 1))
            _place(new, (pos[old][0] + vec[0], pos[old][1] + vec[1]))
    return snapshots, pos


def _map_svg(claim, snap, pos_global):
    """Inline SVG of one turn's claimed map, or None if there is nothing to draw."""
    graph = claim.get("graph", {})
    nodes = [str(n) for n in (graph.get("nodes") or []) if str(n).strip()]
    if not nodes:
        return None

    claimed = []        # (a, dir, b) as the model asserted them
    for d, pairs in (graph.get("edges") or {}).items():
        d = str(d).lower()
        for pr in (pairs or []):
            if isinstance(pr, (list, tuple)) and len(pr) == 2:
                claimed.append((str(pr[0]), d, str(pr[1])))

    # Place nodes: true-walk grid first, then hallucinated rooms next to
    # whichever claimed neighbour is already placed, else parked below.
    pos = {n: pos_global[n] for n in nodes if n in pos_global}

    def _free(cand):
        while cand in pos.values():
            cand = (cand[0] + 0.45, cand[1] + 0.35)
        return cand

    for n in nodes:
        if n in pos:
            continue
        for a, d, b in claimed:
            if d not in _DIR_VEC:
                continue
            if a == n and b in pos:
                vx, vy = _DIR_VEC[_DIR_OPP[d]]
                pos[n] = _free((pos[b][0] + vx, pos[b][1] + vy))
                break
            if b == n and a in pos:
                vx, vy = _DIR_VEC[d]
                pos[n] = _free((pos[a][0] + vx, pos[a][1] + vy))
                break
        else:
            bottom = max((y for _, y in pos.values()), default=0)
            pos[n] = _free((0, bottom + 1.4))

    SX, SY, R = 92, 84, 13
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    minx, miny = min(xs), min(ys)
    px = {n: ((x - minx) * SX + 46, (y - miny) * SY + 34) for n, (x, y) in pos.items()}
    w = int(max(x for x, _ in px.values()) + 46)
    h = int(max(y for _, y in px.values()) + 44)

    parts = []
    # Edges (drawn first, deduped to one line per room pair): solid grey when
    # the walk verified the claim, dashed red when the model asserts a
    # connection its own experience never showed it.
    seen_pairs = {}
    for a, d, b in claimed:
        if a not in px or b not in px:
            continue
        key = frozenset((a, b))
        ok = (a, d, b) in snap["edges"]
        seen_pairs[key] = seen_pairs.get(key, True) and ok
    for key, ok in seen_pairs.items():
        a, b = tuple(key) if len(key) == 2 else (next(iter(key)),) * 2
        (x1, y1), (x2, y2) = px[a], px[b]
        style = ('stroke="#8b98ab" stroke-width="1.6"' if ok else
                 'stroke="#ef4444" stroke-width="1.6" stroke-dasharray="5 4"')
        parts.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" {style} opacity="0.85"/>')

    # Nodes: ring colour = claim correctness; dashed outer ring = current room.
    for n, (x, y) in px.items():
        colour = "#22c55e" if n in snap["visited"] else "#ef4444"
        if n == snap["current"]:
            parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{R + 6}" fill="none" '
                         f'stroke="#3b82f6" stroke-width="2" stroke-dasharray="4 4"/>')
        parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{R}" fill="#0e1a30" '
                     f'stroke="{colour}" stroke-width="2.5"/>')
        parts.append(f'<text x="{x:.0f}" y="{y + R + 15:.0f}" text-anchor="middle" '
                     f'font-size="10.5" font-weight="600" fill="#dbe4f0">{html.escape(n)}</text>')

    return (f'<svg class="map-svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="Map claimed by the AI this turn">{"".join(parts)}</svg>')


_MAP_LEGEND_HTML = (
    '<div class="map-legend">'
    '<span><svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" '
    'fill="none" stroke="#22c55e" stroke-width="2.5"/></svg> claimed correctly</span>'
    '<span><svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" '
    'fill="none" stroke="#ef4444" stroke-width="2.5"/></svg> claimed wrongly</span>'
    '<span><svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="5.5" '
    'fill="none" stroke="#3b82f6" stroke-width="1.8" stroke-dasharray="3 2.4"/></svg> current position</span>'
    '<span><svg width="20" height="14" viewBox="0 0 20 14"><line x1="1" y1="7" x2="19" y2="7" '
    'stroke="#ef4444" stroke-width="1.8" stroke-dasharray="4 3"/></svg> unverified connection</span>'
    '</div>'
)


# Path renderer for specific-room: the model never self-reports a map here
# (just "GO: <dir>"/"DONE"), so this draws the explored path with no
# correctness rings, plus the target room once its position is known.

# The rules text opens with a worked example before the real episode, so we
# anchor on "Let us start." to avoid matching the example's target room.
_SPECIFICROOM_START_RE = re.compile(
    r"Let us start\.\s*The target room is ([^.]+?)\.\s*You are in (?:the |a )?([^.]+?)\."
)


def _path_truth_and_layout(g):
    """Like _map_truth_and_layout, but the start/target room names come from
    the opening rules text since there's no self-reported map to read them from."""
    m = _SPECIFICROOM_START_RE.search(g.rules or "")
    target_room = m.group(1).strip() if m else None
    start_room = m.group(2).strip() if m else None

    snapshots = []
    visited = {start_room} if start_room else set()
    edges = set()
    current = start_room
    moves = []
    resp_dir = None

    for round_msgs in g.data["turns"]:
        for msg in round_msgs:
            a = msg["action"]
            if msg["from"] in g.ai_ids and a.get("label") == "response":
                m2 = re.search(r"GO:\s*(north|south|east|west)",
                                str(a.get("content", "")), re.I)
                resp_dir = m2.group(1).lower() if m2 else None
                snapshots.append({"visited": set(visited), "edges": set(edges),
                                  "current": current})
            elif msg["from"] == "GM" and a.get("type") == "move":
                try:
                    mv = json.loads(a.get("content", ""))
                except (ValueError, TypeError):
                    continue
                old, new = str(mv.get("old")), str(mv.get("new"))
                visited.update((old, new))
                if resp_dir:
                    edges.add((old, resp_dir, new))
                    edges.add((new, _DIR_OPP[resp_dir], old))
                moves.append((old, resp_dir, new))
                current = new

    pos = {}

    def _place(room, cand):
        while cand in pos.values():
            cand = (cand[0] + 0.45, cand[1] + 0.35)
        pos[room] = cand

    if moves:
        _place(moves[0][0], (0, 0))
    elif start_room:
        _place(start_room, (0, 0))
    for old, d, new in moves:
        if old not in pos:
            _place(old, (0, 0))
        if new not in pos:
            vec = _DIR_VEC.get(d, (1, 1))
            _place(new, (pos[old][0] + vec[0], pos[old][1] + vec[1]))
    return snapshots, pos, target_room


def _path_svg(snap, pos_global, target_room):
    """Inline SVG of the explored path so far, or None if nothing is placeable
    yet. Target room gets a gold ring once its position is known."""
    nodes = set(snap["visited"])
    if target_room and target_room in pos_global:
        nodes.add(target_room)
    nodes &= set(pos_global)
    if not nodes:
        return None

    SX, SY, R = 92, 84, 13
    xs = [pos_global[n][0] for n in nodes]
    ys = [pos_global[n][1] for n in nodes]
    minx, miny = min(xs), min(ys)
    px = {n: ((pos_global[n][0] - minx) * SX + 46, (pos_global[n][1] - miny) * SY + 34)
          for n in nodes}
    w = int(max(x for x, _ in px.values()) + 46)
    h = int(max(y for _, y in px.values()) + 44)

    parts = []
    # Edges: one solid line per walked room pair shown on this snapshot.
    seen_pairs = set()
    for a, _d, b in snap["edges"]:
        if a not in px or b not in px:
            continue
        key = frozenset((a, b))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        (x1, y1), (x2, y2) = px[a], px[b]
        parts.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                     f'stroke="#8b98ab" stroke-width="1.6" opacity="0.85"/>')

    # Nodes: gold ring = target room, dashed outer ring = current room.
    for n, (x, y) in px.items():
        colour = "#f5b942" if n == target_room else "#3b82f6"
        if n == snap["current"]:
            parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{R + 6}" fill="none" '
                         f'stroke="#3b82f6" stroke-width="2" stroke-dasharray="4 4"/>')
        parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{R}" fill="#0e1a30" '
                     f'stroke="{colour}" stroke-width="2.5"/>')
        parts.append(f'<text x="{x:.0f}" y="{y + R + 15:.0f}" text-anchor="middle" '
                     f'font-size="10.5" font-weight="600" fill="#dbe4f0">{html.escape(n)}</text>')

    return (f'<svg class="map-svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="Explored path so far">{"".join(parts)}</svg>')


_PATH_LEGEND_HTML = (
    '<div class="map-legend">'
    '<span><svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" '
    'fill="none" stroke="#3b82f6" stroke-width="2.5"/></svg> visited room</span>'
    '<span><svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="5.5" '
    'fill="none" stroke="#3b82f6" stroke-width="1.8" stroke-dasharray="3 2.4"/></svg> current position</span>'
    '<span><svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" '
    'fill="none" stroke="#f5b942" stroke-width="2.5"/></svg> target room</span>'
    '</div>'
)


# HTML builders below are pure functions of a loaded game `g`.

# Glyphs that make up ASCII board art: box-drawing chars for clean_up/
# referencegame/imagegame grids, plus plain wall/floor/goal symbols for the
# newer spatial games. '-' and '.' are excluded — too common in plain prose.
_GRID_CHARS = set("╔╗╚╝║═╟╢╤╧┼┤├┬┴┌┐└┘─│◌▢□" "#+|_√▓█○●")
_GRID_OBJ_RE = re.compile(r"(?<![A-Za-z])([A-Z])(?![A-Za-z])")
# A lone digit is a grid cell too, but a multi-digit number ("1804") stays prose.
_GRID_DIGIT_RE = re.compile(r"(?<!\w)\d(?!\w)")

# Matched after html.escape (hence &lt;/&gt;), so only this exact token shape
# turns into markup — everything else stays plain escaped text.
_WD_TILE_RE = re.compile(r"([A-Za-z])&lt;(red|green|yellow|purple|black)&gt;")

# What each feedback colour MEANS, per game family. The source text spells this
# out ("...correct (green), ...wrong position (yellow), ...incorrect (red)"),
# and the crazy variants deliberately scramble it — the same colour name means
# different things in the two families:
#
#     standard wordle*      green=correct   yellow=wrong-pos  red=absent
#     wordle-crazy*         yellow=correct  black=wrong-pos   purple=absent
#
# So this can never be a single global map. Rendering a crazy transcript with
# standard-Wordle meanings would tell the annotator the model got it right when
# it got it backwards — and judging whether the model coped with the scramble
# is the whole point of the variant.
_WD_CORRECT = "correct position"
_WD_WRONG_POS = "wrong position"
_WD_ABSENT = "not in word"
_WORDLE_LEGENDS = {
    "wordle": {"green": _WD_CORRECT, "yellow": _WD_WRONG_POS, "red": _WD_ABSENT},
    "wordle-crazy": {"yellow": _WD_CORRECT, "black": _WD_WRONG_POS, "purple": _WD_ABSENT},
}


def wordle_legend(family):
    """Colour→meaning map for a game family, or {} if it isn't a wordle game.
    Longest prefix wins so wordle-crazy_withclue doesn't match plain "wordle"."""
    if not family:
        return {}
    for key in sorted(_WORDLE_LEGENDS, key=len, reverse=True):
        if family.startswith(key):
            return _WORDLE_LEGENDS[key]
    return {}


def _wd_tile_sub(legend):
    """Replace a `a&lt;yellow&gt;` token with a tile that still carries its
    meaning as text. The colour word is IN the source data; the old renderer
    deleted it and left background-colour as the only carrier, which loses the
    feedback entirely for anyone not seeing colour."""
    def repl(m):
        letter, colour = m.group(1), m.group(2)
        meaning = legend.get(colour)
        label = f"{colour}: {meaning}" if meaning else colour
        return (f'<span class="wd-tile wd-{colour}" title="{label}">{letter}'
                f'<span class="a11y-sr-only"> ({label}) </span></span>')
    return repl


def wordle_legend_html(family):
    """Legend strip shown once above a wordle transcript. Required by the study
    spec for the crazy variants precisely because their colours are NOT the
    standard ones."""
    legend = wordle_legend(family)
    if not legend:
        return ""
    chips = "".join(
        f'<span class="wd-legend-item"><span class="wd-tile wd-{c}">■</span>'
        f'{html.escape(c)} = {html.escape(meaning)}</span>'
        for c, meaning in legend.items()
    )
    return (f'<div class="wd-legend"><span class="wd-legend-lbl">'
            f'FEEDBACK COLOURS</span>{chips}</div>')

# The single end-of-transcript outcome banner (see _detect_outcome).
_OUTCOME_BANNERS = {
    "won":     '<div class="game-win-msg">🏆 Game Won!</div>',
    "lost":    '<div class="game-loss-msg">❌ Game Lost</div>',
    "aborted": '<div class="game-loss-msg">⚠️ Game aborted</div>',
    "ended":   '<div class="game-end-msg">🏁 Game Ended</div>',
}


def _is_grid_line(ln):
    """A real board row is mostly grid cells; prose that just mentions a glyph
    is not. Capital-letter game objects (R, X, C, L…) count as cells too, since
    imagegame's grid sheds ▢'s and gains letters as it fills in."""
    n = (sum(1 for ch in ln if ch in _GRID_CHARS)
         + len(_GRID_OBJ_RE.findall(ln))
         + len(_GRID_DIGIT_RE.findall(ln)))
    if n < 4:
        return False
    non_space = sum(1 for ch in ln if not ch.isspace())
    return n / non_space >= 0.5


# Border/ruler lines carry too few grid cells to pass _is_grid_line, so we
# absorb any frame line next to a real grid run into the same board block.
_FRAME_CHARS = set("+-=|_" "╔╗╚╝║═╟╢╤╧┼┤├┬┴┌┐└┘─│")


def _is_frame_line(ln):
    s = ln.strip()
    if not s:
        return False
    kept = [ch for ch in s if not ch.isspace()]
    if len(kept) < 2:
        return False
    return all(ch in _FRAME_CHARS or ch.isdigit() for ch in kept)


def _grid_block(lines):
    """One aligned, monospace board block; single capital letters (the game
    objects annotators must track — C/L/P, X/R…) get a highlight span."""
    body = _GRID_OBJ_RE.sub(r'<span class="grid-obj">\1</span>',
                            html.escape("\n".join(lines)))
    return f'<pre class="ascii-grid">{body}</pre>'


def _board_mask(lines):
    """Mark which of `lines` belong to a board. Around each grid row found by
    _is_grid_line, absorb neighbouring lines that are frame/ruler lines or
    share the board's width — this reunites boards whose sparser rows would
    otherwise dip below the per-line grid threshold."""
    grid = [_is_grid_line(ln) for ln in lines]
    mask = list(grid)
    n = len(lines)
    i = 0
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        j = i
        while j < n and lines[j].strip():
            j += 1
        span = range(i, j)
        widths = [len(lines[k].rstrip()) for k in span if grid[k]]
        if widths:
            wmin, wmax = min(widths), max(widths)
            for k in span:
                if grid[k]:
                    continue
                w = len(lines[k].rstrip())
                glyphs = sum(1 for ch in lines[k]
                             if ch in _GRID_CHARS or ch in _FRAME_CHARS)
                if _is_frame_line(lines[k]) or (wmin - 2 <= w <= wmax + 2
                                                and glyphs >= 2):
                    mask[k] = True
        i = j
    return mask


def _fence_is_board(lines):
    """Whether a fenced (```) block should render as a monospace board. True if
    any line is a grid row, or if the block is mostly frame lines (Tower-of-Hanoi's
    peg diagram is too sparse to pass the per-line grid test otherwise)."""
    if any(_is_grid_line(ln) for ln in lines):
        return True
    non_blank = [ln for ln in lines if ln.strip()]
    if len(non_blank) < 2:
        return False
    return sum(1 for ln in non_blank if _is_frame_line(ln)) >= 0.6 * len(non_blank)


_EMPTY_CELLS = {"▢", "□", "◌", "_", "."}


def parse_cell_grid(text):
    """A whitespace-separated rectangular grid of single-character cells, or
    None. ImageGame's Follower replies with the whole grid each turn, so this
    turns that reply into something diffable."""
    if not isinstance(text, str):
        return None
    rows = []
    for line in text.strip().split("\n"):
        cells = line.split()
        if not cells or any(len(c) != 1 for c in cells):
            return None
        rows.append(cells)
    if len(rows) < 2 or len({len(r) for r in rows}) != 1 or len(rows[0]) < 2:
        return None
    return rows


def _grid_html(rows, changed=frozenset(), label=""):
    cells = "".join(
        '<div class="ig-row">' + "".join(
            f'<span class="ig-cell{" ig-changed" if (r, c) in changed else ""}'
            f'{" ig-empty" if ch in _EMPTY_CELLS else ""}">{html.escape(ch)}</span>'
            for c, ch in enumerate(row)
        ) + "</div>"
        for r, row in enumerate(rows)
    )
    cap = f'<div class="ig-cap">{html.escape(label)}</div>' if label else ""
    return f'<div class="ig-grid">{cap}{cells}</div>'


def grid_change_html(prev, cur):
    """Before/after pair with this turn's changed cells marked.

    ImageGame asks the annotator whether an instruction 'still makes sense
    given everything filled in so far', which means spotting what THIS turn
    altered inside an otherwise identical grid — reliable to do by eye only
    when the delta is marked.
    """
    if not cur:
        return ""
    changed = set()
    if prev and len(prev) == len(cur) and len(prev[0]) == len(cur[0]):
        changed = {(r, c) for r, row in enumerate(cur)
                   for c, ch in enumerate(row) if prev[r][c] != ch}
    else:
        prev = None   # shape changed (or first turn) — no meaningful diff

    before = _grid_html(prev, label="before") if prev else ""
    after = _grid_html(cur, changed, label="after" if prev else "grid")
    note = (f'<div class="ig-note">{len(changed)} cell'
            f'{"" if len(changed) == 1 else "s"} changed</div>') if prev else ""
    arrow = '<div class="ig-arrow" aria-hidden="true">→</div>' if before else ""
    return f'<div class="ig-pair">{before}{arrow}{after}</div>{note}'


def _rich_content_html(text, family=None):
    """html.escape + proper rendering for board art. The transcript's
    proportional font breaks grid alignment, so board-shaped fenced blocks
    and unfenced board runs get re-emitted as a monospace .ascii-grid <pre>;
    ordinary prose stays as normal wrapping text.

    `family` selects the wordle colour legend; without it tiles still render
    but carry only the colour name, never a meaning that might be wrong.
    """
    _sub = _wd_tile_sub(wordle_legend(family))

    def esc(lines):
        # Escape first, then swap wordle tokens for tiles, so every other '<' stays inert.
        return _WD_TILE_RE.sub(_sub, html.escape("\n".join(lines)))

    # Partition into alternating unfenced / fenced segments so each is handled
    # by its own rule. An unterminated fence closes at end-of-text.
    segments, cur, in_fence = [], [], False
    for ln in str(text).split("\n"):
        if ln.strip().startswith("```"):
            segments.append(("fence" if in_fence else "open", cur))
            cur, in_fence = [], not in_fence
            continue
        cur.append(ln)
    segments.append(("fence" if in_fence else "open", cur))

    parts = []
    for kind, seg in segments:
        if not seg:
            continue
        if kind == "fence":
            parts.append(_grid_block(seg) if _fence_is_board(seg) else esc(seg))
            continue
        # Unfenced: split into board / non-board runs and render each accordingly.
        mask = _board_mask(seg)
        run, run_board = [], False
        for ln, is_b in zip(seg, mask):
            if run and is_b != run_board:
                parts.append(_grid_block(run) if run_board else esc(run))
                run = []
            run_board = is_b
            run.append(ln)
        if run:
            parts.append(_grid_block(run) if run_board else esc(run))
    return "".join(parts)


# Some responses run to 10-15k chars; clamp to a readable head + expandable tail.
_CLAMP_CHARS = 1200


def _looks_looped(text):
    """Flags a degenerate repetition loop: the response's tail reuses only a
    tiny vocabulary. Display cue only — doesn't affect what's rated."""
    words = str(text).split()
    if len(words) < 60:
        return False
    tail = words[-200:]
    return len(set(tail)) <= max(6, len(tail) * 0.08)


def _render_response_body(content, family=None):
    """Turn-card body for a normal (non-map) response: rich content, clamped to
    a head + expandable tail when very long, with a loop badge when it degenerated."""
    raw = str(content)
    badge = ('<div class="turn-loop-badge">⟳ Repetition loop detected</div>'
             if _looks_looped(raw) else "")
    if len(raw) <= _CLAMP_CHARS:
        return badge + _rich_content_html(raw, family)
    # Split on a line boundary near the clamp so a board/fence isn't cut mid-row.
    cut = raw.rfind("\n", 0, _CLAMP_CHARS)
    if cut < _CLAMP_CHARS // 2:
        cut = _CLAMP_CHARS
    head, tail = raw[:cut], raw[cut:]
    return (
        badge
        + _rich_content_html(head, family)
        + '<details class="turn-longclamp"><summary>▾ Show full response '
        + f'({len(tail):,} more characters)</summary>'
        + _rich_content_html(tail, family)
        + '</details>'
    )


def _fmt_reference(c):
    """Human-readable gold answer. ifeval logs a dict of instruction ids
    ({"change_case:english_lowercase": {}}) → show the id(s); everything else is
    a string (a letter, "yes", or eqbench's multi-line emotion scores)."""
    if isinstance(c, dict):
        return html.escape(", ".join(str(k) for k in c) or json.dumps(c))
    return html.escape(str(c))


def _reference_answer(g):
    """Gold answer for the single-turn QA benchmarks. Gated on a one-turn game
    so a multi-turn game mentioning "target" mid-play never surfaces one."""
    if getattr(g, "n_turns", 0) != 1:
        return None
    for turn in g.data["turns"]:
        for m in turn:
            if m["from"] != "GM":
                continue
            a = m["action"]
            t, c = a.get("type"), a.get("content")
            if t == "target":
                return _fmt_reference(c)
            if (t == "metadata" and isinstance(c, str)
                    and c.strip().lower().startswith("target:")):
                return html.escape(c.split(":", 1)[1].strip())
    return None


def _progress_html(g, rated):
    # aria-live works in place here (unlike the status Markdowns, which Gradio
    # re-renders) because this node is created once and thereafter only has its
    # textContent rewritten by the head script. That script debounces the
    # rewrite so a burst of clicks announces once, not once per radio.
    return (
        f'<div class="annot-progress">'
        f'<span class="prog-rated" role="status" aria-live="polite" '
        f'aria-atomic="true">{rated} of {g.n_turns} turns rated</span>'
        f'</div>'
    )


def _card_header_html(g, idx):
    sender = g.ai_turns[idx]["from"]
    role = g.role(sender)
    sender_chip = f'<span class="ta-sender">{html.escape(sender)}</span>'
    role_chip = (f'<span class="ta-role">{html.escape(role)}</span>'
                 if g.multi_role else "")
    return (
        f'<div class="ta-head">'
        f'<span class="ta-badge">{idx + 1}</span>'
        f'<h3 class="ta-title">Turn {idx + 1} of {g.n_turns}</h3>'
        f'{sender_chip}'
        f'{role_chip}'
        f'<span class="rated-badge">✓ Rated</span>'
        f'</div>'
    )


def _turn_nav_html(g):
    chips = "".join(
        f'<button type="button" class="tn-chip" role="tab" id="tn-chip-{i}" '
        f'data-turn="{i}" aria-selected="{"true" if i == 0 else "false"}" '
        f'tabindex="{"0" if i == 0 else "-1"}">{i + 1}</button>'
        for i in range(g.n_turns)
    )
    return (
        '<div class="turn-nav" role="tablist" aria-label="Annotation turns">'
        '<button type="button" class="tn-arrow" data-nav="prev" '
        'aria-label="Previous turn" title="Previous turn">‹</button>'
        f'<div class="tn-chips">{chips}</div>'
        '<button type="button" class="tn-arrow" data-nav="next" '
        'aria-label="Next turn" title="Next turn">›</button>'
        '</div>'
    )


def _build_transcript_html(g, current_idx, pretty_map=False, pretty_path=False,
                           scroll_cls="txscroll", id_prefix="tc-"):
    # scroll_cls/id_prefix let the training screen reuse this renderer without
    # colliding with the annotation page's JS (which targets .txscroll and
    # the #tc-N turn-card ids globally).
    parts = []
    turn_counter = 0  # index over AI-player response turns only
    _prev_grid = None  # ImageGame: last grid seen, for the per-turn diff

    goal_text = _rich_content_html(g.rules.strip(), g.game_key)
    parts.append(
        f'<div class="goal-box">'
        f'<h2 class="goal-label">GAME GOAL</h2>'
        f'<div class="goal-text">{goal_text}</div>'
        # Stated up front, because the crazy variants' colours are NOT the
        # standard ones and an annotator working from Wordle intuition would
        # read every piece of feedback backwards.
        f'{wordle_legend_html(g.game_key)}'
        f'</div>'
    )

    # Map renderer state (see _map_truth_and_layout) — only built in hybrid mode.
    map_snapshots, map_grid = ([], {})
    if pretty_map:
        map_snapshots, map_grid = _map_truth_and_layout(g)
        parts.append(_MAP_LEGEND_HTML)

    # Path renderer state (see _path_truth_and_layout) — only built in hybrid mode.
    path_snapshots, path_grid, path_target = ([], {}, None)
    if pretty_path:
        path_snapshots, path_grid, path_target = _path_truth_and_layout(g)
        parts.append(_PATH_LEGEND_HTML)

    # Skip a verbatim repeat of the rules already shown in the GAME GOAL box.
    # Only g.rules itself, not every turn-0 message — some games send each
    # player their own extra private info in turn 0 that must still show.
    _turn0_setup = {g.rules}

    # Assign a stable colour slot (p1, p2…) to each AI sender in appearance order.
    _player_slots: dict = {}
    _slot_names = ["p1", "p2", "p3", "p4"]
    for _turn in g.data["turns"]:
        for _m in _turn:
            pid = _m["from"]
            if pid in g.ai_ids and pid not in _player_slots:
                _player_slots[pid] = _slot_names[min(len(_player_slots), len(_slot_names) - 1)]

    for round_idx, round_msgs in enumerate(g.data["turns"]):
        for msg in round_msgs:
            sender = msg["from"]
            action = msg["action"]
            atype = action.get("type", "")
            label = action.get("label", "")
            content = action.get("content", "")

            # GM / environment messages
            if sender == "GM" or sender not in g.ai_ids:
                # End-game bookkeeping events are consumed by _detect_outcome
                # (one banner rendered after the loop) and never shown inline.
                _END_TYPES = {
                    "game_finished", "game end", "game_result",
                    "adventure_finished", "end", "successful agreement", "aborted",
                    "stop", "info", "success",
                }
                if atype in _END_TYPES:
                    continue

                if atype == "correct guess" and isinstance(content, str):
                    # 'end game' / 'game_result = WIN' markers feed the outcome
                    # banner; an actual guessed word (guesswhat) stays inline.
                    if content == "end game" or "game_result" in content:
                        continue
                    parts.append(
                        f'<div class="correct-msg">✅ '
                        f'<strong>{html.escape(str(content))}</strong></div>'
                    )
                    continue

                if not isinstance(content, str):
                    continue
                # Only round 0: some games re-send this same text later as
                # legitimate new context, which must not get hidden too.
                if round_idx == 0 and content in _turn0_setup:
                    continue

                if label == "context":
                    # The map already shows this, so the GM's raw JSON echo is just noise.
                    if pretty_map and _parse_map_response(content):
                        continue
                    tag = "GM" if sender == "GM" else html.escape(g.role(sender))
                    # Label the recipient — in two-player games a private GM
                    # message can land under the OTHER player's turn card.
                    recipient = msg.get("to")
                    if g.multi_role and recipient in g.ai_ids:
                        tag = f"{tag} → {html.escape(recipient)}"
                    parts.append(
                        f'<div class="gm-msg">'
                        f'<span class="gm-tag">{tag}</span> {_rich_content_html(content, g.game_key)}'
                        f'</div>'
                    )
                continue

            # Genuine AI player response → render as a turn card
            if label == "response":
                # Skip retry duplicates (see load_game) without bumping turn_counter,
                # so tc-N card ids stay aligned with ai_turns indices.
                if id(msg) in getattr(g, "skip_msg_ids", set()):
                    continue
                active = turn_counter == current_idx
                slot = _player_slots.get(sender, "p1")
                card_cls = f"turn-card {slot}" + (" active-turn" if active else "")

                # Map SVG shows in both conditions — only the questions differ.
                body_html = _render_response_body(content, g.game_key)

                # ImageGame: the Follower replies with the whole grid, so show
                # it against the previous one with this turn's changes marked.
                # Tracked across turns rather than per-card, because "before" is
                # the last grid ANY turn produced.
                if g.game_key == "imagegame":
                    grid = parse_cell_grid(content)
                    if grid:
                        # The Follower's entire reply IS the grid, so this
                        # replaces the body rather than adding to it —
                        # otherwise the same grid appears twice per card.
                        body_html = grid_change_html(_prev_grid, grid)
                        _prev_grid = grid
                if pretty_map and isinstance(content, str):
                    parsed = _parse_map_response(content)
                    if parsed:
                        action_txt = html.escape(str(parsed.get("action", "")))
                        snap = (map_snapshots[turn_counter]
                                if turn_counter < len(map_snapshots) else None)
                        svg = _map_svg(parsed, snap, map_grid) if snap else None
                        if svg:
                            body_html = (
                                f'<div class="map-action"><strong>Action:</strong> {action_txt}</div>'
                                f'<div class="map-wrap">{svg}</div>'
                            )
                        else:
                            graph_txt = html.escape(json.dumps(parsed["graph"], indent=2, default=list))
                            body_html = (
                                f'<div class="map-action"><strong>Action:</strong> {action_txt}</div>'
                                f'<pre style="white-space:pre-wrap;margin-top:6px;">{graph_txt}</pre>'
                            )

                # Path SVG: target room highlighted gold once its position is known.
                if pretty_path and isinstance(content, str):
                    snap = (path_snapshots[turn_counter]
                            if turn_counter < len(path_snapshots) else None)
                    svg = _path_svg(snap, path_grid, path_target) if snap else None
                    if svg:
                        action_txt = html.escape(content.strip())
                        body_html = (
                            f'<div class="map-action"><strong>Action:</strong> {action_txt}</div>'
                            f'<div class="map-wrap">{svg}</div>'
                        )

                parts.append(
                    f'<div class="{card_cls}" id="{id_prefix}{turn_counter}">'
                    f'<div class="card-header">'
                    f'{html.escape(sender)}&nbsp;&nbsp;·&nbsp;&nbsp;TURN {turn_counter + 1} OF {g.n_turns}'
                    f'</div>'
                    f'<div class="card-body">{body_html}</div>'
                    f'</div>'
                )
                turn_counter += 1

    # Shown after the turn card, so the annotator reads the response first.
    ref = _reference_answer(g)
    if ref:
        parts.append(
            '<div class="ref-answer">'
            '<div class="ref-answer-label">✓ REFERENCE ANSWER</div>'
            f'<div class="ref-answer-body">{ref}</div>'
            '</div>'
        )

    # One outcome banner only — per-event end markers are dropped above, not shown inline.
    parts.append(_OUTCOME_BANNERS[getattr(g, "outcome", "ended")])

    return f'<div class="{scroll_cls}">' + "".join(parts) + "</div>"


def show_q3_for(g, condition):
    """Whether Reasoning Clarity is asked for this transcript.

    The render loop and the export must agree exactly — if they don't, the
    export computes a different fingerprint from the one collection stored and
    every row looks like it was collected under a changed question set.
    """
    bespoke = (BESPOKE_QUESTIONS.get(g.game_key)
               if BLOCK_TO_TYPE.get(condition, "universal") == "hybrid" else None)
    return (bool(bespoke.get("reasoning_clarity"))
            if bespoke is not None else bool(g.has_reasoning))


def first_turn_of_role(g):
    """{role: index of that role's FIRST turn}. A bolt-on listed under
    "bolt_ons_first_turn" is asked only there — the wordle clue question is
    about the opening guess and has no meaning on later turns."""
    first = {}
    for i, msg in enumerate(g.ai_turns):
        first.setdefault(g.role(msg["from"]), i)
    return first


def question_spec(g, condition, show_q3):
    """Canonical description of the exact question set a transcript was shown.

    Per-transcript, not per-family: which roles occur, whether Q3 is shown, and
    the flag list all vary transcript by transcript, and a family-level spec
    would miss all three.

    Hashed and stored at collection time so a later export can decode answers
    against the questions that were actually on screen. Without it, editing
    annotation.py silently re-labels data already collected — the per-turn value
    survives (it is stored in a named column) but its meaning quietly changes.
    """
    mode = BLOCK_TO_TYPE.get(condition, "universal")
    bespoke = BESPOKE_QUESTIONS.get(g.game_key, {}) if mode == "hybrid" else {}
    roles_cfg = bespoke.get("roles") or {}

    def norm(choices):
        # [value, label] — the value is what is stored, the label its meaning.
        return [[v, str(d).split("\n")[-1].strip()] for d, v in (choices or [])]

    def slot(cfg, generic):
        if cfg == "generic" or cfg is None and not roles_cfg:
            return [plain_label(generic[0]), norm(generic[1])]
        if cfg is None:
            return None                     # not asked of this role
        return [plain_label(cfg[0]), norm(cfg[1])]

    roles = {}
    for sender in sorted(g.ai_ids):
        role = g.role(sender)
        if role in roles:
            continue
        cfg = roles_cfg.get(role, {})
        roles[role] = {
            "q1": slot(cfg.get("q1", "generic"), GENERIC_Q1),
            "q2": slot(cfg.get("q2", "generic"), GENERIC_Q2),
            "q3": slot("generic", GENERIC_Q3) if show_q3 else None,
            "bolt_ons": [[k, plain_label(md), norm(ch)]
                         for k, md, ch in cfg.get("bolt_ons", [])],
            "bolt_ons_first_turn": [[k, plain_label(md), norm(ch)]
                                    for k, md, ch in cfg.get("bolt_ons_first_turn", [])],
        }

    _bf = bespoke.get("flags")
    flags = _bf if isinstance(_bf, list) else g.flag_choices

    return {
        "v": 1,
        "game": g.game_key,
        "condition": condition,
        "mode": mode,
        "roles": roles,
        "flags": list(flags or []),
        "whole_game": [[qid, plain_label(md), norm(ch)]
                       for qid, md, ch in normalise_whole_game(bespoke.get("whole_game"))],
        "whole_game_only": bool(bespoke.get("whole_game_only")),
    }


def _assert_spec_matches_render(spec, field_specs, g):
    """Fail loudly if the spec disagrees with what was actually rendered.

    field_specs is built by the render loop itself, so it cannot drift; the spec
    is rebuilt independently. If they disagree the spec is wrong, and recording
    its hash would attach a confident but false description to real data.
    """
    want = set()
    first_turn = first_turn_of_role(g)
    for turn_i, msg in enumerate(g.ai_turns):
        sender = msg["from"]
        role = g.role(sender)
        cfg = spec["roles"].get(role)
        if cfg is None:
            continue
        for slot in ("q1", "q2"):
            if cfg.get(slot) is not None:
                want.add((turn_i, slot))
        # q3 is NOT conditional here, even though the spec records it as None
        # when Reasoning Clarity isn't asked. The render loop always emits a q3
        # component — a real radio when it is asked, a hidden preset-N/A one
        # when it isn't — so that field_specs stays 1:1 with the component list
        # and the JS rated-counter keeps working. The spec is describing what
        # was on SCREEN; field_specs is describing what was WIRED UP. Only the
        # latter decides what lands in this set.
        want.add((turn_i, "q3"))
        for key, _md, _ch in cfg.get("bolt_ons", []):
            want.add((turn_i, "extra", key))
        if first_turn.get(role) == turn_i:
            for key, _md, _ch in cfg.get("bolt_ons_first_turn", []):
                want.add((turn_i, "extra", key))
    got = {tuple(fs) for fs in field_specs
           if fs[1] in ("q1", "q2", "q3", "extra")}
    if want != got:
        raise RuntimeError(
            "question_spec disagrees with the rendered form for "
            f"{g.slug}: only-in-spec={sorted(want - got)[:5]} "
            f"only-rendered={sorted(got - want)[:5]}"
        )


def question_spec_json(spec):
    """sort_keys normalises dict order (incidental) but preserves LIST order,
    which is the thing that must not change silently — whole_game especially."""
    return json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def question_spec_hash(spec):
    return hashlib.sha256(question_spec_json(spec).encode()).hexdigest()[:16]


def _submit(g, field_specs, condition, show_q3, annotator_id, started_at, session_day,
            session_started_at, *vals):
    # field_specs[i] describes what vals[i] holds — see build() for how it's built.

    # Every rendered question is mandatory; only flags and the comment can stay empty.
    incomplete = sorted({spec[0] for spec, val in zip(field_specs, vals)
                         if spec[1] in ("q1", "q2", "q3", "extra") and not val})
    if incomplete:
        turns = ", ".join(str(i + 1) for i in incomplete)
        # The empty marker span flags this as a validation failure for the a11y
        # module in app.py: the unanswered turn card is display:none when this
        # renders, so the module reveals it, focuses the first unanswered
        # option and sets aria-invalid.
        #
        # The turn numbers travel in the message text itself, and the module
        # parses them back out of "...on turn(s) 1, 2, 3." — Gradio's markdown
        # sanitiser drops data-* attributes, extra classes AND element text, so
        # the visible sentence is the only channel that survives. Keep the
        # "on turn/turns <n>, <n>." shape in step with the regex there.
        marker = '<span class="a11y-bad-turns" hidden></span>'
        return (
            f"⚠️ **Not submitted.** Every question must be answered — "
            f"missing answers on turn{'s' if len(incomplete) > 1 else ''} "
            f"{turns}. (Flags and comments are optional.){marker}",
            gr.update(), gr.update(),
        )

    per_turn = {
        i: {"q1": None, "q2": None, "q3": None, "flags": [], "comment": "", "extra": {}}
        for i in range(g.n_turns)
    }
    for spec, val in zip(field_specs, vals):
        turn_i, slot = spec[0], spec[1]
        if slot == "extra":
            per_turn[turn_i]["extra"][spec[2]] = val
        else:
            per_turn[turn_i][slot] = val

    turns_out = []
    for i, msg in enumerate(g.ai_turns):
        t = per_turn[i]
        turns_out.append({
            "turn_index": i,
            "from": msg["from"],
            "role": g.role(msg["from"]),
            "content": msg["action"]["content"],
            "prior_information_use": t["q1"],
            "strategic_logic": t["q2"],
            "reasoning_clarity": t["q3"] if show_q3 else None,
            "flags": t["flags"] or [],
            "comment": t["comment"] or "",
            "extra_responses": t["extra"] or None,
        })

    # Fingerprint the exact question set this submission was collected under.
    # Cross-checked against field_specs first — field_specs is ground truth for
    # what was actually rendered, so a spec that disagrees with it would record
    # a confident lie. Better to fail here than to store a wrong label forever.
    spec = question_spec(g, condition, show_q3)
    _assert_spec_matches_render(spec, field_specs, g)
    qs_hash = question_spec_hash(spec)

    dims = None
    try:
        import study_set
        dims = study_set.dimensions(g.slug)
    except Exception:
        dims = None            # not a study transcript, or inventory unavailable

    db.save_turns(g.slug, g.meta, g.source_path, g.has_reasoning,
                  annotator_id, condition, turns_out,
                  started_at=started_at or None, session_day=session_day or None,
                  session_started_at=session_started_at or None,
                  dims=dims, question_set_hash=qs_hash,
                  question_set_spec=question_spec_json(spec))
    return "✅ Saved!", gr.update(visible=False), gr.update(visible=True)


def build(welcome_page, annotation_page, verdict_page, game_state, annotator_state,
          block_state, playlist_state, playlist_idx_state, started_at_state,
          session_day_state, session_started_at_state, clearing_state):
    with annotation_page:

        # Whole page re-renders whenever `game` or `block` changes, so game
        # loading is URL-driven rather than a fixed default game.
        @gr.render(inputs=[game_state, block_state, playlist_state,
                           playlist_idx_state, clearing_state])
        def _render_annotation(path, block, playlist, playlist_idx, clearing):
            # While clearing_state is True, render nothing so every widget fully
            # unmounts before the next game mounts — this is what stops values
            # carrying over between games.
            if clearing or not path:
                return
            g = load_game(path)
            block_type = BLOCK_TO_TYPE.get(block, "universal")
            bespoke = BESPOKE_QUESTIONS.get(g.game_key) if block_type == "hybrid" else None
            # Universal mode shows Q3 wherever the AI explains itself; hybrid
            # mode only where the bespoke set opts in via reasoning_clarity.
            show_q3 = show_q3_for(g, block)
            pretty_map = bool(BESPOKE_QUESTIONS.get(g.game_key, {}).get("render_graph"))
            pretty_path = bool(BESPOKE_QUESTIONS.get(g.game_key, {}).get("render_path"))

            # Playlist sessions show where the annotator is in today's queue.
            seq_chip = ""
            if playlist:
                seq_chip = (f'<span class="game-seq-tag">Game {playlist_idx + 1} '
                            f'of {len(playlist)}</span>')

            # No heading existed on this screen at all — the one place heading
            # navigation matters most. Also the a11y module's focus target.
            gr.HTML(
                f'<h1 class="a11y-sr-only" tabindex="-1">Rate turns — '
                f'{html.escape(str(g.meta["game_name"]).title())}</h1>'
            )

            with gr.Row(elem_classes=["annot-topnav"]):
                gr.HTML(
                    f'<div class="nav-left">'
                    f'<span class="game-id-tag">#{html.escape(str(g.meta["game_id"]))}</span>'
                    f'<span class="game-name-tag">{html.escape(str(g.meta["game_name"]).title())}</span>'
                    f'{seq_chip}'
                    f'</div>'
                )
                gr.HTML(_progress_html(g, 0), elem_classes=["nav-center"])

            if g.n_turns == 0:
                gr.HTML(
                    '<div class="goal-box"><div class="goal-label">NO AI TURNS</div>'
                    '<div class="goal-text">This transcript has no annotatable AI '
                    'player turns.</div></div>'
                )
                # Without this the page is a dead end — no Submit is rendered
                # (nothing to submit) and no other way back exists.
                gr.Button("Quit", variant="stop", elem_classes=["quit-btn"]).click(
                    fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
                    outputs=[welcome_page, annotation_page],
                )
                return

            with gr.Row(equal_height=False, elem_classes=["anno-main-row"]):

                with gr.Column(scale=3, elem_classes=["tx-col"]):
                    gr.HTML(_build_transcript_html(g, 0, pretty_map=pretty_map,
                                                   pretty_path=pretty_path))

                # No key= here — Gradio 6 keyed blocks can raise DuplicateBlockError
                # when the same game re-renders, and unkeyed components are just as safe.
                with gr.Column(scale=2, elem_id="annot-col"):
                    gr.HTML(_turn_nav_html(g))

                    # Paired 1:1 with field_specs so _submit can reconstruct per-turn answers.
                    components, field_specs = [], []
                    first_turn = first_turn_of_role(g)

                    for i in range(g.n_turns):
                        sender = g.ai_turns[i]["from"]
                        role = g.role(sender)
                        role_cfg = (bespoke or {}).get("roles", {}).get(role, {})

                        with gr.Group(elem_classes=["turn-anno-card"]):
                            gr.HTML(_card_header_html(g, i))

                            # Q1 slot: "generic" (default) = universal widget,
                            # None = not rendered for this role, tuple = bespoke.
                            # label= is sr-only under show_label=False, so it
                            # renames the control without touching the layout.
                            # Turn-qualified: N identical groups share one page.
                            _t = f"Turn {i + 1} — "
                            q1_cfg = role_cfg.get("q1", "generic")
                            if q1_cfg == "generic":
                                gr.Markdown(GENERIC_Q1[0])
                                q1 = gr.Radio(
                                    choices=GENERIC_Q1[1], label=_t + plain_label(GENERIC_Q1[0]),
                                    show_label=False, elem_classes=["scale-radio", "q1-scale"],
                                )
                                components.append(q1); field_specs.append((i, "q1"))
                            elif q1_cfg is not None:
                                label_md, choices = q1_cfg
                                gr.Markdown(label_md)
                                q1 = gr.Radio(choices=choices, label=_t + plain_label(label_md),
                                              show_label=False,
                                              elem_classes=["scale-radio", "q1-scale"])
                                components.append(q1); field_specs.append((i, "q1"))

                            # Q2 slot — same generic/None/bespoke pattern.
                            q2_cfg = role_cfg.get("q2", "generic")
                            if q2_cfg == "generic":
                                gr.Markdown(GENERIC_Q2[0])
                                q2 = gr.Radio(
                                    choices=GENERIC_Q2[1], label=_t + plain_label(GENERIC_Q2[0]),
                                    show_label=False, elem_classes=["scale-radio", "q2-scale"],
                                )
                                components.append(q2); field_specs.append((i, "q2"))
                            elif q2_cfg is not None:
                                label_md, choices = q2_cfg
                                gr.Markdown(label_md)
                                q2 = gr.Radio(choices=choices, label=_t + plain_label(label_md),
                                              show_label=False,
                                              elem_classes=["scale-radio", "q2-scale"])
                                components.append(q2); field_specs.append((i, "q2"))

                            # Bespoke bolt-on(s) — additive, beyond the Q1/Q2
                            # slots. The second list is asked once, on this
                            # role's opening turn only.
                            _bolts = list(role_cfg.get("bolt_ons", []))
                            if first_turn.get(role) == i:
                                _bolts += role_cfg.get("bolt_ons_first_turn", [])
                            for key, label_md, choices in _bolts:
                                gr.Markdown(label_md)
                                bolt = gr.Radio(choices=choices, label=_t + plain_label(label_md),
                                                show_label=False,
                                                elem_classes=["scale-radio"])
                                components.append(bolt); field_specs.append((i, "extra", key))

                            # When hidden, Q3 is a preset-N/A invisible radio so
                            # field_specs and the JS rated-counter stay aligned.
                            if show_q3:
                                gr.Markdown(GENERIC_Q3[0])
                                q3 = gr.Radio(
                                    choices=GENERIC_Q3[1], label=_t + plain_label(GENERIC_Q3[0]),
                                    show_label=False, elem_classes=["scale-radio", "q3-scale"],
                                )
                            else:
                                q3 = gr.Radio(choices=[("N/A", "NA")], value="NA", visible=False,
                                              label=_t + "Reasoning clarity (not applicable)",
                                              show_label=False)
                            components.append(q3); field_specs.append((i, "q3"))

                            # A bespoke flags list overrides the generic set; an
                            # explicit [] means no flags at all for this game.
                            _bf = bespoke.get("flags") if bespoke else None
                            flag_choices = _bf if isinstance(_bf, list) else g.flag_choices
                            if flag_choices:
                                gr.HTML('<div class="flags-lbl">Flags <span class="flags-sub">— tick all that apply</span></div>')
                                fl = gr.CheckboxGroup(
                                    choices=flag_choices,
                                    label=_t + "Flags — tick all that apply",
                                    show_label=False,
                                    elem_classes=["flags-check"],
                                )
                                components.append(fl); field_specs.append((i, "flags"))

                            cm = gr.Textbox(
                                placeholder="Optional turn comment…",
                                label=_t + "comment (optional)",
                                show_label=False, lines=1,
                                elem_classes=["turn-comment"],
                            )
                            components.append(cm); field_specs.append((i, "comment"))

                    status = gr.Markdown("", elem_id="annot-status")
                    with gr.Row():
                        back_btn = gr.Button("Quit", variant="stop", elem_classes=["quit-btn"])
                        submit_btn = gr.Button("Submit All", variant="primary")

            # EVENTS — wired inside @gr.render since `components` is rebuilt
            # fresh on every game/block change.
            submit_btn.click(
                fn=functools.partial(_submit, g, field_specs, block, show_q3),
                inputs=[annotator_state, started_at_state, session_day_state,
                        session_started_at_state, *components],
                outputs=[status, annotation_page, verdict_page],
            )
            back_btn.click(
                fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
                outputs=[welcome_page, annotation_page],
            )

