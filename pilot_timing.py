#!/usr/bin/env python3
"""Measure how long a transcript really takes, and rebuild the budget from it.

The cost estimate rests on one soft number: minutes per transcript. Everything
else is counted. This times you annotating one transcript per game in the real
app, then recomputes the study cost from what it measured.

    python pilot_timing.py --links                    # 8 links, one per game
    python pilot_timing.py --results --pid PILOT-1    # read the timings back

HOW TO RUN THE PILOT

1. On the VM, switch the debug link back on for the duration:

       printf 'ALLOW_DEBUG_LINKS=1\\n' >> .env && bash vm/setup_vm.sh

2. `--links` prints one link per game. Open them ONE AT A TIME. For each:
   click Start, rate every turn, submit the verdict, then go back for the next.
   Reloading between games is what gives each transcript its own clock -
   started_at is stamped per session, verdict_at per transcript.

3. Do not skip the practice round on the first one, and do not rush. The point
   is a number you can defend, not a fast time.

4. `--results` prints measured against modelled minutes, and the rebuilt budget.

5. Afterwards, remove the flag and delete the pilot rows:

       sed -i '/^ALLOW_DEBUG_LINKS=/d' .env && bash vm/setup_vm.sh
       psql "..." -c "DELETE FROM annotations WHERE annotator_id LIKE 'PILOT%';"

TWO ADJUSTMENTS, AND WHY

--buffer   Seconds to add per transcript. You know the games; a participant
           meeting one for the first time does not. 30s is a reasonable start.
--slowdown Multiplier on top. You built the question set, so you read it faster
           than anyone else ever will. 1.2-1.4 is the usual range for
           expert-versus-naive on a rating task; the default is 1.0 so the raw
           measurement is never hidden.

Both are applied to YOUR time. Report the raw figure alongside the adjusted one
whenever the number matters.
"""

import argparse
import collections
import csv
import os
import statistics
import sys

BASE = os.environ.get("PILOT_BASE_URL", "https://breezy.inf.ed.ac.uk")
RATE = 13.45          # GBP per hour, from the study assumptions
FEE = 0.333           # Prolific platform fee
VAT = 0.20            # UK VAT, charged on the fee only
ONBOARD_MIN = 5       # one-off per participant
COVERAGE = 3          # annotators per transcript
MAX_BATCHES = 5       # sittings one participant may complete


def _modelled():
    """Modelled minutes per transcript, per game — the figures to check.

    Kept here rather than imported so the pilot can be run against a checkout
    where the estimate has already moved on, and still say what it was testing.
    """
    return {"codenames": 8.4, "imagegame": 5.6, "dond": 5.4, "privateshared": 4.9,
            "guesswhat": 4.2, "ta_frozen_lake": 3.2, "wordle-crazy_withclue": 3.1,
            "referencegame": 2.8}


def _median_transcript_per_game():
    """One transcript per game, the one closest to that game's median turn
    count. Testing the shortest transcript would flatter the estimate."""
    import annotation

    rows = list(csv.DictReader(open("study_manifest.csv")))
    turns, by_game = {}, collections.defaultdict(list)
    for r in rows:
        n = annotation.load_game(r["source_path"]).n_turns
        turns[r["transcript_id"]] = n
        by_game[r["game"]].append(r)

    picked = {}
    for game, rs in by_game.items():
        med = statistics.median(turns[r["transcript_id"]] for r in rs)
        best = min(rs, key=lambda r: (abs(turns[r["transcript_id"]] - med),
                                      r["transcript_id"]))
        picked[game] = (best, turns[best["transcript_id"]], med)
    return picked


def cmd_links(args):
    import annotation

    picked = _median_transcript_per_game()
    print(f"Open one at a time, in any order. Base: {BASE}\n")
    for game in sorted(picked):
        r, n, med = picked[game]
        # transcript_id IS the slug under games_study. Deriving one from
        # source_path instead gives a path relative to games_final/, which the
        # app cannot resolve.
        slug = r["transcript_id"]
        if annotation.slug_to_path(slug) is None:
            sys.exit(f"{slug} does not resolve under GAMES_DIR="
                     f"{os.environ.get('GAMES_DIR', 'games')}. Set "
                     f"GAMES_DIR=games_study.")
        print(f"{game}  ({n} turns; this game's median is {med:.0f})")
        print(f"  {BASE}/?__theme=dark&annotator={args.pid}"
              f"&block=hybrid&game={slug}\n")
    print(f"{len(picked)} transcripts. Expect roughly "
          f"{sum(_modelled().values()):.0f} minutes in total if the model holds.")
    return 0


def _measured(pid):
    """{game: [minutes, ...]} from the database, one entry per finished row."""
    import export_annotations as export

    conn = export.open_readonly()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT game, started_at, verdict_at FROM annotations "
            "WHERE annotator_id LIKE %s AND verdict_at IS NOT NULL "
            "AND started_at IS NOT NULL ORDER BY verdict_at",
            (pid + "%",))
        rows = cur.fetchall()
    finally:
        conn.close()

    out = collections.defaultdict(list)
    for game, started, verdict in rows:
        minutes = (verdict - started).total_seconds() / 60.0
        if minutes > 0:
            out[game].append(minutes)
    return out


def cmd_results(args):
    measured = _measured(args.pid)
    if not measured:
        sys.exit(f"No finished rows for annotator ids starting {args.pid!r}. "
                 f"Rate a transcript and submit its verdict first.")

    model = _modelled()
    print(f"Adjustments: +{args.buffer:.0f}s per transcript, "
          f"x{args.slowdown} slowdown\n")
    print(f"{'game':24} {'yours':>7} {'adjusted':>9} {'modelled':>9} "
          f"{'ratio':>7}  n")
    adjusted = {}
    for game in sorted(set(model) | set(measured)):
        if game not in measured:
            print(f"{game:24} {'-':>7} {'-':>9} {model[game]:9.1f} "
                  f"{'not run':>7}")
            continue
        raw = statistics.mean(measured[game])
        adj = raw * args.slowdown + args.buffer / 60.0
        adjusted[game] = adj
        m = model.get(game)
        ratio = f"{adj / m:.2f}x" if m else "-"
        print(f"{game:24} {raw:7.1f} {adj:9.1f} {m if m else 0:9.1f} "
              f"{ratio:>7}  {len(measured[game])}")

    if len(adjusted) < len(model):
        missing = sorted(set(model) - set(adjusted))
        print(f"\n{len(missing)} game(s) not yet run: {', '.join(missing)}")
        print("Filling those from the model, so the total below is a mix.")
    per_game = {g: adjusted.get(g, model[g]) for g in model}

    n_tx = 52          # transcripts per game in the study set
    work_h = sum(v * n_tx for v in per_game.values()) * COVERAGE / 60.0
    print(f"\nTotal annotator work: {work_h:.0f} hours "
          f"({work_h / (52 * len(model) * COVERAGE) * 60:.1f} min per transcript)")

    batches = 104
    sittings = batches * COVERAGE
    people = -(-sittings // MAX_BATCHES)
    print(f"\n{'advertised':>12} {'paid':>9} {'rewards':>9} {'fee':>8} "
          f"{'VAT':>7} {'TOTAL':>9}")
    for advertised in (15, 20, 25, 30):
        paid = sittings * advertised + people * ONBOARD_MIN
        rewards = paid / 60.0 * RATE
        fee = rewards * FEE
        vat = fee * VAT
        print(f"{advertised:10} min {paid:9.0f} {rewards:9.0f} {fee:8.0f} "
              f"{vat:7.0f} {rewards + fee + vat:9,.0f}")

    honest = work_h * 60 / sittings
    print(f"\nMeasured work is {honest:.0f} min per sitting on average, so "
          f"advertise {5 * round(honest / 5):.0f} min.")
    print("Advertising below the measured figure underpays participants and "
          "biases who finishes.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", default="PILOT-1",
                    help="annotator id to use and to read back (default PILOT-1)")
    ap.add_argument("--links", action="store_true", help="print the pilot links")
    ap.add_argument("--results", action="store_true",
                    help="read the timings back and rebuild the budget")
    ap.add_argument("--buffer", type=float, default=30.0,
                    help="seconds to add per transcript (default 30)")
    ap.add_argument("--slowdown", type=float, default=1.0,
                    help="multiplier for naive versus expert (default 1.0)")
    args = ap.parse_args(argv)

    if args.links:
        return cmd_links(args)
    if args.results:
        return cmd_results(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
