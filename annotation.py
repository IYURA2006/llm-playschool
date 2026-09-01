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
# Re-exported here because other modules read them off annotation.
from questions import (BESPOKE_QUESTIONS, GENERIC_Q1, GENERIC_Q2,
                       GENERIC_Q3, SC_TICKS, SC_TICK_D)

_dir = os.path.dirname(os.path.abspath(__file__))
# GAMES_DIR picks the tree to serve: games_study/ for the study, games/ for the
# pilot pool. game_key reads the 4th-from-last path part, so the study tree's
# extra model level needs no other change.
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

# Fall back to the first game found if the usual default is missing.
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


# A decode table, not an allow-list. The day1_*/day2_* names are retired pilot
# conditions, kept so old rows still decode. Removing one would not be harmless:
# lookups default to "universal", so its rows would quietly change meaning.
BLOCK_TO_TYPE = {
    "day1_universal": "universal",
    "day1_hybrid": "hybrid",
    "day2_mixed": "hybrid",
    "universal": "universal",
    "hybrid": "hybrid",
}

# What a session URL may ask for — smaller than the table above, which is
# decode-only. app.py checks ?block= against this.
VALID_BLOCKS = {"universal", "hybrid"}


def plain_label(md):
    """Markdown question text -> a flat string for a control's accessible name.

    Every question is "**Title**\\n\\nBody", so one pass handles them all and the
    name always matches the visible text. The label is sr-only, so nothing
    changes on screen.
    """
    s = re.sub(r"[*_`#]", "", md or "")
    s = s.replace("\n\n", " — ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()


def _wg_id(md):
    """Fallback identifier for a whole-game question that has no explicit one:
    a short digest of its text. Stable under REORDERING (which is the failure
    that matters), though not under rewording — hence the explicit ids on the
    study games in questions.py."""
    flat = re.sub(r"\s+", " ", re.sub(r"[*_`#]", "", md or "")).strip().lower()
    return "q" + hashlib.sha256(flat.encode()).hexdigest()[:10]


def normalise_whole_game(entries):
    """Accept (question, choices) or (id, question, choices); always return the
    3-tuple form.

    Answers used to be stored by position, so reordering a game's whole-game
    list silently swapped every stored answer. Keying by id removes that risk.
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
    # Top-level Success/Lose/Aborted flags win when present. Other games fall
    # through to the per-event scan below.
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
    # codenames: "opponent has won" is a LOSS, so check the opponent first.
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
    # taboo/guesswhat: a 'correct guess' event is only logged on a real match.
    if first("correct guess"):
        return "won"
    # Aborted: an explicit abort, or the transcript cutting off mid-reply.
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

    def _is_ai_player(pid):
        info = players.get(pid, {})
        if pid == "GM":
            return False
        model = (info.get("model_name") or "").lower()
        return model != "programmatic" and model != ""

    ai_ids = {pid for pid in players if _is_ai_player(pid)}

    def _role(pid):
        return players.get(pid, {}).get("game_role", pid)

    # Some games put a metadata dict first, so turns[0][0] is not always the
    # rules text. Find the first GM message that looks like an instruction.
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

    # A format retry logs both the rejected and the accepted response. Keep the
    # fuller original and drop the retry.
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

    # Q3 only makes sense when the AI actually explains itself.
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
    # Not rendered as turn cards. Must match the ai_turns filter above, or the
    # transcript's turn numbering drifts from the ai_turns indices.
    g.skip_msg_ids = skip_ids
    # Dond's prose rarely trips the marker-word check, so it is listed by name.
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
    # Edges: solid grey where the walk confirmed the claim, dashed red where the
    # model claims a connection it never actually saw.
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


# Path renderer for specific-room. The model only emits "GO: <dir>"/"DONE" and
# never reports a map, so this draws the path with no correctness rings.

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


# Characters that make up ASCII board art. '-' and '.' are left out: they are
# far too common in ordinary prose.
_GRID_CHARS = set("╔╗╚╝║═╟╢╤╧┼┤├┬┴┌┐└┘─│◌▢□" "#+|_√▓█○●")
_GRID_OBJ_RE = re.compile(r"(?<![A-Za-z])([A-Z])(?![A-Za-z])")
# A lone digit is a grid cell; a multi-digit number like "1804" is prose.
_GRID_DIGIT_RE = re.compile(r"(?<!\w)\d(?!\w)")

# Matched after html.escape (hence &lt;/&gt;), so only this exact token shape
# turns into markup — everything else stays plain escaped text.
_WD_TILE_RE = re.compile(r"([A-Za-z])&lt;(red|green|yellow|purple|black)&gt;")

# What each feedback colour means, per game family. The crazy variants scramble
# the key, so the same colour name means different things:
#
#     standard wordle*      green=correct   yellow=wrong-pos  red=absent
#     wordle-crazy*         yellow=correct  black=wrong-pos   purple=absent
#
# Never use one global map. Showing a crazy transcript with standard meanings
# would tell the annotator the model was right when it was backwards.
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

    ImageGame asks whether an instruction fits what has been built so far. That
    means seeing what this turn changed, which is hard by eye unless it is
    marked.
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
    """html.escape, plus monospace rendering for board art.

    The proportional font breaks grid alignment, so board-shaped blocks are
    re-emitted as a monospace <pre>. Ordinary prose stays as wrapping text.
    `family` picks the wordle colour legend; without it, tiles show the colour
    name only, never a meaning that could be wrong.
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


# Responses can run to 15k chars; clamp to a head plus an expandable tail.
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
    # aria-live works here because this node is created once and only has its
    # text rewritten. The head script debounces that, so a burst of clicks is
    # announced once rather than once per radio.
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
    # clashing with the annotation page's JS, which targets those names globally.
    parts = []
    turn_counter = 0  # index over AI-player response turns only
    _prev_grid = None  # ImageGame: last grid seen, for the per-turn diff

    goal_text = _rich_content_html(g.rules.strip(), g.game_key)
    parts.append(
        f'<div class="goal-box">'
        f'<h2 class="goal-label">GAME GOAL</h2>'
        f'<div class="goal-text">{goal_text}</div>'
        # Up front, because the crazy variants scramble the colours and an
        # annotator using Wordle intuition would read the feedback backwards.
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

    # Skip a repeat of the rules already shown in the GAME GOAL box. Only
    # g.rules itself: some games also send private per-player info in turn 0.
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

                # ImageGame: the Follower replies with the whole grid, so mark
                # this turn's changes against the last grid any turn produced.
                if g.game_key == "imagegame":
                    grid = parse_cell_grid(content)
                    if grid:
                        # The reply IS the grid, so replace the body rather
                        # than adding to it, or the grid appears twice.
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

    # One banner only; per-event end markers were dropped above.
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
    """{role: index of that role's first turn}, for bolt_ons_first_turn."""
    first = {}
    for i, msg in enumerate(g.ai_turns):
        first.setdefault(g.role(msg["from"]), i)
    return first


def question_spec(g, condition, show_q3):
    """The exact question set a transcript was shown.

    Per transcript, not per family: roles, whether Q3 is shown, and the flag
    list all vary between transcripts.

    Hashed and stored when the data is collected, so a later export can decode
    answers against the questions that were really on screen. Without it,
    editing the questions quietly changes what old answers mean.
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
    """Fail loudly if the spec disagrees with what was rendered.

    field_specs comes from the render loop, so it cannot drift. The spec is
    built separately. If they disagree, storing the spec's hash would attach a
    wrong description to real data.
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
        # q3 is always here, even when the spec records it as None. The render
        # loop always emits a q3 component (hidden and preset to N/A when it is
        # not asked), so field_specs stays 1:1 with the component list.
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

    # Every rendered question is required; flags and comments are optional.
    incomplete = sorted({spec[0] for spec, val in zip(field_specs, vals)
                         if spec[1] in ("q1", "q2", "q3", "extra") and not val})
    if incomplete:
        turns = ", ".join(str(i + 1) for i in incomplete)
        # This span tells the a11y module in app.py to reveal the hidden turn
        # card and focus the first unanswered option. The module reads the turn
        # numbers out of the visible sentence, because Gradio's sanitiser strips
        # data-* attributes. Keep the "on turn/turns <n>, <n>." wording in step
        # with the regex there.
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

    # Fingerprint the question set this submission was collected under. Checked
    # against field_specs first, which is what was really rendered — storing a
    # spec that disagrees would label the data wrongly and permanently.
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
            # Render nothing while clearing, so every widget unmounts before the
            # next game mounts. This is what stops answers carrying over.
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

            # The screen had no heading, and this is also the a11y focus target.
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
                # Without this the page is a dead end: nothing to submit, no way back.
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

                            # Some roles are not rated at all (GuessWhat's
                            # Answerer only replies yes/no). Say so, instead of
                            # showing a card that looks broken.
                            if (role_cfg.get("q1", "generic") is None
                                    and role_cfg.get("q2", "generic") is None
                                    and not role_cfg.get("bolt_ons")
                                    and not (first_turn.get(role) == i
                                             and role_cfg.get("bolt_ons_first_turn"))
                                    and not show_q3):
                                gr.HTML(
                                    '<p class="no-q-note">This turn is not '
                                    'rated — it is shown for context only. '
                                    'Move on to the next turn.</p>'
                                )

                            # Q1 slot: "generic" = the shared question, None =
                            # not asked of this role, tuple = this game's own.
                            # Labels are turn-qualified because one page holds
                            # many identical groups.
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

                            # Extra questions beyond Q1/Q2. The second list is
                            # asked only on this role's first turn.
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

