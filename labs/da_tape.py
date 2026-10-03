"""da_tape.py -- sample every da:* key in Redis on a fixed interval and append to a CSV.

Read-only against Redis (SCAN + GET). Lives in labs/ (uvicorn reload_excludes=*labs*) so creating or editing it never trips the
uvicorn reload. Run it in its own terminal BEFORE you start chatting, leave it running,
then plot the CSV afterwards (or just eyeball it -- it prints a one-line summary per tick).

    python labs/da_tape.py            # 10 s ticks, writes labs/da_tape.csv
    python labs/da_tape.py --every 5  # faster
    python labs/da_tape.py --plot     # render labs/da_tape.png from the CSV and exit

Columns: wall_iso, epoch, username, persona, kind, v, ts_written, age_s, relaxed_now
  v          = value the neuron wrote at ts_written
  relaxed_now= what get_state() would report right now (same exp(-dt/tau) the module uses)
"""
import argparse, csv, json, math, os, subprocess, sys, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "da_tape.csv")
TONIC_BASELINE = float(os.getenv("DA_TONIC_BASELINE", "0.30"))
TONIC_TAU = float(os.getenv("DA_TONIC_TAU_SEC", str(45 * 60)))
PHASIC_TAU = float(os.getenv("DA_PHASIC_TAU_SEC", "90"))
BASE = {"tonic": (TONIC_BASELINE, TONIC_TAU), "phasic": (0.0, PHASIC_TAU),
        "novelty_spent": (0.0, TONIC_TAU)}  # EMA kinds relax to 0.5 on TONIC_TAU


def rcli(*args):
    out = subprocess.run(["docker", "exec", "q-redis", "redis-cli", *args],
                         capture_output=True, text=True, timeout=10)
    return out.stdout


def scan():
    keys = [k for k in rcli("--scan", "--pattern", "da:*").split() if k]
    rows = []
    now = time.time()
    for k in sorted(keys):
        raw = rcli("GET", k).strip()
        try:
            d = json.loads(raw)
        except Exception:
            continue
        _, user, persona, kind = k.split(":", 3)
        base, tau = BASE.get(kind, (0.5, TONIC_TAU))
        age = now - float(d["ts"])
        relaxed = base + (float(d["v"]) - base) * math.exp(-age / tau)
        rows.append((datetime.datetime.now().isoformat(timespec="seconds"), round(now, 1),
                     user, persona, kind, float(d["v"]), float(d["ts"]), round(age, 1), round(relaxed, 4)))
    return rows


def record(every):
    new = not os.path.exists(CSV)
    with open(CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["wall_iso", "epoch", "username", "persona", "kind", "v", "ts_written", "age_s", "relaxed_now"])
        print(f"[da_tape] sampling every {every}s -> {CSV}  (ctrl-c to stop)", flush=True)
        while True:
            rows = scan()
            for r in rows:
                w.writerow(r)
            f.flush()
            if rows:
                summ = "  ".join(f"{r[2]}/{r[3]}:{r[4]}={r[8]:.3f}" for r in rows)
            else:
                summ = "no da:* keys -- every persona is at cold baseline"
            print(f"{rows[0][0] if rows else datetime.datetime.now().isoformat(timespec='seconds')}  {summ}", flush=True)
            time.sleep(every)


def plot():
    import collections
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("pip install matplotlib, or just open da_tape.csv")
    series = collections.defaultdict(list)
    with open(CSV) as f:
        for r in csv.DictReader(f):
            series[(r["username"], r["persona"], r["kind"])].append((float(r["epoch"]), float(r["relaxed_now"])))
    if not series:
        sys.exit("csv is empty -- nothing was ever raised above baseline while recording")
    t0 = min(p[0] for s in series.values() for p in s)
    fig, ax = plt.subplots(figsize=(11, 5))
    for (u, p, k), pts in sorted(series.items()):
        pts.sort()
        ax.plot([(x - t0) / 60 for x, _ in pts], [y for _, y in pts], label=f"{u}/{p} {k}", lw=1.6)
    ax.axhline(0.50, ls="--", lw=0.8, color="grey", label="explore threshold")
    ax.axhline(TONIC_BASELINE, ls=":", lw=0.8, color="grey", label="tonic baseline")
    ax.set_xlabel("minutes since first sample"); ax.set_ylabel("value"); ax.legend(fontsize=8); ax.grid(alpha=.3)
    out = os.path.join(HERE, "da_tape.png")
    fig.tight_layout(); fig.savefig(out, dpi=130)
    print("wrote", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=10)
    ap.add_argument("--plot", action="store_true")
    a = ap.parse_args()
    plot() if a.plot else record(a.every)
