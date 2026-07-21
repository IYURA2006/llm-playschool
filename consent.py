"""Consent gate shown before anything else — the University of Edinburgh
Informatics Research Ethics participant information sheet + consent
statement. Blocks welcome_page until "I agree" is clicked.

Two still-unresolved placeholders are rendered as visible ⚠️ TODO markers
(ethics reference number, compensation amount) rather than guessed — the
study coordinator must fill these in (see the TODOs in _INFO_SHEET_MD)
before this goes in front of real participants."""
import gradio as gr

import db

_TODO = ('<span class="consent-todo">⚠️ TODO: {}</span>')

_INFO_SHEET_MD = f"""
## Participant Information Sheet

**Project title:** LM Playschool — Improving Language Models through Learning from Dialogue Interaction

**Principal investigator:** Alessandro Suglia

**Researchers collecting data:** Yurii Ilnytskyi, Sabrina McCallum-Exner

This study was certified according to the Informatics Research Ethics Process,
reference number {_TODO.format("ethics reference number")}. Please take time to
read the following information carefully. You should keep this page for your
records.

### Who are the researchers?

This study is run by Alessandro Suglia together with Yurii Ilnytskyi (LM
Playschool Intern) and Sabrina McCallum-Exner, who are collecting and working
with the data. All three are part of the School of Informatics at the
University of Edinburgh.

### What is the purpose of the study?

We are studying how AI language models behave when they interact in simple,
multi-turn games (like word-guessing or negotiating). To figure out if our AI
training methods are working, we need humans to grade the AI's conversations.
In this study you will help us by reading short transcripts of AI gameplay
and answering straightforward questions about how well the models reasoned
and made decisions.

### Why have I been asked to take part?

We're looking for adult participants who are comfortable reading and
understanding English, since the task involves reading conversations and
answering questions about them. No other special background or experience is
needed.

### Do I have to take part?

No — participation in this study is voluntary. You can withdraw from the
study at any time, without giving a reason. Your rights will not be affected,
and there will be no penalty or negative consequences. If you wish to
withdraw, contact the PI. We will stop using your data in any publications or
presentations submitted after you have withdrawn consent. However, we will
keep copies of your original consent, and of your withdrawal request.

### What will happen if I decide to take part?

You'll read short transcripts of an AI playing a game (for example, guessing
a word, or negotiating with another AI). For each one you'll answer a number
of rating questions — some are short scales (like rating something from 1 to
4) and some are yes/no checkboxes. You can also leave optional short comments
if you want to. This is done entirely online, through a web page — there's no
audio or video recording involved and no in-person meeting. Each session
takes about 10–15 minutes, and how many sessions you do depends on the study.

### Compensation

You will be paid {_TODO.format("compensation amount")} for your participation
in this study.

### Are there any risks associated with taking part?

There are no significant risks associated with participation.

### Are there any benefits associated with taking part?

Besides the compensation outlined above, taking part in this study means
gaining insights into how state-of-the-art AI systems play games you may be
familiar with, and contributing to scientific knowledge used to make AI more
reliable.

### What will happen to the results of this study?

The results of this study may be summarised in published articles, reports
and presentations. Any quotes or key findings will be anonymised: we will
remove any information that could, in our assessment, allow anyone to
identify you. With your consent, information can also be used for future
research. Your data may be archived for a minimum of two years.

### Data protection and confidentiality

Your data will be processed in accordance with Data Protection Law. All
information collected about you will be kept strictly confidential. Your data
will be referred to by a unique participant number rather than by name. Your
data will only be viewed by the research team: Alessandro Suglia, Yurii
Ilnytskyi, and Sabrina McCallum-Exner.

All electronic data will be stored on a password-protected encrypted
computer, on the School of Informatics' secure file servers, or on the
University's secure encrypted cloud storage services (DataShare, ownCloud, or
Sharepoint), and all paper records will be stored in a locked filing cabinet
in the PI's office. Your consent information will be kept separately from
your responses in order to minimise risk.

### What are my data protection rights?

The University of Edinburgh is a Data Controller for the information you
provide. You have the right to access information held about you. Your right
of access can be exercised in accordance with Data Protection Law. You also
have other rights including rights of correction, erasure and objection. For
more details, including the right to lodge a complaint with the Information
Commissioner's Office, please visit [www.ico.org.uk](https://www.ico.org.uk).
Questions, comments and requests about your personal data can also be sent to
the University Data Protection Officer at dpo@ed.ac.uk.

For general information about how we use your data, see the
[Edinburgh Research Office privacy notice](https://www.ed.ac.uk/research-office).

### Who can I contact?

If you have any further questions about the study, please contact the lead
researcher, Alessandro Suglia (asuglia@ed.ac.uk).

If you wish to make a complaint about the study, please contact
inf-ethics@inf.ed.ac.uk. When you contact us, please provide the study title
and detail the nature of your complaint.

### Consent

By proceeding with the study, I agree to all of the following statements:

- I have read and understood the above information.
- I understand that my participation is voluntary, and I can withdraw at any time.
- I consent to my anonymised data being used in academic publications and presentations.
- I allow my data to be used in future ethically approved research.
"""


def build(consent_page, welcome_page, annotator_state):
    with consent_page:
        with gr.Column(elem_classes=["welcome-col"]):
            with gr.Row(elem_classes=["annot-topnav"]):
                gr.HTML(
                    '<div class="welcome-nav">'
                    '<span class="game-name-tag">LM-PLAYSCHOOL</span>'
                    '<span class="game-id-tag">EMNLP 2026 · University of Edinburgh</span>'
                    '</div>'
                )
            with gr.Group(elem_classes=["question-card", "consent-sheet"]):
                gr.Markdown(_INFO_SHEET_MD)
            agree_btn = gr.Button(
                "I agree — take me to the study", variant="primary", size="lg",
                elem_classes=["start-btn"],
            )

    def _agree(annotator_id):
        db.record_consent(annotator_id)
        return gr.update(visible=False), gr.update(visible=True)

    agree_btn.click(_agree, inputs=[annotator_state], outputs=[consent_page, welcome_page])


def route_consent(annotator_id):
    """app.load routing (chained after _capture_session_params, so
    annotator_id is already resolved for the Prolific path by the time this
    runs): skip straight to welcome_page if this identity has already
    consented — e.g. a returning Prolific PID resuming their batch shouldn't
    have to re-consent on every reload. An empty annotator_id (bare-URL
    visitor who hasn't picked a name yet, or a legacy pilot link) always
    sees the gate, since db.has_consented("") is unconditionally False.

    The "not yet consented" branch returns no-ops, NOT gr.update(visible=False)
    on welcome_page: this fires on page load, before welcome_page (which
    starts visible=False) has ever been mounted, and Gradio 6 lazily mounts
    hidden columns — sending visible=False to a never-mounted column poisons
    it so a later visible=True (from _agree, below) never actually shows it.
    Both pages already start at the right visibility (consent_page=True,
    welcome_page=False), so "stay put" needs no explicit value at all."""
    if db.has_consented(annotator_id):
        return gr.update(visible=False), gr.update(visible=True)
    return gr.update(), gr.update()
