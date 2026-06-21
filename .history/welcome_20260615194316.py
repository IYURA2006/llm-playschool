import gradio as gr

# tuple with (icon, number, title, description)
_STEPS = [
    ("📖", "1", "Read the transcript",
     "A full AI game session is shown on the left. Take your time to read "
     "through each turn before rating."),
    ("⭐", "2", "Rate each AI turn",
     "For every AI move, score Prior Information Use and Strategic Logic on a "
     "1–4 scale. Flag any obvious errors you spot."),
    ("✅", "3", "Give an overall verdict",
     "After rating all turns, score the game as a whole on Strategic Coherence "
     "and Overall Quality, then submit."),
]

# (number, accent colour, label, description)
_RATINGS = [
    ("1", "#ef4444", "Poor",          "Completely fails — random, rule-breaking, or incoherent."),
    ("2", "#f59e0b", "Below average", "Struggling — makes obvious mistakes or wastes turns."),
    ("3", "#3b82f6", "Good",          "Competent — sensible, logical, on-task play."),
    ("4", "#22c55e", "Excellent",     "Strong — clever, efficient and clearly strategic."),
]


def build(welcome_page, annotation_page):

    #everything below goes into screen Welcome
    with welcome_page:

        with gr.Column(elem_classes=["welcome-col"]):

            # top NAV
            with gr.Row(elem_classes=["annot-topnav"]):
                gr.HTML(
                    '<div class="welcome-nav">'
                    '<span class="game-name-tag">LM-PLAYSCHOOL</span>'
                    '<span class="game-id-tag">EMNLP 2026 · University of Edinburgh</span>'
                    '<span class="prolific-badge">via Prolific</span>'
                    '</div>'
                )

            # HEADER
            gr.Markdown("# Human Annotation Study")
            gr.Markdown(
                "You will review transcripts of AI models playing dialogue games and "
                "rate the quality of their reasoning and strategy. Each session takes "
                "about 90 seconds.",
                elem_classes=["welcome-sub"],
            )

            # INFO CARDS
            with gr.Row(equal_height=True):
                for icon, n, title, desc in _STEPS:
                    with gr.Group(elem_classes=["question-card", "step-card"]):
                        gr.Markdown(f"{icon} **{n}** \n\n ### {title}  \n\n {desc}")

            # CALLOUT
            with gr.Group(elem_classes=["info-box"]):
                gr.Markdown(
                    "**ℹ️ You are evaluating AI reasoning quality — not the game itself**\n\n"


                    "Focus on whether the AI uses information logically and makes sensible "
                    "strategic choices. A game can be won by luck — or lost despite excellent "
                    "play. Judge the **thinking**, not the outcome."
                )

            # RATING SCALE
            gr.Markdown("**Rating scale** - applies to all scored questions")

            with gr.Group(elem_classes=["question-card"]):
                for n, color, label, desc in _RATINGS:

                    with gr.Row(elem_classes=["ovr-row"]):
                        with gr.Column(scale=5, min_width=46, elem_classes=["ovr-num"]):
                            gr.HTML(
                                f'<div class="rating-badge" '
                                f'style="background:{color}22;border-color:{color};'
                                f'color:{color};">{n}</div>'
                            )
                        with gr.Column(scale=0, min_width=130, elem_classes=["ovr-label"]):
                            gr.Markdown(f"**{label}**")
                        with gr.Column(scale=1, elem_classes=["ovr-desc"]):
                            gr.Markdown(desc)

     
            # ── START ───────────────────────────────────────────────
            gr.Button(
                "Start Annotation →", variant="primary", size="lg",
                elem_classes=["start-btn"],
            ).click(
                fn=lambda: (gr.update(visible=False), gr.update(visible=True)),
                outputs=[welcome_page, annotation_page],
            )

            gr.Markdown(
                "By continuing you confirm you are a registered Prolific participant "
                "and have read these instructions.",
                elem_classes=["welcome-foot"],
            )
