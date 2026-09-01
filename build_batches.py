"""Expand the curated batch plan into concrete per-model batches.

    python build_batches.py            # write batches.csv
    python build_batches.py --dry-run

One batch = ONE game and ONE model. Each of the 13 templates in batch_plan.json
is repeated for all four models, giving 52 batches; each is completed
independently by 3 annotators, so 156 participant sittings over 416 transcripts
and 1,248 transcript judgements.

Batch sizes vary (13, 7, 6, 5, 4) so that a sitting is about 20 minutes.
Instances are balanced on rateable turns, because a turn is one set of
questions: ReferenceGame is always two turns, so all 13 fit one sitting, while
Codenames averages 12.6 and needs three templates.

Two templates cannot reach 20 minutes. REF-1 and WCC-1 already hold all 13
instances of their game, and those games run 2.0 and 1.3 turns per transcript.

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
        templates = json.load(fh)["templates"]

    models = sorted({r["model_id"] for r in manifest.values()})
    rows, problems = [], []
    for tid in sorted(templates):
        spec = templates[tid]
        game = spec["game"]
        for model in models:
            batch_id = f"{tid}__{model}"
            for pos, ent in enumerate(spec["instances"], 1):
                experiment, instance = ent.split("/")
                transcript_id = f"{model}__{game}__{experiment}__{instance}"
                r = manifest.get(transcript_id)
                if r is None:
                    problems.append(f"{batch_id}: {transcript_id} not in manifest")
                    continue
                rows.append({
                    "batch_id": batch_id,
                    "template_id": tid,
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

    print(f"templates {len(templates)} x models {len(models)} = {len(sizes)} batches")
    print(f"transcripts {len(rows)}   sittings {len(sizes) * 3}   "
          f"judgements {len(rows) * 3}")
    print("\nper template (same for every model):")
    for tid in sorted(templates):
        n = len(templates[tid]["instances"])
        print(f"  {tid:9} {templates[tid]['game']:24} {n:>3} transcripts")

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
