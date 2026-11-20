#!/usr/bin/env python3
"""Close out the pilot: take its data out of the study database, prove the copy
is complete, and only then remove it.

    python pilot_closeout.py              # export and verify. Deletes nothing.
    python pilot_closeout.py --delete     # the same, then delete what was exported

The pilot writes to the same database as the study. Its rows have to be gone
before the study starts - readiness.py refuses to launch over them - because
they would count toward a returning participant's sitting cap and turn up in
the study's export as transcripts that are in no manifest. Doing that by hand
is an export, a DELETE typed into psql and a hope that the first finished
before the second began. This does both, in the order that cannot lose data.

WHAT IT WRITES, under exports_pilot/pilot_<timestamp>/

  raw_annotations.json     every pilot row of `annotations`, every column
  raw_turn_ratings.json    every row of `turn_ratings` belonging to them
  raw_question_sets.json   the question sets those rows were collected under
  raw_consents.json        consent records of the pilot's participants
  annotations_<stamp>/     export_annotations.py's decoded CSVs and JSON

The raw files are the record: lossless, and enough to put the rows back. The
decoded export is the one to analyse.

WHAT --delete REMOVES

Exactly the annotation rows it has just exported and read back from disk, by
id, with their turn ratings, in one transaction. Inside that transaction it
checks again that nothing changed since the export - a rating saved in between
aborts the delete with nothing removed, and you run it again. So do this after
the pilot has closed on Prolific, not while someone is still working.

WHAT IT LEAVES

Consent and practice records. Someone who did the pilot has consented and has
seen the practice round; if they join the study they should not be shown
either again. Study rows are never touched: only batch ids starting PILOT-.
"""

import os

# This tool is only ever about the pilot, whatever .env currently serves - it
# is normally run just after GAMES_DIR has been switched back to the study.
# Must precede every import below: study_profile reads it once, at import.
os.environ["GAMES_DIR"] = "games_pilot"
os.environ.setdefault("SKIP_DB_INIT", "1")

import argparse                                              # noqa: E402
import json                                                  # noqa: E402
import sys                                                   # noqa: E402
from datetime import datetime                                # noqa: E402

import psycopg2                                              # noqa: E402
from dotenv import load_dotenv                               # noqa: E402

import study_profile                                         # noqa: E402

load_dotenv()

LIKE = study_profile.PILOT_PREFIX + "%"


def connect():
    missing = [v for v in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
               if not os.environ.get(v)]
    if missing:
        sys.exit(f"Missing DB config: {', '.join(missing)} (see .env.example).")
    return psycopg2.connect(
        host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
        gssencmode=os.environ.get("DB_GSSENCMODE", "disable"))


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def snapshot(conn):
    """Everything the pilot put in the database, read in ONE transaction so the
    four tables agree with each other."""
    with conn.cursor() as cur:
        cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        ann = _rows(cur, "SELECT * FROM annotations WHERE batch_id LIKE %s "
                         "ORDER BY id", (LIKE,))
        ids = [r["id"] for r in ann]
        turns = _rows(cur, "SELECT * FROM turn_ratings WHERE annotation_id = ANY(%s) "
                           "ORDER BY annotation_id, turn_index", (ids,))
        hashes = sorted({r["question_set_hash"] for r in ann
                         if r.get("question_set_hash")})
        qsets = _rows(cur, "SELECT * FROM question_sets WHERE question_set_hash "
                           "= ANY(%s)", (hashes,))
        people = sorted({r["annotator_id"] for r in ann})
        consents = _rows(cur, "SELECT * FROM consents WHERE annotator_id = ANY(%s) "
                              "ORDER BY annotator_id", (people,))
    conn.rollback()
    return {"annotations": ann, "turn_ratings": turns,
            "question_sets": qsets, "consents": consents}


def write_raw(snap, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, rows in snap.items():
        path = os.path.join(out_dir, f"raw_{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1, default=str, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())


def read_back(out_dir):
    """What is really on disk, not what we meant to write."""
    out = {}
    for name in ("annotations", "turn_ratings", "question_sets", "consents"):
        with open(os.path.join(out_dir, f"raw_{name}.json"), encoding="utf-8") as fh:
            out[name] = json.load(fh)
    return out


def fingerprint(rows, key):
    """{id: the whole row as text}. Timestamps and all, so a row that was
    touched after the export does not compare equal to its exported self."""
    return {r[key]: json.dumps(r, sort_keys=True, default=str) for r in rows}


def describe(snap):
    ann = snap["annotations"]
    done = [r for r in ann if r["verdict_at"]]
    per_batch = {}
    for r in ann:
        b = per_batch.setdefault(r["batch_id"], {"people": set(), "done": 0, "rows": 0})
        b["rows"] += 1
        b["people"].add(r["annotator_id"])
        b["done"] += bool(r["verdict_at"])
    print(f"pilot rows       : {len(ann)} annotations "
          f"({len(done)} with a verdict), {len(snap['turn_ratings'])} turn ratings, "
          f"{len({r['annotator_id'] for r in ann})} participants")
    for bid in sorted(per_batch):
        b = per_batch[bid]
        print(f"  {bid:16} {len(b['people'])} participant(s), "
              f"{b['done']}/{b['rows']} transcripts finished")
    unfinished = len(ann) - len(done)
    if unfinished:
        print(f"  note: {unfinished} row(s) have no verdict - reserved or "
              f"part-done sittings. They are exported as they are.")
    target = study_profile.PILOT.coverage_target
    counts = {}
    for r in done:
        counts[r["game_slug"]] = counts.get(r["game_slug"], 0) + 1
    short = sum(1 for n in counts.values() if n < target)
    try:
        import study_set
        short += sum(1 for s in study_set.DIMENSIONS if s not in counts)
        total = len(study_set.DIMENSIONS)
    except Exception:
        total = len(counts)
    print(f"coverage         : {total - short}/{total} transcripts have "
          f"{target} finished ratings" + ("" if not short else
          f" - {short} do not; deleting now ends the pilot short"))


def decoded_export(out_dir):
    """export_annotations.py's snapshot, into the same folder. Returns an error
    string, or None if it wrote one."""
    try:
        import export_annotations
        before = set(os.listdir(out_dir))
        rc = export_annotations.main(["--out", out_dir])
        made = set(os.listdir(out_dir)) - before
        if not made:
            return f"export_annotations wrote nothing (exit {rc})"
        return None
    except SystemExit as exc:
        return f"export_annotations stopped: {exc}"
    except Exception as exc:
        return f"export_annotations failed: {type(exc).__name__}: {exc}"


def delete_exported(conn, on_disk):
    """Remove exactly the rows that are on disk. Returns (deleted, error).

    One transaction. The table is locked against writes for its few
    milliseconds, the rows are compared with their exported selves, and only if
    every one is unchanged does the DELETE run. Anything else rolls back.
    """
    want = fingerprint(on_disk["annotations"], "id")
    want_turns = fingerprint(on_disk["turn_ratings"], "id")
    ids = list(want)
    with conn.cursor() as cur:
        cur.execute("LOCK TABLE annotations, turn_ratings IN SHARE ROW EXCLUSIVE MODE")
        now = fingerprint(_rows(cur, "SELECT * FROM annotations WHERE id = ANY(%s)",
                                (ids,)), "id")
        now_turns = fingerprint(_rows(
            cur, "SELECT * FROM turn_ratings WHERE annotation_id = ANY(%s)", (ids,)),
            "id")
        changed = [i for i in ids if now.get(i) != want[i]]
        if changed or now_turns != want_turns:
            conn.rollback()
            return 0, (f"{len(changed)} annotation row(s) and "
                       f"{len(set(now_turns) ^ set(want_turns))} turn rating(s) "
                       f"changed after the export - someone is still working. "
                       f"Nothing was deleted; run this again when the pilot "
                       f"has closed.")
        cur.execute("DELETE FROM annotations WHERE id = ANY(%s)", (ids,))
        deleted = cur.rowcount
        cur.execute("SELECT count(*) FROM turn_ratings WHERE annotation_id = ANY(%s)",
                    (ids,))
        orphans = cur.fetchone()[0]
        if deleted != len(ids) or orphans:
            conn.rollback()
            return 0, (f"expected to delete {len(ids)} rows, the database "
                       f"reported {deleted} ({orphans} turn ratings left). "
                       f"Rolled back; nothing was deleted.")
    conn.commit()
    return deleted, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--delete", action="store_true",
                    help="after a verified export, delete the exported rows")
    ap.add_argument("--out", default="exports_pilot",
                    help="where to write the snapshot (default: exports_pilot)")
    args = ap.parse_args(argv)

    conn = connect()
    try:
        print(f"database         : {os.environ['DB_NAME']} on {os.environ['DB_HOST']}")
        snap = snapshot(conn)
        if not snap["annotations"]:
            print("pilot rows       : none - nothing to export, nothing to delete.")
            return 0
        describe(snap)

        out_dir = os.path.join(
            args.out, "pilot_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        write_raw(snap, out_dir)
        on_disk = read_back(out_dir)
        for name in snap:
            if len(on_disk[name]) != len(snap[name]):
                print(f"\nSTOPPED: {name} has {len(snap[name])} rows in the "
                      f"database and {len(on_disk[name])} in the file.")
                return 1
        print(f"raw export       : {out_dir}  (read back and counted)")

        err = decoded_export(out_dir)
        if err:
            print(f"decoded export   : FAILED - {err}")
            if args.delete:
                print("\nSTOPPED before deleting: the decoded export did not "
                      "complete. The raw files above are intact.")
                return 1
        else:
            print("decoded export   : written alongside")

        if not args.delete:
            print("\nNothing was deleted. Check the export, then run again "
                  "with --delete.")
            return 0

        deleted, err = delete_exported(conn, on_disk)
        if err:
            print(f"\nSTOPPED: {err}")
            return 1
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM annotations WHERE batch_id LIKE %s",
                        (LIKE,))
            left = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM annotations")
            others = cur.fetchone()[0] - left
        conn.rollback()
        print(f"\ndeleted          : {deleted} annotation rows and their "
              f"{len(on_disk['turn_ratings'])} turn ratings")
        print(f"pilot rows left  : {left}"
              + ("" if not left else "  <- arrived during the close-out; run again"))
        print(f"other rows       : {others} (untouched)")
        print("\nNext: set GAMES_DIR=games_study in .env, redeploy, and run "
              "readiness.py.")
        return 0 if not left else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
