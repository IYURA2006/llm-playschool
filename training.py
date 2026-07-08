"""Interactive practice round shown before a playlist session starts.

The annotator rates a short, easily-verifiable Wordle game (weaker model, real
transcript) with the universal Q1/Q2, then reveals reference ratings with
explanations. Nothing here is persisted — it exists purely to calibrate people
on the 1–4 scales before their first real game.

The reference ratings and wording live in _REFERENCE below so the team can
tweak them without touching the UI code.
"""

import os
from datetime import datetime

import gradio as gr

from annotation import load_game, _build_transcript_html, _card_header_html, _turn_nav_html

_dir = os.path.dirname(os.path.abspath(__file__))

# A real gemini-flash Wordle game (3 turns, won). Lives outside games/ on
# purpose so it can never be picked up by game discovery / assignments.
TRAINING_GAME = os.path.join(_dir, "interactions", "interactions-20.json")

_Q1_MD = ("**Q1 — Prior Information Use**\n\nDid the AI correctly use "
          "information from earlier in the game?")
_Q2_MD = ("**Q2 — Sensible Next Step**\n\nDid this move make sense "
          "as a next step?")
_SCALE_Q1 = [("1\nNone", "1"), ("2\nPartial", "2"), ("3\nGood", "3"), ("4\nExcellent", "4")]
_SCALE_Q2 = [("1\nNonsensical", "1"), ("2\nPoor", "2"), ("3\nReasonable", "3"), ("4\nStrong", "4")]

# Per-turn reference ratings. `lower` is the teaching note: what kind of move
# WOULD have earned a lower score, so the bottom of the scale gets calibrated
# even on a decently-played game.
_REFERENCE = {
    0: {
        "q1": "4",
        "why_q1": "First guess — no earlier information existed yet, so there was "
                  "nothing to use or misuse. When there is nothing to get wrong, "
                  "don't punish.",
        "q2": "4",
        "why_q2": "\"arise\" is a textbook opener: three common vowels plus R and S "
                  "maximise what the first round of feedback can reveal.",
        "lower": "A rare word like \"fjord\" as an opener would be a 2 — it wastes "
                 "the first turn on letters unlikely to appear.",
    },
    1: {
        "q1": "4",
        "why_q1": "The feedback said A is out and R, I, S, E are in but misplaced. "
                  "\"siren\" drops the A, keeps all four yellow letters, and moves "
                  "every one of them to a NEW position — the feedback was used "
                  "completely and correctly.",
        "q2": "3",
        "why_q2": "Reasonable, not maximal: with R, I, S, E confirmed, several "
                  "arrangements were still alive (resin, risen, rinse…). \"siren\" "
                  "tests one of them plus a new letter N — solid, but nothing about "
                  "it narrows down WHICH arrangement is right faster than any other "
                  "permutation would.",
        "lower": "Re-guessing a word containing A, or putting a yellow letter back "
                 "in the same slot it was just flagged in, would drop Q1 to 1–2.",
    },
    2: {
        "q1": "4",
        "why_q1": "It combined everything: N locked at position 5, plus every "
                  "position each yellow letter had already been excluded from. "
                  "Work it through and \"resin\" is the only valid word left.",
        "q2": "4",
        "why_q2": "A clean, forced deduction played with confidence. (Its written "
                  "explanation mixes up position numbers a little — but the MOVE "
                  "used the information correctly, and Q1/Q2 rate the move, not "
                  "the prose.)",
        "lower": "Guessing another permutation that violated a known position "
                 "exclusion would be a 1 — the information was all there.",
    },
}


def _feedback_row(qlabel, ref, given, why):
    if given == ref:
        cls, verdict = "fb-good", "✓ match"
    elif given and abs(int(given) - int(ref)) == 1:
        cls, verdict = "fb-close", "≈ close"
    else:
        cls, verdict = "fb-miss", ("✗ no answer" if not given else "✗ off")
    yours = given or "—"
    return (
        f'<div class="fb-row {cls}"><span class="fb-verdict">{verdict}</span>'
        f'{qlabel}: reference <strong>{ref}</strong> · yours <strong>{yours}</strong></div>'
        f'<div class="fb-why">{why}</div>'
    )


def _check(*vals):
    """vals = (q1_t0, q2_t0, q1_t1, q2_t1, …). Returns per-turn feedback HTML,
    a summary line, and reveals the start button."""
    fb_updates, exact, close = [], 0, 0
    for i in range(len(_REFERENCE)):
        ref = _REFERENCE[i]
        q1, q2 = vals[2 * i], vals[2 * i + 1]
        for given, r in ((q1, ref["q1"]), (q2, ref["q2"])):
            if given == r:
                exact += 1
            elif given and abs(int(given) - int(r)) == 1:
                close += 1
        html = (
            '<div class="train-fb">'
            + _feedback_row("Q1 — Prior Information Use", ref["q1"], q1, ref["why_q1"])
            + _feedback_row("Q2 — Sensible Next Step", ref["q2"], q2, ref["why_q2"])
            + f'<div class="fb-note">💡 {ref["lower"]}</div>'
            + '</div>'
        )
        fb_updates.append(gr.update(value=html, visible=True))

    total = 2 * len(_REFERENCE)
    summary = (
        f"**You matched {exact} of {total} reference ratings exactly** "
        f"({close} more within one point). Differences of one point are normal — "
        f"read the explanations, then start the real session. Exact agreement "
        f"with the reference is not required."
    )
    return (*fb_updates, summary, gr.update(visible=True))


def _start_annotation():
    return (
        gr.update(visible=False),   # training_page
        gr.update(visible=True),    # annotation_page
        datetime.now().isoformat(),  # started_at — the real session timer starts now
    )


def build(welcome_page, training_page, annotation_page, started_at_state):
    g = load_game(TRAINING_GAME)

    with training_page:
        # ── TOP NAV
        with gr.Row(elem_classes=["annot-topnav"]):
            gr.HTML(
                '<div class="nav-left">'
                '<span class="game-name-tag">PRACTICE ROUND</span>'
                '<span class="game-id-tag">not recorded — calibration only</span>'
                '</div>'
            )
            gr.HTML(
                '<div class="annot-progress"><span class="prog-rated">'
                'Rate the 3 turns, then check yourself against the reference'
                '</span></div>',
                elem_classes=["nav-center"],
            )

        with gr.Group(elem_classes=["info-box"]):
            gr.Markdown(
                "**🎓 Practice before you start.** This is a real transcript of an AI "
                "playing Wordle. Rate each of its 3 turns with the two standard "
                "questions, then press **Check my ratings** to compare against "
                "reference ratings with explanations. Your practice answers are "
                "not saved."
            )

        with gr.Row(equal_height=False):
            # LEFT: transcript (train-specific scroll class + card ids so the
            # annotation page's JS never touches these nodes)
            with gr.Column(scale=3, elem_classes=["tx-col"]):
                gr.HTML(_build_transcript_html(g, current_idx=-1,
                                               scroll_cls="train-txscroll",
                                               id_prefix="ttc-"))

            # RIGHT: one practice turn at a time, same chip navigator as the
            # real annotation page — "turn-anno-card" is what app.py's JS
            # (panes()/chips()) targets, "train-card" only adds practice-only
            # styling on top of it.
            with gr.Column(scale=2, elem_id="train-col"):
                gr.HTML(_turn_nav_html(g))
                radios, feedbacks = [], []
                for i in range(g.n_turns):
                    with gr.Group(elem_classes=["train-card", "turn-anno-card"]):
                        gr.HTML(_card_header_html(g, i))
                        gr.Markdown(_Q1_MD)
                        q1 = gr.Radio(choices=_SCALE_Q1, show_label=False,
                                      elem_classes=["scale-radio"])
                        gr.Markdown(_Q2_MD)
                        q2 = gr.Radio(choices=_SCALE_Q2, show_label=False,
                                      elem_classes=["scale-radio"])
                        fb = gr.HTML("", visible=False)
                    radios += [q1, q2]
                    feedbacks.append(fb)

                summary = gr.Markdown("")
                with gr.Row():
                    skip_btn = gr.Button("Skip practice", variant="secondary")
                    check_btn = gr.Button("Check my ratings", variant="primary")
                    start_btn = gr.Button("Start real annotation →",
                                          variant="primary", visible=False)

        check_btn.click(
            fn=_check,
            inputs=radios,
            outputs=[*feedbacks, summary, start_btn],
        )
        for btn in (start_btn, skip_btn):
            btn.click(
                fn=_start_annotation,
                outputs=[training_page, annotation_page, started_at_state],
            )
