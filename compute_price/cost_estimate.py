
"""

cd compute_price

# O1 — 8 IDs
python3 cost_estimate.py --archive lm-playschool-2026-final-results.zip --option 1 --instances 8 --annotators 1
python3 cost_estimate.py --archive lm-playschool-2026-final-results.zip --option 1 --instances 8 --annotators 2

# O1 — 13 IDs
python3 cost_estimate.py --archive lm-playschool-2026-final-results.zip --option 1 --instances 13 --annotators 1
python3 cost_estimate.py --archive lm-playschool-2026-final-results.zip --option 1 --instances 13 --annotators 2

# O2 — 13 IDs
python3 cost_estimate.py --archive lm-playschool-2026-final-results.zip --option 2 --instances 13 --annotators 1
python3 cost_estimate.py --archive lm-playschool-2026-final-results.zip --option 2 --instances 13 --annotators 2
python3 cost_estimate.py --archive lm-playschool-2026-final-results.zip --option 2 --instances 13 --annotators 3

# O3 — all 17 games
python3 cost_estimate.py --archive lm-playschool-2026-final-results.zip --option 3 --instances 4 --annotators 1
python3 cost_estimate.py --archive lm-playschool-2026-final-results.zip --option 3 --instances 4 --annotators 2
python3 cost_estimate.py --archive lm-playschool-2026-final-results.zip --option 3 --instances 5 --annotators 1
python3 cost_estimate.py --archive lm-playschool-2026-final-results.zip --option 3 --instances 5 --annotators 2


WHAT IT ACTUALLY COMPUTES, STEP BY STEP
-----------------------------------------
1. It opens the archive and, for every game, counts how many episodes ALL
   FOUR models managed to finish without aborting. We call this the
   "shared completed" count — it's the real ceiling on how much genuine
   data exists for that game.
2. For each game, it compares what you asked for (--instances) against
   that real ceiling, and uses whichever is smaller. This is the important
   bit: if you ask for 10 instances of a game that only has 4 real
   completed episodes, you get costed for 4, not 10. You can never end up
   with a cost estimate based on data that doesn't exist.
3. It turns that instance count into minutes: instances used, times how
   long that particular game takes per instance, times your number of
   annotators, times your number of models. Add that up across every game
   in your chosen option, and you have total annotation minutes.
4. From there it's just working out the practical logistics and the money:
   how many 20-minute sessions that adds up to, how many different people
   you'd actually need (since each person does a few sessions), a one-off
   onboarding cost per person, and then the actual payment: participant
   pay, Prolific's cut, and UK VAT on Prolific's cut. Add those three
   together and that's your final number, in pounds.

WHERE THE FIXED NUMBERS CAME FROM
------------------------------------
The pay rate, Prolific's fee and VAT come from the "Assumptions" tab of
prolific_cost_final_with_abort_1analysis_rerun.xlsx.

Minutes per instance do NOT. For the 8 study games they were measured with a
stopwatch on 2026-09-03 and are the same figures batch_plan.json is sized on,
so the cost estimate and the study shape cannot drift apart. The spreadsheet's
own column was roughly half the measured value on the heavier games - it put
Codenames at 2.0 minutes for a transcript with 12.6 turns and 24.8 rating
questions, which is not reachable. The 9 games outside the study still carry
the spreadsheet figures and are marked "~" in the output.
"""

from __future__ import annotations

import argparse
import json
import math
import zipfile
from pathlib import Path


# ============================================================
# PART 1 — The fixed numbers.
#
# Nothing in this section changes when you run the script with different
# flags. It's all copied straight from the spreadsheet: how long each game
# takes to annotate, and the pay/fee/tax rates. Think of this as the
# "settings" the rest of the script works from.
# ============================================================

# The four models being compared. A game only counts as "shared completed"
# for an episode if every single one of these four finished it.
MODEL_LABELS = {
    "qwen3.5-27b": "Qwen 3.5 27B",
    "LLP: Large Language Problems__qwen3.5-9b__llp-final": "LLP 9B (llp-final)",
    "DAIR__qwen3.5-2b__sft-dpo-v2": "DAIR 2B (sft-dpo-v2)",
    "CityUoL__qwen3.5-2b__Qwen-GuidePlay-2B-v1": "CityUoL 2B (GuidePlay v1)",
}

# Every game, its display name, and how many minutes it takes an annotator
# to do ONE instance of it.
#
# The 8 study games carry MEASURED minutes: a real annotator worked through one
# transcript per game with a stopwatch (2026-09-03), plus 30 seconds each for a
# participant meeting the game for the first time rather than the person who
# wrote the questions. These are the figures batch_plan.json is sized on, so the
# cost estimate and the study shape cannot drift apart.
#
# The other 9 games keep their original spreadsheet figures and are NOT
# measured. Nothing in the study uses them; --option 3 mixes measured and
# unmeasured, so treat its total as indicative only.
GAMES = {
    # measured
    "codenames":               ("Codenames",           6.5),
    "dond":                    ("Deal or No Deal",     4.5),
    "imagegame":               ("ImageGame",           4.5),
    "privateshared":           ("PrivateShared",       3.5),
    "wordle-crazy_withclue":   ("WCrazy w/ clue",      3.5),
    "guesswhat":               ("GuessWhat",           2.5),
    "referencegame":           ("ReferenceGame",       2.5),
    "ta_frozen_lake":          ("Frozen Lake",         2.5),
    # not measured — original spreadsheet values
    "eqbench":                 ("EQBench",             1.0),
    "adventuregame":           ("AdventureGame",       4.0),
    "clean_up":                ("Clean Up",            3.0),
    "wordle":                  ("Wordle",              2.5),
    "wordle_withclue":         ("Wordle w/ clue",      2.5),
    "st_clean_up":             ("ST Clean Up",         3.5),
    "ta_mastermind":           ("Mastermind",          2.0),
    "wordle-crazy":            ("Wordle Crazy",        2.5),
    "toh_multi_turn":          ("Tower of Hanoi",      2.0),
}

# Games whose figure came from a stopwatch rather than the spreadsheet.
MEASURED = {"codenames", "dond", "imagegame", "privateshared",
            "wordle-crazy_withclue", "guesswhat", "referencegame",
            "ta_frozen_lake"}

# Three named ways of picking a smaller set of games instead of all 17.
# Option 3 just means "every game" and isn't listed here explicitly — the
# code below falls back to the full GAMES list whenever --option isn't 1 or 2.
# Options 1 and 2 were confirmed directly by the project owner (they're not
# derived from anything in the data, so if these ever need to change, this
# is the one place to edit).
OPTIONS = {
    1: ["referencegame", "guesswhat", "privateshared", "imagegame",
        "dond", "codenames", "ta_frozen_lake", "wordle-crazy_withclue"],
    2: ["guesswhat", "dond", "ta_frozen_lake", "wordle-crazy_withclue"],
}

# These are the three "zone" folders that actually exist inside the
# archive. Anything outside these three is ignored.
ZONES = {"static", "clem_indomain", "clem_outofdomain"}

# The pay and fee numbers, copied from the "Assumptions" tab (column B,
# the row number is noted alongside each one so you can cross-check).
PLATFORM_FEE_RATE = 0.333   # row 6  — Prolific's cut, academic/non-profit rate
UK_VAT_RATE = 0.20          # row 7  — UK VAT, charged on Prolific's fee only
APPLIES_VAT = 1             # row 8  — 1 because we're a UK-based team
HOURLY_RATE = 13.45         # row 9  — what each participant is paid, per hour
ONBOARDING_MIN = 5          # row 13 — one-off setup time per person, not per session
# The advertised length of a sitting, and now the real one: batch_plan.json is
# sized so every batch lands between 17 and 24 minutes of measured work.
SESSION_LENGTH_MIN = 20
# Was 3, from the spreadsheet. assignment.MAX_BATCHES is 5, and that is what
# actually limits a participant, so the headcount here was 60% too high.
SESSIONS_PER_ANNOTATOR = 5  # = assignment.MAX_BATCHES


# ============================================================
# PART 2 — Reading the real data.
#
# This is the part that actually opens the archive and works out, honestly,
# how much real usable data exists for each game. Nothing here involves
# your command-line choices yet — it's just "what do we actually have?"
# ============================================================

def number(value, default=None):
    """Safely turn something into a number, or fall back to a default if it
    isn't one. Some fields in the raw score files are occasionally missing
    or malformed, so this just avoids the whole script crashing on that."""
    try:
        converted = float(value)
        return default if math.isnan(converted) else converted
    except (TypeError, ValueError):
        return default


def selected_member(parts: list[str]) -> bool:
    """Is this file inside the archive actually one we care about? Every
    file we want lives at a path shaped like:
        zone / model / game / variation / episode / scores.json
    This just checks that shape, and that the model and game are ones
    we're tracking."""
    return (
        len(parts) == 7
        and parts[1] in ZONES
        and parts[2] in MODEL_LABELS
        and parts[3] in GAMES
        and parts[6] == "scores.json"
    )


def shared_completed_counts(archive_path: Path) -> dict[str, int]:
    """For every game, count how many individual episodes were completed
    (not aborted) by ALL FOUR models on that exact same episode. This is
    the real, honest ceiling — it's not how many episodes exist in total,
    it's how many exist where we can genuinely compare all four models
    side by side."""
    by_game_instance: dict[str, dict[tuple[str, str], set[str]]] = {
        game_id: {} for game_id in GAMES
    }

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        for name in names:
            parts = name.split("/")
            if not selected_member(parts):
                continue
            _, _zone, model_id, game_id, variation, episode, _ = parts
            scores = json.loads(archive.read(name))
            aborted = int(number(scores.get("episode scores", {}).get("Aborted"), 1))
            if aborted == 0:
                # Not aborted — record that this particular model finished
                # this particular episode of this particular game.
                instance_key = (variation, episode)
                by_game_instance[game_id].setdefault(instance_key, set()).add(model_id)

    # Now go back through and only count an episode if every one of the
    # four models is present for it — a partial finish (say, 3 out of 4
    # models) doesn't count, since we need all four to make a fair comparison.
    counts = {}
    for game_id, instances in by_game_instance.items():
        counts[game_id] = sum(
            1 for models in instances.values() if models == set(MODEL_LABELS)
        )
    return counts


# ============================================================
# PART 3 — Turning real data + your choices into a price.
#
# Everything above this point was just "what data do we actually have?"
# This is where your command-line choices (how many instances, how many
# annotators, how many models, which option) finally get used.
# ============================================================

def estimate_cost(shared_completed: dict[str, int], desired_instances: int,
                   annotators: int, models: int, option: int | None) -> dict:
    # Work out which games are actually in play for this run.
    game_ids = OPTIONS[option] if option in OPTIONS else list(GAMES.keys())

    per_game = []
    total_minutes = 0.0
    for game_id in game_ids:
        display_name, min_per_inst = GAMES[game_id]
        available = shared_completed.get(game_id, 0)

        # This is the one line that matters most in the whole script:
        # never annotate more instances than actually, genuinely exist.
        instances_used = min(desired_instances, available)

        minutes = instances_used * min_per_inst * annotators * models
        total_minutes += minutes
        per_game.append({
            "game": display_name,
            "measured": game_id in MEASURED,
            "shared_completed": available,
            "instances_used": instances_used,
            "min_per_inst": min_per_inst,
            "annotation_minutes": minutes,
        })

    # From total minutes of work, to the practical shape of the study:
    # how many sittings, how many actual people, and what it costs.
    sessions = math.ceil(total_minutes / SESSION_LENGTH_MIN)
    unique_annotators = math.ceil(sessions / SESSIONS_PER_ANNOTATOR)
    onboarding_minutes = unique_annotators * ONBOARDING_MIN
    rewards = (total_minutes + onboarding_minutes) / 60 * HOURLY_RATE
    fee = rewards * PLATFORM_FEE_RATE
    vat = fee * UK_VAT_RATE * APPLIES_VAT
    total_cost = rewards + fee + vat

    return {
        "per_game": per_game,
        "total_annotation_minutes": total_minutes,
        "sessions": sessions,
        "unique_annotators": unique_annotators,
        "onboarding_minutes": onboarding_minutes,
        "participant_rewards": rewards,
        "prolific_fee": fee,
        "vat_on_fee": vat,
        "total_cost": total_cost,
    }


# ============================================================
# PART 4 — The command line: how a person actually runs this
# and sees the answer.
# ============================================================

# The archive lives beside this script, so both of these are resolved from
# the script's own location rather than the current directory. That is the
# difference between "python3 cost_estimate.py" working from anywhere and
# only working if you happen to have cd'd into this folder first.
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ARCHIVE_NAME = "lm-playschool-2026-final-results.zip"


def find_archive(given: Path | None) -> Path:
    """Work out which zip to read, and fail with something useful if we can't.

    With no --archive, the expected filename next to the script wins; failing
    that, a single zip sitting there is taken to be the one you meant. If a
    path IS given it is tried as typed first, so an ordinary relative path
    still behaves normally, and only then against the script's folder — which
    is what someone means when they type a bare filename from elsewhere.
    """
    if given is not None:
        for candidate in (given, SCRIPT_DIR / given):
            if candidate.exists():
                return candidate
        raise SystemExit(f"Couldn't find that archive: {given}")

    default = SCRIPT_DIR / DEFAULT_ARCHIVE_NAME
    if default.exists():
        return default

    zips = sorted(SCRIPT_DIR.glob("*.zip"))
    if len(zips) == 1:
        return zips[0]
    if not zips:
        raise SystemExit(
            f"No results archive found next to this script.\n"
            f"Expected: {default}\n"
            f"Put the zip there, or point at it with --archive <path>.")
    listed = "\n  ".join(z.name for z in zips)
    raise SystemExit(
        f"More than one zip sits next to this script, so I won't guess:\n"
        f"  {listed}\n"
        f"Pick one, e.g. --archive {zips[0].name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", type=Path, default=None,
                         help="Path to the results archive zip. Optional — by default the "
                              f"script reads {DEFAULT_ARCHIVE_NAME} from its own folder")
    parser.add_argument("--option", type=int, choices=[1, 2, 3], default=3,
                         help="Which set of games: 1 (8 games), 2 (4 games), or 3 (all 17 — the default)")
    parser.add_argument("--instances", type=int, default=10,
                         help="How many instances per game you'd LIKE to annotate (default: 10) — "
                              "this gets capped automatically per game if that much real data doesn't exist")
    parser.add_argument("--annotators", type=int, default=2,
                         help="How many annotators look at each transcript (default: 2)")
    parser.add_argument("--models", type=int, default=4,
                         help="How many models are being compared (default: 4)")
    args = parser.parse_args()

    archive = find_archive(args.archive)

    shared = shared_completed_counts(archive)
    result = estimate_cost(shared, args.instances, args.annotators, args.models, args.option)

    print(f"\nArchive: {archive.name}")
    print(f"Option {args.option} | you asked for {args.instances} instances/game, "
          f"{args.annotators} annotators/transcript, {args.models} models\n")
    print(f"{'Game':<20} {'Real data':>10} {'Used':>6} {'Min/inst':>9} {'Minutes':>9}")
    for row in result["per_game"]:
        mark = " " if row["measured"] else "~"
        print(f"{row['game']:<20} {row['shared_completed']:>10} {row['instances_used']:>6} "
              f"{row['min_per_inst']:>8}{mark} {row['annotation_minutes']:>9.0f}")
    if any(not r["measured"] for r in result["per_game"]):
        print("\n~ = minutes not measured; spreadsheet estimate, treat as indicative")

    print()
    print(f"Total annotation minutes : {result['total_annotation_minutes']:.0f}")
    print(f"Sessions                 : {result['sessions']}")
    print(f"Unique annotators needed : {result['unique_annotators']}")
    print(f"Onboarding minutes       : {result['onboarding_minutes']}")
    print(f"Participant rewards (£)  : {result['participant_rewards']:.2f}")
    print(f"Prolific fee (£)         : {result['prolific_fee']:.2f}")
    print(f"VAT on fee (£)           : {result['vat_on_fee']:.2f}")
    print(f"TOTAL COST (£)           : {result['total_cost']:.2f}")


if __name__ == "__main__":
    main()
