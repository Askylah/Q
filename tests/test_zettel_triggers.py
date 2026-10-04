"""
Trigger matching layers (2026-10-04): exact regex (unchanged) -> inflected
token sequences -> semantic trigger cards, sharing MAX_DETERMINISTIC = 3.

Standalone, like the rest of tests/ -- no pytest. Throwaway SQLite file
(PERSONAAPP_DB_PATH), Redis db 1, a deterministic fake embedding model, so it
needs neither MiniLM nor the live app. Section [7] additionally runs the real
MiniLM if it is already in the local HF cache (SKIP otherwise).

    python tests/test_zettel_triggers.py
"""
import os, sys, tempfile, shutil, hashlib, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(1, ROOT)
TMP = tempfile.mkdtemp(prefix="zettel_triggers_")
os.environ["PERSONAAPP_DATA_DIR"] = TMP
os.environ["PERSONAAPP_DB_PATH"] = os.path.join(TMP, "users.db")
os.environ["TELEMETRY_OFF"] = "1"
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
for _k in ("ZETTEL_TRIGGER_INFLECT_OFF", "ZETTEL_TRIGGER_SEMANTIC_OFF", "ZETTEL_TRIGGER_INFLECT_GATE_OFF"):
    os.environ.pop(_k, None)

import numpy as np
import database as db
assert os.path.abspath(db.DB_PATH) == os.path.abspath(os.environ["PERSONAAPP_DB_PATH"]), \
    "refusing to run: the DB path override did not take (would touch a real database)"
import zettel_engine as ze

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def check_true(name, got):
    check(name, bool(got), True)


class FakeModel:
    """Deterministic stand-in for MiniLM: a keyword hits its own axis, every
    text also gets a small hash-seeded random component (near-orthogonal to
    everything else in 384-d)."""
    AXES = {"vulnerab": 0, "exploit": 0, "flaw": 0, "severity": 0,
            "debug": 1, "defect": 1, "malfunction": 1,
            "kitten": 2, "cat": 2,
            "pupp": 3, "dog": 3}

    def encode(self, texts, convert_to_numpy=True, **kw):
        if isinstance(texts, str):
            texts = [texts]
        out = []
        for t in texts:
            seed = int(hashlib.md5(t.encode("utf-8")).hexdigest()[:8], 16)
            v = np.random.default_rng(seed).normal(size=384).astype(np.float32) * 0.02
            low = t.lower()
            for kw_, ax in self.AXES.items():
                if kw_ in low:
                    v[ax] += 1.0
            out.append(v / np.linalg.norm(v))
        return np.vstack(out)


def modules(*specs):
    """specs: (id, title, triggers, body) -> on-demand file text."""
    parts = []
    for mid, title, triggers, body in specs:
        parts.append(f"---\nID: {mid}\nTitle: {title}\nType: ON_DEMAND\nTriggers: {triggers}\n---\n{body}\n")
    return "\n".join(parts)


def module_lines(block):
    return re.findall(r"^### MODULE: (\S+)", block or "", flags=re.M)


def with_env(flags, fn):
    saved = {k: os.environ.get(k) for k in flags}
    try:
        for k, v in flags.items():
            if v:
                os.environ[k] = "1"
            else:
                os.environ.pop(k, None)
        return fn()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


OFF_ALL = {"ZETTEL_TRIGGER_INFLECT_OFF": True, "ZETTEL_TRIGGER_SEMANTIC_OFF": True}
INFLECT_ONLY = {"ZETTEL_TRIGGER_INFLECT_OFF": False, "ZETTEL_TRIGGER_SEMANTIC_OFF": True}
ALL_ON = {"ZETTEL_TRIGGER_INFLECT_OFF": False, "ZETTEL_TRIGGER_SEMANTIC_OFF": False}

# ── 1. the suffix normaliser ─────────────────────────────────────────────
print("\n[1] _token_forms: inflections meet their base, unrelated words do not")
meet = lambda a, b: bool(ze._token_forms(a) & ze._token_forms(b))
for a, b in [("debug", "debugging"), ("debugged", "debug"), ("debugger", "debugging"), ("bugs", "bug"),
             ("errors", "error"), ("crashes", "crash"), ("vulnerabilities", "vulnerability"),
             ("tried", "try"), ("rick's", "rick"), ("cant", "can't"), ("coding", "code"),
             ("caching", "cache"), ("logging", "log"), ("logged", "logging"), ("fixing", "fix"),
             ("scaling", "scale"), ("testing", "tests"), ("feelings", "feeling")]:
    check(f"'{a}' meets '{b}'", meet(a, b), True)
for a, b in [("summer", "sum"), ("power", "pow"), ("profile", "profiler"), ("need", "ne"),
             ("string", "str"), ("bug", "bus"), ("thing", "th"), ("used", "us"), ("crash", "cash")]:
    check(f"'{a}' does not meet '{b}'", meet(a, b), False)
check("short tokens are left alone", ze._token_forms("ai"), frozenset({"ai"}))

# ── 2. token-sequence matching ───────────────────────────────────────────
print("\n[2] _match_inflected: multi-word triggers match as contiguous token sequences")
nodes = [{"id": "pk-trace", "title": "T", "content": "TRIGGERS: stack trace,race condition\n\nb"},
         {"id": "pk-stuck", "title": "S", "content": "TRIGGERS: can't find bug,debugging for hours\n\nb"},
         {"id": "pk-bug", "title": "B", "content": "TRIGGERS: debugging,bug,error\n\nb"}]
tc = ze._build_trigger_cache(frozenset(n["id"] for n in nodes), nodes)
m = lambda q: ze._match_inflected(tc, q)
check("plural of the last word", m("these stack traces are long"), ["pk-trace"])
check("plural inside a 2-word trigger", m("two race conditions"), ["pk-trace"])
check("apostrophe-free + plural", m("i cant find bugs"), ["pk-stuck", "pk-bug"])
check("past tense inside a 3-word trigger", m("debugged for hours"), ["pk-stuck", "pk-bug"])
check("non-contiguous does not match", m("stack of trace"), [])
check("word order matters", m("trace stack"), [])
check("hyphen/punctuation split like spaces", m("a stack-trace!"), ["pk-trace"])
check("hits come back in trigger (file) order", m("errors in a stack trace"), ["pk-trace", "pk-bug"])

# ── 3. exact hits are a strict, leading subset ───────────────────────────
print("\n[3] exact-subset guarantee: whatever the old regex fired still fires, first, in the same order")


def exact(tcache, q):
    out, seen = [], set()
    for pat, pk in tcache["patterns"]:
        if pk not in seen and pat.search(q.lower()):
            seen.add(pk)
            out.append(pk)
    return out


def combined(tcache, q, model=None):
    ex = exact(tcache, q)
    qv = model.encode([q]) if model else None
    soft = ze.soft_trigger_hits(tcache, q, set(ex), model=model, query_vec=qv,
                                body_sims={pk: 0.0 for pk, _, _ in tcache["cards"]} if model else None)
    return (ex + [pk for pk, _, _ in soft])[:3], ex[:3]


corpus_nodes = []
for f in [os.path.join(ROOT, "personas", "rick_ondemand.txt"), os.path.join(ROOT, "knowledge_bases", "Rick_kb.txt")]:
    for mod in ze.parse_on_demand_file(f):
        corpus_nodes.append({"id": mod.id, "title": mod.title, "trigger_match": mod.match,
                             "content": f"TRIGGERS: {','.join(mod.triggers)}\n\n{mod.content}"})
if not corpus_nodes:
    corpus_nodes = nodes
    print("  (Rick's files not found; using the synthetic modules)")
ctc = ze._build_trigger_cache(frozenset(n["id"] for n in corpus_nodes), corpus_nodes)
queries = [ze._node_triggers(n) for n in corpus_nodes]
queries = [t for ts in queries if ts for t in ts] + [
    "can you debug this for me", "it throws errors on startup", "is this a security vulnerability",
    "the fix didn't work", "do you miss Diane", "what's for dinner", "my plan is to debug the race condition"]
fake = FakeModel()
broken = []
for q in queries:
    new, old = combined(ctc, q, fake)
    if new[:len(old)] != old:
        broken.append(q)
check(f"over {len(queries)} queries ({len(corpus_nodes)} modules): old seeds are the prefix of new seeds",
      broken, [])

# ── 4. query_knowledge_graph end to end, lesion flags at call time ───────
print("\n[4] query_knowledge_graph: each layer on/off through its lesion flag")
ze.get_shared_model = lambda: fake
# Sections [4]-[6c] run on the fake model's geometry (keyword axes), so they pin
# the semantic threshold to a value that geometry was designed around instead
# of riding the calibrated constant; [7] uses the shipped value on real MiniLM.
SHIPPED_THRESHOLD = ze.SEMANTIC_TRIGGER_THRESHOLD
ze.SEMANTIC_TRIGGER_THRESHOLD = 0.30
dbm = db.UserManager()
U, P = f"trig_user_{os.getpid()}", "trig_persona"
ze.invalidate_zettel_cache(U, P)
FILE = os.path.join(TMP, "mods.txt")
with open(FILE, "w", encoding="utf-8") as f:
    f.write(modules(
        ("TR-EXACT-001", "Exact One", "segfault", "Plain notes about memory faults."),
        ("TR-INFL-001", "Inflected One", "debugging", "How Rick hunts a problem."),
        ("TR-SEM-001", "Severity Honesty", "exploit chain", "How bad a hole really is."),  # trigger phrase on axis 0, body not
        ("TR-CAT-001", "Kittens", "kitten", "Small felines."),
        ("TR-PARENT-001", "Raising Kids", "parenting", "Notes on raising kids.")))
ze.compile_behavioral_zettels(U, P, [FILE])
q = lambda text, flags: with_env(flags, lambda: module_lines(ze.query_knowledge_graph(U, P, text, top_k=5)))

check("exact trigger fires with every soft layer off", q("got a segfault again", OFF_ALL), ["TR-EXACT-001"])
check("'debug' does NOT reach trigger 'debugging' with the soft layers off", "TR-INFL-001" in q("can you debug it", OFF_ALL), False)
check("...and does with inflection on", "TR-INFL-001" in q("can you debug it", INFLECT_ONLY), True)
check("semantic: no exact/inflected word, layer off -> not seeded",
      "TR-SEM-001" in q("is this a vulnerability", INFLECT_ONLY), False)
check("semantic: layer on -> seeded", "TR-SEM-001" in q("is this a vulnerability", ALL_ON), True)
check("ZETTEL_TRIGGER_SEMANTIC_OFF=1 read per call (flip back off)",
      "TR-SEM-001" in q("is this a vulnerability", INFLECT_ONLY), False)

print("\n[5] ordering and the shared cap: exact, then inflected, then semantic; max 3")
got = q("segfault while I debug this vulnerability", ALL_ON)
check("exact first, inflected second, semantic third", got[:3], ["TR-EXACT-001", "TR-INFL-001", "TR-SEM-001"])
got = q("segfault while I debug this vulnerability, also kitten", ALL_ON)
check("two exact hits fill first (file order), the inflected hit takes the last slot", got[:3],
      ["TR-EXACT-001", "TR-CAT-001", "TR-INFL-001"])
check("the semantic hit over the cap is not seeded", "TR-SEM-001" in got, False)

print("\n[6] the semantic gate on inflected-only hits")
# 'kittens' -> trigger 'kitten': phrase on the cat axis, score ~0.5 -> clears the gate
check("an inflected hit with a semantic score over the gate is seeded",
      "TR-CAT-001" in q("look at those kittens", ALL_ON), True)
# 'parents' -> trigger 'parenting': lexically related, semantically unrelated (score ~0)
check("an inflected hit under the gate is turned away ('parents' vs 'parenting')",
      "TR-PARENT-001" in q("my parents are visiting", ALL_ON), False)
check("ZETTEL_TRIGGER_INFLECT_GATE_OFF=1 lets it through",
      "TR-PARENT-001" in q("my parents are visiting", {**ALL_ON, "ZETTEL_TRIGGER_INFLECT_GATE_OFF": True}), True)
check("semantics off -> no scores -> no gate (plain inflection)",
      "TR-PARENT-001" in q("my parents are visiting", INFLECT_ONLY), True)
check("inflection off -> not seeded at all", "TR-PARENT-001" in q("my parents are visiting", OFF_ALL), False)

# ── 6b. `Match: exact` -- the per-module opt-out ─────────────────────────
print("\n[6b] Match: exact keeps a module on the exact layer only")
import io, contextlib


def wall(match_line):
    return ("---\nID: TR-WALL-001\nTitle: The Wall\nType: ON_DEMAND\nLinks: [[TR-EXACT-001]]\n"
            f"Triggers: puppy, lost dog\n{match_line}---\nWall body, nothing to see.\n")


mods = ze.parse_on_demand_text(wall("Match: exact\n"), "wall.txt")
check("Match: stays in the header (one module, body untouched)",
      [(m.id, m.content, m.triggers, m.links) for m in mods],
      [("TR-WALL-001", "Wall body, nothing to see.", ["puppy", "lost dog"], ["TR-EXACT-001"])])
check("parsed as exact", mods[0].match, "exact")
check("case-insensitive", ze.parse_on_demand_text(wall("Match: EXACT\n"), "w")[0].match, "exact")
check("no Match: line -> all", ze.parse_on_demand_text(wall(""), "w")[0].match, "all")
check("Match: all -> all", ze.parse_on_demand_text(wall("Match: all\n"), "w")[0].match, "all")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    odd = ze.parse_on_demand_text(wall("Match: fuzzy\n"), "odd.txt")
check("unknown value -> all", odd[0].match, "all")
check_true("...and says so loudly", "TR-WALL-001 has unknown Match value 'fuzzy'" in buf.getvalue())
check("...and the body is still the body", odd[0].content, "Wall body, nothing to see.")
check("the line is not read as a trigger", odd[0].triggers, ["puppy", "lost dog"])
check("parser version is 3 (forces every file to re-parse once)", ze.ON_DEMAND_PARSER_VERSION, 3)

PX, PA = "trig_wall_exact", "trig_wall_all"     # same module, with and without the opt-out
for persona, line in ((PX, "Match: exact\n"), (PA, "")):
    ze.invalidate_zettel_cache(U, persona)
    path = os.path.join(TMP, f"{persona}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(wall(line))
    ze.compile_behavioral_zettels(U, persona, [path])
qp = lambda persona, text, flags=ALL_ON: with_env(flags, lambda: ze.query_knowledge_graph(U, persona, text, top_k=5))
check("stored as trigger_match='exact'",
      [n["trigger_match"] for n in dbm.get_zettel_nodes_for_persona(U, PX, include_embeddings=False)], ["exact"])
check("control (no Match: line) stored as 'all'",
      [n["trigger_match"] for n in dbm.get_zettel_nodes_for_persona(U, PA)], ["all"])
check("control: the inflected layer fires it ('puppies')", "TR-WALL-001" in module_lines(qp(PA, "two puppies")), True)
# 'doggo' shares the fake model's dog axis but no FTS token with 'lost dog'
check("control: the semantic layer fires it ('my doggo')", "TR-WALL-001" in module_lines(qp(PA, "my doggo")), True)
check("exact: the inflected layer skips it", "TR-WALL-001" in module_lines(qp(PX, "two puppies")), False)
check("exact: ...even ungated (semantics off)",
      "TR-WALL-001" in module_lines(qp(PX, "two puppies", INFLECT_ONLY)), False)
check("exact: the semantic layer skips it", "TR-WALL-001" in module_lines(qp(PX, "my doggo")), False)
out = qp(PX, "found a puppy")
check("exact: the exact trigger still fires it", "TR-WALL-001" in module_lines(out), True)
check("exact: a multi-word exact trigger fires it", "TR-WALL-001" in module_lines(qp(PX, "a lost dog again")), True)
check("the Match line never reaches injected text", ("match: exact" in out.lower(), "Match" in out), (False, False))
check("nor the stored content", [("Match" in n["content"]) for n in dbm.get_zettel_nodes_for_persona(U, PX)], [False])

print("\n[6c] parser v3 forces a recompile of an unchanged file")
path = os.path.join(TMP, f"{PX}.txt")
pks = lambda: {n["id"] for n in dbm.get_zettel_nodes_for_persona(U, PX)}
before = pks()
conn = __import__("sqlite3").connect(db.DB_PATH)
stored = conn.execute("SELECT content_hash FROM zettel_nodes WHERE source_entry_id=?", (path,)).fetchone()[0]
check_true("stored hash carries :p3", stored.endswith(":p3"))
conn.execute("UPDATE zettel_nodes SET content_hash=?, trigger_match='all' WHERE source_entry_id=?",
             (stored.rsplit(":p", 1)[0] + ":p2", path))      # as if compiled by the v2 parser
conn.commit()
conn.close()
ze.compile_behavioral_zettels(U, PX, [path])
check_true("a :p2 hash on unchanged bytes -> recompiled (new primary keys)", not (before & pks()))
check("...and the recompile restores trigger_match='exact'",
      [n["trigger_match"] for n in dbm.get_zettel_nodes_for_persona(U, PX)], ["exact"])

# ── 7. the real MiniLM, if it is already cached locally ──────────────────
ze.SEMANTIC_TRIGGER_THRESHOLD = SHIPPED_THRESHOLD
print(f"\n[7] real MiniLM: the semantic layer on a module whose words the query does not use "
      f"(shipped threshold {ze.SEMANTIC_TRIGGER_THRESHOLD})")
real = None
try:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from sentence_transformers import SentenceTransformer
    real = SentenceTransformer("all-MiniLM-L6-v2")
except Exception as e:
    print(f"  SKIP  real model unavailable ({type(e).__name__})")
if real is not None:
    ze.get_shared_model = lambda: real
    U2 = f"trig_real_{os.getpid()}"
    ze.invalidate_zettel_cache(U2, P)
    FILE2 = os.path.join(TMP, "real.txt")
    with open(FILE2, "w", encoding="utf-8") as f:
        f.write(modules(
            ("RL-DEBUG-001", "Debugging Philosophy", "debugging, bug, error, fix code",
             "Rick treats a bug as evidence that his model of the system is incomplete. Check the dumb "
             "things early. Read error messages as observations, not verdicts. Change one causal variable "
             "at a time when isolating a failure."),
            ("RL-FAMILY-001", "The Daughter He Failed", "beth, daughter, parenting",
             "Rick's guilt about leaving Beth, and how it shows up as contempt.")))
    ze.compile_behavioral_zettels(U2, P, [FILE2])
    nodes2 = dbm.get_zettel_nodes_for_persona(U2, P)
    tc2 = ze._build_trigger_cache(frozenset(n["id"] for n in nodes2), nodes2)
    tag = {n["id"]: n["node_id"] for n in nodes2}

    def soft_tags(text, flags):
        qv = real.encode([text], convert_to_numpy=True)
        qn = qv[0] / np.linalg.norm(qv[0])
        bs = {}
        for n in nodes2:   # the vector path's number: cos(query, stored body embedding)
            v = np.frombuffer(n["embedding"], dtype=np.float32)
            bs[n["id"]] = float(v @ qn / np.linalg.norm(v))
        ex = exact(tc2, text)
        return with_env(flags, lambda: [(tag[pk], lay) for pk, lay, s in ze.soft_trigger_hits(
            tc2, text, set(ex), model=real, query_vec=qv, body_sims=bs)])

    check("'the fix didn't work': no exact trigger", exact(tc2, "the fix didn't work"), [])
    check("...the semantic layer seeds the debugging module",
          ("RL-DEBUG-001", "semantic") in soft_tags("the fix didn't work", ALL_ON), True)
    check("...and nothing with ZETTEL_TRIGGER_SEMANTIC_OFF=1", soft_tags("the fix didn't work", INFLECT_ONLY), [])
    check("'what's for dinner' seeds nothing", soft_tags("what's for dinner", ALL_ON), [])
    check("end to end: query_knowledge_graph injects the debugging module",
          "RL-DEBUG-001" in with_env(ALL_ON, lambda: module_lines(
              ze.query_knowledge_graph(U2, P, "the fix didn't work", top_k=5))), True)

shutil.rmtree(TMP, ignore_errors=True)
print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
