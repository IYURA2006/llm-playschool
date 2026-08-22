"""Read-only snapshot of the collected annotations: tidy CSVs, a nested JSON
bundle, and a data-quality report.

Run it on a machine that can reach the study database (the University VPN, if
that is `breezy`), using the same DB_* credentials in .env that the app uses —
`studyuser` already holds SELECT:

    python export_annotations.py --check     # connectivity + counts, writes nothing
    python export_annotations.py             # full snapshot into exports/

The connection is opened read-only at the session level, so this cannot write
even by accident. That makes it safe to run mid-collection as a coverage
monitor, not only at the end.

Coded answers ("3") and positional keys (verdict_specific is {"0": "3", …},
indexed into annotation.whole_game_questions) are resolved to question text
HERE, at export time, and carried inline in the outputs. Those indices shift
whenever BESPOKE_QUESTIONS changes, so a snapshot decoded later against changed
code would be silently wrong.
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

# Same six vars, same defaults, as db.py — one .env serves the app and this.
load_dotenv()

_DB_HOST = os.environ.get("DB_HOST")
_DB_NAME = os.environ.get("DB_NAME")
_DB_USER = os.environ.get("DB_USER")
_DB_PASSWORD = os.environ.get("DB_PASSWORD")
_DB_PORT = os.environ.get("DB_PORT", "5432")
_DB_SSLMODE = os.environ.get("DB_SSLMODE", "require")
_DB_GSSENCMODE = os.environ.get("DB_GSSENCMODE", "disable")

# A round number is arbitrary; this is only ever a "look at these by hand" flag.
_SPEEDING_SECONDS_PER_TURN = 5.0
# Below this many turns, identical answers across every turn are unremarkable.
_STRAIGHTLINE_MIN_TURNS = 3

_UNRESOLVED = "<UNRESOLVED: question set changed since collection>"


# ---------------------------------------------------------------- connection

def open_readonly():
    """A psycopg2 connection that physically cannot write. Deliberately not
    db._connect(), which is a read-write context manager for the app."""
    missing = [n for n, v in (("DB_HOST", _DB_HOST), ("DB_NAME", _DB_NAME),
                              ("DB_USER", _DB_USER), ("DB_PASSWORD", _DB_PASSWORD))
               if not v]
    if missing:
        sys.exit(f"Missing DB config: {', '.join(missing)}. Set them in .env "
                 f"(see .env.example) before exporting.")
    conn = psycopg2.connect(
        host=_DB_HOST, port=_DB_PORT, dbname=_DB_NAME, user=_DB_USER,
        password=_DB_PASSWORD, sslmode=_DB_SSLMODE, gssencmode=_DB_GSSENCMODE,
    )
    conn.set_session(readonly=True)
    return conn


def _rows(cur, sql):
    """[{column: value}, …] — column names come from the cursor, so the export
    keeps working if a migration adds a column."""
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_all(conn):
    with conn.cursor() as cur:
        return (
            _rows(cur, "SELECT * FROM annotations ORDER BY annotator_id, "
                       "COALESCE(session_index, 1), id"),
            _rows(cur, "SELECT * FROM turn_ratings ORDER BY annotation_id, turn_index"),
            _rows(cur, "SELECT annotator_id, consented_at FROM consents"),
            # Question sets recorded at collection time. Storing the spec, not
            # just its hash, is what lets a row whose questions have since
            # changed still decode correctly instead of merely being flagged.
            _rows(cur, "SELECT * FROM question_sets"),
        )


# ------------------------------------------------------------------ decoding

def load_annotation_module():
    """Import annotation.py for the question definitions.

    Importing it pulls in db.py, which runs _require_db_config() + init_db() at
    import. That is NOT a write against production: studyuser lacks CREATE, so
    init_db() catches InsufficientPrivilege and falls back to the SELECT-only
    _check_schema_exists(). Leave it be rather than "fixing" it — the point of
    importing is that the question text has exactly one definition.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import annotation
        import annotation_verdict
    except Exception as exc:
        sys.exit(f"Could not import the app modules for question decoding: {exc}")
    return annotation, annotation_verdict


_MD_NOISE = re.compile(r"\*+|`")


def plain(md):
    """Question markdown as a single-line plain string fit for a CSV cell."""
    return " ".join(_MD_NOISE.sub("", md or "").split())


def label_for(choices, value):
    """Human label for a stored code. Choices are [(display, value), …] where
    display is "3\\nGood" — or plain "N/A" / "Yes" for the non-scale ones — so
    the label is the last line of the display string."""
    if value is None or not choices:
        return ""
    for display, val in choices:
        if str(val) == str(value):
            return str(display).split("\n")[-1].strip()
    return ""


def numeric(value):
    """The code as an int when it is one ("3" → 3), else None. "NA", "yes" and
    other non-ordinal codes deliberately produce None rather than 0."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def parse_json(raw, integrity, what):
    """json.loads that records malformed values instead of killing the export."""
    if raw in (None, "", "null"):
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        integrity["malformed_json"].append(what)
        return None


class Decoder:
    """Resolves stored codes back to the questions that produced them, using
    annotation.py as the single source of truth."""

    def __init__(self, annotation, annotation_verdict):
        self.a = annotation
        self.v = annotation_verdict
        self._game_cache = {}

    # -- game identity -----------------------------------------------------

    def path_for(self, slug):
        return self.a.slug_to_path(slug)

    def game_key(self, slug):
        """The BESPOKE_QUESTIONS family key. Resolved through the transcript
        path like the app does, not by splitting the slug — for nested trees
        (dond/coop_en/…) the family is not the first path part."""
        path = self.path_for(slug)
        if path is None:
            # The old fallback split the slug and returned its first segment,
            # which under the study tree is the MODEL id — a plausible-looking
            # family name that then indexes BESPOKE_QUESTIONS to {} silently.
            return ""
        return self.a.game_key(path)

    def n_turns(self, slug):
        """Rateable turn count from the transcript itself, for the integrity
        check. None when the transcript is gone or unreadable."""
        if slug not in self._game_cache:
            path = self.path_for(slug)
            try:
                self._game_cache[slug] = self.a.load_game(path).n_turns if path else None
            except Exception:
                self._game_cache[slug] = None
        return self._game_cache[slug]

    def is_hybrid(self, condition):
        return self.a.BLOCK_TO_TYPE.get(condition, "universal") == "hybrid"

    # -- per-turn questions -------------------------------------------------

    def turn_questions(self, slug, condition, role):
        """({slot: (question_md, choices)}, [(key, question_md, choices), …])
        for one role, mirroring the render loop's precedence in annotation.py:
        "generic"/absent → universal widget, None → not asked of this role at
        all, tuple → bespoke. Bespoke sets apply in hybrid mode only."""
        role_cfg = {}
        if self.is_hybrid(condition):
            bespoke = self.a.BESPOKE_QUESTIONS.get(self.game_key(slug)) or {}
            role_cfg = (bespoke.get("roles") or {}).get(role) or {}

        slots = {}
        for slot, generic in (("q1", self.a.GENERIC_Q1), ("q2", self.a.GENERIC_Q2)):
            cfg = role_cfg.get(slot, "generic")
            if cfg == "generic":
                slots[slot] = generic
            elif cfg is not None:
                slots[slot] = cfg
            # cfg is None → the slot is not rendered for this role; omit it so
            # the export shows "not asked" rather than an empty answer.
        # Q3 is always present in the payload — preset to "NA" when hidden.
        slots["q3"] = self.a.GENERIC_Q3
        return slots, list(role_cfg.get("bolt_ons") or [])

    def is_bespoke(self, slug, condition, role, slot):
        if not self.is_hybrid(condition):
            return False
        bespoke = self.a.BESPOKE_QUESTIONS.get(self.game_key(slug)) or {}
        role_cfg = (bespoke.get("roles") or {}).get(role) or {}
        return isinstance(role_cfg.get(slot), tuple)

    # -- whole-game questions ----------------------------------------------

    def whole_game(self, slug, condition):
        path = self.path_for(slug)
        if path is None:
            return []
        return self.a.whole_game_questions(path, condition)

    def current_hash(self, slug, condition):
        """Fingerprint the question set the CURRENT code would show for this
        transcript, so a row's stored hash can be compared against it."""
        path = self.path_for(slug)
        if path is None:
            return None
        try:
            g = self.a.load_game(path)
            # Use the app's own decision, never a local guess — see
            # annotation.show_q3_for.
            return self.a.question_spec_hash(
                self.a.question_spec(g, condition, self.a.show_q3_for(g, condition)))
        except Exception:
            return None

    def whole_game_only(self, slug, condition):
        path = self.path_for(slug)
        return bool(path) and self.a.whole_game_only(path, condition)

    def coherence_label(self, value):
        # _COHERENCE is [(value, short, long), …] — not the (display, value)
        # shape label_for expects, so match on the first element.
        for val, short, _long in self.v._COHERENCE:
            if str(val) == str(value):
                return short
        return ""

    def overall_label(self, value):
        for val, short, _long in self.v._OVERALL_RATINGS:
            if str(val) == str(value):
                return short
        return ""


# ------------------------------------------------------------ row assembly

def classify(row, has_turns, hybrid_condition):
    """('complete' | 'partial' | 'placeholder', is_debug).

    Every row is kept and labelled rather than filtered: the quality report
    needs the abandoned ones to compute an abandonment rate, and stale
    reservations are never deleted from the table.
    """
    if row["verdict_at"] is not None:
        status = "complete"
    elif has_turns:
        status = "partial"
    else:
        status = "placeholder"
    # '' is db.py's "identity not resolved" sentinel; a non-hybrid condition
    # only ever comes from a legacy debug link, never from assignment.py.
    is_debug = not row["annotator_id"] or row["condition"] != hybrid_condition
    return status, is_debug


def seconds_between(start, end):
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 1)


def build(annotations, turn_rows, consents, decoder, hybrid_condition,
          question_sets=None):
    """Assemble the enriched records every output is written from."""
    integrity = defaultdict(list)
    recorded_specs = {q["question_set_hash"]: q for q in (question_sets or [])}

    turns_by_annotation = defaultdict(list)
    for t in turn_rows:
        turns_by_annotation[t["annotation_id"]].append(t)

    # Session duration is the span from the sitting's start to its last
    # verdict, so it needs every row of that sitting (see app.py's own note).
    last_verdict = {}
    for a in annotations:
        if a["verdict_at"] is None:
            continue
        key = (a["annotator_id"], a["session_index"] or 1)
        if key not in last_verdict or a["verdict_at"] > last_verdict[key]:
            last_verdict[key] = a["verdict_at"]

    records = []
    for a in annotations:
        slug = a["game_slug"]
        condition = a["condition"]
        my_turns = turns_by_annotation.get(a["id"], [])
        status, is_debug = classify(a, bool(my_turns), hybrid_condition)

        if decoder.path_for(slug) is None:
            integrity["unknown_slug"].append(slug)

        rec = dict(a)
        rec["status"] = status
        rec["is_debug"] = is_debug
        rec["game_key"] = decoder.game_key(slug)
        rec["n_turns_rated"] = len(my_turns)
        rec["strategic_coherence_label"] = decoder.coherence_label(a["strategic_coherence"])
        rec["overall_rating_label"] = decoder.overall_label(a["overall_rating"])
        rec["whole_game_only"] = decoder.whole_game_only(slug, condition)
        rec["game_duration_seconds"] = seconds_between(a["started_at"], a["verdict_at"])
        rec["session_duration_seconds"] = seconds_between(
            a["session_started_at"],
            last_verdict.get((a["annotator_id"], a["session_index"] or 1)),
        )

        # Whole-game (verdict) bespoke answers. Keys are question IDs (see
        # annotation.normalise_whole_game); older rows may still carry positional
        # keys ("0", "1"), which are resolved by index as a fallback and flagged.
        wg_questions = decoder.whole_game(slug, condition)
        by_id = {qid: (qid, md, ch) for qid, md, ch in wg_questions}
        stored = parse_json(a["verdict_specific"], integrity,
                            f"annotations.id={a['id']}.verdict_specific") or {}
        wg = []
        for key, value in sorted(stored.items()):
            entry, positional = by_id.get(key), False
            if entry is None:
                # Legacy positional key. Only trustworthy when the list length
                # still matches, since a reorder is undetectable by position.
                idx = numeric(key)
                if (idx is not None and len(stored) == len(wg_questions)
                        and 0 <= idx < len(wg_questions)):
                    entry, positional = wg_questions[idx], True
            if entry is None:
                integrity["unresolved_verdict_specific"].append(
                    f"id={a['id']} slug={slug} key={key!r} not in current question set")
                wg.append({"question_id": key, "question": _UNRESOLVED,
                           "value": value, "label": "", "resolved": False})
                continue
            if positional:
                integrity["positional_verdict_key"].append(
                    f"id={a['id']} slug={slug} key={key!r} resolved by position")
            qid, question_md, choices = entry
            wg.append({
                "question_id": qid,
                "question": plain(question_md),
                "value": value,
                "label": label_for(choices, value),
                "resolved": True,
            })
        rec["whole_game_responses"] = wg

        # How trustworthy is this row's decoding?
        #   current     — questions unchanged since collection
        #   superseded  — changed, but the collection-time spec was recorded
        #   unrecorded  — no fingerprint stored (legacy or debug row)
        #   mismatch    — fingerprint stored but its spec is missing
        stored_hash = a.get("question_set_hash")
        if not stored_hash:
            rec["question_set_status"] = "unrecorded"
        elif stored_hash not in recorded_specs:
            rec["question_set_status"] = "mismatch"
            integrity["question_set_missing"].append(
                f"id={a['id']} hash={stored_hash}")
        else:
            rec["question_set_status"] = (
                "current" if stored_hash == decoder.current_hash(slug, condition)
                else "superseded")
            if rec["question_set_status"] == "superseded":
                integrity["question_set_superseded"].append(
                    f"id={a['id']} slug={slug} collected_under={stored_hash}")

        if status == "complete" and not my_turns:
            integrity["complete_without_turns"].append(f"id={a['id']} slug={slug}")
        expected = decoder.n_turns(slug)
        if my_turns and expected is not None and expected != len(my_turns):
            integrity["turn_count_mismatch"].append(
                f"id={a['id']} slug={slug} db={len(my_turns)} transcript={expected}")

        # Per-turn answers, each carrying the question it answered.
        decoded_turns = []
        for t in my_turns:
            slots, bolt_ons = decoder.turn_questions(slug, condition, t["role"])
            responses = {}
            for slot, column in (("q1", "prior_information_use"),
                                 ("q2", "strategic_logic"),
                                 ("q3", "reasoning_clarity")):
                if slot not in slots:
                    continue  # not asked of this role
                question_md, choices = slots[slot]
                responses[slot] = {
                    "question": plain(question_md),
                    "value": t[column],
                    "label": label_for(choices, t[column]),
                    "is_bespoke": decoder.is_bespoke(slug, condition, t["role"], slot),
                }
            extra = parse_json(t["extra_responses"], integrity,
                               f"turn_ratings.id={t['id']}.extra_responses") or {}
            for key, question_md, choices in bolt_ons:
                if key not in extra:
                    continue
                responses[f"extra:{key}"] = {
                    "question": plain(question_md),
                    "value": extra[key],
                    "label": label_for(choices, extra[key]),
                    "is_bespoke": True,
                }
            # Flags are stored as their full English sentences, so they need no
            # lookup — a bespoke override changes the wording, not a code.
            flags = parse_json(t["flags"], integrity,
                               f"turn_ratings.id={t['id']}.flags") or []
            decoded_turns.append({
                "id": t["id"],
                "turn_index": t["turn_index"],
                "from_player": t["from_player"],
                "role": t["role"],
                "content": t["content"],
                "comment": t["comment"],
                "flags": flags,
                "responses": responses,
            })
        rec["turns"] = decoded_turns
        rec["straightlined"] = _straightlined(decoded_turns)
        rec["seconds_per_turn"] = (
            round(rec["game_duration_seconds"] / len(decoded_turns), 1)
            if rec["game_duration_seconds"] and decoded_turns else None)
        records.append(rec)

    consented_at = {c["annotator_id"]: c["consented_at"] for c in consents}
    return records, consented_at, integrity


def _straightlined(turns):
    """True when every question got the same answer on every turn it was asked,
    and no comment was written — a review flag, never grounds for automatic
    exclusion.

    Compared per question rather than per turn: in a multi-role game each role
    answers a different question set, so a whole-turn signature could never
    match across roles and the flag would never fire for (say) codenames.
    """
    if len(turns) < _STRAIGHTLINE_MIN_TURNS:
        return False
    if any((t["comment"] or "").strip() for t in turns):
        return False
    values = defaultdict(set)
    asked = Counter()
    for t in turns:
        for question_id, resp in t["responses"].items():
            values[question_id].add(str(resp["value"]))
            asked[question_id] += 1
    # A question asked only once or twice is constant by construction, not by
    # the annotator clicking down a column.
    if not asked or max(asked.values()) < _STRAIGHTLINE_MIN_TURNS:
        return False
    return all(len(v) == 1 for v in values.values())


# ------------------------------------------------------------------- output

def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def write_csv(path, rows, columns):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: _iso(r.get(c)) for c in columns})
    return len(rows)


_ANNOTATION_COLUMNS = [
    "id", "status", "is_debug", "game_slug",
    # Study dimensions, stamped at collection time (see db.save_turns). game_key
    # is kept alongside `game` on purpose: they are derived independently, so a
    # disagreement between them is an alarm worth being able to see.
    "model_id", "domain", "game", "experiment", "instance",
    "batch_id", "template_id",
    "question_set_hash", "question_set_status",
    "game_key", "game_id", "game_name",
    "source_path", "has_reasoning", "annotator_id", "consented_at",
    "condition", "session_index",
    "session_day", "session_started_at", "started_at", "annotated_at", "verdict_at",
    "updated_at", "n_turns_rated", "strategic_coherence", "strategic_coherence_label",
    "overall_rating", "overall_rating_label", "whole_game_only", "verdict_comment",
    "game_duration_seconds", "session_duration_seconds", "seconds_per_turn",
    "straightlined",
]


_LONG_COLUMNS = [
    "annotation_id", "annotator_id",
    "game_slug", "model_id", "domain", "game", "experiment", "instance",
    "batch_id", "template_id",
    "game_key", "condition", "session_index",
    "scope", "turn_index", "from_player", "role",
    "question_id", "question_text", "is_bespoke",
    "response_value", "response_label", "response_numeric",
    # Free text (turn comments). Empty on every coded row — the file stays
    # analysable, but comments stop being trapped in a table nobody exports.
    "response_text",
]




def annotation_rows(records, consented_at=None):
    consented_at = consented_at or {}
    rows = []
    for r in records:
        row = dict(r)
        # Consent used to reach the export only via participants.csv. Without it
        # here, nothing in a snapshot shows that every annotator consented.
        row["consented_at"] = consented_at.get(r["annotator_id"])
        # Whole-game answers flattened alongside the generic verdict columns.
        # Named by question id, so a column means the same thing across exports
        # even if the question order changes.
        for wg in r["whole_game_responses"]:
            qid = wg["question_id"]
            row[f"wg_{qid}_question"] = wg["question"]
            row[f"wg_{qid}_value"] = wg["value"]
            row[f"wg_{qid}_label"] = wg["label"]
        rows.append(row)
    # Widen the header by however many whole-game questions actually occurred.
    extra = sorted({k for r in rows for k in r if k.startswith("wg_")})
    return rows, _ANNOTATION_COLUMNS + extra


def long_rows(records):
    """One row per response. The only shape in which the eight game families'
    different question sets fit a single table — and the one to build model
    comparison on, since model_id rides on every row.

    Coded answers carry response_value/label/numeric; turn comments come through
    as question_id="comment" with the text in response_text, because the only
    other file that ever carried them is no longer produced.
    """
    rows = []
    for r in records:
        base = {
            "annotation_id": r["id"], "annotator_id": r["annotator_id"],
            "game_slug": r["game_slug"], "game_key": r["game_key"],
            # .get so the export still runs against a pre-migration database.
            "model_id": r.get("model_id"), "domain": r.get("domain"),
            "game": r.get("game"), "experiment": r.get("experiment"),
            "instance": r.get("instance"), "batch_id": r.get("batch_id"),
            "template_id": r.get("template_id"),
            "condition": r["condition"], "session_index": r["session_index"],
            "response_text": "",
        }
        for t in r["turns"]:
            turn_base = dict(base, scope="turn", turn_index=t["turn_index"],
                             from_player=t["from_player"], role=t["role"])
            for question_id, resp in t["responses"].items():
                rows.append(dict(turn_base,
                                 question_id=question_id,
                                 question_text=resp["question"],
                                 is_bespoke=resp["is_bespoke"],
                                 response_value=resp["value"],
                                 response_label=resp["label"],
                                 response_numeric=numeric(resp["value"])))
            for flag in t["flags"]:
                rows.append(dict(turn_base, question_id="flag",
                                 question_text="Flags - tick all that apply",
                                 is_bespoke="", response_value=flag,
                                 response_label=flag, response_numeric=None))
            if (t.get("comment") or "").strip():
                rows.append(dict(turn_base, question_id="comment",
                                 question_text="Optional turn comment",
                                 is_bespoke="", response_value="",
                                 response_label="", response_numeric=None,
                                 response_text=t["comment"]))

        game_base = dict(base, scope="whole_game", turn_index=None,
                         from_player=None, role=None)
        if r["strategic_coherence"] is not None:
            rows.append(dict(game_base, question_id="coherence",
                             question_text="Strategic coherence across the whole game",
                             is_bespoke=False,
                             response_value=r["strategic_coherence"],
                             response_label=r["strategic_coherence_label"],
                             response_numeric=numeric(r["strategic_coherence"])))
        if r["overall_rating"] is not None:
            rows.append(dict(game_base, question_id="overall",
                             question_text="Overall rating of how well the AI played",
                             is_bespoke=False,
                             response_value=r["overall_rating"],
                             response_label=r["overall_rating_label"],
                             response_numeric=numeric(r["overall_rating"])))
        for wg in r["whole_game_responses"]:
            rows.append(dict(game_base, question_id=f"wg:{wg['question_id']}",
                             question_text=wg["question"], is_bespoke=True,
                             response_value=wg["value"], response_label=wg["label"],
                             response_numeric=numeric(wg["value"])))
    return rows


# ------------------------------------------------------------ quality report


# ---------------------------------------------------------------------- CLI


def _integrity_summary(integrity):
    """Print integrity findings. They used to surface only in quality_report.md;
    with that file gone, computing them and discarding them would silently
    swallow exactly what parse_json exists to catch."""
    total = sum(len(v) for v in integrity.values())
    if not total:
        print("\nIntegrity: no problems found.")
        return 0
    print(f"\nIntegrity: {total} finding(s)")
    for key in sorted(integrity):
        items = integrity[key]
        print(f"  {key}: {len(items)}")
        for item in list(dict.fromkeys(items))[:5]:
            print(f"      {item}")
        if len(items) > 5:
            print(f"      … and {len(items) - 5} more")
    return total


def _consent_gaps(records, consented_at):
    """Annotators with data but no consent record. An ethics-approved study
    must be able to show this set is empty; it used to appear only in
    participants.csv."""
    return sorted({r["annotator_id"] for r in records
                   if r["annotator_id"] and not r["is_debug"]
                   and r["annotator_id"] not in consented_at})


def _manifest_disagreements(records):
    """Rows whose stored dimensions no longer match study_manifest.csv.

    Dimensions are frozen per row at collection time, so regenerating the
    manifest mid-study (different seed, hand edit) leaves early and late rows
    describing the same transcript differently, with nothing else to notice it.
    """
    try:
        import study_set
    except Exception as exc:
        return [f"cannot import study_set: {exc!r}"], []
    problems, unknown = [], []
    for r in records:
        dims = study_set.dimensions(r["game_slug"])
        if dims is None:
            unknown.append(r["game_slug"])
            continue
        for field in ("model_id", "domain", "game", "experiment", "instance",
                      "template_id"):
            stored = r.get(field)
            if stored and stored != dims[field]:
                problems.append(f"id={r['id']} {field}: stored={stored!r} "
                                f"manifest={dims[field]!r}")
    return problems, sorted(set(unknown))


def _assignment_violations(records, max_batches):
    """Verify the assignment rules against COLLECTED data, not just in tests.

    The tests prove the code is right; this proves the DEPLOYMENT is right —
    that the rows actually on disk obey the two rules the study rests on. Both
    failures are silent otherwise.
    """
    try:
        import study_set
    except Exception as exc:                                   # pragma: no cover
        return [f"cannot import study_set: {exc!r}"]

    by_template, by_pid = defaultdict(set), defaultdict(set)
    for r in records:
        if r["is_debug"] or not r["annotator_id"]:
            continue
        dims = study_set.dimensions(r["game_slug"]) or {}
        tpl = r.get("template_id") or dims.get("template_id")
        if tpl:
            by_template[(r["annotator_id"], tpl)].add(r.get("batch_id"))
        by_pid[r["annotator_id"]].add(r["session_index"] or 1)

    out = [f"{pid} was given template {tpl} under {len(bs)} batches: "
           f"{sorted(b for b in bs if b)}"
           for (pid, tpl), bs in sorted(by_template.items()) if len(bs) > 1]
    out += [f"{pid} has {len(s)} sittings, cap is {max_batches}"
            for pid, s in sorted(by_pid.items()) if len(s) > max_batches]
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="validate the study inventory and the database, "
                             "print a summary, and write nothing")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero and write nothing if any integrity "
                             "finding or unrecorded question set is present")
    parser.add_argument("--out", default="exports",
                        help="directory to write the timestamped snapshot into "
                             "(default: exports)")
    args = parser.parse_args(argv)

    conn = open_readonly()
    try:
        annotations, turn_rows, consents, question_sets = fetch_all(conn)
    finally:
        conn.close()

    annotation, annotation_verdict = load_annotation_module()
    import assignment

    decoder = Decoder(annotation, annotation_verdict)
    records, consented_at, integrity = build(
        annotations, turn_rows, consents, decoder, assignment.CONDITION,
        question_sets=question_sets)

    counts = {
        "annotations": len(records),
        "complete": sum(1 for r in records if r["status"] == "complete"),
        "partial": sum(1 for r in records if r["status"] == "partial"),
        "placeholder": sum(1 for r in records if r["status"] == "placeholder"),
        "debug_rows": sum(1 for r in records if r["is_debug"]),
        "turn_ratings": len(turn_rows),
        "annotators": len({r["annotator_id"] for r in records}),
        "consents": len(consented_at),
    }

    gaps = _consent_gaps(records, consented_at)
    disagreements, unknown_slugs = _manifest_disagreements(records)

    if args.check:
        try:
            import study_set
            probs = study_set.validate()
            print(f"study inventory : {len(study_set.DIMENSIONS)} transcripts, "
                  f"{len(study_set.BATCH_MEMBERS)} batches, "
                  f"{len(probs)} problem(s)")
            for pr in probs[:10]:
                print(f"    - {pr}")
        except Exception as exc:
            probs = [f"study_set unavailable: {exc!r}"]
            print(f"study inventory : {probs[0]}")

        for k, v in counts.items():
            print(f"{k:16}: {v}")

        no_dims = [r for r in records if not r["is_debug"] and not r.get("model_id")]
        no_batch = [r for r in records if not r["is_debug"] and not r.get("batch_id")]
        print(f"{'no dimensions':16}: {len(no_dims)}")
        print(f"{'no batch_id':16}: {len(no_batch)}"
              + ("  (expected until assignment is batch-aware)" if no_batch else ""))

        hashes = Counter(r.get("question_set_status") or "n/a" for r in records)
        print(f"{'question sets':16}: {dict(hashes)}")

        if unknown_slugs:
            print(f"\nslugs not in the manifest ({len(unknown_slugs)}):")
            for sl in unknown_slugs[:10]:
                print(f"    - {sl}")
        if disagreements:
            print(f"\nstored dimensions disagree with the manifest "
                  f"({len(disagreements)}) — was the manifest regenerated?")
            for d in disagreements[:10]:
                print(f"    - {d}")
        if gaps:
            print(f"\nANNOTATORS WITH DATA BUT NO CONSENT RECORD ({len(gaps)}):")
            for a in gaps[:10]:
                print(f"    - {a}")

        violations = _assignment_violations(records, assignment.MAX_BATCHES)
        if violations:
            print(f"\nASSIGNMENT RULE VIOLATIONS ({len(violations)}):")
            for v in violations[:10]:
                print(f"    - {v}")
        else:
            print(f"{'assignment rules':16}: ok "
                  f"(≤1 batch per template per annotator, cap "
                  f"{assignment.MAX_BATCHES})")

        n_integrity = _integrity_summary(integrity)
        hard = bool(gaps or disagreements or probs or violations)
        return 1 if hard else 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(args.out, f"annotations_{stamp}")

    ann_rows, ann_columns = annotation_rows(records, consented_at)
    lrows = long_rows(records)

    n_integrity = _integrity_summary(integrity)
    if gaps:
        print(f"\nWARNING: {len(gaps)} annotator(s) have data but no consent "
              f"record: {', '.join(gaps[:5])}")
    unrecorded = sum(1 for r in records
                     if r.get("question_set_status") in ("unrecorded", "mismatch"))
    if unrecorded:
        print(f"WARNING: {unrecorded} row(s) have no recorded question set; "
              f"they were decoded against the CURRENT code.")

    if args.strict and (n_integrity or gaps or disagreements or unrecorded):
        print("\n--strict: refusing to write a snapshot with unresolved findings.")
        return 1

    os.makedirs(out_dir, exist_ok=True)
    written = [
        ("annotations.csv", write_csv(os.path.join(out_dir, "annotations.csv"),
                                      ann_rows, ann_columns)),
        ("responses_long.csv", write_csv(os.path.join(out_dir, "responses_long.csv"),
                                         lrows, _LONG_COLUMNS)),
    ]

    print(f"\nSnapshot written to {out_dir}/")
    for name, n in written:
        print(f"  {name:<22} {n:>7} rows")
    print(f"\n{counts['complete']} complete of {counts['annotations']} reservations, "
          f"{counts['annotators']} annotators.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
