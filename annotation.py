import gradio as gr
import functools
import glob
import html
import json
import os

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
            "Guesser ignored its own previous wrong guess",
            "Describer repeated the same clue",
        ],
        "roles": {
            "WordDescriber": {
                "q1": (
                    "**Q1 — Clue Clarity**\n\nWas this clue clear enough to "
                    "guess from, without a forbidden word?",
                    _scale4(["Unclear", "Vague", "Mostly clear", "Fully clear"]),
                ),
                "q2": None,
            },
            "WordGuesser": {
                "q1": None,
                "q2": (
                    "**Q2 — Guess Match**\n\nDid the guess match what the clue "
                    "was pointing to?",
                    _scale4(["No match", "Weak match", "Good match", "Strong match"]),
                ),
            },
        },
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
        "flags": [
            "Spatial Hallucination / Invalid Move",
            "Successful Self-Correction",
            "Final Map Matches Own Movement History",
        ],
        "render_graph": True,
        "roles": {
            "PathGuesser": {
                "q1": (
                    "**Q1 — Map Self-Consistency**\n\nLooking only at the map "
                    "the AI has drawn so far — does its move make sense, even "
                    "if the map itself turns out to be wrong? *You are not "
                    "checking if the AI is right, only if it is consistent "
                    "with what it believes.*",
                    [
                        ("1\nMakes no sense", "1"),
                        ("2\nDoesn't quite fit", "2"),
                        ("3\nMostly makes sense", "3"),
                        ("4\nMakes perfect sense", "4"),
                    ],
                ),
                "q2": None,
            },
        },
    },
}


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

    # ── Collect AI turns (only genuine AI players, in transcript order) ──
    ai_turns = []
    for turn in turns:
        for msg in turn:
            if msg["from"] in ai_ids and msg["action"].get("label") == "response":
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
    g.has_reasoning = _detect_reasoning(ai_turns)
    g.multi_role = len(ai_ids) > 1
    g.flag_choices = [
        "Repeated a previous failed move",
        "Invented or misquoted a game fact",
        "Self-corrected after error",
    ]
    if g.has_reasoning:
        g.flag_choices.append("Reasoning-Action Mismatch")
    g.slug = game_slug(path)
    g.source_path = os.path.relpath(path, _dir)
    return g


# ──────────────────────────────────────────────────────────────────────────
# HTML BUILDERS  (pure functions of a loaded game `g`)
# ──────────────────────────────────────────────────────────────────────────

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

    goal_text = html.escape(g.rules.strip())
    parts.append(
        f'<div class="goal-box">'
        f'<div class="goal-label">GAME GOAL</div>'
        f'<div class="goal-text">{goal_text}</div>'
        f'</div>'
    )

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

    for round_msgs in g.data["turns"]:
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
                if content in _turn0_setup:
                    continue

                # ── Contextual GM messages shown to players ──
                if label == "context":
                    tag = "GM" if sender == "GM" else html.escape(g.role(sender))
                    parts.append(
                        f'<div class="gm-msg">'
                        f'<span class="gm-tag">{tag}</span> {html.escape(content)}'
                        f'</div>'
                    )
                continue

            # Genuine AI player response → render as a turn card
            if label == "response":
                active = turn_counter == current_idx
                slot = _player_slots.get(sender, "p1")
                card_cls = f"turn-card {slot}" + (" active-turn" if active else "")

                # Hybrid-mode TextMapWorld (Graph Reasoning): the AI's raw content
                # is already `{"action": ..., "graph": {...}}` — pretty-print the
                # claimed map instead of showing the compact JSON blob verbatim.
                # Universal mode deliberately skips this (see BESPOKE_QUESTIONS
                # docstring) so the mismatch with generic questions is visible.
                body_html = html.escape(str(content))
                if pretty_map and isinstance(content, str):
                    try:
                        parsed = json.loads(content)
                    except ValueError:
                        parsed = None
                    if isinstance(parsed, dict) and "graph" in parsed:
                        action_txt = html.escape(str(parsed.get("action", "")))
                        graph_txt = html.escape(json.dumps(parsed["graph"], indent=2))
                        body_html = (
                            f'<div><strong>Action:</strong> {action_txt}</div>'
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

def _submit(g, field_specs, condition, annotator_id, started_at, session_day,
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
            "reasoning_clarity": t["q3"] if g.has_reasoning else None,
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
          session_day_state, session_started_at_state):
    with annotation_page:

        # The whole page body re-renders whenever `game` or `block` change
        # (i.e. on page load, once the URL params are parsed) — this is what
        # makes game loading URL-driven instead of the old static DEFAULT_GAME
        # build. app.py's MutationObserver already re-initialises the JS turn
        # navigator/progress counter whenever this swaps the cards.
        @gr.render(inputs=[game_state, block_state, playlist_state, playlist_idx_state])
        def _render_annotation(path, block, playlist, playlist_idx):
            g = load_game(path)
            block_type = BLOCK_TO_TYPE.get(block, "universal")
            game_key = g.slug.split("__", 1)[0]
            bespoke = BESPOKE_QUESTIONS.get(game_key) if block_type == "hybrid" else None
            pretty_map = bool(bespoke and bespoke.get("render_graph"))

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
                return

            # MAIN LAYOUT
            with gr.Row(equal_height=False, elem_classes=["anno-main-row"]):

                # LEFT: scrollable transcript
                with gr.Column(scale=3, elem_classes=["tx-col"]):
                    gr.HTML(_build_transcript_html(g, 0, pretty_map=pretty_map))

                # RIGHT: per-turn annotation cards
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
                                gr.Markdown("**Q1 — Prior Information Use**\n\nDid the AI correctly use information established in earlier turns?")
                                q1 = gr.Radio(
                                    choices=[("1\nNone", "1"), ("2\nPartial", "2"), ("3\nGood", "3"), ("4\nExcellent", "4")],
                                    show_label=False, elem_classes=["scale-radio", "q1-scale"],
                                )
                                components.append(q1); field_specs.append((i, "q1"))
                            elif q1_cfg is not None:
                                label_md, choices = q1_cfg
                                gr.Markdown(label_md)
                                q1 = gr.Radio(choices=choices, show_label=False, elem_classes=["scale-radio", "q1-scale"])
                                components.append(q1); field_specs.append((i, "q1"))

                            # Q2 slot — same generic/None/bespoke pattern.
                            q2_cfg = role_cfg.get("q2", "generic")
                            if q2_cfg == "generic":
                                gr.Markdown("**Q2 — Strategic Logic**\n\nRegardless of constraints, did this move make strategic sense?")
                                q2 = gr.Radio(
                                    choices=[("1\nNonsensical", "1"), ("2\nPoor", "2"), ("3\nReasonable", "3"), ("4\nStrong", "4")],
                                    show_label=False, elem_classes=["scale-radio", "q2-scale"],
                                )
                                components.append(q2); field_specs.append((i, "q2"))
                            elif q2_cfg is not None:
                                label_md, choices = q2_cfg
                                gr.Markdown(label_md)
                                q2 = gr.Radio(choices=choices, show_label=False, elem_classes=["scale-radio", "q2-scale"])
                                components.append(q2); field_specs.append((i, "q2"))

                            # Bespoke bolt-on(s) — additive, beyond the Q1/Q2 slots.
                            for key, label_md, choices in role_cfg.get("bolt_ons", []):
                                gr.Markdown(label_md)
                                bolt = gr.Radio(choices=choices, show_label=False, elem_classes=["scale-radio"])
                                components.append(bolt); field_specs.append((i, "extra", key))

                            # Q3 — unchanged, existing conditional pattern (reused,
                            # not duplicated, regardless of universal/hybrid).
                            if g.has_reasoning:
                                gr.Markdown("**Q3 — Reasoning Clarity** · conditional")
                                q3 = gr.Radio(
                                    choices=[("1\nUnclear", "1"), ("2\nConfused", "2"), ("3\nClear", "3"), ("4\nTransparent", "4"), ("N/A", "NA")],
                                    show_label=False, elem_classes=["scale-radio", "q3-scale"],
                                )
                            else:
                                q3 = gr.Radio(choices=[("N/A", "NA")], value="NA", visible=False, show_label=False)
                            components.append(q3); field_specs.append((i, "q3"))

                            flag_choices = (bespoke.get("flags") if bespoke and bespoke.get("flags") else g.flag_choices)
                            gr.HTML('<div class="flags-lbl">Flags <span class="flags-sub">— tick all that apply</span></div>')
                            fl = gr.CheckboxGroup(
                                choices=flag_choices, show_label=False,
                                elem_classes=["flags-check"],
                            )
                            components.append(fl); field_specs.append((i, "flags"))

                            cm = gr.Textbox(
                                placeholder="Optional turn comment…",
                                show_label=False, lines=2,
                                elem_classes=["turn-comment"],
                            )
                            components.append(cm); field_specs.append((i, "comment"))

                    status = gr.Markdown("")
                    with gr.Row():
                        back_btn = gr.Button("← Back", variant="secondary")
                        submit_btn = gr.Button("Submit All", variant="primary")

            # EVENTS — wired inside @gr.render since `components` is rebuilt
            # fresh on every game/block change.
            submit_btn.click(
                fn=functools.partial(_submit, g, field_specs, block),
                inputs=[annotator_state, started_at_state, session_day_state,
                        session_started_at_state, *components],
                outputs=[status, annotation_page, verdict_page],
            )
            back_btn.click(
                fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
                outputs=[welcome_page, annotation_page],
            )

