# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Running the app

```bash
python app.py
# or
gradio app.py
```

The app launches at `http://localhost:7860` by default.

## Architecture

This is a Gradio annotation tool for evaluating AI game transcripts (from [clembench](https://github.com/clembench/clembench)). Annotators rate how well an LLM played a game based on reasoning clarity and rule compliance.

**Two-screen navigation pattern:** `app.py` creates two `gr.Column` components — one visible, one hidden — and passes both into each screen's `build()` function. Screens switch by toggling `visible` on each column via button click callbacks.

```
app.py          — entry point; wires screen_welcome + screen_annotation, applies CSS
welcome.py      — intro screen with rating scale; "Start Annotation" button switches to annotation screen
annotation.py   — annotation screen (stub: "Coming soon"); "Back" button returns to welcome
interactions/   — JSON transcript files (clembench format) to be loaded and displayed for annotation
```

**Adding a new screen:** create a module with `build(screen_welcome, screen_annotation)` that wraps its UI in a `gr.Column(visible=False)`, adds navigation button callbacks, and returns the column. Register it in `app.py`.

**Interaction data format:** each file in `interactions/` is a clembench game record with `meta`, `player_models`, `players`, and `turns` keys. The `turns` array contains the GM↔Player conversation to display for annotation.
