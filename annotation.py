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

_ERR = '<div style="color:#ef4444;font-size:12px;margin:-4px 0 6px 2px;">⚠️ This field is required</div>'


def _format_round(round_msgs):
    lines = []
    for msg in round_msgs:
        sender = msg["from"]
        action = msg["action"]
        atype = action["type"]

        if atype == "correct guess":
            lines.append(f"**✅ {action['content']}**" if action["content"] != "end game" else "**🏁 Game ended**")
            continue

        if sender == "GM" and action.get("label") == "context":
            continue

        role = _players.get(sender, {}).get("game_role", sender)
        lines.append(f"**{sender}** ({role})")
        lines.append(action["content"])
        lines.append("")

    return "\n\n".join(lines)


def _count_left(values):
    complete = sum(
        1 for i in range(0, len(values), 5)
        if all(values[i + j] is not None for j in range(3))
    )
    left = len(values) // 5 - complete
    return left


def _update_counter(*req_values):
    n = len(req_values) // 3
    complete = sum(
        1 for i in range(0, len(req_values), 3)
        if all(v is not None for v in req_values[i : i + 3])
    )
    left = n - complete
    return "✅ All turns rated" if left == 0 else f"**{left} of {n} turns left to rate**"


def _submit(*values):
    error_htmls = []
    missing_any = False
    for i in range(0, len(values), 5):
        clarity, compliance, prior, _, _ = values[i : i + 5]
        e1 = _ERR if clarity is None else ""
        e2 = _ERR if compliance is None else ""
        e3 = _ERR if prior is None else ""
        error_htmls += [e1, e2, e3]
        if clarity is None or compliance is None or prior is None:
            missing_any = True

    left = _count_left(values)
    counter_str = "✅ All turns rated" if left == 0 else f"**{left} of {len(values) // 5} turns left to rate**"

    if missing_any:
        return ("⚠️ Please fill in all required fields (marked with *) before submitting.", counter_str, *error_htmls)

    turns_out = []
    for i in range(0, len(values), 5):
        clarity, compliance, prior, flags, comment = values[i : i + 5]
        msg = _ai_turns[i // 5]
        turns_out.append({
            "turn_index": i // 5,
            "from": msg["from"],
            "role": _players.get(msg["from"], {}).get("game_role", msg["from"]),
            "content": msg["action"]["content"],
            "reasoning_clarity": clarity,
            "rule_compliance": compliance,
            "use_prior_context": prior,
            "flags": flags or [],
            "comment": comment or "",
        })

    with open(OUTPUT_PATH, "w") as f:
        json.dump({"game_id": _meta["game_id"], "game_name": _meta["game_name"],
                   "annotated_at": datetime.now().isoformat(), "turns": turns_out}, f, indent=2)

    return ("✅ Saved to interactions/annotations-4.json", counter_str, *[""] * len(error_htmls))


def build(welcome_page, annotation_page):
    with annotation_page:
        with gr.Accordion("Game Rules", open=False):
            gr.Markdown(_game_rules)

        gr.Markdown("---")

        components = []
        required_radios = []
        error_htmls = []
        turn_counter = 0

        for round_idx, round_msgs in enumerate(_data["turns"]):
            ai_in_round = [
                m for m in round_msgs
                if m["from"] != "GM" and m["action"].get("label") == "response"
            ]

            with gr.Row(equal_height=False):
                with gr.Column(scale=1):
                    gr.Markdown(f"### Round {round_idx + 1}")
                    gr.Markdown(_format_round(round_msgs))

                with gr.Column(scale=1):
                    for msg in ai_in_round:
                        player = msg["from"]
                        role = _players.get(player, {}).get("game_role", player)
                        content = msg["action"]["content"]
                        turn_counter += 1

                        with gr.Group():
                            gr.Markdown(f"**Turn {turn_counter} — {player} ({role})**\n\n> {content}")
                            r1 = gr.Radio(choices=["1", "2", "3", "4"], label="Reasoning Clarity *")
                            e1 = gr.HTML("")
                            r2 = gr.Radio(choices=["Followed", "Partial", "Violated"], label="Rule Compliance *")
                            e2 = gr.HTML("")
                            r3 = gr.Radio(choices=["1", "2", "3", "4"], label="Use Prior Context *")
                            e3 = gr.HTML("")
                            chk = gr.CheckboxGroup(
                                choices=["Got answer by luck", "Failed to use prior info",
                                         "Format/rule violations", "Self-corrected after error"],
                                label="Flags",
                            )
                            txt = gr.Textbox(
                                label="Comment",
                                lines=1,
                                placeholder="Optional — note anything unusual about this turn",
                            )
                            components += [r1, r2, r3, chk, txt]
                            required_radios.append((r1, r2, r3))
                            error_htmls.append((e1, e2, e3))

            gr.Markdown("---")

        status = gr.Markdown("")

        n_total = len(_ai_turns)
        with gr.Row():
            back_btn = gr.Button("← Back", variant="secondary")
            counter = gr.Markdown(f"**{n_total} of {n_total} turns left to rate**", elem_classes=["turn-counter"])
            submit_btn = gr.Button("Submit →", variant="primary")

        # flatten for event wiring
        all_required = [r for trio in required_radios for r in trio]
        all_errors = [e for trio in error_htmls for e in trio]

        for radio in all_required:
            radio.change(fn=_update_counter, inputs=all_required, outputs=[counter])

        for radio, err in zip(all_required, all_errors):
            radio.change(fn=lambda: "", outputs=[err])

        submit_btn.click(
            fn=_submit,
            inputs=components,
            outputs=[status, counter] + all_errors,
        )

        back_btn.click(
            fn=lambda: (gr.Column(visible=True), gr.Column(visible=False)),
            outputs=[welcome_page, annotation_page],
        )
