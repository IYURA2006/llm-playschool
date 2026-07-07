"""Once-per-sitting end-of-session survey, shown after the last game of a
playlist has been saved (see annotation_verdict.py's _verdict_finish). Not
tied to any single annotation row — persisted separately via
db.save_session_survey into the session_surveys table.
"""

import gradio as gr

import db

_CAPACITY = [
    ("Fewer would've been better", "fewer"),
    ("This was the right amount", "right"),
    ("I could have done more", "more"),
]
_DISRUPTION = [
    ("Yes, it felt disruptive", "disruptive"),
    ("No, it felt fine", "fine"),
]
_GUESS_PREF = [
    ("The general questions", "general"),
    ("The specific questions", "specific"),
    ("About the same", "same"),
]
_CHANGE = [
    ("Yes", "yes"),
    ("No", "no"),
    ("Not sure", "unsure"),
]


def _err_classes(value, err):
    return ["scale-radio", "radio-error"] if (err and not value) else ["scale-radio"]


def _sync_day_ui(day):
    """Day 1: blocked order across 4 different game types → ask about
    switching games. Day 2: 2-3 transcripts of the SAME game → ask about
    repetition instead, and add the day-2-only general-vs-specific question
    (plan.md's locked design: "Day 2 adds the general-vs-specific preference
    question")."""
    is_day2 = (day == "2")
    return (
        gr.update(visible=not is_day2),
        gr.update(visible=is_day2),
        gr.update(visible=is_day2),
        gr.update(visible=is_day2),
    )


def _submit(annotator_id, session_day, capacity, disruption, guess_pref, would_change, comment):
    is_day2 = (session_day == "2")
    err = not capacity or not disruption or not would_change or (is_day2 and not guess_pref)
    if err:
        return (
            "⚠️ Please answer the highlighted questions before finishing.",
            gr.update(elem_classes=_err_classes(capacity, True)),
            gr.update(elem_classes=_err_classes(disruption, True)),
            gr.update(elem_classes=_err_classes(guess_pref, True) if is_day2 else ["scale-radio"]),
            gr.update(elem_classes=_err_classes(would_change, True)),
            gr.update(),
            gr.update(),
        )
    db.save_session_survey(
        annotator_id, session_day, capacity, disruption,
        guess_pref if is_day2 else "", would_change, comment or "",
    )
    return (
        "",
        gr.update(elem_classes=["scale-radio"]),
        gr.update(elem_classes=["scale-radio"]),
        gr.update(elem_classes=["scale-radio"]),
        gr.update(elem_classes=["scale-radio"]),
        gr.update(visible=False),
        gr.update(visible=True),
    )


def build(session_survey_page, annotator_state, session_day_state):
    with session_survey_page:
        with gr.Column(elem_classes=["welcome-col"]):
            gr.Markdown("# Almost done — a few questions about today's session")
            gr.Markdown(
                "Asked once, at the end of today's sitting — not about any "
                "single game.",
                elem_classes=["welcome-sub"],
            )

            with gr.Group(elem_classes=["question-card"]) as form_group:
                gr.Markdown("### How many more games could you have comfortably done today?")
                capacity = gr.Radio(choices=_CAPACITY, show_label=False,
                                    elem_classes=["scale-radio"])

                disruption_label_day1 = gr.Markdown(
                    "### Did switching between different games feel disruptive, or fine?")
                disruption_label_day2 = gr.Markdown(
                    "### Did rating the same game repeatedly feel disruptive, or fine?",
                    visible=False)
                disruption = gr.Radio(choices=_DISRUPTION, show_label=False,
                                      elem_classes=["scale-radio"])

                guess_pref_label = gr.Markdown(
                    "### Which felt more like guessing: the general questions or the specific ones?",
                    visible=False)
                guess_pref = gr.Radio(choices=_GUESS_PREF, show_label=False,
                                      elem_classes=["scale-radio"], visible=False)

                gr.Markdown(
                    "### Now that you've done several of these, would you "
                    "change any of your earlier answers if you went back?")
                would_change = gr.Radio(choices=_CHANGE, show_label=False,
                                        elem_classes=["scale-radio"])

                comment = gr.Textbox(
                    placeholder="Anything else you'd like to add? (optional)",
                    lines=2, show_label=False, elem_classes=["verdict-comment"],
                )

                status = gr.Markdown("")
                finish_btn = gr.Button("Submit & Finish", variant="primary")

            thank_you = gr.Markdown(
                "## 🎉 Thank you — you're all done for today!\n\n"
                "You can safely close this tab.",
                visible=False,
            )

            session_day_state.change(
                fn=_sync_day_ui, inputs=[session_day_state],
                outputs=[disruption_label_day1, disruption_label_day2,
                         guess_pref_label, guess_pref],
            )

            finish_btn.click(
                fn=_submit,
                inputs=[annotator_state, session_day_state, capacity, disruption,
                        guess_pref, would_change, comment],
                outputs=[status, capacity, disruption, guess_pref, would_change,
                         form_group, thank_you],
            )
