"""Test harness: run annotation.py's loading/parsing logic against one instance
of every game in games/ and report what would render. No Gradio, no UI."""
import glob
import json
import os
import traceback

_dir = os.path.dirname(os.path.abspath(__file__))


def analyze(path):
    """Replicate annotation.py parsing and return a report dict (or raise)."""
    with open(path) as f:
        data = json.load(f)

    meta = data["meta"]
    players = data["players"]

    # ── rules extraction ──
    def extract_rules():
        for turn in data["turns"]:
            for msg in turn:
                if msg["from"] == "GM":
                    c = msg["action"].get("content")
                    if isinstance(c, str) and len(c) > 80:
                        return c
        for turn in data["turns"]:
            for msg in turn:
                c = msg["action"].get("content")
                if isinstance(c, str) and c.strip():
                    return c
        return "(no rules text found)"

    rules = extract_rules()

    # ── AI players ──
    def is_ai(pid):
        info = players.get(pid, {})
        if pid == "GM":
            return False
        model = (info.get("model_name") or "").lower()
        return model != "programmatic" and model != ""

    ai_ids = {pid for pid in players if is_ai(pid)}

    def role(pid):
        return players.get(pid, {}).get("game_role", pid)

    # ── AI response turns ──
    ai_turns = []
    for turn in data["turns"]:
        for msg in turn:
            if msg["from"] in ai_ids and msg["action"].get("label") == "response":
                ai_turns.append(msg)
    n_turns = len(ai_turns)

    # ── reasoning detection ──
    def detect_reasoning(turns):
        if not turns:
            return False
        markers = ("explanation:", "because", "since ", "reasoning:", "i'll ", "i will ")
        hits = 0
        for msg in turns:
            c = str(msg["action"].get("content", "")).lower()
            if len(c) > 60 and any(m in c for m in markers):
                hits += 1
        return hits >= max(1, len(turns) // 2)

    has_reasoning = detect_reasoning(ai_turns)
    multi_role = len(ai_ids) > 1

    # sanity: would the transcript builder crash on any content?
    render_issues = []
    for round_msgs in data["turns"]:
        for msg in round_msgs:
            content = msg["action"].get("content", "")
            sender = msg["from"]
            # response cards do str(content); GM context requires str
            if sender in ai_ids and msg["action"].get("label") == "response":
                if not isinstance(content, (str, int, float)):
                    render_issues.append(
                        f"AI response content is {type(content).__name__}, not str")

    return {
        "ok": True,
        "game_name": meta.get("game_name"),
        "game_id": meta.get("game_id"),
        "players": {pid: (players[pid].get("game_role"), players[pid].get("model_name"))
                    for pid in players},
        "ai_ids": sorted(ai_ids),
        "n_turns": n_turns,
        "has_reasoning": has_reasoning,
        "multi_role": multi_role,
        "rules_len": len(rules),
        "rules_preview": rules[:90].replace("\n", " "),
        "render_issues": render_issues,
    }


games = {}
for g in sorted(os.listdir(os.path.join(_dir, "games"))):
    gp = os.path.join(_dir, "games", g)
    if not os.path.isdir(gp):
        continue
    found = sorted(glob.glob(os.path.join(gp, "**", "interactions.json"), recursive=True))
    if found:
        games[g] = found[0]

print(f"Testing {len(games)} games\n" + "=" * 78)
problems = []
for g, path in games.items():
    rel = os.path.relpath(path, _dir)
    try:
        r = analyze(path)
        flags = []
        if r["n_turns"] == 0:
            flags.append("⚠️  ZERO AI TURNS — nothing to annotate")
        if not r["ai_ids"]:
            flags.append("⚠️  NO AI PLAYERS detected")
        if r["rules_preview"].startswith("(no rules"):
            flags.append("⚠️  no rules text")
        if r["render_issues"]:
            flags.append("⚠️  " + "; ".join(set(r["render_issues"])))
        status = "❌" if flags else "✅"
        print(f"\n{status} {g}   [{rel}]")
        print(f"    game_name={r['game_name']!r}  id={r['game_id']}")
        print(f"    players: {r['players']}")
        print(f"    AI players: {r['ai_ids']}  multi_role={r['multi_role']}")
        print(f"    N_TURNS={r['n_turns']}  HAS_REASONING={r['has_reasoning']}")
        print(f"    rules({r['rules_len']} chars): {r['rules_preview']}")
        for fl in flags:
            print(f"    {fl}")
        if flags:
            problems.append((g, flags))
    except Exception as e:
        print(f"\n💥 {g}   [{rel}]")
        print(f"    CRASHED: {type(e).__name__}: {e}")
        traceback.print_exc()
        problems.append((g, [f"CRASH: {e}"]))

print("\n" + "=" * 78)
print(f"SUMMARY: {len(games) - len(problems)}/{len(games)} games OK")
if problems:
    print("\nGames with issues:")
    for g, fl in problems:
        print(f"  • {g}: {fl}")


# ──────────────────────────────────────────────────────────────────────────
# RENDER CHECK: actually build the transcript HTML and inspect the output
# ──────────────────────────────────────────────────────────────────────────
import html as _html
import re as _re

def render_check(path):
    with open(path) as f:
        data = json.load(f)
    players = data["players"]

    def is_ai(pid):
        info = players.get(pid, {})
        if pid == "GM":
            return False
        m = (info.get("model_name") or "").lower()
        return m not in ("programmatic", "")
    ai_ids = {pid for pid in players if is_ai(pid)}

    # rules
    rules = "(none)"
    for turn in data["turns"]:
        for msg in turn:
            if msg["from"] == "GM":
                c = msg["action"].get("content")
                if isinstance(c, str) and len(c) > 80:
                    rules = c; break
        if rules != "(none)": break

    turn0_setup = set()
    for m in data["turns"][0]:
        c = m["action"].get("content")
        if isinstance(c, str):
            turn0_setup.add(c)

    n_cards = 0
    n_gm = 0
    empty_bodies = 0
    longest_card = 0
    for round_msgs in data["turns"]:
        for msg in round_msgs:
            sender = msg["from"]; action = msg["action"]
            label = action.get("label", ""); content = action.get("content", "")
            if sender == "GM" or sender not in ai_ids:
                if not isinstance(content, str): continue
                if content in turn0_setup: continue
                if label == "context": n_gm += 1
                continue
            if label == "response":
                n_cards += 1
                body = _html.escape(str(content)).strip()
                if not body: empty_bodies += 1
                longest_card = max(longest_card, len(str(content)))
    return n_cards, n_gm, empty_bodies, longest_card, len(rules)

print("\n\n" + "=" * 78)
print("RENDER CHECK (transcript HTML build)")
print("=" * 78)
print(f"{'game':<30}{'cards':>6}{'gm-msgs':>9}{'empty':>7}{'longest':>9}{'rules':>7}")
for g, path in games.items():
    try:
        nc, ng, eb, lc, rl = render_check(path)
        warn = "  ⚠️ EMPTY CARD" if eb else ("  ⚠️ no goal text" if rl < 40 else "")
        print(f"{g:<30}{nc:>6}{ng:>9}{eb:>7}{lc:>9}{rl:>7}{warn}")
    except Exception as e:
        print(f"{g:<30}  💥 {type(e).__name__}: {e}")
