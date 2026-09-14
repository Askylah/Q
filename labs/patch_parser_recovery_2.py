"""Second pass: a header may also be missing the '---' AFTER it (header fused
with its own body), and fused runs chain. End the header at the last known
key line and recurse on the remainder. Also extends test [7]."""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

p = "zettel_engine.py"
raw = open(p, "rb").read()
crlf = b"\r\n" in raw
s = raw.decode("utf-8").replace("\r\n", "\n")
start = s.index("def _split_embedded_header(")
end = s.index('    return [("body", block)]\n', start) + len('    return [("body", block)]\n')
NEW = '''_HEADER_KEYS = ("ID", "Title", "Type", "Links", "Triggers", "Priority")


def _is_header_key_line(line: str) -> bool:
    l = line.lstrip()
    return any(l.startswith(k + ":") for k in _HEADER_KEYS)


def _split_embedded_header(block: str, filepath: str = "") -> list:
    """Classify a '---'-delimited block into [(kind, text)...], kind in
    {"header", "body"}, tolerating missing separators in either position:

      body ... ID: X / Title: ...        <- no '---' before the header
      ID: X / Title: ... / body ...      <- no '---' after the header
      body ... ID: X ... body ... ID: Y  <- chains of both

    A header starts at an `ID:` line whose next non-empty line is `Title:` and
    ends at the last consecutive known key line (ID/Title/Type/Links/Triggers/
    Priority). Text before it is body; text after it is fed back through this
    function, so a run of fused modules yields every one of them. Each
    recovery is logged so the author can put the separators back."""
    lines = block.split("\\n")
    n = len(lines)
    start = None
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("ID:"):
            continue
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j < n and lines[j].lstrip().startswith("Title:"):
            start = i
            break
    if start is None:
        return [("body", block)]
    # header runs while lines are known keys (blank lines inside are tolerated)
    k = start
    last_key = start
    while k < n:
        if _is_header_key_line(lines[k]):
            last_key = k
            k += 1
        elif not lines[k].strip() and k + 1 < n and _is_header_key_line(lines[k + 1]):
            k += 1
        else:
            break
    header = "\\n".join(lines[start:last_key + 1]).strip()
    before = "\\n".join(lines[:start]).strip()
    after = "\\n".join(lines[last_key + 1:]).strip()
    name = os.path.basename(filepath) or "?"
    first = header.splitlines()[0][:40]
    if before:
        print(f"[ZETTEL PARSER] {name}: recovered a header without a '---' before it ({first}); "
              f"put the separator back in the source file.")
    if after:
        print(f"[ZETTEL PARSER] {name}: recovered a header without a '---' after it ({first}); "
              f"put the separator back in the source file.")
    out = []
    if before:
        out.append(("body", before))
    out.append(("header", header))
    if after:
        out.extend(_split_embedded_header(after, filepath))
    return out
'''
s = s[:start] + NEW + s[end:]
open(p, "wb").write((s.replace("\n", "\r\n") if crlf else s).encode("utf-8"))
print("zettel_engine.py: _split_embedded_header v2")

# ---- test [7]: add the header-without-closing-separator shapes ----
t = "tests/test_typed_links.py"
s = open(t, encoding="utf-8").read()
old = '''check("Aye's typed link survived the recovery", mods["FZ-A-001"].links if "FZ-A-001" in mods else None, ["FZ-B-001"])
'''
new = '''check("Aye's typed link survived the recovery", mods["FZ-A-001"].links if "FZ-A-001" in mods else None, ["FZ-B-001"])

# the other omission: no '---' AFTER the header, header runs into its body; and a chain of them
FUSED2 = (
    "---\\nID: FY-A-001\\nTitle: Aye\\nType: ON_DEMAND\\nTriggers: aye\\n"      # <- no '---' here
    "Aye body.\\n\\n"
    "ID: FY-B-001\\nTitle: Bee\\nType: ON_DEMAND\\nLinks: [[FY-A-001]]\\nTriggers: bee\\n"   # <- nor here
    "Bee body line one.\\nBee body line two.\\n---\\n"
    "ID: FY-C-001\\nTitle: Cee\\nType: ON_DEMAND\\nTriggers: cee\\n---\\n"
    "Cee body.\\n"
)
FILE4 = os.path.join(TMP, "fused2.txt")
with open(FILE4, "w", encoding="utf-8") as f:
    f.write(FUSED2)
m2 = {m.id: m for m in ze.parse_on_demand_file(FILE4)}
check("header without a closing '---': all three modules parsed", sorted(m2), ["FY-A-001", "FY-B-001", "FY-C-001"])
check("Aye's body is just Aye's", m2["FY-A-001"].content if "FY-A-001" in m2 else None, "Aye body.")
check("Bee's body is just Bee's (chained recovery)", m2["FY-B-001"].content if "FY-B-001" in m2 else None,
      "Bee body line one.\\nBee body line two.")
check("Bee's typed link intact", m2["FY-B-001"].links if "FY-B-001" in m2 else None, ["FY-A-001"])
check("Bee's triggers intact", m2["FY-B-001"].triggers if "FY-B-001" in m2 else None, ["bee"])
check("Cee (well-formed) unaffected", m2["FY-C-001"].content if "FY-C-001" in m2 else None, "Cee body.")
'''
assert s.count(old) == 1
open(t, "w", encoding="utf-8", newline="\n").write(s.replace(old, new))
print("test [7] extended")
