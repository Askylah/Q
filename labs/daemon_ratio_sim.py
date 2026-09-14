"""
daemon_ratio_sim.py -- the hidden ratio Sky named on 2026-09-13.

The daemon is the one actor that pushes both signals on the same event: each
gap pick pays +BOOST tonic (dopamine, tau 2700 s, decays toward 0.30) and one
redundancy step to GABA (BASE * GROWTH**(streak-1), tau 600 s, streak TTL
1200 s). Gate = tonic >= 0.5 AND inhibition < 0.5. This runs that loop with
the real constants, no conversation, unlimited fresh gaps, starting from a
gate that a conversation just opened, and reports who closes it and the
steady-state duty cycle. Pure arithmetic: imports the constants only.

    python labs/daemon_ratio_sim.py [interval_s ...]
"""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TELEMETRY_OFF", "1")
import gaba_state as gs
import dopamine_state as ds

BOOST = 0.04
HOURS = 6.0


def run(interval, start_tonic=0.6, verbose=False):
    t = start_tonic; inh = 0.0; streak = 0; last_red = -1e9
    gate_open = True; opened_at = 0.0
    picks = 0; closes = []; opens = 0; steps = int(HOURS * 3600 / interval)
    now = 0.0
    for _ in range(steps):
        now += interval
        # decay both toward their baselines over one interval
        t = ds.TONIC_BASELINE + (t - ds.TONIC_BASELINE) * math.exp(-interval / ds.TONIC_TAU_SEC)
        inh = inh * math.exp(-interval / gs.GABA_TAU_SEC)
        tonic_ok = t >= ds.EXPLORE_THRESHOLD
        gaba_shut = inh >= gs.GATE_INHIBITION_MAX
        exploring = tonic_ok and not gaba_shut
        if exploring and not gate_open:
            gate_open = True; opened_at = now; opens += 1
        if not exploring and gate_open:
            gate_open = False
            closes.append(("both" if (gaba_shut and not tonic_ok) else "gaba" if gaba_shut else "tonic",
                           round(now - opened_at), picks))
        if exploring:
            picks += 1
            t = min(1.0, t + BOOST)
            if now - last_red > gs.GABA_STREAK_TTL_SEC:
                streak = 0
            streak += 1; last_red = now
            inh = min(1.0, inh + gs.GABA_BASE * gs.GABA_GROWTH ** (streak - 1))
            if verbose:
                print(f"  t={now/60:6.1f}m pick#{picks} tonic={t:.3f} inh={inh:.3f} streak={streak}")
    return {"interval_s": interval, "picks_6h": picks, "closes": len(closes), "reopens": opens,
            "first_close": closes[0] if closes else None,
            "closed_by": {k: sum(1 for c in closes if c[0] == k) for k in ("tonic", "gaba", "both")},
            "final_tonic": round(t, 3), "final_inh": round(inh, 3),
            "picks_per_hour_steady": round(picks / HOURS, 1)}


if __name__ == "__main__":
    ivs = [float(a) for a in sys.argv[1:]] or [30, 60, 120, 300, 600, 900, 1500]
    print(f"constants: BOOST={BOOST} tonic_tau={ds.TONIC_TAU_SEC:.0f}s baseline={ds.TONIC_BASELINE} "
          f"explore>={ds.EXPLORE_THRESHOLD} | GABA base={gs.GABA_BASE} growth={gs.GABA_GROWTH} "
          f"tau={gs.GABA_TAU_SEC:.0f}s streak_ttl={gs.GABA_STREAK_TTL_SEC:.0f}s gate>={gs.GATE_INHIBITION_MAX}")
    print(f"scenario: gate just opened by a conversation (tonic 0.60), then silence, unlimited fresh gaps, {HOURS:.0f} h\n")
    for iv in ivs:
        r = run(iv)
        fc = r["first_close"]
        print(f"interval={iv:>6.0f}s  picks/6h={r['picks_6h']:>4}  closes={r['closes']:>3}  "
              f"first close: by={fc[0] if fc else '-':<5} after {fc[1] if fc else '-':>5}s / {fc[2] if fc else '-':>3} picks  "
              f"closed_by={r['closed_by']}  end tonic={r['final_tonic']} inh={r['final_inh']}  "
              f"~{r['picks_per_hour_steady']} picks/h")
