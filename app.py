import gradio as gr
import welcome
import annotation

css = """
/* ── Shared ───────────────────────────────────────────────────── */
.info-box { background: #eff6ff !important; border: 1px solid #bfdbfe !important; }
* { outline: none !important; }
*:focus, *:focus-visible, *:focus-within { outline: none !important; box-shadow: none !important; }
input:focus, textarea:focus, button:focus, [tabindex]:focus { box-shadow: none !important; border-color: inherit !important; }
/* Override Gradio Soft theme focus variables */
:root {
    --block-border-color-focus: transparent !important;
    --input-border-color-focus: transparent !important;
}
.block:focus, .block:focus-visible,
.wrap:focus, .wrap:focus-visible,
.col:focus, .col:focus-visible,
div:focus, div:focus-visible {
    outline: none !important;
    box-shadow: none !important;
    border-color: inherit !important;
}

/* ── Top nav bar ──────────────────────────────────────────────── */
.annot-topnav {
    background: #0f172a !important;
    border-radius: 10px !important;
    padding: 10px 20px !important;
    margin-bottom: 10px !important;
    align-items: center !important;
    gap: 12px !important;
    flex-wrap: nowrap !important;
}
.nav-left { display: flex; align-items: center; gap: 8px; }
.game-id-tag  { color: #64748b; font-size: 12px; font-family: monospace; }
.game-name-tag {
    background: #3b82f6; color: #fff;
    padding: 3px 10px; border-radius: 5px;
    font-size: 12px; font-weight: 700; letter-spacing: .02em;
}
.nav-center, .annot-progress {
    display: flex !important; align-items: center;
    gap: 8px; justify-content: center; flex: 1;
}
.annot-progress { color: #94a3b8; font-size: 13px; }
.prog-sep  { color: #334155; }
.prog-rated { color: #7dd3fc; }
.nav-timer { color: #cbd5e1; font-family: monospace; font-size: 15px; font-weight: 500; padding: 0 8px; }
.rules-nav-btn {
    background: transparent !important;
    border: 1px solid #334155 !important;
    color: #94a3b8 !important;
    font-size: 12px !important;
    min-width: 56px !important;
}

/* ── Left transcript column ───────────────────────────────────── */
.tx-col {
    background: #f1f5f9 !important;
    border-radius: 10px !important;
    overflow: hidden !important;
    padding: 0 !important;
}
.txscroll {
    padding: 16px 18px;
}
.goal-box {
    background: #e8f0fe;
    border: 1px solid #bfdbfe;
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 16px;
}
.goal-label {
    font-size: 10px; font-weight: 700;
    color: #3b82f6; letter-spacing: .08em;
    margin-bottom: 6px;
}
.goal-text { font-size: 13px; color: #374151; line-height: 1.5; margin: 0; }
.gm-msg {
    font-size: 12px; color: #6b7280;
    padding: 5px 10px; margin: 6px 0;
    border-left: 2px solid #cbd5e1;
    line-height: 1.5;
}
.gm-tag { font-weight: 700; color: #94a3b8; font-size: 10px; letter-spacing: .06em; margin-right: 4px; }
.turn-card {
    background: #1e3a5f;
    border: 2px solid transparent;
    border-radius: 10px;
    padding: 14px 16px;
    margin: 10px 0;
    color: #e2e8f0;
    transition: border-color .15s;
}
.turn-card.active-turn {
    background: #1e3f70 !important;
    border-color: #3b82f6 !important;
    box-shadow: 0 0 0 4px rgba(59,130,246,.25);
}
.card-header {
    font-size: 10px; font-weight: 700;
    color: #7dd3fc; letter-spacing: .1em;
    margin-bottom: 8px;
}
.card-body { font-size: 13px; line-height: 1.65; color: #e2e8f0; white-space: pre-wrap; }
.correct-msg {
    background: #ecfdf5; color: #065f46;
    padding: 8px 12px; border-radius: 6px;
    font-size: 13px; margin: 8px 0;
}
.game-end-msg { text-align: center; color: #9ca3af; font-size: 13px; padding: 10px 0; }

/* ── Right annotation column ──────────────────────────────────── */
#annot-col,
#annot-col > .wrap,
#annot-col > .wrap > div,
#annot-col .block,
#annot-col .form {
    background: #0f172a !important;
    border-color: #1e293b !important;
}
#annot-col label,
#annot-col .svelte-1gfkn6j,
#annot-col .svelte-1ipelgc {
    color: #cbd5e1 !important;
}
.turn-header-box {
    display: flex; align-items: center; gap: 10px;
    color: #f1f5f9; font-size: 15px; font-weight: 600;
    padding-bottom: 12px;
    border-bottom: 1px solid #1e293b;
    margin-bottom: 4px;
}
.turn-num-badge {
    background: #3b82f6; color: #fff;
    width: 28px; height: 28px; border-radius: 50%;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 13px; font-weight: 700; flex-shrink: 0;
}
.turn-title { font-size: 14px; }
.ql {
    display: flex; align-items: baseline; gap: 4px;
    margin-top: 14px; margin-bottom: 2px;
}
.qn  { color: #60a5fa; font-weight: 700; font-size: 12px; }
.qt  { color: #e2e8f0; font-weight: 600; font-size: 13px; }
.cond-tag {
    color: #94a3b8; font-size: 10px; font-style: italic;
    background: #1e293b; padding: 1px 6px; border-radius: 3px; margin-left: 6px;
}
.qd { color: #64748b; font-size: 12px; margin-bottom: 4px; }
.flags-lbl { color: #cbd5e1; font-size: 13px; font-weight: 600; margin-top: 12px; margin-bottom: 2px; }
.flags-sub { color: #64748b; font-weight: 400; font-size: 11px; }

/* ── Scale radio buttons ──────────────────────────────────────── */
.scale-radio .wrap { gap: 5px !important; flex-wrap: wrap !important; }
.scale-radio fieldset { border: none !important; padding: 0 !important; }
.scale-radio input[type=radio] { display: none !important; }
.scale-radio label {
    background: #1e293b !important;
    border: 1px solid #334155 !important;
    color: #94a3b8 !important;
    border-radius: 6px !important;
    padding: 6px 10px !important;
    cursor: pointer !important;
    font-size: 11px !important;
    text-align: center !important;
    min-width: 58px !important;
    white-space: pre-line !important;
    line-height: 1.4 !important;
    transition: all .15s !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
}
.scale-radio label:hover { border-color: #3b82f6 !important; color: #93c5fd !important; }
.scale-radio label:has(input[type=radio]:checked) {
    background: #1d4ed8 !important;
    border-color: #3b82f6 !important;
    color: #fff !important;
}
/* Firefox fallback: checked state via sibling */
.scale-radio input[type=radio]:checked ~ span { color: white !important; }

/* ── Flags checkboxes ─────────────────────────────────────────── */
.flags-check .wrap { gap: 4px !important; flex-direction: column !important; }
.flags-check label {
    color: #94a3b8 !important;
    font-size: 12px !important;
}

/* ── Comment textbox ──────────────────────────────────────────── */
.turn-comment textarea {
    background: #1e293b !important;
    border-color: #334155 !important;
    color: #cbd5e1 !important;
    font-size: 13px !important;
}
.turn-comment textarea::placeholder { color: #475569 !important; }

/* ── Annotation page container — no focus ring ───────────────── */
#annot-page, #annot-page:focus, #annot-page:focus-visible,
#annot-page > *, #annot-page > *:focus {
    outline: none !important;
    box-shadow: none !important;
    border-color: transparent !important;
}

/* ── Rules panel ──────────────────────────────────────────────── */
.rules-panel {
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 10px !important;
    padding: 20px !important;
    margin-top: 10px !important;
}
"""

with gr.Blocks() as app:
    welcome_page = gr.Column(visible=True)
    annotation_page = gr.Column(visible=False, elem_id="annot-page")

    welcome.build(welcome_page, annotation_page)
    annotation.build(welcome_page, annotation_page)

app.launch(css=css, theme=gr.themes.Soft())
