"""Throwaway debrief export for the annotation pilot.

Reads annotations.db and writes two files:

- pilot_export.csv — one row per (annotation, turn), with the verdict/survey/
  timing fields repeated on every row for that annotation so the whole thing
  opens as a single flat sheet. `duration_seconds` is the per-GAME timer
  (started_at → verdict_at).
- pilot_sessions.csv — one row per (annotator, session_day): the per-SESSION
  timer. `wall_clock_seconds` runs from the Start click on the welcome page
  (so it includes the practice round) to the last submitted verdict;
  `active_seconds` is the sum of the per-game durations (excludes practice
  and any break between games).

Usage: python export_pilot_csv.py [output.csv]
"""

import csv
import os
import sqlite3
import sys
from datetime import datetime

_dir = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_dir, "annotations.db")

COLUMNS = [
    "annotator", "game", "condition", "session_day", "turn_index",
    "prior_information_use", "strategic_logic", "reasoning_clarity",
    "extra_responses", "flags", "comment",
    "strategic_coherence", "overall_rating", "verdict_comment",
    "survey_fit", "survey_fatigue", "survey_confidence", "survey_comment",
    "session_started_at", "started_at", "annotated_at", "verdict_at",
    "duration_seconds",
]

QUERY = """
SELECT
    a.annotator_id, a.game_slug, a.condition, a.session_day, t.turn_index,
    t.prior_information_use, t.strategic_logic, t.reasoning_clarity,
    t.extra_responses, t.flags, t.comment,
    a.strategic_coherence, a.overall_rating, a.verdict_comment,
    a.survey_fit, a.survey_fatigue, a.survey_confidence, a.survey_comment,
    a.session_started_at, a.started_at, a.annotated_at, a.verdict_at
FROM turn_ratings t
JOIN annotations a ON a.id = t.annotation_id
ORDER BY a.session_day, a.game_slug, a.annotator_id, a.condition, t.turn_index
"""

SESSION_COLUMNS = [
    "annotator", "session_day", "games_started", "games_completed",
    "session_started_at", "last_verdict_at",
    "wall_clock_seconds", "active_seconds",
]

SESSION_QUERY = """
SELECT annotator_id, session_day, session_started_at, started_at, verdict_at
FROM annotations
WHERE session_day IS NOT NULL AND session_day != ''
ORDER BY annotator_id, session_day
"""


def _seconds_between(start, end):
    """Whole seconds from ISO `start` to ISO `end`, or '' if either is missing."""
    if not start or not end:
        return ""
    try:
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
        return round(delta.total_seconds())
    except ValueError:
        return ""


def _session_rows(rows):
    """Aggregate annotation rows into one summary row per (annotator, day)."""
    sessions = {}
    for annotator, day, session_started, started, verdict in rows:
        s = sessions.setdefault((annotator, day), {
            "started": 0, "completed": 0, "session_started_at": None,
            "last_verdict_at": None, "active": 0,
        })
        s["started"] += 1
        if session_started and (s["session_started_at"] is None
                                or session_started < s["session_started_at"]):
            s["session_started_at"] = session_started
        if verdict:
            s["completed"] += 1
            if s["last_verdict_at"] is None or verdict > s["last_verdict_at"]:
                s["last_verdict_at"] = verdict
            game_secs = _seconds_between(started, verdict)
            if game_secs != "":
                s["active"] += game_secs
    out = []
    for (annotator, day), s in sorted(sessions.items()):
        out.append([
            annotator, day, s["started"], s["completed"],
            s["session_started_at"] or "", s["last_verdict_at"] or "",
            _seconds_between(s["session_started_at"], s["last_verdict_at"]),
            s["active"],
        ])
    return out


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_dir, "pilot_export.csv")
    sessions_path = os.path.join(os.path.dirname(out_path) or _dir, "pilot_sessions.csv")

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(QUERY).fetchall()
    session_rows = conn.execute(SESSION_QUERY).fetchall()
    conn.close()

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for row in rows:
            started_at, verdict_at = row[-3], row[-1]
            writer.writerow([*row, _seconds_between(started_at, verdict_at)])
    print(f"Wrote {len(rows)} rows to {out_path}")

    summaries = _session_rows(session_rows)
    with open(sessions_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SESSION_COLUMNS)
        writer.writerows(summaries)
    print(f"Wrote {len(summaries)} session summaries to {sessions_path}")


if __name__ == "__main__":
    main()
