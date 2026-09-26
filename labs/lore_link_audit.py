#!/usr/bin/env python3
"""lore_link_audit.py -- check every [[link]] in a set of lore files against the modules that
actually exist, and say WHY each one that does not resolve is missing.

    py labs\\lore_link_audit.py                  audit the default sets (SETS below)
    py labs\\lore_link_audit.py FILE [FILE ...]  audit these files as one set

Read-only: opens files for reading, writes nothing.

A module "exists" the way the hardened parsers (Q's zettel parser, YAGAIDB's lore import) see it:
an `ID:` line whose next non-blank line is `Title:`. For each unresolved target the report checks,
in order: parser-invisible header in the set, core-persona definition, formatting/typo near-miss,
another copy of the files on disk that does define it, and which numbers of its family exist.
"""
import difflib
import os
import re
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
DESK = HOME / "OneDrive" / "Desktop"
PA = DESK / "Personas" / "PersonaApp-merged"

SETS = {
    # The single live copy since 2026-09-24 (the ~/.claude copies were retired to Personas/lore_backups/).
    "PersonaApp-merged": [PA / "knowledge_bases" / "Rick_kb.txt", PA / "personas" / "rick_ondemand.txt"],
}
# Defined here = part of the core persona / always-loaded layer, never a lore module.
CORE = [PA / "personas" / "rick.txt", PA / "personas" / "rick_tail.txt",
        PA / "personas" / "global_rules.txt", HOME / ".claude" / "CLAUDE.md"]
# Other copies searched for "defined somewhere else" evidence.
COPY_NAME = re.compile(r"(ondemand|_kb|\.lore\.txt)", re.I)
SKIP_DIRS = {"node_modules", ".git", "dist_installer", ".venv", "venv", "__pycache__", "site-packages"}

ID_RE = re.compile(r"^\s*ID:\s*(.+?)\s*$")
TITLE_RE = re.compile(r"^\s*Title:")
LINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


def read(p):
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


def headers(lines):
    """[(id, lineno, valid)] -- valid when the next non-blank line is Title:."""
    out = []
    for i, ln in enumerate(lines):
        m = ID_RE.match(ln)
        if not m:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        out.append((m.group(1), i + 1, j < len(lines) and bool(TITLE_RE.match(lines[j]))))
    return out


HEADER_FIELD = re.compile(r"^\s*(Title|Type|Links?|Triggers|Priority):")


def body_lines(path, header_line):
    """Non-blank lines between this header and the next ID:, excluding header fields and ---."""
    n = 0
    for ln in read(path)[header_line:]:
        if ID_RE.match(ln):
            break
        if ln.strip() and ln.strip() != "---" and not HEADER_FIELD.match(ln):
            n += 1
    return n


def norm(s):
    s = re.sub(r"[\s_]+", "-", s.strip().upper())
    return re.sub(r"\d+", lambda m: str(int(m.group())), s)


def family(s):
    m = re.match(r"^(.*?)-?(\d+)$", s.strip().upper())
    return (m.group(1), int(m.group(2))) if m else (None, None)


def short(p):
    try:
        return str(p.relative_to(DESK))
    except ValueError:
        return str(p).replace(str(HOME), "~")


def find_copies(exclude):
    found = []
    for root in (DESK, HOME / ".claude"):
        base = len(root.parts)
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS and len(Path(dp).parts) - base < 4]
            for fn in fns:
                p = Path(dp) / fn
                if COPY_NAME.search(fn) and p.resolve() not in exclude:
                    found.append(p)
    return sorted(set(found))


def audit(name, files, core_defs, copies):
    print(f"\n{'=' * 78}\nSET: {name}")
    defs, refs = {}, []
    for f in files:
        if not f.exists():
            print(f"  !! {short(f)} does not exist -- skipping this set")
            return
        lines = read(f)
        hs = headers(lines)
        valid = [h for h in hs if h[2]]
        print(f"  {short(f)}: {len(lines)} lines, {len(valid)} modules"
              + (f", {len(hs) - len(valid)} ID: lines the parser skips" if len(hs) != len(valid) else ""))
        for hid, ln, ok in hs:
            defs.setdefault(hid, []).append((f, ln, ok))
        owner, hi = "(text before the first module)", 0
        for i, ln in enumerate(lines, 1):
            while hi < len(valid) and valid[hi][1] <= i:
                owner, hi = valid[hi][0], hi + 1
            for m in LINK_RE.finditer(ln):
                where = "Links: line" if ln.strip().startswith("Links:") else "body text"
                refs.append((m.group(1), f, i, owner, where))

    dups = {k: v for k, v in defs.items() if sum(1 for d in v if d[2]) > 1}
    for k, v in sorted(dups.items()):
        print(f"  DUPLICATE ID {k}: " + ", ".join(f"{short(f)}:{ln} ({body_lines(f, ln)} body lines)"
                                                 for f, ln, ok in v if ok)
              + "  -> one copy shadows the other; a 0-body copy is a leftover header, delete it")

    all_valid = {k for k, v in defs.items() if any(d[2] for d in v)}
    by_norm = {}
    for k in all_valid | set(core_defs):
        by_norm.setdefault(norm(k), []).append(k)
    same = cross = 0
    cross_list, bad = [], {}
    for tgt, f, ln, owner, where in refs:
        hits = [d for d in defs.get(tgt, []) if d[2]]
        if hits:
            if any(d[0] == f for d in hits):
                same += 1
            else:
                cross += 1
                cross_list.append((tgt, f, ln, owner, hits[0]))
            continue
        bad.setdefault(tgt, []).append((f, ln, owner, where))

    print(f"\n  {len(refs)} links total: {same} resolve in the same file, {cross} point into the OTHER file, "
          f"{sum(len(v) for v in bad.values())} do not resolve ({len(bad)} distinct targets)")
    if cross_list:
        print("\n  CROSS-FILE links (fine in Q, which loads both files into one graph; DANGLING in YAGAIDB")
        print("  if the two files were imported as two separate books -- import them as one book):")
        for tgt, f, ln, owner, (tf, tln, _) in cross_list:
            print(f"    {short(f)}:{ln} in {owner} -> [[{tgt}]] lives in {tf.name}:{tln}")

    for tgt in sorted(bad, key=lambda t: (norm(t), t)):
        places = bad[tgt]
        print(f"\n  [[{tgt}]]  -- referenced {len(places)}x")
        for f, ln, owner, where in places:
            print(f"      from {short(f)}:{ln}  (inside {owner}, {where})")
        why = []
        invisible = [d for d in defs.get(tgt, []) if not d[2]]
        for f, ln, _ in invisible:
            why.append(f"HEADER BROKEN: `ID: {tgt}` exists at {short(f)}:{ln} but the next non-blank line is "
                       f"not `Title:`, so the parser never sees the module. Fix the header.")
        if tgt in core_defs:
            cf, cln = core_defs[tgt]
            why.append(f"CORE, NOT LORE: defined in {short(cf)}:{cln}. That is the always-loaded persona, "
                       f"not a lore module, so a lore lookup finds nothing. Harmless if the persona is "
                       f"loaded; drop the link or accept the warning.")
        nm = [k for k in by_norm.get(norm(tgt), []) if k != tgt]
        if nm:
            why.append(f"FORMATTING TYPO: the link says `{tgt}` but the module is `{nm[0]}` "
                       f"(case / spacing / zero-padding differ).")
        elif not invisible and tgt not in core_defs:
            close = difflib.get_close_matches(tgt.upper(), sorted(all_valid | set(core_defs)), n=3, cutoff=0.75)
            if close:
                why.append(f"PROBABLE TYPO: nearest existing IDs: {', '.join(close)}")
        elsewhere = []
        for cp, cdefs in copies.items():
            for hid, cln, ok in cdefs:
                if hid == tgt and ok:
                    elsewhere.append(f"{short(cp)}:{cln} (modified {datetime.fromtimestamp(cp.stat().st_mtime):%Y-%m-%d})")
        if elsewhere:
            why.append("EXISTS IN ANOTHER COPY: " + "; ".join(elsewhere[:6])
                       + (f" (+{len(elsewhere) - 6} more)" if len(elsewhere) > 6 else "")
                       + " -- the copies have drifted; the module was never carried over, or was deleted here.")
        fam, num = family(tgt)
        if fam and tgt not in core_defs:
            nums = sorted({family(k)[1] for k in all_valid if family(k)[0] == fam})
            if nums:
                why.append(f"FAMILY {fam}: this set has numbers {nums}.")
            else:
                why.append(f"FAMILY {fam}: no module in this set uses that prefix at all.")
        if not (invisible or tgt in core_defs or nm or elsewhere):
            why.append("VERDICT: genuinely missing -- no header anywhere searched. Either write the module or "
                       "remove the link.")
        for w in why:
            print(f"      - {w}")


def main():
    core_defs = {}
    for p in CORE:
        if p.exists():
            for hid, ln, _ in headers(read(p)):
                core_defs.setdefault(hid, (p, ln))
    sets = {"command line": [Path(a) for a in sys.argv[1:]]} if sys.argv[1:] else SETS
    audited = {p.resolve() for fs in sets.values() for p in fs if p.exists()}
    copies = {}
    for cp in find_copies(audited):
        try:
            copies[cp] = headers(read(cp))
        except OSError:
            pass
    all_set_files = [p for fs in sets.values() for p in fs if p.exists()]
    for p in all_set_files:  # the audited sets are also evidence for each other
        copies[p] = headers(read(p))
    print(f"core-persona IDs known: {len(core_defs)}; other copies searched: {len(copies) - len(all_set_files)}")
    for name, files in sets.items():
        own = {p.resolve() for p in files}
        audit(name, files, core_defs, {p: h for p, h in copies.items() if p.resolve() not in own})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
