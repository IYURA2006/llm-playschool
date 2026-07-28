import gradio as gr
import ast
import functools
import glob
import html
import json
import os
import re

import db

# ──────────────────────────────────────────────────────────────────────────
# GAME DISCOVERY + DATA LOADING
# Fully data-driven: every interactions.json under games/ becomes a selectable
# transcript, and load_game() derives all per-game state on demand.
# ──────────────────────────────────────────────────────────────────────────

_dir = os.path.dirname(os.path.abspath(__file__))
_games_dir = os.path.join(_dir, "games")


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
    """The clembench game-FAMILY name (taboo, mmlu_pro, …), the routing key for
    BESPOKE_QUESTIONS / reasoning / map rendering. clembench always ends
    …/<game>/<variant>/<instance>/interactions.json, so the family is the
    4th-from-last path component — true whether the transcript is filed under
    the original games/<game>/… tree OR the test_newgames
    games/<domain>/<model>/<game>/… tree, so routing survives either placement.
    Falls back to the first segment for any shallower (debug) placement.

    NB: distinct from game_slug().split("__")[0], which returned the *first*
    path segment and so silently became the domain ("clem_indomain") once the
    deeper layout put a domain/model dir above the game."""
    parts = os.path.relpath(game_path, _games_dir).split(os.sep)
    return parts[-4] if len(parts) >= 4 else parts[0]


_SLUG_TO_PATH = {game_slug(path): path for _, path in GAMES}


def slug_to_path(slug):
    """Resolve a `game` URL param to its transcript path, or None if unknown."""
    return _SLUG_TO_PATH.get(slug)


# Which block/condition value maps to which question-set mode. The
# day1_*/day2_* names are the original pilot-era one-link-per-game blocks,
# kept working for the legacy single-game debug link; the bare
# "universal"/"hybrid" values are what the general study's playlist items
# (assignment.build_playlist_for, always "hybrid") carry.
BLOCK_TO_TYPE = {
    "day1_universal": "universal",
    "day1_hybrid": "hybrid",
    "day2_mixed": "hybrid",
    "universal": "universal",
    "hybrid": "hybrid",
}
VALID_BLOCKS = set(BLOCK_TO_TYPE)


def _scale4(labels):
    """4-choice 1..4 scale radio, e.g. _scale4(["None","Partial","Good","Excellent"])."""
    return [(f"{i + 1}\n{lbl}", str(i + 1)) for i, lbl in enumerate(labels)]


def _scaleN(n, ends=None):
    """Plain-number 1..n scale radio; `ends={1: 'lo', n: 'hi'}` adds end anchors.
    Used for the bespoke whole-game "specific overall" questions (1-7 / 1-4)."""
    ends = ends or {}
    return [((f"{i}\n{ends[i]}" if i in ends else str(i)), str(i))
            for i in range(1, n + 1)]


# The single-turn QA benchmarks (bbh, cladder, mmlu_pro) are auto-scored — the
# gold answer and the win/lose banner are shown on the left panel — so the human
# judgement worth capturing is the REASONING that led there, not the answer.
# One shared Q1 replaces the generic Q1/Q2, which are multi-turn-oriented ("prior
# information use" / "sensible next step" don't apply to a one-shot answer). The
# player's game_role is "Answerer" in all three. Applied to reasoning games only:
# ifeval (instruction-following) and eqbench (emotion scoring) aren't reasoning
# tasks, are absent here, and so keep the generic questions.
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


# Hybrid-mode bespoke per-turn question sets, wired up only when block_type ==
# "hybrid" (see BLOCK_TO_TYPE). Wording/scales sourced from question_set.md
# (the pilot's design doc) — each entry replaces the universal Q1/Q2 for the
# listed game_role(s); a role/slot not listed here falls back to "generic"
# (the same widget universal mode uses). Games not listed here get no
# branching at all in hybrid mode (matches plan.md: Wordle/Dond need none).
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
                        "that exists on the board at all?",
                        [("Yes", "yes"), ("No", "no")],
                    ),
                ],
            },
        },
    },
    "taboo": {
        "flags": [
            "Guesser repeated a guess it already made",
            "Describer gave the same (or same-meaning) clue as an earlier turn",
        ],
        "roles": {
            "WordDescriber": {
                # Clarity only — forbidden-word use is checked automatically by the
                # game engine, so a human should not judge it on this scale.
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
        # Colours match the map renderer/legend (_map_svg + _MAP_LEGEND_HTML):
        # green ring = claimed correctly, red ring = claimed wrongly.
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
        "flags": [
            "Player revealed its own secret values in the open chat",
            "This player's secret proposal doesn't match what was just agreed",
        ],
        "roles": {
            # Both seats share the "DealOrNoDealPlayer" role.
            "DealOrNoDealPlayer": {
                "q1": (
                    "**Q1 — Value Consistency**\n\nDoes this offer make sense "
                    "given what this player says they value?",
                    _scale4([
                        "Contradicts it", "Barely consistent",
                        "Mostly consistent", "Fully consistent",
                    ]),
                ),
                "q2": (
                    "**Q2 — Builds on the Agreement**\n\nDoes this message build "
                    "on what was agreed earlier?",
                    _scale4([
                        "Ignores/contradicts it", "Barely connected",
                        "Mostly follows on", "Clearly builds on it",
                    ]),
                ),
            },
        },
        "whole_game": [
            (
                "**Whole game — Did they reach an agreement that was genuinely "
                "collaborative and close to the best value for both?**",
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
        # Two AI seats with DIFFERENT roles, so the per-turn questions change with
        # whose turn it is: Giver turns (the "Command: …" text) get Q1/Q2, Follower
        # turns (the ▢-grid the game renders as an ASCII block) get the grid-update
        # question. The target grid the Giver must reproduce is shown once at the
        # top of the transcript (its opening GM prompt), so Q1 is answerable there.
        "flags": [],  # no per-turn flags for imagegame (Giver or Follower)
        "whole_game_only": True,  # verdict shows ONLY the whole_game sliders (no G1/G2)
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
                # Was a Yes/No/N/A tick — now a 1-4 scale (team call).
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
                "**Whole game — Giver's plan**\n\nEven if the Follower made mistakes "
                "along the way, was the Giver's overall plan for the shape a good one?",
                _scaleN(7, {1: "plan itself was confused / didn't make sense",
                            7: "clear and correct the whole way through"}),
            ),
            (
                "**Whole game — Follower's execution**\n\nEven if the Giver's "
                "instructions weren't always correct, did the Follower update the "
                "grid correctly based on what it was actually told?",
                _scaleN(7, {1: "rarely followed instructions correctly",
                            7: "followed correctly nearly every time"}),
            ),
        ],
    },

    # ── test_newgames additions (2026-07-26) ──────────────────────────────
    # 13 families brought in from the test_newgames bundle (see games/<name>/
    # for the transcript data). cryptolect, ta_blackjack, and the data-less
    # bbh/cladder/mmlu_pro placeholders above are deliberately excluded.

    "eqbench": {
        # 1-turn QA benchmark (predict a character's emotional-intensity
        # scores for a dialogue, auto-scored against a reference distribution
        # shown via _reference_answer). Not a reasoning task — no chain of
        # thought to check — so it does NOT share _QA_REASONING_ROLE with
        # bbh/cladder/mmlu_pro. What's left for a human is whether the READ
        # of the dialogue is plausible, independent of hitting the exact
        # reference numbers.
        "flags": [],  # one-shot answer — no per-turn "repeated a move" concept
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
        # 1-turn instruction-following benchmark — mechanical compliance
        # (case, format, word count, …) is auto-checked and shown via the
        # reference-answer strip; the human judgement is whether the response
        # is ALSO still sensible, on-topic content, not just compliant.
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
        # Codenames-shaped: Helper gives one indirect clue per round toward a
        # secret target word, Seeker guesses from it — same clue-safety /
        # guess-match split as codenames' ClueGiver/Guesser.
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
        # Spatial variant of clean_up: same collaborative move-objects-to-
        # positions mechanic and SAY/MOVE actions, just on a coordinate grid
        # with more objects — clean_up's question design fits directly.
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
        # TextArena Frozen Lake — single-seat, navigates a grid avoiding
        # holes revealed as it moves. Role key is literally "Player 0" (this
        # family logs no more descriptive game_role — verified against real
        # transcripts), unlike most other games here.
        "flags": [
            "Moved onto a cell it had already identified as a hole",
            "Repeated a move that already failed",
            "Ignored information revealed by an earlier step",
        ],
        "roles": {
            "Player 0": {
                "q1": (
                    "**Q1 — Hazard Avoidance**\n\nDoes this move avoid a "
                    "hole the board has already revealed to it?",
                    _scale4(["Walks into a known hole", "Risky", "Mostly careful", "Fully careful"]),
                ),
                "q2": (
                    "**Q2 — Progress**\n\nIs this move a sensible step "
                    "toward the goal given what's currently visible?",
                    _scale4(["Nonsensical", "Wastes a turn", "Reasonable", "Efficient"]),
                ),
            },
        },
        "whole_game": [
            (
                "**Whole game — Did it navigate carefully and purposefully "
                "toward the goal?**",
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
        # Role key is literally "Player 0" (same TextArena convention as
        # ta_frozen_lake — no custom game_role logged).
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

# wordle-crazy's twist vs. standard (unbuilt-bespoke) Wordle: the guess-
# feedback colors are deliberately SCRAMBLED (purple/black instead of
# green/yellow — see _WD_TILE_RE) and the mapping is defined only in that
# episode's own rules text. The skill being tested is therefore "did it
# correctly re-derive and apply THIS episode's color key", not just word-
# guessing — worth a bespoke Q1 shared across all three wordle-crazy variants
# (the guessing mechanic itself is identical).
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
    "wordle-crazy_withclue": {
        "flags": _WORDLE_CRAZY_FLAGS,
        "roles": {
            "WordGuesser": {
                "q1": _WORDLE_CRAZY_Q1,
                "q2": "generic",
            },
        },
        "whole_game": _WORDLE_CRAZY_WHOLE_GAME,
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


# Shared Q1/Q2 for plain Wordle and its clue variant (identical mechanic —
# the clue variant's "use the clue's meaning" nuance is folded into Q2's
# wording rather than special-cased to turn 1, since role_cfg applies
# uniformly across all of a role's turns).
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

# original-17-pilot games that still fell back to the generic Q1/Q2 (see
# question_set.md for the drafted wording each of these reuses).
BESPOKE_QUESTIONS.update({
    "guesswhat": {
        "roles": {
            "Guesser": {
                "q1": (
                    "**Q1 — Useful Question**\n\nWas this question a sharp, "
                    "useful choice given everything learned from earlier "
                    "answers?",
                    _scale4(["Not useful", "Weak", "Reasonable", "Sharp, well-targeted"]),
                ),
                "q2": None,
            },
            # The Answerer's forced yes/no has no strategy to judge —
            # question_set.md: "the Answerer's yes/no is probably best left
            # out." Deliberately no per-turn question; its turns still get a
            # card, just with only flags/comment, no q1/q2.
            "Answerer": {"q1": None, "q2": None},
        },
        "flags": ["Repeated a question already asked and answered (exact or near-exact)"],
        "whole_game": [
            (
                "**Whole game — Did the Guesser's questioning use its turns "
                "efficiently, narrowing things down rather than wasting turns?**",
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
        # Player 2 ("Questioner") is model_name: "programmatic" — not a real
        # AI seat, already excluded by _is_ai_player(). Only "Answerer" is
        # annotatable.
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
    # reasoning_clarity: True is required here — the moment a game gets ANY
    # bespoke entry, show_q3's gating switches from the g.has_reasoning
    # heuristic / _REASONING_GAMES set to bool(bespoke.get("reasoning_clarity")).
    # question_set.md wants Q3 always shown for this family ("explanation is
    # mandatory"), so omitting this flag would silently drop Q3 in hybrid mode.
    "wordle": {
        "reasoning_clarity": True,
        "roles": {"WordGuesser": {"q1": _WORDLE_Q1, "q2": _WORDLE_Q2}},
        # No flags override — the existing generic set (repeat a failed move /
        # invent-or-get-a-fact-wrong / self-corrected + "explanation doesn't
        # match the move" for reasoning games) already covers question_set.md's
        # suggested ticks here. No whole_game — outcome is already exact
        # (Win/Lose, Closeness Score).
    },
    "wordle_withclue": {
        "reasoning_clarity": True,
        "roles": {"WordGuesser": {"q1": _WORDLE_Q1, "q2": _WORDLE_Q2}},
    },
    "wordle_withcritic": {
        # Separate game_key from plain wordle — different game_role strings
        # (ReflectingWordGuesser / WordCritic, not WordGuesser).
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
        # No flags/whole_game override — episode is always exactly 2 turns.
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
        # No self-reported map here (bare "GO: <dir>"/"DONE" only) — see the
        # PATH RENDERER block for why _map_svg/render_graph don't apply.
        # render_path triggers _path_svg instead, in both conditions (same
        # rationale as render_graph: the raw GO/DONE text is unreadable
        # without a visual no matter which question set is active).
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


def whole_game_questions(game_path, block):
    """Bespoke whole-game "specific overall" questions for a game as a list of
    (question_markdown, choices), or [] when there are none.

    Returned ONLY in the hybrid condition — mirrors the per-turn bespoke gating
    (BLOCK_TO_TYPE): the universal condition keeps just the generic Coherence +
    Overall verdict questions, so the whole-game A/B addition is hybrid-only.
    """
    if BLOCK_TO_TYPE.get(block, "universal") != "hybrid":
        return []
    return BESPOKE_QUESTIONS.get(game_key(game_path), {}).get("whole_game") or []


def whole_game_only(game_path, block):
    """True when the verdict should show ONLY the game-specific whole-game
    questions (as 1-7 sliders) and hide the generic Coherence + Overall pair.
    Set per game via BESPOKE_QUESTIONS[...]["whole_game_only"]; hybrid-only,
    and only meaningful when whole_game_questions() is non-empty (imagegame:
    a two-role game where a single "overall quality" score doesn't fit)."""
    if BLOCK_TO_TYPE.get(block, "universal") != "hybrid":
        return False
    return bool(BESPOKE_QUESTIONS.get(game_key(game_path), {}).get("whole_game_only"))


def output_path_for(game_path):
    """Stable per-game annotation output file in interactions/."""
    return os.path.join(_dir, "interactions", f"annot__{game_slug(game_path)}.json")


class _Game:
    """Lightweight container for everything a screen needs about one game."""


def _detect_outcome(turns, data=None):
    """One game outcome — "won"/"lost"/"aborted"/"ended" — from the structured
    end-of-game signals each clembench game family logs. Replaces the old
    per-event keyword banners, which misfired two ways: every _END_TYPES event
    emitted its OWN banner (clean_up showed 🏆 twice, adventuregame three
    banners), and substring-matching str(content) called failed games won —
    str({'game_successfully_finished': False}) contains 'success', and
    codenames' 'opponent has won' (a loss) contains 'won'. Checks are ordered
    most-explicit-first; families whose transcripts carry no verdict at all
    (imagegame — scored offline against the target grid; privateshared;
    textmapworld's walker 'stop') fall through to the neutral "ended"."""
    # Top-level Success/Lose/Aborted flags — the authoritative verdict for the
    # test_newgames families (the QA benchmarks, clockwork, cryptolect, the ta_*
    # TextArena games, toh, wordle-crazy, …), whose per-turn events otherwise
    # only carry a 'game_result = WIN/LOSE' string this scan doesn't reach.
    # Checked first because it's the record's own summary; games without these
    # keys (codenames, guesswhat, imagegame, …) fall through to the event scan.
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

    # ── Identify genuine AI players (exclude GM and programmatic/scripted bots) ──
    def _is_ai_player(pid):
        info = players.get(pid, {})
        if pid == "GM":
            return False
        model = (info.get("model_name") or "").lower()
        return model != "programmatic" and model != ""

    ai_ids = {pid for pid in players if _is_ai_player(pid)}

    def _role(pid):
        return players.get(pid, {}).get("game_role", pid)

    # ── Extract the game rules robustly ──
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

    # ── Drop parse-error retry duplicates (clean_up) ──────────────────────
    # When a player's response is rejected for a format error (clean_up's
    # "message must not contain anything before the command" penalty), clembench
    # re-prompts and the model answers again — and logs BOTH the rejected attempt
    # and the accepted retry as label=="response". That surfaced as two adjacent
    # near-duplicate turn cards: the fuller reasoning attempt, then the bare
    # corrected resend of the same SAY. We keep the FULLER original (team call —
    # it strictly contains the retry's text, since the only thing stripped is the
    # reasoning "head") and drop the accepted retry. Detected structurally (a GM
    # parse_error between a response and the SAME player's very next response), so
    # it only ever fires where such a retry actually happened (today: clean_up).
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

    # ── Collect AI turns (only genuine AI players, in transcript order) ──
    ai_turns = []
    for turn in turns:
        for msg in turn:
            if (msg["from"] in ai_ids and msg["action"].get("label") == "response"
                    and id(msg) not in skip_ids):
                ai_turns.append(msg)

    # ── Detect whether this game has explicit reasoning/explanation text ──
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
    # question_set.md fixes Q3's trigger by GAME ("Shown when: Game requires
    # an explanation (Wordle family, Dond)") — the marker heuristic alone
    # misses Dond, whose negotiation prose explains itself without the exact
    # marker words (1 of 4 turns hit; threshold needs half). Game list first,
    # heuristic as the catch-all for anything else that visibly reasons.
    g.game_key = game_key(path)
    _REASONING_GAMES = {"wordle", "wordle_withclue", "wordle_withcritic", "dond"}
    g.has_reasoning = g.game_key in _REASONING_GAMES or _detect_reasoning(ai_turns)
    g.multi_role = len(ai_ids) > 1
    g.flag_choices = [
        "Repeated a move that already failed",
        "Invented or got a game fact wrong",
        "Noticed and fixed an earlier mistake",
    ]
    if g.has_reasoning:
        g.flag_choices.append("Explanation does not match the move")
    g.slug = game_slug(path)
    g.source_path = os.path.relpath(path, _dir)
    return g


# ──────────────────────────────────────────────────────────────────────────
# TEXTMAPWORLD (GRAPH REASONING) MAP RENDERER
# Per question_set.md's "updated, detailed version": each turn card shows the
# map the model has CLAIMED so far, validated against what its walk actually
# revealed — green ring = claimed correctly, red = claimed wrongly (and stays
# red until the claim is fixed), dashed blue ring = current position.
# ──────────────────────────────────────────────────────────────────────────

_DIR_VEC = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
_DIR_OPP = {"north": "south", "south": "north", "east": "west", "west": "east"}


def _parse_map_response(content):
    """Parse the model's `{"action": …, "graph": …}` reply, or None.

    The models write edge lists as Python tuples — `[("A", "B")]` — which is
    NOT valid JSON, so json.loads alone rejects almost every real turn (this
    is why the old pretty-print path silently fell back to raw text). Try
    JSON first, then a Python-literal parse.
    """
    if not isinstance(content, str) or "graph" not in content:
        return None
    for parse in (json.loads, ast.literal_eval):
        try:
            d = parse(content.strip())
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            continue
        if isinstance(d, dict) and isinstance(d.get("graph"), dict):
            return d
    return None


def _map_truth_and_layout(g):
    """Walk the full transcript once; return (snapshots, positions).

    snapshots[i] = ground truth OBSERVED BY THE MODEL up to (not including)
    its i-th response: visited rooms, directed true edges (both directions),
    and the room it stood in when it answered. Built from the GM's `move`
    records paired with the model's own `GO: <dir>` actions — the env only
    reveals the map by walking, so this is exactly what the model could know.

    positions = one stable full-game grid layout {room: (x, y)} derived from
    the true walk's compass directions, so rooms don't jump around between
    turn cards. Hallucinated (never-visited) rooms are placed later, per
    claim, in _map_svg.
    """
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

    # ── Place nodes: true-walk grid first, then hallucinated rooms next to
    # whichever claimed neighbour is already placed, else parked below. ──
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

    # ── Scale grid → pixels ──
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


# ──────────────────────────────────────────────────────────────────────────
# TEXTMAPWORLD (SPECIFIC ROOM) PATH RENDERER
# Unlike graph reasoning, the model here never self-reports a map — each turn
# is a bare "GO: <dir>" or "DONE". There is no claim to validate against the
# walk, so this draws only the actual explored path (no green/red
# correctness rings), plus the target room highlighted once its position is
# known from the full episode's walk (matches the "target room highlighted"
# ask — the annotator is judging the AI, not roleplaying it, so showing
# ground truth the AI may not have reached yet is consistent with how the
# graph-reasoning renderer already reveals ground truth via its rings).
# ──────────────────────────────────────────────────────────────────────────

# The GM's rules text opens with a WORKED EXAMPLE ("The target room is a
# Bedroom. You are in the Kitchen...") before the real episode — a naive
# search for "target room is X" would match the example, not the real
# answer. Anchoring on "Let us start." (which only precedes the real target)
# avoids that trap.
_SPECIFICROOM_START_RE = re.compile(
    r"Let us start\.\s*The target room is ([^.]+?)\.\s*You are in (?:the |a )?([^.]+?)\."
)


def _path_truth_and_layout(g):
    """Sibling of _map_truth_and_layout for textmapworld_specificroom.

    Same GM `move` event tracking and stable full-game grid layout, but
    resp_dir comes straight from the raw "GO: <dir>" response text (no
    graph blob to parse) and there is no self-reported starting room, so the
    start/target room names are pulled once from the opening rules text
    instead. Returns (snapshots, positions, target_room).
    """
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
    """Inline SVG of the explored path up to this turn, or None if there's
    nothing placeable yet. Rooms are drawn plain (no claim-correctness
    coloring — there's no self-report to validate); the target room gets a
    gold ring once its position is known from the full walk, even before
    the AI has actually reached it."""
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


# ──────────────────────────────────────────────────────────────────────────
# HTML BUILDERS  (pure functions of a loaded game `g`)
# ──────────────────────────────────────────────────────────────────────────

# Characters that make up ASCII board art (clean_up's box-drawing grids,
# imagegame/referencegame's ▢ grids). Both empty-cell squares are needed:
# referencegame's letter/number grids use ▢ (U+25A2) but its line/random
# grids use the near-identical □ (U+25A1) — missing it rendered those two
# variants' boards as wrapped prose.
#
# The second run of glyphs (#+|_√▓█○●) covers the test_newgames spatial
# families whose boards are plain ASCII rather than box-drawing: ta_sokoban /
# st_clean_up (# wall, _ floor, √ box-on-goal, O goal, X box, P player),
# ta_frozen_lake (+--+ / | cell frames), clockwork_courier (# . and a +--+
# border). Without these the rows counted zero grid chars and fell through to
# proportional-font prose, shredding the boards (same failure mode the ▢ set
# fixed for clean_up). Prose stays safe via the n≥4 + ratio≥0.5 guards in
# _is_grid_line — verified against QA prose, "'Cable' no." loops, arrow paths
# like (3,5) -> (3,4), and year ranges like 1804-1813. Note '-' and '.' are
# deliberately NOT here (too common in prose); board borders/rulers made of
# them are instead absorbed by _is_frame_line during block accumulation.
_GRID_CHARS = set("╔╗╚╝║═╟╢╤╧┼┤├┬┴┌┐└┘─│◌▢□" "#+|_√▓█○●")
_GRID_OBJ_RE = re.compile(r"(?<![A-Za-z])([A-Z])(?![A-Za-z])")
# Lone single digits are grid cells too — clockwork's coordinate rulers and its
# numbered pickup cells (1, 2). Adjacent digits (a multi-digit number in prose)
# are NOT matched, so "1804" stays prose while a standalone "1" counts.
_GRID_DIGIT_RE = re.compile(r"(?<!\w)\d(?!\w)")

# Wordle guess feedback arrives as 'm<red> o<yellow> e<green>' markup; matched
# AFTER html.escape (hence &lt;/&gt;), so only this exact token shape can ever
# produce markup — anything else stays escaped text. purple/black are the
# wordle-crazy variants' deliberately-scrambled key: the tiles are painted the
# LITERAL named colour and the episode's own rules text defines their meaning.
_WD_TILE_RE = re.compile(r"([A-Za-z])&lt;(red|green|yellow|purple|black)&gt;")

# The single end-of-transcript outcome banner (see _detect_outcome).
_OUTCOME_BANNERS = {
    "won":     '<div class="game-win-msg">🏆 Game Won!</div>',
    "lost":    '<div class="game-loss-msg">❌ Game Lost</div>',
    "aborted": '<div class="game-loss-msg">⚠️ Game aborted</div>',
    "ended":   '<div class="game-end-msg">🏁 Game Ended</div>',
}


def _is_grid_line(ln):
    """A real board ROW is mostly grid cells — prose that merely mentions one
    (e.g. "…must only contain the symbol '◌'.") is not. A cell is either a grid
    glyph (▢, box-drawing) OR a lone capital game-object (R/X/E, C/L/P). Counting
    the objects too is essential for imagegame: as the follower fills the board,
    rows accumulate letters and shed ▢'s, so a row like "R R ▢ ▢ ▢" has only 3
    glyphs — glyph-only counting dropped it below the threshold and rendered it
    as prose, fragmenting the grid (or de-gridding a mostly-filled board
    entirely). The density test still keeps object-mentioning sentences inline:
    prose is dominated by lowercase word-letters, pushing the ratio well under
    0.5."""
    n = (sum(1 for ch in ln if ch in _GRID_CHARS)
         + len(_GRID_OBJ_RE.findall(ln))
         + len(_GRID_DIGIT_RE.findall(ln)))
    if n < 4:
        return False
    non_space = sum(1 for ch in ln if not ch.isspace())
    return n / non_space >= 0.5


# A board's frame/ruler lines — a +---+ border, a run of ═/─, a bare column
# ruler like "123456789" — carry few grid CELLS and so fail _is_grid_line, which
# split clockwork's map into a de-gridded top border + gridded interior. Once a
# real grid run is found we absorb IMMEDIATELY-adjacent frame lines into it so
# the whole board renders as one <pre>. Kept strict (only frame glyphs / digits
# / spaces, and at least a couple of them) so ordinary prose is never absorbed.
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
    """Mark which of `lines` belong to a board. Within each maximal run of
    consecutive non-blank lines that contains at least one grid ROW, absorb the
    neighbours that either (a) are frame/ruler lines (+---+ border, digit ruler)
    or (b) share the board's width and carry board glyphs. This keeps a board
    together even when some interior rows dilute below the per-line grid ratio:
    clockwork_courier's map has dense-wall rows that grid and sparse-floor rows
    (' 2|.p#.#....|') that don't, but all rows are the same width and delimited
    the same way, so the run-plus-width rule reunites them into one <pre>.
    Ordinary prose never triggers this — a paragraph with no grid row is left
    untouched."""
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
    """Whether a fenced (```) block should render as a monospace board rather
    than wrapping prose. True when any line is a grid row (the existing rule —
    fenced boards in the current games all have grid rows), OR when the block is
    dominated by frame lines (Tower-of-Hanoi's peg diagram is all │/─ pegs and
    stacks, whose lines are too sparse to pass the per-line grid test). Ordinary
    fenced prose has long letter-dominated lines and stays wrapping."""
    if any(_is_grid_line(ln) for ln in lines):
        return True
    non_blank = [ln for ln in lines if ln.strip()]
    if len(non_blank) < 2:
        return False
    return sum(1 for ln in non_blank if _is_frame_line(ln)) >= 0.6 * len(non_blank)


def _rich_content_html(text):
    """html.escape + proper rendering for board art.

    The transcript containers use white-space:pre-line/pre-wrap with a
    proportional font, which (a) collapses runs of spaces and (b) gives every
    glyph a different width — both destroy grid alignment (clean_up's board
    was unreadable, see the pilot feedback). Fenced (```) blocks that are board-
    structured, and unfenced runs of board rows (with their +---+ borders and
    coordinate rulers absorbed), are re-emitted as an .ascii-grid <pre>; fence
    markers are dropped either way. Fenced PROSE stays ordinary wrapping text —
    freezing it into a no-wrap block forced horizontal scrolling. Everything is
    escaped in all paths.
    """
    def esc(lines):
        # escape first, THEN swap wordle letter<color> tokens for tiles —
        # operating on the escaped text keeps every other '<' inert.
        return _WD_TILE_RE.sub(r'<span class="wd-tile wd-\2">\1</span>',
                               html.escape("\n".join(lines)))

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
        # Unfenced: split into maximal board / non-board runs. Board runs render
        # as one aligned <pre>; everything else as wrapping (tile-substituted)
        # prose.
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


# Some test_newgames responses run to 10–15k chars, and several degenerate into
# a short unit repeated hundreds of times ("'Cable' no." ×250). Clamp the card
# body to a readable head + a <details> reveal, and badge the ones that looped.
_CLAMP_CHARS = 1200


def _looks_looped(text):
    """Heuristic flag for a degenerate repetition loop: the tail of the response
    reuses only a tiny vocabulary. A healthy long response keeps introducing new
    words; a loop ("'Cable' no. 'Cable' no. …") does not. Display cue only — it
    never changes what the annotator rates."""
    words = str(text).split()
    if len(words) < 60:
        return False
    tail = words[-200:]
    return len(set(tail)) <= max(6, len(tail) * 0.08)


def _render_response_body(content):
    """Turn-card body for a normal (non-map) response: rich content, clamped to
    a head + expandable tail when very long, with a loop badge when it degenerated."""
    raw = str(content)
    badge = ('<div class="turn-loop-badge">⟳ Repetition loop detected</div>'
             if _looks_looped(raw) else "")
    if len(raw) <= _CLAMP_CHARS:
        return badge + _rich_content_html(raw)
    # Split on a line boundary near the clamp so a board/fence isn't cut mid-row.
    cut = raw.rfind("\n", 0, _CLAMP_CHARS)
    if cut < _CLAMP_CHARS // 2:
        cut = _CLAMP_CHARS
    head, tail = raw[:cut], raw[cut:]
    return (
        badge
        + _rich_content_html(head)
        + '<details class="turn-longclamp"><summary>▾ Show full response '
        + f'({len(tail):,} more characters)</summary>'
        + _rich_content_html(tail)
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
    """Gold/reference answer for the single-turn QA benchmarks (mmlu_pro, bbh,
    cladder, eqbench, ifeval), logged as a GM 'target' event (or bbh's
    "Target: …" metadata) and otherwise dropped by the renderer. Gated on a
    one-turn game so multi-turn transcripts that mention a target mid-play
    (e.g. wordle's 'target_word') never surface one."""
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
    return (
        f'<div class="annot-progress">'
        f'<span class="prog-rated">{rated} of {g.n_turns} turns rated</span>'
        f'</div>'
    )


def _card_header_html(g, idx):
    sender = g.ai_turns[idx]["from"]
    role = g.role(sender)
    # Always show the sender ID (Player 1 / Player 2) as a pill.
    # Also show the role when the game has multiple distinct AI roles.
    sender_chip = f'<span class="ta-sender">{html.escape(sender)}</span>'
    role_chip = (f'<span class="ta-role">{html.escape(role)}</span>'
                 if g.multi_role else "")
    return (
        f'<div class="ta-head">'
        f'<span class="ta-badge">{idx + 1}</span>'
        f'<span class="ta-title">Turn {idx + 1} of {g.n_turns}</span>'
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

    goal_text = _rich_content_html(g.rules.strip())
    parts.append(
        f'<div class="goal-box">'
        f'<div class="goal-label">GAME GOAL</div>'
        f'<div class="goal-text">{goal_text}</div>'
        f'</div>'
    )

    # TextMapWorld (Graph Reasoning) map renderer state: ground truth as the
    # walker revealed it, per turn, plus one stable room layout — see
    # _map_truth_and_layout. Only computed in hybrid mode (pretty_map).
    map_snapshots, map_grid = ([], {})
    if pretty_map:
        map_snapshots, map_grid = _map_truth_and_layout(g)
        parts.append(_MAP_LEGEND_HTML)

    # TextMapWorld (Specific Room) path renderer state — see
    # _path_truth_and_layout. Only computed in hybrid mode (pretty_path).
    path_snapshots, path_grid, path_target = ([], {}, None)
    if pretty_path:
        path_snapshots, path_grid, path_target = _path_truth_and_layout(g)
        parts.append(_PATH_LEGEND_HTML)

    # Content already shown in the GAME GOAL box up top (g.rules) — skip a
    # verbatim repeat of it in the turn-0 body. Deliberately NOT every turn-0
    # message: some games (e.g. Deal or No Deal, Clean Up) send each player a
    # DIFFERENT context message in turn 0 — the shared rules plus that
    # player's own private info (DoND's secret value function, Clean Up's
    # board position). Matching against every turn-0 message hid the second
    # player's distinct context too, since only the first one ever gets
    # promoted to g.rules — annotators never saw what the second player knew.
    _turn0_setup = {g.rules}

    # Assign a stable colour slot (p1, p2, p3…) to each AI sender
    # in the order they first appear as a "response" message.
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
                # ── End-game bookkeeping events: consumed by _detect_outcome ──
                # (one banner rendered after the loop), never shown inline.
                # Emitting a banner per event here double-bannered clean_up /
                # adventuregame and mis-called failed games as won — see
                # _detect_outcome's docstring.
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

                # ── Skip non-string content and setup messages ──
                if not isinstance(content, str):
                    continue
                # Scoped to round 0 only: this content-equality check exists to
                # hide turn-0's setup lines (the rules prompt), but games like
                # TextMapWorld re-send an earlier round's exact text as later
                # context (e.g. a room description first appears as the
                # programmatic describer's round-0 response, then again as GM
                # context at the start of round 1) — matching it everywhere,
                # not just within round 0, silently dropped that legitimate
                # later context.
                if round_idx == 0 and content in _turn0_setup:
                    continue

                # ── Contextual GM messages shown to players ──
                if label == "context":
                    # With the map rendered on every turn card, the GM's echo
                    # of the model's own {"action", "graph"} JSON is pure
                    # noise — drop it (hybrid/pretty_map mode only).
                    if pretty_map and _parse_map_response(content):
                        continue
                    tag = "GM" if sender == "GM" else html.escape(g.role(sender))
                    # In two-player games each GM message is privately addressed —
                    # clean_up sends each player their OWN grid + move feedback —
                    # and the interleaved P1/P2 order lands a player's feedback
                    # under the OTHER player's turn card. Label the recipient so
                    # e.g. "Moved 'C' successfully" reads as Player 1's feedback,
                    # not the Player 2 card it happens to sit beneath.
                    recipient = msg.get("to")
                    if g.multi_role and recipient in g.ai_ids:
                        tag = f"{tag} → {html.escape(recipient)}"
                    parts.append(
                        f'<div class="gm-msg">'
                        f'<span class="gm-tag">{tag}</span> {_rich_content_html(content)}'
                        f'</div>'
                    )
                continue

            # Genuine AI player response → render as a turn card
            if label == "response":
                # Skip parse-error retry duplicates (see load_game). Must skip
                # WITHOUT bumping turn_counter so the tc-N card ids stay aligned
                # with ai_turns indices (the annotation cards / nav depend on it).
                if id(msg) in getattr(g, "skip_msg_ids", set()):
                    continue
                active = turn_counter == current_idx
                slot = _player_slots.get(sender, "p1")
                card_cls = f"turn-card {slot}" + (" active-turn" if active else "")

                # TextMapWorld (Graph Reasoning): draw the model's claimed map
                # as an SVG graph, validated against its own walk (green/red
                # rings, dashed ring = current position — see the renderer
                # block above). Shown in BOTH conditions — only the questions
                # differ between universal and hybrid, not the visualization.
                body_html = _render_response_body(content)
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

                # TextMapWorld (Specific Room): draw the actual explored path
                # so far (no claim to validate — the model only ever emits a
                # bare direction), with the target room highlighted gold once
                # its position is known from the full walk.
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

                # Show just the sender ID in the card header — clean and unambiguous.
                parts.append(
                    f'<div class="{card_cls}" id="{id_prefix}{turn_counter}">'
                    f'<div class="card-header">'
                    f'{html.escape(sender)}&nbsp;&nbsp;·&nbsp;&nbsp;TURN {turn_counter + 1} OF {g.n_turns}'
                    f'</div>'
                    f'<div class="card-body">{body_html}</div>'
                    f'</div>'
                )
                turn_counter += 1

    # Reference answer for the single-turn QA benchmarks — shown AFTER the
    # model's turn card (so the annotator reads the response first), styled as
    # reference material rather than part of the episode.
    ref = _reference_answer(g)
    if ref:
        parts.append(
            '<div class="ref-answer">'
            '<div class="ref-answer-label">✓ REFERENCE ANSWER</div>'
            f'<div class="ref-answer-body">{ref}</div>'
            '</div>'
        )

    # Exactly ONE outcome banner, computed by _detect_outcome — never emitted
    # inline per event (see the _END_TYPES comment above).
    parts.append(_OUTCOME_BANNERS[getattr(g, "outcome", "ended")])

    return f'<div class="{scroll_cls}">' + "".join(parts) + "</div>"


# ──────────────────────────────────────────────────────────────────────────
# SUBMIT  (closure over the active game + the field layout that render built)
# ──────────────────────────────────────────────────────────────────────────

def _submit(g, field_specs, condition, show_q3, annotator_id, started_at, session_day,
            session_started_at, *vals):
    # field_specs[i] describes what vals[i] holds: (turn_index, "q1"/"q2"/"q3"
    # /"flags"/"comment") or (turn_index, "extra", key) for a bespoke bolt-on.
    # Built dynamically per-render since hybrid mode renders a different shape
    # of widgets per game/role — see build().

    # Every rendered question (Q1/Q2/Q3 and bespoke bolt-ons) is mandatory;
    # only flags and the free-text comment may be left empty. The turn chips
    # in the nav only go green once a turn is complete, so listing the turn
    # numbers here is enough for the annotator to find the gaps.
    incomplete = sorted({spec[0] for spec, val in zip(field_specs, vals)
                         if spec[1] in ("q1", "q2", "q3", "extra") and not val})
    if incomplete:
        turns = ", ".join(str(i + 1) for i in incomplete)
        return (
            f"⚠️ **Not submitted.** Every question must be answered — "
            f"missing answers on turn{'s' if len(incomplete) > 1 else ''} "
            f"{turns}. (Flags and comments are optional.)",
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

    db.save_turns(g.slug, g.meta, g.source_path, g.has_reasoning,
                  annotator_id, condition, turns_out,
                  started_at=started_at or None, session_day=session_day or None,
                  session_started_at=session_started_at or None)
    return "✅ Saved!", gr.update(visible=False), gr.update(visible=True)


# ──────────────────────────────────────────────────────────────────────────
# BUILD
# ──────────────────────────────────────────────────────────────────────────

def build(welcome_page, annotation_page, verdict_page, game_state, annotator_state,
          block_state, playlist_state, playlist_idx_state, started_at_state,
          session_day_state, session_started_at_state, clearing_state):
    with annotation_page:

        # The whole page body re-renders whenever `game` or `block` change
        # (i.e. on page load, once the URL params are parsed) — this is what
        # makes game loading URL-driven instead of the old static DEFAULT_GAME
        # build. app.py's MutationObserver already re-initialises the JS turn
        # navigator/progress counter whenever this swaps the cards.
        @gr.render(inputs=[game_state, block_state, playlist_state,
                           playlist_idx_state, clearing_state])
        def _render_annotation(path, block, playlist, playlist_idx, clearing):
            # Blank intermediate render: while clearing_state is True (the
            # first half of a game switch — see app.py where the state is
            # defined) render NOTHING so every widget fully unmounts before
            # the next game's widgets mount. This is what prevents Gradio
            # from carrying user-entered values across games.
            if clearing or not path:
                return
            g = load_game(path)
            block_type = BLOCK_TO_TYPE.get(block, "universal")
            bespoke = BESPOKE_QUESTIONS.get(g.game_key) if block_type == "hybrid" else None
            # Q3 (Reasoning Clarity) gating by condition:
            #  - universal ("general"): only where the AI actually explains its
            #    reasoning (g.has_reasoning) — i.e. where it can be answered.
            #  - hybrid ("mix"): only when the game's bespoke set opts in via a
            #    "reasoning_clarity" flag. None do today, so Q3 is dropped from the
            #    hybrid condition (it was never in those bespoke question lists).
            show_q3 = (bool(bespoke.get("reasoning_clarity"))
                       if bespoke is not None else g.has_reasoning)
            # The claimed-map renderer shows in BOTH conditions (team call,
            # 2026-07-06): raw graph JSON is unreadable no matter which
            # question set you're answering, so only the QUESTIONS differ
            # between universal and hybrid — not the visualization.
            pretty_map = bool(BESPOKE_QUESTIONS.get(g.game_key, {}).get("render_graph"))
            pretty_path = bool(BESPOKE_QUESTIONS.get(g.game_key, {}).get("render_path"))

            # Playlist sessions show where the annotator is in today's queue.
            seq_chip = ""
            if playlist:
                seq_chip = (f'<span class="game-seq-tag">Game {playlist_idx + 1} '
                            f'of {len(playlist)}</span>')

            # NAV BAR
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

            # MAIN LAYOUT
            with gr.Row(equal_height=False, elem_classes=["anno-main-row"]):

                # LEFT: scrollable transcript
                with gr.Column(scale=3, elem_classes=["tx-col"]):
                    gr.HTML(_build_transcript_html(g, 0, pretty_map=pretty_map,
                                                   pretty_path=pretty_path))

                # RIGHT: per-turn annotation cards.
                # NO key= on anything created inside this @gr.render: keyed
                # blocks go through Gradio 6's key_to_id_map, which reuses
                # block ids across render passes and intermittently raises
                # DuplicateBlockError ("A block with id N has already been
                # rendered") when the same game re-renders — seen twice in
                # real sessions, always at a keyed component. The keys also
                # buy nothing: they provably do NOT stop the cross-game value
                # leak (that fix is the clearing_state blank render, see the
                # top of _render_annotation), so unkeyed fresh-id components
                # are strictly safer.
                with gr.Column(scale=2, elem_id="annot-col"):
                    gr.HTML(_turn_nav_html(g))

                    # Flat, ordered list of every input component created below,
                    # paired 1:1 with field_specs so _submit can reconstruct
                    # per-turn answers regardless of which widgets a given
                    # turn/role/game actually got (see _submit docstring note).
                    components, field_specs = [], []

                    for i in range(g.n_turns):
                        sender = g.ai_turns[i]["from"]
                        role = g.role(sender)
                        role_cfg = (bespoke or {}).get("roles", {}).get(role, {})

                        with gr.Group(elem_classes=["turn-anno-card"]):
                            gr.HTML(_card_header_html(g, i))

                            # Q1 slot: "generic" (default) = universal widget,
                            # None = not rendered for this role, tuple = bespoke.
                            q1_cfg = role_cfg.get("q1", "generic")
                            if q1_cfg == "generic":
                                gr.Markdown("**Q1 — Prior Information Use**\n\nDid the AI correctly use information from earlier in the game?")
                                q1 = gr.Radio(
                                    choices=[("1\nNone", "1"), ("2\nPartial", "2"), ("3\nGood", "3"), ("4\nExcellent", "4")],
                                    show_label=False, elem_classes=["scale-radio", "q1-scale"],
                                )
                                components.append(q1); field_specs.append((i, "q1"))
                            elif q1_cfg is not None:
                                label_md, choices = q1_cfg
                                gr.Markdown(label_md)
                                q1 = gr.Radio(choices=choices, show_label=False,
                                              elem_classes=["scale-radio", "q1-scale"])
                                components.append(q1); field_specs.append((i, "q1"))

                            # Q2 slot — same generic/None/bespoke pattern.
                            q2_cfg = role_cfg.get("q2", "generic")
                            if q2_cfg == "generic":
                                gr.Markdown("**Q2 — Sensible Next Step**\n\nDid this move make sense as a next step?")
                                q2 = gr.Radio(
                                    choices=[("1\nNonsensical", "1"), ("2\nPoor", "2"), ("3\nReasonable", "3"), ("4\nStrong", "4")],
                                    show_label=False, elem_classes=["scale-radio", "q2-scale"],
                                )
                                components.append(q2); field_specs.append((i, "q2"))
                            elif q2_cfg is not None:
                                label_md, choices = q2_cfg
                                gr.Markdown(label_md)
                                q2 = gr.Radio(choices=choices, show_label=False,
                                              elem_classes=["scale-radio", "q2-scale"])
                                components.append(q2); field_specs.append((i, "q2"))

                            # Bespoke bolt-on(s) — additive, beyond the Q1/Q2 slots.
                            for key, label_md, choices in role_cfg.get("bolt_ons", []):
                                gr.Markdown(label_md)
                                bolt = gr.Radio(choices=choices, show_label=False,
                                                elem_classes=["scale-radio"])
                                components.append(bolt); field_specs.append((i, "extra", key))

                            # Q3 — shown per show_q3 (condition-gated above). When
                            # hidden it's a preset-N/A invisible radio so field_specs
                            # / submit / the JS rated-counter all stay aligned.
                            if show_q3:
                                gr.Markdown("**Q3 — Reasoning Clarity** · conditional\n\nHow clearly does the AI explain its move?")
                                q3 = gr.Radio(
                                    choices=[("1\nUnclear", "1"), ("2\nConfused", "2"), ("3\nClear", "3"), ("4\nTransparent", "4"), ("N/A", "NA")],
                                    show_label=False, elem_classes=["scale-radio", "q3-scale"],
                                )
                            else:
                                q3 = gr.Radio(choices=[("N/A", "NA")], value="NA", visible=False,
                                              show_label=False)
                            components.append(q3); field_specs.append((i, "q3"))

                            # Flags: a bespoke "flags" LIST overrides the generic set;
                            # an explicit [] means "no flags for this game" (imagegame),
                            # so the CheckboxGroup is skipped entirely. None/absent
                            # (e.g. codenames) falls back to the generic set.
                            _bf = bespoke.get("flags") if bespoke else None
                            flag_choices = _bf if isinstance(_bf, list) else g.flag_choices
                            if flag_choices:
                                gr.HTML('<div class="flags-lbl">Flags <span class="flags-sub">— tick all that apply</span></div>')
                                fl = gr.CheckboxGroup(
                                    choices=flag_choices, show_label=False,
                                    elem_classes=["flags-check"],
                                )
                                components.append(fl); field_specs.append((i, "flags"))

                            cm = gr.Textbox(
                                placeholder="Optional turn comment…",
                                show_label=False, lines=1,
                                elem_classes=["turn-comment"],
                            )
                            components.append(cm); field_specs.append((i, "comment"))

                    status = gr.Markdown("")
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

