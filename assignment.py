"""Coverage-balanced batch assignment for the Prolific general study.

A reservation is just a placeholder row in db.py's `annotations` table — its
UNIQUE(game_slug, annotator_id, condition) constraint is what makes the claim
stick under concurrent Prolific traffic. A sitting is one batch, sized by a
time budget rather than a fixed game count, holding a single game type so its
rules are read once."""

import random
from datetime import datetime, timedelta

import annotation
import db

COVERAGE_TARGET = 3     # independent annotators before a transcript is "covered"
STALE_AFTER_HOURS = 2   # abandoned (no verdict, no activity) reservations free up
CONDITION = "hybrid"    # the only condition the general study ever assigns

# Transcripts vary too much in length for a fixed game count to give even sessions.
TARGET_SECONDS = 20 * 60
# Allowed overshoot on the LAST added transcript — without slack the picker
# stops early and systematically under-fills every batch.
OVERSHOOT = 1.15
MIN_BATCH_GAMES = 1     # a single over-long transcript is still a valid sitting
MAX_BATCH_GAMES = 12    # guard against a pathological pile of 1-turn episodes

# How many sittings one Prolific participant may complete in total. A
# returning PID gets a fresh, disjoint batch each time until this is reached.
MAX_SESSIONS = 10

# est_seconds = SECONDS_PER_TURN * turns + chars / CHARS_PER_SECOND + SECONDS_PER_VERDICT,
# plus the rules cost once per batch (why a batch holds a single game type).

# Placeholder values — not yet calibrated against real timed durations.
SECONDS_PER_TURN = 25.0
CHARS_PER_SECOND = 20.0
SECONDS_PER_VERDICT = 60.0
SECONDS_PER_RULES_CAP = 180.0

# Transcripts to keep out of the general pool even though they're
# discoverable and their game-type has a bespoke set.
EXCLUDED_SLUGS = frozenset({
    # 58 AI turns — alone blows past a single session's time budget.
    "adventuregame__potion_brewing_basic_undefined__instance_00000",
})

NO_TASKS_MESSAGE = (
    "🙏 There are no annotation tasks available right now — every transcript "
    "has enough annotators at the moment. Thank you for your interest; "
    "please check back later."
)

CAP_MESSAGE = (
    f"🎉 Thank you — you've completed the maximum of {MAX_SESSIONS} sessions "
    f"for this study, so there's nothing further for you to do. We're very "
    f"grateful for your work."
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
    """ISO string in the same naive-local format every timestamp in this app
    uses (see db.coverage_counts) — mixing formats breaks the comparison."""
    return (datetime.now() - timedelta(hours=STALE_AFTER_HOURS)).isoformat()


_cost_cache = {}


def transcript_seconds(slug):
    """Estimated seconds of work for one transcript, cached per slug. Uses
    annotation.load_game so "what counts as a rateable turn" has one definition."""
    if slug not in _cost_cache:
        path = annotation.slug_to_path(slug)
        if path is None:
            # Unknown slug: cost 0 would make it look infinitely cheap and
            # flood every batch. Treat as a full session so the picker takes
            # it alone if it takes it at all — it should never be in the pool.
            return float(TARGET_SECONDS)
        g = annotation.load_game(path)
        chars = sum(len(str(m["action"].get("content", ""))) for m in g.ai_turns)
        _cost_cache[slug] = (SECONDS_PER_TURN * g.n_turns
                             + chars / CHARS_PER_SECOND
                             + SECONDS_PER_VERDICT)
    return _cost_cache[slug]


_rules_cache = {}


def rules_seconds(slug):
    """One-off cost of reading a game's rules, charged once per batch. Keyed
    by game TYPE (every transcript of a type shares the rules text), and
    capped: past a point participants skim rather than read linearly."""
    game_type = _game_type(slug)
    if game_type not in _rules_cache:
        path = annotation.slug_to_path(slug)
        if path is None:
            return 0.0
        rules = annotation.load_game(path).rules or ""
        _rules_cache[game_type] = min(SECONDS_PER_RULES_CAP,
                                      len(rules) / CHARS_PER_SECOND)
    return _rules_cache[game_type]


def _pick_batch(pool, counts, coverage_target, exclude=(), rng=None,
                cost_fn=None, rules_fn=None, target_seconds=TARGET_SECONDS):
    """Pure, DB-free selection (inject cost_fn/rules_fn to unit-test without a
    real DB) — one game type, least-covered transcripts first, filled by time
    budget rather than a fixed count. Returns [] only when nothing is eligible."""
    rng = rng or random.Random()
    cost_fn = cost_fn or transcript_seconds
    rules_fn = rules_fn or rules_seconds
    exclude = set(exclude)

    eligible = [s for s in pool
                if counts.get(s, 0) < coverage_target and s not in exclude]
    if not eligible:
        return []

    by_type = {}
    for s in eligible:
        by_type.setdefault(_game_type(s), []).append(s)

    # Pick the game type whose least-covered transcript needs coverage most; ties shuffled.
    types = list(by_type)
    rng.shuffle(types)
    types.sort(key=lambda t: min(counts.get(s, 0) for s in by_type[t]))
    chosen_type = types[0]

    # Least-covered transcripts of that type first, ties shuffled.
    candidates = by_type[chosen_type]
    rng.shuffle(candidates)
    candidates.sort(key=lambda s: counts.get(s, 0))

    budget = target_seconds * OVERSHOOT
    # rules_fn needs a real slug to resolve a path, so pass an actual transcript, not the type name.
    spent = rules_fn(by_type[chosen_type][0])
    picked = []
    for slug in candidates:
        if len(picked) >= MAX_BATCH_GAMES:
            break
        cost = cost_fn(slug)
        if len(picked) >= MIN_BATCH_GAMES and spent + cost > budget:
            # Keep scanning — a later, shorter transcript may still fit the budget.
            continue
        picked.append(slug)
        spent += cost
    return picked


def _resume_target(summary):
    """(session_to_resume, completed_count) from db.session_summary rows.
    session_to_resume is None once every sitting on file is complete."""
    unfinished = next((idx for idx, total, done in summary if done < total), None)
    completed = sum(1 for _, total, done in summary if done >= total and total)
    return unfinished, completed


def current_session_index(annotator_id, condition=CONDITION):
    """1-based index of the sitting this participant is currently in. Call it
    AFTER build_playlist_for so a freshly reserved batch is counted — used to
    gate the practice round to a participant's first sitting only."""
    summary = db.session_summary(annotator_id, condition=condition)
    resume_idx, _ = _resume_target(summary)
    if resume_idx is not None:
        return resume_idx
    return max((idx for idx, _, _ in summary), default=0) + 1


def build_playlist_for(annotator_id, condition=CONDITION):
    """Entry point for a Prolific PID. Returns (playlist, error_message).
    Resumes an unfinished sitting as-is — never re-picked, which could change
    the games under someone halfway through them."""
    summary = db.session_summary(annotator_id, condition=condition)
    resume_idx, completed = _resume_target(summary)
    if resume_idx is not None:
        return db.assigned_games(annotator_id, condition=condition,
                                 session_index=resume_idx), None
    if completed >= MAX_SESSIONS:
        return [], CAP_MESSAGE

    picked = None
    new_idx = (max((idx for idx, _, _ in summary), default=0) + 1)
    with db.write_transaction() as conn:
        # Re-check inside the lock — a concurrent request for the same PID
        # (two tabs on one link) could otherwise reserve two batches at once.
        summary = db.session_summary(annotator_id, condition=condition, conn=conn)
        resume_idx, completed = _resume_target(summary)
        if resume_idx is None and completed < MAX_SESSIONS:
            new_idx = max((idx for idx, _, _ in summary), default=0) + 1
            counts = db.coverage_counts(condition, stale_before=_stale_cutoff(), conn=conn)
            picked = _pick_batch(
                POOL_SLUGS, counts, COVERAGE_TARGET,
                # Never hand back a transcript this participant has already
                # been reserved in an earlier sitting — the no-repeat rule.
                exclude=db.reserved_slugs(annotator_id, conn=conn),
            )
            if picked:
                db.reserve_games(annotator_id, condition, picked,
                                 session_index=new_idx, conn=conn)

    if resume_idx is not None:
        return db.assigned_games(annotator_id, condition=condition,
                                 session_index=resume_idx), None
    if completed >= MAX_SESSIONS:
        return [], CAP_MESSAGE
    if not picked:
        return [], NO_TASKS_MESSAGE

    est = sum(transcript_seconds(s) for s in picked) + rules_seconds(picked[0])
    if est < TARGET_SECONDS / 2:
        # Just a log — flags an under-length sitting, usually a sign the pool is thin.
        print(f"⚠️ assignment: session {new_idx} for {annotator_id!r} is only "
              f"~{est / 60:.0f} min ({len(picked)} games) against a "
              f"{TARGET_SECONDS / 60:.0f} min target — pool is running low.")
    return [{"game": s, "condition": condition} for s in picked], None
