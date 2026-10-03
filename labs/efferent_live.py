"""Efferent live turn -- two real chat turns on a throwaway account, one at rest, one seeded.

Turn 1 (rest): fresh account, no organ keys -> efferent must be the identity: temperature and
max_tokens unchanged, band "".
Turn 2 (seeded): tonic is pushed over EXPLORE_THRESHOLD through the organ's own public
boost_tonic() (Redis, so the app process reads it), then one turn -> band "exploring",
temperature up, a [DISPOSITION] block at the end of the prompt.

The seed is SYNTHETIC: this shows the wire carries state into a live request, not that a real
conversation earns the state. Evidence is the efferent/turn telemetry rows plus the app
terminal's [EFFERENT] line; the assembled prompt is not visible from out here.

    python labs/efferent_live.py            # 2 turns, ~1 min, a few cents of flash
    python labs/efferent_live.py --boost 0.3
    python labs/efferent_live.py --root C:\\path\\to\\PersonaApp-merged

--root is the LIVE checkout (the one the app on :8000 runs from): organs, telemetry and the
gaba_live driver are imported from there, read-only. Output goes next to this file, in
labs/live_out/efferent_<stamp>/, so a run from a worktree never writes into the live tree.
"""
import argparse, datetime, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))


def _default_root():
    """This file's repo, or the base checkout when it sits in .claude/worktrees/<name>/."""
    root = os.path.dirname(HERE)
    parts = root.replace("\\", "/").split("/")
    if ".claude" in parts:
        root = "/".join(parts[:parts.index(".claude")])
    return root


REST_MSG = "Quick one. Why does a copper pan turn a flame green but a steel pan does nothing?"
SEEDED_MSG = ("Different thing. Why do octopuses have most of their neurons in their arms, and "
              "what does that do to the idea of a single self?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=_default_root())
    ap.add_argument("--boost", type=float, default=0.30, help="tonic boost before turn 2 (default 0.30 -> ~0.60)")
    ap.add_argument("--model", default="google/gemini-3-flash-preview")
    ap.add_argument("--thinking", default="Off")
    a = ap.parse_args()

    sys.path.insert(0, a.root)
    sys.path.insert(0, os.path.join(a.root, "labs"))
    import requests
    import gaba_live as gl
    import dopamine_state as ds
    import telemetry

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    gl.USERNAME = f"efferent_live_{stamp}"
    gl.HDR = {"X-Profile-Username": gl.USERNAME, "X-Profile-Key": gl.SECRET}
    out_dir = os.path.join(HERE, "live_out", f"efferent_{stamp}")
    os.makedirs(out_dir, exist_ok=True)
    raw_path = os.path.join(out_dir, "raw_sse.txt")

    if ds._RCONN is None:
        sys.exit("Redis not reachable from here: the seed would stay in this process. Aborting.")

    s = requests.Session()
    gl.register(s)
    t0 = time.time()
    history, turns = [], []
    for label, msg in (("rest", REST_MSG), ("seeded", SEEDED_MSG)):
        if label == "seeded":
            st = ds.boost_tonic(gl.USERNAME, gl.PERSONA, a.boost)
            print(f"[seed] boost_tonic({a.boost}) -> {st}", flush=True)
        print(f"[{gl.now_iso()}] {label}: sending", flush=True)
        r = gl.stream_turn(s, msg, history, raw_path, a.model, a.thinking)
        text = r["text"]
        bad = (not text) or text.startswith(gl.ERROR_PREFIX) or r["http"] != 200
        print(f"    http={r['http']} finish={r['finish']} chars={len(text)} dur={r['t_end'] - r['t_send']:.1f}s"
              f"{'  ** ERROR REPLY **' if bad else ''}", flush=True)
        history.append({"role": "user", "content": msg})
        if not bad:
            gl.save_assistant(s, text)
            history.append({"role": "assistant", "content": text})
        turns.append({"label": label, "message": msg, "reply": text, "chars": len(text),
                      "finish": r["finish"], "http": r["http"], "error": bad})
        time.sleep(3)

    rows = [x for x in telemetry.read_range(1)
            if x.get("channel") == "efferent" and x.get("username") == gl.USERNAME]
    print(f"\nefferent/turn rows for {gl.USERNAME}: {len(rows)}")
    for x in rows:
        print("  ", {k: x.get(k) for k in ("tonic", "phasic", "inhibition", "streak", "temperature_in",
                                           "temperature_out", "max_tokens_in", "max_tokens_out", "band")})
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump({"username": gl.USERNAME, "t_start": t0, "turns": turns, "efferent_rows": rows}, f,
                  indent=2, ensure_ascii=False, default=str)
    print(f"\nwrote {out_dir}")


if __name__ == "__main__":
    main()
