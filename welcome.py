from datetime import datetime

import gradio as gr

import consent
import db
import annotation

_STEPS = [
    ("📖", "1", "Read the transcript",
     "A full AI game session is shown on the left. Take your time to read "
     "through each turn before rating."),
    ("⭐", "2", "Rate each AI turn",
     "For every AI move, score how it uses prior information and whether it's a "
     "sensible next step on a 1–4 scale. Flag any obvious errors you spot."),
    ("✅", "3", "Give an overall verdict",
     "After rating all turns, score the game as a whole on Strategic Coherence "
     "and Overall Quality, then submit."),
]

# (number, badge colour, text colour, label, description). The badge keeps its
# original hue for fill and border; the digit uses the lighter -400 step, because
# the same hue as text on its own 13%-alpha fill only reached 3.6:1.
_RATINGS = [
    ("1", "#ef4444", "#f87171", "Poor",          "Completely fails — random, rule-breaking, or incoherent."),
    ("2", "#f59e0b", "#fbbf24", "Below average", "Struggling — makes obvious mistakes or wastes turns."),
    ("3", "#3b82f6", "#60a5fa", "Good",          "Competent — sensible, logical, on-task play."),
    ("4", "#22c55e", "#4ade80", "Excellent",     "Strong — clever, efficient and clearly strategic."),
]


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
            return stay(f"🎉 You've already completed all {len(playlist)} games "
                        f"for this session. Nothing left to do — thank you!")
        item = playlist[idx]
        path = annotation.slug_to_path(item["game"])
        if not path:
            return stay(f"⚠️ Your assignment references an unknown game "
                        f"({item['game']!r}) — tell the study coordinator.")
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

    return stay("⚠️ No participant link detected. Please use the study link "
                "you were given (or ask the study coordinator for a corrected one).")


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
        return True, gr.skip(), "⚠️ Please tick the box above to confirm before continuing."
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
                "rate the quality of their reasoning and strategy. Each session takes "
                "about 90 seconds.",
                elem_classes=["welcome-sub"],
            )

            # The step cards are h3s; without this the outline jumps h1 -> h3.
            # sr-only rather than a visible heading so the layout is untouched.
            gr.HTML('<h2 class="a11y-sr-only">How the study works</h2>')

            with gr.Row(equal_height=True):
                for icon, n, title, desc in _STEPS:
                    with gr.Group(elem_classes=["question-card", "step-card"]):
                        gr.Markdown(f"{icon} **{n}** \n\n ### {title}  \n\n {desc}")

            with gr.Group(elem_classes=["info-box"]):
                gr.Markdown(
                    "**ℹ️ You are evaluating AI reasoning quality — not the game itself**\n\n"


                    "Focus on whether the AI uses information logically and makes sensible "
                    "strategic choices. A game can be won by luck — or lost despite excellent "
                    "play. Judge the **thinking**, not the outcome."
                )

            # Was bold body text acting as a heading for the whole scale group.
            gr.HTML('<h2 class="rating-scale-h">Rating scale'
                    '<span> - applies to all scored questions</span></h2>')

            with gr.Group(elem_classes=["question-card"]):
                for n, color, text_color, label, desc in _RATINGS:

                    with gr.Row(elem_classes=["ovr-row"]):

                        with gr.Column(scale=0, min_width=46, elem_classes=["ovr-num"]):
                            gr.HTML(
                                f'<div class="rating-badge" '
                                f'style="background:{color}22;border-color:{color};'
                                f'color:{text_color};">{n}</div>'
                            )

                        with gr.Column(scale=0, min_width=130, elem_classes=["ovr-label"]):
                            gr.Markdown(f"**{label}**")


                        with gr.Column(scale=1, elem_classes=["ovr-desc"]):
                            gr.Markdown(desc)

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
