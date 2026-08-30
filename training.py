"""Interactive practice round shown before a playlist session starts. Rates a
short reference game, then reveals explained reference ratings to calibrate the
1-4 scales. Nothing here is persisted."""

import os
from datetime import datetime

import gradio as gr

import db
from annotation import (load_game, plain_label, _build_transcript_html,
                        _card_header_html, _turn_nav_html)

_dir = os.path.dirname(os.path.abspath(__file__))

# A real GuessWhat episode (target "theatre", guessed correctly on turn 5).
# Lives in games_practice/, OUTSIDE the study tree, so it can never be picked up
# by discovery or handed out in a batch — and its instance (Abs_Level_1/
# instance_00004) is deliberately not one of the 13 guesswhat instances the
# study uses, so no annotator ever meets it twice.
TRAINING_GAME = os.path.join(
    _dir, "games_practice", "guesswhat", "Abs_Level_1", "instance_00004",
    "interactions.json")

# Display name for the practice game, derived from the path so that swapping
# TRAINING_GAME cannot leave the on-screen text describing the previous one.
_PRACTICE_NAME = os.path.basename(
    os.path.dirname(os.path.dirname(os.path.dirname(TRAINING_GAME)))
).replace("_", " ").title().replace("Guesswhat", "GuessWhat")

# Only this role's turns are rated. GuessWhat has two seats, but the Answerer's
# forced yes/no carries no strategy worth judging (the real annotation page
# gives it no questions either) — rating "ANSWER: no" would teach the wrong
# thing. Its replies still show in the transcript as context.
PRACTICE_ROLE = "Guesser"


def _load_practice_game():
    """load_game, narrowed to the rated role.

    Filtering ai_ids as well as ai_turns keeps three things aligned that the
    page's JS assumes are 1:1 — transcript cards, nav chips and question cards —
    and turns the Answerer's messages into context lines rather than empty
    rateable cards.
    """
    g = load_game(TRAINING_GAME)
    keep = {s for s in g.ai_ids if g.role(s) == PRACTICE_ROLE}
    if keep and keep != set(g.ai_ids):
        g.ai_turns = [m for m in g.ai_turns if m.get("from") in keep]
        g.ai_ids = keep
        g.n_turns = len(g.ai_turns)
        g.multi_role = False
    return g

_Q1_MD = ("**Q1 — Prior Information Use**\n\nDid the AI correctly use "
          "information from earlier in the game?")
_Q2_MD = ("**Q2 — Sensible Next Step**\n\nDid this move make sense "
          "as a next step?")
_SCALE_Q1 = [("1\nNone", "1"), ("2\nPartial", "2"), ("3\nGood", "3"), ("4\nExcellent", "4")]
_SCALE_Q2 = [("1\nNonsensical", "1"), ("2\nPoor", "2"), ("3\nReasonable", "3"), ("4\nStrong", "4")]

# `lower` teaches what would have scored worse, calibrating the scale's bottom too.
#
# Reviewed and approved by the study team (Yurii Ilnytskyi, 2026-08-30) ahead
# of recruitment. This is the calibration standard every annotator is trained
# against, so it sets the study's working definition of "good questioning" —
# any change to these five ratings or their explanations needs the same review.
#
# The episode: target "theatre" from {charming, compassionate, genocide, gun,
# writing, theatre, overwhelmed, sorrow}. The Guesser eliminates the four
# emotion words, establishes it is a physical object, rules out the weapon, then
# confirms and guesses correctly on turn 5.
#
# Note this is a CLEAN run — nothing here scores below 3. The `lower` lines are
# doing all the work of teaching the bottom of the scale, which is a weakness of
# using a well-played episode for calibration.
_REFERENCE = {
    0: {
        "q1": "4",
        "why_q1": "First question — no earlier information existed yet, so there "
                  "was nothing to use or misuse. When there is nothing to get "
                  "wrong, don't punish.",
        "q2": "4",
        "why_q2": "\"Related to a feeling or emotion?\" splits the eight "
                  "candidates almost perfectly: four emotion words (charming, "
                  "compassionate, overwhelmed, sorrow) against four that aren't. "
                  "A near-even split is the most a single yes/no can buy.",
        "lower": "\"Is the word 'gun'?\" would be a 2 — it tests one candidate "
                 "instead of half of them, so a 'no' eliminates only one word.",
    },
    1: {
        "q1": "4",
        "why_q1": "The 'no' ruled out all four emotion words, leaving genocide, "
                  "gun, writing and theatre. Asking about physical objects moves "
                  "to a genuinely new axis rather than re-testing what was just "
                  "settled.",
        "q2": "3",
        "why_q2": "Sensible and it does divide the remainder — but \"physical "
                  "object\" is fuzzy for 'writing' and arguably for 'theatre' "
                  "(the art form versus the building), so the answer is less "
                  "decisive than the question looks.",
        "lower": "Asking \"is it an emotion?\" again would be a 1 — the previous "
                 "answer already settled that, so the turn buys nothing.",
    },
    2: {
        "q1": "4",
        "why_q1": "Correctly carried the 'yes' forward: among the physical "
                  "candidates, 'gun' is the one worth separating out.",
        "q2": "3",
        "why_q2": "Reasonable with only two or three candidates left, but it "
                  "tests a single word rather than splitting the remainder. With "
                  "more candidates alive this would have been wasteful.",
        "lower": "\"Is it a weapon?\" asked as the FIRST question would be a 2 — "
                 "same question, far worse timing, because it eliminates one of "
                 "eight instead of one of three.",
    },
    3: {
        "q1": "4",
        "why_q1": "Used every previous answer — not an emotion, is a physical "
                  "object, not a weapon — to arrive at the one remaining "
                  "category worth testing.",
        "q2": "4",
        "why_q2": "\"A place where performances are held\" is effectively a "
                  "definition of the target. Confirming before committing is "
                  "sound play when a turn is still available.",
        "lower": "Guessing 'gun' here would be a 1 — the previous answer "
                 "explicitly ruled it out.",
    },
    4: {
        "q1": "4",
        "why_q1": "The guess follows directly from the confirmed 'yes': every "
                  "answer in the episode points at this word and no other.",
        "q2": "4",
        "why_q2": "Correct word, and committed at the right moment — the "
                  "previous answer left nothing further to narrow.",
        "lower": "Asking another question instead of guessing here would be a 2 "
                 "— the answer was already determined, so it burns a turn.",
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


def _start_annotation(annotator_id):
    # Recorded on both buttons: someone who deliberately skipped shouldn't be
    # shown the practice round again next session. This flag — not the session
    # index — is what stops a page reload replaying it (welcome._start).
    db.record_practice(annotator_id)
    return (
        gr.update(visible=False),   # training_page
        gr.update(visible=True),    # annotation_page
        datetime.now().isoformat(),  # started_at — the real session timer starts now
    )


def build(welcome_page, training_page, annotation_page, started_at_state,
          annotator_state):
    g = _load_practice_game()

    with training_page:
        # This screen had no heading at all; it's also the focus target the
        # a11y module moves to when the screen becomes visible.
        gr.HTML('<h1 class="a11y-sr-only" tabindex="-1">Practice round</h1>')
        with gr.Row(elem_classes=["annot-topnav"]):
            gr.HTML(
                '<div class="nav-left">'
                '<span class="game-name-tag">PRACTICE ROUND</span>'
                '<span class="game-id-tag">not recorded — calibration only</span>'
                '</div>'
            )
            gr.HTML(
                '<div class="annot-progress"><span class="prog-rated">'
                f'Rate the {len(_REFERENCE)} turns, then check yourself '
                'against the reference'
                '</span></div>',
                elem_classes=["nav-center"],
            )

        with gr.Group(elem_classes=["info-box"]):
            # Game name and turn count are read from the practice episode, not
            # written out: both were left saying "Wordle" and "3 turns" when the
            # practice round was swapped to GuessWhat, and every new participant
            # was told the wrong thing on the first screen they see.
            gr.Markdown(
                f"**🎓 Practice before you start.** This is a real transcript of "
                f"an AI playing {_PRACTICE_NAME}. Rate each of its "
                f"{len(_REFERENCE)} turns with the two standard questions, then "
                f"press **Check my ratings** to compare against reference "
                f"ratings with explanations. Your practice answers are not saved."
            )

        with gr.Row(equal_height=False):
            # train-specific scroll class + card ids keep the annotation
            # page's JS from touching these nodes.
            with gr.Column(scale=3, elem_classes=["tx-col"]):
                gr.HTML(_build_transcript_html(g, current_idx=-1,
                                               scroll_cls="train-txscroll",
                                               id_prefix="ttc-"))

            # "turn-anno-card" is what app.py's JS (panes()/chips()) targets;
            # "train-card" only adds practice-only styling on top of it.
            with gr.Column(scale=2, elem_id="train-col"):
                gr.HTML(_turn_nav_html(g))
                radios, feedbacks = [], []
                for i in range(g.n_turns):
                    with gr.Group(elem_classes=["train-card", "turn-anno-card"]):
                        gr.HTML(_card_header_html(g, i))
                        _t = f"Practice turn {i + 1} — "
                        gr.Markdown(_Q1_MD)
                        q1 = gr.Radio(choices=_SCALE_Q1,
                                      label=_t + plain_label(_Q1_MD),
                                      show_label=False,
                                      elem_classes=["scale-radio"])
                        gr.Markdown(_Q2_MD)
                        q2 = gr.Radio(choices=_SCALE_Q2,
                                      label=_t + plain_label(_Q2_MD),
                                      show_label=False,
                                      elem_classes=["scale-radio"])
                        fb = gr.HTML("", visible=False)
                    radios += [q1, q2]
                    feedbacks.append(fb)

                summary = gr.Markdown("", elem_id="train-summary")
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
                inputs=[annotator_state],
                outputs=[training_page, annotation_page, started_at_state],
            )
