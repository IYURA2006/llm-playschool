"""Coverage-balanced batch assignment for the Prolific general study.

Replaces the pilot's hand-edited assignments.json for anonymous, unknown
participants. Every reservation is written straight into db.py's
`annotations` table (no separate assignments table) — a placeholder row IS
the claim, and the existing UNIQUE(game_slug, annotator_id, condition)
constraint plus db.write_transaction() is what keeps concurrent Prolific
traffic from over-assigning a transcript.

A participant may return for up to MAX_SESSIONS sittings. Each sitting is
one batch, sized by a TIME budget rather than a fixed game count, and holds
a single game type so its rules are read once and amortised. Batches are
disjoint per participant — nobody is ever handed a transcript they have
already seen. There is no `batches` table: a batch exists only as the set of
rows sharing (annotator_id, session_index), which is what lets the whole
resume/idempotency story fall out of the constraint that was already there.
"""

import random
from datetime import datetime, timedelta

import annotation
import db

COVERAGE_TARGET = 3     # independent annotators before a transcript is "covered"
STALE_AFTER_HOURS = 2   # abandoned (no verdict, no activity) reservations free up
CONDITION = "hybrid"    # the only condition the general study ever assigns

# ── Session budget ────────────────────────────────────────────────────────
# A sitting is sized by TIME, not by a fixed game count: transcripts differ by
# an order of magnitude in length (a 2-turn taboo episode vs. a 58-turn
# adventuregame one), so any fixed BATCH_SIZE produces sessions ranging from a
# few minutes to well over an hour. The picker fills a batch until the
# estimated total reaches TARGET_SECONDS.
TARGET_SECONDS = 20 * 60
# Allowed overshoot on the LAST added transcript — without slack the picker
# stops early and systematically under-fills every batch.
OVERSHOOT = 1.15
MIN_BATCH_GAMES = 1     # a single over-long transcript is still a valid sitting
MAX_BATCH_GAMES = 12    # guard against a pathological pile of 1-turn episodes

# How many sittings one Prolific participant may complete in total. A
# returning PID gets a fresh, disjoint batch each time until this is reached.
MAX_SESSIONS = 10

# ── Time model ────────────────────────────────────────────────────────────
# est_seconds(transcript) = SECONDS_PER_TURN * rateable_turns
#                         + response_chars / CHARS_PER_SECOND
#                         + SECONDS_PER_VERDICT
# plus, ONCE per batch, the cost of reading that game's rules — which is
# exactly why a batch holds a single game type: the rules are read once and
# amortised over every transcript in the sitting.
#
# PLACEHOLDER VALUES. They are the right shape but not yet calibrated: nobody
# has been timed on this task. Fit them against real per-game durations
# (verdict_at − started_at) once the study has data, rather than trusting the
# batch sizes these produce.
SECONDS_PER_TURN = 25.0
CHARS_PER_SECOND = 20.0
SECONDS_PER_VERDICT = 60.0
SECONDS_PER_RULES_CAP = 180.0

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
    """ISO string in the same datetime.now().isoformat() format every other
    timestamp in this app uses — see db.coverage_counts' docstring for why
    this must never be computed via SQLite's datetime('now')."""
    return (datetime.now() - timedelta(hours=STALE_AFTER_HOURS)).isoformat()


_cost_cache = {}


def transcript_seconds(slug):
    """Estimated minutes-of-work for one transcript, in seconds.

    Derived from annotation.load_game rather than a private re-parse, so
    "what counts as a rateable turn" has exactly one definition — the time
    model must be based on the turns the participant is actually asked to
    rate, and load_game is what decides that (it drops the GM, programmatic
    players, and clean_up's parse-error retry duplicates).

    Cached per slug: the picker asks about every eligible transcript on every
    assignment, and load_game parses (and lru_caches) the whole JSON."""
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
    """Pure, DB-free selection (unit-testable without SQLite or real
    transcripts — inject `cost_fn`/`rules_fn`). Returns the slugs for ONE
    sitting.

    Three rules, in priority order:

    1. ONE GAME TYPE per batch. The game's rules are read once and amortised
       over the whole sitting, which is what lets a 20-minute session hold ten
       short taboo episodes but only one long adventuregame one. It also means
       `rules_fn` is charged once, not per transcript.
    2. TIME, not count, decides how many fit. Transcripts vary by an order of
       magnitude in length, so a fixed batch size produces wildly uneven
       sittings. Fill until adding the next one would exceed
       target_seconds * OVERSHOOT.
    3. Least-covered first, ties shuffled — never a stable alphabetical order.
       At study start nearly everything ties at 0 coverage, and a stable sort
       would keep handing out the same few transcripts, which is the opposite
       of coverage-balancing.

    `exclude` is the set of slugs this participant has already been reserved
    in an earlier sitting; they are dropped before anything else, which is
    what enforces "never annotate the same transcript twice".

    Always returns at least MIN_BATCH_GAMES transcripts when any are eligible
    — a single transcript longer than the whole budget is still a valid
    sitting, and returning [] there would wrongly report the pool exhausted.
    Returns [] only when nothing is eligible at all; callers must treat that
    as "no work available", not an error."""
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

    # Pick the game TYPE first: the one whose least-covered transcript is
    # furthest from the target, so coverage still drives selection even though
    # a batch can no longer span types. Ties shuffled before the sort, so
    # equally-uncovered types rotate instead of always resolving the same way.
    types = list(by_type)
    rng.shuffle(types)
    types.sort(key=lambda t: min(counts.get(s, 0) for s in by_type[t]))
    chosen_type = types[0]

    # Least-covered transcripts of that type first, ties shuffled.
    candidates = by_type[chosen_type]
    rng.shuffle(candidates)
    candidates.sort(key=lambda s: counts.get(s, 0))

    budget = target_seconds * OVERSHOOT
    # Rules cost is charged once, from a representative transcript of the
    # chosen type — rules_fn needs a REAL slug to resolve a path, so it must
    # not be handed a synthesised "<type>__" string (that resolves to no path
    # and would silently score the rules as free).
    spent = rules_fn(by_type[chosen_type][0])
    picked = []
    for slug in candidates:
        if len(picked) >= MAX_BATCH_GAMES:
            break
        cost = cost_fn(slug)
        if len(picked) >= MIN_BATCH_GAMES and spent + cost > budget:
            # Keep scanning rather than break: a later, shorter transcript of
            # the same type may still fit. Stopping at the first over-budget
            # one would systematically under-fill every sitting whose
            # least-covered transcript happens to be a long one.
            continue
        picked.append(slug)
        spent += cost
    return picked


def _resume_target(summary):
    """(session_to_resume, completed_count) from db.session_summary rows.

    A sitting is unfinished while any reserved game in it lacks a verdict, and
    a participant may only ever have one unfinished sitting (a new batch is
    reserved only when the previous one is fully verdicted). Returns
    session_to_resume=None when every sitting on file is complete, meaning the
    next visit should start a new one."""
    unfinished = next((idx for idx, total, done in summary if done < total), None)
    completed = sum(1 for _, total, done in summary if done >= total and total)
    return unfinished, completed


def current_session_index(annotator_id, condition=CONDITION):
    """1-based index of the sitting this participant is currently in — the
    unfinished one if there is one, otherwise the next one they would start.
    Call it AFTER build_playlist_for so a freshly reserved batch is counted.

    Used to decide whether the practice round is shown: it belongs on a
    participant's first sitting only, and a returning PID has already done
    it. Deliberately a separate read rather than an extra return value on
    build_playlist_for, whose (playlist, error) contract is relied on by
    app.py and the tests."""
    summary = db.session_summary(annotator_id, condition=condition)
    resume_idx, _ = _resume_target(summary)
    if resume_idx is not None:
        return resume_idx
    return max((idx for idx, _, _ in summary), default=0) + 1


def build_playlist_for(annotator_id, condition=CONDITION):
    """Entry point for a Prolific PID. Returns (playlist, error_message).

    Three outcomes:

    - RESUME — the participant has an unfinished sitting, so they get that
      exact playlist back, never a re-picked one. Re-picking would defeat the
      reservation-as-claim mechanism and could change the games under someone
      who is halfway through them.
    - NEW SESSION — every sitting on file is complete and they are under
      MAX_SESSIONS, so a fresh batch is picked and reserved under the next
      session index, disjoint from everything they have already seen.
    - NOTHING — playlist is [] and error_message is set: CAP_MESSAGE once
      MAX_SESSIONS sittings are complete, NO_TASKS_MESSAGE when the pool has
      no transcript left that this participant may still annotate.

    error_message is None on the first two."""
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
        # Re-check INSIDE the write lock: a concurrent request for the SAME
        # PID (double page-load, two tabs on the same Prolific link) may have
        # committed a reservation between the check above and acquiring this
        # lock. Without this re-check, both requests would independently
        # pick+reserve a batch, leaving the PID with two sittings' worth of
        # reserved games — and, now that sessions are counted, silently
        # burning two of their allowed sessions at once.
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

    db.backup_db_to_hf_async()
    est = sum(transcript_seconds(s) for s in picked) + rules_seconds(picked[0])
    if est < TARGET_SECONDS / 2:
        # Non-blocking: the participant still gets a valid (shorter) session
        # and the existing end-of-playlist flow handles it with zero extra
        # code. Logged so the study coordinator notices sittings are coming
        # out badly under-length — usually the sign that the pool has been
        # picked over — matching db.py's "loud, never silent" convention.
        print(f"⚠️ assignment: session {new_idx} for {annotator_id!r} is only "
              f"~{est / 60:.0f} min ({len(picked)} games) against a "
              f"{TARGET_SECONDS / 60:.0f} min target — pool is running low.")
    return [{"game": s, "condition": condition} for s in picked], None
