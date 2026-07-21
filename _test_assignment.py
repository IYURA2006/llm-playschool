"""Test harness: exercise assignment.py's coverage-balanced reservation logic
against a scratch SQLite DB. No Gradio, no browser, no real HF upload.
Run via: python _test_assignment.py"""
import concurrent.futures
import os
import sqlite3
import tempfile
import traceback
from collections import Counter
from datetime import datetime, timedelta

# Neutralize any real HF backup credentials from .env *before* importing db —
# this script writes ~150 synthetic PIDs and must never touch a real HF
# dataset. dotenv's default override=False means these empty strings stick.
os.environ["HF_TOKEN"] = ""
os.environ["HF_PILOT_DATASET_REPO"] = ""
os.environ["HF_DATASET_REPO"] = ""

import db
import assignment

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name


def reset_db():
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(db.DB_PATH + suffix)
        except OSError:
            pass
    db.init_db()


def _raw_reservation_counts():
    """Distinct-annotator count per game_slug from ALL reservations (turns
    saved or not, verdicted or not) — unlike db.coverage_counts (which is
    deliberately verdict/staleness-gated for the assignment algorithm's own
    use), this is the direct ground truth for "how many annotators actually
    hold a claim on this slug right now", which is what the over-assignment
    checks below need to verify against."""
    with sqlite3.connect(db.DB_PATH) as conn:
        rows = conn.execute(
            "SELECT game_slug, COUNT(DISTINCT annotator_id) FROM annotations "
            "WHERE condition='hybrid' GROUP BY game_slug"
        ).fetchall()
    return dict(rows)


failures = []


def check(label, condition):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}")
        failures.append(label)


def run(name, fn):
    print(f"\n=== {name} ===")
    try:
        fn()
    except Exception:
        print(f"  💥 {name} raised:")
        traceback.print_exc()
        failures.append(f"{name} (exception)")


# ──────────────────────────────────────────────────────────────────────────
def test_basic_batch_and_idempotency():
    reset_db()
    print(f"  pool size: {len(assignment.POOL_SLUGS)} transcripts across "
          f"{len({assignment._game_type(s) for s in assignment.POOL_SLUGS})} game types")

    pid = "fakepid_basic"
    playlist1, err1 = assignment.build_playlist_for(pid)
    check("first call returns a batch, no error", err1 is None and len(playlist1) == assignment.BATCH_SIZE)
    types = [assignment._game_type(item["game"]) for item in playlist1]
    check("batch has no duplicate game types (pool has room)", len(types) == len(set(types)))
    check("every item uses the hybrid condition", all(item["condition"] == "hybrid" for item in playlist1))

    playlist2, err2 = assignment.build_playlist_for(pid)
    check("re-calling the same PID returns the identical playlist (idempotent)",
          playlist1 == playlist2 and err2 is None)


def _safe_pid_count(margin=5):
    """Number of participants that can be assigned a full batch without the
    pool running dry — computed from the actual pool size so this doesn't
    silently start asserting the wrong thing if the game roster changes."""
    max_full_batches = (len(assignment.POOL_SLUGS) * assignment.COVERAGE_TARGET) // assignment.BATCH_SIZE
    return max(1, max_full_batches - margin)


def test_coverage_balances_across_many_pids():
    reset_db()
    n_pids = _safe_pid_count()
    print(f"  using {n_pids} participants (pool capacity supports "
          f"~{len(assignment.POOL_SLUGS) * assignment.COVERAGE_TARGET // assignment.BATCH_SIZE} "
          f"full batches before exhaustion)")
    for i in range(n_pids):
        playlist, err = assignment.build_playlist_for(f"fakepid_cov_{i:03d}")
        assert err is None, f"unexpected error for pid {i}: {err}"

    counts = _raw_reservation_counts()
    over_target = {slug: c for slug, c in counts.items() if c > assignment.COVERAGE_TARGET}
    check(f"no transcript exceeds COVERAGE_TARGET={assignment.COVERAGE_TARGET} "
          f"after {n_pids} sequential participants", not over_target)
    print(f"  coverage distribution: {sorted(Counter(counts.values()).items())}")


def test_concurrent_distinct_pids_stay_within_target():
    reset_db()
    n_pids = _safe_pid_count()

    def assign(i):
        return assignment.build_playlist_for(f"fakepid_conc_{i:03d}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(assign, range(n_pids)))

    check("all concurrent distinct-PID requests succeeded",
          all(err is None for _, err in results))
    counts = _raw_reservation_counts()
    over_target = {slug: c for slug, c in counts.items() if c > assignment.COVERAGE_TARGET}
    check(f"no transcript exceeds COVERAGE_TARGET under concurrent distinct PIDs "
          f"(write_transaction() serialization holds)", not over_target)


def test_concurrent_same_pid_no_double_reservation():
    reset_db()
    pid = "fakepid_race_same"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(assignment.build_playlist_for, pid)
        f2 = pool.submit(assignment.build_playlist_for, pid)
        r1, e1 = f1.result()
        r2, e2 = f2.result()

    check("both concurrent calls for the same new PID succeeded", e1 is None and e2 is None)
    check("both concurrent calls return the SAME playlist", r1 == r2)
    reserved = db.assigned_games(pid, condition="hybrid")
    check(f"exactly BATCH_SIZE={assignment.BATCH_SIZE} rows reserved, not "
          f"{2 * assignment.BATCH_SIZE} (double-checked lock holds)",
          len(reserved) == assignment.BATCH_SIZE)


def test_stale_reservation_frees_its_slot():
    reset_db()
    # Fill every slug in one game-type up to COVERAGE_TARGET with plain
    # (non-stale) reservations, then hand-craft one more that's artificially
    # old, and confirm coverage_counts drops it once stale_before excludes it.
    slug = assignment.POOL_SLUGS[0]
    with sqlite3.connect(db.DB_PATH) as conn:
        for i in range(assignment.COVERAGE_TARGET):
            conn.execute(
                "INSERT INTO annotations (game_slug, annotator_id, condition, updated_at) "
                "VALUES (?, ?, 'hybrid', ?)",
                (slug, f"filler_{i}", datetime.now().isoformat()),
            )
        stale_ts = (datetime.now() - timedelta(hours=assignment.STALE_AFTER_HOURS + 1)).isoformat()
        conn.execute(
            "INSERT INTO annotations (game_slug, annotator_id, condition, updated_at) "
            "VALUES (?, 'filler_stale', 'hybrid', ?)",
            (slug, stale_ts),
        )

    fresh_cutoff = assignment._stale_cutoff()
    counts_with_staleness = db.coverage_counts("hybrid", stale_before=fresh_cutoff)
    check(f"stale reservation is excluded — count stays at COVERAGE_TARGET "
          f"({assignment.COVERAGE_TARGET}), not {assignment.COVERAGE_TARGET + 1}",
          counts_with_staleness.get(slug, 0) == assignment.COVERAGE_TARGET)

    counts_no_staleness = db.coverage_counts("hybrid", stale_before=None)
    check("a pure verdict-only count (stale_before=None) ignores unverdicted "
          "rows entirely (all are placeholders here, none verdicted)",
          counts_no_staleness.get(slug, 0) == 0)


def test_pool_exhaustion_returns_no_tasks_message():
    reset_db()
    orig_target, orig_batch = assignment.COVERAGE_TARGET, assignment.BATCH_SIZE
    try:
        assignment.COVERAGE_TARGET = 1
        assignment.BATCH_SIZE = 1
        n_slugs = len(assignment.POOL_SLUGS)
        # At target=1/batch=1, each participant claims exactly one
        # never-before-picked slug, so exhausting the whole pool takes
        # exactly one participant per slug (not per type).
        for i in range(n_slugs):
            playlist, err = assignment.build_playlist_for(f"fakepid_exhaust_{i:03d}")
            assert err is None, f"unexpected exhaustion before the pool was full: {err}"

        playlist, err = assignment.build_playlist_for("fakepid_exhaust_overflow")
        check("once every transcript hits target, a new PID gets [] + NO_TASKS_MESSAGE",
              playlist == [] and err == assignment.NO_TASKS_MESSAGE)
    finally:
        assignment.COVERAGE_TARGET, assignment.BATCH_SIZE = orig_target, orig_batch


# ──────────────────────────────────────────────────────────────────────────
run("basic batch + idempotency", test_basic_batch_and_idempotency)
run("coverage balances across many sequential PIDs", test_coverage_balances_across_many_pids)
run("concurrent distinct PIDs stay within COVERAGE_TARGET", test_concurrent_distinct_pids_stay_within_target)
run("concurrent same-PID double-checked lock", test_concurrent_same_pid_no_double_reservation)
run("stale reservation frees its slot", test_stale_reservation_frees_its_slot)
run("pool exhaustion returns NO_TASKS_MESSAGE", test_pool_exhaustion_returns_no_tasks_message)

for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(db.DB_PATH + suffix)
    except OSError:
        pass

print("\n" + "=" * 60)
if failures:
    print(f"❌ {len(failures)} check(s) failed:")
    for f in failures:
        print(f"   - {f}")
    raise SystemExit(1)
print("✅ all checks passed")
