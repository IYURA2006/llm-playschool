import gradio as gr
import pandas as pd

rating_data = pd.DataFrame({
    "Score": ["🔴 1 — Poor", "🟠 2 — Below Average", "🟢 3 — Good", "⭐ 4 — Excellent"],
    "Meaning": [
        "Clear failure — wrong reasoning, ignores the rules.",
        "Works but with significant flaws.",
        "Solid, with only minor issues.",
        "Exemplary — optimal performance.",
    ]
})


def build(welcome_page, annotation_page):
    with welcome_page:
        gr.Markdown("# AI Game Quality Evaluation")
        gr.Markdown("You will read conversations between an AI and a game system, then rate how well the AI played.")
        gr.Markdown("## How it works")
        with gr.Row():
            with gr.Group():
                gr.Markdown("**📖 Step 1**\n\n**Read the transcript**\n\nRead the conversation between the Game Master and the AI player from top to bottom.")
            with gr.Group():
                gr.Markdown("**⭐ Step 2**\n\n**Rate each AI turn**\n\nFor each AI response, rate reasoning clarity, rule compliance, and flag any issues.")
            with gr.Group():
                gr.Markdown("**✅ Step 3**\n\n**Give overall verdict**\n\nRate the conversation as a whole and identify the single worst turn.")

        with gr.Group(elem_classes=["info-box"]):
            gr.Markdown("**ℹ️ You are evaluating AI reasoning quality — not the game itself**\n\n▸ The AI plays a game by following specific rules.\n\n▸ You will see the game rules at the top of each transcript.\n\n▸ You are judging: Does the AI reason clearly? Does it follow the rules? Does it use prior information?")

        gr.Markdown("**Rating scale · used throughout**")
        gr.Dataframe(value=rating_data, interactive=False)

        gr.Markdown("✓ There are no trick questions — rate what you genuinely observe.\n\n✓ If you are unsure, use your best judgment.\n\n✓ Each annotation is anonymous.")

        gr.Button("Start annotation →", variant="primary", size="lg").click(
            fn=lambda: (gr.Column(visible=False), gr.Column(visible=True)),
            outputs=[welcome_page, annotation_page]
        )
