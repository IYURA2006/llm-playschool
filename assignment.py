"""Batch assignment for the Prolific study.

A sitting is one whole curated BATCH: one game, one model, 4-13 transcripts.
The count is chosen so the sitting is about 20 minutes, balanced on rateable
turns (see batch_plan.json). Each batch is completed independently by
COVERAGE_TARGET annotators.

A reservation is a placeholder row in db.py's `annotations` table — its
UNIQUE(game_slug, annotator_id, condition) constraint is what makes the claim
stick under concurrent Prolific traffic.

Two rules do the real work:

  * A participant may take at most MAX_BATCHES batches.
  * A participant may never take two batches of the same TEMPLATE. Every
    model's version of a template holds the SAME instances, so REF-1__qwen27b
    followed by REF-1__dair2b would be re-rating the same games — the two
    ratings would not be independent, which is the whole point of having three.

This module is files + database only. It deliberately does not import
annotation: batch membership comes from study_set (which reads the manifest),
so picking cannot depend on transcript discovery. preflight() is where the
"do these transcripts actually exist on disk" question is asked, once, at boot.
"""

import collections
import os
import random
from datetime import datetime, timedelta

import db
import study_set

COVERAGE_TARGET = 3     # independent annotators before a transcript is "covered"
CONDITION = "hybrid"    # the only condition the general study ever assigns

# Abandoned reservations expire after this long. One hour was too short: a long
# batch went stale while the annotator was still working on it, and the
# transcripts were handed out again.
STALE_AFTER_HOURS = 2

# Total batches one participant may complete. A returning PID gets a batch from
# a template they have not seen, until this limit.
MAX_BATCHES = 5

# Every batched transcript, read from the manifest rather than from disk, so it
# does not depend on GAMES_DIR. preflight() checks the two agree.
POOL_SLUGS = tuple(sorted(
    s for members in study_set.BATCH_MEMBERS.values() for s in members))

NO_TASKS_MESSAGE = (
    "🙏 There are no annotation tasks available right now — every set of games "
    "has enough annotators at the moment. Thank you for your interest; "
    "please check back later."
)

CAP_MESSAGE = (
    f"🎉 Thank you — you've completed the maximum of {MAX_BATCHES} sets of "
    f"games for this study, so there's nothing further for you to do. We're "
    f"very grateful for your work."
)

# Different from NO_TASKS_MESSAGE on purpose. Work remains, but only in
# templates this person has already seen, so "check back later" would never
# come true for them.
EXHAUSTED_MESSAGE = (
    "🙏 Thank you — you've already completed every set of games available to "
    "you in this study. There's nothing further for you to do, so please "
    "return your submission on Prolific rather than waiting."
)

# Why _pick_batch found nothing.
NOTHING_OPEN = "no_open"            # the study itself is finished
ANNOTATOR_EXHAUSTED = "exhausted"   # work remains, but none of it is theirs

Pick = collections.namedtuple("Pick", "batch_id slugs reason")


def _stale_cutoff():
    """ISO string in the same naive-local format every timestamp in this app
    uses (see db.coverage_counts) — mixing formats breaks the comparison."""
    return (datetime.now() - timedelta(hours=STALE_AFTER_HOURS)).isoformat()


def _templates_held(slugs):
    """Templates this participant has already been reserved into.

    Derived from the slugs they hold, NOT from annotations.template_id. The
    stamped column is the historical answer; batches.csv is the current one. If
    an instance is ever re-curated into a different template, the column would
    say they did REF-1 (true then, irrelevant now) and happily offer them the
    template that today contains a transcript they have already rated.
    """
    held = set()
    for slug in slugs:
        dims = study_set.DIMENSIONS.get(slug)
        if dims and dims.get("template_id"):
            held.add(dims["template_id"])
    return held


def _pick_batch(counts, coverage_target, exclude_templates=(), exclude_slugs=(),
                rng=None, batch_members=None, batch_template=None):
    """Choose one batch. Pure: no database, no disk, everything injectable.

    Returns a Pick. `slugs` holds the batch's still-under-covered members in
    POSITION order and is empty exactly when `reason` is set, so `bool(slugs)`
    is the success test and `reason` distinguishes the two dead ends.

    A batch's coverage is the coverage of its WEAKEST transcript. prune_over_
    covered deletes individual rows, so a batch really can sit at 12/13 — and
    any average- or any-member definition would call that finished and strand
    the last transcript at 2 ratings forever.
    """
    rng = rng or random.Random()
    # Read at call time, not as a default argument: defaults are bound at def
    # time, which made the old version unpatchable from the tests.
    if batch_members is None:
        batch_members = study_set.BATCH_MEMBERS
    if batch_template is None:
        batch_template = study_set.BATCH_TEMPLATE
    exclude_templates = set(exclude_templates)
    exclude_slugs = set(exclude_slugs)

    anything_open = False
    eligible = []
    for batch_id, members in batch_members.items():
        under = [s for s in members if counts.get(s, 0) < coverage_target]
        if not under:
            continue                        # this batch is finished
        anything_open = True
        if batch_template.get(batch_id) in exclude_templates:
            continue                        # they have already seen these games
        needed = [s for s in under if s not in exclude_slugs]
        if not needed:
            continue                        # belt-and-braces; template rule covers it
        # Send intact batches out before partly covered ones, so short sittings
        # land at the end instead of paying a full sitting for two transcripts.
        eligible.append((min(counts.get(s, 0) for s in under),
                         -len(under), batch_id, needed))

    if not eligible:
        return Pick(None, [],
                    ANNOTATOR_EXHAUSTED if anything_open else NOTHING_OPEN)

    rng.shuffle(eligible)                   # random tie-break …
    eligible.sort(key=lambda e: e[:2])      # … preserved: sort the KEY, not the
                                            # whole tuple, or batch_id would
                                            # break every tie alphabetically.
    _level, _n, batch_id, needed = eligible[0]
    return Pick(batch_id, needed, None)


def _resume_target(summary):
    """(session_to_resume, completed_count) from db.session_summary rows.
    session_to_resume is None once every sitting on file is complete."""
    unfinished = next((idx for idx, total, done in summary if done < total), None)
    completed = sum(1 for _, total, done in summary if done >= total and total)
    return unfinished, completed


def current_session_index(annotator_id, condition=CONDITION):
    """1-based index of the sitting this participant is currently in. Call it
    AFTER build_playlist_for so a freshly reserved batch is counted — stamped
    onto each row as session metadata. (It used to gate the practice round;
    that now hangs off db.has_completed_practice, which survives a reload.)"""
    summary = db.session_summary(annotator_id, condition=condition)
    resume_idx, _ = _resume_target(summary)
    if resume_idx is not None:
        return resume_idx
    return max((idx for idx, _, _ in summary), default=0) + 1


def _log_short_sitting(annotator_id, session_index, batch_id, playlist):
    """The only way a sitting can now be shorter than its curated batch is that
    coverage or pruning removed members. Worth a line: it means someone is being
    paid a full sitting's rate for part of one."""
    n_members = len(study_set.BATCH_MEMBERS.get(batch_id, ()))
    if n_members and len(playlist) < n_members:
        print(f"⚠️ assignment: sitting {session_index} for {annotator_id!r} is "
              f"{len(playlist)} of {batch_id}'s {n_members} transcripts — the "
              f"rest already reached COVERAGE_TARGET.")


def build_playlist_for(annotator_id, condition=CONDITION):
    """Entry point for a Prolific PID. Returns (playlist, error_message).

    One call reserves at most one batch. Resumes an unfinished sitting rather
    than re-picking, so the games cannot change under someone mid-sitting.
    """
    summary = db.session_summary(annotator_id, condition=condition)
    resume_idx, completed = _resume_target(summary)
    # Nothing to write for a capped-out participant, so skip the lock.
    if resume_idx is None and completed >= MAX_BATCHES:
        return [], CAP_MESSAGE

    playlist, reason, picked_batch, new_idx = None, None, None, None
    with db.write_transaction() as conn:
        # Re-read under the lock, or two tabs would each reserve a batch.
        summary = db.session_summary(annotator_id, condition=condition, conn=conn)
        resume_idx, completed = _resume_target(summary)

        if resume_idx is not None:
            db.prune_over_covered(annotator_id, condition, resume_idx,
                                  COVERAGE_TARGET, conn=conn)
            # The prune can empty the sitting; then we fall through to a fresh
            # pick, and the voided sitting costs no cap slot.
            summary = db.session_summary(annotator_id, condition=condition, conn=conn)
            resume_idx, completed = _resume_target(summary)

        if resume_idx is not None:
            playlist = db.assigned_games(annotator_id, condition=condition,
                                         session_index=resume_idx, conn=conn)

        elif completed < MAX_BATCHES:
            new_idx = max((idx for idx, _, _ in summary), default=0) + 1
            counts = db.coverage_counts(condition, stale_before=_stale_cutoff(),
                                        conn=conn)
            held = db.reserved_slugs(annotator_id, conn=conn)
            pick = _pick_batch(counts, COVERAGE_TARGET,
                               exclude_templates=_templates_held(held),
                               exclude_slugs=held)
            reason = pick.reason
            if pick.slugs:
                picked_batch = pick.batch_id
                # There is no position column. Order survives only by
                # convention: members are sorted at load, the filters keep
                # order, rows are inserted in list order and read back by id.
                db.reserve_games(
                    annotator_id, condition, pick.slugs,
                    session_index=new_idx, conn=conn,
                    batch_id=pick.batch_id,
                    dims_by_slug={s: study_set.dimensions(s) for s in pick.slugs},
                )
                # Read back rather than building the list here, so both paths
                # return the same shape. ON CONFLICT DO NOTHING also means an
                # inline list could name a row that was never written.
                playlist = db.assigned_games(annotator_id, condition=condition,
                                             session_index=new_idx, conn=conn)

    if playlist:
        if picked_batch:
            _log_short_sitting(annotator_id, new_idx, picked_batch, playlist)
        return playlist, None
    if completed >= MAX_BATCHES:
        return [], CAP_MESSAGE
    if reason == ANNOTATOR_EXHAUSTED:
        return [], EXHAUSTED_MESSAGE
    return [], NO_TASKS_MESSAGE


def preflight():
    """Problems that must be fixed before recruiting. Returns a list; empty is
    clean. Call at boot — this module no longer imports annotation, so nothing
    else checks that the batched transcripts actually exist under GAMES_DIR,
    and the failure would otherwise land on a participant AFTER their rows were
    reserved."""
    problems = list(study_set.validate())
    try:
        import annotation
    except Exception as exc:                                  # pragma: no cover
        return problems + [f"cannot import annotation: {exc!r}"]

    missing = [s for s in POOL_SLUGS if annotation.slug_to_path(s) is None]
    if missing:
        problems.append(
            f"{len(missing)}/{len(POOL_SLUGS)} batched transcripts do not "
            f"resolve under GAMES_DIR={os.environ.get('GAMES_DIR', 'games')!r} "
            f"(e.g. {missing[0]}) — the app is pointed at the wrong tree")
    if len(study_set.TEMPLATE_BATCHES) < MAX_BATCHES:
        problems.append(
            f"MAX_BATCHES={MAX_BATCHES} exceeds the "
            f"{len(study_set.TEMPLATE_BATCHES)} templates available, so a "
            f"participant could never reach the cap")
    return problems


if __name__ == "__main__":
    import json
    print(json.dumps(study_set.summary(), indent=2))
    probs = preflight()
    print(f"\npreflight(): {len(probs)} problem(s)")
    for p in probs[:20]:
        print("  -", p)
