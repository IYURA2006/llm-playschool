"""Consent popup content and widgets — welcome.py owns all the show/hide and
consent-recording logic, so there's one place deciding that, not two.

The text below is the approved participant information sheet (ethics
reference 171955). Do not reword it without the study coordinator's say-so —
it's what the ethics application was certified against."""
import gradio as gr

_INFO_SHEET_MD = """
## Participant Information Sheet

**Project title:** LM Playschool — Improving Language Models through Learning from Dialogue Interaction

**Principal investigator:** Alessandro Suglia

**Researcher collecting data:** Yurii Ilnytskyi, Sabrina McCallum-Exner

This study was certified according to the Informatics Research Ethics Process,
reference number 171955. Please take time to read the following information
carefully. You should keep this page for your records.

### Who are the researchers?

This study is run by Alessandro Suglia together with Yurii Ilnytskyi (LLM
Playschool Intern) and Sabrina McCallum-Exner who are collecting and working
with the data. All three are part of the School of Informatics at the
University of Edinburgh.

### What is the purpose of the study?

We are studying how AI language models behave when they interact in simple,
multi-turn games (like word-guessing or negotiating). To figure out if our AI
training methods are working, we need humans to grade the AI's conversations.
In this study you will help us by reading short transcripts of AI gameplay
and answering questions about how well the models reasoned and made
decisions.

### Why have I been asked to take part?

We're looking for adult participants who are comfortable reading and
understanding English since the task involves reading conversations and
answering questions about them. No other special background or experience is
needed.

### Do I have to take part?

Participation in this study is entirely voluntary. You can withdraw from the
study at any time, without giving a reason, and without any penalty or
negative consequences. If you wish to withdraw, contact the PI. We will stop
using your data in any publications or presentations submitted after you have
withdrawn consent. However, we will keep copies of your original consent, and
of your withdrawal request.

### What will happen if I decide to take part?

You'll read short transcripts of an AI playing a game (for example, guessing
a word, or negotiating with another AI). For each one you'll answer a number
of rating questions — some are scales (like rating something from 1 to 4) and
some are yes/no checkboxes. You can also leave optional short comments if you
want to. This is done entirely online, through a web page, and there's no
audio or video recording involved. Each session takes about 15–20 minutes and
how many sessions you do depends on the study.

### Compensation

Payment for this study will be made through Prolific, the platform hosting
the study. You will be paid **£4.48** for each 20-minute session you
complete, in line with the current UK Real Living Wage (£13.45 per hour). If
you choose to take part in further sessions or studies as part of this
project, you will be paid accordingly for each one you complete.

### Are there any risks associated with taking part?

There are no significant risks associated with participation.

### Are there any benefits associated with taking part?

Besides the compensation outlined above, taking part in this study means
gaining insights into how state-of-the-art AI systems play games you may be
familiar with, and contributing to scientific knowledge used to make AI more
reliable.

### What will happen to the results of this study?

The results of this study may be summarised in published articles, reports
and presentations. Any quotes or key findings will be anonymized: We will
remove any information that could, in our assessment, allow anyone to
identify you. With your consent, information can also be used for future
research. Your data may be archived for a minimum of two years.

### Data protection and confidentiality

Your data will be processed in accordance with Data Protection Law. All
information collected about you will be kept strictly confidential. Your data
will be referred to by a unique participant number rather than by name. Your
data will only be viewed by the researcher/research team Yurii Ilnytskyi,
Alessandro Suglia, Sabrina McCallum-Exner.

All electronic data will be stored on a password-protected encrypted computer
on the School of Informatics' secure file servers, or on the University's
secure encrypted cloud storage services (DataShare, ownCloud, or Sharepoint),
and all paper records will be stored in a locked filing cabinet in the PI's
office. Your consent information will be kept separately from your responses
to minimise risk.

### What are my data protection rights?

The University of Edinburgh is a Data Controller for the information you
provide. You have the right to access information held about you. Your right
of access can be exercised in accordance with Data Protection Law. You also
have other rights including rights of correction, erasure and objection. For
more details, including the right to lodge a complaint with the Information
Commissioner's Office, please visit [www.ico.org.uk](https://www.ico.org.uk).
Questions, comments and requests about your personal data can also be sent to
the University Data Protection Officer at dpo@ed.ac.uk.

For general information about how we use your data, go to:
[Privacy notice | Edinburgh Research Office](https://www.ed.ac.uk/research-office/research-integrity-governance/research-data-protection/privacy-notice).

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


def build(consent_popup):
    """Static content and widgets only — no DB calls, no event wiring.
    Returns (agree_cb, confirm_btn, popup_note) for welcome.py to wire up."""
    with consent_popup:
        with gr.Column(elem_classes=["consent-modal-card"]):
            with gr.Group(elem_classes=["question-card", "consent-sheet"]):
                gr.Markdown(_INFO_SHEET_MD)
            agree_cb = gr.Checkbox(
                label="I have read and understood the information above and agree to take part",
                value=False,
            )
            popup_note = gr.Markdown("")
            confirm_btn = gr.Button(
                "I agree", variant="primary", size="lg",
                elem_classes=["start-btn"],
            )
    return agree_cb, confirm_btn, popup_note
