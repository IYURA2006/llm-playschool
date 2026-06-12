import gradio as gr
import welcome
import annotation
import annotation_verdict

css = """
/* ── Shared ───────────────────────────────────────────────────── */
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

/* ── Left transcript column ───────────────────────────────────── */
/* Left column sizes to its content but is capped at the right rating column's
   height (set in JS, see sync_heights script) and scrolls internally past that.
   Short conversations stay short — no forced fill. */
.tx-col {
    background: #f1f5f9 !important;
    border-radius: 10px !important;
    overflow: hidden !important;
    padding: 0 !important;
    align-self: flex-start !important;
}
.txscroll {
    padding: 16px 18px;
    overflow-y: auto;
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

/* ── Right annotation column (stacked turn cards) ─────────────── */
#annot-col { background: #0a0e1a !important; border-radius: 10px !important; padding: 10px 12px !important; }
#annot-col > .wrap, #annot-col > .wrap > div { background: transparent !important; border: none !important; }
#annot-col label { color: #cbd5e1 !important; }

/* Per-turn annotation card.
   gr.Group applies elem_classes to a nested wrapper+inner pair (Gradio 6), so each
   card is two .turn-anno-card nodes. Style the outer wrapper as the visual card and
   pass the inner one through transparently. */
.turn-anno-card:has(.turn-anno-card) {
    background: black !important;
    border: 1px solid #1e3a5f !important;
    border-radius: 12px !important;
    padding: 14px 16px !important;
    margin-bottom: 14px !important;
}
.turn-anno-card:not(:has(.turn-anno-card)) {
    background: transparent !important; border: none !important; padding: 0 !important; margin: 0 !important;
}
.turn-anno-card .block, .turn-anno-card .form, .turn-anno-card .wrap {
    background: transparent !important; border: none !important; box-shadow: none !important;
}
/* Rated state: green border once Q1 and Q2 are both answered (pure CSS, scoped to
   the outer wrapper so the ring isn't drawn twice). */
.turn-anno-card:has(.turn-anno-card):has(.q1-scale input:checked):has(.q2-scale input:checked) {
    border-color: #22c55e !important;
    box-shadow: 0 0 0 1px rgba(34,197,94,.35) !important;
}
/* Card header */
.ta-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.ta-badge {
    width: 26px; height: 26px; border-radius: 50%;
    background: #3b82f6; color: #fff; flex-shrink: 0;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 13px; font-weight: 700;
}
.turn-anno-card:has(.q1-scale input:checked):has(.q2-scale input:checked) .ta-badge { background: #22c55e !important; }
.ta-title { font-size: 15px; font-weight: 600; color: #f1f5f9; }
.rated-badge { margin-left: auto; color: #22c55e; font-size: 12px; font-weight: 600; display: none; }
.turn-anno-card:has(.q1-scale input:checked):has(.q2-scale input:checked) .rated-badge { display: inline-flex; }
/* Rating buttons fill the card width, 4–5 equal columns */
.turn-anno-card .scale-radio .wrap {
    width: 100% !important; gap: 8px !important;
    background: transparent !important; border: none !important;
    padding: 0 !important; margin: 6px 0 10px !important;
    flex-wrap: nowrap !important;
}
.turn-anno-card .scale-radio label { flex: 1 1 0 !important; min-width: 0 !important; }

/* ── Turn navigator (client-side pagination) ──────────────────── */
.turn-nav {
    position: sticky; top: 6px; z-index: 5;
    display: flex; align-items: center; gap: 8px;
    background: #0d1424; border: 1px solid #1e293b;
    border-radius: 10px; padding: 8px 10px; margin-bottom: 12px;
}
.tn-chips { display: flex; flex-wrap: wrap; gap: 6px; flex: 1; justify-content: center; }
.tn-chip {
    min-width: 30px; height: 30px; padding: 0 8px;
    border: 1px solid #2d3748; border-radius: 8px;
    background: #161e2e; color: #94a3b8;
    font-size: 13px; font-weight: 600; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
    transition: background .12s, border-color .12s, color .12s;
}
.tn-chip:hover { border-color: #3b82f6; color: #cbd5e1; }
.tn-chip.is-rated { border-color: #22c55e; color: #4ade80; }
.tn-chip.is-current { background: #1d4ed8; border-color: #3b82f6; color: #fff; }
.tn-chip.is-current.is-rated { background: #16a34a; border-color: #22c55e; }
.tn-arrow {
    width: 32px; height: 32px; flex-shrink: 0;
    border: 1px solid #2d3748; border-radius: 8px;
    background: #161e2e; color: #cbd5e1;
    font-size: 18px; line-height: 1; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
}
.tn-arrow:hover { border-color: #3b82f6; }
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
/* ── Markdown question labels inside dark panel ───────────────── */
#annot-col .prose strong, #annot-col p strong { color: #e2e8f0 !important; }
#annot-col .prose p, #annot-col p { color: #64748b !important; font-size: 12px !important; margin: 2px 0 6px !important; }
.cond-tag {
    color: #94a3b8; font-size: 10px; font-style: italic;
    background: #1e293b; padding: 1px 6px; border-radius: 3px; margin-left: 6px;
}
.qd { color: #64748b; font-size: 12px; margin-bottom: 4px; }
.flags-lbl { color: #cbd5e1; font-size: 13px; font-weight: 600; margin-top: 12px; margin-bottom: 2px; }
.flags-sub { color: #64748b; font-weight: 400; font-size: 11px; }

/* ── Scale radio buttons ──────────────────────────────────────── */
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
    background: rgba(100,116,139,.07) !important;
    border: 1px solid rgba(100,116,139,.12) !important;
    border-radius: 10px !important;
}
.scale-radio label {
    background: #1e293b !important;
    border: 1px solid #334155 !important;
    color: #94a3b8 !important;
    border-radius: 6px !important;
    padding: 8px 14px !important;
    cursor: pointer !important;
    font-size: 11px !important;
    text-align: center !important;
    min-width: 62px !important;
    white-space: pre-line !important;
    line-height: 1.5 !important;
    transition: all .15s !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
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

/* ── Flags checkboxes — full-width bordered rows ──────────────── */
.flags-check .wrap { gap: 6px !important; flex-direction: column !important; }
.flags-check label {
    display: flex !important; align-items: center !important;
    width: 100% !important;
    background: #161e2e !important;
    border: 1px solid #2d3748 !important;
    border-radius: 8px !important;
    padding: 9px 12px !important;
    color: #cbd5e1 !important;
    font-size: 13px !important;
}
.flags-check label:hover { border-color: #3b82f6 !important; }

/* ── Comment textbox ──────────────────────────────────────────── */
.turn-comment textarea {
    background: #1e293b !important;
    border-color: #334155 !important;
    color: #cbd5e1 !important;
    font-size: 13px !important;
}
.turn-comment textarea::placeholder { color: #64748b !important; }

/* ── Verdict comment textbox ─────────────────────────────────── */
.verdict-comment { background: transparent !important; border: none !important; padding: 0 !important; box-shadow: none !important; }
.verdict-comment textarea {
    background: #1e2130 !important;
    border: 1px solid #383c4f !important;
    border-radius: 8px !important;
    color: #cbd5e1 !important;
}
.verdict-comment textarea::placeholder { color: #94a3b8 !important; }

/* ── Page containers — no focus ring ─────────────────────────── */
#annot-page, #annot-page:focus, #annot-page:focus-visible,
#annot-page > *, #annot-page > *:focus,
#verdict-page, #verdict-page:focus, #verdict-page:focus-visible,
#verdict-page > *, #verdict-page > *:focus {
    outline: none !important;
    box-shadow: none !important;
    border-color: transparent !important;
}

/* ── Verdict page option descriptions ────────────────────────── */
.option-desc p { font-size: 12px !important; color: #64748b !important; line-height: 1.6 !important; margin: 4px 0 !important; }
.option-desc strong { color: #374151 !important; }

/* ── Question cards (verdict page) ───────────────────────────── */
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
/* ── Coherence column cards (pure Gradio layout) ──────────────── */
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

/* ── Overall Game Quality — vertical list rows ────────────────── */
.ovr-row {
    align-items: center !important;
    gap: 14px !important;
    padding: 10px 6px !important;
    border-bottom: 1px solid #1e293b !important;
    flex-wrap: nowrap !important;
}
/* Number badge column */
.ovr-num {
    flex: 0 0 46px !important; min-width: 46px !important;
    display: flex !important; align-items: center !important;
    justify-content: center !important; padding: 0 !important;
}
.ovr-num .block, .ovr-num .wrap {
    padding: 0 !important; margin: 0 !important;
    display: flex !important; align-items: center !important; justify-content: center !important;
}
/* Bold label column */
.ovr-label { flex: 0 0 130px !important; min-width: 0 !important; }
.ovr-label p, .ovr-label strong {
    font-size: 14px !important; font-weight: 700 !important;
    color: #e2e8f0 !important; margin: 0 !important;
}
/* Description column */
.ovr-desc { flex: 1 1 0 !important; min-width: 0 !important; }
.ovr-desc p {
    font-size: 13px !important; color: #64748b !important;
    line-height: 1.5 !important; margin: 0 !important;
}
/* Select button column */
.ovr-sel-btn {
    flex: 0 0 76px !important; min-width: 76px !important;
    display: flex !important; align-items: center !important;
    justify-content: center !important; padding: 0 !important;
}
.ovr-sel-btn .block, .ovr-sel-btn .wrap {
    display: flex !important; align-items: center !important;
    justify-content: center !important; padding: 0 !important;
}
.ovr-sel-btn button {
    width: 100% !important; min-width: unset !important;
    font-size: 11px !important; font-weight: 600 !important;
    padding: 5px 10px !important; border-radius: 6px !important;
    box-shadow: none !important; letter-spacing: 0.02em !important;
}
.ovr-btn-err button {
    border-color: #ef4444 !important;
    box-shadow: 0 0 0 3px rgba(239,68,68,.15) !important;
}
/* Selected row highlight */
.ovr-row-sel {
    background: rgba(29, 78, 216, 0.12) !important;
    border-bottom-color: #1e40af !important;
    border-left: 3px solid #3b82f6 !important;
    border-radius: 0 4px 4px 0 !important;
    padding-left: 3px !important;
}
.ovr-row-sel .ovr-label p,
.ovr-row-sel .ovr-label strong { color: #93c5fd !important; }
/* Strip block backgrounds inside rows */
.ovr-row .block, .ovr-row > div > .block {
    background: transparent !important; border: none !important; padding: 0 !important;
}

/* Buttons stretch to fill the card */
.question-card .scale-radio .wrap {
    width: 100% !important;
    justify-content: space-evenly !important;
    box-sizing: border-box !important;
    border: 1px solid rgba(100,116,139,.12) !important;
}

/* ── Welcome page ─────────────────────────────────────────────── */
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

/* ── Rules panel ──────────────────────────────────────────────── */
.rules-panel {
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 10px !important;
    padding: 20px !important;
    margin-top: 10px !important;
}
"""

# Head scripts:
#  1. Force dark mode (runs as <head> parses, before render — no light-theme flash).
#  2. Live "X of N turns rated" counter — counts cards whose Q1+Q2 are answered,
#     so the nav progress updates without wiring a Gradio event per turn.
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
    // Each turn is one annotation card. gr.Group doubles the class onto a
    // wrapper+inner pair, so the "real" cards are the outer wrappers.
    function panes() {
        return Array.prototype.filter.call(
            document.querySelectorAll('.turn-anno-card'),
            function (c) { return c.querySelector('.turn-anno-card'); }
        );
    }
    function chips() {
        return Array.prototype.slice.call(document.querySelectorAll('.tn-chip'));
    }
    function isRated(card) {
        return !!(card.querySelector('.q1-scale input:checked') &&
                  card.querySelector('.q2-scale input:checked'));
    }

    var current = 0;

    // Show only the current card; sync chips, transcript highlight, and counter.
    function refresh() {
        var cards = panes();
        if (!cards.length) return;
        current = Math.max(0, Math.min(cards.length - 1, current));
        var cs = chips(), rated = 0;
        cards.forEach(function (card, i) {
            card.style.display = (i === current ? '' : 'none');
            var r = isRated(card);
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
        var active = document.getElementById('tc-' + current);
        if (active) active.classList.add('active-turn');
        var el = document.querySelector('#annot-page .prog-rated');
        if (el) el.textContent = rated + ' of ' + cards.length + ' turns rated';
    }

    function goTo(i, focusChip) {
        current = i;
        refresh();
        var active = document.getElementById('tc-' + current);
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

    function init() {
        if (!panes().length || !chips().length) return false;
        wireAria();
        document.addEventListener('click', onClick);
        document.addEventListener('keydown', onKey);
        document.addEventListener('change', refresh);
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
})();
</script>
<script>
// Cap the left transcript's height to the right rating column so the two panels
// line up, while letting a short transcript stay short. Gradio's flexbox won't
// transfer the height reliably, so measure it directly and re-sync on resize and
// whenever the visible rating card changes (which changes the right column height).
(function () {
    function sync() {
        var annot = document.getElementById('annot-col');
        var scroll = document.querySelector('.txscroll');
        if (!annot || !scroll) return;
        var h = annot.offsetHeight;
        if (h > 0) scroll.style.maxHeight = h + 'px';
    }
    function init() {
        var annot = document.getElementById('annot-col');
        var scroll = document.querySelector('.txscroll');
        if (!annot || !scroll) return false;
        sync();
        if (window.ResizeObserver) { new ResizeObserver(sync).observe(annot); }
        window.addEventListener('resize', sync);
        // Selecting a turn / answering a question can resize the right column.
        document.addEventListener('click', function () { setTimeout(sync, 50); });
        document.addEventListener('change', function () { setTimeout(sync, 50); });
        return true;
    }
    if (!init()) {
        var tries = 0;
        var iv = setInterval(function () {
            if (init() || ++tries > 100) clearInterval(iv);
        }, 100);
    }
})();
</script>
"""

with gr.Blocks() as app:
    welcome_page = gr.Column(visible=True)
    annotation_page = gr.Column(visible=False, elem_id="annot-page")
    verdict_page = gr.Column(visible=False, elem_id="verdict-page")

    welcome.build(welcome_page, annotation_page)
    annotation.build(welcome_page, annotation_page, verdict_page)
    annotation_verdict.build(welcome_page, annotation_page, verdict_page)

app.launch(css=css, theme=gr.themes.Soft(), head=force_dark, share=True)
