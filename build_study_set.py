"""Build the curated study tree from games_final/.

Takes the full clembench export (4 models x 347 matched instances) and selects
the study set: 13 instances per game, the SAME 13 for every model, so every
model comparison is paired.

    python build_study_set.py                 # build games_study/ + manifest
    python build_study_set.py --dry-run       # report the selection, write nothing
    python build_study_set.py --seed 7        # different draw (default 20260822)

Output layout — one model level above the clembench structure:

    games_study/<model_id>/<game>/<experiment>/<instance>/interactions.json

annotation.py's discovery, game_slug and game_key are all depth-agnostic
(game_key reads the 4th-from-last path part), so this layout needs no code
change: the slug becomes "<model_id>__<game>__<experiment>__<instance>" and the
model falls out of it for free.

Point the app at the result with GAMES_DIR=games_study.

Selection rule: stratified by experiment. Each game's 13 are spread as evenly as
possible over its experiments, and any shortfall from an undersized experiment
is redistributed to the ones that have spare. This guarantees every experiment
is represented — it is the only rule under which Codenames' 13 experiments each
contribute exactly one, and under which its three single-instance experiments
(risk_high, risk_low, ambiguous) survive at all.
"""

import argparse
import csv
import glob
import json
import os
import random
import shutil
import sys
from collections import Counter, defaultdict

SOURCE = "games_final"
DEST = "games_study"
PRACTICE_DEST = "games_practice"
MANIFEST = "study_manifest.csv"
BATCH_PLAN = "batch_plan.json"
PER_GAME = 13
DEFAULT_SEED = 20260822

# Folder name in games_final -> short id used in slugs, URLs and export columns.
# The source names carry a colon and spaces, which are hostile to all three.
MODELS = {
    "qwen3.5-27b": "qwen27b",
    "LLP: Large Language Problems__qwen3.5-9b__llp-final": "llp9b",
    "DAIR__qwen3.5-2b__sft-dpo-v2": "dair2b",
    "CityUoL__qwen3.5-2b__Qwen-GuidePlay-2B-v1": "cityuol2b",
}

# The eight study games. clean_up was explicitly dropped; adventuregame, wordle
# and wordle_withclue are present in the source but out of scope (one of them
# supplies the practice round instead — see PRACTICE_GAME).
GAMES = [
    "referencegame", "guesswhat", "privateshared", "imagegame",
    "dond", "codenames", "ta_frozen_lake", "wordle-crazy_withclue",
]

# Practice transcript, shown once per annotator. Deliberately a game that is NOT
# in GAMES, so a practice transcript can never reappear inside a real batch.
PRACTICE_GAME = "wordle_withclue"
PRACTICE_MODEL = "qwen3.5-27b"


def discover(source):
    """{(game, experiment, instance): {model_folder: path}} for the study games."""
    found = defaultdict(dict)
    pattern = os.path.join(source, "*", "*", "*", "*", "*", "interactions.json")
    for path in glob.glob(pattern):
        parts = path.split(os.sep)
        domain, model, game, experiment, instance = parts[1], parts[2], parts[3], parts[4], parts[5]
        if model not in MODELS or game not in GAMES:
            continue
        found[(game, experiment, instance)][model] = (path, domain)
    return found


def matched_only(found):
    """Drop any instance not present for all four models — an unmatched one
    would silently break the paired comparison the whole study rests on."""
    full, dropped = {}, []
    for key, by_model in found.items():
        if len(by_model) == len(MODELS):
            full[key] = by_model
        else:
            dropped.append((key, sorted(by_model)))
    return full, dropped


def select_from_plan(matched, plan_path):
    """Take exactly the instances named in the curated batch plan.

    Replaces the earlier stratified random draw. The plan was built from the
    real transcript lengths and annotation burden, which a uniform draw cannot
    see: ReferenceGame is always 2 responses while PrivateShared reaches 30, so
    "13 per game" means very different sittings per game.
    """
    with open(plan_path) as fh:
        templates = json.load(fh)["templates"]
    chosen, plan, missing = [], {}, []
    for bid in sorted(templates):
        spec = templates[bid]
        game = spec["game"]
        for ent in spec["instances"]:
            experiment, instance = ent.split("/")
            key = (game, experiment, instance)
            if key not in matched:
                missing.append(f"{bid}: {game}/{ent}")
                continue
            chosen.append(key)
            plan.setdefault(game, Counter())[experiment] += 1
    if missing:
        raise SystemExit("batch plan references instances that are not matched "
                         "across all models:\n  " + "\n  ".join(missing[:20]))
    dupes = [k for k, n in Counter(chosen).items() if n > 1]
    if dupes:
        raise SystemExit(f"batch plan lists the same instance twice: {dupes[:5]}")
    return sorted(chosen), {g: dict(c) for g, c in plan.items()}


def _unused_stratified_select(matched, per_game, seed):
    """Superseded by select_from_plan; kept for reference only."""
    by_game = defaultdict(lambda: defaultdict(list))
    for (game, experiment, instance) in matched:
        by_game[game][experiment].append(instance)

    chosen, plan = [], {}
    for game in sorted(by_game):
        experiments = by_game[game]
        pool = sum(len(v) for v in experiments.values())
        if pool < per_game:
            raise SystemExit(
                f"{game}: only {pool} matched instances, need {per_game}")

        # Even base allocation, then hand out the remainder to the largest
        # experiments first so the redistribution below has room to work.
        names = sorted(experiments, key=lambda e: (-len(experiments[e]), e))
        base, rem = divmod(per_game, len(names))
        want = {e: base + (1 if i < rem else 0) for i, e in enumerate(names)}

        # An experiment smaller than its allocation gives its shortfall back;
        # push it onto whichever experiments still have unused instances.
        deficit = 0
        for e in names:
            if want[e] > len(experiments[e]):
                deficit += want[e] - len(experiments[e])
                want[e] = len(experiments[e])
        for e in names:
            if not deficit:
                break
            spare = len(experiments[e]) - want[e]
            take = min(spare, deficit)
            want[e] += take
            deficit -= take
        if deficit:
            raise SystemExit(f"{game}: could not place {deficit} instances")

        rng = random.Random(f"{seed}:{game}")
        for e in sorted(names):
            picks = sorted(experiments[e])
            rng.shuffle(picks)
            for inst in sorted(picks[:want[e]]):
                chosen.append((game, e, inst))
        plan[game] = {e: want[e] for e in sorted(names)}
    return sorted(chosen), plan


def build(matched, chosen, dest, dry_run):
    """Copy each selected instance for all four models into the study tree."""
    rows = []
    for (game, experiment, instance) in chosen:
        for model_folder, short in sorted(MODELS.items(), key=lambda kv: kv[1]):
            src, domain = matched[(game, experiment, instance)][model_folder]
            out_dir = os.path.join(dest, short, game, experiment, instance)
            if not dry_run:
                os.makedirs(out_dir, exist_ok=True)
                # Copy the whole instance folder: instance.json and scores.json
                # carry the target grids and outcomes the renderer may need.
                for f in os.listdir(os.path.dirname(src)):
                    if f.endswith((".json", ".html")):
                        shutil.copy2(os.path.join(os.path.dirname(src), f),
                                     os.path.join(out_dir, f))
            rows.append({
                "transcript_id": f"{short}__{game}__{experiment}__{instance}",
                "model_id": short,
                "model_folder": model_folder,
                "model_name": _model_name(src),
                "domain": domain,
                "game": game,
                "experiment": experiment,
                "instance": instance,
                "source_path": src,
            })
    return rows


def _model_name(path):
    """The model as clembench recorded it, kept for traceability.

    Nesting varies by clembench version: older exports put model_name directly
    on the player entry, these ones wrap it in a "model_spec" dict. Reading only
    the top level silently returned "" for every row.
    """
    try:
        with open(path) as fh:
            pm = json.load(fh).get("player_models") or {}
        first = next(iter(pm.values()), {})
        if not isinstance(first, dict):
            return ""
        spec = first.get("model_spec")
        if isinstance(spec, dict):
            first = {**first, **spec}
        return first.get("model_name") or first.get("model_id") or ""
    except Exception:
        return ""


def copy_practice(dest, dry_run):
    """One practice transcript, from a game deliberately outside the study set.

    Written to a SIBLING directory, never inside dest: _discover_games globs
    the whole tree, so anything under games_study/ becomes assignable, and the
    practice transcript turning up in a real batch is exactly what using an
    excluded game was meant to prevent.
    """
    hits = sorted(glob.glob(os.path.join(
        SOURCE, "*", PRACTICE_MODEL, PRACTICE_GAME, "*", "*", "interactions.json")))
    if not hits:
        print(f"  ! practice game {PRACTICE_GAME!r} not found — skipped")
        return None
    src = hits[0]
    parts = src.split(os.sep)
    out_dir = os.path.join(PRACTICE_DEST, PRACTICE_GAME, parts[4], parts[5])
    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)
        for f in os.listdir(os.path.dirname(src)):
            if f.endswith((".json", ".html")):
                shutil.copy2(os.path.join(os.path.dirname(src), f),
                             os.path.join(out_dir, f))
    return os.path.relpath(os.path.join(out_dir, "interactions.json"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--dest", default=DEST)
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--plan", default=BATCH_PLAN,
                    help="curated batch plan JSON (default: batch_plan.json)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.source):
        raise SystemExit(f"source tree not found: {args.source}")

    found = discover(args.source)
    matched, dropped = matched_only(found)
    if dropped:
        print(f"! {len(dropped)} instance(s) not present for all "
              f"{len(MODELS)} models — excluded:")
        for key, have in dropped[:5]:
            print(f"    {'/'.join(key)}  (only: {', '.join(have)})")

    chosen, plan = select_from_plan(matched, args.plan)

    print(f"\nselection from {args.plan}, "
          f"same instances for all {len(MODELS)} models:")
    for game in sorted(plan):
        spread = ", ".join(f"{e}:{n}" for e, n in plan[game].items() if n)
        print(f"  {game:24} {sum(plan[game].values()):>3}   {spread}")

    if not args.dry_run:
        for stale in (args.dest, PRACTICE_DEST):
            if stale and os.path.exists(stale):
                shutil.rmtree(stale)
    rows = build(matched, chosen, args.dest, args.dry_run)

    practice = copy_practice(args.dest, args.dry_run)

    if not args.dry_run:
        cols = ["transcript_id", "model_id", "model_folder", "model_name",
                "domain", "game", "experiment", "instance", "source_path"]
        with open(args.manifest, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)

    per_model = Counter(r["model_id"] for r in rows)
    per_game = Counter(r["game"] for r in rows)
    print(f"\ntranscripts: {len(rows)}  "
          f"({len(chosen)} instances x {len(MODELS)} models)")
    print("  per model:", dict(sorted(per_model.items())))
    print("  per game :", dict(sorted(per_game.items())))
    print(f"  domains  : {dict(Counter(r['domain'] for r in rows))}")
    if practice:
        print(f"  practice : {practice}")
    if args.dry_run:
        print("\n(dry run — nothing written)")
    else:
        print(f"\nwrote {args.dest}/ and {args.manifest}")
        print(f"run the app with:  GAMES_DIR={args.dest} python app.py")


if __name__ == "__main__":
    main()
