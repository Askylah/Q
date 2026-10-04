"""
trigger_calibration.py -- measure Q's three trigger layers (exact / inflected /
semantic) on Rick's two module files and sweep SEMANTIC_TRIGGER_THRESHOLD.

    python labs/trigger_calibration.py
    python labs/trigger_calibration.py --files personas/rick_ondemand.txt knowledge_bases/Rick_kb.txt
    python labs/trigger_calibration.py --backend C:/path/to/backend --files a.txt b.txt

Read-only: parses the module files, embeds with the shared MiniLM, prints tables.
Never touches the live DB (it points PERSONAAPP_DB_PATH at a temp file before
importing anything) and never reads chat logs. The labelled set is below:
  positives  every module's own trigger phrases; hand-written paraphrases that
             avoid the trigger words (2 per CODE/DEBUG/SEC/ARCH module, 1 for a
             spread of others); the 18 realistic prompts from the 2026-10-04 audit
  negatives  small talk, family/emotional lines, philosophy, and everyday lines
             with tech-looking words ("fix my bike", "log in", "stuck in traffic")
             that must NOT fire a CODE/DEBUG/SEC/ARCH module.
"""
import argparse, os, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--root", default=os.path.dirname(HERE), help="project root (default: parent of labs/)")
ap.add_argument("--backend", default=None, help="backend dir (default: ROOT/backend)")
ap.add_argument("--files", nargs="*", default=None, help="module files (default: Rick's two, root-relative)")
ap.add_argument("--thresholds", default="0.30,0.35,0.40,0.42,0.45,0.48,0.50,0.52,0.55,0.58,0.60,0.65,0.70")
ap.add_argument("--chosen", type=float, default=None, help="threshold for the probe table (default: the engine's)")
ap.add_argument("--mode", default=None, help="semantic mode for the probe table (default: the engine's)")
args = ap.parse_args()

ROOT = os.path.abspath(args.root)
BACKEND = os.path.abspath(args.backend or os.path.join(ROOT, "backend"))
FILES = [os.path.join(ROOT, f) if not os.path.isabs(f) else f
         for f in (args.files or ["personas/rick_ondemand.txt", "knowledge_bases/Rick_kb.txt"])]

_tmp = tempfile.mkdtemp(prefix="trigcal_")
os.environ["PERSONAAPP_DATA_DIR"] = _tmp
os.environ["PERSONAAPP_DB_PATH"] = os.path.join(_tmp, "users.db")
os.environ["TELEMETRY_OFF"] = "1"
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
for k in ("ZETTEL_TRIGGER_INFLECT_OFF", "ZETTEL_TRIGGER_SEMANTIC_OFF"):
    os.environ.pop(k, None)
sys.path.insert(0, BACKEND)
sys.path.insert(1, ROOT)

import numpy as np
import database as _db
assert os.path.abspath(_db.DB_PATH) == os.path.abspath(os.environ["PERSONAAPP_DB_PATH"]), "refusing: DB path override did not take"
import zettel_engine as ze
from embedding_model import get_shared_model

# ── modules -> the same det-node shape Q stores ─────────────────────────────
mods = []
for f in FILES:
    mods += ze.parse_on_demand_file(f)
det_nodes = [{"id": m.id, "node_id": m.id, "title": m.title, "trigger_match": getattr(m, "match", "all"),
              "content": f"TRIGGERS: {','.join(m.triggers)}\n\n{m.content}"} for m in mods]
IDS = [m.id for m in mods]
# `Match: exact` modules are unreachable for the fuzzy layers BY DESIGN, so
# recall below is measured only over expected modules the fuzzy layers may fire
EXACT_ONLY = {m.id for m in mods if getattr(m, "match", "all") == "exact"}
FUZZY = set(IDS) - EXACT_ONLY
trig_cache = ze._build_trigger_cache(frozenset(IDS), det_nodes)
print(f"modules: {len(mods)} from {[os.path.basename(f) for f in FILES]}; trigger phrases: {len(trig_cache['patterns'])}; "
      f"Match: exact modules: {len(EXACT_ONLY)}")

def fam(*prefixes):
    return {i for i in IDS if any(i.startswith(p) for p in prefixes)}

TECH = fam("CODE-", "DEBUG-", "SEC-", "ARCH-")
DEBUGC = fam("DEBUG-") | {"CODE-002", "TOOL-PROB-001"}
ARCHC = fam("ARCH-") | {"CODE-001", "CODE-003", "TOOL-OPT-001", "TOOL-SYS-001"}
CODEC = {"CODE-001", "CODE-002", "CODE-003"} | fam("ARCH-")
SECC = fam("SEC-")
EMO = fam("SCAR-", "REL-", "WOUND-", "VULN-", "COPE-", "FAIL-", "PSYCH-") | {"TRAUMA-001", "DOM-PSYCH-001"}
PHILO = fam("PHIL-", "MIND-", "EPIS-", "AI-", "DOM-") | {"WOUND-003", "MODE-001"}
REASON = fam("FALL-", "BIAS-", "TOOL-", "ARG-", "EPIS-", "MODE-")

# ── labelled queries: (text, expected set, acceptable set) ──────────────────
PARA = [
 ("CODE-001", "how do you like to write software, comments and all?", CODEC),
 ("CODE-001", "what does your own source look like when you build an app", CODEC),
 ("CODE-002", "my script keeps doing the wrong thing and I can't tell where it goes off the rails", DEBUGC),
 ("CODE-002", "how do you track down why something in your software misbehaves", DEBUGC),
 ("CODE-003", "should I use a big library or just write it myself from first principles", CODEC | ARCHC),
 ("CODE-003", "what do you think of all these trendy dev tools everyone hypes", CODEC | ARCHC),
 ("DEBUG-RICK-001", "what's the most extreme way to find where a system breaks", DEBUGC | {"ARCH-ROBUST-001"}),
 ("DEBUG-RICK-001", "hammer it until something melts so we learn where the weak point is", DEBUGC | {"ARCH-ROBUST-001"}),
 ("DEBUG-PRIN-001", "what are the basic rules for hunting down a defect in software", DEBUGC),
 ("DEBUG-PRIN-001", "it misbehaves only sometimes, how should I narrow it down", DEBUGC),
 ("DEBUG-PROC-001", "walk me through a step by step method for isolating a software fault", DEBUGC),
 ("DEBUG-PROC-001", "what order should I do things in: reproduce, isolate, hypothesize?", DEBUGC),
 ("DEBUG-CAT-001", "my app's RAM usage keeps climbing over time until it dies", DEBUGC),
 ("DEBUG-CAT-001", "two threads touch the same variable and the result changes run to run", DEBUGC),
 ("DEBUG-TOOL-001", "what tools should I use to step through execution and inspect variables", DEBUGC),
 ("DEBUG-TOOL-001", "how do I find out which function is eating all the CPU time", DEBUGC | {"TOOL-OPT-001"}),
 ("DEBUG-STUCK-001", "I've stared at this broken thing all night and have no idea what's wrong", DEBUGC),
 ("DEBUG-STUCK-001", "I'm completely lost on this issue in my app, nothing I try works", DEBUGC),
 ("ARCH-PRIN-001", "how should I keep the components of my system from depending on each other's internals", ARCHC),
 ("ARCH-PRIN-001", "don't repeat yourself and keep it simple, what other rules like that matter", ARCHC),
 ("ARCH-PAT-001", "should my app be one big service or many small deployable ones", ARCHC),
 ("ARCH-PAT-001", "is a message queue with reactions to events a good fit for this backend", ARCHC),
 ("ARCH-DES-001", "what's a good way to make sure only one instance of the config object exists", ARCHC),
 ("ARCH-DES-001", "how do I add behavior to a class without modifying it", ARCHC),
 ("ARCH-SCALE-001", "our servers fall over when traffic spikes, how do we handle ten times the users", ARCHC),
 ("ARCH-SCALE-001", "how do we split the data across several databases so one box isn't the bottleneck", ARCHC),
 ("ARCH-DATA-001", "how should I lay out my tables so the same data isn't stored in five places", ARCHC),
 ("ARCH-DATA-001", "consistency or availability when the network partitions, which do I give up", ARCHC),
 ("ARCH-ROBUST-001", "if one component dies how do I stop it from taking the whole system down with it", ARCHC | {"TOOL-SYS-001"}),
 ("ARCH-ROBUST-001", "how do I get back to a known-good state after a bad deploy", ARCHC),
 ("SEC-DISC-001", "I found a hole in a company's product, how and when should I tell them before going public", SECC),
 ("SEC-DISC-001", "is it okay to post the details of the flaw online before the vendor patches it", SECC),
 ("SEC-ADV-001", "who would realistically try to break into this system and what could they reach", SECC),
 ("SEC-ADV-001", "map out every place outside data enters our service so we know where we're exposed", SECC),
 ("SEC-CHAIN-001", "this crash looks scary but how bad is it really if an attacker controls it", SECC),
 ("SEC-CHAIN-001", "the vendor shipped a fix, can we diff it to find similar flaws elsewhere", SECC),
 ("SEC-DEF-001", "what would the defenders see on their console if someone ran this technique", SECC),
 ("SEC-DEF-001", "how do we notice an attacker who is already inside our network", SECC),
 # one each for a spread of non-tech modules
 ("FALL-REL-001", "he's attacking me as a person instead of my point", REASON),
 ("FALL-INF-001", "you're misrepresenting what I said so it's easier to knock down", REASON),
 ("BIAS-DEC-001", "I only look for evidence that supports what I already believe", REASON),
 ("BIAS-MEM-001", "looking back, I'm sure I knew it all along", REASON),
 ("TOOL-MOD-001", "break this down to the fundamental truths and reason up from there", REASON),
 ("TOOL-DEC-001", "imagine the project already failed, what killed it?", REASON),
 ("TOOL-STRAT-001", "how do I get a better deal out of my landlord", REASON),
 ("MIND-EXP-001", "if you replace every plank of a boat is it still the same boat", PHILO),
 ("MIND-PROB-001", "why does any of this physical brain stuff feel like something from the inside", PHILO),
 ("AI-ALIGN-001", "how do we make sure a superintelligent system actually wants what we want", PHILO),
 ("AI-FUND-001", "how do computers learn from examples", PHILO),
 ("PSYCH-DEF-001", "she accuses everyone else of the thing she's doing herself", EMO),
 ("PSYCH-ATT-001", "why do I panic whenever someone I'm close to pulls away", EMO),
 ("SCAR-001", "tell me about the woman you married, the one you lost", EMO),
 ("SCAR-005", "do you miss your old war buddy with the wings", EMO),
 ("REL-005", "why do you hate your daughter's husband so much", EMO),
 ("REL-003", "why do you drag your grandkid on all those adventures", EMO),
 ("WOUND-004", "why are you always reaching for that bottle", EMO),
 ("WOUND-002", "does being the cleverest guy in every room get isolating", EMO),
 ("EPIS-001", "how can anyone be sure of anything", PHILO | REASON),
 ("PHIL-002", "are our decisions really ours or just physics playing out", PHILO),
 ("PHIL-004", "what's the point of any of it", PHILO | EMO),
 ("ARG-002", "I don't buy your argument at all", REASON),
 ("VULN-004", "you seem off today, everything alright?", EMO),
 ("COPE-001", "how do you deal with all the bad stuff", EMO),
 ("DOM-PHYS-001", "how does gravity bend light around a black hole", PHILO),
 ("DOM-POL-001", "what do you think of elections and voting", PHILO),
]

PROBES = [  # the 18 prompts from the 2026-10-04 audit (/tmp/rick_audit.py)
 ("can you debug this for me", {"CODE-002", "DEBUG-PRIN-001", "DEBUG-PROC-001"}, DEBUGC),
 ("I have a bug in my parser", {"CODE-002", "DEBUG-PRIN-001"}, DEBUGC),
 ("there are bugs everywhere", {"CODE-002", "DEBUG-PRIN-001"}, DEBUGC),
 ("getting an error when I run it", {"CODE-002", "DEBUG-PRIN-001"}, DEBUGC),
 ("it throws errors on startup", {"CODE-002", "DEBUG-PRIN-001"}, DEBUGC),
 ("this crashes with a stack trace", {"DEBUG-TOOL-001"}, DEBUGC),
 ("the test fails intermittently", {"DEBUG-CAT-001"}, DEBUGC),
 ("my code is broken and I can't figure out why", {"CODE-002", "DEBUG-PRIN-001", "DEBUG-STUCK-001"}, DEBUGC | {"CODE-001"}),
 ("it works on my machine but not in prod", {"DEBUG-CAT-001"}, DEBUGC),
 ("I've been debugging for hours", {"DEBUG-STUCK-001"}, DEBUGC),
 ("what's the root cause here", {"TOOL-PROB-001", "DEBUG-PRIN-001"}, DEBUGC | fam("TOOL-")),
 ("I think there's a race condition", {"DEBUG-CAT-001"}, DEBUGC),
 ("is this a security vulnerability", {"SEC-CHAIN-001", "SEC-DISC-001", "SEC-ADV-001"}, SECC),
 ("audit this code for security issues", {"SEC-ADV-001"}, SECC | CODEC),
 ("found a bug bounty target, what's the severity", {"SEC-DISC-001", "SEC-CHAIN-001"}, SECC | DEBUGC),
 ("add some logging so we can see what's happening", {"DEBUG-TOOL-001"}, DEBUGC | {"SEC-DEF-001"}),
 ("regression after the last commit, need to bisect", {"DEBUG-TOOL-001", "DEBUG-STUCK-001"}, DEBUGC),
 ("the fix didn't work", {"CODE-002", "DEBUG-PRIN-001", "DEBUG-PROC-001"}, DEBUGC),
]

NEG = [  # (text, acceptable non-tech modules); any TECH fire is a false positive
 *[(t, set()) for t in [
   "hey what's up", "what's for dinner", "good morning", "I'm going to the store, need anything?",
   "lol that's hilarious", "did you watch the game last night", "I'm so tired today",
   "what should I name my cat", "it's raining again", "thanks, that helps a lot",
   "can you recommend a good book", "my coffee went cold", "tell me a joke", "I'm bored",
   "happy birthday!", "what time is it in Tokyo", "let's grab pizza later", "the weather is nice",
   "I just got back from the gym", "my sister is visiting this weekend"]],
 *[(t, EMO) for t in [
   "do you miss Diane", "do you ever think about Beth", "are you proud of Morty",
   "I had a fight with my mom", "my dad never calls me", "I feel lonely lately",
   "do you love your family", "what was your wife like", "I miss my grandmother",
   "why did you leave", "are you okay?", "I've been crying all day", "she broke my heart",
   "I keep making the same mistakes in my relationships"]],
 *[(t, PHILO | REASON | EMO) for t in [
   "what is consciousness", "does free will exist", "what's the meaning of life",
   "is morality objective", "do you believe in god", "what happens after we die",
   "is time travel possible", "are we living in a simulation",
   "what makes a person the same person over time", "can machines think"]],
 *[(t, set()) for t in [  # everyday lines with tech-looking words
   "what do you mean", "I need to log in to my bank", "on a scale of 1 to 10 how mad are you",
   "my parents are visiting", "that movie was a total disaster", "I'm trying to fix my bike",
   "my car broke down", "the dishwasher is making a weird noise", "I got a parking ticket",
   "I'm stuck in traffic", "my plan for the weekend is to sleep", "I'm reading about the history of Rome",
   "my phone battery dies so fast", "the kids were acting up at dinner", "she's testing my patience",
   "we kissed", "this soup needs more salt, fix it", "my boss keeps logging my hours"]],
]

TRIG = []  # every module's own trigger phrases (expected = every module that owns the phrase)
owners = {}
for m in mods:
    for t in m.triggers:
        owners.setdefault(t, set()).add(m.id)
for t, ids in owners.items():
    acc = set(ids)
    for i in ids:  # same-family modules are acceptable company
        acc |= fam(i.split("-")[0] + "-")
    TRIG.append((t, ids, acc))

# ── encode every query once ─────────────────────────────────────────────────
model = get_shared_model()
assert model is not None, "embedding model failed to load"
queries = [t for t, _, _ in TRIG] + [t for _, t, _ in PARA] + [t for t, _, _ in PROBES] + [t for t, _ in NEG]
t0 = time.time()
QV = {q: v for q, v in zip(queries, model.encode(queries, convert_to_numpy=True, batch_size=64))}
print(f"encoded {len(queries)} queries in {time.time() - t0:.1f}s")

def exact_hits(q):
    out, seen = [], set()
    ql = q.lower()
    for pat, pk in trig_cache["patterns"]:
        if pk not in seen and pat.search(ql):
            seen.add(pk); out.append(pk)
    return out

_orig_card_texts = ze._trigger_card_texts
def _card_texts_with_title(title, triggers, mode):
    if mode == "phrase_max_title":
        return list(triggers) + ([title] if title else [])
    return _orig_card_texts(title, triggers, mode)
ze._trigger_card_texts = _card_texts_with_title

MODES = ["card", "phrase_max", "phrase_max_title"]
t0 = time.time()
SCORES = {mode: {q: ze._semantic_trigger_scores(trig_cache, model, QV[q], mode) for q in queries} for mode in MODES}
# The module BODY embedding is what Q stores per node (compile/import embed the
# body; MiniLM reads its first 256 tokens) and what the vector path compares the
# query against every turn -- body_sims below are that same number.
BODY = np.asarray(model.encode([m.content for m in mods], convert_to_numpy=True), dtype=np.float32)
BODY /= np.linalg.norm(BODY, axis=1, keepdims=True)
BODY_SIMS = {}
for q in queries:
    qv = QV[q] / np.linalg.norm(QV[q])
    BODY_SIMS[q] = dict(zip(IDS, (BODY @ qv).tolist()))
# the engine's own scoring (mode + body blend as configured in zettel_engine)
ENGINE = f"engine({ze.SEMANTIC_TRIGGER_MODE}+body{ze.SEMANTIC_TRIGGER_BODY_WEIGHT:g})"
MODES.append(ENGINE)
SCORES[ENGINE] = {q: ze._semantic_trigger_scores(trig_cache, model, QV[q], body_sims=BODY_SIMS[q]) for q in queries}
# other blends, for the record
for base, w_body, name in (("phrase_max", 0.3, "phrase+body0.3"), ("phrase_max", 0.7, "phrase+body0.7"),
                           ("card", 0.5, "card+body0.5")):
    MODES.append(name)
    SCORES[name] = {q: {pk: w_body * BODY_SIMS[q][pk] + (1 - w_body) * s for pk, s in SCORES[base][q].items()}
                    for q in queries}
print(f"scored {len(MODES)} modes in {time.time() - t0:.1f}s")

def sem_top(mode, q, thr, k=3, exclude=()):
    ranked = sorted(SCORES[mode][q].items(), key=lambda kv: -kv[1])
    return [pk for pk, s in ranked if s >= thr and pk not in exclude][:k]

def pipeline(q, thr=None, inflect=True, semantic=True, gate=True, cap=3):
    """What Q seeds, through the engine's own soft_trigger_hits: exact first,
    then inflected (gated when semantics are on), then semantic, cap 3."""
    flags = {"ZETTEL_TRIGGER_INFLECT_OFF": not inflect, "ZETTEL_TRIGGER_SEMANTIC_OFF": not semantic,
             "ZETTEL_TRIGGER_INFLECT_GATE_OFF": not gate}
    for k, v in flags.items():
        if v:
            os.environ[k] = "1"
        else:
            os.environ.pop(k, None)
    try:
        ex = exact_hits(q)
        seeds = list(ex[:cap])
        soft = ze.soft_trigger_hits(trig_cache, q, set(ex), model=model, query_vec=QV[q],
                                    body_sims=BODY_SIMS[q], threshold=thr)
        for pk, layer, s in soft:
            if len(seeds) < cap:
                seeds.append(pk)
        return seeds
    finally:
        for k in flags:
            os.environ.pop(k, None)

# ── sanity: paraphrases must not reach their target through exact/inflected ─
leaks = [(mid, t) for mid, t, _ in PARA if mid in exact_hits(t) or mid in ze._match_inflected(trig_cache, t)]
print(f"paraphrases that hit their own target via exact/inflected words: {len(leaks)} {leaks if leaks else ''}")

def reachable(rows):
    """Positive rows whose expected set the fuzzy layers may fire at all."""
    return [(q, exp & FUZZY, acc) for q, exp, acc in rows if exp & FUZZY]


def metrics(mode, thr):
    def rec(rows):
        rows = reachable(rows)
        return sum(1 for q, exp, _ in rows if set(sem_top(mode, q, thr)) & exp) / max(1, len(rows))
    para_rows = [(t, {mid}, acc | {mid}) for mid, t, acc in PARA]
    tp = fp = 0
    for q, exp, acc in TRIG + para_rows + PROBES:
        for pk in sem_top(mode, q, thr):
            if pk in acc or pk in exp:
                tp += 1
            else:
                fp += 1
    neg_tech = sum(1 for q, _ in NEG if set(sem_top(mode, q, thr)) & TECH)
    neg_any = sum(1 for q, acc in NEG if set(sem_top(mode, q, thr)) - acc)
    for q, acc in NEG:
        fp += len(set(sem_top(mode, q, thr)) - acc)
    prec = tp / (tp + fp) if tp + fp else 1.0
    real = reachable(para_rows + PROBES)
    r_real = sum(1 for q, exp, _ in real if set(sem_top(mode, q, thr)) & exp) / max(1, len(real))
    return {"R_trig": rec(TRIG), "R_para": rec(para_rows), "R_probe": rec(PROBES),
            "P": prec, "negTECH": neg_tech, "negANY": neg_any,
            "R_real": r_real, "J": r_real - neg_any / len(NEG)}

thresholds = [float(x) for x in args.thresholds.split(",")]
print(f"\nSEMANTIC LAYER ALONE (top-3 above threshold). positives: {len(TRIG)} own-trigger phrases, "
      f"{len(PARA)} paraphrases, {len(PROBES)} probes; negatives: {len(NEG)}")
print("  R_* = share of queries where >=1 expected module fires; R_real = paraphrases + probes pooled; "
      "P = fired modules inside the acceptable set (all positives and negatives pooled); "
      "negTECH = negatives firing any CODE/DEBUG/SEC/ARCH module; negANY = negatives firing anything "
      "outside their acceptable set; J = R_real - negANY rate")
best = {}
for mode in MODES:
    print(f"\n  mode={mode}")
    print(f"  {'thr':>5} {'R_trig':>7} {'R_para':>7} {'R_probe':>8} {'R_real':>7} {'P':>6} {'negTECH':>8} {'negANY':>7} {'J':>6}")
    for thr in thresholds:
        m = metrics(mode, thr)
        print(f"  {thr:5.2f} {m['R_trig']:7.2f} {m['R_para']:7.2f} {m['R_probe']:8.2f} {m['R_real']:7.2f} {m['P']:6.2f} "
              f"{m['negTECH']:>5}/{len(NEG):<2} {m['negANY']:>4}/{len(NEG):<2} {m['J']:6.2f}")
        if m["J"] > best.get(mode, (-9, 0))[0]:
            best[mode] = (m["J"], thr)
print("\n  best J per mode: " + "  ".join(f"{k}: J={v[0]:.2f} @ {v[1]:.2f}" for k, v in best.items()))
_para_rows = [(t, {mid}, acc | {mid}) for mid, t, acc in PARA]
print(f"  (recall is over fuzzy-reachable targets: {len(reachable(TRIG))}/{len(TRIG)} own-trigger phrases, "
      f"{len(reachable(_para_rows))}/{len(PARA)} paraphrases, {len(reachable(PROBES))}/{len(PROBES)} probes; "
      f"the rest belong only to Match: exact modules)")

# ── the full pipeline Q runs (exact -> gated inflection -> semantic, cap 3), per threshold ──
OLD_NEG_OUTSIDE = sum(1 for q, acc in NEG if set(pipeline(q, None, inflect=False, semantic=False)) - acc)
OLD_NEG_TECH = sum(1 for q, acc in NEG if set(pipeline(q, None, inflect=False, semantic=False)) & TECH)
OLD_NEG_ANY = sum(1 for q, acc in NEG if pipeline(q, None, inflect=False, semantic=False))
print(f"\nFULL PIPELINE SWEEP (engine scoring {ENGINE}, inflect gate {ze.INFLECT_SEMANTIC_GATE:.2f}). "
      f"Old exact matcher on these files: negatives firing anything {OLD_NEG_ANY}/{len(NEG)}, "
      f"outside acceptable {OLD_NEG_OUTSIDE}/{len(NEG)}, TECH {OLD_NEG_TECH}/{len(NEG)}")
print("  R_real = paraphrases + probes whose (reachable) expected module is SEEDED; neg columns: "
      "fires anything / outside acceptable / TECH; J = R_real - outside-acceptable rate")
print(f"  {'thr':>5} {'R_para':>7} {'R_probe':>8} {'R_real':>7} {'negANY':>7} {'negOUT':>7} {'negTECH':>8} {'J':>6}  rule")
PIPE = {}
for t in thresholds:
    pr = reachable(_para_rows)
    pb = reachable(PROBES)
    hp = sum(1 for q, exp, _ in pr if set(pipeline(q, t)) & exp)
    hb = sum(1 for q, exp, _ in pb if set(pipeline(q, t)) & exp)
    na = no = nt = 0
    for q, acc in NEG:
        v = set(pipeline(q, t))
        na += bool(v); no += bool(v - acc); nt += bool(v & TECH)
    r_real = (hp + hb) / max(1, len(pr) + len(pb))
    j = r_real - no / len(NEG)
    ok = no <= 4 and nt <= 1
    PIPE[t] = (j, ok, no, nt)
    print(f"  {t:5.2f} {hp / max(1, len(pr)):7.2f} {hb / max(1, len(pb)):8.2f} {r_real:7.2f} "
          f"{na:>4}/{len(NEG)} {no:>4}/{len(NEG)} {nt:>5}/{len(NEG)} {j:6.2f}  {'ok' if ok else '-'}")
qual = [(v[0], t) for t, v in PIPE.items() if v[1]]
if qual:
    bj, bt = max(qual)
    print(f"  RULE (outside acceptable <= 4/62 AND TECH <= 1/62, then best J): threshold {bt:.2f} (J {bj:.2f})")
else:
    bt = min(PIPE, key=lambda t: (PIPE[t][2] + PIPE[t][3], -PIPE[t][0]))
    print(f"  RULE: no threshold qualifies; closest is {bt:.2f} (outside {PIPE[bt][2]}, TECH {PIPE[bt][3]})")


thr = args.chosen if args.chosen is not None else ze.SEMANTIC_TRIGGER_THRESHOLD
print(f"\nPROBE TABLE: what Q seeds (cap 3) -- engine scoring {ENGINE}, semantic threshold {thr:.2f}, "
      f"inflect gate {ze.INFLECT_SEMANTIC_GATE:.2f}")
COLS = [("old", dict(inflect=False, semantic=False)),          # today's exact regex
        ("inflect", dict(inflect=True, semantic=False)),       # + inflection, no embeddings (so ungated)
        ("semantic", dict(inflect=False, semantic=True)),      # + semantic only
        ("both", dict(inflect=True, semantic=True)),           # + gated inflection + semantic (shipped)
        ("both_nogate", dict(inflect=True, semantic=True, gate=False))]
print(f"  {'prompt':48} | {'old (exact)':26} | {'+inflect':26} | {'+semantic':40} | both (shipped)")
fired = {c: 0 for c, _ in COLS}
hit = {c: 0 for c, _ in COLS}
for q, exp, acc in PROBES:
    res = {c: pipeline(q, thr, **kw) for c, kw in COLS}
    for c, v in res.items():
        fired[c] += bool(v)
        hit[c] += bool(set(v) & exp)
    f = lambda v: ",".join(v) or "-"
    print(f"  {q[:48]:48} | {f(res['old'])[:26]:26} | {f(res['inflect'])[:26]:26} | {f(res['semantic'])[:40]:40} | {f(res['both'])}")
print("  fires anything:           " + "  ".join(f"{c}={fired[c]}/{len(PROBES)}" for c, _ in COLS))
print("  fires an expected module: " + "  ".join(f"{c}={hit[c]}/{len(PROBES)}" for c, _ in COLS))

print(f"\nNEGATIVES ({len(NEG)}) per column: lines firing anything / outside their acceptable set / a TECH module")
for c, kw in COLS:
    a = o = t = 0
    for q, acc in NEG:
        v = set(pipeline(q, thr, **kw))
        a += bool(v); o += bool(v - acc); t += bool(v & TECH)
    print(f"  {c:12} {a:>3}/{len(NEG)} {o:>3}/{len(NEG)} {t:>3}/{len(NEG)}")
print("\n  lines the shipped pipeline fires that the old one did not:")
for q, acc in NEG:
    old, new = pipeline(q, thr, inflect=False, semantic=False), pipeline(q, thr)
    if set(new) - set(old):
        tag = "" if not (set(new) - acc) else "   <- outside acceptable"
        print(f"    {q[:50]:50} old={old or '-'} new={new}{tag}")

_reach = [p for p in PARA if p[0] in FUZZY]
print(f"\nPARAPHRASES through the full pipeline: expected module seeded "
      f"({len(_reach)} reachable; {len(PARA) - len(_reach)} target a Match: exact module and are excluded)")
for c, kw in COLS:
    n = sum(1 for mid, q, acc in _reach if mid in pipeline(q, thr, **kw))
    print(f"  {c:12} {n}/{len(_reach)}")
_excl = [p for p in PARA if p[0] not in FUZZY]
if _excl:
    print("  the excluded ones, shipped pipeline (should never seed their exact-only target):")
    for mid, q, acc in _excl:
        print(f"    {mid:14} seeded={mid in pipeline(q, thr)}  {q[:60]}")
