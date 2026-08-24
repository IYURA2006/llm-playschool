import html
from datetime import datetime

import gradio as gr

import consent
import db
import annotation
from annotation import GENERIC_Q1, GENERIC_Q2, GENERIC_Q3
from annotation_verdict import COHERENCE, OVERALL_RATINGS

_STEPS = [
    ("1", "Read the transcript",
     "A full AI game session is shown on the left. Read through every turn "
     "before you start rating."),
    ("2", "Rate each AI turn",
     "For each AI move, answer the questions shown beside it. Most games ask "
     "how well it used earlier information and whether the move was a sensible "
     "next step; some games ask questions written for that game."),
    ("3", "Give an overall verdict",
     "After the last turn, score the game as a whole, add any comments, then "
     "submit and move on to the next transcript."),
]

# (badge colour, digit colour) for scale points 1..4. The badge keeps its
# original hue for fill and border; the digit uses the lighter -400 step, because
# the same hue as text on its own 13%-alpha fill only reached 3.6:1.
_BADGE_COLOURS = [
    ("#ef4444", "#f87171"),
    ("#f59e0b", "#fbbf24"),
    ("#3b82f6", "#60a5fa"),
    ("#22c55e", "#4ade80"),
]


def _transcripts(n):
    """Batches hold 4 to 13 transcripts, so the count is always plural in the
    real study - but the singular is one word and costs nothing to get right."""
    return "transcript" if n == 1 else "transcripts"


def _badge(number, idx):
    """One coloured number badge. idx picks the palette step; anything past the
    4th point (the 1-7 overall scale) reuses the top colour."""
    fill, digit = _BADGE_COLOURS[min(idx, len(_BADGE_COLOURS) - 1)]
    return (f'<span class="rating-badge" style="background:{fill}22;'
            f'border-color:{fill};color:{digit};">{html.escape(number)}</span>')


def _opts_html(items):
    """items: (number, label, palette index) -> one wrapping row of badges."""
    opts = "".join(
        f'<span class="scale-opt">{_badge(n, i)}{html.escape(lbl)}</span>'
        for n, lbl, i in items
    )
    return f'<div class="scale-opts">{opts}</div>'


def _from_choices(choices):
    """Gradio radio choices -> (number, label, index) triples.

    The display half of every choice in annotation.py is "<number>\n<label>",
    so reading the real lists is what keeps this page in step with the
    questions the annotation screen actually shows.
    """
    out = []
    for i, (display, _value) in enumerate(choices):
        number, _, label = display.partition("\n")
        out.append((number, label, i))
    return out


def _question_html(question, items=None, note=None):
    """Render one generic question from its own definition in annotation.py:
    "**Q1 - Title**\n\nPrompt?" -> heading, prompt, and its scale points."""
    title, _, prompt = question[0].partition("\n\n")
    title = title.replace("**", "").replace(" · conditional", "").strip()
    return (
        '<div class="scale-q">'
        f'<div class="scale-q-title">{html.escape(title)}</div>'
        f'<div class="scale-q-note">{html.escape(prompt)}'
        + (f' {html.escape(note)}' if note else "")
        + '</div>'
        + _opts_html(items if items is not None else _from_choices(question[1]))
        + '</div>'
    )


def _turn_scales_html():
    """The three per-turn questions, read from annotation.GENERIC_Q1/Q2/Q3."""
    return (
        '<h3 class="scale-group-h">During the game — every AI turn</h3>'
        + _question_html(GENERIC_Q1)
        + _question_html(GENERIC_Q2)
        # Q3 is conditional and its 5th choice is a bare "N/A", which has no
        # number to put in a badge — so it is explained in words instead.
        + _question_html(GENERIC_Q3, items=_from_choices(GENERIC_Q3[1][:4]),
                         note="This one appears only when the AI explains its "
                              "thinking; otherwise choose N/A.")
        + '<p class="scale-foot">Some games ask their own questions instead of '
          'the first two. When they do, the question and its labels are shown '
          'next to the turn you are rating.</p>'
    )


def _verdict_scales_html():
    """The end-of-game scales, read from annotation_verdict so the landing page
    and the verdict screen can never describe different scales."""
    lo_num, lo_name, _ = OVERALL_RATINGS[0]
    hi_num, hi_name, _ = OVERALL_RATINGS[-1]
    return (
        '<h3 class="scale-group-h">At the end of each game</h3>'
        '<div class="scale-q">'
        '<div class="scale-q-title">Strategic coherence</div>'
        '<div class="scale-q-note">How well the AI stuck to a plan and '
        'adapted it as the game went on.</div>'
        + _opts_html([(v, name, i) for i, (v, name, _d) in enumerate(COHERENCE)])
        + '</div><div class="scale-q">'
          '<div class="scale-q-title">Overall game quality</div>'
          '<div class="scale-q-note">How well it actually played, on a slider '
          f'from 1 to {len(OVERALL_RATINGS)}.</div>'
        + '<div class="scale-opts">'
        + f'<span class="scale-opt">{_badge(lo_num, 0)}{html.escape(lo_name)}</span>'
        + '<span class="scale-gap">to</span>'
        + f'<span class="scale-opt">{_badge(hi_num, 3)}{html.escape(hi_name)}</span>'
        + '</div></div>'
        + '<p class="scale-foot">Some games add one further question about '
          'that game specifically.</p>'
    )


def _start(err, playlist, block, annotator):
    """Route the Start click: error banner > consent gate > playlist (resumes
    at the first game without a submitted verdict) > legacy single-game link."""
    # Never visible=False — Gradio 6 lazily mounts hidden columns, and
    # visible=False on a never-mounted column breaks a later visible=True.
    noop = gr.update()

    # Last element is always clearing_state=False — Start's first chained
    # event set it True to blank the page; every branch here resets it.
    def stay(note, show_popup=False):
        return (noop, noop, noop, gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                gr.skip(), gr.skip(), gr.skip(), gr.skip(), note,
                gr.update(visible=True) if show_popup else gr.skip(), False)

    if err:
        return stay("")

    # has_consented("") is always False, so this only gates real participants.
    if annotator and not db.has_consented(annotator):
        return stay("", show_popup=True)

    # Doubles as the session clock (practice counts toward it) and the first
    # game's clock; training re-stamps started_at once real annotation begins.
    now = datetime.now().isoformat()

    if playlist:  # the general study's link — the only path a real participant takes
        # Recompute resume position at click time: the completed set may have
        # grown since page load (or since a Back → Start round-trip).
        done = db.completed_pairs(annotator)
        idx = next((i for i, it in enumerate(playlist)
                    if (it["game"], it["condition"]) not in done), None)
        if idx is None:
            return stay(f"You have already completed all {len(playlist)} "
                        f"{_transcripts(len(playlist))} for this session. "
                        f"Nothing left to do — thank you.")
        item = playlist[idx]
        path = annotation.slug_to_path(item["game"])
        if not path:
            return stay(f"Cannot start — your assignment references an "
                        f"unknown game ({item['game']!r}). Please tell the "
                        f"study coordinator.")
        # Practice only for a first-timer. Gated on a persisted flag, not the
        # session index — a mid-session-1 page reload used to replay it.
        # has_completed_practice("") is False, so the legacy debug link
        # (annotator="") still gets it, as it did before.
        if not db.has_completed_practice(annotator):
            pages = (gr.update(visible=False), noop, gr.update(visible=True))
        else:          # resuming → straight to annotation
            pages = (gr.update(visible=False), gr.update(visible=True), noop)
        return (*pages, now, now, gr.skip(), item["condition"], path,
                gr.skip(), idx, gr.skip(), "", gr.skip(), False)

    if block:     # legacy single-game link — straight to annotation
        return (gr.update(visible=False), gr.update(visible=True), noop, now, now,
                gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                "", gr.skip(), False)

    return stay("Cannot start — no participant link detected. Please use "
                "the study link you were given, or ask the study coordinator "
                "for a corrected one.")


def _decline_consent():
    """Close the consent popup without recording consent or starting anything.

    Exists so the dialog's focus trap has an exit — see consent.py. Declining
    is not an error, so the welcome-page note is worded neutrally and explains
    how to change their mind.
    """
    return (
        gr.update(visible=False),
        "",
        "You have not given consent, so the study has not started. "
        "You can close this tab, or press **Start Annotation** again if you "
        "would like to re-read the information sheet.",
    )


def _confirm_consent(agreed, annotator_id):
    """Step 1 of the consent popup's click chain; _start runs next and
    decides whether to keep the popup open."""
    if not agreed:
        return True, gr.skip(), "Please tick the box above to confirm before continuing."
    db.record_consent(annotator_id)
    return True, gr.update(visible=False), ""


def build(welcome_page, annotation_page, training_page, error_state,
          playlist_state, started_at_state, session_started_at_state,
          annotator_state, block_state, game_state, playlist_idx_state,
          session_day_state, clearing_state, consent_popup):

    with welcome_page:

        with gr.Column(elem_classes=["welcome-col"]):

            with gr.Row(elem_classes=["annot-topnav"]):
                gr.HTML(
                    '<div class="welcome-nav">'
                    '<span class="game-name-tag">LM-PLAYSCHOOL</span>'
                    '<span class="game-id-tag">EMNLP 2026 · University of Edinburgh</span>'
                    '<span class="prolific-badge">via Prolific</span>'
                    '</div>'
                )

            # Malformed-link banner — only visible when app.load's session-param
            # parsing (app.py) found a missing/invalid annotator, block, or game.
            @gr.render(inputs=[error_state])
            def _error_banner(msg):
                if msg:
                    gr.Markdown(f"**{msg}**", elem_classes=["info-box"],
                                elem_id="welcome-error")

            gr.Markdown("# Human Annotation Study")
            gr.Markdown(
                "You will review transcripts of AI models playing dialogue games and "
                "rate the quality of their reasoning and strategy. A session takes "
                "about 15–20 minutes. The first time you take part, a short "
                "practice round comes first.",
                elem_classes=["welcome-sub"],
            )

            # Batches are not a fixed size (4 to 13 transcripts), so the count
            # has to be read from the assigned playlist rather than stated as a
            # constant. Same render-on-state pattern as the error banner above.
            @gr.render(inputs=[playlist_state])
            def _session_size(pl):
                if pl:
                    gr.Markdown(f"This session has **{len(pl)}** "
                                f"{_transcripts(len(pl))} to rate.",
                                elem_classes=["welcome-sub"])

            # The step cards are h3s; without this the outline jumps h1 -> h3.
            # sr-only rather than a visible heading so the layout is untouched.
            gr.HTML('<h2 class="a11y-sr-only">How the study works</h2>')

            with gr.Row(equal_height=True):
                for n, title, desc in _STEPS:
                    with gr.Group(elem_classes=["question-card", "step-card"]):
                        gr.Markdown(f"**{n}** \n\n ### {title}  \n\n {desc}")

            with gr.Group(elem_classes=["info-box"]):
                gr.Markdown(
                    "**You are evaluating AI reasoning quality — not the game itself**\n\n"
                    "Focus on whether the AI uses information logically and makes sensible "
                    "strategic choices. A game can be won by luck — or lost despite excellent "
                    "play. Judge the **thinking**, not the outcome."
                )

            # Was bold body text acting as a heading for the whole scale group.
            # The old copy claimed one 1-4 scale covered every scored question;
            # it covered none of them, so both groups below are generated from
            # the question definitions themselves instead of being retyped.
            gr.HTML('<h2 class="rating-scale-h">Rating scales'
                    '<span> - the words beside each number change from question '
                    'to question, so read them before you choose</span></h2>')

            with gr.Group(elem_classes=["question-card"]):
                gr.HTML(_turn_scales_html())

            with gr.Group(elem_classes=["question-card"]):
                gr.HTML(_verdict_scales_html())

            status_note = gr.Markdown("", elem_id="welcome-status")

            # First chained click blanks the annotation page, then _start (below) mounts it fresh.
            start_btn = gr.Button(
                "Start Annotation →", variant="primary", size="lg",
                elem_classes=["start-btn"],
            )

            gr.Markdown(
                "By continuing you confirm you are a registered Prolific participant "
                "and have read these instructions",
                elem_classes=["welcome-foot"],
            )

            # target=_blank so following it never costs a participant their
            # session: this page holds the playlist and consent state, and a
            # same-tab navigation away from it would drop both.
            gr.HTML(
                '<p class="welcome-foot a11y-foot">'
                '<a href="/gradio_api/file/accessibility.html" target="_blank" '
                'rel="noopener">Accessibility statement</a>'
                '</p>'
            )

        agree_cb, confirm_btn, popup_note, decline_btn = consent.build(consent_popup)

        _start_inputs = [error_state, playlist_state, block_state,
                         annotator_state]
        _start_outputs = [welcome_page, annotation_page, training_page,
                          started_at_state, session_started_at_state,
                          annotator_state, block_state,
                          game_state, playlist_state, playlist_idx_state,
                          session_day_state, status_note, consent_popup,
                          clearing_state]

        start_btn.click(
            lambda: True, outputs=[clearing_state],
        ).then(_start, inputs=_start_inputs, outputs=_start_outputs)

        confirm_btn.click(
            _confirm_consent,
            inputs=[agree_cb, annotator_state],
            outputs=[clearing_state, consent_popup, popup_note],
        ).then(_start, inputs=_start_inputs, outputs=_start_outputs)

        # Declining just closes the popup and leaves them on welcome — no
        # consent recorded, nothing started. Hides an already-mounted column,
        # exactly as _confirm_consent does, so it's safe for the blank-page bug.
        decline_btn.click(
            _decline_consent,
            inputs=None,
            outputs=[consent_popup, popup_note, status_note],
        )
