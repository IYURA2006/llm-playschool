from datetime import datetime

import gradio as gr

import db
from annotation import DEFAULT_GAME, load_game, game_slug, slug_to_path

_COHERENCE = [
    ("1", "No plan",   "Each move seems disconnected from the last — no consistent logic across turns."),
    ("2", "Rigid",     "Had a plan but kept following it even when feedback clearly showed it was not working."),
    ("3", "Adaptive",  "Maintained a clear goal and adjusted its approach when something was not working."),
    ("4", "Strategic", "Built every new piece of information into its plan smoothly."),
]

_OVERALL_RATINGS = [
    ("1", "Broken",    "The AI did not follow the game's format or rules. The game could not continue properly because of this."),
    ("2", "Blind",     "Stumbled around blindly — choices constantly hurt its own chances of winning."),
    ("3", "Sloppy",    "Understood the goal but played poorly — wasted turns or missed obvious opportunities."),
    ("4", "Bare min.", "Followed basic rules and avoided huge mistakes but showed no real strategy."),
    ("5", "Solid",     "Actively pushed toward the goal with smart, logical moves and handled the game smoothly."),
    ("6", "Skilled",   "Played at a high level, optimised its path to victory, and adapted perfectly."),
    ("7", "Flawless",  "The AI played as well as a skilled human would. Every move was sharp and purposeful."),
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


def _coh_select(chosen):
    return (*_col_updates(_COHERENCE, chosen), *_btn_updates(_COHERENCE, chosen), chosen)


def _survey_update(value, err):
    # Red ring on an unanswered pilot-survey radio, reusing .radio-error CSS.
    classes = ["scale-radio", "radio-error"] if (err and not value) else ["scale-radio"]
    return gr.update(elem_classes=classes)


def _save_verdict(game_path, annotator_id, condition, playlist, playlist_idx,
                  coherence, overall, comment, fit, fatigue, confidence,
                  fb_comment):
    err = not coherence or not fit or not fatigue or not confidence

    # Error paths return a NO-OP update for next_btn, not visible=False: the
    # button starts hidden/unmounted, and Gradio 6 never shows a component
    # again once visible=False reached it before its first mount.
    if err:
        return (
            "⚠️ Please fill in the highlighted fields before submitting.",
            *_col_updates(_COHERENCE, coherence, err=True),
            _survey_update(fit, err=True),
            _survey_update(fatigue, err=True),
            _survey_update(confidence, err=True),
            gr.update(),
        )

    slug = game_slug(game_path or DEFAULT_GAME)
    ok = db.save_verdict(slug, annotator_id, condition, coherence, int(overall),
                         comment or "", survey_fit=fit, survey_fatigue=fatigue,
                         survey_confidence=confidence,
                         survey_comment=fb_comment or "")
    if not ok:
        return (
            "⚠️ Turn annotations not found. Please complete Step 1 first.",
            *_col_updates(_COHERENCE, coherence),
            _survey_update(fit, err=False),
            _survey_update(fatigue, err=False),
            _survey_update(confidence, err=False),
            gr.update(),
        )

    if playlist:
        if playlist_idx + 1 < len(playlist):
            status = "✅ Saved — click **Next game →** to continue."
            next_visible = True
        else:
            status = (f"🎉 **All done!** All {len(playlist)} games for today are "
                      f"submitted. Thank you — you can close this tab.")
            next_visible = False
    else:
        status = "✅ Verdict saved."
        next_visible = False

    return (
        status,
        *_col_updates(_COHERENCE, coherence),
        _survey_update(fit, err=False),
        _survey_update(fatigue, err=False),
        _survey_update(confidence, err=False),
        gr.update(visible=next_visible),
    )


def _next_game(playlist, playlist_idx):
    """Advance to the next playlist item: swap game+condition (the annotation
    page's @gr.render rebuilds off these), restart the timer, reset every
    verdict/survey widget, and flip back to the annotation page."""
    new_idx = min(playlist_idx + 1, len(playlist) - 1)
    item = playlist[new_idx]
    path = slug_to_path(item["game"]) or DEFAULT_GAME
    return (
        new_idx,
        path,
        item["condition"],
        datetime.now().isoformat(),
        gr.update(visible=True),    # annotation_page
        gr.update(visible=False),   # verdict_page
        "",                          # status
        gr.update(visible=False),   # next_btn
        "",                          # coherence value
        *_col_updates(_COHERENCE, ""),
        *_btn_updates(_COHERENCE, ""),
        gr.update(value=4),          # overall slider
        "",                          # verdict comment
        gr.update(value=None, elem_classes=["scale-radio"]),  # fit
        gr.update(value=None, elem_classes=["scale-radio"]),  # fatigue
        gr.update(value=None, elem_classes=["scale-radio"]),  # confidence
        "",                          # survey comment
    )


def build(welcome_page, annotation_page, verdict_page, game_state, annotator_state,
          block_state, playlist_state, playlist_idx_state, started_at_state,
          session_day_state):
    with verdict_page:

        # ── TOP NAV ───────────────────────────────────────────────────
        # The game id/name reflects whichever game was selected for annotation,
        # so it re-renders whenever game_state changes.
        with gr.Row(elem_classes=["annot-topnav"]):
            @gr.render(inputs=[game_state])
            def _verdict_nav(path):
                meta = load_game(path or DEFAULT_GAME).meta
                gr.HTML(
                    f'<div class="nav-left">'
                    f'<span class="game-id-tag">#{meta["game_id"]}</span>'
                    f'<span class="game-name-tag">{meta["game_name"].title()}</span>'
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
            _lo_v, _lo_lbl, _lo_desc = _OVERALL_RATINGS[0]
            _hi_v, _hi_lbl, _hi_desc = _OVERALL_RATINGS[-1]
            gr.HTML(
                '<div class="ovr-slider-ends">'
                f'<div class="ovr-end ovr-end-lo"><span class="ovr-end-num">{_lo_v}</span>'
                f'<span class="ovr-end-lbl">{_lo_lbl}</span>'
                f'<span class="ovr-end-desc">{_lo_desc}</span></div>'
                f'<div class="ovr-end ovr-end-hi"><span class="ovr-end-num">{_hi_v}</span>'
                f'<span class="ovr-end-lbl">{_hi_lbl}</span>'
                f'<span class="ovr-end-desc">{_hi_desc}</span></div>'
                '</div>'
            )
            overall = gr.Slider(
                minimum=1, maximum=7, step=1, value=4,
                label="Overall rating", show_label=False,
                elem_classes=["ovr-slider"],
            )

        # ── Comment
        comment = gr.Textbox(
            placeholder="Any overall observations about this game? (optional)",
            lines=4, show_label=False,
            elem_classes=["verdict-comment"],
        )

        # ── Pilot micro-survey — feedback about the STUDY DESIGN, not the AI.
        # This is the actual outcome measure of the internal A/B test: which
        # question set fits which game, and how draining a session is.
        with gr.Group(elem_classes=["question-card"]):
            gr.Markdown("### 📋 Pilot feedback — about the questions, not the AI")
            gr.Markdown("How well did the questions you just answered fit this game?")
            survey_fit = gr.Radio(
                choices=[("1\nDidn't fit", "1"), ("2\nAwkward", "2"),
                         ("3\nMostly fit", "3"), ("4\nPerfect fit", "4")],
                show_label=False, elem_classes=["scale-radio"],
            )
            gr.Markdown("How mentally drained do you feel right now?")
            survey_fatigue = gr.Radio(
                choices=[("1\nFresh", "1"), ("2\nFine", "2"),
                         ("3\nTiring", "3"), ("4\nDrained", "4")],
                show_label=False, elem_classes=["scale-radio"],
            )
            gr.Markdown("How confident are you in the ratings you just gave for this game?")
            survey_confidence = gr.Radio(
                choices=[("1\nGuessing", "1"), ("2\nUnsure", "2"),
                         ("3\nMixed", "3"), ("4\nConfident", "4"),
                         ("5\nCertain", "5")],
                show_label=False, elem_classes=["scale-radio"],
            )
            survey_comment = gr.Textbox(
                placeholder="Anything confusing or missing in the questions or the app? (optional)",
                lines=2, show_label=False,
                elem_classes=["verdict-comment"],
            )

        status = gr.Markdown("")

        with gr.Row():
            back_btn = gr.Button("← Back to Annotation", variant="secondary")
            submit_btn = gr.Button("Submit Verdict", variant="primary")
            next_btn = gr.Button("Next game →", variant="primary", visible=False)

        # ── EVENT WIRING
        for i, (val, *_) in enumerate(_COHERENCE):
            coh_btns[i].click(
                fn=lambda v=val: _coh_select(v),
                outputs=[*coh_cols, *coh_btns, coherence],
            )

        submit_btn.click(
            fn=_save_verdict,
            inputs=[game_state, annotator_state, block_state, playlist_state,
                    playlist_idx_state, coherence, overall, comment,
                    survey_fit, survey_fatigue, survey_confidence, survey_comment],
            outputs=[status, *coh_cols, survey_fit, survey_fatigue,
                     survey_confidence, next_btn],
        )

        next_btn.click(
            fn=_next_game,
            inputs=[playlist_state, playlist_idx_state],
            outputs=[playlist_idx_state, game_state, block_state, started_at_state,
                     annotation_page, verdict_page, status, next_btn,
                     coherence, *coh_cols, *coh_btns, overall, comment,
                     survey_fit, survey_fatigue, survey_confidence, survey_comment],
        )

        back_btn.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[annotation_page, verdict_page],
        )
