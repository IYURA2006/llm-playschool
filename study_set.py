"""The study's transcript inventory and batch plan, read from CSV.

    study_manifest.csv   what build_study_set.py selected  (416 transcripts)
    batches.csv          how build_batches.py grouped them (72 batches)

Files only — no database, and no `annotation` import at module scope, because
annotation imports db and that would cycle. `validate()` imports it lazily.

Two consumers:

  * assignment  needs BATCH_MEMBERS to hand a whole batch to one annotator
  * export      needs DIMENSIONS to attach model/domain/game/... to every row

`transcript_id` in the manifest is byte-identical to `annotation.game_slug(path)`
by construction (build_study_set.py builds it from the same parts), which is what
makes the join a plain dict lookup rather than a parse.

Note `domain` and `batch_id` are the two fields that CANNOT be recovered from the
slug: build_study_set flattens the domain level away, and batch membership only
exists in batches.csv. That is why this module exists at all.
"""

import csv
import os
from collections import defaultdict

_dir = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(_dir, os.environ.get("STUDY_MANIFEST", "study_manifest.csv"))
BATCHES = os.path.join(_dir, os.environ.get("STUDY_BATCHES", "batches.csv"))

# Fields copied onto every annotation row. Names match the CSV headers exactly so
# the DB columns, the manifest and the export all read the same.
DIMENSION_FIELDS = ("model_id", "domain", "game", "experiment", "instance",
                    "batch_id", "template_id")


def _read(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _load():
    dims, members, batch_game = {}, defaultdict(list), {}
    batch_template, template_batches = {}, defaultdict(list)

    for r in _read(MANIFEST):
        dims[r["transcript_id"]] = {
            "model_id": r.get("model_id", ""),
            "domain": r.get("domain", ""),
            "game": r.get("game", ""),
            "experiment": r.get("experiment", ""),
            "instance": r.get("instance", ""),
            "batch_id": None,          # filled in from batches.csv below
            "template_id": None,
        }

    rows = _read(BATCHES)
    rows.sort(key=lambda r: (r["batch_id"], int(r.get("position") or 0)))
    for r in rows:
        tid, bid = r["transcript_id"], r["batch_id"]
        members[bid].append(tid)
        batch_game.setdefault(bid, r.get("game", ""))
        tpl = r.get("template_id") or bid
        batch_template.setdefault(bid, tpl)
        if bid not in template_batches[tpl]:
            template_batches[tpl].append(bid)
        if tid in dims:
            dims[tid]["batch_id"] = bid
            dims[tid]["template_id"] = tpl

    return (dims, {b: tuple(v) for b, v in members.items()}, batch_game,
            batch_template, {t: tuple(v) for t, v in template_batches.items()})


(DIMENSIONS, BATCH_MEMBERS, BATCH_GAME,
 BATCH_TEMPLATE, TEMPLATE_BATCHES) = _load()


def dimensions(slug):
    """Dimension dict for a transcript slug, or None if it isn't in the study."""
    d = DIMENSIONS.get(slug)
    return dict(d) if d else None


def batch_of(slug):
    d = DIMENSIONS.get(slug)
    return d["batch_id"] if d else None


def template_of(batch_id):
    """The template a batch instantiates.

    Every model's version of a template holds the SAME instances, so assignment
    must exclude by TEMPLATE, not by batch: an annotator who did REF-1 for one
    model would otherwise be handed REF-1 for another and re-rate transcripts of
    games they have already seen, destroying the independence of the 3 ratings.
    """
    return BATCH_TEMPLATE.get(batch_id)


def batches_for_template(template_id):
    return TEMPLATE_BATCHES.get(template_id, ())


def reload():
    """Re-read both CSVs. For tests and for editing batches without a restart."""
    global DIMENSIONS, BATCH_MEMBERS, BATCH_GAME, BATCH_TEMPLATE, TEMPLATE_BATCHES
    (DIMENSIONS, BATCH_MEMBERS, BATCH_GAME,
     BATCH_TEMPLATE, TEMPLATE_BATCHES) = _load()


def validate(expect_batch_size=None):
    """Return a list of problems; empty means the inventory is internally
    consistent AND agrees with the transcripts actually on disk.

    Imported lazily so this module stays DB-free at import time.
    """
    problems = []
    if not DIMENSIONS:
        return [f"manifest is empty or missing: {MANIFEST}"]

    try:
        import annotation
    except Exception as exc:                      # pragma: no cover
        return [f"cannot import annotation to cross-check: {exc!r}"]

    on_disk = {annotation.game_slug(p) for _, p in annotation.GAMES}
    manifest_ids = set(DIMENSIONS)

    for slug in sorted(manifest_ids - on_disk)[:10]:
        problems.append(f"in manifest but not on disk: {slug}")
    for slug in sorted(on_disk - manifest_ids)[:10]:
        problems.append(f"on disk but not in manifest: {slug}")

    # The manifest's `game` must agree with what the app derives from the path,
    # or the question set applied at render time won't match the export's label.
    for _, path in annotation.GAMES:
        slug = annotation.game_slug(path)
        d = DIMENSIONS.get(slug)
        if d and d["game"] and d["game"] != annotation.game_key(path):
            problems.append(
                f"game mismatch for {slug}: manifest={d['game']!r} "
                f"path={annotation.game_key(path)!r}")

    if BATCH_MEMBERS:
        seen = [t for members in BATCH_MEMBERS.values() for t in members]
        if len(seen) != len(set(seen)):
            problems.append("batches overlap — a transcript appears in more than one")
        missing = set(manifest_ids) - set(seen)
        if missing:
            problems.append(f"{len(missing)} transcript(s) belong to no batch")
        unknown = set(seen) - manifest_ids
        for t in sorted(unknown)[:10]:
            problems.append(f"batch references unknown transcript: {t}")

        for bid, members in sorted(BATCH_MEMBERS.items()):
            if expect_batch_size:
                lo, hi = expect_batch_size
                if not lo <= len(members) <= hi:
                    problems.append(f"batch {bid} has {len(members)} transcripts "
                                    f"(expected {lo}-{hi})")
            games = {DIMENSIONS[t]["game"] for t in members if t in DIMENSIONS}
            if len(games) > 1:
                problems.append(f"batch {bid} mixes game types: {sorted(games)}")
            # One batch, one model — that is what makes model identity hideable
            # and keeps a sitting's difficulty uniform.
            mods = {DIMENSIONS[t]["model_id"] for t in members if t in DIMENSIONS}
            if len(mods) > 1:
                problems.append(f"batch {bid} mixes models: {sorted(mods)}")

        # Every template must expand to exactly one batch per model, holding the
        # same instances — otherwise the cross-model exclusion is unsound.
        for tpl, bids in sorted(TEMPLATE_BATCHES.items()):
            shapes = {tuple(sorted(t.split("__", 1)[1] for t in BATCH_MEMBERS[b]))
                      for b in bids}
            if len(shapes) > 1:
                problems.append(f"template {tpl} expands to differing instance "
                                f"sets across its {len(bids)} batches")
    return problems


def summary():
    from collections import Counter
    return {
        "transcripts": len(DIMENSIONS),
        "batches": len(BATCH_MEMBERS),
        "models": dict(sorted(Counter(d["model_id"] for d in DIMENSIONS.values()).items())),
        "games": dict(sorted(Counter(d["game"] for d in DIMENSIONS.values()).items())),
        "domains": dict(sorted(Counter(d["domain"] for d in DIMENSIONS.values()).items())),
        "unbatched": sum(1 for d in DIMENSIONS.values() if not d["batch_id"]),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=2))
    probs = validate()
    print(f"\nvalidate(): {len(probs)} problem(s)")
    for p in probs[:20]:
        print("  -", p)
