import gradio as gr
import json
import os
from datetime import datetime

_dir = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_dir, "interactions", "interactions-4.json")
OUTPUT_PATH = os.path.join(_dir, "interactions", "annotations-4.json")

with open(DATA_PATH) as f:
    _data = json.load(f)

_meta = _data["meta"]
_players = _data["players"]
_game_rules = _data["turns"][0][0]["action"]["content"]


_ai_turns = []
for turn in _data["turns"]:
    for msg in turn:
        if msg["from"]!= "GM" and msg["action"].get("label") == "response":
           _ai_turns.append(msg)

N_TURNS = len(_ai_turns)


def _progress_html(rated: int) -> str:
    return (
        f'<div class="annot-progress">'
        f'<span class="prog-sep">·</span>'
        f'<span class="prog-rated">{rated} of {N_TURNS} turns rated</span>'
        f'</div>'
    )


def _card_header_html(idx: int) -> str:
    return (
        f'<div class="ta-head">'
        f'<span class="ta-badge">{idx + 1}</span>'
        f'<span class="ta-title">Turn {idx + 1} of {N_TURNS}</span>'
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

    goal_text = _game_rules.strip()
    parts.append(
        f'<div class="goal-box">'
        f'<div class="goal-label">GAME GOAL</div>'
        f'<div class="goal-text">{goal_text}</div>'
        f'</div>'
    )

    turn_counter = 0
    _turn0_setup = {_data["turns"][0][0]["action"]["content"]}
    if len(_data["turns"][0]) > 2:
        _turn0_setup.add(_data["turns"][0][2]["action"]["content"])

    for round_idx, round_msgs in enumerate(_data["turns"]):
        for msg in round_msgs:
            sender = msg["from"]
            action = msg["action"]
            atype = action["type"]
            label = action.get("label", "")
            content = action["content"]

            if sender == "GM":
                if content in _turn0_setup:
                    continue
                if label == "context":
                    short = content[:160] + ("…" if len(content) > 160 else "")
                    parts.append(
                        f'<div class="gm-msg">'
                        f'<span class="gm-tag">GM</span> {short}'
                        f'</div>'
                    )
                elif atype == "correct guess":
                    if content == "end game":
                        parts.append('<div class="game-end-msg">🏁 Game ended</div>')
                    else:
                        parts.append(
                            f'<div class="correct-msg">✅ Correct: <strong>{content}</strong></div>'
                        )
            elif label == "response":
                player = sender
                active = turn_counter == current_idx
                card_cls = "turn-card active-turn" if active else "turn-card"
                parts.append(
                    f'<div class="{card_cls}" id="tc-{turn_counter}">'
                    f'<div class="card-header">'
                    f'{player.upper()} (AI)&nbsp;&nbsp;·&nbsp;&nbsp;TURN {turn_counter + 1} OF {N_TURNS}'
                    f'</div>'
                    f'<div class="card-body">{content}</div>'
                    f'</div>'
                )
                turn_counter += 1

    return '<div class="txscroll">' + "".join(parts) + "</div>"


_FLAG_CHOICES = [
    "Repeated a previous failed move",
    "Invented or misquoted a game fact",
    "Self-corrected after error",
    "Reasoning-Action Mismatch",
]


def _submit(*vals):
    n = N_TURNS
    q1v, q2v, q3v = vals[0:n], vals[n:2 * n], vals[2 * n:3 * n]
    fv, cv = vals[3 * n:4 * n], vals[4 * n:5 * n]
    turns_out = []
    for i, msg in enumerate(_ai_turns):
        turns_out.append({
            "turn_index": i,
            "from": msg["from"],
            "role": _players.get(msg["from"], {}).get("game_role", msg["from"]),
            "content": msg["action"]["content"],
            "prior_information_use": q1v[i],
            "strategic_logic": q2v[i],
            "reasoning_clarity": q3v[i],
            "flags": fv[i] or [],
            "comment": cv[i] or "",
        })
    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "game_id": _meta["game_id"],
            "game_name": _meta["game_name"],
            "annotated_at": datetime.now().isoformat(),
            "turns": turns_out,
        }, f, indent=2)
    return "✅ Saved!", gr.update(visible=False), gr.update(visible=True)


def build(welcome_page, annotation_page, verdict_page):
    with annotation_page:

        # ── TOP NAV ──────────────────────────────────────────────────
        with gr.Row(elem_classes=["annot-topnav"]):
            gr.HTML(
                f'<div class="nav-left">'
                f'<span class="game-id-tag">#{_meta["game_id"]}</span>'
                f'<span class="game-name-tag">{_meta["game_name"].title()}</span>'
                f'</div>'
            )
            gr.HTML(_progress_html(0), elem_classes=["nav-center"])
            gr.HTML('<div class="nav-right"><div class="nav-timer">00:00</div></div>')
            rules_btn = gr.Button("Rules", variant="secondary", size="sm", elem_classes=["rules-nav-btn"])

        # ── MAIN LAYOUT ──────────────────────────────────────────────
        with gr.Row(equal_height=False, elem_classes=["anno-main-row"]):

            # LEFT: scrollable transcript
            with gr.Column(scale=3, elem_classes=["tx-col"]):
                gr.HTML(_build_transcript_html(0))

            # RIGHT: paginated annotation cards (one shown at a time, client-side)
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

                        gr.Markdown("**Q3 — Reasoning Clarity** · conditional")
                        q3 = gr.Radio(
                            choices=[("1\nUnclear", "1"), ("2\nConfused", "2"), ("3\nClear", "3"), ("4\nTransparent", "4"), ("N/A", "NA")],
                            show_label=False, elem_classes=["scale-radio", "q3-scale"],
                        )

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

        # ── RULES PANEL ──────────────────────────────────────────────
        with gr.Column(visible=False, elem_classes=["rules-panel"]) as rules_col:
            gr.Markdown("## Game Rules")
            gr.Markdown(_game_rules)
            close_rules = gr.Button("Close", variant="secondary")

        # ── EVENT WIRING ─────────────────────────────────────────────
        submit_btn.click(
            fn=_submit,
            inputs=[*q1s, *q2s, *q3s, *flagss, *comments],
            outputs=[status, annotation_page, verdict_page],
        )

        rules_btn.click(fn=lambda: gr.update(visible=True), outputs=[rules_col])
        close_rules.click(fn=lambda: gr.update(visible=False), outputs=[rules_col])

        back_btn.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[welcome_page, annotation_page],
        )
