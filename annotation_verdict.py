import html
import json
from datetime import datetime

import gradio as gr

import db
from annotation import (DEFAULT_GAME, load_game, game_slug, slug_to_path,
                        plain_label, question_spec, question_spec_hash,
                        whole_game_questions, whole_game_only)

# The completion code belongs to ONE Prolific study. This is the pilot's; the
# main study gets its own from Prolific and readiness.py blocks until it is set.
PROLIFIC_COMPLETION_URL = "https://app.prolific.com/submissions/complete?cc=C7ZBGQYY"

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


# Every handler that writes into specific_state. Each one reads the dict, adds
# its own answer and returns the result, so two that run at the same moment
# both start from the same dict and the second overwrites the first: one of
# two answers given in quick succession vanished, and the save was refused
# with both visibly selected. Sharing a concurrency id makes them queue.
_ONE_AT_A_TIME = {"concurrency_id": "verdict_specific", "concurrency_limit": 1}


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
        # Name what is missing. One generic line left people hunting: the
        # slider shows a number before it has been touched, so it looks
        # answered, and a missing game-specific answer was not marked at all.
        missing = []
        if not wg_only and not coherence:
            missing.append("**G1** — choose one of the four options")
        if not wg_only and not overall_touched:
            missing.append("**G2** — set the slider (click or move it, even if "
                           "the value you want is already showing)")
        if specific_incomplete:
            missing.append("**This game specifically** — answer every "
                           "question in that box")
        return (
            "Not saved yet. Still needed: " + "; ".join(missing) + ".",
            gr.skip(),                              # clearing_state
            gr.skip(),                               # coherence value
            *_col_updates(COHERENCE, coherence, err=True),
            *([gr.skip()] * 4),                      # coh_btns unchanged
            # Red only when the slider itself is what is missing.
            _overall_update(err=not wg_only and not overall_touched),
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
            "The study was updated while you were working on this game. "
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
            "Turn annotations not found. Please complete Step 1 first.",
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


def _save_and_clear_flagged(*args):
    """_verdict_save_and_clear plus one trailing output: "1" when it has just
    asked for the page to be blanked, "" otherwise. The click chain's next step
    is browser-side and needs to know which, and it cannot read a gr.State."""
    out = _verdict_save_and_clear(*args)
    return (*out, "1" if out[1] is True else "")


# Runs in the browser between "blank the page" and "mount the next game", and
# holds the chain until the old game's widgets have really left the DOM.
#
# Without it the two steps race. The blank is a render, the mount is a render,
# and nothing made the second wait for the first; when the server or the
# connection was slow enough for the mount to overtake, Gradio reused the old
# widgets for the new game. The next transcript then opened with the previous
# one's answers and comments already filled in, and they were saved as that
# transcript's ratings unless the participant happened to change them. Seen in
# about 1 of every 150 game switches with six people working at once.
#
# The timeout is a floor on how bad things can get, not the mechanism: after
# 8 seconds it lets the chain continue rather than strand the participant.
#
# It is attached to _verdict_finish as that event's own js=, which Gradio
# awaits before calling the server, and it hands its arguments back untouched.
# A separate js-only step in between does not work: in this Gradio a .then()
# after an event with no server function never fires, so the chain stopped
# there and the next game never mounted.
_AWAIT_BLANK_JS = """async (...args) => {
    if (args[args.length - 1] === "1") {
        const stale = () => document.querySelector(
            '#annot-page .turn-anno-card, #verdict-page .g3-card');
        const t0 = Date.now();
        while (stale() && Date.now() - t0 < 8000) {
            await new Promise((r) => setTimeout(r, 40));
        }
    }
    return args;
}"""


def _verdict_finish(ok, playlist, playlist_idx, _await_blank=None):
    """Step 3 (chained via .then): route to the next game, or a static
    "saved"/"all done" status. Leaves clearing_state alone on the last-game
    branch — it only matters while still inside the annotation flow."""
    if not ok:
        return (gr.skip(),) * 10
    if not playlist:
        return (
            gr.skip(), "Verdict saved.", gr.skip(), gr.skip(), gr.skip(),
            gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
        )
    if playlist_idx + 1 >= len(playlist):
        return (
            gr.skip(),                   # clearing_state — irrelevant from here on
            # The link is the action, not a fallback. A 3s auto-redirect used
            # to sit here, but that is a time limit the user cannot control
            # (WCAG 2.2.1) and it cut screen-reader users off mid-sentence.
            "Thank you — you've completed all games in this session!\n\n"
            f"### [Return to Prolific to confirm completion]({PROLIFIC_COMPLETION_URL})\n\n"
            "Your work is already saved. You must follow this link for your "
            "participation to be recorded on Prolific.",
            gr.skip(), gr.skip(), gr.skip(), gr.skip(),
            gr.skip(),                   # annotation_page — never shown again
            gr.skip(),                   # verdict_page — STAYS visible, showing the message
            # Both buttons go. Left on screen they sat directly under the
            # completion link, still live, and "Save & Finish Study" read as
            # the thing to press - when the link above it is what gets a
            # participant paid. Both are mounted, so hiding them is safe.
            gr.update(visible=False),    # action_btn
            gr.update(visible=False),    # back_btn
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
        gr.skip(),                    # back_btn
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
            # Set by the save step, read by _AWAIT_BLANK_JS. A Textbox rather
            # than a State because the browser has to be able to read it.
            await_blank = gr.Textbox(value="", visible=False)

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

        # clearing_state is an input for the same reason it is on the annotation
        # page: rendering nothing between games is the only thing that unmounts
        # these widgets. Without it Gradio reuses them, so the next game opened
        # with the previous game's answer already selected - and because
        # specific_state had been reset, clicking that same answer fired no
        # change event and the verdict could not be submitted at all.
        #
        # triggers= is as important as the input. By default a render re-runs
        # on a change to ANY input, and a game switch changes several, so the
        # questions were being rebuilt two or three times in a row - each
        # rebuild throwing away whatever had just been clicked. clearing_state
        # flips exactly twice per switch: True to blank, False to mount.
        @gr.render(inputs=[game_state, block_state, clearing_state],
                   triggers=[clearing_state.change])
        def _specific_overall(path, block, clearing):
            if clearing:
                return
            questions = whole_game_questions(path or DEFAULT_GAME, block)
            if not questions:
                return
            slider_mode = whole_game_only(path or DEFAULT_GAME, block)
            # g3-card is what _AWAIT_BLANK_JS looks for.
            cls = ["question-card", "g3-card"] + (["wg-only"] if slider_mode else [])
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
                        # User-driven events only, never .change, so an untouched
                        # slider stays out of specific_state and still counts as
                        # unanswered.
                        s.release(
                            fn=lambda val, cur, k=qid: {**(cur or {}), k: str(int(val))},
                            inputs=[s, specific_state], outputs=[specific_state],
                            **_ONE_AT_A_TIME,
                        )
                        # Keyboard users: see overall.input below.
                        s.input(
                            fn=lambda val, cur, k=qid: {**(cur or {}), k: str(int(val))},
                            inputs=[s, specific_state], outputs=[specific_state],
                            show_progress="hidden", trigger_mode="always_last",
                            **_ONE_AT_A_TIME,
                        )
                    else:
                        r = gr.Radio(choices=choices, label=plain_label(q_md),
                                     show_label=False,
                                     elem_classes=["scale-radio"])
                        r.change(
                            fn=lambda val, cur, k=qid: {**(cur or {}), k: val},
                            inputs=[r, specific_state], outputs=[specific_state],
                            **_ONE_AT_A_TIME,
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
        # .release is a pointer event, so the arrow keys never fired it and a
        # keyboard user could move the slider but never have it count. .input
        # fires on any change the user makes and not on the programmatic reset
        # between games, which is the property overall_touched depends on.
        overall.input(fn=lambda: True, outputs=[overall_touched],
                      show_progress="hidden", trigger_mode="always_last")

        # See _verdict_save_and_clear's docstring for why save+clear stays one event.
        action_btn.click(
            fn=_save_and_clear_flagged,
            inputs=[game_state, annotator_state, block_state, playlist_state,
                    playlist_idx_state, coherence, overall, overall_touched,
                    comment, specific_state],
            outputs=[status, clearing_state, coherence, *coh_cols, *coh_btns,
                     overall, overall_touched, comment, specific_state,
                     verdict_ok_state, await_blank],
        ).then(
            fn=_verdict_finish,
            # await_blank last: _AWAIT_BLANK_JS reads it from that position.
            inputs=[verdict_ok_state, playlist_state, playlist_idx_state,
                    await_blank],
            js=_AWAIT_BLANK_JS,
            outputs=[clearing_state, status, playlist_idx_state, game_state,
                     block_state, started_at_state, annotation_page, verdict_page,
                     action_btn, back_btn],
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
