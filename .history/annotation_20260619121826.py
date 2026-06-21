import gradio as gr
import html
import json
import os
import re
from datetime import datetime

# ──────────────────────────────────────────────────────────────────────────
# DATA LOADING — fully data-driven, works for any clembench game transcript
# ──────────────────────────────────────────────────────────────────────────

_dir = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_dir, "interactions", "interactions-10.json")
OUTPUT_PATH = os.path.join(_dir, "interactions", "annotations-4.json")

with open(DATA_PATH) as f:
    _data = json.load(f)

_meta = _data["meta"]
_players = _data["players"]


# ── Extract the game rules robustly ──
# Some games (e.g. Codenames) put a non-string metadata dict as the first
# message, so we cannot assume turns[0][0] is the rules text. Find the first
# GM message with string content that looks like an instruction prompt.
def _extract_game_rules() -> str:
    for turn in _data["turns"]:
        for msg in turn:
            if msg["from"] == "GM":
                c = msg["action"].get("content")
                if isinstance(c, str) and len(c) > 80:
                    return c
    for turn in _data["turns"]:
        for msg in turn:
            c = msg["action"].get("content")
            if isinstance(c, str) and c.strip():
                return c
    return "(no rules text found)"


_game_rules = _extract_game_rules()

# ── Identify genuine AI players (exclude GM and programmatic/scripted bots) ──
# CRITICAL FIX: the old code annotated every non-GM "response", which wrongly
# included programmatic players (e.g. PathDescriber, Questioner) whose messages
# are environment output, not AI reasoning.
def _is_ai_player(pid: str) -> bool:
    info = _players.get(pid, {})
    if pid == "GM":
        return False
    model = (info.get("model_name") or "").lower()
    return model != "programmatic" and model != ""

_AI_PLAYER_IDS = {pid for pid in _players if _is_ai_player(pid)}


def _player_role(pid: str) -> str:
    return _players.get(pid, {}).get("game_role", pid)


# ── Collect AI turns (only genuine AI players, in transcript order) ──
_ai_turns = []
for turn in _data["turns"]:
    for msg in turn:
        if (
            msg["from"] in _AI_PLAYER_IDS
            and msg["action"].get("label") == "response"
        ):
            _ai_turns.append(msg)

N_TURNS = len(_ai_turns)

# ── Detect whether this game has explicit reasoning/explanation text ──
# Q3 (Reasoning Clarity) is only meaningful when the AI produces reasoning.
# Games like Wordle mandate an "explanation:" field; games like Adventure
# ("> go north") or TextMapWorld ("GO: east") have no reasoning to rate.
def _detect_reasoning(turns) -> bool:
    if not turns:
        return False
    markers = ("explanation:", "because", "since ", "reasoning:", "i'll ", "i will ")
    hits = 0
    for msg in turns:
        c = str(msg["action"].get("content", "")).lower()
        # a turn "has reasoning" if it is reasonably long AND contains a marker
        if len(c) > 60 and any(m in c for m in markers):
            hits += 1
    # require the majority of turns to contain reasoning to enable Q3
    return hits >= max(1, len(turns) // 2)


HAS_REASONING = _detect_reasoning(_ai_turns)

# Is this a multi-agent game with more than one distinct AI role?
_DISTINCT_ROLES = {_player_role(pid) for pid in _AI_PLAYER_IDS}
IS_MULTI_ROLE = len(_AI_PLAYER_IDS) > 1

_FLAG_CHOICES = [
    "Repeated a previous failed move",
    "Invented or misquoted a game fact",
    "Self-corrected after error",
]
if HAS_REASONING:
    _FLAG_CHOICES.append("Reasoning-Action Mismatch")


# ──────────────────────────────────────────────────────────────────────────
# HTML BUILDERS
# ──────────────────────────────────────────────────────────────────────────

def _progress_html(rated: int) -> str:
    return (
        f'<div class="annot-progress">'
        f'<span class="prog-rated">{rated} of {N_TURNS} turns rated</span>'
        f'</div>'
    )


def _card_header_html(idx: int) -> str:
    role = _player_role(_ai_turns[idx]["from"])
    role_chip = f'<span class="ta-role">{html.escape(role)}</span>' if IS_MULTI_ROLE else ""
    return (
        f'<div class="ta-head">'
        f'<span class="ta-badge">{idx + 1}</span>'
        f'<span class="ta-title">Turn {idx + 1} of {N_TURNS}</span>'
        f'{role_chip}'
        f'<span class="rated-badge">✓ Rated</span>'
        f'</div>'
    )


def _turn_nav_html() -> str:
    chips = "".join(
        f'<button type="button" class="tn-chip" role="tab" id="tn-chip-{i}" '
        f'data-turn="{i}" aria-selected="{"true" if i == 0 else "false"}" '
        f'tabindex="{"0" if i == 0 else "-1"}">{i + 1}</button>'
        for i in range(N_TURNS)
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


def _build_transcript_html(current_idx: int) -> str:
    parts = []
    turn_counter = 0  # index over AI-player response turns only

    goal_text = html.escape(_game_rules.strip())
    parts.append(
        f'<div class="goal-box">'
        f'<div class="goal-label">GAME GOAL</div>'
        f'<div class="goal-text">{goal_text}</div>'
        f'</div>'
    )

    # messages from turn 0 that are pure setup (the long rules prompt) — skip in body
    _turn0_setup = set()
    for m in _data["turns"][0]:
        c = m["action"].get("content")
        if isinstance(c, str):
            _turn0_setup.add(c)

    for round_msgs in _data["turns"]:
        for msg in round_msgs:
            sender = msg["from"]
            action = msg["action"]
            atype = action["type"]
            label = action.get("label", "")
            content = action.get("content", "")

            # GM / environment messages
            if sender == "GM" or sender not in _AI_PLAYER_IDS:
                if not isinstance(content, str):
                    continue
                if content in _turn0_setup:
                    continue
                if label == "context":
                    # context shown TO an AI player = useful environment info
                    tag = "GM" if sender == "GM" else html.escape(_player_role(sender))
                    parts.append(
                        f'<div class="gm-msg">'
                        f'<span class="gm-tag">{tag}</span> {html.escape(content)}'
                        f'</div>'
                    )
                elif atype == "correct guess":
                    if content == "end game":
                        parts.append('<div class="game-end-msg">🏁 Game ended</div>')
                    else:
                        parts.append(
                            f'<div class="correct-msg">✅ Correct: '
                            f'<strong>{html.escape(str(content))}</strong></div>'
                        )
                continue

            # Genuine AI player response → render as a turn card
            if label == "response":
                active = turn_counter == current_idx
                card_cls = "turn-card active-turn" if active else "turn-card"
                role = _player_role(sender)
                role_label = f"{html.escape(role)}" if IS_MULTI_ROLE else f"{html.escape(sender.upper())} (AI)"
                parts.append(
                    f'<div class="{card_cls}" id="tc-{turn_counter}">'
                    f'<div class="card-header">'
                    f'{role_label}&nbsp;&nbsp; · &nbsp;&nbsp;TURN {turn_counter + 1} OF {N_TURNS}'
                    f'</div>'
                    f'<div class="card-body">{html.escape(str(content))}</div>'
                    f'</div>'
                )
                turn_counter += 1

    return '<div class="txscroll">' + "".join(parts) + "</div>"


# ──────────────────────────────────────────────────────────────────────────
# SUBMIT
# ──────────────────────────────────────────────────────────────────────────

def _submit(*vals):
    n = N_TURNS
    q1v, q2v, q3v = vals[0:n], vals[n:2 * n], vals[2 * n:3 * n]
    fv, cv = vals[3 * n:4 * n], vals[4 * n:5 * n]

    turns_out = []
    for i, msg in enumerate(_ai_turns):
        turns_out.append({
            "turn_index": i,
            "from": msg["from"],
            "role": _player_role(msg["from"]),
            "content": msg["action"]["content"],
            "prior_information_use": q1v[i],
            "strategic_logic": q2v[i],
            "reasoning_clarity": q3v[i] if HAS_REASONING else None,
            "flags": fv[i] or [],
            "comment": cv[i] or "",
        })

    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "game_id": _meta["game_id"],
            "game_name": _meta["game_name"],
            "has_reasoning": HAS_REASONING,
            "annotated_at": datetime.now().isoformat(),
            "turns": turns_out,
        }, f, indent=2)
    return "✅ Saved!", gr.update(visible=False), gr.update(visible=True)


# ──────────────────────────────────────────────────────────────────────────
# BUILD
# ──────────────────────────────────────────────────────────────────────────

def build(welcome_page, annotation_page, verdict_page):
    with annotation_page:

        # NAV BAR
        with gr.Row(elem_classes=["annot-topnav"]):
            gr.HTML(
                f'<div class="nav-left">'
                f'<span class="game-id-tag">#{_meta["game_id"]}</span>'
                f'<span class="game-name-tag">{_meta["game_name"].title()}</span>'
                f'</div>'
            )
            gr.HTML(_progress_html(0), elem_classes=["nav-center"])

        # MAIN LAYOUT
        with gr.Row(equal_height=False, elem_classes=["anno-main-row"]):

            # LEFT: scrollable transcript
            with gr.Column(scale=3, elem_classes=["tx-col"]):
                gr.HTML(_build_transcript_html(0))

            # RIGHT: per-turn annotation cards
            with gr.Column(scale=2, elem_id="annot-col"):
                gr.HTML(_turn_nav_html())
                q1s, q2s, q3s, flagss, comments = [], [], [], [], []

                for i in range(N_TURNS):
                    with gr.Group(elem_classes=["turn-anno-card"]):
                        gr.HTML(_card_header_html(i))

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
                        if HAS_REASONING:
                            gr.Markdown("**Q3 — Reasoning Clarity** · conditional")
                            q3 = gr.Radio(
                                choices=[("1\nUnclear", "1"), ("2\nConfused", "2"), ("3\nClear", "3"), ("4\nTransparent", "4"), ("N/A", "NA")],
                                show_label=False, elem_classes=["scale-radio", "q3-scale"],
                            )
                        else:
                            # hidden placeholder so input arity stays constant
                            q3 = gr.Radio(choices=[("N/A", "NA")], value="NA", visible=False, show_label=False)

                        gr.HTML('<div class="flags-lbl">Flags <span class="flags-sub">— tick all that apply</span></div>')
                        fl = gr.CheckboxGroup(
                            choices=_FLAG_CHOICES, show_label=False,
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
            fn=_submit,
            inputs=[*q1s, *q2s, *q3s, *flagss, *comments],
            outputs=[status, annotation_page, verdict_page],
        )
        back_btn.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[welcome_page, annotation_page],
        )