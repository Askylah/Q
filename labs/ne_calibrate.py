"""NE calibration -- replay real chat history through the norepinephrine arithmetic, firing off.

lab_notes/norepinephrine.md §5. Read-only against the live DB (mode=ro&immutable=1), no LLM
calls, no writes anywhere except labs/live_out/ne_calib_<stamp>/. The embedding model is the
one the app already uses (all-MiniLM-L6-v2, local HF cache, offline).

Per (username, persona), user messages in time order, the §3 rules:
  - a gap over --session-gap since the last user message resets CONV and M (new session)
  - messages under --min-words are skipped (no s, no update)
  - the first --warmup messages after a reset seed CONV/M and are never evaluated
  - s = 1 - cos(e, CONV);  delta = s - M;  then M += alpha_m (s - M), CONV -> EMA(alpha_c)
Two CONV variants for Open 2: 'user' (user messages only) and 'both' (the persona's replies
folded into CONV too; s is still only measured on user messages).

Two data sets, never mixed:
  REAL  : --users (default Sky); the distribution the constants come from
  LABEL : the gaba_live* scripted runs, where the frame breaks are known. D ("Totally
          different...") after 10+ bread turns is the headline case; A is five topic jumps in a
          row (the chaotic-user case: should fire early, then adapt).

    python labs/ne_calibrate.py
    python labs/ne_calibrate.py --min-words 3 --alpha-c 0.5
"""
import argparse, csv, datetime, json, os, sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "PersonaApp", "users.db")
MODEL = "all-MiniLM-L6-v2"
THRESHOLDS = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30)
LAB_PREFIX = "gaba_live"

# gaba_live.py phase markers (first words of each scripted message)
LABELS = (("Totally different.", "D"), ("New one.", "E"), ("Different thing.", "B1"),
          ("Quick chemistry one", "A1"), ("Random memory.", "A2"),
          ("Have you ever looked at the board game", "A3"), ("If you had a week alone", "A4"),
          ("My car does a shudder", "A5"))


def label_of(text):
    for prefix, lab in LABELS:
        if text.startswith(prefix):
            return lab
    return "bread"


def text_of(content):
    """Messages with images are stored as a JSON list of blocks; keep the text parts."""
    c = (content or "").strip()
    if c.startswith("["):
        try:
            blocks = json.loads(c)
            return " ".join(b.get("text", "") for b in blocks if isinstance(b, dict)).strip()
        except Exception:
            pass
    return c


def is_tool_output(text):
    """Marker text in a user row. NOT tool output: current code never stores tool results as user
    rows (norepinephrine.md §6 correction). The 4 matches are Sky's own pastes; kept as an option."""
    return text.startswith("[EXECUTION RESULT]") or "[UNTRUSTED_TOOL_OUTPUT]" in text[:200]


def parse_ts(ts):
    try:
        return datetime.datetime.fromisoformat(str(ts)).timestamp()
    except Exception:
        return None


def load(db, users, lab):
    uri = "file:" + db.replace("\\", "/") + "?mode=ro&immutable=1"
    import sqlite3
    con = sqlite3.connect(uri, uri=True)
    if lab:
        q = ("select username, persona, role, content, timestamp from conversations "
             "where username like ? order by username, persona, timestamp, id")
        args = (LAB_PREFIX + "%",)
    else:
        q = (f"select username, persona, role, content, timestamp from conversations "
             f"where username in ({','.join('?' * len(users))}) order by username, persona, timestamp, id")
        args = tuple(users)
    convs = defaultdict(list)
    for u, p, role, content, ts in con.execute(q, args):
        t = text_of(content)
        if t and role in ("user", "assistant"):
            convs[(u, p)].append({"role": role, "text": t, "ts": parse_ts(ts), "ts_raw": ts})
    con.close()
    return convs


def replay(events, vecs, variant, a):
    """The §3 arithmetic with firing disabled. Returns one row per user message."""
    conv, m, seen, last_ts = None, None, 0, None
    rows = []

    def ema(c, e, alpha):
        v = (1 - alpha) * c + alpha * e
        return v / (np.linalg.norm(v) or 1.0)

    for ev, e in zip(events, vecs):
        if ev["role"] == "assistant":
            if variant == "both" and conv is not None:
                conv = ema(conv, e, a.alpha_c)
            continue
        row = {"ts": ev["ts"], "ts_raw": ev["ts_raw"], "text": ev["text"], "words": len(ev["text"].split()),
               "status": "", "s": None, "delta": None, "m_before": None}
        if a.skip_tool_markers and is_tool_output(ev["text"]):
            row["status"] = "tool"
            rows.append(row)
            continue
        if last_ts is not None and ev["ts"] is not None and ev["ts"] - last_ts > a.session_gap:
            conv, seen = None, 0
            if not a.keep_m:
                m = None
            row["session_start"] = True
        if ev["ts"] is not None:
            last_ts = ev["ts"]
        if row["words"] < a.min_words:
            row["status"] = "short"
            rows.append(row)
            continue
        if conv is None:
            conv, seen = e.copy(), 1
            row["status"] = "seed"
            rows.append(row)
            continue
        s = float(1.0 - np.dot(e, conv))
        row["s"] = s
        if m is None or (not a.keep_m and seen < a.warmup):
            row["status"] = "warmup"
            m = s if m is None else m + a.alpha_m * (s - m)
        else:
            row["status"] = "eval"
            row["m_before"] = m
            row["delta"] = s - m
            m = m + a.alpha_m * (s - m)
        conv = ema(conv, e, a.alpha_c)
        seen += 1
        rows.append(row)
    return rows


def episodes(rows, thr, refractory):
    """Exceedances, and how many survive a refractory window (a fire suppresses the next R s)."""
    raw, kept, until = 0, 0, -1.0
    for r in rows:
        if r["delta"] is not None and r["delta"] > thr:
            raw += 1
            if r["ts"] is None or r["ts"] >= until:
                kept += 1
                until = (r["ts"] or 0) + refractory
    return raw, kept


def pct(xs, qs=(50, 75, 90, 95, 99)):
    if not xs:
        return "n/a"
    v = np.percentile(np.array(xs), qs)
    return "  ".join(f"p{q}={x:.3f}" for q, x in zip(qs, v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--users", default="Sky", help="comma list of real accounts")
    ap.add_argument("--min-words", type=int, default=4)
    ap.add_argument("--warmup", type=int, default=2, help="messages after a reset that never evaluate")
    ap.add_argument("--session-gap", type=float, default=7200.0)
    ap.add_argument("--alpha-m", type=float, default=0.25, help="expected-shift EMA (dopamine's R-W rate)")
    ap.add_argument("--alpha-c", type=float, default=0.35, help="conversation-vector EMA")
    ap.add_argument("--refractory", type=float, default=300.0)
    ap.add_argument("--top", type=int, default=12, help="real turns listed in fires.md per variant")
    ap.add_argument("--keep-m", action="store_true",
                    help="a session gap resets CONV only; M (this user's usual shift) carries over")
    ap.add_argument("--skip-tool-markers", action="store_true",
                    help="skip user rows carrying [EXECUTION RESULT]/[UNTRUSTED_TOOL_OUTPUT] (off: live NE has no such filter)")
    ap.add_argument("--personas", default="", help="comma list to restrict REAL to (e.g. rick)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL)

    users = [u.strip() for u in a.users.split(",") if u.strip()]
    sets = {"REAL": load(a.db, users, lab=False), "LABEL": load(a.db, users, lab=True)}
    if a.personas:
        keep = {p.strip() for p in a.personas.split(",") if p.strip()}
        sets["REAL"] = {k: v for k, v in sets["REAL"].items() if k[1] in keep}
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(HERE, "live_out", f"ne_calib_{stamp}")
    os.makedirs(out, exist_ok=True)

    results = {}  # (set, variant) -> [(key, row)]
    for name, convs in sets.items():
        for key, events in convs.items():
            vecs = model.encode([ev["text"] for ev in events], normalize_embeddings=True,
                                convert_to_numpy=True, show_progress_bar=False)
            for variant in ("user", "both"):
                for r in replay(events, vecs, variant, a):
                    results.setdefault((name, variant), []).append((key, r))

    L = [f"# NE calibration {stamp}", "",
         f"db `{a.db}` (read-only), model `{MODEL}`, users {users}",
         f"min-words {a.min_words}, warmup {a.warmup}, session-gap {a.session_gap:.0f}s, "
         f"alpha_m {a.alpha_m}, alpha_c {a.alpha_c}, refractory {a.refractory:.0f}s, "
         f"keep-m {a.keep_m}, tool-marker rows {'skipped' if a.skip_tool_markers else 'kept'}, "
         f"personas {a.personas or 'all'}", ""]

    # --- REAL ---
    real_user = results.get(("REAL", "user"), [])
    by_conv = defaultdict(lambda: defaultdict(int))
    for (u, p), r in real_user:
        by_conv[f"{u}/{p}"][r["status"]] += 1
        by_conv[f"{u}/{p}"]["sessions"] += int(bool(r.get("session_start")))
    L += ["## REAL: what there is to calibrate on", "",
          "| conversation | user msgs | tool | short | seed | warmup | evaluated | session resets |",
          "|---|---|---|---|---|---|---|---|"]
    for k, c in sorted(by_conv.items()):
        n = sum(c[s] for s in ("tool", "short", "seed", "warmup", "eval"))
        L.append(f"| {k} | {n} | {c['tool']} | {c['short']} | {c['seed']} | {c['warmup']} | {c['eval']} | {c['sessions']} |")
    words = [r["words"] for _, r in real_user]
    L += ["", f"word counts: {pct(words, (10, 25, 50, 75, 90))}; "
          + ", ".join(f"<{w} words: {sum(x < w for x in words)}" for w in (3, 4, 5, 6)), ""]

    for variant in ("user", "both"):
        rows = [r for _, r in results.get(("REAL", variant), [])]
        ev = [r for r in rows if r["status"] == "eval"]
        L += [f"### CONV = {variant}", "",
              f"evaluated turns: {len(ev)}",
              f"s     : {pct([r['s'] for r in rows if r['s'] is not None])}",
              f"delta : {pct([r['delta'] for r in ev])}", "",
              "| delta > | fires (raw) | per 100 turns | episodes after refractory |", "|---|---|---|---|"]
        per_conv = defaultdict(list)
        for k, r in results.get(("REAL", variant), []):
            per_conv[k].append(r)
        for thr in THRESHOLDS:
            raw = kept = 0
            for rs in per_conv.values():
                x, y = episodes(rs, thr, a.refractory)
                raw, kept = raw + x, kept + y
            L.append(f"| {thr:.2f} | {raw} | {100 * raw / max(1, len(ev)):.1f} | {kept} |")
        L.append("")

    # --- LABEL ---
    L += ["## LABEL: gaba_live scripted runs (known frame breaks)", "",
          "Rank = position of that turn's delta among all evaluated turns of its run (1 = biggest).", ""]
    for variant in ("user", "both"):
        per_conv = defaultdict(list)
        for k, r in results.get(("LABEL", variant), []):
            per_conv[k].append(r)
        L += [f"### CONV = {variant}", "", "| run | turns eval | D delta (rank) | E delta (rank) | B1 delta (rank) | A deltas in order |",
              "|---|---|---|---|---|---|"]
        for (u, p), rs in sorted(per_conv.items()):
            ev = [r for r in rs if r["status"] == "eval"]
            order = sorted(ev, key=lambda r: -r["delta"])

            def cell(lab):
                hit = [r for r in ev if label_of(r["text"]) == lab]
                if not hit:
                    return "-"
                r = hit[-1]
                return f"{r['delta']:+.3f} ({order.index(r) + 1}/{len(ev)})"
            a_seq = [f"{label_of(r['text'])}:{r['delta']:+.2f}" for r in ev if label_of(r["text"]).startswith("A")]
            L.append(f"| {u}/{p} | {len(ev)} | {cell('D')} | {cell('E')} | {cell('B1')} | {' '.join(a_seq) or '-'} |")
        L.append("")

    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    # --- fires.md: the real turns it would fire on, as text, for Sky to judge ---
    with open(os.path.join(out, "fires.md"), "w", encoding="utf-8") as f:
        f.write("# Biggest surprise turns in REAL history (contains chat text; live_out is gitignored)\n\n")
        for variant in ("user", "both"):
            f.write(f"## CONV = {variant}\n\n")
            per_conv = defaultdict(list)
            for k, r in results.get(("REAL", variant), []):
                per_conv[k].append(r)
            flat = []
            for k, rs in per_conv.items():
                prev = None
                for r in rs:
                    if r["status"] == "eval":
                        flat.append((r["delta"], k, r, prev))
                    if r["status"] != "short":
                        prev = r
            for d, (u, p), r, prev in sorted(flat, key=lambda x: -x[0])[:a.top]:
                f.write(f"**{u}/{p}  {r['ts_raw']}  delta {d:+.3f}  s {r['s']:.3f}  (expected {r['m_before']:.3f})**\n\n")
                if prev:
                    f.write(f"> before: {prev['text'][:220]}\n\n")
                f.write(f"> this: {r['text'][:300]}\n\n---\n")

    with open(os.path.join(out, "turns.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["set", "variant", "username", "persona", "ts", "status", "words", "s", "delta", "label"])
        for (name, variant), items in sorted(results.items()):
            for (u, p), r in items:
                w.writerow([name, variant, u, p, r["ts_raw"], r["status"], r["words"],
                            "" if r["s"] is None else f"{r['s']:.4f}",
                            "" if r["delta"] is None else f"{r['delta']:.4f}",
                            label_of(r["text"]) if name == "LABEL" else ""])

    print("\n".join(L))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
