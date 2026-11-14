"""Replay a gaba_live run's user messages through the Reflector gate, old vs fix (b).

gaba note §11-§12. Read-only against the live DB (mode=ro&immutable=1), no LLM calls: the
gate only needs embeddings, and this uses the app's own plugins/memory_plugin
_chunk_similarity / _nearest_among on the shared MiniLM. The watermark is simulated: every
"reflect" verdict appends a dense_observation, as a successful store would.

Honesty check first: the OLD gate (no nearest_fn) must reproduce the run's live verdicts from
its telemetry rows. If it doesn't, the simulation is wrong and the fix-(b) column means nothing.

    python labs/gaba_window_replay.py                       # run 20261011_110043
    python labs/gaba_window_replay.py --run 20261011_110043
"""
import argparse, json, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(1, os.path.join(ROOT, "plugins"))
sys.path.insert(2, ROOT)
os.environ.setdefault("TELEMETRY_OFF", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import reflect_gate as rg          # noqa: E402
import memory_plugin as mp         # noqa: E402

DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "PersonaApp", "users.db")
PHASES = ["A"] * 5 + ["B"] * 5 + ["C"] * 10 + ["D", "E"]


def simulate(msgs, nearest_fn):
    logs, out = [], []
    for text in msgs:
        logs.append({"type": "user_message", "content": text})
        plan = rg.plan_reflection(logs, 5, similarity=mp._chunk_similarity,
                                  sim_ceil=mp.REFLECT_SIM_CEIL, nearest_fn=nearest_fn)
        out.append(plan)
        if plan["verdict"] == "reflect":
            logs.append({"type": "dense_observation", "content": "(simulated store)"})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20261011_110043")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    run_dir = os.path.join(HERE, "live_out", a.run)
    meta = json.load(open(os.path.join(run_dir, "meta.json"), encoding="utf-8"))
    user = meta["username"]

    con = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro&immutable=1", uri=True)
    msgs = [c for t, c in con.execute(
        "select event_type, content from observations where username=? and persona='rick' order by id", (user,))
        if t == "user_message"]
    live = []
    rows_path = os.path.join(run_dir, "telemetry_rows.jsonl")
    for line in open(rows_path, encoding="utf-8"):
        r = json.loads(line)
        if r.get("channel") == "reflect" and r.get("event") in ("wait", "reflect", "shrug"):
            live.append(r["event"])

    old = simulate(msgs, None)
    new = simulate(msgs, mp._nearest_among)
    print(f"run {a.run}  account {user}  {len(msgs)} user messages, {len(live)} live verdicts")
    print("turn ph | live    | old (replayed) | fix (b)  | window  | newest alone")
    agree = 0
    for i, (o, n) in enumerate(zip(old, new)):
        lv = live[i] if i < len(live) else "?"
        agree += lv == o["verdict"]
        w = "" if n["nearest"] is None else f"{n['nearest']:.3f}"
        nn = "" if n.get("newest_nearest") is None else f"{n['newest_nearest']:.3f}"
        flag = "  <- changed" if o["verdict"] != n["verdict"] else ""
        print(f"{i + 1:4d} {PHASES[i] if i < len(PHASES) else '?':2} | {lv:7} | {o['verdict']:14} | "
              f"{n['verdict']:8} | {w:7} | {nn}{flag}")
    print(f"\nold gate reproduces live: {agree}/{len(old)}")
    ph = {i + 1: (PHASES[i] if i < len(PHASES) else "?") for i in range(len(new))}
    c_shrugs = all(new[i - 1]["verdict"] == "shrug" for i in range(15, 21))
    d_novel = new[20]["verdict"] == "reflect" if len(new) > 20 else None
    e_not_shrug = new[21]["verdict"] != "shrug" if len(new) > 21 else None
    print(f"pass criteria: turns 15-20 still shrug: {c_shrugs} | D reflects: {d_novel} | E not a shrug: {e_not_shrug}")


if __name__ == "__main__":
    main()
