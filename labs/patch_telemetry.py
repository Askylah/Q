"""One-shot patch: wire telemetry.emit() into every [DA]/[GABA] stdout site.
Preserves each file's line endings (memory_plugin/memory_engine/stream_worker are CRLF).
Run from the repo root. Idempotent: refuses to re-apply if the shim is already present."""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

IMPORT = '''try:
    import telemetry as _tm
except Exception:  # telemetry must never be load-bearing
    _tm = None


def _emit(*a, **k):
    if _tm is not None:
        try:
            _tm.emit(*a, **k)
        except Exception:
            pass
'''


def patch(path, pairs):
    raw = open(path, "rb").read()
    crlf = b"\r\n" in raw
    txt = raw.decode("utf-8").replace("\r\n", "\n")
    if "import telemetry as _tm" in txt:
        print("SKIP (already patched)", path)
        return
    for old, new in pairs:
        n = txt.count(old)
        assert n == 1, f"{path}: expected 1 match, got {n} for:\n{old[:160]}"
        txt = txt.replace(old, new)
    out = txt.replace("\n", "\r\n") if crlf else txt
    open(path, "wb").write(out.encode("utf-8"))
    print("patched", path, f"({len(pairs)} edits, {'CRLF' if crlf else 'LF'})")


# ---------------- gaba_state.py ----------------
patch("gaba_state.py", [
("""_LOCK = threading.Lock()
_MEM_FALLBACK = {}
""", """_LOCK = threading.Lock()
_MEM_FALLBACK = {}

""" + IMPORT),
("""    print(f"[GABA] redundant({source}): streak={stk} nearest={near} "
          f"+{min(inc, 1.0 - cur):.4f} inhibition={new:.4f}"
          f"{' GATE_SHUT' if new >= GATE_INHIBITION_MAX else ''}", flush=True)
    return {"streak": stk, "increment": round(inc, 4), **get_state(username, persona)}
""", """    print(f"[GABA] redundant({source}): streak={stk} nearest={near} "
          f"+{min(inc, 1.0 - cur):.4f} inhibition={new:.4f}"
          f"{' GATE_SHUT' if new >= GATE_INHIBITION_MAX else ''}", flush=True)
    _emit("gaba", "redundant", username, persona, source=source, streak=stk,
          nearest=nearest, before=cur, increment=min(inc, 1.0 - cur), inhibition=new,
          gate_shut=bool(new >= GATE_INHIBITION_MAX))
    return {"streak": stk, "increment": round(inc, 4), **get_state(username, persona)}
"""),
("""    print(f"[GABA] novel({source}): streak reset, nearest={near} inhibition={inh:.4f} (draining)",
          flush=True)
    return get_state(username, persona)
""", """    print(f"[GABA] novel({source}): streak reset, nearest={near} inhibition={inh:.4f} (draining)",
          flush=True)
    _emit("gaba", "novel", username, persona, source=source, nearest=nearest, inhibition=inh)
    return get_state(username, persona)
"""),
])

# ---------------- plugins/memory_plugin.py ----------------
patch("plugins/memory_plugin.py", [
("""import os as _os
import reflect_gate
""", """import os as _os
import reflect_gate
""" + IMPORT),
("""        _near = plan["nearest"]
        _near_s = "none" if _near is None else f"{_near:.3f}"
        if plan["verdict"] == "wait":
            return
        if plan["verdict"] == "shrug":
""", """        _near = plan["nearest"]
        _near_s = "none" if _near is None else f"{_near:.3f}"
        # Every verdict lands on disk, including "wait": the wait rows are the
        # cadence trace (one per user turn) and cost nothing.
        _emit("reflect", plan["verdict"], self.username, self.persona,
              nearest=_near, new_events=plan["new_events"], threshold=turn_threshold,
              sim_ceil=REFLECT_SIM_CEIL, novelty_gate=REFLECT_NOVELTY_GATE,
              window_rows=len(full), reason=plan["reason"])
        if plan["verdict"] == "wait":
            return
        if plan["verdict"] == "shrug":
"""),
("""                print(f"[DA] social: valence={valence_observed:.2f} expected={_soc.get('expected')} "
                      f"rpe={_soc.get('rpe')} tonic={_soc.get('tonic')} phasic={_soc.get('phasic')}",
                      flush=True)
            except Exception as e:
                print(f"[DA] social_reward failed (non-fatal): {e}")
""", """                print(f"[DA] social: valence={valence_observed:.2f} expected={_soc.get('expected')} "
                      f"rpe={_soc.get('rpe')} tonic={_soc.get('tonic')} phasic={_soc.get('phasic')}",
                      flush=True)
                _emit("da", "social", self.username, self.persona, valence=valence_observed,
                      expected=_soc.get("expected"), rpe=_soc.get("rpe"),
                      tonic=_soc.get("tonic"), phasic=_soc.get("phasic"))
            except Exception as e:
                print(f"[DA] social_reward failed (non-fatal): {e}")
                _emit("da", "social_failed", self.username, self.persona, error=str(e))
"""),
("""        if dense_observation and len(dense_observation.strip()) > 10:
            self.db.add_observation(
                username=self.username,
                persona=self.persona,
                event_type="dense_observation",
                content=dense_observation.strip(),
                reflection_score=1.0
            )
""", """        if dense_observation and len(dense_observation.strip()) > 10:
            self.db.add_observation(
                username=self.username,
                persona=self.persona,
                event_type="dense_observation",
                content=dense_observation.strip(),
                reflection_score=1.0
            )
            _emit("reflect", "stored", self.username, self.persona, nearest=_near,
                  chars=len(dense_observation.strip()), valence=valence_observed)
"""),
("""            return True
        return False

def memory_observer_hook(""", """            return True
        _emit("reflect", "empty", self.username, self.persona, nearest=_near,
              chars=len((dense_observation or "").strip()))
        return False

def memory_observer_hook("""),
])

# ---------------- memory_engine.py ----------------
patch("memory_engine.py", [
("""    import dopamine_state
    _DA_AVAILABLE = True
except ImportError:
    _DA_AVAILABLE = False
""", """    import dopamine_state
    _DA_AVAILABLE = True
except ImportError:
    _DA_AVAILABLE = False
""" + IMPORT),
("""                _near = "none" if max_sim is None else round(max_sim, 3)
                if _nov.get("paid", 0.0) > 0.0:
""", """                _near = "none" if max_sim is None else round(max_sim, 3)
                _emit("da", "novelty", self.username, self.persona, nearest=max_sim,
                      novelty=_nov.get("novelty"), paid=_nov.get("paid", 0.0),
                      predicted=bool(_nov.get("paid", 0.0) <= 0.0), tonic=_nov.get("tonic"),
                      budget_left=_nov.get("budget_left"), memory_id=mem_id)
                if _nov.get("paid", 0.0) > 0.0:
"""),
("""            except Exception as _nov_err:
                print(f"[DA] novelty_reward failed (non-fatal): {_nov_err}", flush=True)
""", """            except Exception as _nov_err:
                print(f"[DA] novelty_reward failed (non-fatal): {_nov_err}", flush=True)
                _emit("da", "novelty_failed", self.username, self.persona, error=str(_nov_err))
"""),
])

# ---------------- llm_engine.py ----------------
patch("llm_engine.py", [
("""    import dopamine_state
except ImportError:
    dopamine_state = None
""", """    import dopamine_state
except ImportError:
    dopamine_state = None
""" + IMPORT),
("""                    print(f"[DA] tool_reward rpe={_da_res['rpe']} tonic={_da_res['tonic']} phasic={_da_res['phasic']} infra_discounted={_da_res.get('infra_discounted', False)}")
                except Exception as _da_ex:
                    print(f"[DA] tool_reward failed (non-fatal): {_da_ex}")
""", """                    print(f"[DA] tool_reward rpe={_da_res['rpe']} tonic={_da_res['tonic']} phasic={_da_res['phasic']} infra_discounted={_da_res.get('infra_discounted', False)}")
                    _emit("da", "tool_reward", kwargs.get('username', 'default'), kwargs.get('persona_key', 'default'),
                          successes=_da_counts["success"], action_failures=_da_counts["action_fail"],
                          infra_failures=_da_counts["infra_fail"], rpe=_da_res.get('rpe'),
                          tonic=_da_res.get('tonic'), phasic=_da_res.get('phasic'),
                          infra_discounted=bool(_da_res.get('infra_discounted', False)))
                except Exception as _da_ex:
                    print(f"[DA] tool_reward failed (non-fatal): {_da_ex}")
                    _emit("da", "tool_reward_failed", kwargs.get('username', 'default'),
                          kwargs.get('persona_key', 'default'), error=str(_da_ex))
"""),
])

# ---------------- stream_worker.py ----------------
patch("stream_worker.py", [
("""    import gaba_state
except ImportError:
    gaba_state = None
""", """    import gaba_state
except ImportError:
    gaba_state = None
""" + IMPORT),
("""            if _exploring and _gaba_inh is not None and gaba_state.is_inhibited(_gaba_inh):
                logger.info(
                    f"[GABA] {persona}: inhibition={_gaba_inh} >= {gaba_state.GATE_INHIBITION_MAX} "
                    f"-- gate held shut (tonic={_da_tonic}).")
                _exploring = False
""", """            _gaba_shut = bool(_gaba_inh is not None and gaba_state is not None
                              and gaba_state.is_inhibited(_gaba_inh))
            if _exploring and _gaba_shut:
                logger.info(
                    f"[GABA] {persona}: inhibition={_gaba_inh} >= {gaba_state.GATE_INHIBITION_MAX} "
                    f"-- gate held shut (tonic={_da_tonic}).")
                _exploring = False
            _emit("daemon", "gate", username, persona, tonic=_da_tonic, inhibition=_gaba_inh,
                  gaba_shut=_gaba_shut, exploring=_exploring)
"""),
("""                        _b = dopamine_state.boost_tonic(username, persona, 0.04)
                        # §6.8: this writer used to print nothing.
                        logger.info(f"[DA] boost: gap discovery +0.04 tonic={_b.get('tonic')}")
""", """                        _b = dopamine_state.boost_tonic(username, persona, 0.04)
                        # §6.8: this writer used to print nothing.
                        logger.info(f"[DA] boost: gap discovery +0.04 tonic={_b.get('tonic')}")
                        _emit("da", "boost", username, persona, source="daemon_gap", amount=0.04,
                              tonic=_b.get("tonic"), node=str(target_node.get("title", ""))[:120])
"""),
])
print("done")
