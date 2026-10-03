"""
Persona tail: contrast pairs and stance wording at the end of the prompt.

Standalone, like the rest of tests/ -- no pytest:

    python tests/test_persona_tail.py

Exits non-zero on any failure. Reads personas/rick_tail.txt and writes only a
temp file it deletes. No database, no Redis, no embedding call, no LLM.

Each check names the failure it exists to prevent.
"""
import os, sys, json, tempfile
os.environ["TELEMETRY_OFF"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# Nothing here reads or writes organ state, but importing the parser imports
# redis_client, which connects. Point it at the test db like the other suites.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.pop("PERSONA_TAIL_OFF", None)

import persona_tail as pt
import efferent as ef

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def check_true(name, got):
    check(name, bool(got), True)


FIXTURE = """preamble that is not a module

---
ID: CONTRAST-001
Title: always
Type: ALWAYS_LOAD
---
[REGRESSION] Happy to help!
[X] No.

---
ID: CONTRAST-B
Title: gated
Type: BAND
Triggers: inhibited, restless
---
[REGRESSION] As I said earlier.
[X] Scroll up.

---
ID: CONTRAST-ORPHAN
Title: band type, no band
Type: BAND
---
can never ride

---
ID: CONTRAST-TYPO
Title: misspelled band
Type: BAND
Triggers: inhibitted
---
can never ride either

---
ID: CONTRAST-ZETTELISH
Title: borrowed the zettel vocabulary
Type: ON_DEMAND
Triggers: inhibited
---
valid band, wrong word for it

---
ID: DISP-INHIBITED
Title: stance
Type: BAND
Triggers: inhibited
---
You have heard this.

---
ID: LORE-001
Title: not ours
Type: ON_DEMAND
Triggers: inhibited
---
lore does not belong in a tail file
"""

fd, FIX_PATH = tempfile.mkstemp(suffix=".txt", text=True)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(FIXTURE)

try:
    print("[1] no file, no tail")
    # Prevents: a persona without a tail_file (every custom persona) getting a
    # changed prompt, or a bad path taking the turn down.
    check("missing config", pt.load_for({}), pt._EMPTY)
    check("None persona_data", pt.load_for(None), pt._EMPTY)
    check("path that does not exist", pt.load("personas/_nope_.txt"), pt._EMPTY)
    check("empty tail renders nothing", pt.render_contrast(pt._EMPTY, "inhibited"), "")

    print("[2] modules land in the right bucket")
    tail = pt.load(FIX_PATH)
    check("always pairs", [i for i, _ in tail["always"]], ["CONTRAST-001"])
    check("gated pairs", [(i, sorted(b)) for i, b, _ in tail["gated"]], [("CONTRAST-B", ["inhibited", "restless"])])
    check("stance overrides", tail["stances"], {"inhibited": "You have heard this."})
    # Prevents: a pair that can never fire sitting in the file looking alive.
    # The loader prints why; here we check it did not quietly become ALWAYS.
    ids = [i for i, _ in tail["always"]] + [i for i, _, _ in tail["gated"]]
    check_true("BAND with no band is dropped, not promoted", "CONTRAST-ORPHAN" not in ids)
    # Prevents: the two meanings of ON_DEMAND blurring. It is the zettel
    # engine's word for "retrieved from what the operator said"; a tail module
    # using it is refused even when the band it names is valid.
    check_true("Type ON_DEMAND is refused in a tail file", "CONTRAST-ZETTELISH" not in ids)
    check_true("a misspelled band is dropped, not promoted", "CONTRAST-TYPO" not in ids)
    check_true("non-tail modules are ignored", "LORE-001" not in ids)

    print("[3] gating follows the band")
    at_rest = [i for i, _ in pt.select_contrast(tail, "")]
    bored = [i for i, _ in pt.select_contrast(tail, "inhibited")]
    keen = [i for i, _ in pt.select_contrast(tail, "exploring")]
    check("rest: always only", at_rest, ["CONTRAST-001"])
    check("inhibited: always then gated", bored, ["CONTRAST-001", "CONTRAST-B"])
    check("a band the pair does not name", keen, ["CONTRAST-001"])
    block = pt.render_contrast(tail, "inhibited")
    check_true("wrapped", block.strip().startswith("[VOICE_CONTRAST]") and block.strip().endswith("[/VOICE_CONTRAST]"))
    check_true("told not to quote them", "do not quote" in block)

    print("[4] the always-on pairs can be cut")
    # Prevents: being unable to measure whether the pairs change the voice.
    os.environ["PERSONA_TAIL_OFF"] = "1"
    check("lesion drops always, keeps the band's pairs",
          [i for i, _ in pt.select_contrast(tail, "inhibited")], ["CONTRAST-B"])
    check("lesion at rest is empty", pt.render_contrast(tail, ""), "")
    os.environ.pop("PERSONA_TAIL_OFF")

    print("[5] the tail has a ceiling and drops whole pairs")
    # Prevents: a half pair. A [REGRESSION] line whose fix was cut off is an
    # instruction to write the failure.
    big = {"stances": {}, "always": [("A1", "a" * 1800), ("A2", "b" * 1800)],
           "gated": [("G1", {"inhibited"}, "c" * 1000)]}
    kept = pt.select_contrast(big, "inhibited")
    check("the band's pair is budgeted first, overflow dropped whole", [i for i, _ in kept], ["A1", "G1"])
    check_true("nothing was truncated", all(len(c) in (1800, 1000) for _, c in kept))
    check_true("under the ceiling", sum(len(c) for _, c in kept) <= pt.MAX_CONTRAST_CHARS)

    print("[6] an edit to the file is picked up")
    # Prevents: the mtime cache serving yesterday's pairs to an author mid-edit.
    with open(FIX_PATH, "a", encoding="utf-8") as f:
        f.write("\n---\nID: CONTRAST-NEW\nTitle: new\nType: ALWAYS_LOAD\n---\n[REGRESSION] a\n[X] b\n")
    os.utime(FIX_PATH, (os.path.getatime(FIX_PATH), os.path.getmtime(FIX_PATH) + 5))
    check_true("new pair appears", "CONTRAST-NEW" in [i for i, _ in pt.load(FIX_PATH)["always"]])

    print("[7] Rick's real file")
    # Prevents: a header typo in the shipped file silently eating a module.
    with open(os.path.join(ROOT, "personas.json"), "r", encoding="utf-8") as f:
        rick_cfg = json.load(f)["rick"]
    check_true("tail_file is configured", rick_cfg.get("tail_file"))
    # Prevents: the tail being compiled into the zettel graph as lore.
    check_true("and is not an on_demand file", rick_cfg["tail_file"] not in rick_cfg.get("on_demand_files", []))
    # Prevents: a file configured both ways. The zettel engine would compile
    # the pairs as lore and match "inhibited" against the operator's message.
    both = dict(rick_cfg, on_demand_files=list(rick_cfg.get("on_demand_files", [])) + [rick_cfg["tail_file"]])
    check("a file listed as both tail and on-demand is refused", pt.load_for(both), pt._EMPTY)
    rick = pt.load_for(rick_cfg)
    check("every band has Rick's wording", sorted(rick["stances"]), sorted(pt.BANDS))
    check("always pairs", len(rick["always"]), 5)
    check("gated pairs", len(rick["gated"]), 2)
    for pid, content in rick["always"] + [(i, c) for i, _, c in rick["gated"]]:
        check_true(f"{pid} has both halves", "[REGRESSION]" in content and "[RICK]" in content)
    check_true("the whole always set fits the ceiling",
               len(pt.select_contrast(rick, "")) == len(rick["always"]))
    for band in pt.BANDS:
        text = ef.render_stance(band, rick["stances"])
        check_true(f"{band}: Rick's wording replaces the generic", ef.STANCES[band] not in text)
        check(f"{band}: no organ vocabulary",
              [w for w in ("dopamine", "gaba", "tonic", "phasic", "inhibition", "streak") if w in text.lower()], [])
finally:
    os.remove(FIX_PATH)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("all persona_tail checks passed")
