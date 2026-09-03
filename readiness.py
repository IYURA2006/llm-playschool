#!/usr/bin/env python3
"""Is the study ready to recruit? One command, one answer.

    python readiness.py              # go / no-go
    python readiness.py --verbose    # show the checks that passed too

Exits 0 only if every check that ran either passed or is a warning. Any BLOCKER
exits 1.

Runs anywhere. Static checks always run. Database checks run only when the
credentials in .env actually connect, and report SKIPPED otherwise - never as
passed, because "we could not look" is not the same as "it is fine".

The checks here are the ones that a person cannot be relied on to remember at
the moment of launch. Several of them exist because the thing they check for
was actually wrong at some point: the practice screen named the wrong game for
weeks, the debug link stayed reachable, the completion code is still a
placeholder.
"""

import os

# Both must be set before anything imports db.py, which connects at import.
# Without the first, a static check would need a database; without the second,
# an unreachable host hangs the whole run on the TCP timeout instead of
# reporting SKIPPED.
os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("PGCONNECT_TIMEOUT", "5")

import argparse
import re
import subprocess
import sys

BLOCKER, WARN, OK, SKIP = "BLOCKER", "WARN", "OK", "SKIP"
_results = []


def record(group, name, status, detail=""):
    _results.append((group, name, status, detail))


def _run(cmd):
    """A subprocess, returning (rc, combined output)."""
    env = {**os.environ, "GAMES_DIR": os.environ.get("GAMES_DIR", "games_study")}
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------- study set

def check_study_set():
    g = "study set"
    try:
        import study_set
        study_set.validate()
        record(g, "batches match the manifest", OK,
               f"{len(study_set.BATCH_MEMBERS)} batches")
    except SystemExit as exc:
        record(g, "batches match the manifest", BLOCKER, str(exc).strip()[:300])
        return
    except Exception as exc:
        record(g, "batches match the manifest", BLOCKER,
               f"{type(exc).__name__}: {exc}")
        return

    try:
        import assignment
        assignment.preflight()
        record(g, "every batched transcript exists on disk", OK)
    except SystemExit as exc:
        record(g, "every batched transcript exists on disk", BLOCKER,
               str(exc).strip()[:300])
    except Exception as exc:
        record(g, "every batched transcript exists on disk", BLOCKER,
               f"{type(exc).__name__}: {exc}")

    import csv
    import collections
    man = {r["transcript_id"] for r in csv.DictReader(open("study_manifest.csv"))}
    rows = list(csv.DictReader(open("batches.csv")))
    ids = [r["transcript_id"] for r in rows]
    missing, extra = man - set(ids), set(ids) - man
    dupes = [t for t, n in collections.Counter(ids).items() if n > 1]
    if missing or extra or dupes:
        record(g, "every transcript batched exactly once", BLOCKER,
               f"{len(missing)} missing, {len(extra)} unknown, {len(dupes)} duplicated")
    else:
        record(g, "every transcript batched exactly once", OK, f"{len(ids)} transcripts")

    # An annotator must never meet the same game state twice in one sitting.
    per = collections.defaultdict(list)
    for r in rows:
        per[r["batch_id"]].append((r["experiment"], r["instance"]))
    repeats = [b for b, keys in per.items() if len(set(keys)) != len(keys)]
    record(g, "no instance repeated inside a batch",
           BLOCKER if repeats else OK,
           f"{len(repeats)} batch(es) repeat an instance" if repeats else "")


def check_sitting_lengths():
    """Sittings should be near the advertised 20 minutes.

    A flat per-game average, deliberately cruder than the real sizing, which
    scales each transcript by its turn count. A batch of three long DonD
    transcripts reads 13.5 here and 17.9 when scaled properly. The band is wide
    enough to absorb that: the job is to catch gross drift - someone
    regenerating batches.csv with the wrong figures - not to re-derive the plan.
    """
    g = "sitting length"
    import csv
    import collections
    import statistics
    MEASURED = {"codenames": 6.5, "dond": 4.5, "imagegame": 4.5,
                "privateshared": 3.5, "wordle-crazy_withclue": 3.5,
                "guesswhat": 2.5, "referencegame": 2.5, "ta_frozen_lake": 2.5}
    tot = collections.defaultdict(float)
    for r in csv.DictReader(open("batches.csv")):
        if r["game"] not in MEASURED:
            record(g, "every game has a measured time", BLOCKER,
                   f"no measured minutes for {r['game']}")
            return
        tot[r["game"] + "|" + r["batch_id"]] += MEASURED[r["game"]]
    mins = list(tot.values())
    out = [m for m in mins if not 12 <= m <= 28]
    med = statistics.median(mins)
    record(g, "sittings are near 20 minutes",
           WARN if out else OK,
           f"{len(out)} of {len(mins)} outside 12-28 min on a flat estimate "
           f"(median {med:.0f})" if out
           else f"{len(mins)} batches, median {med:.0f} min (flat estimate)")


# ---------------------------------------------------------------- questions

def check_questions():
    g = "questions"
    rc, out = _run([sys.executable, "questions.py", "--check"])
    m = re.search(r"(\d+) error", out)
    errors = int(m.group(1)) if m else -1
    record(g, "question set validates",
           OK if rc == 0 and errors == 0 else BLOCKER,
           "" if errors == 0 else f"{errors} error(s); run questions.py --check")

    rc, out = _run([sys.executable, "questions.py", "--markdown"])
    if rc != 0:
        record(g, "question_set.md is current", BLOCKER, "generator failed")
        return
    on_disk = open("question_set.md", encoding="utf-8").read()
    record(g, "question_set.md is current",
           OK if out == on_disk else WARN,
           "" if out == on_disk else
           "stale - regenerate with questions.py --markdown > question_set.md")


# ------------------------------------------------------- participant path

def check_participant_path():
    g = "participant path"

    src = open("annotation_verdict.py", encoding="utf-8").read()
    m = re.search(r'PROLIFIC_COMPLETION_URL\s*=\s*["\']([^"\']+)', src)
    url = m.group(1) if m else ""
    placeholder = "C10WMMGK"
    todo = re.search(r"#\s*TODO[^\n]*\n\s*PROLIFIC_COMPLETION_URL", src)
    if not url:
        detail = "not found"
    elif placeholder in url or todo:
        detail = ("still the placeholder code - participants will finish the "
                  "work and not be marked complete, so they cannot be paid")
    else:
        detail = ""
    record(g, "Prolific completion code is real",
           OK if not detail else BLOCKER, detail)

    # How a participant leaves the study, and what each ending owes them.
    #
    # Finished work in THIS submission -> must show the completion link, or
    # they cannot be paid. It is otherwise rendered only on the screen shown
    # straight after the final verdict, so closing that tab is a dead end.
    #
    # No work in this submission (cap reached, nothing left for them, nothing
    # open) -> must tell them to RETURN it. A completion link there would claim
    # payment for work not done in that submission.
    finished = [("app.py", r"already completed all"),
                ("welcome.py", r"already completed all")]
    no_work = [("assignment.py", r"CAP_MESSAGE = \("),
               ("assignment.py", r"EXHAUSTED_MESSAGE = \("),
               ("assignment.py", r"NO_TASKS_MESSAGE = \(")]

    def _near(f, pat, span=700):
        body = open(f, encoding="utf-8").read()
        return [(body[:m.start()].count("\n") + 1, body[m.start():m.start() + span])
                for m in re.finditer(pat, body)]

    bad = []
    for f, pat in finished:
        for line, near in _near(f, pat):
            if "COMPLETION_URL" not in near:
                bad.append(f"{f}:{line} (no completion link)")
    record(g, "a finished participant can always reach the completion link",
           BLOCKER if bad else OK,
           "; ".join(bad) + " - closing the tab or reloading would leave them "
           "unable to claim payment" if bad else "")

    vague = []
    for f, pat in no_work:
        for line, near in _near(f, pat):
            if "return" not in near.lower():
                vague.append(f"{f}:{line}")
    record(g, "endings with no work tell the participant to return it",
           WARN if vague else OK,
           "; ".join(vague) + " - says neither complete nor return, so they "
           "will sit on an open submission" if vague else "")

    dbg = os.environ.get("ALLOW_DEBUG_LINKS", "").strip().lower()
    record(g, "debug links are off",
           BLOCKER if dbg in {"1", "true", "yes"} else OK,
           "ALLOW_DEBUG_LINKS is set - the Prolific gate is bypassed and rows "
           "can be written under any id in the URL" if dbg else "")

    games_dir = os.environ.get("GAMES_DIR", "games")
    record(g, "GAMES_DIR is games_study",
           OK if games_dir == "games_study" else BLOCKER,
           "" if games_dir == "games_study" else
           f"GAMES_DIR={games_dir!r} - the study would run on the wrong corpus")

    for var, why in (("PORT", "Apache proxies to 3000; a mismatch is a 503"),
                     ("GRADIO_SERVER_NAME",
                      "0.0.0.0 would publish the app over plain HTTP")):
        record(g, f"{var} is unset", WARN if os.environ.get(var) else OK,
               f"{var}={os.environ[var]!r} - {why}" if os.environ.get(var) else "")

    # The practice screen named the wrong game and turn count for weeks, so it
    # is checked against what training.py actually serves rather than trusted.
    try:
        import training
        import annotation
        name = training._PRACTICE_NAME
        turns = len(training._REFERENCE)
        real_game = annotation.load_game(training.TRAINING_GAME)
        blurb = open("training.py", encoding="utf-8").read()
        ok = (f"{{_PRACTICE_NAME}}" in blurb or name.lower() in blurb.lower()) \
            and "len(_REFERENCE)" in blurb
        record(g, "practice screen describes the real practice game",
               OK if ok else BLOCKER,
               "" if ok else "the blurb hardcodes a game name or turn count")
        record(g, "practice game loads",
               OK if real_game.n_turns else BLOCKER,
               f"{name}, {turns} rated turns")
    except Exception as exc:
        record(g, "practice round loads", BLOCKER, f"{type(exc).__name__}: {exc}")


# ----------------------------------------------------------------- database

def check_database():
    g = "database"
    try:
        import export_annotations as export
        conn = export.open_readonly()
    except SystemExit as exc:
        record(g, "reachable", SKIP, str(exc).strip()[:160])
        return
    except Exception as exc:
        record(g, "reachable", SKIP, f"{type(exc).__name__}: {str(exc).strip()[:140]}")
        return

    try:
        cur = conn.cursor()
        record(g, "reachable", OK)

        import db as dbmod
        need = {"annotations", "turn_ratings", "consents",
                "practice_completions", "question_sets"}
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        have = {r[0] for r in cur.fetchall()}
        record(g, "all tables present",
               OK if need <= have else BLOCKER,
               "" if need <= have else f"missing {sorted(need - have)}")

        for table, cols in dbmod._REQUIRED_COLUMNS.items():
            if table not in have:
                continue
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name=%s", (table,))
            got = {r[0] for r in cur.fetchall()}
            miss = [c for c in cols if c not in got]
            record(g, f"{table} has its later columns",
                   OK if not miss else BLOCKER,
                   "" if not miss else f"missing {miss} - run postgres_schema.sql")

        bad = []
        for t in sorted(need & have):
            cur.execute("SELECT has_table_privilege(current_user,%s,'INSERT'), "
                        "has_table_privilege(current_user,%s,'UPDATE')", (t, t))
            ins, upd = cur.fetchone()
            if not (ins and upd):
                bad.append(t)
        record(g, "the app can write to every table",
               OK if not bad else BLOCKER,
               "" if not bad else
               f"no INSERT/UPDATE on {bad} - run postgres_grants.sql as an admin")

        if "annotations" in have:
            cur.execute("SELECT count(*) FROM annotations")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM annotations WHERE annotator_id ILIKE ANY"
                "(ARRAY['PILOT%%','ZZTEST%%','%%test%%','fakepid%%','e2e%%'])")
            junk = cur.fetchone()[0]
            record(g, "no test rows left behind",
                   BLOCKER if junk else OK,
                   f"{junk} row(s) from testing - delete them before recruiting"
                   if junk else "")
            record(g, "database is empty before launch",
                   WARN if total else OK,
                   f"{total} annotation row(s) already present" if total else "")
    finally:
        conn.close()


# ------------------------------------------------------------------- deploy

def check_deploy():
    g = "deploy"
    rc, out = _run(["git", "rev-list", "--left-right", "--count",
                    "origin/main...HEAD"])
    if rc != 0:
        record(g, "local matches origin/main", SKIP, "not a git checkout")
        return
    try:
        behind, ahead = (int(x) for x in out.split())
    except ValueError:
        record(g, "local matches origin/main", SKIP, out.strip()[:120])
        return
    # This cannot see breezy. It catches "forgot to push", not "forgot to
    # deploy" - the VM pulls published main, so pushing is necessary but not
    # sufficient. Warning, not a blocker, for that reason.
    record(g, "local matches origin/main", WARN if (ahead or behind) else OK,
           f"{ahead} ahead, {behind} behind - the VM serves published main, so "
           f"unpushed work is not live" if (ahead or behind) else
           "still redeploy on the VM to pick it up")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true",
                    help="list the checks that passed as well")
    args = ap.parse_args(argv)

    for fn in (check_study_set, check_sitting_lengths, check_questions,
               check_participant_path, check_database, check_deploy):
        try:
            fn()
        except Exception as exc:                       # pragma: no cover
            record(fn.__name__, "check itself failed", BLOCKER,
                   f"{type(exc).__name__}: {exc}")

    width = max(len(n) for _, n, _, _ in _results) + 2
    group = None
    for grp, name, status, detail in _results:
        if status == OK and not args.verbose:
            continue
        if grp != group:
            print(f"\n{grp.upper()}")
            group = grp
        mark = {BLOCKER: "FAIL", WARN: "warn", OK: "ok  ", SKIP: "skip"}[status]
        print(f"  [{mark}] {name:<{width}} {detail}")

    blockers = [r for r in _results if r[2] == BLOCKER]
    warns = [r for r in _results if r[2] == WARN]
    skips = [r for r in _results if r[2] == SKIP]
    passed = [r for r in _results if r[2] == OK]

    print("\n" + "=" * 66)
    print(f"{len(passed)} passed, {len(blockers)} blocking, "
          f"{len(warns)} warning, {len(skips)} skipped")
    if blockers:
        print("\nNOT READY. Blocking:")
        for _, name, _, detail in blockers:
            print(f"  - {name}{': ' + detail if detail else ''}")
        return 1
    if skips:
        print("\nReady, as far as the checks that ran. Skipped checks were not "
              "verified:")
        for _, name, _, detail in skips:
            print(f"  - {name}{': ' + detail if detail else ''}")
    else:
        print("\nREADY.")
    if warns:
        print("\nWorth looking at:")
        for _, name, _, detail in warns:
            print(f"  - {name}{': ' + detail if detail else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
