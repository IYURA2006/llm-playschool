import html
import json
from datetime import datetime

import gradio as gr

import db
from annotation import (DEFAULT_GAME, load_game, game_slug, slug_to_path,
                        plain_label, question_spec, question_spec_hash,
                        whole_game_questions, whole_game_only)

# TODO: placeholder completion code — swap for the real one before going live.
PROLIFIC_COMPLETION_URL = "https://app.prolific.com/submissions/complete?cc=C10WMMGK"

COHERENCE = [
    ("1", "No plan",   "Each move seems disconnected from the last — no consistent logic across turns."),
    ("2", "Rigid",     "Had a plan but kept following it even when feedback clearly showed it was not working."),
    ("3", "Adaptive",  "Maintained a clear goal and adjusted its approach when something was not working."),
    ("4", "Strategic", "Built every new piece of information into its plan smoothly."),
]

OVERALL_RATINGS = [
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
    return (*_col_updates(COHERENCE, chosen), *_btn_updates(COHERENCE, chosen), chosen)


def _overall_update(err):
    # A Slider's DOM differs from a Radio's, so the error style needs its own rule.
    classes = ["ovr-slider", "ovr-slider-err"] if err else ["ovr-slider"]
    return gr.update(elem_classes=classes)


def _action_label(playlist, playlist_idx):
    """The merged save/advance button's label depends on where we are in the
    playlist — known at render time (no need to wait for a click)."""
    if not playlist:
        return "Save Verdict"
    if playlist_idx + 1 >= len(playlist):
        return "Save & Finish Study"
    return "Save & Next Game →"


def _verdict_save_and_clear(game_path, annotator_id, condition, playlist, playlist_idx,
                            coherence, overall, overall_touched, comment, specific):
    """Step 1 of the merged action button: validate + persist, and — only if
    that succeeds and another playlist game remains — blank every verdict
    widget in the same event. Splitting "clear" into its own .then() step
    threw frontend errors on every game switch, so this stays one event.

    A slider is never falsy, so overall_touched (set only by .release()) is
    the sole signal for whether G2 was actually answered."""
    # Game-specific whole-game questions; required when shown. `specific` is
    # {question_id: value}, keyed by id and never by position.
    wg = whole_game_questions(game_path or DEFAULT_GAME, condition)
    wg_only = whole_game_only(game_path or DEFAULT_GAME, condition)
    specific = specific or {}
    specific_incomplete = bool(wg) and any(
        not specific.get(qid) for qid, _md, _ch in wg)
    # whole_game_only games hide G1/G2, so only the specific sliders are required there.
    if wg_only:
        err = specific_incomplete
    else:
        err = not coherence or not overall_touched or specific_incomplete

    if err:
        return (
            "⚠️ Please fill in the highlighted fields before submitting.",
            gr.skip(),                              # clearing_state
            gr.skip(),                               # coherence value
            *_col_updates(COHERENCE, coherence, err=True),
            *([gr.skip()] * 4),                      # coh_btns unchanged
            _overall_update(err=True),
            gr.skip(), gr.skip(),                     # overall_touched, comment
            gr.skip(),                                # specific_state unchanged
            False,                                    # verdict_ok_state
        )

    slug = game_slug(game_path or DEFAULT_GAME)
    # G1/G2 are hidden for whole_game_only games, so store NULL rather than
    # their untouched defaults. The answers live in verdict_specific.
    save_coherence = None if wg_only else coherence
    save_overall = None if wg_only else int(overall)
    # Recompute the fingerprint; save_verdict refuses if it moved since the
    # turns were saved.
    try:
        _g = load_game(game_path or DEFAULT_GAME)
        _spec = question_spec(_g, condition, bool(_g.has_reasoning))
        _qs_hash = question_spec_hash(_spec)
    except Exception:
        _qs_hash = None
    try:
        ok = db.save_verdict(
            slug, annotator_id, condition, save_coherence, save_overall, comment or "",
            verdict_specific=json.dumps(specific) if (wg and specific) else None,
            question_set_hash=_qs_hash,
        )
    except db.QuestionSetChanged:
        return (
            "⚠️ The study was updated while you were working on this game. "
            "Please reload the page and rate this transcript again — your "
            "earlier games are safe.",
            gr.skip(), gr.skip(),
            *_col_updates(COHERENCE, coherence),
            *([gr.skip()] * 4),
            _overall_update(err=False),
            gr.skip(), gr.skip(), gr.skip(),
            False,
        )
    if not ok:
        return (
            "⚠️ Turn annotations not found. Please complete Step 1 first.",
            gr.skip(), gr.skip(),
            *_col_updates(COHERENCE, coherence),
            *([gr.skip()] * 4),
            _overall_update(err=False),
            gr.skip(), gr.skip(),
            gr.skip(),                                # specific_state unchanged
            False,
        )

    # Saved. _verdict_finish sets the status text; only clear the widgets if
    # another game follows.
    if playlist and playlist_idx + 1 < len(playlist):
        return (
            "",
            True,                          # clearing_state — blank annotation page
            "",                            # coherence value
            *_col_updates(COHERENCE, ""),
            *_btn_updates(COHERENCE, ""),
            # Cosmetic only — overall_touched (reset next) is what gates validation.
            gr.update(value=4, elem_classes=["ovr-slider"]),  # overall
            False,                         # overall_touched
            "",                            # verdict comment
            {},                            # specific_state — reset for next game
            True,                          # verdict_ok_state
        )

    # Last game or legacy mode — nothing to clear, leave every widget as-is.
    return (
        "",
        gr.skip(),
        gr.skip(),
        *_col_updates(COHERENCE, coherence),
        *([gr.skip()] * 4),
        _overall_update(err=False),
        gr.skip(), gr.skip(),
        gr.skip(),                                # specific_state unchanged
        True,
    )


def _verdict_finish(ok, playlist, playlist_idx):
    """Step 3 (chained via .then): route to the next game, or a static
    "saved"/"all done" status. Leaves clearing_state alone on the last-game
    branch — it only matters while still inside the annotation flow."""
    if not ok:
        return (gr.skip(),) * 9
    if not playlist:
        return (
            gr.skip(), "✅ Verdict saved.", gr.skip(), gr.skip(), gr.skip(),
            gr.skip(), gr.skip(), gr.skip(), gr.skip(),
        )
    if playlist_idx + 1 >= len(playlist):
        return (
            gr.skip(),                   # clearing_state — irrelevant from here on
            # The link is the action, not a fallback. A 3s auto-redirect used
            # to sit here, but that is a time limit the user cannot control
            # (WCAG 2.2.1) and it cut screen-reader users off mid-sentence.
            "🎉 Thank you — you've completed all games in this session!\n\n"
            f"### [Return to Prolific to confirm completion]({PROLIFIC_COMPLETION_URL})\n\n"
            "Your work is already saved. You must follow this link for your "
            "participation to be recorded on Prolific.",
            gr.skip(), gr.skip(), gr.skip(), gr.skip(),
            gr.skip(),                   # annotation_page — never shown again
            gr.skip(),                   # verdict_page — STAYS visible, showing the message
            gr.skip(),                   # action_btn — page is left behind
        )
    new_idx = playlist_idx + 1
    item = playlist[new_idx]
    path = slug_to_path(item["game"]) or DEFAULT_GAME
    return (
        False,                        # clearing_state — mount the new game
        "",
        new_idx, path, item["condition"],
        datetime.now().isoformat(),   # started_at
        gr.update(visible=True),      # annotation_page
        gr.update(visible=False),     # verdict_page
        # Relabel here: a separate listener would race with annotation.py's render.
        gr.update(value=_action_label(playlist, new_idx)),
    )


def build(welcome_page, annotation_page, verdict_page,
          game_state, annotator_state, block_state, playlist_state,
          playlist_idx_state, started_at_state, session_day_state, clearing_state):
    with verdict_page:
        # The screen had no h1. Hidden rather than promoting the visible
        # heading, which would resize it. Also the a11y focus target.
        gr.HTML('<h1 class="a11y-sr-only" tabindex="-1">'
                'Overall verdict — step 2 of 2</h1>')

        with gr.Row(elem_classes=["annot-topnav"]):
            @gr.render(inputs=[game_state])
            def _verdict_nav(path):
                meta = load_game(path or DEFAULT_GAME).meta
                gr.HTML(
                    f'<div class="nav-left">'
                    f'<span class="game-id-tag">#{html.escape(str(meta["game_id"]))}</span>'
                    f'<span class="game-name-tag">{html.escape(str(meta["game_name"]).title())}</span>'
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

        gr.Markdown("## Overall Verdict")
        gr.Markdown("You have rated all individual turns. Now give your overall assessment of this game session.")

        # .g1-card / .g2-card let the head script hide the generic pair for
        # whole_game_only games.
        with gr.Group(elem_classes=["question-card", "g1-card"]):
            gr.Markdown("### G1 — Strategic Coherence")
            gr.Markdown("How well did the AI stick to and adapt its plan throughout the game?")
            coh_cols, coh_btns = [], []
            with gr.Row(equal_height=True):
                for v, name, desc in COHERENCE:
                    with gr.Column(scale=1, min_width=0, elem_classes=["coh-col"]) as col:
                        # A div, not markdown: as "## {v}" these became four
                        # headings containing only a digit, which broke heading
                        # navigation. The number is decorative.
                        gr.HTML(f'<div class="coh-num">{v}</div>')
                        gr.Markdown(f"**{name}**", elem_classes=["coh-lbl-md"])
                        gr.Markdown(desc, elem_classes=["coh-desc-md"])
                        btn = gr.Button("Select", size="sm", variant="secondary",
                                        elem_classes=["coh-sel-btn"])
                    coh_cols.append(col)
                    coh_btns.append(btn)
            coherence = gr.Textbox(value="", visible=False)

        with gr.Group(elem_classes=["question-card", "g2-card"]):
            gr.Markdown("### G2 — Overall Game Quality")
            gr.Markdown("Looking at the whole game, how well did the AI actually play to achieve the main goal?")
            _lo_v, _lo_lbl, _lo_desc = OVERALL_RATINGS[0]
            _hi_v, _hi_lbl, _hi_desc = OVERALL_RATINGS[-1]
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
            # overall_touched tracks whether the slider was actually moved; the
            # value alone cannot tell. Gradio builds the aria-label as "range
            # slider for {label}", so the label must read as a noun phrase.
            overall = gr.Slider(
                minimum=1, maximum=7, step=1, value=4,
                label="G2 — Overall Game Quality, 1 to 7",
                show_label=False, elem_classes=["ovr-slider"],
            )
            overall_touched = gr.State(False)

        # Game-specific whole-game questions. Each widget writes into
        # specific_state, so the save chain keeps a fixed shape.
        specific_state = gr.State({})

        @gr.render(inputs=[game_state, block_state])
        def _specific_overall(path, block):
            questions = whole_game_questions(path or DEFAULT_GAME, block)
            if not questions:
                return
            slider_mode = whole_game_only(path or DEFAULT_GAME, block)
            cls = ["question-card"] + (["wg-only"] if slider_mode else [])
            with gr.Group(elem_classes=cls):
                gr.Markdown("### This game specifically" if slider_mode
                            else "### G3 — This game specifically")
                for qid, q_md, choices in questions:
                    gr.Markdown(q_md)
                    if slider_mode:
                        # 1-7 slider like G2, with the end anchors from the choices.
                        n = len(choices)
                        lo = choices[0][0].split("\n", 1)
                        hi = choices[-1][0].split("\n", 1)
                        lo_lbl = lo[1] if len(lo) > 1 else ""
                        hi_lbl = hi[1] if len(hi) > 1 else ""
                        gr.HTML(
                            '<div class="ovr-slider-ends">'
                            f'<div class="ovr-end ovr-end-lo"><span class="ovr-end-num">1</span>'
                            f'<span class="ovr-end-desc">{lo_lbl}</span></div>'
                            f'<div class="ovr-end ovr-end-hi"><span class="ovr-end-num">{n}</span>'
                            f'<span class="ovr-end-desc">{hi_lbl}</span></div></div>'
                        )
                        s = gr.Slider(minimum=1, maximum=n, step=1, value=(1 + n) // 2,
                                      label=f"{plain_label(q_md)}, 1 to {n}",
                                      show_label=False, elem_classes=["ovr-slider"])
                        # .release only, so an untouched slider stays out of
                        # specific_state and still counts as unanswered.
                        s.release(
                            fn=lambda val, cur, k=qid: {**(cur or {}), k: str(int(val))},
                            inputs=[s, specific_state], outputs=[specific_state],
                        )
                    else:
                        r = gr.Radio(choices=choices, label=plain_label(q_md),
                                     show_label=False,
                                     elem_classes=["scale-radio"])
                        r.change(
                            fn=lambda val, cur, k=qid: {**(cur or {}), k: val},
                            inputs=[r, specific_state], outputs=[specific_state],
                        )

        comment = gr.Textbox(
            placeholder="Any overall observations about this game? (optional)",
            label="Overall comments about this game (optional)",
            lines=4, show_label=False,
            elem_classes=["verdict-comment"],
        )

        status = gr.Markdown("", elem_id="verdict-status")

        with gr.Row():
            back_btn = gr.Button("← Back to Annotation", variant="secondary")
            action_btn = gr.Button(_action_label([], 0), variant="primary")

        # Local signal for the click chain below; never passed to app.py.
        verdict_ok_state = gr.State(False)

        for i, (val, *_) in enumerate(COHERENCE):
            coh_btns[i].click(
                fn=lambda v=val: _coh_select(v),
                outputs=[*coh_cols, *coh_btns, coherence],
            )

        overall.release(fn=lambda: True, outputs=[overall_touched])

        # See _verdict_save_and_clear's docstring for why save+clear stays one event.
        action_btn.click(
            fn=_verdict_save_and_clear,
            inputs=[game_state, annotator_state, block_state, playlist_state,
                    playlist_idx_state, coherence, overall, overall_touched,
                    comment, specific_state],
            outputs=[status, clearing_state, coherence, *coh_cols, *coh_btns,
                     overall, overall_touched, comment, specific_state,
                     verdict_ok_state],
        ).then(
            fn=_verdict_finish,
            inputs=[verdict_ok_state, playlist_state, playlist_idx_state],
            outputs=[clearing_state, status, playlist_idx_state, game_state,
                     block_state, started_at_state, annotation_page, verdict_page,
                     action_btn],
        ).then(
            # Move focus to the completion link: it is the last action of the
            # study and nothing else remains on the page.
            fn=None,
            inputs=[status],
            outputs=None,
            js="""(status) => {
                if (status && status.includes("completed all games")) {
                    setTimeout(() => {
                        var link = document.querySelector('#verdict-status a');
                        if (link) { link.setAttribute('tabindex', '0'); link.focus(); }
                    }, 150);
                }
            }""",
        )

        # First view only; _verdict_finish resyncs the label after that.
        def _sync_action_label(playlist, playlist_idx):
            return gr.update(value=_action_label(playlist, playlist_idx))

        playlist_state.change(fn=_sync_action_label,
                              inputs=[playlist_state, playlist_idx_state],
                              outputs=[action_btn])

        back_btn.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[annotation_page, verdict_page],
        )
