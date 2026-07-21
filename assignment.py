"""Coverage-balanced batch assignment for the Prolific general study.

Replaces the pilot's hand-edited assignments.json for anonymous, unknown
participants. Every reservation is written straight into db.py's
`annotations` table (no separate assignments table) — a placeholder row IS
the claim, and the existing UNIQUE(game_slug, annotator_id, condition)
constraint plus db.write_transaction() is what keeps concurrent Prolific
traffic from over-assigning a transcript.
"""

import random
from datetime import datetime, timedelta

import annotation
import db

BATCH_SIZE = 4          # games per participant sitting
COVERAGE_TARGET = 3     # independent annotators before a transcript is "covered"
STALE_AFTER_HOURS = 2   # abandoned (no verdict, no activity) reservations free up
CONDITION = "hybrid"    # the only condition the general study ever assigns

# Transcripts to keep out of the general pool even though they're
# discoverable and their game-type has a bespoke set. Empty until the study
# coordinator explicitly decides otherwise (e.g. holding back the pilot's
# confirmed-bug transcripts, or games/taboo/low_en).
EXCLUDED_SLUGS = frozenset()

NO_TASKS_MESSAGE = (
    "🙏 There are no annotation tasks available right now — every transcript "
    "has enough annotators at the moment. Thank you for your interest; "
    "please check back later."
)


def _pool_slugs():
    bespoke_types = set(annotation.BESPOKE_QUESTIONS)
    return tuple(sorted(
        annotation.game_slug(path) for _, path in annotation.GAMES
        if annotation.game_slug(path).split("__", 1)[0] in bespoke_types
        and annotation.game_slug(path) not in EXCLUDED_SLUGS
    ))


POOL_SLUGS = _pool_slugs()  # computed once at import, like annotation.GAMES


def _game_type(slug):
    return slug.split("__", 1)[0]


def _stale_cutoff():
    """ISO string in the same datetime.now().isoformat() format every other
    timestamp in this app uses — see db.coverage_counts' docstring for why
    this must never be computed via SQLite's datetime('now')."""
    return (datetime.now() - timedelta(hours=STALE_AFTER_HOURS)).isoformat()


def _pick_batch(pool, counts, coverage_target, batch_size, rng=None):
    """Pure, DB-free selection (unit-testable without SQLite). Least-covered
    slugs first; ties broken by shuffling (never a fixed alphabetical order
    — at study start nearly everything ties at 0, so a stable sort would
    always fill the same handful of transcripts/types first, which is the
    opposite of coverage-balancing); at most one slug per game TYPE per
    batch while any not-yet-used-this-batch type still has eligible slugs
    (mirrors the pilot's "4 different task types" Day-1 pattern and keeps
    one participant's batch from lopsidedly landing on the deepest pool),
    falling back to repeats only once every eligible type has contributed
    one pick. Simulates the count increments a pick causes so later picks in
    the SAME batch see an up-to-date picture. Returns a list shorter than
    batch_size (even []) if the pool is exhausted — callers must handle
    that, not treat it as an error."""
    rng = rng or random.Random()
    eligible = [s for s in pool if counts.get(s, 0) < coverage_target]
    if not eligible:
        return []

    by_type = {}
    for s in eligible:
        by_type.setdefault(_game_type(s), []).append(s)

    local = dict(counts)
    picked, used_types = [], set()

    def pick_from(candidates):
        min_c = min(local.get(s, 0) for s in candidates)
        tier = [s for s in candidates if local.get(s, 0) == min_c]
        rng.shuffle(tier)
        return tier[0]

    # Pass 1: one slug per type, cycling types by ascending min-count.
    while len(picked) < batch_size:
        remaining = [t for t, slugs in by_type.items()
                     if t not in used_types
                     and any(local.get(s, 0) < coverage_target for s in slugs)]
        if not remaining:
            break
        rng.shuffle(remaining)  # break type-count ties randomly too
        remaining.sort(key=lambda t: min(local.get(s, 0) for s in by_type[t]
                                          if local.get(s, 0) < coverage_target))
        chosen_type = remaining[0]
        candidates = [s for s in by_type[chosen_type] if local.get(s, 0) < coverage_target]
        slug = pick_from(candidates)
        picked.append(slug)
        used_types.add(chosen_type)
        local[slug] = local.get(slug, 0) + 1

    # Pass 2: batch still short (more slots than distinct eligible types
    # left) — allow repeats, same least-covered + shuffle-tie logic.
    while len(picked) < batch_size:
        candidates = [s for s in eligible
                      if local.get(s, 0) < coverage_target and s not in picked]
        if not candidates:
            break
        slug = pick_from(candidates)
        picked.append(slug)
        local[slug] = local.get(slug, 0) + 1

    return picked


def build_playlist_for(annotator_id, condition=CONDITION):
    """Idempotent entry point: a PID that already has reservations under
    `condition` always gets that SAME playlist back (never re-picked).
    Otherwise picks + reserves a fresh coverage-balanced batch.
    Returns (playlist, error_message) — playlist is `[]` and error_message
    is set (NO_TASKS_MESSAGE) when the pool is exhausted; otherwise
    error_message is None."""
    existing = db.assigned_games(annotator_id, condition=condition)
    if existing:
        return existing, None

    picked = None
    with db.write_transaction() as conn:
        # Re-check INSIDE the write lock: a concurrent request for the SAME
        # brand-new PID (double page-load, two tabs on the same Prolific
        # link) may have committed a reservation between the check above
        # and acquiring this lock. Without this re-check, both requests
        # would independently pick+reserve a full batch, leaving the PID
        # with 2x batch_size reserved games.
        existing = db.assigned_games(annotator_id, condition=condition, conn=conn)
        if not existing:
            counts = db.coverage_counts(condition, stale_before=_stale_cutoff(), conn=conn)
            picked = _pick_batch(POOL_SLUGS, counts, COVERAGE_TARGET, BATCH_SIZE)
            if picked:
                db.reserve_games(annotator_id, condition, picked, conn=conn)

    if existing:
        return existing, None
    if not picked:
        return [], NO_TASKS_MESSAGE

    db.backup_db_to_hf_async()
    if len(picked) < BATCH_SIZE:
        # Non-blocking: the participant still gets a valid (shorter) session
        # and the existing "you've completed everything" flow handles a
        # short playlist correctly with zero extra code. Logged so the
        # study coordinator notices the pool is running low, matching
        # db.py's "loud, never silent" convention for degraded states.
        print(f"⚠️ assignment: only {len(picked)}/{BATCH_SIZE} games available "
              f"for {annotator_id!r} — pool is nearly exhausted.")
    return [{"game": s, "condition": condition} for s in picked], None
