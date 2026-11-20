#!/usr/bin/env python3
"""Build the pilot: a small set of transcripts the main study does NOT use.

    python build_pilot_set.py              # write games_pilot/ + the two CSVs
    python build_pilot_set.py --dry-run    # report the selection, write nothing
    python build_pilot_set.py --batches-per-game 2

The pilot exists to run the whole pipeline - Prolific link, consent, practice,
rating, verdict, completion, the VM and its database - on real annotators
before the 416-transcript study starts, without spending any of the study's
own transcripts on it.

Three games, one sitting each by default, sized like the main study's sittings:

    referencegame   8 transcripts
    dond            4
    codenames       3

Each sitting is completed by 2 annotators (study_profile.PILOT.coverage_target),
so the default pilot is 15 transcripts, 6 sittings, 30 judgements.

Selection rules, all enforced, none left to luck:

  * never an instance in study_manifest.csv, under any model - a pilot
    annotator who later joins the study must not have seen its game states;
  * never the practice instance;
  * only instances present for all four models, as in the study;
  * every transcript in a sitting is a different instance, and models rotate
    so a sitting mixes them the way the study's batches do;
  * turn counts stay inside the range the study has for that game, so the
    pilot's timings say something about the study's.

Serve it with GAMES_DIR=games_pilot (see study_profile.py). Deterministic for
a given --seed.
"""

import argparse
import csv
import os
import random
import shutil
from collections import defaultdict

# Both must be set before annotation is imported: it pulls in db, which
# connects at import, and study_profile, which validates GAMES_DIR.
os.environ.setdefault("SKIP_DB_INIT", "1")

import build_study_set as bss            # noqa: E402
import study_profile                     # noqa: E402

SOURCE = bss.SOURCE
DEST = study_profile.PILOT.games_dir
MANIFEST = study_profile.PILOT.manifest
BATCHES = study_profile.PILOT.batches
STUDY_MANIFEST = study_profile.MAIN.manifest
DEFAULT_SEED = 20261118

# game -> (batch id stem, transcripts per sitting). Sizes follow the stopwatch
# timings behind batch_plan.json: about 20 minutes of work each.
PLAN = {
    "referencegame": ("REF", 8),
    "dond": ("DOND", 4),
    "codenames": ("CODE", 3),
}
MODEL_IDS = sorted(bss.MODELS.values())
_FOLDER_OF = {short: folder for folder, short in bss.MODELS.items()}


def _turns(path):
    import annotation
    return annotation.load_game(path).n_turns


def study_instances():
    with open(STUDY_MANIFEST, newline="") as fh:
        rows = list(csv.DictReader(fh))
    used = {(r["game"], r["experiment"], r["instance"]) for r in rows}
    return used, rows


def study_turn_range(rows):
    """{game: (min, max)} rated turns across the study's own transcripts."""
    seen = defaultdict(list)
    for r in rows:
        if r["game"] in PLAN and os.path.exists(r["source_path"]):
            seen[r["game"]].append(_turns(r["source_path"]))
    return {g: (min(v), max(v)) for g, v in seen.items()}


def select(matched, used, turn_range, batches_per_game, seed):
    """[(batch_id, game, position, model_id, experiment, instance, path, domain)]"""
    out = []
    for game, (stem, size) in PLAN.items():
        pool = sorted(k for k in matched
                      if k[0] == game and k not in used and k != bss.PRACTICE)
        rng = random.Random(f"{seed}:{game}")
        rng.shuffle(pool)
        # Round-robin over experiments, so one sitting is not eight of a kind.
        by_exp = defaultdict(list)
        for k in pool:
            by_exp[k[1]].append(k)
        order, exps = [], sorted(by_exp)
        rng.shuffle(exps)
        while any(by_exp.values()):
            for e in exps:
                if by_exp[e]:
                    order.append(by_exp[e].pop())

        lo, hi = turn_range.get(game, (1, 10 ** 6))
        need = size * batches_per_game
        picked, slot = [], 0
        for key in order:
            if len(picked) == need:
                break
            # Models rotate across the whole game, not per batch, so a batch of
            # 3 does not always leave out the same fourth model.
            model = MODEL_IDS[slot % len(MODEL_IDS)]
            path, domain = matched[key][_FOLDER_OF[model]]
            if not lo <= _turns(path) <= hi:
                continue
            picked.append((model, key, path, domain))
            slot += 1
        if len(picked) < need:
            raise SystemExit(
                f"{game}: only {len(picked)} unused instances fit the study's "
                f"{lo}-{hi} turn range, need {need}")
        for b in range(batches_per_game):
            batch_id = f"{study_profile.PILOT_PREFIX}{stem}-{b + 1:02d}"
            for pos, (model, key, path, domain) in enumerate(
                    picked[b * size:(b + 1) * size], 1):
                out.append((batch_id, game, pos, model, key[1], key[2], path, domain))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--batches-per-game", type=int, default=1)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.source):
        raise SystemExit(f"source tree not found: {args.source}")
    if args.batches_per_game != study_profile.PILOT.max_batches // len(PLAN):
        print(f"! note: study_profile.PILOT.max_batches is "
              f"{study_profile.PILOT.max_batches}; with "
              f"{args.batches_per_game * len(PLAN)} pilot batches, change it to "
              f"match or assignment.preflight will object.\n")

    matched, _ = bss.matched_only(bss.discover(args.source))
    used, study_rows = study_instances()
    chosen = select(matched, used, study_turn_range(study_rows),
                    args.batches_per_game, args.seed)

    # The one property the pilot must never lose. Checked on the result rather
    # than trusted from the filter above.
    clash = [c for c in chosen if (c[1], c[4], c[5]) in used]
    if clash:
        raise SystemExit(f"selection overlaps the study: {clash[:3]}")
    keys = [(c[1], c[4], c[5]) for c in chosen]
    if len(set(keys)) != len(keys):
        raise SystemExit("an instance was selected twice")

    manifest_rows, batch_rows = [], []
    for batch_id, game, pos, model, experiment, instance, path, domain in chosen:
        tid = f"{model}__{game}__{experiment}__{instance}"
        manifest_rows.append({
            "transcript_id": tid, "model_id": model,
            "model_folder": _FOLDER_OF[model],
            "model_name": bss._model_name(path), "domain": domain,
            "game": game, "experiment": experiment, "instance": instance,
            "source_path": path,
        })
        batch_rows.append({
            "batch_id": batch_id, "template_id": batch_id, "position": pos,
            "transcript_id": tid, "model_id": model, "game": game,
            "experiment": experiment, "instance": instance, "domain": domain,
        })

    for batch_id in sorted({c[0] for c in chosen}):
        members = [c for c in chosen if c[0] == batch_id]
        print(f"{batch_id}  ({len(members)} transcripts)")
        for _, game, pos, model, experiment, instance, path, _d in members:
            print(f"   {pos}. {model:10} {experiment:22} {instance}  "
                  f"{_turns(path):>2} turns")
    n_b = len({c[0] for c in chosen})
    cov = study_profile.PILOT.coverage_target
    print(f"\n{len(chosen)} transcripts, {n_b} batches, {cov} annotators each: "
          f"{n_b * cov} sittings, {len(chosen) * cov} judgements")

    if args.dry_run:
        print("\n(dry run — nothing written)")
        return

    if os.path.exists(DEST):
        shutil.rmtree(DEST)
    for row in manifest_rows:
        out_dir = os.path.join(DEST, row["model_id"], row["game"],
                               row["experiment"], row["instance"])
        os.makedirs(out_dir, exist_ok=True)
        for f in bss.INSTANCE_FILES:
            src = os.path.join(os.path.dirname(row["source_path"]), f)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(out_dir, f))
    for path, rows in ((MANIFEST, manifest_rows), (BATCHES, batch_rows)):
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print(f"\nwrote {DEST}/, {MANIFEST} and {BATCHES}")
    print(f"serve it with:  GAMES_DIR={DEST} python app.py")


if __name__ == "__main__":
    main()
