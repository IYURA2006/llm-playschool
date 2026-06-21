import gradio as gr
import functools
import glob
import html
import json
import os
from datetime import datetime

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


def output_path_for(game_path):
    """Stable per-game annotation output file in interactions/."""
    rel = os.path.relpath(game_path, _games_dir)
    slug = rel.replace(os.sep, "__").replace("interactions.json", "").strip("_")
    return os.path.join(_dir, "interactions", f"annot__{slug}.json")


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
    g.output_path = output_path_for(path)
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


def _build_transcript_html(g, current_idx):
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
                # Show just the sender ID in the card header — clean and unambiguous.
                parts.append(
                    f'<div class="{card_cls}" id="tc-{turn_counter}">'
                    f'<div class="card-header">'
                    f'{html.escape(sender)}&nbsp;&nbsp;·&nbsp;&nbsp;TURN {turn_counter + 1} OF {g.n_turns}'
                    f'</div>'
                    f'<div class="card-body">{html.escape(str(content))}</div>'
                    f'</div>'
                )
                turn_counter += 1

    return '<div class="txscroll">' + "".join(parts) + "</div>"


# ──────────────────────────────────────────────────────────────────────────
# SUBMIT  (closure over the active game)
# ──────────────────────────────────────────────────────────────────────────

def _submit(g, *vals):
    n = g.n_turns
    q1v, q2v, q3v = vals[0:n], vals[n:2 * n], vals[2 * n:3 * n]
    fv, cv = vals[3 * n:4 * n], vals[4 * n:5 * n]

    turns_out = []
    for i, msg in enumerate(g.ai_turns):
        turns_out.append({
            "turn_index": i,
            "from": msg["from"],
            "role": g.role(msg["from"]),
            "content": msg["action"]["content"],
            "prior_information_use": q1v[i],
            "strategic_logic": q2v[i],
            "reasoning_clarity": q3v[i] if g.has_reasoning else None,
            "flags": fv[i] or [],
            "comment": cv[i] or "",
        })

    os.makedirs(os.path.dirname(g.output_path), exist_ok=True)
    with open(g.output_path, "w") as f:
        json.dump({
            "game_id": g.meta["game_id"],
            "game_name": g.meta["game_name"],
            "source_path": os.path.relpath(g.path, _dir),
            "has_reasoning": g.has_reasoning,
            "annotated_at": datetime.now().isoformat(),
            "turns": turns_out,
        }, f, indent=2)
    return "✅ Saved!", gr.update(visible=False), gr.update(visible=True)


# ──────────────────────────────────────────────────────────────────────────
# BUILD
# ──────────────────────────────────────────────────────────────────────────

def build(welcome_page, annotation_page, verdict_page, game_state):
    # ── STATIC render for testing — loads DEFAULT_GAME once at build time ──
    g = load_game(DEFAULT_GAME)

    with annotation_page:

        # NAV BAR
        with gr.Row(elem_classes=["annot-topnav"]):
            gr.HTML(
                f'<div class="nav-left">'
                f'<span class="game-id-tag">#{g.meta["game_id"]}</span>'
                f'<span class="game-name-tag">{g.meta["game_name"].title()}</span>'
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
                gr.HTML(_build_transcript_html(g, 0))

            # RIGHT: per-turn annotation cards
            with gr.Column(scale=2, elem_id="annot-col"):
                gr.HTML(_turn_nav_html(g))
                q1s, q2s, q3s, flagss, comments = [], [], [], [], []

                for i in range(g.n_turns):
                    with gr.Group(elem_classes=["turn-anno-card"]):
                        gr.HTML(_card_header_html(g, i))

                        gr.Markdown("**Q1 — Prior Information Use**\n\nDid the AI correctly use information established in earlier turns?")
                        q1 = gr.Radio(
                            choices=[("1\nNone", "1"), ("2\nPartial", "2"), ("3\nGood", "3"), ("4\nExcellent", "4")],
                            show_label=False, elem_classes=["scale-radio", "q1-scale"],
                        )

                        gr.Markdown("**Q2 — Strategic Logic**\n\nRegardless of constraints, did this move make strategic sense?")
                        q2 = gr.Radio(
                            choices=[("1\nNonsensical", "1"), ("2\nPoor", "2"), ("3\nReasonable", "3"), ("4\nStrong", "4")],
                            show_label=False, elem_classes=["scale-radio", "q2-scale"],
                        )

                        # Q3 only rendered when the game has reasoning text
                        if g.has_reasoning:
                            gr.Markdown("**Q3 — Reasoning Clarity** · conditional")
                            q3 = gr.Radio(
                                choices=[("1\nUnclear", "1"), ("2\nConfused", "2"), ("3\nClear", "3"), ("4\nTransparent", "4"), ("N/A", "NA")],
                                show_label=False, elem_classes=["scale-radio", "q3-scale"],
                            )
                        else:
                            q3 = gr.Radio(choices=[("N/A", "NA")], value="NA", visible=False, show_label=False)

                        gr.HTML('<div class="flags-lbl">Flags <span class="flags-sub">— tick all that apply</span></div>')
                        fl = gr.CheckboxGroup(
                            choices=g.flag_choices, show_label=False,
                            elem_classes=["flags-check"],
                        )

                        cm = gr.Textbox(
                            placeholder="Optional turn comment…",
                            show_label=False, lines=2,
                            elem_classes=["turn-comment"],
                        )

                    q1s.append(q1); q2s.append(q2); q3s.append(q3)
                    flagss.append(fl); comments.append(cm)

                status = gr.Markdown("")
                with gr.Row():
                    back_btn = gr.Button("← Back", variant="secondary")
                    submit_btn = gr.Button("Submit All", variant="primary")

        # EVENTS
        submit_btn.click(
            fn=functools.partial(_submit, g),
            inputs=[*q1s, *q2s, *q3s, *flagss, *comments],
            outputs=[status, annotation_page, verdict_page],
        )
        back_btn.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[welcome_page, annotation_page],
        )

