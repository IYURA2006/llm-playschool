import html
import json
from datetime import datetime

import gradio as gr

import db
from annotation import (DEFAULT_GAME, load_game, game_slug, slug_to_path,
                        whole_game_questions, whole_game_only)

# TODO: placeholder completion code — swap for the real one before going live.
PROLIFIC_COMPLETION_URL = "https://app.prolific.com/submissions/complete?cc=C10WMMGK"

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


def _overall_update(err):
    # .ovr-slider-err is a dedicated rule since a Slider's DOM shape differs
    # from a Radio's (reused for the coherence radio-error styling too).
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
    # Hybrid-only game-specific whole-game question(s): mandatory when shown.
    # `specific` is the accumulated {str(index): value} dict from specific_state.
    wg = whole_game_questions(game_path or DEFAULT_GAME, condition)
    wg_only = whole_game_only(game_path or DEFAULT_GAME, condition)
    specific = specific or {}
    specific_incomplete = bool(wg) and any(
        not specific.get(str(i)) for i in range(len(wg)))
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
            *_col_updates(_COHERENCE, coherence, err=True),
            *([gr.skip()] * 4),                      # coh_btns unchanged
            _overall_update(err=True),
            gr.skip(), gr.skip(),                     # overall_touched, comment
            gr.skip(),                                # specific_state unchanged
            False,                                    # verdict_ok_state
        )

    slug = game_slug(game_path or DEFAULT_GAME)
    # For whole_game_only games the generic G1/G2 are hidden, so don't persist
    # their (untouched) defaults — store NULL; the answers live in verdict_specific.
    save_coherence = None if wg_only else coherence
    save_overall = None if wg_only else int(overall)
    ok = db.save_verdict(
        slug, annotator_id, condition, save_coherence, save_overall, comment or "",
        verdict_specific=json.dumps(specific) if (wg and specific) else None,
    )
    if not ok:
        return (
            "⚠️ Turn annotations not found. Please complete Step 1 first.",
            gr.skip(), gr.skip(),
            *_col_updates(_COHERENCE, coherence),
            *([gr.skip()] * 4),
            _overall_update(err=False),
            gr.skip(), gr.skip(),
            gr.skip(),                                # specific_state unchanged
            False,
        )

    # Saved OK. _verdict_finish sets the real status text; only clear widgets
    # here if we're about to advance to another playlist game.
    if playlist and playlist_idx + 1 < len(playlist):
        return (
            "",
            True,                          # clearing_state — blank annotation page
            "",                            # coherence value
            *_col_updates(_COHERENCE, ""),
            *_btn_updates(_COHERENCE, ""),
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
        *_col_updates(_COHERENCE, coherence),
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
            "🎉 Thank you — you've completed all games in this session! "
            "You'll be redirected to Prolific to confirm completion. If "
            f"nothing happens, [click here to return to Prolific]({PROLIFIC_COMPLETION_URL}).",
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
        # Relabel here, not via a separate listener on playlist_idx_state —
        # a standalone listener races with annotation.py's own @gr.render.
        gr.update(value=_action_label(playlist, new_idx)),
    )


def build(welcome_page, annotation_page, verdict_page,
          game_state, annotator_state, block_state, playlist_state,
          playlist_idx_state, started_at_state, session_day_state, clearing_state):
    with verdict_page:

        # Re-renders whenever game_state changes, to reflect whichever game
        # was selected for annotation.
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
        # whole_game_only games — see #verdict-page.hide-generic in app.py.
        with gr.Group(elem_classes=["question-card", "g1-card"]):
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

        with gr.Group(elem_classes=["question-card", "g2-card"]):
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
            # overall_touched tracks whether this was actually moved (see the
            # docstring on _verdict_save_and_clear for why the value alone can't tell).
            overall = gr.Slider(
                minimum=1, maximum=7, step=1, value=4,
                show_label=False, elem_classes=["ovr-slider"],
            )
            overall_touched = gr.State(False)

        # Bespoke whole-game questions, hybrid only. The .wg-only marker below
        # tells the head script to hide G1/G2 for whole_game_only games.
        # Each widget writes into specific_state so the save chain stays fixed-shape.
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
                for idx, (q_md, choices) in enumerate(questions):
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
                                      show_label=False, elem_classes=["ovr-slider"])
                        # .release only (a user gesture) — an untouched slider stays
                        # OUT of specific_state, so validation still catches "unanswered".
                        s.release(
                            fn=lambda val, cur, i=idx: {**(cur or {}), str(i): str(int(val))},
                            inputs=[s, specific_state], outputs=[specific_state],
                        )
                    else:
                        r = gr.Radio(choices=choices, show_label=False,
                                     elem_classes=["scale-radio"])
                        r.change(
                            fn=lambda val, cur, i=idx: {**(cur or {}), str(i): val},
                            inputs=[r, specific_state], outputs=[specific_state],
                        )

        comment = gr.Textbox(
            placeholder="Any overall observations about this game? (optional)",
            lines=4, show_label=False,
            elem_classes=["verdict-comment"],
        )

        status = gr.Markdown("")

        with gr.Row():
            back_btn = gr.Button("← Back to Annotation", variant="secondary")
            action_btn = gr.Button(_action_label([], 0), variant="primary")

        # Local-only pass-through signal for the click chain below — never
        # threaded to app.py.
        verdict_ok_state = gr.State(False)

        for i, (val, *_) in enumerate(_COHERENCE):
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
            # Client-side only — redirects to Prolific after the thank-you message shows.
            fn=None,
            inputs=[status],
            outputs=None,
            js="""(status) => {
                if (status && status.includes("completed all games")) {
                    setTimeout(() => {
                        window.location.href = "%s";
                    }, 3000);
                }
            }""" % PROLIFIC_COMPLETION_URL,
        )

        # Syncs the label for the first view only — _verdict_finish's own
        # action_btn output resyncs it after that (see its comment above).
        def _sync_action_label(playlist, playlist_idx):
            return gr.update(value=_action_label(playlist, playlist_idx))

        playlist_state.change(fn=_sync_action_label,
                              inputs=[playlist_state, playlist_idx_state],
                              outputs=[action_btn])

        back_btn.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[annotation_page, verdict_page],
        )
