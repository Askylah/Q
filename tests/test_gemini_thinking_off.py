"""
Gemini thinking must actually be switched off when thinking_level is Off.

The google / google_vertex routes previously sent no thinking config at all, so
Gemini 3 flash thought anyway (600-4000 reasoning tokens) and in ~37% of turns with
real assistant history emitted only whitespace (15/40 across five prompt shapes,
Vertex, 2026-09-06). A thinking_budget of 0 is what stopped it (0/16). gemini-3.5-flash
accepts it, gemini-3.1-pro ignores it; a model that refuses it is retried at lowest effort. See
lab_notes/gaba_inhibition.md section 6 for the tally.

Both routes share one branch in call_llm. This test drives the plain google route
(no OAuth) with the wire stubbed out; nothing leaves the machine.

Run:  python tests/test_gemini_thinking_off.py
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
import llm_engine
os.environ.pop("VERTEX_PROJECT_ID", None)   # after import: the engine's own load_dotenv would put it back
os.environ.pop("VERTEX_LOCATION", None)

captured = {}
class _FakeResp:
    status_code = 200
    text = ""
    def json(self):
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
def _fake_post(url, headers=None, data=None, **kw):
    captured["url"] = url
    captured["payload"] = json.loads(data)
    return _FakeResp()
llm_engine.OR_SESSION.post = _fake_post

results = []
def check(name, cond, detail=""):
    results.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))

def _call(thinking_level, model_id="google/gemini-3-flash-preview"):
    captured.clear()
    llm_engine.call_llm(model_id=model_id, system_prompt="SYS",
                        messages=[{"role": "user", "content": "hi"}],
                        api_keys={"google": "AIza-test"}, stream=False, thinking_level=thinking_level)
    return captured["payload"]

def _cfg(p):
    return ((p.get("extra_body") or {}).get("google") or {}).get("thinking_config") or {}

p = _call("Off")
check("routed to the plain google route", "generativelanguage" in captured.get("url", ""), captured.get("url"))
check("Off: thinking_budget 0 sent", _cfg(p).get("thinking_budget") == 0, repr(p.get("extra_body")))
check("Off: no reasoning_effort", "reasoning_effort" not in p, repr(p.get("reasoning_effort")))

p = _call("high")
check("high: reasoning_effort high", p.get("reasoning_effort") == "high", repr(p.get("reasoning_effort")))
check("high: no thinking_budget", "extra_body" not in p, repr(p.get("extra_body")))

# A model that refuses the zero budget (400 naming thinking) is retried once at lowest effort.
_seq = {"n": 0, "payloads": []}
class _Refuse:
    status_code = 400
    text = '{"error": {"message": "Thinking cannot be disabled for this model."}}'
def _refusing_post(url, headers=None, data=None, **kw):
    _seq["n"] += 1
    _seq["payloads"].append(json.loads(data))
    return _Refuse() if _seq["n"] == 1 else _fake_post(url, headers=headers, data=data, **kw)
llm_engine.OR_SESSION.post = _refusing_post
captured.clear()
r = llm_engine.call_llm(model_id="google/gemini-9-flash", system_prompt="SYS",
                        messages=[{"role": "user", "content": "hi"}],
                        api_keys={"google": "AIza-test"}, stream=False, thinking_level="Off")
llm_engine.OR_SESSION.post = _fake_post
check("refusal: exactly two requests were sent", _seq["n"] == 2, repr(_seq["n"]))
check("refusal: first request carried thinking_budget 0", _cfg(_seq["payloads"][0]).get("thinking_budget") == 0, repr(_seq["payloads"][0].get("extra_body")))
check("refusal: retry carried reasoning_effort low and no budget",
      len(_seq["payloads"]) == 2 and _seq["payloads"][1].get("reasoning_effort") == "low" and "extra_body" not in _seq["payloads"][1],
      repr(_seq["payloads"][1:]))
check("refusal: caller got the retried reply, not an API Error", isinstance(r, dict), repr(r)[:80])

p = _call("Off", model_id="google/gemini-2.5-flash-thinking")
check("Off on a reasoning-mandatory name: left alone", "extra_body" not in p and "reasoning_effort" not in p,
      repr((p.get("reasoning_effort"), p.get("extra_body"))))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
