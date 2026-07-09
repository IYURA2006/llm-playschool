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
    """Stable, filesystem-free identifier for a game transcript."""
    rel = os.path.relpath(game_path, _games_dir)
    return rel.replace(os.sep, "__").replace("interactions.json", "").strip("_")


_SLUG_TO_PATH = {game_slug(path): path for _, path in GAMES}


def slug_to_path(slug):
    """Resolve a `game` URL param to its transcript path, or None if unknown."""
    return _SLUG_TO_PATH.get(slug)


# Pilot condition config: which block maps to which question-set mode.
# The day1_*/day2_* names are the original one-link-per-game blocks; the bare
# "universal"/"hybrid" values are what playlist items (assignments.json) carry.
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


# Hybrid-mode bespoke per-turn question sets, wired up only when block_type ==
# "hybrid" (see BLOCK_TO_TYPE). Wording/scales sourced from question_set.md
# (the pilot's design doc) — each entry replaces the universal Q1/Q2 for the
# listed game_role(s); a role/slot not listed here falls back to "generic"
# (the same widget universal mode uses). Games not listed here get no
# branching at all in hybrid mode (matches plan.md: Wordle/Dond need none).
BESPOKE_QUESTIONS = {
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
}


def whole_game_questions(game_path, block):
    """Bespoke whole-game "specific overall" questions for a game as a list of
    (question_markdown, choices), or [] when there are none.

    Returned ONLY in the hybrid condition — mirrors the per-turn bespoke gating
    (BLOCK_TO_TYPE): the universal condition keeps just the generic Coherence +
    Overall verdict questions, so the whole-game A/B addition is hybrid-only.
    """
    if BLOCK_TO_TYPE.get(block, "universal") != "hybrid":
        return []
    game_key = game_slug(game_path).split("__", 1)[0]
    return BESPOKE_QUESTIONS.get(game_key, {}).get("whole_game") or []


def whole_game_only(game_path, block):
    """True when the verdict should show ONLY the game-specific whole-game
    questions (as 1-7 sliders) and hide the generic Coherence + Overall pair.
    Set per game via BESPOKE_QUESTIONS[...]["whole_game_only"]; hybrid-only,
    and only meaningful when whole_game_questions() is non-empty (imagegame:
    a two-role game where a single "overall quality" score doesn't fit)."""
    if BLOCK_TO_TYPE.get(block, "universal") != "hybrid":
        return False
    game_key = game_slug(game_path).split("__", 1)[0]
    return bool(BESPOKE_QUESTIONS.get(game_key, {}).get("whole_game_only"))


def output_path_for(game_path):
    """Stable per-game annotation output file in interactions/."""
    return os.path.join(_dir, "interactions", f"annot__{game_slug(game_path)}.json")


class _Game:
    """Lightweight container for everything a screen needs about one game."""


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
    # Response messages to NOT render as turn cards — kept in sync with the
    # ai_turns filter above so transcript turn_counter matches ai_turns indices.
    g.skip_msg_ids = skip_ids
    # question_set.md fixes Q3's trigger by GAME ("Shown when: Game requires
    # an explanation (Wordle family, Dond)") — the marker heuristic alone
    # misses Dond, whose negotiation prose explains itself without the exact
    # marker words (1 of 4 turns hit; threshold needs half). Game list first,
    # heuristic as the catch-all for anything else that visibly reasons.
    game_key = os.path.relpath(path, _games_dir).split(os.sep)[0]
    _REASONING_GAMES = {"wordle", "wordle_withclue", "wordle_withcritic", "dond"}
    g.has_reasoning = game_key in _REASONING_GAMES or _detect_reasoning(ai_turns)
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
# HTML BUILDERS  (pure functions of a loaded game `g`)
# ──────────────────────────────────────────────────────────────────────────

# Characters that make up ASCII board art (clean_up's box-drawing grids,
# imagegame/referencegame's ▢ grids).
_GRID_CHARS = set("╔╗╚╝║═╟╢╤╧┼┤├┬┴┌┐└┘─│◌▢")
_GRID_OBJ_RE = re.compile(r"(?<![A-Za-z])([A-Z])(?![A-Za-z])")


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
    n = sum(1 for ch in ln if ch in _GRID_CHARS) + len(_GRID_OBJ_RE.findall(ln))
    if n < 4:
        return False
    non_space = sum(1 for ch in ln if not ch.isspace())
    return n / non_space >= 0.5


def _grid_block(lines):
    """One aligned, monospace board block; single capital letters (the game
    objects annotators must track — C/L/P, X/R…) get a highlight span."""
    body = _GRID_OBJ_RE.sub(r'<span class="grid-obj">\1</span>',
                            html.escape("\n".join(lines)))
    return f'<pre class="ascii-grid">{body}</pre>'


def _rich_content_html(text):
    """html.escape + proper rendering for board art.

    The transcript containers use white-space:pre-line/pre-wrap with a
    proportional font, which (a) collapses runs of spaces and (b) gives every
    glyph a different width — both destroy grid alignment (clean_up's board
    was unreadable, see the pilot feedback). Fenced (```) blocks that contain
    board rows, and unfenced runs of board rows, are re-emitted as an
    .ascii-grid <pre>; fence markers are dropped either way. Fenced PROSE
    stays ordinary wrapping text — freezing it into a no-wrap block forced
    horizontal scrolling. Everything is escaped in all paths.
    """
    parts, plain, block = [], [], []
    in_fence = False

    def flush_plain():
        if plain:
            parts.append(html.escape("\n".join(plain)))
            plain.clear()

    def flush_block():
        if block:
            if any(_is_grid_line(ln) for ln in block):
                parts.append(_grid_block(block))
            else:
                parts.append(html.escape("\n".join(block)))
            block.clear()

    for ln in str(text).split("\n"):
        if ln.strip().startswith("```"):
            flush_block() if in_fence else flush_plain()
            in_fence = not in_fence
            continue
        if in_fence or _is_grid_line(ln):
            if not in_fence and plain:
                flush_plain()
            block.append(ln)
        else:
            flush_block()
            plain.append(ln)
    flush_plain()
    flush_block()
    return "".join(parts)


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


def _build_transcript_html(g, current_idx, pretty_map=False,
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

    # messages from turn 0 that are pure setup (the long rules prompt) — skip in body
    _turn0_setup = set()
    for m in g.data["turns"][0]:
        c = m["action"].get("content")
        if isinstance(c, str):
            _turn0_setup.add(c)

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
            atype = action["type"]
            label = action.get("label", "")
            content = action.get("content", "")

            # GM / environment messages
            if sender == "GM" or sender not in g.ai_ids:
                # ── End-game banners (checked BEFORE _turn0_setup filter) ──────
                # Covers every end-game action type found across all game formats.
                _END_TYPES = {
                    "game_finished", "game end", "game_result",
                    "adventure_finished", "end", "successful agreement", "aborted",
                    "stop", "info",
                }
                if atype in _END_TYPES:
                    cl = str(content).strip().lower()
                    if "win" in cl or "success" in cl or "agreement" in atype:
                        parts.append('<div class="game-win-msg">🏆 Game Won!</div>')
                    elif "loss" in cl or "lose" in cl or "fail" in cl or "abort" in cl:
                        parts.append('<div class="game-loss-msg">❌ Game Lost</div>')
                    else:
                        parts.append('<div class="game-end-msg">🏁 Game Ended</div>')
                    continue

                if atype == "success" and isinstance(content, str):
                    c_val = content.strip().lower()
                    if c_val == "true":
                        parts.append('<div class="game-win-msg">🏆 Game Won!</div>')
                    elif c_val == "false":
                        parts.append('<div class="game-loss-msg">❌ Game Lost</div>')
                    continue

                if atype == "correct guess" and isinstance(content, str):
                    cl = content.strip().lower()
                    if content == "end game":
                        parts.append('<div class="game-end-msg">🏁 Game Ended</div>')
                    elif "win" in cl:
                        parts.append('<div class="game-win-msg">🏆 Game Won!</div>')
                    elif "loss" in cl or "lose" in cl or "fail" in cl:
                        parts.append('<div class="game-loss-msg">❌ Game Lost</div>')
                    else:
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
                body_html = _rich_content_html(str(content))
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
            game_key = g.slug.split("__", 1)[0]
            bespoke = BESPOKE_QUESTIONS.get(game_key) if block_type == "hybrid" else None
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
            pretty_map = bool(BESPOKE_QUESTIONS.get(game_key, {}).get("render_graph"))

            # Playlist sessions show where the annotator is in today's queue.
            seq_chip = ""
            if playlist:
                seq_chip = (f'<span class="game-seq-tag">Game {playlist_idx + 1} '
                            f'of {len(playlist)}</span>')

            # NAV BAR
            with gr.Row(elem_classes=["annot-topnav"]):
                gr.HTML(
                    f'<div class="nav-left">'
                    f'<span class="game-id-tag">#{g.meta["game_id"]}</span>'
                    f'<span class="game-name-tag">{g.meta["game_name"].title()}</span>'
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
                    gr.HTML(_build_transcript_html(g, 0, pretty_map=pretty_map))

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

