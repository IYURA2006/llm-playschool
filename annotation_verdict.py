import html
import json
from datetime import datetime

import gradio as gr

import db
from annotation import (DEFAULT_GAME, load_game, game_slug, slug_to_path,
                        whole_game_questions, whole_game_only)

# Prolific's "submit" completion URL for this study, appended with the study's
# completion code (?cc=...) — Prolific uses this to auto-approve participants
# who reach it. TODO: this is the placeholder code for now; swap for the real
# study's completion URL/code before sending live Prolific links.
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
    """Step 1 of the merged action button: validate + persist, AND (only if
    that succeeds and another playlist game remains) blank/reset every
    verdict widget in the SAME event — mirroring the old _next_game_clear,
    which likewise set clearing_state=True together with every widget reset
    in one event. Keeping validate+save+clear as ONE event (not two chained
    ones) matters: an earlier version split "clear" into its own .then()
    step, and that separate event — despite being a no-op on error and
    otherwise identical in shape to the old code — reliably threw ~200
    frontend "Cannot read properties of null" errors on every game switch
    that the old 2-event (this event + _verdict_finish) shape never did.
    Root cause not fully pinned down (likely a Gradio 6 quirk with 3-deep
    .then() chains feeding into annotation.py's own @gr.render), but the
    old 2-event shape is proven not to trigger it, so this collapses back
    to that shape rather than chasing the internals further.

    A slider never reports a falsy value (minimum=1), so "not overall" can
    no longer detect an untouched G2 — overall_touched (set only by the
    slider's .release() event, never by a programmatic reset) is the sole
    authority on that."""
    # Hybrid-only game-specific whole-game question(s): mandatory when shown.
    # `specific` is the accumulated {str(index): value} dict from specific_state.
    wg = whole_game_questions(game_path or DEFAULT_GAME, condition)
    wg_only = whole_game_only(game_path or DEFAULT_GAME, condition)
    specific = specific or {}
    specific_incomplete = bool(wg) and any(
        not specific.get(str(i)) for i in range(len(wg)))
    # whole_game_only games (imagegame) hide G1/G2 — only the specific sliders
    # are required there; every other game still requires coherence + a
    # touched overall slider.
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

    # Saved OK. Real status text is set by _verdict_finish (it knows which
    # branch — advance / finish study / legacy — actually fires); only clear
    # the widgets here if we're about to advance to another playlist game.
    if playlist and playlist_idx + 1 < len(playlist):
        return (
            "",
            True,                          # clearing_state — blank annotation page
            "",                            # coherence value
            *_col_updates(_COHERENCE, ""),
            *_btn_updates(_COHERENCE, ""),
            # Reset value is cosmetic only — overall_touched (reset right
            # after) is what actually gates validation, so this number is
            # never mistaken for a recorded judgment even if briefly shown.
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
    "saved"/"all done" status — whichever applies. Deliberately does NOT
    touch clearing_state on the last-game branch: that mechanism only
    protects annotation.py's @gr.render from stale widgets, which stops
    mattering once the annotator is leaving the annotation flow for good."""
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
        # Relabel in the SAME atomic event that advances playlist_idx_state,
        # rather than a separate .change() listener on that state — a
        # standalone listener reacting to the same mutation raced with
        # annotation.py's own @gr.render (also keyed on playlist_idx_state),
        # throwing frontend "Cannot read properties of null" errors.
        gr.update(value=_action_label(playlist, new_idx)),
    )


def build(welcome_page, annotation_page, verdict_page,
          game_state, annotator_state, block_state, playlist_state,
          playlist_idx_state, started_at_state, session_day_state, clearing_state):
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

        # ── MAIN CONTENT
        gr.Markdown("## Overall Verdict")
        gr.Markdown("You have rated all individual turns. Now give your overall assessment of this game session.")

        # ── G1: Strategic Coherence
        # (.g1-card / .g2-card let the head script hide the generic pair for
        # whole_game_only games — see #verdict-page.hide-generic in app.py.)
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

        # ── G2: Overall Game Quality
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
            # A slider, with explicit "touched" tracking: unlike a Radio, a
            # Slider always reports a numeric value (never falsy), so "chose
            # 4" and "never touched it" are indistinguishable from the value
            # alone. overall_touched is flipped True only by .release() — the
            # user-gesture-only event that (unlike .change()) never fires on a
            # programmatic gr.update(value=...) reset — and is what
            # _verdict_save_and_clear actually checks instead of the slider's value.
            overall = gr.Slider(
                minimum=1, maximum=7, step=1, value=4,
                show_label=False, elem_classes=["ovr-slider"],
            )
            overall_touched = gr.State(False)

        # ── Game-specific whole-game question(s) — HYBRID condition only.
        # Normally rendered ON TOP of the generic G1/G2 (kept for everyone). For a
        # "whole_game_only" game (imagegame) these REPLACE G1/G2 — the .wg-only
        # marker below tells the head script to hide .g1-card/.g2-card, and the
        # server skips the coherence/overall checks (see _verdict_save_and_clear).
        # Each widget writes its value into specific_state ({index: value}) so the
        # fixed-shape save chain needs only ONE extra input, never dynamic ones.
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

        # ── Comment
        comment = gr.Textbox(
            placeholder="Any overall observations about this game? (optional)",
            lines=4, show_label=False,
            elem_classes=["verdict-comment"],
        )

        status = gr.Markdown("")

        with gr.Row():
            back_btn = gr.Button("← Back to Annotation", variant="secondary")
            action_btn = gr.Button(_action_label([], 0), variant="primary")

        # Local-only pass-through signal for the 3-step click chain below —
        # never threaded to app.py.
        verdict_ok_state = gr.State(False)

        # ── EVENT WIRING
        for i, (val, *_) in enumerate(_COHERENCE):
            coh_btns[i].click(
                fn=lambda v=val: _coh_select(v),
                outputs=[*coh_cols, *coh_btns, coherence],
            )

        overall.release(fn=lambda: True, outputs=[overall_touched])

        # ONE button now does what used to take two clicks: validate+save
        # (and, if continuing, blank every verdict widget) in ONE event, then
        # (chained via .then) advance to the next game / show the "all done"
        # message — the exact same TWO-event clearing_state shape the old
        # "Next game →" flow used (_next_game_clear that also did the reset,
        # then _next_game_advance), just with the leading event now also
        # doing validate+save. See _verdict_save_and_clear's docstring for
        # why this stayed two events rather than three.
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
            # Pure client-side (fn=None skips a server round trip): reads the
            # status Markdown _verdict_finish just wrote and redirects to
            # Prolific only on the genuine "all done" branch (identified by
            # its fixed substring) — never on the "advance to next game"
            # branch, where status is "". A few seconds' delay lets the
            # participant actually see the thank-you message before leaving.
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

        # Syncs the button label for the very FIRST view of this page in a
        # sitting (playlist_state is assigned once, on page load / Start,
        # before verdict_page is ever shown — this fires reliably then,
        # mirroring the top-nav game-name @gr.render's proven reliance on the
        # same mechanism). Deliberately NOT also listening on
        # playlist_idx_state: that state is mutated by _verdict_finish above,
        # and a second independent listener reacting to the same mutation
        # raced with annotation.py's own @gr.render (also keyed on
        # playlist_idx_state), corrupting the frontend — _verdict_finish's
        # own action_btn output (this same atomic event) handles resyncing
        # the label for every game after the first instead.
        def _sync_action_label(playlist, playlist_idx):
            return gr.update(value=_action_label(playlist, playlist_idx))

        playlist_state.change(fn=_sync_action_label,
                              inputs=[playlist_state, playlist_idx_state],
                              outputs=[action_btn])

        back_btn.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[annotation_page, verdict_page],
        )
