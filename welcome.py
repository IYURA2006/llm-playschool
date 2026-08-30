import html
from datetime import datetime

import gradio as gr

import consent
import db
import annotation
from annotation import GENERIC_Q1, GENERIC_Q2, GENERIC_Q3
from annotation_verdict import COHERENCE, OVERALL_RATINGS

# Kept to a similar length each, so the three cards balance on a row.
_STEPS = [
    ("Read the transcript",
     "A full AI game session appears on the left. Read every turn carefully "
     "before you rate anything. Take as much time as you need."),
    ("Rate each AI turn",
     "For each AI move, answer the questions next to it. They ask how well "
     "the AI used what it already knew, and whether its move made sense."),
    ("Give an overall verdict",
     "After the last turn, rate the whole game. You can add a comment if you "
     "want to. Then submit it and move on to the next transcript."),
]

def _transcripts(n):
    """Batches hold 4 to 13 transcripts, so the count is always plural in the
    real study - but the singular is one word and costs nothing to get right."""
    return "transcript" if n == 1 else "transcripts"


def _q_html(title, prompt, points, note=None):
    """One question: its name, the range it is scored on, and what it asks.

    The scale words themselves (None / Partial / Good / Excellent, and the rest)
    are deliberately not repeated here. They differ per question, several games
    replace them outright, and listing them turned this section into a wall of
    adjectives. They appear where they are actually used — beside the control
    on the rating screen.
    """
    return (
        '<div class="scale-q">'
        f'<div class="scale-q-title">{html.escape(title)}'
        f'<span class="scale-q-range">(scale 1\u2013{points})</span></div>'
        f'<div class="scale-q-note">{html.escape(prompt)}'
        + (f' {html.escape(note)}' if note else "")
        + '</div></div>'
    )


def _generic_html(question, points=None, note=None):
    """Render one generic question from its own definition in annotation.py:
    "**Q1 - Title**\n\nPrompt?" -> heading, range, prompt."""
    title, _, prompt = question[0].partition("\n\n")
    title = title.replace("**", "").replace(" \u00b7 conditional", "").strip()
    return _q_html(title, prompt,
                   points if points is not None else len(question[1]), note)


def _turn_scales_html():
    """The three per-turn questions, read from annotation.GENERIC_Q1/Q2/Q3."""
    return (
        '<h3 class="scale-group-h">During the game \u2014 every AI turn'
        '<span class="scale-star"> *</span></h3>'
        + _generic_html(GENERIC_Q1)
        + _generic_html(GENERIC_Q2)
        # Q3's 5th choice is "N/A", which is an escape hatch rather than a
        # point on the scale, so the range reads 1-4 and N/A is explained.
        + _generic_html(GENERIC_Q3, points=4,
                        note="This one appears only when the AI explains its "
                             "thinking; otherwise choose N/A.")
        + '<p class="scale-foot">* These are the general questions. Some games '
          'ask their own questions instead, written for that game, because the '
          'general ones do not suit every game. You will see those next to the '
          'turn you are rating.</p>'
    )


def _verdict_scales_html():
    """The end-of-game scales, read from annotation_verdict so the landing page
    and the verdict screen can never describe different ranges."""
    return (
        '<h3 class="scale-group-h">At the end of each game'
        '<span class="scale-star"> *</span></h3>'
        + _q_html("Strategic coherence",
                  "How well the AI stuck to a plan and adapted it as the "
                  "game went on.", len(COHERENCE))
        + _q_html("Overall game quality",
                  "How well it actually played, chosen on a slider.",
                  len(OVERALL_RATINGS))
        + '<p class="scale-foot">* Some games add one further question about '
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
            with gr.Column(elem_classes=["session-line"]):
                @gr.render(inputs=[playlist_state])
                def _session_size(pl):
                    if pl:
                        gr.Markdown(f"This session has **{len(pl)}** "
                                    f"{_transcripts(len(pl))} to rate.",
                                    elem_classes=["welcome-sub"])

            # The step cards are h3s; without this the outline jumps h1 -> h3.
            # sr-only rather than a visible heading so the layout is untouched.
            gr.HTML('<h2 class="a11y-sr-only">How the study works</h2>')

            with gr.Row(equal_height=True, elem_classes=["step-row"]):
                for title, desc in _STEPS:
                    with gr.Group(elem_classes=["question-card", "step-card"]):
                        gr.Markdown(f"### {title}  \n\n {desc}")

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
