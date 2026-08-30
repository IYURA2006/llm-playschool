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

/* Visible to assistive tech only. Gradio ships its own .sr-only but it's
   Svelte-scoped (.sr-only.svelte-xxxx), so it can't be reused from here. */
.a11y-sr-only {
    position: absolute !important;
    width: 1px !important; height: 1px !important;
    margin: -1px !important; padding: 0 !important;
    overflow: hidden !important; clip: rect(0, 0, 0, 0) !important;
    white-space: nowrap !important; border: 0 !important;
}
/* What this block suppresses is Gradio's focus *chrome* — the block-border
   recolour and box-shadow it puts on wrappers. It used to kill outlines too,
   which left the whole app with no visible keyboard focus. Outlines are now
   restored below; everything else here is unchanged on purpose. */
*:focus, *:focus-visible, *:focus-within { box-shadow: none !important; }
input:focus, textarea:focus, button:focus, [tabindex]:focus { box-shadow: none !important; border-color: inherit !important; }
:root {
    --block-border-color-focus: transparent !important;
    --input-border-color-focus: transparent !important;
}
/* :focus only, not :focus-visible — containers we focus programmatically
   (screen headings, the dialog card) must not draw a ring, but real keyboard
   focus on a control must. */
.block:focus,
.wrap:focus,
.col:focus,
div:focus {
    box-shadow: none !important;
    border-color: inherit !important;
}

/* The single focus indicator for the whole app. #93c5fd clears 3:1 against
   every surface a control actually sits on (6.4–10.7; worst is the selected
   coherence blue at 3.7). It would fail on the light transcript panel, but
   that panel contains only static markup — no focusable controls. */
:is(a, button, summary, input, select, textarea, [tabindex]:not([tabindex="-1"])):focus-visible {
    outline: 3px solid #93c5fd !important;
    outline-offset: 2px !important;
}
/* Scale options sit 5–6px apart, so a 3px/2px ring on adjacent labels would
   collide. Tighter ring here only. */
.scale-radio label:focus-visible,
.scale-radio label:has(input[type=radio]:focus-visible) {
    outline: 2px solid #93c5fd !important;
    outline-offset: 1px !important;
}
/* The radio input itself is the 1px sr-only box — draw on the label, not it. */
.scale-radio input[type=radio]:focus-visible { outline: none !important; }

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
.game-id-tag  { color: #94a3b8; font-size: 12px; font-family: monospace; }
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
/* An <h2> so the transcript has an entry point for heading navigation;
   margin-top zeroed since it was a <div> before. */
.goal-label {
    font-size: 10px; font-weight: 700;
    color: #1d4ed8; letter-spacing: .08em;
    margin-top: 0; margin-bottom: 6px;
}
/* !important keeps these dark on the light .tx-col panel even under the app's own forced dark theme. */
.goal-text { font-size: 13px; color: #374151 !important; line-height: 1.5; margin: 0; white-space: pre-line; overflow-wrap: anywhere; }
.gm-msg {
    font-size: 12px; color: #4b5563 !important;
    padding: 5px 10px; margin: 6px 0;
    border-left: 2px solid #cbd5e1;
    line-height: 1.5;
    white-space: pre-line;
    overflow-wrap: anywhere;
}
.gm-tag { font-weight: 700; color: #475569 !important; font-size: 10px; letter-spacing: .06em; margin-right: 4px; }
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
/* Which transcript turn you're rating was signalled by background colour
   alone. refresh() also sets aria-current on this card. */
.turn-card.active-turn .card-header::after {
    content: " · NOW RATING";
    color: #93c5fd; font-weight: 700; letter-spacing: .06em;
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
.game-end-msg { text-align: center; color: #4b5563 !important; font-size: 13px; padding: 10px 0; }
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
/* An <h3>, not a span, so the turn cards are reachable by heading navigation.
   Margins must be zeroed — it sits in a flex row that assumed an inline span. */
.ta-title { font-size: 15px; font-weight: 600; color: #f1f5f9; margin: 0 !important; }
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
/* Every tile paints the colour the transcript literally names — never the
   colour that would mean the same thing in standard Wordle. wordle-crazy
   scrambles the key (yellow=correct, black=wrong-position, purple=absent), and
   silently normalising it would hide the very thing the variant tests. The
   meaning travels as text on each tile and in .wd-legend, not as hue. */
.wd-green  { background: #22c55e; color: #052e16; }
.wd-yellow { background: #eab308; color: #3b2a03; }
.wd-red    { background: #b91c1c; color: #fff1f2; }
.wd-purple { background: #7e22ce; color: #faf5ff; }
.wd-black  { background: #1f2937; color: #f3f4f6; }

/* ImageGame before/after grids. Cells carry the change as a ring AND a bold
   weight change, never colour alone. */
.ig-pair { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 6px; }
.ig-grid { display: inline-block; }
.ig-cap {
    font-size: 9px; font-weight: 700; letter-spacing: .08em;
    color: #94a3b8; margin-bottom: 3px; text-transform: uppercase;
}
.ig-row { display: flex; gap: 2px; margin-bottom: 2px; }
.ig-cell {
    width: 20px; height: 20px; border-radius: 3px;
    display: inline-flex; align-items: center; justify-content: center;
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-size: 12px; font-weight: 700;
    background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
}
.ig-cell.ig-empty { color: #94a3b8; background: #0f172a; }
.ig-cell.ig-changed {
    background: #1d4ed8; color: #fff; border-color: #93c5fd;
    box-shadow: 0 0 0 2px rgba(147,197,253,.45);
}
.ig-arrow { color: #64748b; font-size: 16px; }
.ig-note { font-size: 11px; color: #94a3b8; margin-bottom: 8px; }

/* Colour key, shown once under the goal box for wordle-family transcripts. */
.wd-legend {
    display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
    margin-top: 10px; padding-top: 8px; border-top: 1px solid #bfdbfe;
}
.wd-legend-lbl {
    font-size: 10px; font-weight: 700; color: #1d4ed8; letter-spacing: .08em;
}
.wd-legend-item {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 12px; color: #374151 !important;
}

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
    padding: 0 5px;   /* claw back the width the ✓ adds */
}
/* Green fill alone made "rated" a colour-only cue. The tick is the non-colour
   half; refresh() adds ", rated" to the chip's accessible name for the rest. */
.tn-chip.is-rated::after { content: "✓"; margin-left: 3px; font-size: 11px; }
/* Unanswered turn after a failed submit — again, not colour alone. */
.tn-chip[data-a11y-err] {
    border-color: #f87171 !important; border-width: 2px !important;
}
.tn-chip[data-a11y-err]::after { content: "!"; margin-left: 3px; font-weight: 700; }
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
#annot-col .prose p, #annot-col p { color: #94a3b8 !important; font-size: 12px !important; margin: 1px 0 3px !important; }
.cond-tag {
    color: #94a3b8; font-size: 10px; font-style: italic;
    background: #1e293b; padding: 1px 6px; border-radius: 3px; margin-left: 6px;
}
.flags-lbl { color: #cbd5e1; font-size: 13px; font-weight: 600; margin-top: 3px; margin-bottom: 2px; }
.flags-sub { color: #94a3b8; font-weight: 400; font-size: 11px; }

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
/* Unanswered-question state. Keyed off aria-invalid, which the a11y module
   sets on submit — so the visual cue and the announced one can't drift apart.
   (The old .radio-error class was never applied by any code path, which is
   why a failed submit used to highlight nothing at all.) */
fieldset[aria-invalid="true"] {
    border-color: #ef4444 !important;
    box-shadow: 0 0 0 3px rgba(239,68,68,.18) !important;
    border-radius: 8px !important;
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
.turn-comment textarea::placeholder { color: #94a3b8 !important; }

.verdict-comment { background: transparent !important; border: none !important; padding: 0 !important; box-shadow: none !important; }
.verdict-comment textarea {
    background: #1e2130 !important;
    border: 1px solid #383c4f !important;
    border-radius: 8px !important;
    color: #cbd5e1 !important;
}
.verdict-comment textarea::placeholder { color: #94a3b8 !important; }

/* Page containers only — :focus, never :focus-visible, so the a11y module can
   focus a page/heading on screen change without painting a ring on it. */
#annot-page, #annot-page:focus,
#annot-page > *, #annot-page > *:focus,
#verdict-page, #verdict-page:focus,
#verdict-page > *, #verdict-page > *:focus,
#train-page, #train-page:focus,
#train-page > *, #train-page > *:focus {
    box-shadow: none !important;
    border-color: transparent !important;
}


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
.coh-num {
    font-size: 26px !important; font-weight: 700 !important;
    color: #94a3b8 !important; margin: 0 0 4px !important; text-align: center !important;
}
.coh-col.coh-col-sel .coh-num { color: rgba(255,255,255,.9) !important; }
.coh-lbl-md p, .coh-lbl-md strong {
    font-size: 13px !important; font-weight: 600 !important;
    color: #e2e8f0 !important; margin: 0 0 6px !important;
}
.coh-col.coh-col-sel .coh-lbl-md p,
.coh-col.coh-col-sel .coh-lbl-md strong { color: #fff !important; }
.coh-desc-md p {
    font-size: 12px !important; color: #94a3b8 !important;
    line-height: 1.5 !important; margin: 0 !important;
}
.coh-col.coh-col-sel .coh-desc-md p { color: rgba(255,255,255,.6) !important; }
/* elem_classes lands on the <button> itself, so the old ".coh-sel-btn button"
   selector matched nothing and was never applied. Target the button directly;
   sizing intentionally left to Gradio's size="sm" as that's what has been
   rendering all along. */
.coh-sel-btn { margin-top: 8px !important; }
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
    font-size: 12px !important; color: #94a3b8 !important;
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
    font-size: 13px !important; color: #94a3b8 !important;
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
/* The accessibility link is a bare <p>, not Markdown-wrapped, so it needs the
   same treatment directly. Colour is lifted off .welcome-foot's #475569:
   that fails WCAG AA on this background, and a link people cannot read is
   the one link on the page that has to be legible. */
.a11y-foot { font-size: 12px !important; text-align: center !important; margin-top: 4px !important; }
.a11y-foot a { color: #94a3b8 !important; text-decoration: underline !important; }
.a11y-foot a:hover, .a11y-foot a:focus { color: #e2e8f0 !important; }
/* Top nav: name badge left, Prolific badge pushed right */
.welcome-nav { display: flex; align-items: center; gap: 10px; width: 100%; }
.welcome-nav .prolific-badge { margin-left: auto; }
.prolific-badge {
    background: #3a2e08; color: #fcd34d; border: 1px solid #a16207;
    padding: 4px 10px; border-radius: 6px;
    font-size: 11px; font-weight: 700; letter-spacing: .03em;
}
/* Was "**Rating scale** - applies…" as body markdown; now a real <h2> styled
   to match what that rendered as, so the outline gains a level and the page
   looks identical. */
.rating-scale-h {
    font-size: 16px !important; font-weight: 700 !important;
    color: #e2e8f0 !important; margin: 10px 0 2px !important;
}
.rating-scale-h span { font-weight: 400 !important; color: #94a3b8 !important; }
/* Step cards: three boxes of identical size. equal_height=True alone only
   stretches heights — the widths still follow each card's content, so the
   three come out uneven. flex-basis 0 makes the row split evenly regardless
   of how much text each card holds. */
.step-row { display: flex !important; align-items: stretch !important; }
.step-row > * {
    flex: 1 1 0 !important;
    min-width: 0 !important;
    align-self: stretch !important;
    /* height:auto, NOT 100%: an explicit height cancels the stretch, and each
       card then sizes to its own text — which is what made the middle one
       taller when its paragraph wrapped onto a fifth line. */
    height: auto !important;
}
.step-card { display: flex !important; flex-direction: column !important; }
/* The session-count line renders nothing when there is no assigned playlist.
   Its container still occupies a row's worth of padding and gap, leaving a
   gap between the intro text and the cards. */
.session-line { gap: 0 !important; padding: 0 !important; margin: 0 !important; }
.session-line:empty { display: none !important; }
.step-card h3 { font-size: 15px !important; margin: 2px 0 4px !important; }
.step-card p { font-size: 12.5px !important; line-height: 1.55 !important; }
/* Coloured number badge for the rating rows */
.rating-badge {
    width: 30px; height: 30px; border-radius: 7px; border: 1px solid;
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; font-weight: 700; box-sizing: border-box;
}
/* Welcome-screen scale summary. The rating rows on the annotation screens are
   one option per line, which would run to 19 lines here; on the landing page
   the same information has to stay scannable, so options sit inline and wrap.
   .rating-badge is reused as-is for the numbers. */
.scale-group-h {
    font-size: 14px !important; font-weight: 700 !important;
    color: #e2e8f0 !important; margin: 2px 0 10px !important;
}
.scale-q { padding: 8px 2px 12px; border-bottom: 1px solid #1e293b; }
.scale-q:last-of-type { border-bottom: none; padding-bottom: 4px; }
.scale-q-title { font-size: 13.5px; font-weight: 700; color: #e2e8f0; }
.scale-q-range {
    font-weight: 400 !important; color: #64748b !important;
    font-size: 12.5px !important; margin-left: 8px;
}
.scale-q-note {
    font-size: 13px; color: #94a3b8; line-height: 1.5; margin: 2px 0 9px;
}
.scale-star { color: #64748b; font-weight: 400; }
/* The starred note sits at the foot of its box, below a divider, so it reads
   as a footnote to the group rather than as part of the last question. */
.scale-foot {
    font-size: 12.5px !important; color: #94a3b8 !important;
    line-height: 1.5 !important;
    margin: 16px 0 0 !important;
    padding-top: 12px !important;
    border-top: 1px solid #1e293b !important;
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
/* The completion link is now the only way back to Prolific (the auto-redirect
   was removed), so it gets button-sized affordance rather than inline-link. */
#verdict-status a {
    display: inline-block;
    background: #1d4ed8; color: #fff !important;
    padding: 10px 18px; border-radius: 8px;
    font-weight: 700; text-decoration: none !important;
    min-height: 24px;
}
#verdict-status a:hover { background: #2563eb; }

/* Secondary to "I agree" but must stay visibly available — it's the dialog's
   only other way out. */
.consent-decline-btn {
    background: transparent !important;
    border: 1px solid #334155 !important;
    color: #94a3b8 !important;
    margin-top: 8px !important;
}
.consent-decline-btn:hover { border-color: #64748b !important; color: #cbd5e1 !important; }
.consent-todo {
    background: #7c2d12; color: #fed7aa; border: 1px solid #c2410c;
    padding: 1px 8px; border-radius: 5px; font-weight: 700; font-size: 12.5px;
    white-space: nowrap;
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
#train-col .prose p, #train-col p { color: #94a3b8 !important; font-size: 12px !important; margin: 2px 0 6px !important; }
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
    var _progTimer = null;   // debounces the aria-live rated counter

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
                // Rated state is a green fill visually; carry it in the name too,
                // or it's colour-only. Safe here — chips are our own HTML, not a
                // Gradio Block root (Svelte would strip aria-label from those).
                chip.setAttribute('aria-label',
                    'Turn ' + (i + 1) + (r ? ', rated' : ', not rated'));
            }
        });
        document.querySelectorAll('.turn-card').forEach(function (t) {
            t.classList.remove('active-turn');
            t.removeAttribute('aria-current');
        });
        var active = transcriptCardEl(current);
        if (active) {
            active.classList.add('active-turn');
            active.setAttribute('aria-current', 'true');
        }
        // .prog-rated is an aria-live region, so writing it on every radio
        // click would narrate the counter continuously. Debounce to one
        // announcement per burst.
        var el = document.querySelector('#annot-page .prog-rated');
        if (el) {
            var next = rated + ' of ' + cards.length + ' turns rated';
            if (el.textContent !== next) {
                clearTimeout(_progTimer);
                _progTimer = setTimeout(function () { el.textContent = next; }, 750);
            }
        }
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
        // The a11y module's validation handler needs to reveal a hidden turn
        // card before it can focus a control inside it.
        window.__a11y = window.__a11y || {};
        window.__a11y.goTo = goTo;
        window.__a11y.turnCount = function () { return panes().length; };
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
<script>
/* Accessibility module. Deliberately a separate IIFE from the turn-nav script
   above so a failure in one can't take out the other.

   Two hard rules, both from how Gradio's Svelte build re-renders:
     1. Never set aria-label on a Gradio Block root — Svelte's attribute spread
        includes aria-label and deletes ours on the next re-render. Use
        aria-labelledby / aria-describedby / aria-invalid / role, which it
        doesn't touch. (aria-label IS fine on HTML we author ourselves.)
     2. Never add a class to a node whose elem_classes or variant can change —
        Svelte assigns className wholesale and wipes it. Attributes only. */
(function () {
    'use strict';

    var uid = 0;
    function ensureId(el, prefix) {
        if (el && !el.id) el.id = prefix + '-' + (++uid);
        return el ? el.id : '';
    }
    function text(el) { return (el && el.textContent || '').trim(); }

    /* ---------- live regions (C) ----------------------------------------
       Appended to document.body, outside <gradio-app>, so no Gradio re-render
       can destroy them. Everything else mirrors INTO these. */
    var polite = null, assertive = null;

    function ensureRegions() {
        if (polite || !document.body) return;
        polite = document.createElement('div');
        polite.id = 'a11y-polite';
        polite.className = 'a11y-sr-only';
        polite.setAttribute('aria-live', 'polite');
        polite.setAttribute('aria-atomic', 'true');
        assertive = document.createElement('div');
        assertive.id = 'a11y-assertive';
        assertive.className = 'a11y-sr-only';
        assertive.setAttribute('role', 'alert');
        assertive.setAttribute('aria-live', 'assertive');
        assertive.setAttribute('aria-atomic', 'true');
        document.body.appendChild(polite);
        document.body.appendChild(assertive);
    }

    function announce(msg, opts) {
        ensureRegions();
        var region = (opts && opts.assertive) ? assertive : polite;
        if (!region || !msg) return;
        // Clear first, then set on the next frame — without this, submitting
        // twice with the same error produces no second announcement.
        region.textContent = '';
        requestAnimationFrame(function () { region.textContent = msg; });
    }

    /* ---------- accessible names for Radio / CheckboxGroup (A) ----------
       Gradio renders these as <fieldset> whose title is a <span>, not a
       <legend>, and wires no aria-labelledby — so label= alone leaves the
       group unnamed. Point the fieldset at its own (sr-only) title span.
       Textbox and Slider need none of this; label= is enough for them. */
    function nameFieldsets() {
        document.querySelectorAll('fieldset').forEach(function (fs) {
            if (fs.getAttribute('aria-labelledby')) return;
            var span = fs.querySelector('span[data-testid="block-info"]');
            // Guard on non-empty: an unlabelled control would otherwise pick up
            // Gradio's i18n fallback and get named "Radio".
            if (span && text(span)) {
                fs.setAttribute('aria-labelledby', ensureId(span, 'a11y-lbl'));
                return;
            }
            // CheckboxGroup renders that span empty no matter what label= says,
            // so fall back to the visible "Flags — tick all that apply" heading
            // in the same turn card. Scoped to the card rather than walking
            // siblings, because Gradio's wrapper nesting varies. One flags
            // group per card, so this is unambiguous — and it means the name
            // and the on-screen text can't diverge.
            if (!fs.classList.contains('flags-check')) return;
            var card = fs.closest('.turn-anno-card, .train-card');
            var vis = card && card.querySelector('.flags-lbl');
            if (vis && text(vis)) fs.setAttribute('aria-labelledby', ensureId(vis, 'a11y-lbl'));
        });
    }

    /* ---------- coherence widget as a real radiogroup (B) --------------- */
    function wireCoherence() {
        var cols = document.querySelectorAll('#verdict-page .coh-col');
        if (!cols.length) return;
        var row = cols[0].parentElement;
        if (!row) return;

        if (row.getAttribute('role') !== 'radiogroup') {
            row.setAttribute('role', 'radiogroup');
            var heading = document.querySelector('#verdict-page .g1-card h3, #verdict-page .coh-group-label');
            if (heading) row.setAttribute('aria-labelledby', ensureId(heading, 'a11y-coh'));
        }

        var btns = [];
        cols.forEach(function (col) {
            var btn = col.querySelector('.coh-sel-btn');
            if (!btn) return;
            btns.push(btn);
            if (btn.getAttribute('role') !== 'radio') {
                btn.setAttribute('role', 'radio');
                // Name it from the number + label + description already on
                // screen, so it announces "2, Rigid, Had a plan but…" instead
                // of the visible word "Select" (which stays unchanged).
                var ids = ['.coh-num', '.coh-lbl-md', '.coh-desc-md'].map(function (sel) {
                    var n = col.querySelector(sel);
                    return n ? ensureId(n, 'a11y-coh') : '';
                }).filter(Boolean);
                if (ids.length) btn.setAttribute('aria-labelledby', ids.join(' '));
            }
            // .coh-col-sel is authoritative — it's what the server sets.
            var sel = col.classList.contains('coh-col-sel');
            btn.setAttribute('aria-checked', sel ? 'true' : 'false');
        });

        // Roving tabindex: the group is one tab stop, arrows move within it.
        var selIdx = btns.findIndex(function (b) {
            return b.getAttribute('aria-checked') === 'true';
        });
        if (selIdx < 0) selIdx = 0;
        btns.forEach(function (b, i) {
            b.setAttribute('tabindex', i === selIdx ? '0' : '-1');
        });
    }

    function onCoherenceKey(e) {
        var btn = e.target.closest && e.target.closest('.coh-sel-btn');
        if (!btn) return;
        var btns = Array.prototype.slice.call(
            document.querySelectorAll('#verdict-page .coh-sel-btn'));
        var i = btns.indexOf(btn);
        if (i < 0) return;
        var next = null;
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = btns[(i + 1) % btns.length];
        else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = btns[(i - 1 + btns.length) % btns.length];
        else if (e.key === 'Home') next = btns[0];
        else if (e.key === 'End') next = btns[btns.length - 1];
        if (!next) return;
        e.preventDefault();
        // Optimistic: _coh_select is a server round-trip, and waiting for it
        // would delay the announcement past the point it's useful.
        btns.forEach(function (b) { b.setAttribute('aria-checked', 'false'); b.setAttribute('tabindex', '-1'); });
        next.setAttribute('aria-checked', 'true');
        next.setAttribute('tabindex', '0');
        next.focus();
        next.click();
    }

    /* ---------- screen transitions (G + H) ------------------------------ */
    var SCREENS = [
        ['welcome-page',  'Welcome',         'Welcome'],
        ['train-page',    'Practice round',  'Practice round'],
        ['annot-page',    'Rate turns',      'Rate turns'],
        ['verdict-page',  'Overall verdict', 'Overall verdict']
    ];
    var TITLE_SUFFIX = ' — LM Playschool Annotation Study';
    var currentScreen = null;

    function visibleScreen() {
        for (var i = 0; i < SCREENS.length; i++) {
            var el = document.getElementById(SCREENS[i][0]);
            if (el && getComputedStyle(el).display !== 'none') return SCREENS[i];
        }
        return null;
    }

    function syncScreen() {
        var s = visibleScreen();
        if (!s || (currentScreen && s[0] === currentScreen[0])) return;
        var first = currentScreen === null;
        currentScreen = s;
        document.title = s[1] + TITLE_SUFFIX;
        if (first || dialogOpen) return;   // don't steal focus on first paint
        var el = document.getElementById(s[0]);
        var h = el && el.querySelector('h1, h2');
        var target = h || el;
        if (target) {
            target.setAttribute('tabindex', '-1');
            target.focus({ preventScroll: false });
        }
        announce(s[2]);
    }

    /* ---------- consent dialog (E) -------------------------------------- */
    var dialogOpen = false, dialogReturn = null;
    var PAGE_IDS = ['welcome-page', 'train-page', 'annot-page', 'verdict-page'];

    function focusablesIn(root) {
        return Array.prototype.slice.call(root.querySelectorAll(
            'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'
        )).filter(function (el) { return el.offsetParent !== null; });
    }

    function openDialog(modal) {
        dialogOpen = true;
        dialogReturn = document.activeElement;
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        var h = modal.querySelector('h2');
        if (h) modal.setAttribute('aria-labelledby', ensureId(h, 'a11y-dlg'));
        PAGE_IDS.forEach(function (id) {
            var p = document.getElementById(id);
            if (p) { p.setAttribute('inert', ''); p.setAttribute('aria-hidden', 'true'); }
        });
        var card = modal.querySelector('.consent-modal-card') || modal;
        card.setAttribute('tabindex', '-1');
        // Focus the card, not the checkbox — the sheet should be read from the
        // top, not from 130 lines past the text being consented to.
        requestAnimationFrame(function () { card.focus(); });
    }

    function closeDialog(modal) {
        dialogOpen = false;
        modal.removeAttribute('role');
        modal.removeAttribute('aria-modal');
        PAGE_IDS.forEach(function (id) {
            var p = document.getElementById(id);
            if (p) { p.removeAttribute('inert'); p.removeAttribute('aria-hidden'); }
        });
        var ret = dialogReturn;
        dialogReturn = null;
        // Consent may or may not navigate (_confirm_consent chains into _start).
        // Wait a frame: if the screen changed, syncScreen owns focus; if not,
        // put it back where it was. One owner either way, so they can't race.
        requestAnimationFrame(function () {
            var before = currentScreen;
            syncScreen();
            if (before === currentScreen && ret && document.contains(ret)) ret.focus();
        });
    }

    function onDialogKey(e) {
        if (!dialogOpen) return;
        var modal = document.getElementById('consent-modal');
        if (!modal) return;
        if (e.key === 'Escape') {
            var decline = modal.querySelector('.consent-decline-btn');
            if (decline) { e.preventDefault(); decline.click(); }
            return;
        }
        if (e.key !== 'Tab') return;
        // Trap Tab inside the dialog. Safe only because there's a decline path
        // (the "I do not agree" button + Escape) — a trap without one would be
        // a WCAG 2.1.2 keyboard trap for anyone who reads the sheet and says no.
        var f = focusablesIn(modal);
        if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }

    function syncDialog() {
        var modal = document.getElementById('consent-modal');
        if (!modal) return;
        var open = getComputedStyle(modal).display !== 'none';
        if (open && !dialogOpen) openDialog(modal);
        else if (!open && dialogOpen) closeDialog(modal);
    }

    /* ---------- status mirrors + validation errors (C + D) -------------- */
    var MIRRORS = [
        ['annot-status',   true],
        ['verdict-status', true],
        ['welcome-error',  true],
        ['consent-note',   true],
        ['welcome-status', false],
        ['train-summary',  false]
    ];
    var lastSaid = {};

    function readStatus(el) {
        // The bad-turns marker is hidden metadata, not part of the message.
        var clone = el.cloneNode(true);
        clone.querySelectorAll('.a11y-bad-turns').forEach(function (n) { n.remove(); });
        return (clone.textContent || '').trim();
    }

    function handleStatus(id, isAssertive) {
        var el = document.getElementById(id);
        if (!el) return;
        var msg = readStatus(el);
        if (!msg || lastSaid[id] === msg) return;
        lastSaid[id] = msg;
        announce(msg, { assertive: isAssertive });
        if (id === 'annot-status') applyTurnErrors(el);
    }

    function clearTurnErrors() {
        document.querySelectorAll('#annot-page fieldset[aria-invalid]').forEach(function (fs) {
            fs.removeAttribute('aria-invalid');
            fs.removeAttribute('aria-describedby');
        });
        document.querySelectorAll('.tn-chip[data-a11y-err]').forEach(function (c) {
            c.removeAttribute('data-a11y-err');
        });
    }

    // annotation.py's _submit emits an empty <span class="a11y-bad-turns"> as
    // the "this was a validation failure" flag, and names the turns in the
    // message itself. We parse the numbers back out of that sentence because
    // Gradio's markdown sanitiser strips data-* attributes, extra classes and
    // element text — the visible text is the only channel left. Kept in step
    // with the f-string in _submit.
    // The offending card is display:none at this point, so mark it, then
    // reveal it via the turn-nav module before focusing anything.
    function applyTurnErrors(statusEl) {
        clearTurnErrors();
        if (!statusEl.querySelector('.a11y-bad-turns')) return;
        var m = /on turns?\\s+([\\d,\\s]+)/.exec(statusEl.textContent || '');
        if (!m) return;
        var idx = m[1].split(/[,\\s]+/)
            .map(function (s) { return parseInt(s, 10); })
            .filter(function (n) { return !isNaN(n); })
            .map(function (n) { return n - 1; })    // message is 1-based
            .sort(function (a, b) { return a - b; });
        if (!idx.length) return;

        var chips = document.querySelectorAll('#annot-page .tn-chip');
        idx.forEach(function (i) {
            if (chips[i]) chips[i].setAttribute('data-a11y-err', 'true');
        });

        var first = idx[0];
        if (window.__a11y && window.__a11y.goTo) window.__a11y.goTo(first, false);
        requestAnimationFrame(function () {
            var cards = document.querySelectorAll('#annot-page .turn-anno-card');
            var card = cards[first];
            if (!card) return;
            card.querySelectorAll('fieldset').forEach(function (fs) {
                if (fs.querySelector('input:checked')) return;
                fs.setAttribute('aria-invalid', 'true');
                fs.setAttribute('aria-describedby', 'annot-status');
            });
            var bad = card.querySelector('fieldset[aria-invalid] label');
            if (bad) bad.focus();
        });
    }

    function syncStatuses() {
        MIRRORS.forEach(function (m) { handleStatus(m[0], m[1]); });
    }

    /* ---------- boot ---------------------------------------------------- */
    function sync() {
        ensureRegions();
        nameFieldsets();
        wireCoherence();
        syncScreen();
        syncDialog();
        syncStatuses();
    }

    function boot() {
        sync();
        document.addEventListener('keydown', onDialogKey, true);
        document.addEventListener('keydown', onCoherenceKey);
        document.addEventListener('change', function () {
            clearTurnErrors();
            setTimeout(wireCoherence, 0);
        });
        if (window.MutationObserver) {
            var pending = null;
            new MutationObserver(function () {
                clearTimeout(pending);
                pending = setTimeout(sync, 60);
            }).observe(document.documentElement,
                { childList: true, subtree: true, attributes: true,
                  attributeFilter: ['class', 'style'] });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
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
        # The only bound on what reaches annotator_id, now that the PID is
        # stored as Prolific sends it — a sanity check, not a format check.
        if len(prolific_pid) > 100:
            return _session_error("⚠️ Malformed participant link.")
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


# Refuse to serve a misconfigured study. games/ (the 234-transcript pilot pool)
# and games_study/ (the curated 416) share zero slugs, so pointing GAMES_DIR at
# the wrong one would recruit real participants onto the wrong corpus — and the
# symptom would be an "unknown game" error shown AFTER their rows were reserved.
_preflight = assignment.preflight()
if _preflight:
    raise SystemExit(
        "Refusing to start — the study inventory does not check out:\n  "
        + "\n  ".join(f"- {p}" for p in _preflight)
        + "\n\nFix the above (usually: GAMES_DIR=games_study in .env, or "
          "re-run build_study_set.py / build_batches.py), then restart."
    )

with gr.Blocks(title="LM Playschool — Annotation Study") as app:
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

    welcome_page = gr.Column(visible=True, elem_id="welcome-page")
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
    training.build(welcome_page, training_page, annotation_page, started_at_state,
                   annotator_state)
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
# Serve the accessibility statement as a static file, reachable at
# /gradio_api/file/accessibility.html. Required by the Public Sector Bodies
# Accessibility Regulations 2018: a statement nobody can reach does not
# discharge the duty, and this app has no other route to it.
#
# set_static_paths is an ALLOW-LIST, not a served directory — anything not
# named here (db.py, .env) still returns 403, verified. Keep it that way.
gr.set_static_paths([os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "accessibility.html")])

theme = gr.themes.Soft(
    font=[gr.themes.Font(f) for f in ("system-ui", "-apple-system", "Segoe UI", "sans-serif")],
    font_mono=[gr.themes.Font(f) for f in ("ui-monospace", "SFMono-Regular", "monospace")],
)
# share=True tunnels through Gradio's own servers, which we don't need —
# the VM's own domain/TLS setup is the real public entry point.
# 3000 is not arbitrary: Apache on breezy.inf.ed.ac.uk terminates TLS on 443
# and reverse-proxies to 127.0.0.1:3000, so this default is what makes the
# public URL work with no config on the VM. Do not "align" it with the 7860 in
# the Dockerfile/nginx template on the vm-deploy branch — that infra predates
# the VM and targets nginx, which breezy does not run. A mismatch here surfaces
# as an Apache 503, with a perfectly healthy-looking app log.
app.launch(css=css, theme=theme, head=force_dark, share=False,
           server_port=int(os.environ.get("PORT", 3000)))
