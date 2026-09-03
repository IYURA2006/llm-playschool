"""Test harness: exercise assignment.py's coverage-balanced reservation logic
against a disposable test Postgres database. Run via: python _test_assignment.py

Requires TEST_DB_NAME (in .env), a database distinct from the real `study`
DB with the same schema/grants (see postgres_schema.sql) — refuses to run
rather than risk mutating real annotation data."""
import concurrent.futures
import contextlib
import os
import traceback
from collections import Counter
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

_test_db = os.environ.get("TEST_DB_NAME")
if not _test_db:
    raise SystemExit(
        "TEST_DB_NAME is not set — refusing to run against the real `study` "
        "database. Set TEST_DB_NAME in .env to a separate, disposable "
        "Postgres database (same schema/grants as study — see "
        "postgres_schema.sql) before running this script."
    )
os.environ["DB_NAME"] = _test_db
# Must precede the imports below: annotation.py resolves its tree at import.
# games/ and games_study/ share zero slugs, so testing against the wrong one
# silently exercises the pilot pool instead of the study.
os.environ.setdefault("GAMES_DIR", "games_study")

import db
import assignment
import study_set


def reset_db():
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM turn_ratings")
        cur.execute("DELETE FROM annotations")
        cur.execute("DELETE FROM consents")
        cur.execute("DELETE FROM practice_completions")
    db.init_db()


def _raw_reservation_counts():
    """Distinct-annotator count per game_slug from ALL reservations — the
    ground truth for over-assignment checks, unlike the gated db.coverage_counts."""
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT game_slug, COUNT(DISTINCT annotator_id) FROM annotations "
            "WHERE condition='hybrid' GROUP BY game_slug"
        )
        rows = cur.fetchall()
    return dict(rows)


def complete_session(pid, condition="hybrid"):
    """Simulate this PID finishing their current sitting by verdicting every
    unverdicted reservation they hold (see assignment._resume_target)."""
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE annotations SET verdict_at=%s, updated_at=%s "
            "WHERE annotator_id=%s AND condition=%s AND verdict_at IS NULL",
            (datetime.now().isoformat(), datetime.now().isoformat(), pid, condition),
        )


def retire_slugs(slugs, prefix, n=None, condition="hybrid"):
    """Have `n` OTHER PIDs complete each slug, so it reaches COVERAGE_TARGET
    and should never be offered again."""
    n = assignment.COVERAGE_TARGET if n is None else n
    now = datetime.now().isoformat()
    with db._connect() as conn:
        cur = conn.cursor()
        for slug in slugs:
            for i in range(n):
                cur.execute(
                    "INSERT INTO annotations (game_slug, annotator_id, condition, "
                    "                         session_index, verdict_at, updated_at) "
                    "VALUES (%s, %s, %s, 1, %s, %s) "
                    "ON CONFLICT(game_slug, annotator_id, condition) DO NOTHING",
                    (slug, f"{prefix}_{i}", condition, now, now),
                )


def age_reservations(pid, hours, condition="hybrid"):
    """Backdate a PID's claims so their lease has expired."""
    ts = (datetime.now() - timedelta(hours=hours)).isoformat()
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE annotations SET updated_at=%s "
            "WHERE annotator_id=%s AND condition=%s AND verdict_at IS NULL",
            (ts, pid, condition),
        )


def annotation_id(pid, slug, condition="hybrid"):
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM annotations "
            "WHERE annotator_id=%s AND game_slug=%s AND condition=%s",
            (pid, slug, condition),
        )
        row = cur.fetchone()
    return row[0] if row else None


def add_turn_rating(pid, slug, condition="hybrid"):
    """Simulate partial work: one saved turn rating on an otherwise
    unfinished reservation."""
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO turn_ratings (annotation_id, turn_index, prior_information_use) "
            "VALUES (%s, 0, '3')",
            (annotation_id(pid, slug, condition),),
        )


def as_dt(v):
    """updated_at comes back from psycopg2 as a datetime, but _stale_cutoff()
    is an ISO string — comparing the two as strings silently always says
    "stale", because str(datetime) uses a space where isoformat uses 'T'."""
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def updated_at_by_slug(pid, condition="hybrid"):
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT game_slug, updated_at FROM annotations "
            "WHERE annotator_id=%s AND condition=%s",
            (pid, condition),
        )
        rows = cur.fetchall()
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


def test_basic_batch_and_idempotency():
    reset_db()
    print(f"  pool size: {len(assignment.POOL_SLUGS)} transcripts across "
          f"{len({d["game"] for d in study_set.DIMENSIONS.values()})} game types")

    pid = "fakepid_basic"
    playlist1, err1 = assignment.build_playlist_for(pid)
    check("first call returns a batch, no error", err1 is None and len(playlist1) >= 1)
    types = {study_set.DIMENSIONS[item["game"]]["game"] for item in playlist1}
    check("batch holds exactly ONE game type", len(types) == 1)
    check("every item uses the hybrid condition", all(item["condition"] == "hybrid" for item in playlist1))
    check("no duplicate transcripts within the batch",
          len({i["game"] for i in playlist1}) == len(playlist1))

    # The sitting IS the batch: same members, same order.
    bid = study_set.batch_of(playlist1[0]["game"])
    check("playlist is exactly its batch, in position order",
          [i["game"] for i in playlist1] == list(study_set.BATCH_MEMBERS[bid]))
    # Batches mix models on purpose, so a sitting comes out an even length.
    # What must never happen is the same instance twice under two models.
    keys = [(study_set.DIMENSIONS[i["game"]]["experiment"],
             study_set.DIMENSIONS[i["game"]]["instance"]) for i in playlist1]
    check("no instance appears twice in a sitting", len(set(keys)) == len(keys))

    playlist2, err2 = assignment.build_playlist_for(pid)
    check("re-calling mid-session returns the identical playlist (resume, not re-pick)",
          playlist1 == playlist2 and err2 is None)


def _pid_count():
    """Enough participants to drive the pool toward exhaustion — batch size
    varies, so tests deliberately oversubscribe and expect a late NO_TASKS_MESSAGE."""
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
          all(len({study_set.DIMENSIONS[i["game"]]["game"] for i in p}) == 1 for p in served))
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
    # One artificially-old reservation, on top of COVERAGE_TARGET fresh ones.
    slug = assignment.POOL_SLUGS[0]
    with db._connect() as conn:
        cur = conn.cursor()
        for i in range(assignment.COVERAGE_TARGET):
            cur.execute(
                "INSERT INTO annotations (game_slug, annotator_id, condition, updated_at) "
                "VALUES (%s, %s, 'hybrid', %s)",
                (slug, f"filler_{i}", datetime.now().isoformat()),
            )
        stale_ts = (datetime.now() - timedelta(hours=assignment.STALE_AFTER_HOURS + 1)).isoformat()
        cur.execute(
            "INSERT INTO annotations (game_slug, annotator_id, condition, updated_at) "
            "VALUES (%s, 'filler_stale', 'hybrid', %s)",
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
    orig_target = assignment.COVERAGE_TARGET
    try:
        assignment.COVERAGE_TARGET = 1
        # At target=1 the unit is the BATCH, not the transcript: one fresh
        # participant per batch drains the study.
        n_batches = len(study_set.BATCH_MEMBERS)
        for i in range(n_batches):
            playlist, err = assignment.build_playlist_for(f"fakepid_exhaust_{i:03d}")
            assert err is None, (
                f"exhausted after {i} of {n_batches} batches: {err}")
            complete_session(f"fakepid_exhaust_{i:03d}")

        playlist, err = assignment.build_playlist_for("fakepid_exhaust_overflow")
        check("once every transcript hits target, a new PID gets [] + NO_TASKS_MESSAGE",
              playlist == [] and err == assignment.NO_TASKS_MESSAGE)
    finally:
        assignment.COVERAGE_TARGET = orig_target


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
          len({study_set.DIMENSIONS[i["game"]]["game"] for i in second}) == 1)

    sessions = db.session_summary(pid, condition="hybrid")
    check("exactly two sessions on file, the first complete and the second not",
          [(i, t == d) for i, t, d in sessions] == [(1, True), (2, False)])


def test_session_cap_blocks_further_work():
    reset_db()
    pid = "fakepid_cap"
    try:
        # The real cap, unpatched: 5 <= 17 templates, so the CAP — not template
        # exhaustion and not the pool — is what stops this PID.
        for n in range(assignment.MAX_BATCHES):
            playlist, err = assignment.build_playlist_for(pid)
            assert err is None and playlist, f"session {n + 1} refused early: {err}"
            complete_session(pid)

        playlist, err = assignment.build_playlist_for(pid)
        check(f"after {assignment.MAX_BATCHES} completed sessions the PID is "
              f"blocked with CAP_MESSAGE",
              playlist == [] and err == assignment.CAP_MESSAGE)

        done = db.session_summary(pid, condition="hybrid")
        check("no extra session was reserved by the refused attempt",
              len(done) == assignment.MAX_BATCHES)
    finally:
        pass


def test_half_finished_session_does_not_count_toward_cap():
    reset_db()
    pid = "fakepid_partial"
    playlist, err = assignment.build_playlist_for(pid)
    assert err is None and playlist, f"no batch: {err}"

    # Verdict exactly one game, leaving the sitting unfinished.
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE annotations SET verdict_at=%s, updated_at=%s "
            "WHERE annotator_id=%s AND game_slug=%s",
            (datetime.now().isoformat(), datetime.now().isoformat(),
             pid, playlist[0]["game"]),
        )

    resumed, err = assignment.build_playlist_for(pid)
    check("a half-finished sitting resumes with the same playlist",
          err is None and resumed == playlist)
    check("and does not open a second session",
          len(db.session_summary(pid, condition="hybrid")) == 1)


def test_stale_resume_drops_over_covered():
    """The abandon-and-return path: a returning annotator must not put a 4th
    rating on transcripts that retired while their claim was expired."""
    reset_db()
    pid = "fakepid_stale_resume"
    first, err = assignment.build_playlist_for(pid)
    assert err is None and first, f"no batch: {err}"
    original = [i["game"] for i in first]

    # They walk away; the lease expires; COVERAGE_TARGET others finish the lot.
    age_reservations(pid, assignment.STALE_AFTER_HOURS + 1)
    retire_slugs(original, "other")

    resumed, err = assignment.build_playlist_for(pid)
    check("returning to a fully-retired batch does not resume it",
          err is None and not (set(i["game"] for i in resumed) & set(original)))
    check("the voided sitting is replaced by a fresh batch",
          err is None and bool(resumed))

    counts = _raw_reservation_counts()
    over = {s: c for s, c in counts.items()
            if s in original and c > assignment.COVERAGE_TARGET}
    check(f"no retired transcript exceeds COVERAGE_TARGET="
          f"{assignment.COVERAGE_TARGET} reservations after the return "
          f"(over: {over})", not over)


def test_stale_resume_keeps_started_work():
    """Partial work is never destroyed — a transcript they already rated
    stays theirs to finish, even if it retired meanwhile."""
    reset_db()
    pid = "fakepid_partial_resume"
    # No size wrapper needed: every curated batch is >= 4 transcripts.
    first, err = assignment.build_playlist_for(pid)
    assert err is None and first, f"no batch: {err}"
    original = [i["game"] for i in first]
    assert len(original) >= 2, f"expected a multi-game batch, got {original}"

    started = original[0]
    add_turn_rating(pid, started)
    age_reservations(pid, assignment.STALE_AFTER_HOURS + 1)
    retire_slugs(original, "other")   # every one of them, started included

    resumed, err = assignment.build_playlist_for(pid)
    resumed_slugs = [i["game"] for i in resumed]
    check("the sitting resumes with ONLY the transcript they had worked on",
          err is None and resumed_slugs == [started])

    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM turn_ratings WHERE annotation_id=%s",
            (annotation_id(pid, started),),
        )
        n_ratings = cur.fetchone()[0]
    check("their saved turn rating survived the prune", n_ratings == 1)


def test_voided_session_costs_no_cap_slot():
    reset_db()
    pid = "fakepid_voided"
    first, err = assignment.build_playlist_for(pid)
    assert err is None and first, f"no batch: {err}"
    age_reservations(pid, assignment.STALE_AFTER_HOURS + 1)
    retire_slugs([i["game"] for i in first], "other")

    assignment.build_playlist_for(pid)   # voids sitting 1, reserves a new one
    summary = db.session_summary(pid, condition="hybrid")
    check("a voided sitting leaves exactly one sitting on file, unfinished",
          [(t == d) for _, t, d in summary] == [False])
    check("and counts as zero completed sittings against the cap",
          assignment._resume_target(summary)[1] == 0)


def test_session_lease_refreshes_whole_batch():
    """Working on one game must keep the REST of the batch leased — otherwise
    a participant slower than STALE_AFTER_HOURS leaks their unopened games."""
    reset_db()
    pid = "fakepid_lease"
    playlist, err = assignment.build_playlist_for(pid)
    assert err is None and playlist, f"no batch: {err}"
    assert len(playlist) >= 2, f"expected a multi-game batch, got {playlist}"

    age_reservations(pid, assignment.STALE_AFTER_HOURS + 1)
    before = updated_at_by_slug(pid)
    cutoff = as_dt(assignment._stale_cutoff())
    check("precondition: the whole batch reads as stale",
          all(as_dt(ts) < cutoff for ts in before.values()))

    # Empty turns_out keeps this a pure lease test — no game fixture needed.
    db.save_turns(playlist[0]["game"], {}, None, False, pid, "hybrid", [])

    after = updated_at_by_slug(pid)
    siblings = [s for s in after if s != playlist[0]["game"]]
    check("activity on one game refreshes the lease on every other game "
          "in the same sitting",
          all(as_dt(after[s]) > cutoff for s in siblings))


def test_practice_completion_is_recorded_once():
    reset_db()
    pid = "fakepid_practice"
    check("a first-timer has no practice record",
          db.has_completed_practice(pid) is False)
    db.record_practice(pid)
    check("after the practice round it is recorded", db.has_completed_practice(pid))
    db.record_practice(pid)   # e.g. a second click — must not raise
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM practice_completions WHERE annotator_id=%s",
                    (pid,))
        n = cur.fetchone()[0]
    check("recording twice keeps exactly one row", n == 1)
    check("an unresolved identity never has a record and never hits the DB",
          db.has_completed_practice("") is False)


def test_reservation_stamps_batch_and_template():
    """The columns must stop being NULL — the export's whole model dimension
    hangs off them, and a placeholder row with no dims makes "which batches are
    still open" unanswerable."""
    reset_db()
    pid = "fakepid_stamp"
    playlist, err = assignment.build_playlist_for(pid)
    assert err is None and playlist, f"no batch: {err}"
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT game_slug, batch_id, template_id, model_id, domain, game, "
            "experiment, instance FROM annotations WHERE annotator_id=%s", (pid,))
        rows = cur.fetchall()
    check("every reserved row has all dimensions",
          rows and all(all(c is not None for c in r) for r in rows))
    ok = True
    for slug, bid, tpl, model, dom, game, exp, inst in rows:
        d = study_set.DIMENSIONS[slug]
        ok &= (bid == d["batch_id"] and tpl == d["template_id"]
               and model == d["model_id"] and dom == d["domain"]
               and game == d["game"] and exp == d["experiment"]
               and inst == d["instance"])
    check("stamped dimensions match the manifest", ok)
    check("all rows share one batch_id", len({r[1] for r in rows}) == 1)


def test_pick_batch_is_pure():
    """DB-free unit test of the selection rule, including the def-time-binding
    regression that made the OLD picker's target_seconds unpatchable."""
    import random
    BM = {"A__m": ("a1", "a2", "a3"), "B__m": ("b1", "b2"), "C__m": ("c1", "c2")}
    BT = {"A__m": "A", "B__m": "B", "C__m": "C"}
    # One instance key per batch, enough to exercise the exclusion.
    BI = {"A__m": {("g", "e", "A")}, "B__m": {("g", "e", "B")},
          "C__m": {("g", "e", "C")}}

    def pick(counts, **kw):
        return assignment._pick_batch(counts, 3, rng=random.Random(0),
                                      batch_members=BM, batch_template=BT,
                                      batch_instances=BI, **kw)

    p = pick({"a1": 2, "a2": 2, "a3": 2, "b1": 0, "b2": 0, "c1": 1, "c2": 1})
    check("least-covered batch wins", p.batch_id == "B__m")

    p = assignment._pick_batch(
        {"d1": 0, "d2": 0, "e1": 0, "e2": 3}, 3, rng=random.Random(0),
        batch_members={"D__m": ("d1", "d2"), "E__m": ("e1", "e2")},
        batch_template={"D__m": "D", "E__m": "E"},
        batch_instances={"D__m": set(), "E__m": set()})
    check("intact batch preferred over a holed one at the same level",
          p.batch_id == "D__m")

    zero = {k: 0 for k in ["a1", "a2", "a3", "b1", "b2", "c1", "c2"]}
    check("instance exclusion is honoured",
          pick(zero, exclude_instances={("g", "e", "A"),
                                        ("g", "e", "B")}).batch_id == "C__m")
    check("all instances excluded -> ANNOTATOR_EXHAUSTED",
          pick(zero, exclude_instances={("g", "e", "A"), ("g", "e", "B"),
                                        ("g", "e", "C")}).reason
          == assignment.ANNOTATOR_EXHAUSTED)
    check("one shared instance is enough to skip a whole batch",
          pick(zero, exclude_instances={("g", "e", "A")}).batch_id != "A__m")
    check("nothing under target -> NOTHING_OPEN",
          pick({k: 3 for k in zero}).reason == assignment.NOTHING_OPEN)
    check("only under-covered members are handed out",
          pick({"a1": 3, "a2": 0, "a3": 0, "b1": 3, "b2": 3,
                "c1": 3, "c2": 3}).slugs == ["a2", "a3"])

    # study_set is resolved at CALL time, so a reload/monkeypatch takes effect.
    saved_m, saved_t = study_set.BATCH_MEMBERS, study_set.BATCH_TEMPLATE
    try:
        study_set.BATCH_MEMBERS = {"Z__m": ("z1",)}
        study_set.BATCH_TEMPLATE = {"Z__m": "Z"}
        p = assignment._pick_batch({"z1": 0}, 3, rng=random.Random(0))
        check("study_set is read at call time, not bound at def time",
              p.batch_id == "Z__m")
    finally:
        study_set.BATCH_MEMBERS, study_set.BATCH_TEMPLATE = saved_m, saved_t


def test_template_exclusion_across_models():
    """One PID to their cap must see five DISTINCT templates. Batch-level
    exclusion alone would let them re-rate the same games under another model."""
    reset_db()
    pid = "fakepid_tpl"
    slugs = []
    for _ in range(assignment.MAX_BATCHES):
        playlist, err = assignment.build_playlist_for(pid)
        if err:
            break
        slugs += [i["game"] for i in playlist]
        complete_session(pid)
    templates = {study_set.DIMENSIONS[s]["template_id"] for s in slugs}
    batches = {study_set.DIMENSIONS[s]["batch_id"] for s in slugs}
    check(f"{assignment.MAX_BATCHES} sittings used "
          f"{assignment.MAX_BATCHES} distinct templates",
          len(templates) == assignment.MAX_BATCHES)
    check("one batch per template", len(batches) == len(templates))
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM (SELECT template_id FROM annotations "
            "WHERE annotator_id=%s AND template_id IS NOT NULL "
            "GROUP BY template_id HAVING COUNT(DISTINCT batch_id) > 1) x", (pid,))
        collisions = cur.fetchone()[0]
    check("no template appears under two batches in the DB", collisions == 0)


def test_partially_covered_batch_yields_only_needed_members():
    reset_db()
    probe, err = assignment.build_playlist_for("fakepid_probe")
    assert err is None and probe
    bid = study_set.batch_of(probe[0]["game"])
    members = list(study_set.BATCH_MEMBERS[bid])
    reset_db()
    retire_slugs(members[:2], "cover", n=assignment.COVERAGE_TARGET)

    pid = "fakepid_holed"
    playlist, err = assignment.build_playlist_for(pid)
    assert err is None and playlist
    got = [i["game"] for i in playlist]
    if study_set.batch_of(got[0]) == bid:
        check("retired members are not re-offered",
              not (set(got) & set(members[:2])))
        check("remaining members offered in position order",
              got == [m for m in members if m not in members[:2]])
    else:
        check("a holed batch is not preferred over intact ones", True)


def test_exhausted_is_distinct_from_no_tasks():
    """Work remains, but none of it is theirs. "Check back later" would be a
    lie, so the message must differ."""
    reset_db()
    pid = "fakepid_exhaust"
    orig = assignment.MAX_BATCHES
    try:
        assignment.MAX_BATCHES = 99          # let the instance rule, not the cap, stop them
        seen = 0
        while True:
            playlist, err = assignment.build_playlist_for(pid)
            if err:
                break
            seen += 1
            complete_session(pid)
        # Exclusion is per instance now, so one sitting can retire instances
        # that several later batches share. The exact count depends on the
        # packing; what must hold is that they run out of work of their own
        # while plenty is still open to other people.
        check("PID completed some sittings before running out",
              0 < seen < len(study_set.BATCH_MEMBERS))
        held = {(study_set.DIMENSIONS[t]["game"], study_set.DIMENSIONS[t]["experiment"],
                 study_set.DIMENSIONS[t]["instance"])
                for t in db.reserved_slugs(pid)}
        untouched = [b for b in study_set.BATCH_MEMBERS
                     if not (study_set.instances_of(b) & held)]
        check("every remaining batch shares an instance they have already rated",
              untouched == [])
        check("EXHAUSTED_MESSAGE, not NO_TASKS_MESSAGE",
              err == assignment.EXHAUSTED_MESSAGE)
        counts = db.coverage_counts(assignment.CONDITION)
        check("work genuinely remains for others",
              any(counts.get(s, 0) < assignment.COVERAGE_TARGET
                  for s in assignment.POOL_SLUGS))
    finally:
        assignment.MAX_BATCHES = orig


def test_preflight_and_pool_resolve_on_disk():
    check("preflight() is clean", assignment.preflight() == [])
    check("POOL_SLUGS is the whole study", len(assignment.POOL_SLUGS) == 416)


run("basic batch + idempotency", test_basic_batch_and_idempotency)
run("reservation stamps batch_id/template_id/dims", test_reservation_stamps_batch_and_template)
run("_pick_batch is pure and correct", test_pick_batch_is_pure)
run("template exclusion across models", test_template_exclusion_across_models)
run("partially covered batch yields only needed members", test_partially_covered_batch_yields_only_needed_members)
run("exhausted != no tasks", test_exhausted_is_distinct_from_no_tasks)
run("preflight + pool resolve on disk", test_preflight_and_pool_resolve_on_disk)
run("coverage balances across many sequential PIDs", test_coverage_balances_across_many_pids)
run("concurrent distinct PIDs stay within COVERAGE_TARGET", test_concurrent_distinct_pids_stay_within_target)
run("concurrent same-PID double-checked lock", test_concurrent_same_pid_no_double_reservation)
run("stale reservation frees its slot", test_stale_reservation_frees_its_slot)
run("pool exhaustion returns NO_TASKS_MESSAGE", test_pool_exhaustion_returns_no_tasks_message)
run("returning participant gets a new, disjoint batch", test_returning_participant_gets_a_new_disjoint_batch)
run("session cap blocks further work", test_session_cap_blocks_further_work)
run("half-finished session resumes and doesn't count", test_half_finished_session_does_not_count_toward_cap)
run("stale resume drops over-covered transcripts", test_stale_resume_drops_over_covered)
run("stale resume keeps started work", test_stale_resume_keeps_started_work)
run("voided session costs no cap slot", test_voided_session_costs_no_cap_slot)
run("session lease refreshes the whole batch", test_session_lease_refreshes_whole_batch)


reset_db()  # leave the test DB clean, it's shared across runs

print("\n" + "=" * 60)
if failures:
    print(f"❌ {len(failures)} check(s) failed:")
    for f in failures:
        print(f"   - {f}")
    raise SystemExit(1)
print("✅ all checks passed")


def test_never_rates_one_instance_twice():
    """The guarantee the whole batching design rests on.

    Batches mix models, so the same game instance sits in the pool four times
    under four transcripts. All four show an identical game state. A
    participant who rated one and is later handed another has not given a
    second independent judgement - they have re-scored a game they remember,
    and the study's three ratings collapse to two.

    The per-sitting check elsewhere only covers one batch. This walks a
    participant through every sitting they can get and checks the property
    across all of them.
    """
    reset_db()
    pid = "fakepid_no_repeat"
    orig = assignment.MAX_BATCHES
    try:
        assignment.MAX_BATCHES = 99          # go until the instance rule stops them
        seen, sittings = [], 0
        while True:
            playlist, err = assignment.build_playlist_for(pid)
            if err:
                break
            sittings += 1
            for item in playlist:
                d = study_set.DIMENSIONS[item["game"]]
                seen.append((d["game"], d["experiment"], d["instance"]))
            complete_session(pid)
    finally:
        assignment.MAX_BATCHES = orig

    check("the participant was given more than one sitting", sittings > 1)
    repeats = {k for k in seen if seen.count(k) > 1}
    check(f"no instance rated twice across {sittings} sittings "
          f"({len(seen)} transcripts)", not repeats)
    models = {study_set.DIMENSIONS[t]["model_id"]
              for t in db.reserved_slugs(pid)}
    check("and they saw several models, which is the point of mixing",
          len(models) > 1)


run("practice completion is recorded once", test_practice_completion_is_recorded_once)
run("never rates one instance twice", test_never_rates_one_instance_twice)
