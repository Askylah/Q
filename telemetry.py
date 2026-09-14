"""
telemetry.py -- the chart recorder behind the gauges.

Every neuromodulator number that matters for calibration (reflect_gate
verdicts and their nearest-similarity, GABA increments, dopamine deltas,
daemon gate decisions) used to reach the world through print(). The daemon is
a multiprocessing child of main.py, so print() is the terminal window: it
scrolls off, the window closes, the number is gone. Redis holds the organs'
*state* with TTLs (GABA 1 h, dopamine 3 h) -- a live gauge, not a record.
Measured 2026-09-13: the first live watermark cycle (Sep 11) left a
dense_observation row and nothing else; the `nearest` value that
REFLECT_SIM_CEIL tuning needs existed for one print call.

This module appends one JSON object per line to

    %LOCALAPPDATA%\\PersonaApp\\telemetry\\YYYY-MM-DD.jsonl

Rules:
- emit() NEVER raises. A telemetry failure must not take an organ down.
- Open/append/close per call. Lines are short; O_APPEND keeps the two
  writers (uvicorn app, daemon child) from interleaving mid-line on Windows.
- No imports from the organs. They import this, not the other way round.
- TELEMETRY_OFF=1 disables writes (read per call); TELEMETRY_DIR overrides
  the folder. tests/ set TELEMETRY_OFF so fixtures never land in the real sink.

Reading it back:

    import telemetry
    rows = telemetry.read()                # today
    rows = telemetry.read("2026-09-11")    # one day
    rows = telemetry.read_range(days=7)    # last week, oldest first
    telemetry.tail(20)                     # print the last 20 lines

or `python telemetry.py [N]` from the repo root. Each row has ts (epoch),
iso, pid, channel, event, username, persona, then the event's own fields.
"""

import os
import sys
import json
import time
import glob
import threading
import datetime as _dt

_LOCK = threading.Lock()


def _off() -> bool:
    """Read per call, not at import: test suites and smoke runs flip
    TELEMETRY_OFF after the organs are already imported."""
    return os.getenv("TELEMETRY_OFF", "") not in ("", "0", "false", "False")


def telemetry_dir() -> str:
    d = os.getenv("TELEMETRY_DIR")
    if not d:
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "PersonaApp", "telemetry")
    return d


def _path_for(ts: float) -> str:
    day = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    return os.path.join(telemetry_dir(), f"{day}.jsonl")


def emit(channel: str, event: str, username=None, persona=None, **payload) -> bool:
    """Append one record. Returns True if written, False if disabled or failed.
    Never raises."""
    if _off():
        return False
    try:
        ts = time.time()
        rec = {
            "ts": round(ts, 3),
            "iso": _dt.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds"),
            "pid": os.getpid(),
            "channel": str(channel),
            "event": str(event),
            "username": username,
            "persona": persona,
        }
        for k, v in payload.items():
            rec[k] = _jsonable(v)
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
        path = _path_for(ts)
        with _LOCK:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        return True
    except Exception:
        return False


def _jsonable(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        if isinstance(v, float):
            return round(v, 4)
        return v
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    return str(v)[:500]


# --- read side ------------------------------------------------------------

def read(day: str = None) -> list:
    """Rows for one day (YYYY-MM-DD, default today), oldest first.
    Malformed lines are skipped, not raised."""
    if day is None:
        day = _dt.date.today().strftime("%Y-%m-%d")
    path = os.path.join(telemetry_dir(), f"{day}.jsonl")
    return _read_file(path)


def read_range(days: int = 7) -> list:
    """Rows across the last `days` day-files present on disk, oldest first."""
    files = sorted(glob.glob(os.path.join(telemetry_dir(), "*.jsonl")))[-max(1, int(days)):]
    out = []
    for p in files:
        out.extend(_read_file(p))
    return out


def _read_file(path: str) -> list:
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def tail(n: int = 20, days: int = 2) -> None:
    rows = read_range(days)[-n:]
    for r in rows:
        head = {k: r.get(k) for k in ("iso", "channel", "event", "persona")}
        rest = {k: v for k, v in r.items()
                if k not in ("ts", "iso", "pid", "channel", "event", "username", "persona")}
        print(f"{head['iso']} [{head['channel']}] {head['event']} {head['persona'] or ''} {json.dumps(rest, ensure_ascii=False)}")
    if not rows:
        print(f"(no telemetry under {telemetry_dir()})")


if __name__ == "__main__":
    tail(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
