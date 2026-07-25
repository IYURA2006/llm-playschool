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


def complete_session(pid, condition="hybrid"):
    """Stamp a verdict on every unverdicted reservation this PID holds, i.e.
    simulate them finishing their current sitting. A session only counts
    toward the cap (and only frees the participant to start a new batch)
    once every game in it is verdicted — see assignment._resume_target."""
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE annotations SET verdict_at=?, updated_at=? "
            "WHERE annotator_id=? AND condition=? AND verdict_at IS NULL",
            (datetime.now().isoformat(), datetime.now().isoformat(), pid, condition),
        )


def batch_seconds(playlist):
    """Estimated length of a playlist, the same way assignment.py sizes it:
    every transcript's cost plus ONE rules-reading charge for the shared
    game type."""
    slugs = [item["game"] for item in playlist]
    return (sum(assignment.transcript_seconds(s) for s in slugs)
            + assignment.rules_seconds(slugs[0]))


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
    check("first call returns a batch, no error", err1 is None and len(playlist1) >= 1)
    types = {assignment._game_type(item["game"]) for item in playlist1}
    check("batch holds exactly ONE game type", len(types) == 1)
    check("every item uses the hybrid condition", all(item["condition"] == "hybrid" for item in playlist1))
    check("no duplicate transcripts within the batch",
          len({i["game"] for i in playlist1}) == len(playlist1))

    est = batch_seconds(playlist1)
    within = est <= assignment.TARGET_SECONDS * assignment.OVERSHOOT
    check(f"batch fits the time budget (~{est / 60:.1f} min vs "
          f"{assignment.TARGET_SECONDS / 60:.0f} min target), or is a single "
          f"over-long transcript", within or len(playlist1) == 1)
    print(f"  batch: {len(playlist1)} × {types.pop()} ≈ {est / 60:.1f} min")

    playlist2, err2 = assignment.build_playlist_for(pid)
    check("re-calling mid-session returns the identical playlist (resume, not re-pick)",
          playlist1 == playlist2 and err2 is None)


def _pid_count():
    """Enough participants to drive the pool toward exhaustion. Batch size is
    no longer fixed (the time budget decides it, so it varies from 1 game for
    a long adventuregame transcript to a dozen short taboo ones), which means
    the exact number the pool can serve is not predictable up front. These
    tests therefore oversubscribe deliberately and assert the invariant that
    actually matters — no transcript is ever assigned past COVERAGE_TARGET —
    treating a late NO_TASKS_MESSAGE as the expected terminal state rather
    than a failure."""
    return len(assignment.POOL_SLUGS) * assignment.COVERAGE_TARGET


def _report(results):
    served = [p for p, err in results if err is None and p]
    exhausted = [err for _, err in results if err == assignment.NO_TASKS_MESSAGE]
    unexpected = [err for _, err in results
                  if err is not None and err != assignment.NO_TASKS_MESSAGE]
    return served, exhausted, unexpected


def test_coverage_balances_across_many_pids():
    reset_db()
    n_pids = _pid_count()
    results = [assignment.build_playlist_for(f"fakepid_cov_{i:03d}")
               for i in range(n_pids)]
    served, exhausted, unexpected = _report(results)
    print(f"  {len(served)}/{n_pids} participants served, {len(exhausted)} hit "
          f"an exhausted pool (pool: {len(assignment.POOL_SLUGS)} transcripts "
          f"× target {assignment.COVERAGE_TARGET})")

    check("no participant got an unexpected error", not unexpected)
    check("at least some participants were served", len(served) > 0)
    counts = _raw_reservation_counts()
    over_target = {slug: c for slug, c in counts.items() if c > assignment.COVERAGE_TARGET}
    check(f"no transcript exceeds COVERAGE_TARGET={assignment.COVERAGE_TARGET} "
          f"after {n_pids} sequential participants", not over_target)
    check("every batch held a single game type",
          all(len({assignment._game_type(i["game"]) for i in p}) == 1 for p in served))
    print(f"  coverage distribution: {sorted(Counter(counts.values()).items())}")


def test_concurrent_distinct_pids_stay_within_target():
    reset_db()
    n_pids = _pid_count()

    def assign(i):
        return assignment.build_playlist_for(f"fakepid_conc_{i:03d}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(assign, range(n_pids)))

    served, exhausted, unexpected = _report(results)
    check("no concurrent request failed unexpectedly (only pool exhaustion)",
          not unexpected)
    check("concurrent participants were served", len(served) > 0)
    counts = _raw_reservation_counts()
    over_target = {slug: c for slug, c in counts.items() if c > assignment.COVERAGE_TARGET}
    check(f"no transcript exceeds COVERAGE_TARGET under {n_pids} concurrent "
          f"distinct PIDs (write_transaction() serialization holds)", not over_target)


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
    check(f"one batch reserved ({len(r1)} rows), not two (double-checked lock "
          f"holds — otherwise the PID silently burns 2 of their sessions)",
          len(reserved) == len(r1))
    sessions = db.session_summary(pid, condition="hybrid")
    check("the race produced exactly ONE session, not two", len(sessions) == 1)


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
    orig_target, orig_max = assignment.COVERAGE_TARGET, assignment.MAX_BATCH_GAMES
    try:
        assignment.COVERAGE_TARGET = 1
        assignment.MAX_BATCH_GAMES = 1
        n_slugs = len(assignment.POOL_SLUGS)
        # At target=1 and one game per batch, each participant claims exactly
        # one never-before-picked slug, so exhausting the whole pool takes
        # exactly one participant per slug (not per type).
        for i in range(n_slugs):
            playlist, err = assignment.build_playlist_for(f"fakepid_exhaust_{i:03d}")
            assert err is None, f"unexpected exhaustion before the pool was full: {err}"

        playlist, err = assignment.build_playlist_for("fakepid_exhaust_overflow")
        check("once every transcript hits target, a new PID gets [] + NO_TASKS_MESSAGE",
              playlist == [] and err == assignment.NO_TASKS_MESSAGE)
    finally:
        assignment.COVERAGE_TARGET, assignment.MAX_BATCH_GAMES = orig_target, orig_max


def test_returning_participant_gets_a_new_disjoint_batch():
    reset_db()
    pid = "fakepid_return"
    first, err = assignment.build_playlist_for(pid)
    assert err is None and first, f"no first batch: {err}"
    check("session index starts at 1", assignment.current_session_index(pid) == 1)

    # Still mid-session → must resume, NOT start a second sitting.
    again, _ = assignment.build_playlist_for(pid)
    check("an unfinished session is resumed, never replaced", again == first)

    complete_session(pid)
    second, err2 = assignment.build_playlist_for(pid)
    check("after finishing, a returning participant gets a NEW batch",
          err2 is None and second and second != first)
    check("session index advances to 2", assignment.current_session_index(pid) == 2)

    first_slugs = {i["game"] for i in first}
    second_slugs = {i["game"] for i in second}
    check("the new batch shares NO transcript with the previous one "
          "(never annotate the same thing twice)",
          not (first_slugs & second_slugs))
    check("the new batch is still a single game type",
          len({assignment._game_type(i["game"]) for i in second}) == 1)

    sessions = db.session_summary(pid, condition="hybrid")
    check("exactly two sessions on file, the first complete and the second not",
          [(i, t == d) for i, t, d in sessions] == [(1, True), (2, False)])


def test_session_cap_blocks_further_work():
    reset_db()
    pid = "fakepid_cap"
    orig_max_sessions, orig_max_games = assignment.MAX_SESSIONS, assignment.MAX_BATCH_GAMES
    try:
        # Small numbers so the cap — not the pool — is what stops this PID.
        assignment.MAX_SESSIONS = 3
        assignment.MAX_BATCH_GAMES = 1
        for n in range(assignment.MAX_SESSIONS):
            playlist, err = assignment.build_playlist_for(pid)
            assert err is None and playlist, f"session {n + 1} refused early: {err}"
            complete_session(pid)

        playlist, err = assignment.build_playlist_for(pid)
        check(f"after {assignment.MAX_SESSIONS} completed sessions the PID is "
              f"blocked with CAP_MESSAGE",
              playlist == [] and err == assignment.CAP_MESSAGE)

        done = db.session_summary(pid, condition="hybrid")
        check("no extra session was reserved by the refused attempt",
              len(done) == assignment.MAX_SESSIONS)
    finally:
        assignment.MAX_SESSIONS = orig_max_sessions
        assignment.MAX_BATCH_GAMES = orig_max_games


def test_half_finished_session_does_not_count_toward_cap():
    reset_db()
    pid = "fakepid_partial"
    playlist, err = assignment.build_playlist_for(pid)
    assert err is None and playlist, f"no batch: {err}"
    if len(playlist) < 2:
        print("  (skipped: pool produced a 1-game batch, nothing to half-finish)")
        return

    # Verdict exactly one game, leaving the sitting unfinished.
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE annotations SET verdict_at=?, updated_at=? "
            "WHERE annotator_id=? AND game_slug=?",
            (datetime.now().isoformat(), datetime.now().isoformat(),
             pid, playlist[0]["game"]),
        )

    resumed, err = assignment.build_playlist_for(pid)
    check("a half-finished sitting resumes with the same playlist",
          err is None and resumed == playlist)
    check("and does not open a second session",
          len(db.session_summary(pid, condition="hybrid")) == 1)


# ──────────────────────────────────────────────────────────────────────────
run("basic batch + idempotency", test_basic_batch_and_idempotency)
run("coverage balances across many sequential PIDs", test_coverage_balances_across_many_pids)
run("concurrent distinct PIDs stay within COVERAGE_TARGET", test_concurrent_distinct_pids_stay_within_target)
run("concurrent same-PID double-checked lock", test_concurrent_same_pid_no_double_reservation)
run("stale reservation frees its slot", test_stale_reservation_frees_its_slot)
run("pool exhaustion returns NO_TASKS_MESSAGE", test_pool_exhaustion_returns_no_tasks_message)
run("returning participant gets a new, disjoint batch", test_returning_participant_gets_a_new_disjoint_batch)
run("session cap blocks further work", test_session_cap_blocks_further_work)
run("half-finished session resumes and doesn't count", test_half_finished_session_does_not_count_toward_cap)

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
