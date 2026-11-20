#!/usr/bin/env python3
"""End-to-end test: real browsers doing whole sittings, then a check that the
database holds exactly what was clicked.

_test_assignment.py proves the assignment rules by calling the functions. This
proves the thing a participant actually touches: link, consent, practice, every
turn's ratings, the verdict, the completion link - and that it still works when
several people are doing it at once, which is where every bug this has found
so far lived (answers carried into the next game, a second answer overwriting
the first, a Save that did nothing).

    # 1. a copy of the app on a disposable database, on its own port
    GAMES_DIR=games_pilot DB_NAME=$TEST_DB_NAME PORT=3001 python app.py

    # 2. in another terminal
    python _test_e2e.py --participants 6             # six at once, then verify
    python _test_e2e.py --participants 6 --rounds 5  # five times (needs --reset)
    python _test_e2e.py --participants 2 --sittings 3   # the same two, 3 sittings each

Needs `pip install playwright && playwright install chromium`. That is a test
dependency only, so it is not in requirements.txt.

Every id it uses starts with "e2e-", which readiness.py already treats as a
test row and refuses to launch over.

AGAINST THE VM

    python _test_e2e.py --base https://breezy.inf.ed.ac.uk --participants 1 --no-verify

This writes real rows and takes one of a batch's annotator places until they
are deleted, so do it before recruiting, never during:

    DELETE FROM annotations WHERE annotator_id LIKE 'e2e-%';
    DELETE FROM consents WHERE annotator_id LIKE 'e2e-%';
    DELETE FROM practice_completions WHERE annotator_id LIKE 'e2e-%';

WHAT A FAILURE MEANS

  fatal            a participant could not get through: the message says where
  MISMATCH         an answer in the database is not the one that was clicked
  over coverage    a transcript was rated by more people than its target
  Traceback        look in the app's own log as well; this cannot see it
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time

LOCAL = ("http://127.0.0.1", "http://localhost")
TOP_CARD = ".turn-anno-card:not(.turn-anno-card .turn-anno-card)"
# Gradio writes "queue: 1/1 | 0.2s" and "processing | 1.2s" into a status
# element while an event is in flight. Those are not the app's messages.
_PROGRESS = re.compile(r"^\s*(queue:|processing|\d+(\.\d+)?\s*s\b)|\|\s*\d+(\.\d+)?s")


# ------------------------------------------------------------------ browser

def _visible_page(page):
    for pid in ("welcome-page", "train-page", "annot-page", "verdict-page"):
        el = page.locator(f"#{pid}")
        if el.count() and el.first.is_visible():
            return pid
    return None


def _rate_cards(page, root, rng, record):
    """Answer every question on every turn card, the way a person would: by
    clicking the visible label, never by setting the hidden input."""
    cards = page.locator(f"{root} {TOP_CARD}")
    chips = page.locator(f"{root} .tn-chip")
    for i in range(cards.count()):
        card = cards.nth(i)
        if not card.is_visible():
            chips.nth(i).click()
        card.wait_for(state="visible", timeout=8000)
        answers = []
        groups = card.locator(".scale-radio")
        for gi in range(groups.count()):
            grp = groups.nth(gi)
            if not grp.is_visible():
                continue
            opts = grp.locator("input[type=radio]")
            k = rng.randrange(opts.count())
            grp.locator("label").nth(k).click()
            if not opts.nth(k).is_checked():
                raise RuntimeError("a rating click did not register")
            answers.append(opts.nth(k).get_attribute("value"))
            page.wait_for_timeout(rng.randrange(60, 160))
        ticked = []
        flags = card.locator(".flags-check label")
        if flags.count() and rng.random() < 0.4:
            k = rng.randrange(flags.count())
            flags.nth(k).click()
            ticked.append(flags.nth(k).inner_text().strip())
        comment = ""
        box = card.locator(".turn-comment textarea, .turn-comment input")
        if box.count() and rng.random() < 0.3:
            comment = f"c{i}-{rng.randrange(10 ** 6)}"
            box.first.fill(comment)
            box.first.blur()
        record.append({"turn": i, "radios": answers, "flags": ticked,
                       "comment": comment})


def study_url(base, pid, sitting, with_submission=True):
    """The link Prolific sends: the participant, plus one SESSION_ID per
    submission. Each sitting here is its own submission, as in the study."""
    url = f"{base}/?PROLIFIC_PID={pid}"
    if with_submission:
        url += f"&STUDY_ID=e2e-study&SESSION_ID={submission_id(pid, sitting)}"
    return url


def submission_id(pid, sitting):
    return f"{pid}-sub{sitting}"


def reopen(browser, url):
    """Come back on the same link in a fresh browser, as someone does who
    closed the tab before clicking the completion link. Returns what they see."""
    page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_selector("text=Start Annotation", timeout=120000)
        # The page paints before the server has said who this is. Wait for the
        # answer itself - a banner, or a sitting on offer - not for a fixed
        # time, which under load reads the page before either has arrived.
        t0 = time.time()
        try:
            page.wait_for_function(
                "() => document.querySelector('#welcome-error') || "
                "((document.querySelector('.session-line') || {}).innerText || '')"
                ".trim().length > 0", timeout=30000)
        except Exception:
            return {"nothing_after_30s": True}
        waited = round(time.time() - t0, 1)
        banner = page.locator("#welcome-error")
        if banner.count():
            link = page.locator("#welcome-error a")
            return {"banner": banner.inner_text().strip(), "waited": waited,
                    "link": link.first.get_attribute("href") if link.count() else None}
        return {"new_sitting": page.locator(".session-line").inner_text().strip(),
                "waited": waited}
    finally:
        page.context.close()


def run_sitting(base, pid, sitting, seed, shots=None, with_submission=True):
    """One participant, one sitting. Returns a dict of everything it did."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    rng = random.Random(seed)
    out = {"pid": pid, "sitting": sitting, "games": [], "timings": {},
           "page_errors": 0,
           "submission": submission_id(pid, sitting) if with_submission else None}
    url = study_url(base, pid, sitting, with_submission)

    def shot(page, name):
        if shots:
            os.makedirs(shots, exist_ok=True)
            page.screenshot(path=os.path.join(shots, f"{pid}-s{sitting}-{name}.png"),
                            full_page=True)

    shown = ("() => [%s].some(id => { const e = document.getElementById(id); "
             "return e && e.getClientRects().length > 0; })")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            viewport={"width": 1440, "height": 900}).new_page()
        page.on("pageerror", lambda e: out.__setitem__(
            "page_errors", out["page_errors"] + 1))
        try:
            t0 = time.time()
            page.goto(url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_selector("text=Start Annotation", timeout=120000)
            page.wait_for_timeout(1500)
            out["timings"]["welcome"] = round(time.time() - t0, 1)
            shot(page, "1-welcome")

            banner = page.locator("#welcome-error")
            if banner.count():
                out["turned_away"] = banner.inner_text().strip()
                return out

            page.get_by_role("button", name="Start Annotation").click()
            t1 = time.time()
            page.wait_for_function(
                shown % "'consent-modal','train-page','annot-page'", timeout=120000)
            out["timings"]["start_to_next_screen"] = round(time.time() - t1, 1)
            if page.locator("#consent-modal").is_visible():
                out["consent_shown"] = True
                page.locator("#consent-modal input[type=checkbox]").check(force=True)
                page.locator("#consent-modal button.start-btn").click()
            page.wait_for_function(shown % "'train-page','annot-page'",
                                   timeout=120000)
            page.wait_for_timeout(800)

            if _visible_page(page) == "train-page":
                out["practice_shown"] = True
                _rate_cards(page, "#train-page", rng, [])
                page.get_by_role("button", name="Check my ratings").click()
                page.wait_for_timeout(1200)
                shot(page, "2-practice")
                page.get_by_role("button", name="Start real annotation").click()

            gi = 0
            while True:
                page.wait_for_selector("#annot-page .turn-anno-card",
                                       state="attached", timeout=120000)
                page.wait_for_timeout(1500)
                seq = page.locator("#annot-page .game-seq-tag")
                game = {
                    "seq": seq.first.inner_text() if seq.count() else "",
                    "game_id": page.locator("#annot-page .game-id-tag").first.inner_text(),
                    "turns": [],
                }
                if gi == 0:
                    shot(page, "3-turns")
                _rate_cards(page, "#annot-page", rng, game["turns"])
                page.get_by_role("button", name="Submit All").click()
                try:
                    page.wait_for_selector("#verdict-page", state="visible",
                                           timeout=60000)
                except PWTimeout:
                    out["games"].append(game)
                    out["fatal"] = (f"game {gi + 1}: Submit All did not reach "
                                    f"the verdict screen")
                    shot(page, "FAILED")
                    return out
                page.wait_for_timeout(1200)

                v = {"sliders": [], "specific": []}
                cohs = page.locator("#verdict-page .coh-sel-btn")
                if cohs.count() and cohs.first.is_visible():
                    k = rng.randrange(cohs.count())
                    cohs.nth(k).click()
                    v["coherence"] = str(k + 1)
                    page.wait_for_selector("#verdict-page .coh-col-sel",
                                           timeout=15000)
                sliders = page.locator("#verdict-page .ovr-slider input[type=range]")
                for si in range(sliders.count()):
                    s = sliders.nth(si)
                    if not s.is_visible():
                        continue
                    box = s.bounding_box()
                    frac = rng.choice([0.02, 0.2, 0.36, 0.5, 0.66, 0.82, 0.98])
                    page.mouse.click(box["x"] + box["width"] * frac,
                                     box["y"] + box["height"] / 2)
                    page.wait_for_timeout(150)
                    v["sliders"].append(s.input_value())
                groups = page.locator("#verdict-page .scale-radio")
                for ri in range(groups.count()):
                    grp = groups.nth(ri)
                    if not grp.is_visible():
                        continue
                    opts = grp.locator("input[type=radio]")
                    k = rng.randrange(opts.count())
                    grp.locator("label").nth(k).click()
                    page.wait_for_timeout(rng.randrange(60, 160))
                    v["specific"].append(opts.nth(k).get_attribute("value"))
                v["comment"] = f"verdict-{pid}-{sitting}-{gi}"
                page.locator("#verdict-page .verdict-comment textarea").fill(v["comment"])
                page.wait_for_timeout(400)
                if gi == 0:
                    shot(page, "4-verdict")
                game["verdict"] = v
                out["games"].append(game)
                page.locator("#verdict-page button.primary").last.click()

                outcome = None
                for _ in range(240):
                    page.wait_for_timeout(250)
                    st = page.locator("#verdict-status")
                    txt = st.inner_text() if st.count() and st.is_visible() else ""
                    if "completed all games" in txt:
                        outcome = "done"
                        break
                    if txt.strip() and not _PROGRESS.search(txt):
                        outcome = txt.strip()
                        break
                    if _visible_page(page) == "annot-page":
                        outcome = "next"
                        break
                if outcome == "done":
                    link = page.locator("#verdict-status a")
                    out["completion_link"] = (link.get_attribute("href")
                                              if link.count() else None)
                    out["timings"]["sitting"] = round(time.time() - t0, 1)
                    page.wait_for_timeout(1200)
                    shot(page, "5-complete")
                    # Twice: the second visit must change nothing either.
                    out["reopened"] = [reopen(browser, url), reopen(browser, url)]
                    return out
                if outcome != "next":
                    game.pop("verdict")           # it was not saved
                    out["fatal"] = (f"game {gi + 1}: after Save - "
                                    f"{outcome or 'nothing happened for 60s'}")
                    shot(page, "FAILED")
                    return out
                gi += 1
        except Exception as exc:                  # a timeout, a missing element
            out["fatal"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
            try:
                shot(page, "FAILED")
            except Exception:
                pass
            return out
        finally:
            browser.close()


# ----------------------------------------------------------------- database

def _connect(db_name):
    import psycopg2
    from dotenv import load_dotenv
    load_dotenv()
    return psycopg2.connect(
        host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
        dbname=db_name, user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
        gssencmode=os.environ.get("DB_GSSENCMODE", "disable"))


def reset(db_name):
    """Empty every table. Only ever called on the disposable test database."""
    conn = _connect(db_name)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM annotations")       # turn_ratings cascade
        for t in ("consents", "practice_completions", "question_sets"):
            cur.execute(f"DELETE FROM {t}")
    conn.close()


def verify(results, db_name, coverage_target):
    """Problems found comparing what was clicked with what is stored."""
    conn = _connect(db_name)
    cur = conn.cursor()
    problems, compared = [], 0

    def same(ok, msg):
        nonlocal compared
        compared += 1
        if not ok:
            problems.append("MISMATCH " + msg)

    for res in results:
        if "turned_away" in res:
            continue
        pid, sitting = res["pid"], res["sitting"]
        cur.execute(
            "SELECT id, game_slug, game_id, strategic_coherence, overall_rating, "
            "verdict_comment, verdict_specific, verdict_at, started_at, batch_id, "
            "model_id, question_set_hash FROM annotations WHERE annotator_id=%s "
            "AND COALESCE(session_index,1)=%s ORDER BY id", (pid, sitting))
        rows = cur.fetchall()
        saved = [g for g in res["games"] if "verdict" in g]
        same(sum(1 for r in rows if r[7]) == len(saved),
             f"{pid} sitting {sitting}: {len(saved)} verdicts saved in the "
             f"browser, {sum(1 for r in rows if r[7])} in the database")
        for k, g in enumerate(saved):
            m = re.search(r"Game (\d+) of", g["seq"])
            row = rows[int(m.group(1)) - 1] if m and int(m.group(1)) <= len(rows) \
                else (rows[k] if k < len(rows) else None)
            if row is None:
                problems.append(f"MISMATCH {pid}: game {k + 1} has no row")
                continue
            (aid, slug, game_id, coh, overall, vcomment, vspec, vat, started,
             batch, model, qhash) = row
            who = f"{pid} {slug}"
            v = g["verdict"]
            same(g["game_id"].lstrip("#") == str(game_id), f"{who}: wrong transcript")
            spec = json.loads(vspec) if vspec else {}
            extra_sliders = v["sliders"]
            if "coherence" in v:
                same(str(coh) == v["coherence"], f"{who}: G1 {v['coherence']} vs {coh}")
                same(str(overall) == v["sliders"][0],
                     f"{who}: G2 {v['sliders'][0]} vs {overall}")
                extra_sliders = v["sliders"][1:]
            same(sorted(spec.values()) == sorted(v["specific"] + extra_sliders),
                 f"{who}: game-specific {v['specific'] + extra_sliders} vs {spec}")
            same(vcomment == v["comment"], f"{who}: verdict comment")
            for val, name in ((vat, "verdict_at"), (started, "started_at"),
                              (batch, "batch_id"), (model, "model_id"),
                              (qhash, "question_set_hash")):
                same(val is not None, f"{who}: {name} is NULL")
            cur.execute(
                "SELECT prior_information_use, strategic_logic, reasoning_clarity, "
                "flags, comment, extra_responses FROM turn_ratings "
                "WHERE annotation_id=%s ORDER BY turn_index", (aid,))
            turns = cur.fetchall()
            same(len(turns) == len(g["turns"]),
                 f"{who}: {len(g['turns'])} turns rated, {len(turns)} stored")
            for t, (q1, q2, q3, flags, comment, extra) in zip(g["turns"], turns):
                extra = json.loads(extra) if extra else {}
                stored = [x for x in (q1, q2) if x is not None] \
                    + list(extra.values()) + ([q3] if q3 is not None else [])
                at = f"{who} turn {t['turn'] + 1}"
                same([str(x) for x in stored] == t["radios"],
                     f"{at}: clicked {t['radios']}, stored {stored}")
                same(json.loads(flags) == t["flags"], f"{at}: flags")
                # The one that catches an answer carried over from the
                # previous game: a comment stored that was never typed here.
                same((comment or "") == t["comment"],
                     f"{at}: comment typed {t['comment']!r}, stored {comment!r}")

    # One submission, one sitting: stamped on its rows, and reopening its link
    # afterwards shows the completion link without reserving anything.
    cur.execute("SELECT 1 FROM information_schema.columns WHERE "
                "table_name='annotations' AND column_name='prolific_session_id'")
    has_column = cur.fetchone() is not None
    finished = [r for r in results if "completion_link" in r and r.get("submission")]
    if finished and not has_column:
        print("    note: this database has no prolific_session_id column, so a "
              "reopened link starts a new sitting (the old behaviour)")
    for res in finished if has_column else []:
        pid, sitting, sub = res["pid"], res["sitting"], res["submission"]
        cur.execute("SELECT DISTINCT prolific_session_id FROM annotations WHERE "
                    "annotator_id=%s AND COALESCE(session_index,1)=%s", (pid, sitting))
        same([r[0] for r in cur.fetchall()] == [sub],
             f"{pid} sitting {sitting}: rows not stamped with submission {sub}")
        for n, seen in enumerate(res.get("reopened", []), 1):
            same("banner" in seen and seen.get("link") == res["completion_link"],
                 f"{pid} sitting {sitting}: reopening the finished link (visit "
                 f"{n}) did not show the completion link - saw {seen}")
    for pid in {r["pid"] for r in finished} if has_column else ():
        mine = [r for r in results if r["pid"] == pid and "turned_away" not in r]
        cur.execute("SELECT COUNT(DISTINCT COALESCE(session_index,1)) FROM "
                    "annotations WHERE annotator_id=%s", (pid,))
        on_file = cur.fetchone()[0]
        same(on_file == len(mine),
             f"{pid}: did {len(mine)} sitting(s) but the database holds "
             f"{on_file} - reopening a finished link reserved another")

    cur.execute("SELECT game_slug, COUNT(DISTINCT annotator_id) FROM annotations "
                "WHERE verdict_at IS NOT NULL GROUP BY 1 HAVING "
                "COUNT(DISTINCT annotator_id) > %s", (coverage_target,))
    for slug, n in cur.fetchall():
        problems.append(f"over coverage: {slug} has {n} (target {coverage_target})")
    cur.execute("SELECT COUNT(*) FROM annotations a WHERE verdict_at IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM turn_ratings t "
                "WHERE t.annotation_id=a.id)")
    orphans = cur.fetchone()[0]
    if orphans:
        problems.append(f"{orphans} verdict(s) with no turn ratings")
    conn.close()
    return compared, problems


# ------------------------------------------------------------------- runner

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:3001")
    ap.add_argument("--participants", type=int, default=1,
                    help="how many work at the same time")
    ap.add_argument("--sittings", type=int, default=1,
                    help="sittings each one does, one after another")
    ap.add_argument("--rounds", type=int, default=1,
                    help="repeat the whole thing; needs --reset between rounds")
    ap.add_argument("--reset", action="store_true",
                    help="empty the test database before each round")
    ap.add_argument("--no-session-id", action="store_true",
                    help="links carry no SESSION_ID, as for people who are "
                         "not on Prolific")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the database comparison (e.g. against the VM)")
    ap.add_argument("--db-name", default=os.environ.get("TEST_DB_NAME"))
    ap.add_argument("--shots", help="directory for screenshots of each screen")
    ap.add_argument("--seed", type=int, default=int(time.time()) % 100000)
    ap.add_argument("--worker", nargs=4, metavar=("PID", "SITTING", "SEED", "OUT"),
                    help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.worker:
        pid, sitting, seed, out = args.worker
        res = run_sitting(args.base, pid, int(sitting), int(seed), args.shots,
                          with_submission=not args.no_session_id)
        with open(out, "w") as fh:
            json.dump(res, fh)
        return 0

    local = args.base.startswith(LOCAL)
    use_db = not args.no_verify
    if use_db or args.reset:
        if not local:
            sys.exit("Not comparing against a remote database from here. Pass "
                     "--no-verify, and check the rows on the VM with "
                     "export_annotations.py --check.")
        if not args.db_name:
            from dotenv import load_dotenv
            load_dotenv()
            args.db_name = os.environ.get("TEST_DB_NAME")
        if not args.db_name or args.db_name == "study":
            sys.exit("Set TEST_DB_NAME to the disposable database the test app "
                     "is running on. This will not read or reset `study`.")
    if args.rounds > 1 and not args.reset:
        sys.exit("--rounds needs --reset: a second round on a database the "
                 "first one filled is turned away at the door.")

    os.environ.setdefault("SKIP_DB_INIT", "1")
    import study_profile
    target = study_profile.ACTIVE.coverage_target
    print(f"{args.base}   corpus {study_profile.GAMES_DIR} (set GAMES_DIR to "
          f"match the app under test)   seed {args.seed}")

    failed = False
    for rnd in range(1, args.rounds + 1):
        if args.reset:
            reset(args.db_name)
        stamp = f"{int(time.time()) % 100000}-{rnd}"
        results = []
        for sitting in range(1, args.sittings + 1):
            procs = []
            for i in range(1, args.participants + 1):
                out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
                cmd = [sys.executable, os.path.abspath(__file__), "--base", args.base,
                       "--worker", f"e2e-{stamp}-{i}", str(sitting),
                       str(args.seed * 1000 + rnd * 100 + sitting * 10 + i), out]
                if args.shots:
                    cmd += ["--shots", args.shots]
                if args.no_session_id:
                    cmd += ["--no-session-id"]
                procs.append((subprocess.Popen(cmd), out))
            for proc, out in procs:
                proc.wait()
                try:
                    with open(out) as fh:
                        results.append(json.load(fh))
                    os.unlink(out)
                except Exception:
                    results.append({"pid": "?", "sitting": sitting, "games": [],
                                    "fatal": "the browser process died"})

        done = [r for r in results if "completion_link" in r]
        away = [r for r in results if "turned_away" in r]
        fatal = [r for r in results if "fatal" in r]
        games = sum(len(r["games"]) for r in results)
        print(f"\nround {rnd}: {len(results)} sittings - {len(done)} completed, "
              f"{len(away)} turned away, {len(fatal)} FAILED - {games} transcripts")
        for r in fatal:
            print(f"  FAILED {r['pid']} sitting {r['sitting']}: {r['fatal']}")
        for msg in sorted({r["turned_away"][:110] for r in away}):
            print(f"  turned away with: {msg}")
        waits = [v["waited"] for r in results for v in r.get("reopened", [])
                 if "waited" in v]
        if waits:
            print(f"  reopened links: {len(waits)}, slowest answer {max(waits):.1f}s")
        slow = [r["timings"].get("start_to_next_screen", 0) for r in results]
        if slow:
            print(f"  slowest Start -> next screen: {max(slow):.1f}s")
        failed = failed or bool(fatal)

        if use_db:
            compared, problems = verify(results, args.db_name, target)
            print(f"  database: {compared} answers compared, "
                  f"{len(problems)} problem(s)")
            for p in problems[:20]:
                print("   ", p)
            failed = failed or bool(problems)

    print("\nFAILED" if failed else "\nall good")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
