import gradio as gr
import welcome
import annotation

css = """
.info-box { background: #eff6ff !important; border: 1px solid #bfdbfe !important; }
.turn-counter { display: flex !important; align-items: center; justify-content: flex-end; padding-right: 8px; }
.turn-counter p { margin: 0; }
* { outline: none !important; }
input:focus, textarea:focus, button:focus, [tabindex]:focus { box-shadow: none !important; border-color: inherit !important; }
"""

with gr.Blocks(css=css, theme=gr.themes.Soft()) as app:
    welcome_page = gr.Column(visible=True)
    annotation_page = gr.Column(visible=False)

    welcome.build(welcome_page, annotation_page)
    annotation.build(welcome_page, annotation_page)

app.launch()
