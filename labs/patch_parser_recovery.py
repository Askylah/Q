"""parse_on_demand_file: survive a missing '---' between a body and the next
header instead of silently dropping the module. Plus a parser-version salt on
the compile hash so every persona recompiles once. Idempotent, preserves CRLF."""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def patch(path, pairs, marker):
    raw = open(path, "rb").read()
    crlf = b"\r\n" in raw
    txt = raw.decode("utf-8").replace("\r\n", "\n")
    if marker in txt:
        print("SKIP (already patched)", path); return
    for old, new in pairs:
        n = txt.count(old)
        assert n == 1, f"{path}: expected 1 match, got {n} for:\n{old[:200]}"
        txt = txt.replace(old, new)
    open(path, "wb").write((txt.replace("\n", "\r\n") if crlf else txt).encode("utf-8"))
    print("patched", path, f"({len(pairs)} edits, {'CRLF' if crlf else 'LF'})")


OLD_LOOP = '''    blocks = re.split(r"^---$", raw_content, flags=re.MULTILINE)
    
    current_header = None
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        if "ID:" in block and "Title:" in block:
            current_header = _parse_header(block)
        elif current_header:
            new_module = OnDemandModule(
                id=current_header.get("ID", "UNKNOWN"),
                title=current_header.get("Title", "Untitled"),
                type=current_header.get("Type", "ON_DEMAND"),
                links=current_header.get("Links", []),
                triggers=current_header.get("Triggers", []),
                content=block,
                priority=current_header.get("Priority", "NORMAL")
            )
            modules.append(new_module)
            current_header = None
'''

NEW_LOOP = '''    # FIX(parser-eats-module): the old test for "is this block a header" was
    # `"ID:" in block and "Title:" in block`. When an author forgets the '---'
    # between a body and the next header, the two fuse into one block, the
    # fused block passes that test, and the module whose body it was is
    # silently dropped -- along with every module in the run until the next
    # clean separator. Rick_kb.txt lost 8 modules that way (TOOL-CHECK/SYS/
    # INFO/OPT, ARCH-DATA/ROBUST, SCAR-008/009) and rick_ondemand.txt 2
    # (RESEARCH-002/003), measured 2026-09-13. Now: a header is a block whose
    # FIRST line is `ID:`; a body block that contains an `ID:` line followed
    # by a `Title:` line is split there, the front half kept as the body it
    # was, the back half treated as the header it is. Recovery is logged so
    # the author can put the separator back.
    blocks = re.split(r"^---\\s*$", raw_content, flags=re.MULTILINE)

    current_header = None
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        for kind, text in _split_embedded_header(block, filepath):
            if kind == "header":
                if current_header is not None:
                    print(f"[ZETTEL PARSER] {os.path.basename(filepath)}: module "
                          f"{current_header.get('ID', '?')} has no body (header followed by header); skipped.")
                current_header = _parse_header(text)
            elif current_header is not None:
                modules.append(OnDemandModule(
                    id=current_header.get("ID", "UNKNOWN"),
                    title=current_header.get("Title", "Untitled"),
                    type=current_header.get("Type", "ON_DEMAND"),
                    links=current_header.get("Links", []),
                    triggers=current_header.get("Triggers", []),
                    content=text,
                    priority=current_header.get("Priority", "NORMAL")
                ))
                current_header = None
            # else: body text before any header -- preamble, ignored
    if current_header is not None:
        print(f"[ZETTEL PARSER] {os.path.basename(filepath)}: module "
              f"{current_header.get('ID', '?')} has no body (end of file); skipped.")
'''

HELPERS = '''ON_DEMAND_PARSER_VERSION = 2


def _split_embedded_header(block: str, filepath: str = "") -> list:
    """Classify a '---'-delimited block. Returns [(kind, text)...] with kind in
    {"header", "body"}. A block whose first line is `ID:` is a header. A block
    that starts as body but contains an `ID:` line whose next non-empty line is
    `Title:` is a fused body+header (missing separator) and is split in two."""
    lines = block.split("\\n")
    if lines and lines[0].lstrip().startswith("ID:"):
        return [("header", block)]
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("ID:"):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and lines[j].lstrip().startswith("Title:"):
            body = "\\n".join(lines[:i]).strip()
            header = "\\n".join(lines[i:]).strip()
            print(f"[ZETTEL PARSER] {os.path.basename(filepath) or '?'}: recovered a header without a "
                  f"'---' before it ({header.splitlines()[0][:40]}); put the separator back in the source file.")
            out = []
            if body:
                out.append(("body", body))
            out.append(("header", header))
            return out
    return [("body", block)]


def parse_on_demand_file(filepath: str) -> list:'''

patch("zettel_engine.py", [
    (OLD_LOOP, NEW_LOOP),
    ("def parse_on_demand_file(filepath: str) -> list:", HELPERS),
    ('''        file_hash = get_file_hash_cached(path)
        if not file_hash:
            continue
            
        # If the file hasn't changed, skip compilation
        if path in existing_hashes and existing_hashes[path] == file_hash:
            continue
''', '''        file_hash = get_file_hash_cached(path)
        if not file_hash:
            continue
            
        # The stored hash carries the parser version: a parser fix (2026-09-13,
        # missing-separator recovery) must recompile files whose bytes did not
        # change, or the modules it recovers never reach the graph.
        file_hash = f"{file_hash}:p{ON_DEMAND_PARSER_VERSION}"
        # If the file hasn't changed, skip compilation
        if path in existing_hashes and existing_hashes[path] == file_hash:
            continue
'''),
], marker="def _split_embedded_header")
print("done")
