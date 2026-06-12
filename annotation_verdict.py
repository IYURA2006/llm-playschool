import gradio as gr
import json
import os

_dir = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_dir, "interactions", "interactions-4.json")
OUTPUT_PATH = os.path.join(_dir, "interactions", "annotations-4.json")

with open(DATA_PATH) as f:
    _data = json.load(f)

_meta = _data["meta"]

_COHERENCE = [
    ("1", "No plan",   "Each move seems disconnected from the last — no consistent logic across turns."),
    ("2", "Rigid",     "Had a plan but kept following it even when feedback clearly showed it was not working."),
    ("3", "Adaptive",  "Maintained a clear goal and adjusted its approach when something was not working."),
    ("4", "Strategic", "Built every new piece of information into its plan smoothly."),
]

_OVERALL_RATINGS = [
    ("1", "Broken",    "Completely failed to play the game, broke formatting rules, or ruined the match."),
    ("2", "Blind",     "Stumbled around blindly — choices constantly hurt its own chances of winning."),
    ("3", "Sloppy",    "Understood the goal but played poorly — wasted turns or missed obvious opportunities."),
    ("4", "Bare min.", "Followed basic rules and avoided huge mistakes but showed no real strategy."),
    ("5", "Solid",     "Actively pushed toward the goal with smart, logical moves and handled the game smoothly."),
    ("6", "Skilled",   "Played at a high level, optimised its path to victory, and adapted perfectly."),
    ("7", "Flawless",  "Played perfectly — strategic sharpness and logical execution of an expert human player."),
]


def _col_updates(options, chosen, err=False):
    return [
        gr.update(elem_classes=["coh-col", "coh-col-sel"] if v == chosen
                  else ["coh-col", "coh-col-err"] if (err and not chosen)
                  else ["coh-col"])
        for v, *_ in options
    ]


def _btn_updates(options, chosen):
    return [gr.update(variant="primary" if v == chosen else "secondary")
            for v, *_ in options]


def _ovr_err_col_updates(err=False):
    classes = ["ovr-sel-btn", "ovr-btn-err"] if err else ["ovr-sel-btn"]
    return [gr.update(elem_classes=classes) for _ in _OVERALL_RATINGS]


def _coh_select(chosen):
    return (*_col_updates(_COHERENCE, chosen), *_btn_updates(_COHERENCE, chosen), chosen)


def _ovr_row_updates(chosen):
    return [
        gr.update(elem_classes=["ovr-row", "ovr-row-sel"] if v == chosen else ["ovr-row"])
        for v, *_ in _OVERALL_RATINGS
    ]


def _overall_select(chosen):
    return (*_ovr_row_updates(chosen), *_ovr_err_col_updates(err=False), *_btn_updates(_OVERALL_RATINGS, chosen), chosen)


def _save_verdict(coherence, overall, comment):
    c_err = not coherence
    o_err = not overall

    if c_err or o_err:
        return (
            "⚠️ Please fill in the highlighted fields before submitting.",
            *_col_updates(_COHERENCE, coherence, err=c_err),
            *_ovr_err_col_updates(err=o_err),
        )

    try:
        with open(OUTPUT_PATH) as f:
            saved = json.load(f)
    except FileNotFoundError:
        return (
            "⚠️ Turn annotations not found. Please complete Step 1 first.",
            *_col_updates(_COHERENCE, coherence),
            *_ovr_err_col_updates(),
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
        *_col_updates(_COHERENCE, coherence),
        *_ovr_err_col_updates(),
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

        # ── MAIN CONTENT
        gr.Markdown("## Overall Verdict")
        gr.Markdown("You have rated all individual turns. Now give your overall assessment of this game session.")

        # ── G1: Strategic Coherence
        with gr.Group(elem_classes=["question-card"]):
            gr.Markdown("### G1 — Strategic Coherence")
            gr.Markdown("How well did the AI stick to and adapt its plan throughout the game?")
            coh_cols, coh_btns = [], []
            with gr.Row(equal_height=True):
                for v, name, desc in _COHERENCE:
                    with gr.Column(scale=1, min_width=0, elem_classes=["coh-col"]) as col:
                        gr.Markdown(f"## {v}", elem_classes=["coh-num-md"])
                        gr.Markdown(f"**{name}**", elem_classes=["coh-lbl-md"])
                        gr.Markdown(desc, elem_classes=["coh-desc-md"])
                        btn = gr.Button("Select", size="sm", variant="secondary",
                                        elem_classes=["coh-sel-btn"])
                    coh_cols.append(col)
                    coh_btns.append(btn)
            coherence = gr.Textbox(value="", visible=False)

        # ── G2: Overall Game Quality
        with gr.Group(elem_classes=["question-card"]):
            gr.Markdown("### G2 — Overall Game Quality")
            gr.Markdown("Looking at the whole game, how well did the AI actually play to achieve the main goal?")
            ovr_rows, ovr_btns, ovr_err_cols = [], [], []
            for v, label, desc in _OVERALL_RATINGS:
                with gr.Row(elem_classes=["ovr-row"]) as row:
                    with gr.Column(scale=0, min_width=46, elem_classes=["ovr-num"]):
                        gr.HTML(
                            f'<div style="width:36px;height:36px;background:#1e293b;'
                            f'border:1px solid #2d3748;border-radius:6px;'
                            f'display:flex;align-items:center;justify-content:center;'
                            f'font-size:14px;font-weight:700;color:#94a3b8;">{v}</div>'
                        )
                    with gr.Column(scale=0, min_width=130, elem_classes=["ovr-label"]):
                        gr.Markdown(f"**{label}**")
                    with gr.Column(scale=1, elem_classes=["ovr-desc"]):
                        gr.Markdown(desc)
                    with gr.Column(scale=0, min_width=76, elem_classes=["ovr-sel-btn"]) as err_col:
                        btn = gr.Button("Select", size="sm", variant="secondary")
                ovr_rows.append(row)
                ovr_btns.append(btn)
                ovr_err_cols.append(err_col)
            overall = gr.Textbox(value="", visible=False)

        # ── Comment
        comment = gr.Textbox(
            placeholder="Any overall observations about this game? (optional)",
            lines=4, show_label=False,
            elem_classes=["verdict-comment"],
        )

        status = gr.Markdown("")

        with gr.Row():
            back_btn = gr.Button("← Back to Annotation", variant="secondary")
            submit_btn = gr.Button("Submit Verdict", variant="primary")

        # ── EVENT WIRING 
        for i, (val, *_) in enumerate(_COHERENCE):
            coh_btns[i].click(
                fn=lambda v=val: _coh_select(v),
                outputs=[*coh_cols, *coh_btns, coherence],
            )

        for i, (val, *_) in enumerate(_OVERALL_RATINGS):
            ovr_btns[i].click(
                fn=lambda v=val: _overall_select(v),
                outputs=[*ovr_rows, *ovr_err_cols, *ovr_btns, overall],
            )

        submit_btn.click(
            fn=_save_verdict,
            inputs=[coherence, overall, comment],
            outputs=[status, *coh_cols, *ovr_err_cols],
        )

        back_btn.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[annotation_page, verdict_page],
        )
