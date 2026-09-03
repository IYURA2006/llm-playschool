"""Expand the curated batch plan into concrete per-model batches.

    python build_batches.py            # write batches.csv
    python build_batches.py --dry-run

One batch = ONE game and ONE model. Each of the 26 templates in batch_plan.json
is repeated for all four models, giving 104 batches; each is completed
independently by 3 annotators, so 312 participant sittings over 416 transcripts
and 1,248 transcript judgements.

Batch sizes vary (2 to 7) so that a sitting is about 20 minutes of real work.
Sizing is based on modelled annotator time, NOT on turn count. About 45% of the
effort on a transcript is fixed - reading the game rules and answering the
end-of-game verdict - so how many transcripts a sitting holds matters more than
how long each one is. Codenames is the heaviest at roughly 8 minutes per
transcript and takes 2-3 per sitting; ReferenceGame is the lightest at under 3
and takes 6-7.

`template_id` is the column that makes the cross-model rule enforceable. Every
model's version of a template contains the SAME instances, so an annotator who
takes REF-1 for one model must never be offered REF-1 for another — they would
be re-rating transcripts of games they have already seen, and their judgements
would not be independent. Assignment excludes on template_id, not batch_id.
"""

import argparse
import csv
import json
import os

MANIFEST = "study_manifest.csv"
PLAN = "batch_plan.json"
OUT = "batches.csv"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--plan", default=PLAN)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.manifest, newline="") as fh:
        manifest = {r["transcript_id"]: r for r in csv.DictReader(fh)}
    with open(args.plan) as fh:
        templates = json.load(fh)["batches"]

    rows, problems = [], []
    for batch_id in sorted(templates):
        spec = templates[batch_id]
        game = spec["game"]
        seen_instances = set()
        for pos, ent in enumerate(spec["transcripts"], 1):
            model, experiment, instance = ent.split("/")
            # Two models of one instance in a sitting would show the annotator
            # the same game state twice.
            if (experiment, instance) in seen_instances:
                problems.append(f"{batch_id}: {experiment}/{instance} appears twice")
            seen_instances.add((experiment, instance))
            transcript_id = f"{model}__{game}__{experiment}__{instance}"
            r = manifest.get(transcript_id)
            if r is None:
                problems.append(f"{batch_id}: {transcript_id} not in manifest")
                continue
            rows.append({
                "batch_id": batch_id,
                # Kept, and equal to batch_id. A batch no longer instantiates a
                # shared instance set, so this column groups rows for the export
                # and nothing else; assignment excludes by instance instead.
                "template_id": batch_id,
                "position": pos,
                "transcript_id": transcript_id,
                "model_id": model,
                "game": game,
                "experiment": experiment,
                "instance": instance,
                "domain": r["domain"],
            })
    if problems:
        raise SystemExit("plan does not match the manifest:\n  "
                         + "\n  ".join(problems[:20]))

    sizes = {}
    for r in rows:
        sizes[r["batch_id"]] = sizes.get(r["batch_id"], 0) + 1

    print(f"{len(sizes)} batches   transcripts {len(rows)}   "
          f"sittings {len(sizes) * 3}   judgements {len(rows) * 3}")
    per_game = {}
    for bid, spec in templates.items():
        per_game.setdefault(spec["game"], []).append(len(spec["transcripts"]))
    print("\nper game:")
    for game in sorted(per_game):
        n = per_game[game]
        print(f"  {game:24} {len(n):>3} batches, {min(n)}-{max(n)} transcripts each")

    if args.dry_run:
        print("\n(dry run — nothing written)")
        return
    cols = ["batch_id", "template_id", "position", "transcript_id",
            "model_id", "game", "experiment", "instance", "domain"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
