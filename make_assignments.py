"""Generate assignments.json + per-person links for the internal A/B pilot.

Design (see RUNBOOK.md for the full protocol):
- Two fixed groups. Day 1: Group A rates every Day-1 game with the GENERAL
  (universal) questions only, Group B with the SPECIFIC (hybrid/bespoke)
  questions only. Day 2: conditions flip (A → specific, B → general) on 2–3
  transcripts of ONE game nobody saw on Day 1.
- Day 1 covers 4 different task types, one transcript each, every transcript
  containing a known, confirmed bug — the outcome measure is whether each
  condition catches it.
- Everyone gets the same playlist in the same order. Day-2 pairs (two people,
  same condition, same transcripts, compare notes) are organised offline —
  they need no representation here.

Edit ANNOTATORS_GROUP_A / ANNOTATORS_GROUP_B / DAY1_GAMES / DAY2_GAMES /
BASE_URL below, then run:
    python make_assignments.py
"""

import glob
import json
import os

_dir = os.path.dirname(os.path.abspath(__file__))

# ── EDIT ME ────────────────────────────────────────────────────────────────
# Group A: Day 1 general (universal) → Day 2 specific (hybrid).
# Group B: Day 1 specific (hybrid)  → Day 2 general (universal).
ANNOTATORS_GROUP_A = ["Alessandro", "Sherzod", "Filippo"]
ANNOTATORS_GROUP_B = ["Sabrina", "Davi", "Dimitrije"]

# The pilot Space URL (or http://localhost:7860/ for a local dry run).
BASE_URL = "http://localhost:7860/"

# Day 1 — 4 different task types, ONE transcript each, done one game at a time.
# IMPORTANT: each slug must point at the transcript with the CONFIRMED bug
# (see RUNBOOK.md's bug table); swap instances here if the bug lives elsewhere.
DAY1_GAMES = [
    # Negotiation — secret proposal doesn't match the verbal agreement
    "dond__coop_en__instance_00000",
    # Agreement/coordination — both players declared victory while far apart.
    # Shortest clean_up available (12 AI turns); replace with a trimmed
    # 8–10-turn snippet if one gets added to games/.
    "clean_up__0_easy_3obj_en__instance_00000",
    # Map/world model — loops forever, never realises it's mapped everything
    "textmapworld_graphreasoning__medium__instance_00010",
    # Communication — same clue repeated 3 times, unflagged
    # (taboo low_en is deliberately absent everywhere — it's the practice game)
    "taboo__medium_en__instance_00000",
]

# Day 2 — 2–3 transcripts of ONE game that nobody saw on Day 1.
# Default: Imagegame (vivid confirmed bug: grid silently failed to update).
# NOTE: only 2 imagegame transcripts exist in games/ right now and
# compact_grids has a single AI turn — pull 1–2 more episodes from
# clembench-runs (ideally the grid-freeze one) before Day 2, or fall back to
# taboo (medium_en + high_en are the two non-practice transcripts).
DAY2_GAMES = [
    "imagegame__random_grids__instance_00000",
    "imagegame__compact_grids__instance_00000",
]
# ───────────────────────────────────────────────────────────────────────────

ANNOTATORS = ANNOTATORS_GROUP_A + ANNOTATORS_GROUP_B

# (day, group) → condition. "universal" = general questions only;
# "hybrid" = universal core swapped/extended with the game's bespoke set.
CONDITIONS = {
    ("1", "A"): "universal",
    ("1", "B"): "hybrid",
    ("2", "A"): "hybrid",
    ("2", "B"): "universal",
}

DAY_GAMES = {"1": DAY1_GAMES, "2": DAY2_GAMES}


def _discovered_slugs():
    """Same slug derivation as annotation.game_slug, without importing the app
    (importing annotation pulls in db, which hits the HF backup on import)."""
    slugs = set()
    for path in glob.glob(os.path.join(_dir, "games", "**", "interactions.json"),
                          recursive=True):
        rel = os.path.relpath(path, os.path.join(_dir, "games"))
        slugs.add(rel.replace(os.sep, "__").replace("interactions.json", "").strip("_"))
    return slugs


def main():
    known = _discovered_slugs()
    for day, games in DAY_GAMES.items():
        for slug in games:
            if slug not in known:
                raise SystemExit(f"Unknown game slug in day {day}: {slug}")
    day1_types = {slug.split("__", 1)[0] for slug in DAY1_GAMES}
    day2_types = {slug.split("__", 1)[0] for slug in DAY2_GAMES}
    if day1_types & day2_types:
        raise SystemExit(f"Day 2 game type(s) already used on Day 1: "
                         f"{sorted(day1_types & day2_types)}")

    assignments = {}
    for group, names in (("A", ANNOTATORS_GROUP_A), ("B", ANNOTATORS_GROUP_B)):
        for name in names:
            assignments[name] = {
                day: [{"game": slug, "condition": CONDITIONS[(day, group)]}
                      for slug in DAY_GAMES[day]]
                for day in sorted(DAY_GAMES)
            }

    out = os.path.join(_dir, "assignments.json")
    with open(out, "w") as f:
        json.dump(assignments, f, indent=2)
    print(f"Wrote {out}\n")

    for group, names in (("A", ANNOTATORS_GROUP_A), ("B", ANNOTATORS_GROUP_B)):
        d1 = CONDITIONS[("1", group)]
        d2 = CONDITIONS[("2", group)]
        print(f"── Group {group}: day 1 = {d1}, day 2 = {d2} " + "─" * 20)
        for name in names:
            print(f"  {name:16s} {BASE_URL}?annotator={name}&day=1   "
                  f"{BASE_URL}?annotator={name}&day=2")
    print("\nuniversal = general questions only; hybrid = game-specific set.")
    print(f"Day 1: {len(DAY1_GAMES)} games × 1 transcript, both groups, "
          f"one game at a time.")
    print(f"Day 2: {len(DAY2_GAMES)} transcripts of one game, conditions "
          f"flipped, worked in pairs (pairing is organised offline).")


if __name__ == "__main__":
    main()
