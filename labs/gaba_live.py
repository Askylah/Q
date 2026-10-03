"""GABA live test driver -- runs the "boring user" plan against the LIVE app.

lab_notes/gaba_inhibition.md, "Next session (2026-09-13)" item 1: the organ is built but
has never seen a real conversation. This drives one through /chat/rick/stream exactly as
the frontend would (stream, then POST the assistant reply so history sees it), on a
throwaway account, and then reads the telemetry sink back for that account.

Shape (why 5 + 5 + N + 1 + wait + 1): the reflector fires when >= turn_threshold (5) raw
events have piled up since the last dense_observation, then compares the newest 5 raw
events with the 5 before them (MiniLM cosine). >= REFLECT_SIM_CEIL (0.85) -> shrug ->
gaba/redundant, streak+1. Only user_message rows are raw events (gaba note §8.1, checked
again in the live DB 2026-09-18): Rick's replies are NOT in the window. After the first
shrug the watermark does not advance, so every further dull user turn is another shrug
(run 20260918_214952: shrug at turn 16 with new_events=5, again at 17 with new_events=6).
Crossing 0.50 takes five shrugs at weight ~1, i.e. about 5 + 4 = 9 dull user turns after
the last store. The 17-turn default gives phase C five turns = ONE shrug; --c-turns 10
(22 turns, ~28 min) is the plan that can cross.

    A  turns 1-5      five unrelated topics          -> reflect/reflect, gaba/novel, tonic up
    B  turns 6-10     one topic, rephrased           -> nearest ~0.5, still reflect
    C  turns 11-10+N  turn 10 near-verbatim x N      -> shrugs from turn 15 on; N=10 -> crossed=true
    D  next turn      brand-new topic                -> gaba/novel, streak 0, gate STILL shut
       wait 600 s     nothing                        -> drain; da/boost daemon_gap rows
    E  last turn      brand-new topic                -> does the gate reopen, and how long after

    python labs/gaba_live.py --dry                 # print the plan, send nothing
    python labs/gaba_live.py                       # ~22 min: 17 turns @30 s + 10 min wait + 2 min quiet
    python labs/gaba_live.py --c-turns 10          # ~28 min: 22 turns, the crossing run
    python labs/gaba_live.py --gap 20 --wait 300   # faster (drain numbers then mean less)
    python labs/gaba_live.py --messages plan.json  # your own plan: ["text", ...] or [{"phase": "C", "text": ...}, ...]
    python labs/gaba_live.py --user gaba_live      # reuse an account instead of gaba_live_<stamp>
    python labs/gaba_live.py --digest-only <stamp> # re-read the sink for an earlier run

Each run registers its own account, gaba_live_<stamp>: run 20260918_214952 inherited the
ten user rows of the killed run before it, reflected on turn 1 instead of 5, and every
window after that sat one turn off the phase plan.

A reply that is exactly "⚠️ Connection Error: No API key provided." is not Rick: on the
Vertex route llm_engine.call_llm only reaches that string on attempt 2, after a 429 or
401/403 from Vertex made the fallback swap the provider to anthropic with no key. The real
status is on the app terminal ([VERTEX FALLBACK] / [ROUTER] lines), not in the sink. The
driver counts such a turn as an error and does not save it as an assistant message.

Pre-flight it does NOT do for you: app up on :8000, stream_worker.py alive, Redis up
(without Redis the chat process and the daemon each keep private in-memory inhibition).
Safe against the live tree: lives in labs/ (reload-excluded), writes only labs/live_out/.
"""
import argparse, csv, json, os, sys, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
assert os.path.basename(ROOT) == "PersonaApp-merged", f"refusing to run outside the live repo: {ROOT}"
sys.path.insert(0, ROOT)
import requests  # noqa: E402

USERNAME = "gaba_live"          # overwritten in main(): gaba_live_<stamp> unless --user
PERSONA = "rick"
SECRET = "gaba-live-2026-09-18"
BASE_URL = "http://127.0.0.1:8000"
HDR = {"X-Profile-Username": USERNAME, "X-Profile-Key": SECRET}
OUT_ROOT = os.path.join(HERE, "live_out")
ERROR_PREFIX = "⚠️"   # the app's error replies all start with this

# ---------------------------------------------------------------- the messages
T10 = ("Still on the bread thing. My kitchen sits around 68 F and the recipe says the dough "
       "should double in 90 minutes, but mine takes closer to three hours every single time. "
       "Same flour, same yeast packet, same amount of water. What is actually slowing the rise "
       "down in my kitchen, and is there one thing I can change to get it back to 90 minutes?")

PHASE_A = [
    "Quick chemistry one. Why does a copper pan turn a flame green but a steel pan does nothing? "
    "I get that it is electrons dropping levels, but why that color for copper specifically?",
    "Random memory. When I was seven our dog Biscuit figured out how to open the screen door "
    "with her nose and let herself out every afternoon. Nobody ever taught her. How much of "
    "that is learning versus just a dog being a dog?",
    "Have you ever looked at the board game Tigris and Euphrates? I keep hearing it is the best "
    "design of the nineties and I cannot tell from the rules why. What makes a board game "
    "actually deep instead of just complicated?",
    "If you had a week alone in a cabin with no internet, what would you actually do with it? "
    "Not the postcard answer. I am trying to decide if I would go crazy by day three.",
    "My car does a shudder at exactly 40 mph and nowhere else. Not braking, not accelerating, "
    "just cruising at 40. Mechanic says tires, I am not convinced. What resonates at one speed?",
]
PHASE_B = [
    "Different thing. I have been baking bread and the dough rises way slower than the recipe "
    "says. My kitchen is about 68 F, the recipe says double in 90 minutes, mine takes almost "
    "three hours. Same flour, same yeast, same water. Why is my rise so slow?",
    "So about the slow bread rise. Kitchen is 68 F, recipe expects doubling in 90 minutes, I "
    "get three hours, same ingredients as the recipe. You mentioned temperature but that seems "
    "like a small difference. What is really slowing my dough down?",
    "Back to the dough. I still do not get it. 68 F kitchen, the recipe promises 90 minutes to "
    "double, mine needs three hours, and I am using the exact flour and yeast it lists. Is it "
    "the temperature, the yeast, or something about my kitchen?",
    "Let me ask the bread question one more way. Same recipe, same flour, same yeast packet, "
    "my kitchen at 68 F, and my dough takes three hours to double instead of the ninety "
    "minutes the recipe says. What is the single biggest reason for the slow rise?",
    T10,
]
# C: turn 10 near-verbatim, <= 5 words changed each; the first --c-turns of these are used
PHASE_C = [
    T10.replace("Still on the bread thing.", "Still on this bread thing."),
    T10.replace("every single time", "every time").replace("What is actually", "What is really"),
    T10.replace("sits around 68 F", "is around 68 F").replace("closer to three hours", "nearly three hours"),
    T10.replace("Same flour, same yeast packet, same amount of water.", "Same flour, same yeast, same water."),
    T10.replace("and is there one thing", "and is there a single thing").replace("back to 90 minutes", "down to 90 minutes"),
    T10.replace("Still on the bread thing.", "Bread again."),
    T10.replace("the recipe says the dough", "the recipe claims the dough").replace("in my kitchen, and", "in my kitchen, so"),
    T10.replace("mine takes closer to", "mine needs closer to").replace("one thing I can change", "one thing I could change"),
    T10.replace("My kitchen sits around", "My kitchen stays around").replace("every single time", "every single bake"),
    T10.replace("What is actually slowing", "What is truly slowing").replace("get it back to", "bring it back to"),
]
PHASE_D = ("Totally different. Why do some people get a metallic taste right before a thunderstorm? "
           "My grandmother swore by it and I always thought it was folklore, but I had it yesterday "
           "twenty minutes before the sky opened up.")
PHASE_E = ("New one. The Antikythera mechanism has gearing nobody matched for a thousand years. What "
           "does that say about how much got lost, and is there anything today we would lose the same way?")


def build_plan(c_turns=5):
    """[(phase, text), ...] -- A x5, B x5, C x c_turns, D, E."""
    assert 1 <= c_turns <= len(PHASE_C), f"--c-turns must be 1..{len(PHASE_C)}"
    return ([("A", m) for m in PHASE_A] + [("B", m) for m in PHASE_B] +
            [("C", m) for m in PHASE_C[:c_turns]] + [("D", PHASE_D), ("E", PHASE_E)])


def load_plan(path):
    """JSON list of strings (17 -> default phases, else '?') or of {"phase", "text"} objects."""
    raw = json.load(open(path, encoding="utf-8"))
    assert isinstance(raw, list) and raw, "need a non-empty JSON list"
    if all(isinstance(m, str) for m in raw):
        if len(raw) == 17:
            return [(p, m) for (p, _), m in zip(build_plan(5), raw)]
        return [("?", m) for m in raw]
    return [(str(m.get("phase", "?")), m["text"]) for m in raw]


def now_iso():
    return datetime.datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------- app calls
def register(session):
    r = session.post(f"{BASE_URL}/auth/register", json={"username": USERNAME, "secret_key": SECRET}, timeout=30)
    if r.status_code not in (200, 400):
        raise RuntimeError(f"register: {r.status_code} {r.text[:200]}")
    r = session.post(f"{BASE_URL}/auth/verify", json={"username": USERNAME, "secret_key": SECRET}, timeout=30)
    r.raise_for_status()


def stream_turn(session, message, history, raw_path, model, thinking):
    body = {"username": USERNAME, "message": message, "chat_history": history[-8:],
            "target_model_id": model, "thinking_level": thinking}
    t_send = time.time()
    t_first = None
    chunks, finish, n_events, http = [], None, 0, 200
    with open(raw_path, "a", encoding="utf-8", newline="") as raw:
        raw.write(f"# send {t_send:.3f} {now_iso()} history_msgs={len(history[-8:])}\n")
        with session.post(f"{BASE_URL}/chat/{PERSONA}/stream", json=body, headers=HDR, stream=True,
                          timeout=(30, 600)) as r:
            if r.status_code != 200:
                raw.write(f"# HTTP {r.status_code} {r.text[:500]}\n")
                return {"t_send": t_send, "t_first": None, "t_end": time.time(), "text": "",
                        "finish": None, "http": r.status_code, "n_events": 0}
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if t_first is None:
                    t_first = time.time()
                raw.write(f"{time.time() - t_send:8.3f} {line}\n")
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                n_events += 1
                try:
                    j = json.loads(payload)
                except Exception:
                    continue
                if "control" in j:
                    continue
                ch = j.get("choices", [{}])[0] if j.get("choices") else {}
                delta = ch.get("delta", {}).get("content")
                if delta:
                    chunks.append(delta)
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
        text = "".join(chunks)
        raw.write(f"# end {time.time():.3f} dur={time.time() - t_send:.2f}s chars={len(text)} finish={finish}\n")
    return {"t_send": t_send, "t_first": t_first, "t_end": time.time(), "text": text,
            "finish": finish, "http": http, "n_events": n_events}


def save_assistant(session, text):
    """Mirror the frontend: POST the assistant reply so history sees it."""
    r = session.post(f"{BASE_URL}/chat/{PERSONA}", json={"username": USERNAME, "role": "assistant", "content": text},
                     headers=HDR, timeout=30)
    return r.status_code


# ---------------------------------------------------------------- telemetry digest
def digest(t_start, t_end, wait_window, out_dir, username=None):
    import telemetry  # repo root, sys.path above
    username = username or USERNAME
    rows = [r for r in telemetry.read_range(2)
            if r.get("username") == username and t_start - 5 <= float(r.get("ts", 0)) <= t_end + 5]
    rows.sort(key=lambda r: r["ts"])
    lines = []

    def say(s=""):
        lines.append(s)
        print(s)

    say(f"\n===== telemetry digest for {username}/{PERSONA}: {len(rows)} rows "
        f"{datetime.datetime.fromtimestamp(t_start):%H:%M:%S} -> {datetime.datetime.fromtimestamp(t_end):%H:%M:%S}")
    say("--- every non-heartbeat row, in order (daemon/gate rows only when something changed)")
    last_gate = None
    for r in rows:
        ch, ev = r.get("channel"), r.get("event")
        rel = r["ts"] - t_start
        if ch == "daemon" and ev == "gate":
            key = (r.get("gaba_shut"), r.get("exploring"), round(float(r.get("inhibition", 0)), 2),
                   round(float(r.get("tonic", 0)), 2))
            if key == last_gate:
                continue
            last_gate = key
        extra = {k: v for k, v in r.items() if k not in ("ts", "iso", "pid", "channel", "event", "username", "persona")}
        say(f"{rel:8.1f}s  {ch}/{ev:<12} " + " ".join(f"{k}={v}" for k, v in extra.items()))

    def pick(ch, ev=None):
        return [r for r in rows if r.get("channel") == ch and (ev is None or r.get("event") == ev)]

    say("\n--- the numbers the note is waiting on")
    verdicts = {}
    for r in pick("reflect"):
        verdicts[r["event"]] = verdicts.get(r["event"], 0) + 1
    say(f"reflect verdicts: {verdicts}")
    shrugs = pick("reflect", "shrug")
    say(f"shrug nearest values (§7.3 REFLECT_SIM_CEIL): {[round(float(r.get('nearest', -1)), 3) for r in shrugs]}")
    refl = [round(float(r["nearest"]), 3) for r in pick("reflect", "reflect") if r.get("nearest") is not None]
    say(f"reflect (moved) nearest values: {refl}")
    red = pick("gaba", "redundant")
    say(f"gaba/redundant: {len(red)} rows; by source: "
        f"{ {s: sum(1 for r in red if r.get('source') == s) for s in set(r.get('source') for r in red)} }")
    for r in red:
        say(f"   +{r['ts'] - t_start:6.1f}s streak={r.get('streak')} nearest={r.get('nearest')} "
            f"intensity={r.get('intensity')} weight={r.get('weight')} before={r.get('before')} "
            f"after={r.get('after', r.get('inhibition'))} crossed={r.get('crossed')} source={r.get('source')}")
    nov = pick("gaba", "novel")
    say(f"gaba/novel: {len(nov)} rows at " + ", ".join(f"+{r['ts'] - t_start:.0f}s" for r in nov))
    crossed = [r for r in red if r.get("crossed")]
    say(f"first crossed=true: " + (f"+{crossed[0]['ts'] - t_start:.1f}s (streak {crossed[0].get('streak')}, "
                                    f"source {crossed[0].get('source')})" if crossed else "never"))
    gates = pick("daemon", "gate")
    shut = [r for r in gates if r.get("gaba_shut")]
    say(f"daemon/gate rows: {len(gates)}; gaba_shut=true rows: {len(shut)}")
    if shut:
        f, l = shut[0], shut[-1]
        say(f"   first shut +{f['ts'] - t_start:.0f}s tonic={f.get('tonic')} inhibition={f.get('inhibition')}; "
            f"last shut +{l['ts'] - t_start:.0f}s tonic={l.get('tonic')} inhibition={l.get('inhibition')}")
        after = [r for r in gates if r["ts"] > l["ts"] and not r.get("gaba_shut")]
        say(f"   reopened: " + (f"+{after[0]['ts'] - t_start:.0f}s, {after[0]['ts'] - l['ts']:.0f}s after last shut row "
                                f"(inhibition {after[0].get('inhibition')})" if after else "not within the run"))
    tonic_max = max((float(r.get("tonic", 0)) for r in gates), default=None)
    say(f"tonic seen by the daemon: max {tonic_max} (gate needs >= 0.50 AND inhibition < 0.50)")
    for ev in ("gate_close", "gate_open"):
        for r in pick("daemon", ev):
            say(f"daemon/{ev} +{r['ts'] - t_start:.0f}s closed_by={r.get('closed_by')} "
                f"tonic={r.get('tonic')} inhibition={r.get('inhibition')}")
    boosts = [r for r in pick("da", "boost") if wait_window and wait_window[0] <= r["ts"] <= wait_window[1]]
    say(f"da/boost daemon_gap during the wait: {len(boosts)}")
    novs = pick("da", "novelty")
    say(f"da/novelty paid>0: {sum(1 for r in novs if float(r.get('paid', 0) or 0) > 0)} of {len(novs)}")
    with open(os.path.join(out_dir, "digest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(out_dir, "telemetry_rows.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    say(f"\nwritten: {out_dir}\\digest.txt, telemetry_rows.jsonl, turns.csv, raw_sse.txt")


# ---------------------------------------------------------------- main
def main():
    global USERNAME, HDR
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--gap", type=float, default=30.0, help="seconds between turns (default 30)")
    ap.add_argument("--wait", type=float, default=600.0, help="silence after the D turn (default 600 = GABA_TAU_SEC)")
    ap.add_argument("--quiet", type=float, default=120.0, help="silence after the last turn before the digest")
    ap.add_argument("--c-turns", type=int, default=5, help=f"near-verbatim turns in phase C, 1..{len(PHASE_C)} (default 5; 10 to cross 0.50)")
    ap.add_argument("--messages", help="JSON file: list of strings, or of {\"phase\", \"text\"} objects; any length")
    ap.add_argument("--user", help="account to use (default: gaba_live_<stamp>, fresh per run)")
    ap.add_argument("--model", default="google/gemini-3-flash-preview")
    ap.add_argument("--thinking", default="Off", help="Off keeps 3-flash from blanking (§6.9)")
    ap.add_argument("--start-at", type=int, default=1, help="resume from turn N (history is rebuilt empty)")
    ap.add_argument("--digest-only", metavar="STAMP", help="re-read the sink for labs/live_out/<STAMP>")
    args = ap.parse_args()

    if args.digest_only:
        out_dir = os.path.join(OUT_ROOT, args.digest_only)
        meta = json.load(open(os.path.join(out_dir, "meta.json")))
        digest(meta["t_start"], meta["t_end"], meta.get("wait_window"), out_dir,
               username=meta.get("username", "gaba_live"))
        return

    plan = load_plan(args.messages) if args.messages else build_plan(args.c_turns)
    n = len(plan)
    wait_after = n - 1 if n >= 2 else n   # the D turn; E is the last one
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    USERNAME = args.user or f"gaba_live_{stamp}"
    HDR = {"X-Profile-Username": USERNAME, "X-Profile-Key": SECRET}

    print(f"[plan] {USERNAME}/{PERSONA} @ {BASE_URL}  model={args.model} thinking={args.thinking} "
          f"gap={args.gap:.0f}s wait={args.wait:.0f}s (after turn {wait_after}) quiet={args.quiet:.0f}s  {n} turns")
    for i, (p, m) in enumerate(plan, 1):
        print(f"  {i:2d} [{p}] {m[:90]}{'...' if len(m) > 90 else ''}")
    est = (n - 1) * (args.gap + 15) + args.wait + 15 + args.quiet
    print(f"[plan] ~{est / 60:.0f} min if replies take ~15 s")
    if args.dry:
        return

    for url, what in ((f"{BASE_URL}/docs", "app"),):
        try:
            requests.get(url, timeout=5)
        except Exception as e:
            sys.exit(f"{what} not reachable at {BASE_URL}: {e}")

    out_dir = os.path.join(OUT_ROOT, stamp)
    os.makedirs(out_dir, exist_ok=True)
    raw_path = os.path.join(out_dir, "raw_sse.txt")
    s = requests.Session()
    register(s)
    t_start = time.time()
    history = []
    wait_window = None
    blanks = errors = 0
    with open(os.path.join(out_dir, "turns.csv"), "w", newline="", encoding="utf-8") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(["turn", "phase", "t_send_rel", "ttfb_s", "dur_s", "reply_chars", "finish", "http", "save_http"])
        for i, (phase, m) in enumerate(plan, 1):
            if i < args.start_at:
                continue
            print(f"\n[{now_iso()}] turn {i:2d} [{phase}] -> {m[:70]}...")
            res = stream_turn(s, m, history, raw_path, args.model, args.thinking)
            text = res["text"].strip()
            ttfb = (res["t_first"] - res["t_send"]) if res["t_first"] else None
            dur = res["t_end"] - res["t_send"]
            save_http = ""
            finish = res["finish"]
            if text.startswith(ERROR_PREFIX):
                errors += 1
                finish = "error"
                history += [{"role": "user", "content": m}]
                print(f"   <- APP ERROR after {dur:.1f}s, not saved: {text[:120]}")
                print("      (on the Vertex route this means attempt 1 was refused; see [VERTEX FALLBACK] on the app terminal)")
            elif text:
                save_http = save_assistant(s, res["text"])
                history += [{"role": "user", "content": m}, {"role": "assistant", "content": res["text"]}]
                print(f"   <- {len(text)} chars in {dur:.1f}s (ttfb {ttfb or 0:.1f}s): {text[:110].replace(chr(10), ' ')}...")
            else:
                blanks += 1
                history += [{"role": "user", "content": m}]
                print(f"   <- BLANK reply (http {res['http']}, finish {res['finish']}) -- not saved; window gets the user msg only")
            w.writerow([i, phase, f"{res['t_send'] - t_start:.1f}", f"{ttfb or 0:.2f}", f"{dur:.2f}",
                        len(text), finish, res["http"], save_http])
            fcsv.flush()
            if i == wait_after:
                wait_window = (time.time(), time.time() + args.wait)
                print(f"\n[{now_iso()}] --- waiting {args.wait:.0f}s in silence (drain + daemon self-boosts) ---")
                time.sleep(args.wait)
            elif i < n:
                time.sleep(args.gap)
    print(f"\n[{now_iso()}] --- quiet {args.quiet:.0f}s, then digest ---")
    time.sleep(args.quiet)
    t_end = time.time()
    json.dump({"t_start": t_start, "t_end": t_end, "wait_window": wait_window, "blanks": blanks, "errors": errors,
               "username": USERNAME, "turns": n, "c_turns": None if args.messages else args.c_turns,
               "model": args.model, "thinking": args.thinking, "gap": args.gap, "wait": args.wait},
              open(os.path.join(out_dir, "meta.json"), "w"), indent=1)
    if blanks or errors:
        print(f"WARNING: {blanks} blank and {errors} error replies -- those turns put only the user message into history")
    digest(t_start, t_end, wait_window, out_dir)


if __name__ == "__main__":
    main()
