import json
import os
from datetime import datetime

import gradio as gr

import db
import annotation

_dir = os.path.dirname(os.path.abspath(__file__))

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


def _load_assignments():
    try:
        with open(os.path.join(_dir, "assignments.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _progress_note(name, day):
    """Live note under the name/day form: how much of the session is done."""
    if not name or not day:
        return ""
    items = _load_assignments().get(name, {}).get(day) or []
    if not items:
        return f"⚠️ No games assigned for **{name}** on Day {day} — tell the study coordinator."
    done = db.completed_pairs(name)
    n = sum((it["game"], it["condition"]) in done for it in items)
    if n == 0:
        return (f"**Day {day}:** {len(items)} games ahead. You'll get a short "
                f"practice round first — then your games load one after another.")
    if n >= len(items):
        return f"🎉 You've already completed **all {len(items)} games** for Day {day}."
    return (f"▶️ **{n} of {len(items)} games done** for Day {day} — "
            f"Start will resume where you left off (practice round is skipped).")


def _start(err, playlist, block, name, day, annotator):
    """Route the Start click.

    Priority: filled name/day form → URL playlist link → legacy single-game
    link. The form outranks a playlist loaded earlier in the session (via URL
    or a previous Start): once the loaded day is finished, picking the other
    day in the form must actually switch to it — with playlist-first routing
    the stale playlist shadowed the form forever. BOTH playlist paths resume
    at the first game without a submitted verdict — they used to diverge, and
    reopening a ?annotator=x&day=n link silently restarted at game 1 and
    overwrote completed work via the DB upsert.

    Pages that stay hidden get a no-op gr.update(), NOT visible=False: Gradio 6
    lazily mounts hidden columns, and sending visible=False to a never-mounted
    column breaks every later visible=True on it (the page would stay blank).
    States that shouldn't change get gr.skip().

    The final element of every return is clearing_state=False — the Start
    click's first chained event set it True (blanking the annotation page so
    no widget values survive from a previous game, see app.py); leaving it
    True on any branch would strand a permanently blank page.
    """
    noop = gr.update()

    def stay(note):
        return (noop, noop, noop, gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                gr.skip(), gr.skip(), gr.skip(), gr.skip(), note, False)

    # A URL error only blocks Start while the form is unfilled — the welcome
    # form must stay usable under an error banner (e.g. the "all done for this
    # day's link" case, where the fix is simply picking the other day below).
    if err and (not name or not day):
        return stay("")
    # `now` doubles as the session clock (stamped once per Start click — the
    # practice round counts toward the session) and the first game's clock
    # (training re-stamps started_at when real annotation begins).
    now = datetime.now().isoformat()

    # ── Name/day form path — the user's explicit choice, checked first ──
    if name and day:
        items = _load_assignments().get(name, {}).get(day) or []
        if not items:
            return stay(f"⚠️ No games assigned for **{name}** on Day {day} — "
                        f"tell the study coordinator.")
        done = db.completed_pairs(name)
        idx = next((i for i, it in enumerate(items)
                    if (it["game"], it["condition"]) not in done), None)
        if idx is None:
            return stay(f"🎉 You've already completed all {len(items)} games for "
                        f"Day {day}. Nothing left to do — thank you!")
        item = items[idx]
        path = annotation.slug_to_path(item["game"])
        if not path:
            return stay(f"⚠️ Your assignment references an unknown game "
                        f"({item['game']!r}) — tell the study coordinator.")

        if idx == 0:   # fresh session → practice round first
            pages = (gr.update(visible=False), noop, gr.update(visible=True))
        else:          # resuming → skip training, land on the next unfinished game
            pages = (gr.update(visible=False), gr.update(visible=True), noop)
        return (*pages, now, now, name, item["condition"], path, items, idx, day,
                "", False)

    if playlist:  # URL pilot link (?annotator=x&day=n), form untouched
        # Recompute resume position at click time: the completed set may have
        # grown since page load (or since a Back → Start round-trip).
        done = db.completed_pairs(annotator)
        idx = next((i for i, it in enumerate(playlist)
                    if (it["game"], it["condition"]) not in done), None)
        if idx is None:
            return stay(f"🎉 You've already completed all {len(playlist)} games "
                        f"for this session. Nothing left to do — thank you!")
        item = playlist[idx]
        path = annotation.slug_to_path(item["game"])
        if not path:
            return stay(f"⚠️ Your assignment references an unknown game "
                        f"({item['game']!r}) — tell the study coordinator.")
        if idx == 0:   # nothing finished yet → practice round first
            pages = (gr.update(visible=False), noop, gr.update(visible=True))
        else:          # resuming → skip training, land on the next unfinished game
            pages = (gr.update(visible=False), gr.update(visible=True), noop)
        return (*pages, now, now, gr.skip(), item["condition"], path,
                gr.skip(), idx, gr.skip(), "", False)

    if block:     # legacy single-game link — straight to annotation
        return (gr.update(visible=False), gr.update(visible=True), noop, now, now,
                gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                "", False)

    return stay("⚠️ Pick your **name** and **session day** above, then press Start.")


def build(welcome_page, annotation_page, training_page, error_state,
          playlist_state, started_at_state, session_started_at_state,
          annotator_state, block_state, game_state, playlist_idx_state,
          session_day_state, clearing_state):

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

            # Malformed-link banner — only visible when app.load's session-param
            # parsing (app.py) found a missing/invalid annotator, block, or game.
            @gr.render(inputs=[error_state])
            def _error_banner(msg):
                if msg:
                    gr.Markdown(f"**{msg}**", elem_classes=["info-box"])

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

                        with gr.Column(scale=0, min_width=46, elem_classes=["ovr-num"]):
                            gr.HTML(
                                f'<div class="rating-badge" '
                                f'style="background:{color}22;border-color:{color};'
                                f'color:{color};">{n}</div>'
                            )

                        with gr.Column(scale=0, min_width=130, elem_classes=["ovr-label"]):
                            gr.Markdown(f"**{label}**")


                        with gr.Column(scale=1, elem_classes=["ovr-desc"]):
                            gr.Markdown(desc)

            # WHO ARE YOU — the public entry point: pick a name + day, the app
            # loads that playlist and resumes at the first unfinished game.
            names = sorted(_load_assignments().keys())
            with gr.Group(elem_classes=["question-card"]):
                gr.Markdown("### 🙋 Start your session")
                with gr.Row():
                    name_dd = gr.Dropdown(
                        choices=names, value=None, label="Your name",
                        elem_classes=["game-select"],
                    )
                    day_radio = gr.Radio(
                        choices=[("Day 1", "1"), ("Day 2", "2")],
                        value=None, label="Session",
                    )
                form_note = gr.Markdown("")

            for widget in (name_dd, day_radio):
                widget.change(_progress_note, inputs=[name_dd, day_radio],
                              outputs=[form_note])

            # NEXT PAGE — two chained events: the first blanks the annotation
            # page (clearing_state=True → empty @gr.render) so no widget
            # values can survive from a previously annotated game (e.g.
            # finish day 1 → Back → pick day 2 → Start); _start then sets the
            # game states AND clearing_state=False in one event, mounting the
            # page fresh exactly once. See app.py's clearing_state comment.
            gr.Button(
                "Start Annotation →", variant="primary", size="lg",
                elem_classes=["start-btn"],
            ).click(
                lambda: True,
                outputs=[clearing_state],
            ).then(
                _start,
                inputs=[error_state, playlist_state, block_state, name_dd,
                        day_radio, annotator_state],
                outputs=[welcome_page, annotation_page, training_page,
                         started_at_state, session_started_at_state,
                         annotator_state, block_state,
                         game_state, playlist_state, playlist_idx_state,
                         session_day_state, form_note, clearing_state],
            )


            gr.Markdown(
                "By continuing you confirm you are a registered Prolific participant "
                "and have read these instructions",
                elem_classes=["welcome-foot"],
            )
