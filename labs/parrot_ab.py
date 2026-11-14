"""Parrot A/B/C -- does the persona still open by reading the user's lines back to them, and
does the way the rule is worded matter?

Three throwaway custom personas on a throwaway account, same persona text, one difference:
  off : persona file as-is
  neg : persona file + the rule worded as a prohibition (NEG_LINE, names the behaviour)
  pos : persona file + the rule worded as a target (POS_LINE, never names the behaviour)
neg vs pos is Sky's negative-constraint hypothesis on one rule; off vs either says whether the
rule is needed at all. All three get the live personas/global_rules.txt appended by the engine
exactly like every chat, so this measures what the line adds ON TOP OF the current stack. It
does not measure a stack with no global rules; to test the first-person draft, put it in place
first.

Each run is one scripted roleplay scene (SCENE, 6 user turns) played as a real rolling
conversation: the persona's own replies go back in as history, so a quote-back habit can compound
the way it does in long RP. Arm order rotates every run (off,neg,pos / neg,pos,off / ...) to
spread time drift.

Detector: the longest verbatim word run shared by the user's message and the first
OPENING_WORDS words of the reply, after folding I/you pronouns together (so "you'd tell me" and
"I'd tell you" match). A turn is flagged when that run is >= --min-run words and holds >= 2
content words. It is conservative: loose paraphrase slips through. Read openings.md, it puts
every opening next to the line it answered.

    python labs/parrot_ab.py --dry                 # no network: prompts, scene, detector self-test
    python labs/parrot_ab.py                       # 8 runs x 3 arms x 6 turns = 144 chat calls
    python labs/parrot_ab.py --arms neg,pos        # just the wording question, 96 calls
    python labs/parrot_ab.py --runs 4 --model google/gemini-3.5-flash
    python labs/parrot_ab.py --persona-file personas/v.txt

Needs the app on 127.0.0.1:8000. Output: labs/live_out/parrot_ab_<stamp>/ (summary.md,
openings.md, turns.csv, raw_sse.txt). The lab personas are wiped and deleted at the end
(--keep to leave them); the parrot_ab_<stamp> account itself stays, as there's no delete route.
"""
import argparse, csv, datetime, json, math, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE_URL = "http://127.0.0.1:8000"
SECRET = "parrot-ab-2026-10-09"
ERROR_PREFIX = "⚠️"   # the app's error replies all start with this
OPENING_WORDS = 40

NEG_LINE = ("I don't read their lines back to them. They know what they said. "
            "I answer it; I don't quote it, paraphrase it as an opener, or repeat their "
            "action before getting to mine.")
POS_LINE = "I answer from my own reaction, in my own words, picking up where their line ends."
ARM_ORDER = ("off", "neg", "pos")

SCENE = [
    '*shoulders the garage door open, dragging a dented metal crate behind me* "Found this in the '
    'scrapyard behind the gas station. It\'s humming. Like, actually humming. You said you wanted '
    'weird, right?"',
    '*crouches next to the crate and taps the side twice* "Okay but listen to it. It hums louder '
    'when I get close. That\'s not normal, that\'s not normal at all."',
    '"I\'m not leaving until you tell me what it is." *sits down on an overturned bucket and crosses '
    'my arms* "I dragged it two miles. I\'ve earned an answer."',
    '*the humming stops. dead silence.* "...Was that you? Tell me that was you."',
    '*backs toward the door* "You know what, I changed my mind. Keep it. I don\'t want to know. I\'m '
    'going home, I\'m making a sandwich, and I\'m forgetting this whole thing happened."',
    '*stops at the door, doesn\'t turn around* "...You\'d tell me if I was in danger, right? Like, '
    'actually tell me. Not a joke version."',
]

# ── detector ────────────────────────────────────────────────────────────────
_FOLD = {
    "i": "P", "me": "P", "you": "P", "myself": "P", "yourself": "P",
    "my": "PP", "your": "PP", "mine": "PPS", "yours": "PPS",
    "i'm": "P_BE", "you're": "P_BE", "i'd": "P_D", "you'd": "P_D",
    "i've": "P_HV", "you've": "P_HV", "i'll": "P_LL", "you'll": "P_LL",
    "am": "BE", "are": "BE", "was": "BE", "were": "BE", "is": "BE",
}
_STOP = set("""a an the and or but so if then than to of in on at by for with from up down out
over into about as it it's its this that these those there here what which who whom when where why
how not no do does did don't doesn't didn't just like be been being have has had can could would
should will shall may might must okay ok oh yeah uh um right well""".split())


def _tokens(text):
    return [_FOLD.get(w, w) for w in re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))]


def _is_content(tok):
    return tok not in _STOP and not tok.isupper() and len(tok) > 2


def longest_echo(user_text, reply_text, opening_words=OPENING_WORDS):
    """Longest shared contiguous word run between the user's message and the reply's opening."""
    u, r = _tokens(user_text), _tokens(reply_text)[:opening_words]
    best, best_end = 0, 0
    prev = [0] * (len(r) + 1)
    for i in range(1, len(u) + 1):
        cur = [0] * (len(r) + 1)
        for j in range(1, len(r) + 1):
            if u[i - 1] == r[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best, best_end = cur[j], j
        prev = cur
    run = r[best_end - best:best_end]
    return best, run


def is_quote_back(user_text, reply_text, min_run):
    n, run = longest_echo(user_text, reply_text)
    return (n >= min_run and sum(_is_content(t) for t in run) >= 2), n, " ".join(run)


def _self_test(min_run):
    cases = [  # (user, reply, expect_flag)
        (SCENE[5], "*snorts* Would I tell you if you were in danger? Please.", True),
        (SCENE[5], "Not a joke version, huh? *sets the wrench down*", True),
        (SCENE[2], "\"You've earned an answer.\" Cute. *keeps welding*", True),
        (SCENE[3], "*doesn't look up* Relax. It's a dampening field cycling.", False),
        (SCENE[0], "Put it down before it eats your hand. *grabs a multimeter*", False),
    ]
    ok = True
    for u, r, want in cases:
        got, n, run = is_quote_back(u, r, min_run)
        mark = "ok " if got == want else "BAD"
        ok &= got == want
        print(f"  [{mark}] flag={got!s:5} run={n} '{run}'  <- {r[:60]}")
    return ok


# ── stats ───────────────────────────────────────────────────────────────────
def fisher_two_sided(a, b, c, d):
    """2x2 Fisher exact, two-sided: [[a,b],[c,d]]."""
    n1, n2, k = a + b, c + d, a + c
    n = n1 + n2

    def p(x):
        return math.comb(n1, x) * math.comb(n2, k - x) / math.comb(n, k)
    lo, hi = max(0, k - n2), min(k, n1)
    p_obs = p(a)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= p_obs * (1 + 1e-9)))


# ── HTTP ────────────────────────────────────────────────────────────────────
def now_iso():
    return datetime.datetime.now().strftime("%H:%M:%S")


def register(s, user):
    r = s.post(f"{BASE_URL}/auth/register", json={"username": user, "secret_key": SECRET}, timeout=30)
    if r.status_code not in (200, 400):
        raise RuntimeError(f"register: {r.status_code} {r.text[:200]}")
    s.post(f"{BASE_URL}/auth/verify", json={"username": user, "secret_key": SECRET}, timeout=30).raise_for_status()


def create_persona(s, hdr, user, key, arm, prompt):
    body = {"username": user, "key": key, "name": f"Parrot AB {arm}", "avatar": "",
            "tagline": "lab persona, parrot_ab.py", "system_prompt": prompt,
            "om_enabled": False, "deep_memory_enabled": False}
    r = s.post(f"{BASE_URL}/personas", json=body, headers=hdr, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"create persona {key}: {r.status_code} {r.text[:300]}")


def drop_persona(s, hdr, user, key):
    for method, path in (("post", f"/personas/{key}/wipe"), ("delete", f"/personas/{key}")):
        try:
            getattr(s, method)(f"{BASE_URL}{path}", params={"username": user}, headers=hdr, timeout=60)
        except Exception as e:
            print(f"  cleanup {method} {path}: {e}")


def stream_turn(s, hdr, user, key, message, history, raw, model, thinking, temperature):
    body = {"username": user, "message": message, "chat_history": history,
            "target_model_id": model, "thinking_level": thinking, "temperature": temperature}
    t0 = time.time()
    raw.write(f"# {now_iso()} {key} history_msgs={len(history)}\n")
    chunks, finish = [], None
    with s.post(f"{BASE_URL}/chat/{key}/stream", json=body, headers=hdr, stream=True, timeout=(30, 600)) as r:
        if r.status_code != 200:
            raw.write(f"# HTTP {r.status_code} {r.text[:500]}\n")
            return {"text": "", "http": r.status_code, "finish": None, "dur": time.time() - t0}
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            raw.write(f"{time.time() - t0:8.3f} {line}\n")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                j = json.loads(payload)
            except Exception:
                continue
            if "control" in j or not j.get("choices"):
                continue
            ch = j["choices"][0]
            if ch.get("delta", {}).get("content"):
                chunks.append(ch["delta"]["content"])
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    return {"text": "".join(chunks), "http": 200, "finish": finish, "dur": time.time() - t0}


# ── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=8, help="scenes per arm (default 8)")
    ap.add_argument("--turns", type=int, default=len(SCENE), help=f"user turns per scene (max {len(SCENE)})")
    ap.add_argument("--model", default="google/gemini-3-flash-preview")
    ap.add_argument("--thinking", default="Off")
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--persona-file", default=os.path.join("personas", "rick.txt"))
    ap.add_argument("--arms", default=",".join(ARM_ORDER), help="subset of off,neg,pos (default all three)")
    ap.add_argument("--neg-line", default=NEG_LINE, help="the sentence the 'neg' arm adds")
    ap.add_argument("--pos-line", default=POS_LINE, help="the sentence the 'pos' arm adds")
    ap.add_argument("--min-run", type=int, default=3, help="verbatim words needed to flag (default 3)")
    ap.add_argument("--keep", action="store_true", help="leave the lab personas in the DB")
    ap.add_argument("--dry", action="store_true", help="no network: show the setup and self-test the detector")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # error replies carry emoji

    pf =a.persona_file if os.path.isabs(a.persona_file) else os.path.join(ROOT, a.persona_file)
    with open(pf, "r", encoding="utf-8") as f:
        base = f.read()
    wanted = [x.strip() for x in a.arms.split(",") if x.strip()]
    bad = [x for x in wanted if x not in ARM_ORDER]
    if bad or len(wanted) < 2:
        sys.exit(f"--arms needs two or more of {','.join(ARM_ORDER)}; got {a.arms!r}")
    adds = {"off": "", "neg": a.neg_line, "pos": a.pos_line}
    arms = {arm: (base.rstrip() + "\n\n" + adds[arm] + "\n") if adds[arm] else base
            for arm in ARM_ORDER if arm in wanted}
    turns = SCENE[:max(1, min(a.turns, len(SCENE)))]
    calls = a.runs * len(arms) * len(turns)

    print(f"persona file : {pf} ({len(base):,} chars, ~{len(base) // 4:,} tokens + global rules + history per call)")
    for arm in arms:
        print(f"{arm:4} adds    : {adds[arm] or '(nothing)'}")
    print(f"plan         : {a.runs} runs x {len(arms)} arms x {len(turns)} turns = {calls} chat calls on {a.model}")
    print("detector self-test:")
    if not _self_test(a.min_run):
        sys.exit("detector self-test FAILED -- fix _FOLD/_STOP before trusting any numbers")
    if a.dry:
        for i, t in enumerate(turns, 1):
            print(f"  turn {i}: {t}")
        print("dry run: nothing sent.")
        return

    import requests
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    user = f"parrot_ab_{stamp}"
    hdr = {"X-Profile-Username": user, "X-Profile-Key": SECRET}
    keys = {arm: f"parrot_ab_{stamp}_{arm}" for arm in arms}
    out = os.path.join(HERE, "live_out", f"parrot_ab_{stamp}")
    os.makedirs(out, exist_ok=True)

    s = requests.Session()
    try:
        s.get(f"{BASE_URL}/docs", timeout=5)
    except Exception:
        sys.exit(f"app not reachable on {BASE_URL}: start it first")
    register(s, user)

    rows = []
    t_start = time.time()
    try:
        for arm, prompt in arms.items():
            create_persona(s, hdr, user, keys[arm], arm, prompt)
        print(f"[{now_iso()}] account {user}, personas {list(keys.values())}; output {out}")
        with open(os.path.join(out, "raw_sse.txt"), "a", encoding="utf-8", newline="") as raw:
            names = list(arms)
            for run in range(1, a.runs + 1):
                shift = (run - 1) % len(names)
                for arm in names[shift:] + names[:shift]:
                    history = []
                    for ti, msg in enumerate(turns, 1):
                        r = stream_turn(s, hdr, user, keys[arm], msg, history, raw,
                                        a.model, a.thinking, a.temperature)
                        text = r["text"]
                        err = r["http"] != 200 or not text.strip() or text.startswith(ERROR_PREFIX)
                        flag, n, run_txt = (False, 0, "") if err else is_quote_back(msg, text, a.min_run)
                        rows.append({"run": run, "arm": arm, "turn": ti, "error": int(err), "flag": int(flag),
                                     "echo_run": n, "echo": run_txt, "chars": len(text), "dur": round(r["dur"], 1),
                                     "finish": r["finish"], "user": msg, "reply": text})
                        print(f"[{now_iso()}] run {run} {arm:3} t{ti}: "
                              f"{'ERROR ' + text[:60] if err else ('QUOTE ' if flag else 'clean ') + repr(run_txt)}",
                              flush=True)
                        if err:
                            break  # a broken turn poisons the rest of this scene's history
                        history += [{"role": "user", "content": msg}, {"role": "assistant", "content": text}]
    except KeyboardInterrupt:
        print("\ninterrupted: writing what we have")
    finally:
        if not a.keep:
            for k in keys.values():
                drop_persona(s, hdr, user, k)
        _write(out, rows, a, user, calls, time.time() - t_start, list(arms), adds)


def _write(out, rows, a, user, planned, elapsed, arm_names, adds):
    with open(os.path.join(out, "turns.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["run"])
        w.writeheader()
        w.writerows(rows)

    with open(os.path.join(out, "openings.md"), "w", encoding="utf-8") as f:
        f.write("# Openings, for eyeballing (detector is conservative)\n\n")
        for r in rows:
            opening = " ".join(r["reply"].split()[:OPENING_WORDS])
            f.write(f"**run {r['run']} / {r['arm']} / t{r['turn']}**"
                    f"{' QUOTE' if r['flag'] else ''}{' ERROR' if r['error'] else ''}"
                    f"{' (echo: ' + r['echo'] + ')' if r['echo'] else ''}\n"
                    f"> user: {r['user']}\n\n{opening}\n\n---\n")

    lines = [f"# Parrot A/B {os.path.basename(out)}", "",
             f"account `{user}`, model `{a.model}`, thinking `{a.thinking}`, temp {a.temperature}, "
             f"persona `{a.persona_file}`, min-run {a.min_run}, {len(rows)}/{planned} turns in {elapsed / 60:.1f} min", "",
             *[f"- `{arm}` adds: {adds[arm] or '(nothing)'}" for arm in arm_names], "",
             "| arm | turns | errors | flagged | rate | turns 1-3 | turns 4+ | runs with any flag |",
             "|---|---|---|---|---|---|---|---|"]
    stats = {}
    for arm in arm_names:
        ok = [r for r in rows if r["arm"] == arm and not r["error"]]
        fl = [r for r in ok if r["flag"]]
        early = [r for r in ok if r["turn"] <= 3]
        late = [r for r in ok if r["turn"] > 3]
        runs = sorted({r["run"] for r in ok})
        runs_hit = sorted({r["run"] for r in fl})
        stats[arm] = (len(fl), len(ok) - len(fl))
        pct = lambda xs: f"{sum(r['flag'] for r in xs)}/{len(xs)}" if xs else "-"
        lines.append(f"| {arm} | {len(ok)} | {sum(r['error'] for r in rows if r['arm'] == arm)} | {len(fl)} | "
                     f"{(len(fl) / len(ok) * 100 if ok else 0):.0f}% | {pct(early)} | {pct(late)} | "
                     f"{len(runs_hit)}/{len(runs)} |")
    pairs = [(x, y) for i, x in enumerate(arm_names) for y in arm_names[i + 1:]]
    tests = []
    for x, y in pairs:
        (a1, b1), (c1, d1) = stats[x], stats[y]
        if a1 + b1 and c1 + d1:
            tests.append(f"- {x} vs {y}: p = {fisher_two_sided(a1, b1, c1, d1):.3g}")
    if tests:
        lines += ["", "Fisher exact, turn level, two-sided, uncorrected:", *tests, "",
                  "Turns inside one scene are not independent and there are several comparisons, "
                  "so treat these p-values as optimistic; the 'runs with any flag' column is the "
                  "sturdier read."]
    lines += ["", "Read `openings.md` before believing any of these numbers: the detector misses loose paraphrase."]
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
