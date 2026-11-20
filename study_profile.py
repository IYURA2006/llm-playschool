"""Which study this process is serving: the main one, or the small pilot.

GAMES_DIR is the single switch. Everything else that has to agree with it -
the manifest, the batch file, how many annotators each transcript gets, how
many sittings one person may take - is looked up from it here, so a pilot
cannot be half-configured by changing one of them and forgetting another.

    GAMES_DIR=games_study   the 416-transcript study, 3 annotators each
    GAMES_DIR=games_pilot   the pilot built by build_pilot_set.py, 2 each

The pilot's transcripts are instances the main study does NOT use (the builder
refuses any that it does), so the two share no slugs and their rows can sit in
the same database without colliding. Pilot batch ids all start with
PILOT_PREFIX, which is how readiness.py and the export tell them apart.

No database and no gradio import: study_set, assignment, annotation and
readiness all read this, and two of those must stay importable off the VM.
"""

import collections
import os

from dotenv import load_dotenv

# db.py also does this, but this module is imported before db in some paths
# (study_set, readiness), and GAMES_DIR normally lives in .env.
load_dotenv()

Profile = collections.namedtuple(
    "Profile", "name games_dir manifest batches coverage_target max_batches")

MAIN = Profile("main", "games_study", "study_manifest.csv", "batches.csv",
               coverage_target=3, max_batches=8)
# max_batches equals the number of pilot batches (one per game), so a pilot
# annotator can take every sitting and assignment.preflight's "the cap must be
# reachable" rule still holds.
PILOT = Profile("pilot", "games_pilot", "pilot_manifest.csv", "pilot_batches.csv",
                coverage_target=2, max_batches=3)

PROFILES = {p.games_dir: p for p in (MAIN, PILOT)}
PILOT_PREFIX = "PILOT-"

GAMES_DIR = os.environ.get("GAMES_DIR", MAIN.games_dir).strip().rstrip("/")

if GAMES_DIR not in PROFILES:
    raise SystemExit(
        f"GAMES_DIR={GAMES_DIR!r} is not a corpus this app knows. Use "
        f"{' or '.join(sorted(PROFILES))} (set it in .env).")

ACTIVE = PROFILES[GAMES_DIR]
IS_PILOT = ACTIVE is PILOT
