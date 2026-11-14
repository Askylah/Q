"""NE wiring check through the LIVE app, for zero model spend.

A throwaway account (its own rick copy) sends five near-identical bread turns and then the
gaba_live D frame break, through /chat/rick/stream, WITHOUT any API key. Each request runs the
zettel query, norepinephrine_state.on_user_message and efferent.for_turn before the model call,
which then fails with "No API key provided": the wiring is exercised, nothing is billed.
Then it reads the telemetry sink back: ne/turn per message, efferent/turn (alert, band_shadow),
and daemon/would_hold if the daemon sweeps the account inside --daemon-wait.

    python labs/ne_wiring_check.py
"""
import argparse, datetime, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gaba_live as gl   # noqa: E402  (register, ensure_persona, stream_turn, the phase texts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon-wait", type=float, default=150.0, help="seconds to watch for daemon/would_hold")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import requests

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    gl.USERNAME = f"ne_check_{stamp}"
    gl.HDR = {"X-Profile-Username": gl.USERNAME, "X-Profile-Key": gl.SECRET}
    gl.API_KEYS = {}                      # deliberately keyless: no model call can succeed
    out = os.path.join(HERE, "live_out", f"ne_check_{stamp}")
    os.makedirs(out, exist_ok=True)
    raw = os.path.join(out, "raw_sse.txt")

    s = requests.Session()
    gl.register(s)
    print(f"[persona] {gl.USERNAME}/rick: {gl.ensure_persona(s)}")
    msgs = [gl.PHASE_C[0], gl.PHASE_C[1], gl.PHASE_C[2], gl.PHASE_C[3], gl.PHASE_C[4], gl.PHASE_D]
    t0 = time.time()
    history = []
    for i, m in enumerate(msgs, 1):
        r = gl.stream_turn(s, m, history, raw, "google/gemini-3-flash-preview", "Off")
        print(f"  turn {i}: http {r['http']}  reply: {r['text'][:60]!r}")
        history.append({"role": "user", "content": m})
        time.sleep(3)

    sink = os.path.join(os.environ.get("LOCALAPPDATA", ""), "PersonaApp", "telemetry",
                        datetime.date.today().isoformat() + ".jsonl")
    deadline = time.time() + a.daemon_wait
    would = []
    while True:
        rows = []
        with open(sink, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("username") == gl.USERNAME and r.get("ts", 0) >= t0 - 1:
                    rows.append(r)
        would = [r for r in rows if r.get("channel") == "daemon" and r.get("event") in ("would_hold", "sweep_held")]
        if would or time.time() > deadline:
            break
        time.sleep(5)

    print("\nne/turn:")
    for r in (r for r in rows if r.get("channel") == "ne"):
        print(f"  {r.get('status'):8} s={r.get('s')} delta={r.get('delta')} fired={r.get('fired')} "
              f"alert={r.get('alert')} shadow={r.get('shadow')} embedding={r.get('embedding')}")
    print("efferent/turn:")
    for r in (r for r in rows if r.get("channel") == "efferent"):
        print(f"  band={r.get('band')!r} band_shadow={r.get('band_shadow')!r} alert={r.get('alert')} "
              f"ne_acting={r.get('ne_acting')} temp {r.get('temperature_in')}->{r.get('temperature_out')}")
    print("daemon hold:", [(r["event"], r.get("alert")) for r in would] or f"none within {a.daemon_wait:.0f}s")
    with open(os.path.join(out, "telemetry_rows.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
