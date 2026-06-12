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

_ai_turns = [
    msg
    for turn in _data["turns"]
    for msg in turn
    if msg["from"] != "GM" and msg["action"].get("label") == "response"
]

N_TURNS = len(_ai_turns)


def _progress_html(rated: int) -> str:
    return (
        f'<div class="annot-progress">'
        f'<span>Transcript 1 of 1</span>'
        f'<span class="prog-sep">·</span>'
        f'<span class="prog-rated">{rated} of {N_TURNS} turns rated</span>'
        f'</div>'
    )


def _turn_header_html(idx: int) -> str:
    return (
        f'<div class="turn-header-box">'
        f'<span class="turn-num-badge">{idx + 1}</span>'
        f'<span class="turn-title">Turn {idx + 1} of {N_TURNS}</span>'
        f'</div>'
    )


def _build_transcript_html(current_idx: int) -> str:
    parts = []

    goal_lines = _game_rules.strip().split("\n")
    goal_text = " ".join(goal_lines[:2])[:220]
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


def _initial_annotations():
    return {i: {"q1": None, "q2": None, "q3": None, "flags": [], "comment": ""} for i in range(N_TURNS)}


def _navigate(direction, turn_idx, anns, q1v, q2v, q3v, fv, cv):
    anns = dict(anns)
    anns[turn_idx] = {"q1": q1v, "q2": q2v, "q3": q3v, "flags": fv or [], "comment": cv or ""}
    new_idx = max(0, min(N_TURNS - 1, turn_idx + direction))
    ann = anns[new_idx]
    rated = sum(1 for a in anns.values() if a["q1"] and a["q2"])
    return (
        new_idx,
        anns,
        _build_transcript_html(new_idx),
        _turn_header_html(new_idx),
        ann["q1"],
        ann["q2"],
        ann["q3"],
        ann["flags"] or [],
        ann["comment"] or "",
        _progress_html(rated),
        "",
    )


def _submit(turn_idx, anns, q1v, q2v, q3v, fv, cv):
    anns = dict(anns)
    anns[turn_idx] = {"q1": q1v, "q2": q2v, "q3": q3v, "flags": fv or [], "comment": cv or ""}
    turns_out = []
    for i, msg in enumerate(_ai_turns):
        a = anns[i]
        turns_out.append({
            "turn_index": i,
            "from": msg["from"],
            "role": _players.get(msg["from"], {}).get("game_role", msg["from"]),
            "content": msg["action"]["content"],
            "prior_information_use": a["q1"],
            "strategic_logic": a["q2"],
            "reasoning_clarity": a["q3"],
            "flags": a["flags"],
            "comment": a["comment"],
        })
    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "game_id": _meta["game_id"],
            "game_name": _meta["game_name"],
            "annotated_at": datetime.now().isoformat(),
            "turns": turns_out,
        }, f, indent=2)
    return anns, "✅ Saved!", gr.update(visible=False), gr.update(visible=True)


def build(welcome_page, annotation_page, verdict_page):
    with annotation_page:
        current_turn = gr.State(0)
        annotations = gr.State(_initial_annotations())

        # ── TOP NAV ──────────────────────────────────────────────────
        with gr.Row(elem_classes=["annot-topnav"]):
            gr.HTML(
                f'<div class="nav-left">'
                f'<span class="game-id-tag">#{_meta["game_id"]}</span>'
                f'<span class="game-name-tag">{_meta["game_name"].title()}</span>'
                f'</div>'
            )
            progress_disp = gr.HTML(_progress_html(0), elem_classes=["nav-center"])
            gr.HTML('<div class="nav-right"><div class="nav-timer">00:00</div></div>')
            rules_btn = gr.Button("Rules", variant="secondary", size="sm", elem_classes=["rules-nav-btn"])

        # ── MAIN LAYOUT ──────────────────────────────────────────────
        with gr.Row(equal_height=False):

            # LEFT: scrollable transcript
            with gr.Column(scale=3, elem_classes=["tx-col"]):
                transcript = gr.HTML(_build_transcript_html(0))

            # RIGHT: annotation form
            with gr.Column(scale=2, elem_id="annot-col"):
                turn_header = gr.HTML(_turn_header_html(0))

                gr.Markdown('**Q1 — Prior Information Use**\n\nDid the AI correctly use information established in earlier turns?')
                q1 = gr.Radio(
                    choices=[("1\nNone", "1"), ("2\nPartial", "2"), ("3\nGood", "3"), ("4\nExcellent", "4")],
                    label="", show_label=False,
                    elem_classes=["scale-radio"],
                )

                gr.Markdown('**Q2 — Strategic Logic**\n\nRegardless of constraints, did this move make strategic sense?')
                q2 = gr.Radio(
                    choices=[("1\nNonsensical", "1"), ("2\nPoor", "2"), ("3\nReasonable", "3"), ("4\nStrong", "4")],
                    label="", show_label=False,
                    elem_classes=["scale-radio"],
                )

                gr.Markdown('**Q3 — Reasoning Clarity**')
                gr.HTML('<span class="cond-tag">conditional</span>')
                gr.Markdown('How clearly does the AI explain what it is doing and why in this turn?')
                q3 = gr.Radio(
                    choices=[("1\nConfused", "1"), ("2\nNot Clear", "2"), ("3\nClear", "3"), ("4\nTransparent", "4"), ("N/A", "NA")],
                    label="", show_label=False,
                    elem_classes=["scale-radio"],
                )

                gr.HTML('<div class="flags-lbl">Flags <span class="flags-sub">— tick all that apply</span></div>')
                flags = gr.CheckboxGroup(
                    choices=[
                        "Repeated a previous failed move",
                        "Invented or misquoted a game fact",
                        "Self-corrected after error",
                        "Reasoning-Action Mismatch",
                    ],
                    label="", show_label=False,
                    elem_classes=["flags-check"],
                )

                comment = gr.Textbox(
                    placeholder="Optional turn comment…",
                    label="", show_label=False,
                    lines=2,
                    elem_classes=["turn-comment"],
                )

                with gr.Row():
                    prev_btn = gr.Button("← Prev", variant="secondary", size="sm")
                    next_btn = gr.Button("Next →", variant="primary", size="sm")

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
        nav_outputs = [
            current_turn, annotations, transcript, turn_header,
            q1, q2, q3, flags, comment, progress_disp, status,
        ]

        prev_btn.click(
            fn=lambda *a: _navigate(-1, *a),
            inputs=[current_turn, annotations, q1, q2, q3, flags, comment],
            outputs=nav_outputs,
        )
        next_btn.click(
            fn=lambda *a: _navigate(1, *a),
            inputs=[current_turn, annotations, q1, q2, q3, flags, comment],
            outputs=nav_outputs,
        )

        submit_btn.click(
            fn=_submit,
            inputs=[current_turn, annotations, q1, q2, q3, flags, comment],
            outputs=[annotations, status, annotation_page, verdict_page],
        )

        rules_btn.click(fn=lambda: gr.update(visible=True), outputs=[rules_col])
        close_rules.click(fn=lambda: gr.update(visible=False), outputs=[rules_col])

        back_btn.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[welcome_page, annotation_page],
        )
