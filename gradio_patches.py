"""One fix to Gradio itself, applied at import. Import this before building the app.

Gradio runs @gr.render functions in worker threads, and one state change in this
app re-runs several of them at once (the transcript, the verdict header, the
game-specific questions). Each run adds to and then walks the same per-session
registries, which are plain dicts, and Gradio walks them again on the event
loop when it builds the reply. Two that overlap die with

    RuntimeError: dictionary changed size during iteration
        gradio/renderable.py  Renderable.apply -> attach_load_events
        gradio/blocks.py      process_api -> BlocksConfig.get_config

The page that render was drawing then never arrives. For a participant that is
"I pressed Save / Submit and nothing happened", and only a reload gets them
out. It needs real concurrency to show: one person clicking through alone never
hits it; six at once hit it about once in every thirty to fifty game switches.

The fix is to run renders on the event loop instead of in threads. Gradio
awaits a coroutine function in place rather than handing it to the thread pool,
so making the render entry point a coroutine means no render can overlap
another render, or the loop's own walk of the same dicts. Nothing is locked.

The cost is that a render holds the loop while it runs. They take a few
milliseconds to a few tens (set RENDER_TIMING=1 to log them), and happen a
handful of times per transcript per participant.

Two alternatives were tried and measured first. gr.render's concurrency_id
serialises renders globally through the queue, and the waiting makes Gradio
coalesce the "blank the page" render with the one after it - which is the step
that stops answers carrying over to the next game. A per-session lock around
the render fixed the first traceback above and not the second, which happens
outside the render on the loop.

requirements.txt pins gradio, and this checks that what it patches still
exists, so an upgrade that moves it fails here, loudly, instead of silently
dropping the fix.
"""

import inspect
import os
import time

from gradio import renderable

_orig_apply = getattr(renderable.Renderable, "apply", None)
if _orig_apply is None:
    raise RuntimeError(
        "gradio_patches: gradio.renderable.Renderable.apply is gone - Gradio "
        "was upgraded. Re-check whether concurrent renders are safe now, then "
        "update or remove this module.")

_TIMING = os.environ.get("RENDER_TIMING") == "1"

if not inspect.iscoroutinefunction(_orig_apply):

    async def _apply_on_loop(self, *args, **kwargs):
        if not _TIMING:
            return _orig_apply(self, *args, **kwargs)
        t0 = time.perf_counter()
        try:
            return _orig_apply(self, *args, **kwargs)
        finally:
            print(f"render {getattr(self.fn, '__name__', '?')}: "
                  f"{(time.perf_counter() - t0) * 1000:.0f} ms", flush=True)

    renderable.Renderable.apply = _apply_on_loop
