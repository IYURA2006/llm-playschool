import gradio as gr
import json
import os

_dir = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_dir, "interactions", "interactions-4.json")
OUTPUT_PATH = os.path.join(_dir, "interactions", "annotations-4.json")

with open(DATA_PATH) as f:
    _data = json.load(f)

_meta = _data["meta"]

_COHERENCE_DESC = (
    "**1 — No plan:** Each move seems disconnected from the last — no consistent logic across turns.\n\n"
    "**2 — Rigid:** Had a plan but kept following it even when feedback clearly showed it was not working.\n\n"
    "**3 — Adaptive:** Maintained a clear goal and adjusted its approach when something was not working.\n\n"
    "**4 — Strategic:** Built every new piece of information into its plan smoothly."
)


def _save_verdict(coherence, overall, comment):
    if not coherence:
        return "⚠️ Please rate Strategic Coherence before submitting."
    if not overall:
        return "⚠️ Please give an Overall Rating before submitting."

    try:
        with open(OUTPUT_PATH) as f:
            saved = json.load(f)
    except FileNotFoundError:
        return "⚠️ Turn annotations not found. Please complete Step 1 first."

    saved["overall_verdict"] = {
        "strategic_coherence": coherence,
        "overall_rating": overall,
        "comment": comment or "",
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(saved, f, indent=2)

    return "✅ Verdict saved to interactions/annotations-4.json"


def build(welcome_page, annotation_page, verdict_page):
    with verdict_page:
        gr.HTML(
            f'<div class="nav-left" style="padding:12px 0 4px;">'
            f'<span class="game-id-tag">#{_meta["game_id"]}</span>'
            f'<span class="game-name-tag">{_meta["game_name"].title()}</span>'
            f'<span style="color:#64748b;font-size:12px;margin-left:10px;">Step 2 of 2</span>'
            f'</div>'
        )

        gr.Markdown("## Overall Verdict")
        gr.Markdown("You have rated all individual turns. Now give your overall assessment of this game session.")
        gr.Markdown("---")

        # ── Q1: Strategic Coherence ───────────────────────────────────
        gr.Markdown("### Strategic Coherence")
        gr.Markdown("How well did the AI stick to and adapt its plan throughout the game?")
        coherence = gr.Radio(
            choices=[
                ("1\nNo plan", "1"),
                ("2\nRigid", "2"),
                ("3\nAdaptive", "3"),
                ("4\nStrategic", "4"),
            ],
            label="",
            show_label=False,
            elem_classes=["scale-radio"],
        )
        gr.Markdown(_COHERENCE_DESC, elem_classes=["option-desc"])

        gr.Markdown("---")

        # ── Q2: Overall Rating ────────────────────────────────────────
        gr.Markdown("### Overall Rating")
        gr.Markdown("Taking everything into account, how would you rate this game session overall?")
        overall = gr.Radio(
            choices=[(str(i), str(i)) for i in range(1, 8)],
            label="",
            show_label=False,
            elem_classes=["scale-radio"],
        )

        gr.Markdown("---")

        # ── Comment ───────────────────────────────────────────────────
        gr.Markdown("### Overall Comment *(optional)*")
        comment = gr.Textbox(
            placeholder="Any overall observations about this game session...",
            lines=4,
            show_label=False,
        )

        status = gr.Markdown("")

        with gr.Row():
            back_btn = gr.Button("← Back to Annotation", variant="secondary")
            submit_btn = gr.Button("Submit Verdict", variant="primary")

        submit_btn.click(
            fn=_save_verdict,
            inputs=[coherence, overall, comment],
            outputs=[status],
        )

        back_btn.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[annotation_page, verdict_page],
        )
