import gradio as gr
import json
import os

_dir = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_dir, "interactions", "interactions-4.json")
OUTPUT_PATH = os.path.join(_dir, "interactions", "annotations-4.json")

with open(DATA_PATH) as f:
    _data = json.load(f)

_meta = _data["meta"]

_OVERALL_DESC = (
    "**1** — The AI completely failed to play the game, broke formatting rules, "
    "or ruined the progression of the match.\n\n"
    "**2** — The AI stumbled around blindly, making choices that constantly "
    "hurt its own chances of winning.\n\n"
    "**3** — The AI understood the goal but played poorly — wasting turns or "
    "missing obvious opportunities to win.\n\n"
    "**4** — The AI followed the basic rules and avoided huge mistakes, but showed "
    "no real strategy or forward thinking.\n\n"
    "**5** — The AI actively pushed toward the goal with smart, logical moves "
    "and handled the game smoothly.\n\n"
    "**6** — The AI played at a high level, optimised its path to victory, and "
    "adapted perfectly to game updates.\n\n"
    "**7** — The AI played perfectly, showing the strategic sharpness and logical "
    "execution of an expert human player."
)

_OK  = ["scale-radio"]
_ERR = ["scale-radio", "radio-error"]


def _save_verdict(coherence, overall, comment):
    c_cls = _ERR if not coherence else _OK
    o_cls = _ERR if not overall else _OK

    if not coherence or not overall:
        return (
            "⚠️ Please fill in the highlighted fields before submitting.",
            gr.update(elem_classes=c_cls),
            gr.update(elem_classes=o_cls),
        )

    try:
        with open(OUTPUT_PATH) as f:
            saved = json.load(f)
    except FileNotFoundError:
        return (
            "⚠️ Turn annotations not found. Please complete Step 1 first.",
            gr.update(elem_classes=_OK),
            gr.update(elem_classes=_OK),
        )

    saved["overall_verdict"] = {
        "strategic_coherence": coherence,
        "overall_rating": overall,
        "comment": comment or "",
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(saved, f, indent=2)

    return (
        "✅ Verdict saved to interactions/annotations-4.json",
        gr.update(elem_classes=_OK),
        gr.update(elem_classes=_OK),
    )


def build(welcome_page, annotation_page, verdict_page):
    with verdict_page:

        # ── TOP NAV ───────────────────────────────────────────────────
        with gr.Row(elem_classes=["annot-topnav"]):
            gr.HTML(
                f'<div class="nav-left">'
                f'<span class="game-id-tag">#{_meta["game_id"]}</span>'
                f'<span class="game-name-tag">{_meta["game_name"].title()}</span>'
                f'</div>'
            )
            gr.HTML(
                '<div class="annot-progress">'
                '<span>Step 2 of 2</span>'
                '<span class="prog-sep">·</span>'
                '<span class="prog-rated">Overall Verdict</span>'
                '</div>',
                elem_classes=["nav-center"],
            )
            gr.HTML('<div class="nav-right"><div class="nav-timer"></div></div>')
            gr.Button("", visible=False, size="sm")

        # ── MAIN CONTENT ──────────────────────────────────────────────
        gr.Markdown("## Overall Verdict")
        gr.Markdown("You have rated all individual turns. Now give your overall assessment of this game session.")

        # G1: Strategic Coherence
        with gr.Group(elem_classes=["question-card"]):
            gr.Markdown("### G1 — Strategic Coherence")
            gr.Markdown("How well did the AI stick to and adapt its plan throughout the game?")
            coherence = gr.Radio(
                choices=[
                    ("1\nNo plan", "1"),
                    ("2\nRigid", "2"),
                    ("3\nAdaptive", "3"),
                    ("4\nStrategic", "4"),
                ],
                label="", show_label=False,
                elem_classes=["scale-radio"],
            )

        # G2: Overall Game Quality
        with gr.Group(elem_classes=["question-card"]):
            gr.Markdown("### G2 — Overall Game Quality")
            gr.Markdown("Looking at the whole game, how well did the AI actually play to achieve the main goal?")
            overall = gr.Radio(
                choices=[
                    ("1\nBroken", "1"),
                    ("2\nIncompetent", "2"),
                    ("3\nSloppy", "3"),
                    ("4\nBare min.", "4"),
                    ("5\nSolid", "5"),
                    ("6\nSkilled", "6"),
                    ("7\nFlawless", "7"),
                ],
                label="", show_label=False,
                elem_classes=["scale-radio"],
            )
        gr.Markdown(_OVERALL_DESC, elem_classes=["option-desc"])

        # Comment
        with gr.Group(elem_classes=["question-card"]):
            comment = gr.Textbox(
                placeholder="Any overall observations about this game? (optional)",
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
            outputs=[status, coherence, overall],
        )

        coherence.change(fn=lambda: gr.update(elem_classes=_OK), outputs=[coherence])
        overall.change(fn=lambda: gr.update(elem_classes=_OK), outputs=[overall])

        back_btn.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[annotation_page, verdict_page],
        )
