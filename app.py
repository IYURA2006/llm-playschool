import os

import gradio as gr
import db
import welcome
import training
import annotation
import annotation_verdict
import assignment

css = """
.info-box {
    background: #11233f !important;
    border: 1px solid #1e3a5f !important;
    border-left: 3px solid #3b82f6 !important;
    border-radius: 10px !important;
    padding: 6px 18px !important;
}
.info-box .block, .info-box .form, .info-box .wrap, .info-box > div {
    background: transparent !important; border: none !important; box-shadow: none !important;
}
.info-box strong { color: #dbeafe !important; }
.info-box p { color: #9fb0c9 !important; font-size: 13px !important; line-height: 1.6 !important; }
* { outline: none !important; }
*:focus, *:focus-visible, *:focus-within { outline: none !important; box-shadow: none !important; }
input:focus, textarea:focus, button:focus, [tabindex]:focus { box-shadow: none !important; border-color: inherit !important; }
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
.annot-progress { color: #f1f5f9; font-size: 13px; }
.prog-sep  { color: #334155; }
.prog-rated { color: #7dd3fc; }
/* Verdict page: whole progress line pure white in both themes */
#verdict-page .annot-progress,
#verdict-page .annot-progress span { color: #ffffff !important; }
.nav-timer { color: #cbd5e1; font-family: monospace; font-size: 15px; font-weight: 500; padding: 0 8px; }
.rules-nav-btn {
    background: transparent !important;
    border: 1px solid #334155 !important;
    color: #94a3b8 !important;
    font-size: 12px !important;
    min-width: 56px !important;
}
/* Red, not the default muted grey — Quit abandons unsaved ratings with no confirmation. */
.quit-btn {
    background: #7f1d1d !important;
    border: 1px solid #b91c1c !important;
    color: #fecaca !important;
}
.quit-btn:hover { background: #991b1b !important; border-color: #ef4444 !important; }

/* Fixed height, scrolls on its own, so it stays lined up with the questions column. */
.tx-col {
    background: #f1f5f9 !important;
    border-radius: 10px !important;
    overflow: hidden !important;
    padding: 0 !important;
    align-self: flex-start !important;
}
.txscroll {
    padding: 16px 18px;
    /* Fallback only, before syncTxHeight (JS) sets the real height. Not
       viewport-based — a content-sized iframe would make 100vh grow unbounded. */
    overflow-y: auto !important;
    height: 600px;
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
/* !important keeps these dark on the light .tx-col panel even under the app's own forced dark theme. */
.goal-text { font-size: 13px; color: #374151 !important; line-height: 1.5; margin: 0; white-space: pre-line; overflow-wrap: anywhere; }
.gm-msg {
    font-size: 12px; color: #6b7280 !important;
    padding: 5px 10px; margin: 6px 0;
    border-left: 2px solid #cbd5e1;
    line-height: 1.5;
    white-space: pre-line;
    overflow-wrap: anywhere;
}
.gm-tag { font-weight: 700; color: #94a3b8 !important; font-size: 10px; letter-spacing: .06em; margin-right: 4px; }
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
/* Player 2 — purple */
.turn-card.p2 { background: #1e1b4b; }
.turn-card.p2 .card-header { color: #a78bfa !important; }
.turn-card.p2.active-turn {
    background: #2e1b69 !important;
    border-color: #7c3aed !important;
    box-shadow: 0 0 0 4px rgba(124,58,237,.25) !important;
}
/* Player 3 — teal */
.turn-card.p3 { background: #0d2f2f; }
.turn-card.p3 .card-header { color: #2dd4bf !important; }
.turn-card.p3.active-turn {
    background: #0f3d3d !important;
    border-color: #14b8a6 !important;
    box-shadow: 0 0 0 4px rgba(20,184,166,.25) !important;
}
.card-header {
    font-size: 10px; font-weight: 700;
    color: #7dd3fc; letter-spacing: .1em;
    margin-bottom: 8px;
}
.card-body { font-size: 13px; line-height: 1.65; color: #e2e8f0; white-space: pre-wrap; overflow-wrap: anywhere; }
.correct-msg {
    background: #ecfdf5; color: #065f46 !important;
    padding: 8px 12px; border-radius: 6px;
    font-size: 13px; margin: 8px 0;
}
.game-end-msg { text-align: center; color: #9ca3af !important; font-size: 13px; padding: 10px 0; }
.game-win-msg {
    text-align: center;
    background: #052e16; border: 1px solid #166534; border-radius: 8px;
    color: #4ade80; font-size: 15px; font-weight: 700;
    padding: 12px 0; margin: 10px 0;
    letter-spacing: .02em;
}
.game-loss-msg {
    text-align: center;
    background: #2d0808; border: 1px solid #7f1d1d; border-radius: 8px;
    color: #f87171; font-size: 15px; font-weight: 700;
    padding: 12px 0; margin: 10px 0;
    letter-spacing: .02em;
}


#annot-col {
    background: #0a0e1a !important;
    border-radius: 10px !important;
    padding: 10px 12px !important;
    /* Must stay auto-height — syncTxHeight reads offsetHeight to size the transcript. */
    height: auto !important;
    align-self: flex-start !important;
    flex-wrap: nowrap !important;
}
/* Keep children at natural height inside the auto-height flex column */
#annot-col > * { flex-shrink: 0 !important; }
#annot-col > .wrap, #annot-col > .wrap > div { background: transparent !important; border: none !important; }
#annot-col label { color: #cbd5e1 !important; }

/* gr.Group nests elem_classes into a wrapper+inner pair (Gradio 6), giving
   each card two .turn-anno-card nodes — style only the outer one. */
.turn-anno-card:has(.turn-anno-card) {
    background: #0e1a30 !important;
    border: 1px solid #1e3a5f !important;
    border-radius: 12px !important;
    padding: 6px 12px !important;
    margin-bottom: 6px !important;
}

.turn-anno-card:not(:has(.turn-anno-card)) {
    background: transparent !important; border: none !important; padding: 0 !important; margin: 0 !important;
}
/* Force ALL inner Gradio containers to the same solid dark-blue so no grey leaks through */
.turn-anno-card .block,
.turn-anno-card .form,
.turn-anno-card .wrap,
.turn-anno-card > div,
.turn-anno-card fieldset,
.turn-anno-card .svelte-phx28p,
.turn-anno-card [data-testid] {
    background: #0e1a30 !important;
    border: none !important;
    box-shadow: none !important;
}
/* But the inner .turn-anno-card node itself stays transparent */
.turn-anno-card .turn-anno-card,
.turn-anno-card .turn-anno-card .block,
.turn-anno-card .turn-anno-card .form,
.turn-anno-card .turn-anno-card .wrap,
.turn-anno-card .turn-anno-card > div {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}
/* .is-rated is toggled by JS (isRated) — CSS :has() can't express "all
   rendered questions answered" since hybrid cards render different sets. */
.turn-anno-card.is-rated {
    border-color: #22c55e !important;
    box-shadow: 0 0 0 1px rgba(34,197,94,.35) !important;
}
.ta-head { display: flex; align-items: center; gap: 10px; margin-bottom: 2px; }
.ta-badge {
    width: 26px; height: 26px; border-radius: 50%;
    background: #3b82f6; color: #fff; flex-shrink: 0;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 13px; font-weight: 700;
}
.turn-anno-card.is-rated .ta-badge { background: #22c55e !important; }
.ta-title { font-size: 15px; font-weight: 600; color: #f1f5f9; }
.ta-sender {
    background: rgba(59,130,246,.18);
    color: #93c5fd;
    border: 1px solid rgba(59,130,246,.35);
    border-radius: 5px;
    font-size: 11px; font-weight: 700;
    padding: 2px 8px;
    letter-spacing: .03em;
}
.ta-role {
    background: rgba(100,116,139,.15);
    color: #94a3b8;
    border: 1px solid rgba(100,116,139,.25);
    border-radius: 5px;
    font-size: 10px; font-weight: 600;
    padding: 2px 7px;
    letter-spacing: .02em;
}
.rated-badge { margin-left: auto; color: #22c55e; font-size: 12px; font-weight: 600; display: none; }
.turn-anno-card.is-rated .rated-badge { display: inline-flex; }
/* Buttons share the card width and wrap to a second row instead of clipping. */
.turn-anno-card .scale-radio .wrap {
    width: 100% !important; gap: 5px !important;
    background: transparent !important; border: none !important;
    padding: 0 !important; margin: 3px 0 6px !important;
    flex-wrap: wrap !important;
}
.turn-anno-card .scale-radio label {
    flex: 1 1 0 !important;
    min-width: 0 !important;
    padding: 10px 4px !important;
    font-size: 11px !important;
}

/* Monospace + pre — the container's proportional font shredded board grids otherwise. */
.ascii-grid {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
    font-size: 16px !important;
    line-height: 1.35 !important;
    white-space: pre !important;
    overflow-x: auto;
    background: #0f172a !important;
    color: #94a3b8 !important;
    border: 1px solid #1e3a5f;
    border-radius: 8px;
    padding: 10px 14px !important;
    margin: 8px 0 !important;
    width: fit-content;
    max-width: 100%;
}
/* The game objects (C/L/P, X/R…) are what annotators track — make them pop */
.ascii-grid .grid-obj { color: #fbbf24; font-weight: 700; }

/* Wordle guess-feedback tiles — letter<color> markup rendered as the actual
   colored squares (see annotation.py's _WD_TILE_RE). */
.wd-tile {
    display: inline-block; min-width: 20px; padding: 1px 4px; margin: 0 1px;
    border-radius: 4px; text-align: center;
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-weight: 700; font-size: 13px; text-transform: uppercase;
}
.wd-green  { background: #22c55e; color: #052e16; }
.wd-yellow { background: #eab308; color: #3b2a03; }
.wd-red    { background: #64748b; color: #0b1220; }
/* wordle-crazy scrambles the key, so these tiles use the literal named colour. */
.wd-purple { background: #a855f7; color: #faf5ff; }
.wd-black  { background: #1f2937; color: #f3f4f6; }

/* !important keeps this readable on the light panel under the app's own forced dark theme. */
.ref-answer {
    background: #f0fdf4;
    border: 1px solid #86efac;
    border-left: 4px solid #22c55e;
    border-radius: 8px;
    padding: 10px 14px;
    margin: 12px 0;
}
.ref-answer-label {
    font-size: 10px; font-weight: 700;
    color: #16a34a !important; letter-spacing: .08em;
    margin-bottom: 4px;
}
.ref-answer-body {
    font-size: 14px; font-weight: 600;
    color: #14532d !important;
    white-space: pre-line; overflow-wrap: anywhere;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

/* Long-response clamp + repetition-loop badge; both live inside a dark
   turn card, so light text is fine here. */
.turn-loop-badge {
    display: inline-block;
    background: #7c2d12; color: #fed7aa;
    font-size: 10px; font-weight: 700; letter-spacing: .06em;
    padding: 2px 8px; border-radius: 999px; margin-bottom: 8px;
}
.turn-longclamp { margin-top: 6px; }
.turn-longclamp > summary {
    cursor: pointer; list-style: none;
    color: #7dd3fc; font-size: 12px; font-weight: 600;
    padding: 4px 0; user-select: none;
}
.turn-longclamp > summary::-webkit-details-marker { display: none; }
.turn-longclamp[open] > summary { color: #38bdf8; }

/* Legend row sits on the light transcript panel, above the turn cards */
.map-legend {
    display: flex; flex-wrap: wrap; align-items: center;
    gap: 6px 16px;
    background: #0f172a; border-radius: 8px;
    padding: 8px 14px; margin-bottom: 14px;
    font-size: 11.5px; color: #cbd5e1 !important;
}
.map-legend span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.map-legend svg { flex-shrink: 0; }
/* The claimed-map SVG inside a (dark) turn card */
.map-wrap { overflow-x: auto; margin-top: 8px; }
.map-svg { display: block; max-width: 100%; height: auto; }
.map-action { font-size: 13px; }

.turn-nav {
    position: sticky; top: 4px; z-index: 5;
    display: flex; align-items: center; gap: 8px;
    background: #0d1424; border: 1px solid #1e293b;
    border-radius: 10px; padding: 5px 10px; margin-bottom: 8px;
}
.tn-chips { display: flex; flex-wrap: wrap; gap: 6px; flex: 1; justify-content: center; }
/* !important guards against Gradio's own button theme rule outranking a
   bare .tn-chip.is-rated in some environments, repainting rated chips grey. */
.tn-chip {
    min-width: 30px; height: 30px; padding: 0 8px;
    border: 1px solid #2d3748 !important; border-radius: 8px;
    background: #161e2e !important; color: #94a3b8 !important;
    font-size: 13px; font-weight: 600; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
    transition: background .12s, border-color .12s, color .12s;
}
.tn-chip:hover { border-color: #3b82f6 !important; color: #cbd5e1 !important; }
/* Solid green fill, not just an outline, so "done" stays obvious after navigating away. */
.tn-chip.is-rated {
    background: #166534 !important; border-color: #22c55e !important; color: #dcfce7 !important;
}
.tn-chip.is-current { background: #1d4ed8 !important; border-color: #60a5fa !important; color: #fff !important; }
.tn-chip.is-current.is-rated { background: #16a34a !important; border-color: #4ade80 !important; color: #fff !important; }
.tn-arrow {
    width: 32px; height: 32px; flex-shrink: 0;
    border: 1px solid #2d3748 !important; border-radius: 8px;
    background: #161e2e !important; color: #cbd5e1 !important;
    font-size: 18px; line-height: 1; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
}
.tn-arrow:hover { border-color: #3b82f6 !important; }
/* Restore visible focus rings (the global reset removes them) — accessibility */
.tn-chip:focus-visible, .tn-arrow:focus-visible {
    outline: 2px solid #93c5fd !important; outline-offset: 2px;
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
#annot-col .prose strong, #annot-col p strong { color: #e2e8f0 !important; }
#annot-col .prose p, #annot-col p { color: #64748b !important; font-size: 12px !important; margin: 1px 0 3px !important; }
.cond-tag {
    color: #94a3b8; font-size: 10px; font-style: italic;
    background: #1e293b; padding: 1px 6px; border-radius: 3px; margin-left: 6px;
}
.qd { color: #64748b; font-size: 12px; margin-bottom: 4px; }
.flags-lbl { color: #cbd5e1; font-size: 13px; font-weight: 600; margin-top: 3px; margin-bottom: 2px; }
.flags-sub { color: #64748b; font-weight: 400; font-size: 11px; }

.scale-radio fieldset { border: none !important; padding: 0 !important; }
/* Visually hide the radio but keep it keyboard-focusable (a11y) */
.scale-radio input[type=radio] {
    position: absolute !important; opacity: 0 !important;
    width: 1px !important; height: 1px !important; margin: 0 !important;
    pointer-events: none !important;
}
.scale-radio label:has(input[type=radio]:focus-visible) {
    outline: 2px solid #93c5fd !important; outline-offset: 2px;
}
/* Wrap: fit-content box centred, just big enough for the buttons */
.scale-radio .wrap {
    display: flex !important;
    flex-wrap: wrap !important;
    gap: 8px !important;
    width: fit-content !important;
    margin: 4px auto !important;
    padding: 10px 14px !important;
    background: #0e1a30 !important;
    border: none !important;
    border-radius: 10px !important;
}
/* Higher specificity so this wins over .turn-anno-card .wrap above */
.turn-anno-card .scale-radio .wrap {
    background: #0e1a30 !important;
    border: none !important;
}
.scale-radio label {
    background: #0d1828 !important;
    border: 1px solid #1e3a5f !important;
    color: #94a3b8 !important;
    border-radius: 6px !important;
    padding: 6px 12px !important;
    cursor: pointer !important;
    font-size: 13px !important;
    text-align: center !important;
    min-width: 80px !important;
    white-space: pre-line !important;
    line-height: 1.25 !important;
    transition: all .15s !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
}
/* Colours only — geometry stays in the rule above so buttons can shrink/wrap. */
.turn-anno-card .scale-radio label {
    background: #0d1828 !important;
    border: 1px solid #1e3a5f !important;
    font-size: 12px !important;
}
.scale-radio label:hover { border-color: #3b82f6 !important; color: #93c5fd !important; }
/* Red error state on the wrap container */
.scale-radio.radio-error .wrap {
    border-color: #ef4444 !important;
    box-shadow: 0 0 0 3px rgba(239,68,68,.18) !important;
}
.scale-radio label:has(input[type=radio]:checked) {
    background: #1d4ed8 !important;
    border-color: #3b82f6 !important;
    color: #fff !important;
}
/* Firefox fallback: checked state via sibling */
.scale-radio input[type=radio]:checked ~ span { color: white !important; }

/* 2 columns instead of 1 — keeps the card short enough to fit one screen. */
.flags-check .wrap {
    display: grid !important;
    grid-template-columns: 1fr 1fr !important;
    gap: 5px 8px !important;
}
.flags-check label {
    display: flex !important; align-items: center !important;
    width: 100% !important;
    background: #161e2e !important;
    border: 1px solid #2d3748 !important;
    border-radius: 8px !important;
    padding: 6px 10px !important;
    color: #cbd5e1 !important;
    font-size: 12px !important;
    line-height: 1.3 !important;
}
.flags-check label:hover { border-color: #3b82f6 !important; }

/* Strip the Gradio outer wrapper so only the textarea colour shows */
.turn-comment,
.turn-comment .block,
.turn-comment .wrap,
.turn-comment > div {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
}
.turn-comment textarea {
    background: #0d1828 !important;
    border: 1px solid #1e3a5f !important;
    border-radius: 8px !important;
    color: #cbd5e1 !important;
    font-size: 13px !important;
}
.turn-anno-card .turn-comment textarea {
    background: #0d1828 !important;
    border: 1px solid #1e3a5f !important;
}
.turn-comment textarea::placeholder { color: #64748b !important; }

.verdict-comment { background: transparent !important; border: none !important; padding: 0 !important; box-shadow: none !important; }
.verdict-comment textarea {
    background: #1e2130 !important;
    border: 1px solid #383c4f !important;
    border-radius: 8px !important;
    color: #cbd5e1 !important;
}
.verdict-comment textarea::placeholder { color: #94a3b8 !important; }

#annot-page, #annot-page:focus, #annot-page:focus-visible,
#annot-page > *, #annot-page > *:focus,
#verdict-page, #verdict-page:focus, #verdict-page:focus-visible,
#verdict-page > *, #verdict-page > *:focus,
#train-page, #train-page:focus, #train-page:focus-visible,
#train-page > *, #train-page > *:focus {
    outline: none !important;
    box-shadow: none !important;
    border-color: transparent !important;
}

.option-desc p { font-size: 12px !important; color: #64748b !important; line-height: 1.6 !important; margin: 4px 0 !important; }
.option-desc strong { color: #374151 !important; }

.question-card {
    background: #1a2236 !important;
    border: 1px solid #2d3748 !important;
    border-radius: 12px !important;
    padding: 12px 16px !important;
    margin: 6px 0 !important;
}
.question-card .prose h3,
.question-card h3 { color: #f1f5f9 !important; font-size: 17px !important; margin-bottom: 4px !important; }
.question-card .prose p,
.question-card p { color: #94a3b8 !important; font-size: 13px !important; margin-bottom: 10px !important; }
/* Strip Gradio's grey backgrounds from all inner containers */
.question-card .block,
.question-card .form,
.question-card .wrap,
.question-card > div {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}
.coh-col {
    background: #1e293b !important;
    border: 1px solid #334155 !important;
    border-radius: 10px !important;
    padding: 14px 12px !important;
    text-align: center !important;
    transition: background .15s, border-color .15s !important;
}
.coh-col.coh-col-sel {
    background: #1d4ed8 !important;
    border-color: #3b82f6 !important;
    box-shadow: 0 0 0 3px rgba(59,130,246,.2) !important;
}
.coh-num-md h2 {
    font-size: 26px !important; font-weight: 700 !important;
    color: #94a3b8 !important; margin: 0 0 4px !important; text-align: center !important;
}
.coh-col.coh-col-sel .coh-num-md h2 { color: rgba(255,255,255,.9) !important; }
.coh-lbl-md p, .coh-lbl-md strong {
    font-size: 13px !important; font-weight: 600 !important;
    color: #e2e8f0 !important; margin: 0 0 6px !important;
}
.coh-col.coh-col-sel .coh-lbl-md p,
.coh-col.coh-col-sel .coh-lbl-md strong { color: #fff !important; }
.coh-desc-md p {
    font-size: 10px !important; color: #64748b !important;
    line-height: 1.5 !important; margin: 0 !important;
}
.coh-col.coh-col-sel .coh-desc-md p { color: rgba(255,255,255,.6) !important; }
.coh-sel-btn button {
    width: 100% !important; font-size: 10px !important;
    margin-top: 8px !important; padding: 4px 8px !important;
}
.coh-col.coh-col-err {
    border-color: #ef4444 !important;
    box-shadow: 0 0 0 3px rgba(239,68,68,.15) !important;
}
.coh-col .block, .coh-col .wrap, .coh-col > div {
    background: transparent !important; border: none !important;
}

.ovr-slider-ends {
    display: flex !important; justify-content: space-between !important;
    gap: 24px !important; margin-bottom: 6px !important;
}
.ovr-end {
    display: flex !important; flex-direction: column !important;
    flex: 1 1 0 !important; min-width: 0 !important;
}
.ovr-end-hi { text-align: right !important; align-items: flex-end !important; }
.ovr-end-num {
    font-size: 22px !important; font-weight: 800 !important;
    color: #e2e8f0 !important; line-height: 1.1 !important;
}
.ovr-end-lbl {
    font-size: 13px !important; font-weight: 700 !important;
    color: #93c5fd !important; margin-top: 2px !important;
}
.ovr-end-desc {
    font-size: 12px !important; color: #64748b !important;
    line-height: 1.45 !important; margin-top: 3px !important;
}
/* Applied to the outer block only — bordering inner wraps too drew a double ring. */
.ovr-slider.ovr-slider-err {
    border: 1px solid #ef4444 !important;
    border-radius: 10px !important;
    box-shadow: 0 0 0 3px rgba(239,68,68,.18) !important;
}
/* whole_game_only games: JS adds .hide-generic when a .wg-only marker renders. */
#verdict-page.hide-generic .g1-card,
#verdict-page.hide-generic .g2-card { display: none !important; }

.ovr-row {
    align-items: center !important;
    gap: 14px !important;
    padding: 8px 6px !important;
    border-bottom: 1px solid #1e293b !important;
    flex-wrap: nowrap !important;
}
.ovr-row:last-child { border-bottom: none !important; }
.ovr-num {
    flex: 0 0 46px !important; min-width: 46px !important;
    display: flex !important; align-items: center !important;
    justify-content: center !important; padding: 0 !important;
}
.ovr-num .block, .ovr-num .wrap {
    padding: 0 !important; margin: 0 !important;
    display: flex !important; align-items: center !important; justify-content: center !important;
}
.ovr-label { flex: 0 0 130px !important; min-width: 0 !important; }
.ovr-label p, .ovr-label strong {
    font-size: 14px !important; font-weight: 700 !important;
    color: #e2e8f0 !important; margin: 0 !important;
}
.ovr-desc { flex: 1 1 0 !important; min-width: 0 !important; }
.ovr-desc p {
    font-size: 13px !important; color: #64748b !important;
    line-height: 1.5 !important; margin: 0 !important;
}
/* Strip block backgrounds inside rows */
.ovr-row .block, .ovr-row > div > .block {
    background: transparent !important; border: none !important; padding: 0 !important;
}

.question-card .scale-radio .wrap {
    width: 100% !important;
    justify-content: space-evenly !important;
    box-sizing: border-box !important;
    border: 1px solid rgba(100,116,139,.12) !important;
}

.welcome-col { max-width: 880px; margin: 0 auto !important; }
.welcome-sub p { color: #94a3b8 !important; font-size: 14px !important; line-height: 1.6 !important; }
.welcome-foot p { color: #475569 !important; font-size: 12px !important; text-align: center !important; margin-top: 8px !important; }
/* Top nav: name badge left, Prolific badge pushed right */
.welcome-nav { display: flex; align-items: center; gap: 10px; width: 100%; }
.welcome-nav .prolific-badge { margin-left: auto; }
.prolific-badge {
    background: #3a2e08; color: #fcd34d; border: 1px solid #a16207;
    padding: 4px 10px; border-radius: 6px;
    font-size: 11px; font-weight: 700; letter-spacing: .03em;
}
/* Step cards: equal height, compact heading */
.step-card { height: 100%; }
.step-card h3 { font-size: 15px !important; margin: 2px 0 4px !important; }
.step-card p { font-size: 12.5px !important; line-height: 1.55 !important; }
/* Coloured number badge for the rating rows */
.rating-badge {
    width: 30px; height: 30px; border-radius: 7px; border: 1px solid;
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; font-weight: 700; box-sizing: border-box;
}
.start-btn { width: 100% !important; margin-top: 6px !important; }

#consent-modal {
    position: fixed !important;
    inset: 0 !important;
    z-index: 1000 !important;
    align-items: center !important;
    justify-content: center !important;
    background: rgba(2, 6, 23, 0.72) !important;
    padding: 24px !important;
    overflow-y: auto !important;
}
.consent-modal-card {
    width: 100%; max-width: 720px; margin: auto !important;
    background: #1a2236 !important;
    border: 1px solid #2d3748 !important;
    border-radius: 14px !important;
    padding: 22px 26px !important;
    box-shadow: 0 20px 60px rgba(0,0,0,.5) !important;
    max-height: 85vh; overflow-y: auto;
}
.consent-sheet { max-height: min(50vh, 520px); overflow-y: auto; }
.consent-sheet h2, .consent-sheet h3 { color: #e2e8f0 !important; }
.consent-sheet p, .consent-sheet li { color: #9fb0c9 !important; font-size: 13.5px !important; line-height: 1.6 !important; }
.consent-sheet strong { color: #dbeafe !important; }
.consent-sheet a { color: #7dd3fc !important; }
.consent-todo {
    background: #7c2d12; color: #fed7aa; border: 1px solid #c2410c;
    padding: 1px 8px; border-radius: 5px; font-weight: 700; font-size: 12.5px;
    white-space: nowrap;
}

.rules-panel {
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 10px !important;
    padding: 20px !important;
    margin-top: 10px !important;
}

.game-select-row { margin-bottom: 10px !important; }
.game-select { background: transparent !important; }
.game-select label span { color: #94a3b8 !important; font-size: 12px !important; }
.game-select input, .game-select .wrap-inner, .game-select .secondary-wrap {
    background: #0f172a !important; color: #e2e8f0 !important;
    border-color: #334155 !important;
}
.game-select .container { background: transparent !important; }

.game-seq-tag {
    color: #e2e8f0; font-size: 12px; font-weight: 600;
    background: #1e293b; border: 1px solid #334155;
    padding: 3px 10px; border-radius: 5px; margin-left: 8px;
}

#train-col { background: #0a0e1a !important; border-radius: 10px !important; padding: 10px 12px !important;
    /* Same auto-height rule as #annot-col. */
    height: auto !important; align-self: flex-start !important; }
#train-col label { color: #cbd5e1 !important; }
#train-col .prose strong, #train-col p strong { color: #e2e8f0 !important; }
#train-col .prose p, #train-col p { color: #64748b !important; font-size: 12px !important; margin: 2px 0 6px !important; }
/* Same pre-JS fallback rule as .txscroll, styled separately for the training page. */
.train-txscroll { padding: 16px 18px; overflow-y: auto !important; height: 600px; }
/* Practice card — same wrapper+inner doubling handling as .turn-anno-card */
.train-card:has(.train-card) {
    background: #0e1a30 !important;
    border: 1px solid #1e3a5f !important;
    border-radius: 12px !important;
    padding: 14px 16px !important;
    margin-bottom: 14px !important;
}
.train-card:not(:has(.train-card)) {
    background: transparent !important; border: none !important; padding: 0 !important; margin: 0 !important;
}
.train-card .block, .train-card .form, .train-card .wrap,
.train-card > div, .train-card fieldset {
    background: #0e1a30 !important; border: none !important; box-shadow: none !important;
}
.train-card .train-card, .train-card .train-card .block, .train-card .train-card .form,
.train-card .train-card .wrap, .train-card .train-card > div {
    background: transparent !important; border: none !important; box-shadow: none !important;
}
.train-card .scale-radio .wrap {
    width: 100% !important; gap: 6px !important;
    background: transparent !important; border: none !important;
    padding: 0 !important; margin: 6px 0 10px !important;
    flex-wrap: wrap !important;
}
.train-card .scale-radio label {
    flex: 1 1 0 !important; min-width: 0 !important;
    padding: 10px 4px !important; font-size: 11px !important;
    background: #0d1828 !important; border: 1px solid #1e3a5f !important;
}
/* Reference-rating feedback blocks (revealed by "Check my ratings") */
.train-fb {
    background: #0d1424; border: 1px solid #1e293b;
    border-radius: 8px; padding: 10px 12px; margin-top: 4px;
}
.fb-row { font-size: 13px; font-weight: 600; padding: 6px 8px; border-radius: 6px; margin: 4px 0; }
.fb-verdict { font-size: 11px; font-weight: 700; margin-right: 8px; letter-spacing: .03em; }
.fb-good  { background: #052e16; color: #4ade80; border: 1px solid #166534; }
.fb-close { background: #3a2e08; color: #fcd34d; border: 1px solid #a16207; }
.fb-miss  { background: #2d0808; color: #f87171; border: 1px solid #7f1d1d; }
.fb-why  { font-size: 12px; color: #94a3b8; line-height: 1.55; margin: 2px 4px 8px; }
.fb-note { font-size: 12px; color: #7dd3fc; margin-top: 6px; line-height: 1.5; }
"""

# Forces dark mode before render, then wires up the live "X of N rated" counter.
force_dark = """
<script>
(function () {
    const url = new URL(window.location);
    if (url.searchParams.get('__theme') !== 'dark') {
        url.searchParams.set('__theme', 'dark');
        window.location.replace(url.href);
    }
})();
</script>
<script>
(function () {
    // Gradio never unmounts a hidden page, so a finished practice round's
    // cards stay in the DOM — scope every query to a visible page only.
    function onHiddenPage(el) {
        var page = el.closest('#train-page, #annot-page, #verdict-page');
        return !!(page && getComputedStyle(page).display === 'none');
    }
    // Gradio doubles the class onto a wrapper+inner pair; only count the outer node.
    function panes() {
        return Array.prototype.filter.call(
            document.querySelectorAll('.turn-anno-card'),
            function (c) {
                return !(c.parentElement && c.parentElement.closest('.turn-anno-card')) &&
                    !onHiddenPage(c);
            }
        );
    }
    function chips() {
        return Array.prototype.filter.call(
            document.querySelectorAll('.tn-chip'),
            function (c) { return !onHiddenPage(c); }
        );
    }
    // Mirrors annotation._submit's rule: only flags and comments are optional.
    function isRated(card) {
        var groups = card.querySelectorAll('.scale-radio');
        if (!groups.length) return false;
        return Array.prototype.every.call(groups, function (grp) {
            return !!grp.querySelector('input:checked');
        });
    }

    var current = 0;

    // Real annotation cards use id="tc-N", practice cards "ttc-N" — try both,
    // but only accept a match on the currently-visible page (the other
    // page's cards stay in the DOM, just hidden).
    function transcriptCardEl(idx) {
        var ids = ['tc-' + idx, 'ttc-' + idx];
        for (var i = 0; i < ids.length; i++) {
            var el = document.getElementById(ids[i]);
            if (el && !onHiddenPage(el)) return el;
        }
        return null;
    }

    // Matches the transcript height to the questions column, floored at TX_MIN_H.
    var TX_MIN_H = 500;
    function syncCol(colSel, txSel) {
        var col = document.querySelector(colSel);
        var tx = document.querySelector(txSel);
        if (!col || !tx || onHiddenPage(col)) return;
        tx.style.height = Math.max(col.offsetHeight, TX_MIN_H) + 'px';
    }
    function syncHeights() {
        syncCol('#annot-col', '.txscroll');
        syncCol('#train-col', '.train-txscroll');
    }
    // Re-sync whenever the questions column reflows, not just on turn switch.
    var _txRO = window.ResizeObserver ? new ResizeObserver(function () { syncHeights(); }) : null;
    function observeCols() {
        if (!_txRO) return;
        ['#annot-col', '#train-col'].forEach(function (s) {
            var el = document.querySelector(s);
            if (el) { try { _txRO.observe(el); } catch (e) {} }
        });
    }

    // Show only the current card; sync chips, transcript highlight, and counter.
    function refresh() {
        var cards = panes();
        if (!cards.length) return;
        current = Math.max(0, Math.min(cards.length - 1, current));
        var cs = chips(), rated = 0;
        cards.forEach(function (card, i) {
            card.style.display = (i === current ? '' : 'none');
            var r = isRated(card);
            card.classList.toggle('is-rated', r);
            if (r) rated++;
            var chip = cs[i];
            if (chip) {
                chip.classList.toggle('is-current', i === current);
                chip.classList.toggle('is-rated', r);
                chip.setAttribute('aria-selected', i === current ? 'true' : 'false');
                chip.setAttribute('tabindex', i === current ? '0' : '-1');
            }
        });
        document.querySelectorAll('.turn-card').forEach(function (t) {
            t.classList.remove('active-turn');
        });
        var active = transcriptCardEl(current);
        if (active) active.classList.add('active-turn');
        var el = document.querySelector('#annot-page .prog-rated');
        if (el) el.textContent = rated + ' of ' + cards.length + ' turns rated';
        syncHeights();
    }

    function goTo(i, focusChip) {
        current = i;
        refresh();
        var active = transcriptCardEl(current);
        if (active) active.scrollIntoView({ block: 'nearest' });
        if (focusChip) { var c = chips()[current]; if (c) c.focus(); }
    }

    function wireAria() {
        var cards = panes(), cs = chips();
        cards.forEach(function (card, i) {
            if (!card.id) card.id = 'tpane-' + i;
            card.setAttribute('role', 'tabpanel');
            card.setAttribute('aria-labelledby', 'tn-chip-' + i);
            card.setAttribute('tabindex', '0');
            if (cs[i]) cs[i].setAttribute('aria-controls', card.id);
        });
    }

    function onClick(e) {
        var chip = e.target.closest && e.target.closest('.tn-chip');
        if (chip) { goTo(parseInt(chip.getAttribute('data-turn'), 10), true); return; }
        var arrow = e.target.closest && e.target.closest('.tn-arrow');
        if (arrow) goTo(current + (arrow.getAttribute('data-nav') === 'next' ? 1 : -1), true);
    }
    function onKey(e) {
        if (!(e.target.closest && e.target.closest('.turn-nav'))) return;
        var n = panes().length;
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { goTo(Math.min(n - 1, current + 1), true); e.preventDefault(); }
        else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { goTo(Math.max(0, current - 1), true); e.preventDefault(); }
        else if (e.key === 'Home') { goTo(0, true); e.preventDefault(); }
        else if (e.key === 'End') { goTo(n - 1, true); e.preventDefault(); }
    }

    // Comparing chip and card counts is a reliable "fully painted" signal.
    function ready() {
        var p = panes(), c = chips();
        return p.length > 0 && p.length === c.length;
    }

    function init() {
        if (!ready()) return false;
        wireAria();
        observeCols();
        document.addEventListener('click', onClick);
        document.addEventListener('keydown', onKey);
        document.addEventListener('change', refresh);
        window.addEventListener('resize', syncHeights);
        refresh();
        return true;
    }

    // Gradio renders asynchronously; retry until the cards exist.
    if (!init()) {
        var tries = 0;
        var iv = setInterval(function () {
            if (init() || ++tries > 100) clearInterval(iv);
        }, 100);
    }

    // Re-initialise when @gr.render swaps the per-game cards in/out.
    (function () {
        if (!window.MutationObserver) return;
        // Observe <html>, not #annot-page — the page doesn't exist yet when this script runs.
        var debounceTimer, retryIv;
        // Ignore refresh()'s own text-node writes, or this would re-trigger itself forever.
        function isStructural(muts) {
            function has(nodes) {
                return Array.prototype.some.call(nodes, function (n) {
                    return n.nodeType === 1 && n.matches &&
                        (n.matches('.turn-anno-card, .tn-chip') ||
                         (n.querySelector && n.querySelector('.turn-anno-card, .tn-chip')));
                });
            }
            return muts.some(function (m) { return has(m.addedNodes) || has(m.removedNodes); });
        }
        new MutationObserver(function (muts) {
            if (!isStructural(muts)) return;
            // Debounce rapid mutations (Gradio fires many during render)
            clearTimeout(debounceTimer);
            clearInterval(retryIv);
            debounceTimer = setTimeout(function () {
                // Poll until every card is mounted — @gr.render streams cards in one at a time.
                var attempts = 0;
                retryIv = setInterval(function () {
                    if (ready()) {
                        clearInterval(retryIv);
                        init();   // re-attaches listeners; addEventListener dedupes, so safe to call again
                        current = 0;
                        refresh();
                    } else if (++attempts > 80) {
                        clearInterval(retryIv);
                    }
                }, 50);
            }, 80);
        }).observe(document.documentElement, { childList: true, subtree: true });
    })();
})();
</script>
<script>
(function () {
    // Toggles .hide-generic on #verdict-page when a .wg-only marker renders (see CSS).
    function toggleGeneric() {
        var vp = document.getElementById('verdict-page');
        if (!vp) return;
        vp.classList.toggle('hide-generic', !!vp.querySelector('.wg-only'));
    }
    if (window.MutationObserver) {
        new MutationObserver(toggleGeneric).observe(document.documentElement,
            { childList: true, subtree: true });
    }
    toggleGeneric();
})();
</script>
"""

def _session_error(msg):
    """Uniform error return shape: no annotator, default game, empty playlist."""
    return "", "", annotation.DEFAULT_GAME, msg, [], 0, ""


def _capture_session_params(request: gr.Request):
    # Accepts either a Prolific redirect link or a legacy single-game debug link.
    qp = dict(request.query_params or {})

    # Case-insensitive — Prolific's own docs are inconsistent about casing.
    prolific_pid = next(
        (v.strip() for k, v in qp.items() if k.upper() == "PROLIFIC_PID" and v.strip()),
        "",
    )
    if prolific_pid:
        if len(prolific_pid) > 100:  # sanity bound, not a strict format check
            return _session_error("⚠️ Malformed participant link.")
        # The raw Prolific PID is never stored — everything downstream (and
        # the DB) only ever sees this pseudonym. See db.pseudonymize_pid.
        prolific_pid = db.pseudonymize_pid(prolific_pid)
        playlist, err_msg = assignment.build_playlist_for(prolific_pid)
        if err_msg:
            return _session_error(err_msg)
        done = db.completed_pairs(prolific_pid)
        idx = next((i for i, it in enumerate(playlist)
                    if (it["game"], it["condition"]) not in done), None)
        if idx is None:
            return _session_error(
                f"🎉 You've already completed all {len(playlist)} tasks for "
                f"this study. Thank you!"
            )
        item = playlist[idx]
        # Session index reaches welcome._start, which only shows the practice
        # round on session "1" — a returning participant has already done it.
        return (prolific_pid, item["condition"], annotation.slug_to_path(item["game"]),
                "", playlist, idx, str(assignment.current_session_index(prolific_pid)))

    annotator = (qp.get("annotator") or "").strip()
    block = (qp.get("block") or "").strip()
    game = (qp.get("game") or "").strip()

    if block or game or annotator:
        missing = [name for name, val in (("annotator", annotator), ("block", block), ("game", game)) if not val]
        if missing:
            return _session_error(
                f"⚠️ This link is missing required parameter(s): {', '.join(missing)}. "
                f"Ask the study coordinator for a corrected link."
            )
        if block not in annotation.VALID_BLOCKS:
            return _session_error(
                f"⚠️ This link has an invalid 'block' value ({block!r}). "
                f"Expected one of: {', '.join(sorted(annotation.VALID_BLOCKS))}."
            )
        game_path = annotation.slug_to_path(game)
        if not game_path:
            return _session_error(
                f"⚠️ This link references an unknown game ({game!r}). "
                f"Ask the study coordinator for a corrected link."
            )
        return annotator, block, game_path, "", [], 0, ""

    return _session_error(
        "This study is only accessible via its Prolific link. If you believe "
        "you're seeing this in error, please contact the study coordinator."
    )


with gr.Blocks() as app:
    # Shared selected-game path; annotation and verdict screens render off it.
    game_state = gr.State(annotation.DEFAULT_GAME)
    annotator_state = gr.State("")  # captured from the URL's annotator param
    # "universal"/"hybrid" for the current game, or a legacy day1_*/day2_* value
    block_state = gr.State("")
    # Non-empty when the session URL is malformed; gates Start Annotation
    error_state = gr.State("")
    # Ordered [{"game": slug, "condition": ...}, …] from
    # assignment.build_playlist_for; empty list = legacy single-game debug link
    playlist_state = gr.State([])
    playlist_idx_state = gr.State(0)
    # 1-based session index as a string ("1".."MAX_SESSIONS"), "" on the
    # legacy debug link; gates the practice round and is persisted per row
    session_day_state = gr.State("")
    # Stamped when a game's annotation actually starts; duration is
    # verdict_at − started_at
    started_at_state = gr.State("")
    # Stamped once per sitting at the Start click; anchors the whole-session
    # timer (duration is max(verdict_at) − session_started_at)
    session_started_at_state = gr.State("")
    # True only between the two chained events of a game switch, while the
    # annotation page is blanked so no stale widget value survives.
    clearing_state = gr.State(False)

    welcome_page = gr.Column(visible=True)
    training_page = gr.Column(visible=False, elem_id="train-page")
    annotation_page = gr.Column(visible=False, elem_id="annot-page")
    verdict_page = gr.Column(visible=False, elem_id="verdict-page")
    # Modal overlay, not a page — toggled by welcome._start / _confirm_consent.
    consent_popup = gr.Column(visible=False, elem_id="consent-modal")

    welcome.build(welcome_page, annotation_page, training_page, error_state,
                  playlist_state, started_at_state, session_started_at_state,
                  annotator_state, block_state,
                  game_state, playlist_idx_state, session_day_state,
                  clearing_state, consent_popup)
    training.build(welcome_page, training_page, annotation_page, started_at_state)
    annotation.build(welcome_page, annotation_page, verdict_page, game_state,
                     annotator_state, block_state, playlist_state,
                     playlist_idx_state, started_at_state, session_day_state,
                     session_started_at_state, clearing_state)
    annotation_verdict.build(welcome_page, annotation_page, verdict_page,
                             game_state, annotator_state, block_state,
                             playlist_state, playlist_idx_state, started_at_state,
                             session_day_state, clearing_state)

    app.load(_capture_session_params, inputs=None,
              outputs=[annotator_state, block_state, game_state, error_state,
                       playlist_state, playlist_idx_state, session_day_state])

# System fonts avoid a remote Google Fonts fetch on first paint. Must be
# gr.themes.Font objects — a plain string crashes Gradio's font comparison.
theme = gr.themes.Soft(
    font=[gr.themes.Font(f) for f in ("system-ui", "-apple-system", "Segoe UI", "sans-serif")],
    font_mono=[gr.themes.Font(f) for f in ("ui-monospace", "SFMono-Regular", "monospace")],
)
# share=True tunnels through Gradio's own servers, which we don't need —
# the VM's own domain/TLS setup is the real public entry point.
# PORT lets deployments override the default without code changes.
app.launch(css=css, theme=theme, head=force_dark, share=False,
           server_port=int(os.environ.get("PORT", 3000)))
