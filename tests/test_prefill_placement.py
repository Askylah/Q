"""
Pre-fill placement regression (non-Anthropic providers).

The persona-name pre-fill ("Rick Sanchez: ") must ride as a trailing
assistant-role message, exactly like the Anthropic path does -- except on Gemini,
which gets no pre-fill at all (case 5). It must NEVER be
glued onto the end of the user's message: that makes the model read the persona
name as something the *user* said, which (a) makes Gemini 3 flash answer with a
single whitespace token once real assistant history exists, and (b) makes the
persona complain about being told who it is.

Run:  python tests/test_prefill_placement.py
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.pop("VERTEX_PROJECT_ID", None)   # keep the router off the Vertex/OAuth path
import llm_engine

PRE = "Rick Sanchez: "
captured = {}

class _FakeResp:
    status_code = 200
    text = ""
    def json(self):
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}

def _fake_post(url, headers=None, data=None, **kw):
    captured["payload"] = json.loads(data)
    return _FakeResp()

llm_engine.OR_SESSION.post = _fake_post   # capture instead of sending

def _call(messages, pre_fill):
    captured.clear()
    llm_engine.call_llm(
        model_id="some/openai-compatible-model", system_prompt="SYS", messages=messages,
        api_keys={"universal": "sk-test"}, stream=False, thinking_level="Off",
        custom_base_url="http://127.0.0.1:9/v1/chat/completions", custom_provider_type="openai",
        pre_fill=pre_fill,
    )
    return captured["payload"]["messages"]

results = []
def check(name, cond, detail=""):
    results.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))

hist = [
    {"role": "user", "content": "first question"},
    {"role": "assistant", "content": "*burp* first answer"},
    {"role": "user", "content": "second question"},
]

# 1. Pre-fill present: user turn untouched, trailing assistant carries the pre-fill.
msgs = _call(hist, PRE)
last_user = [m for m in msgs if m["role"] == "user"][-1]
check("user message is not modified by the pre-fill",
      last_user["content"] == "second question", repr(last_user["content"]))
check("payload ends with an assistant-role pre-fill message",
      msgs[-1]["role"] == "assistant" and msgs[-1]["content"] == PRE, repr(msgs[-1]))
check("exactly one message was added",
      len(msgs) == 1 + len(hist) + 1, f"got {len(msgs)}")

# 2. No pre-fill: nothing appended.
msgs = _call(hist, "")
check("no pre-fill -> no trailing assistant message",
      msgs[-1]["role"] == "user" and len(msgs) == 1 + len(hist), repr(msgs[-1]))

# 3. History already ends on an assistant turn (agentic re-entry): don't stack a second one.
msgs = _call(hist + [{"role": "assistant", "content": "partial"}], PRE)
check("does not stack a second assistant message after an assistant turn",
      sum(1 for m in msgs if m["role"] == "assistant") == 2, repr(msgs[-2:]))

# 4. Caller's list is not mutated.
before = json.dumps(hist)
_call(hist, PRE)
check("caller's messages list is not mutated", json.dumps(hist) == before)

# 5. Gemini on any route: no pre-fill at all. gemini-3.7-flash on Vertex answers a trailing
#    model turn with 400 "Requests ending with a model turn are not supported" (2026-09-06),
#    and on gemini-3-flash the pre-fill changed nothing measurable. Identity rides in the system prompt.
def _call_model(messages, pre_fill, model_id):
    captured.clear()
    llm_engine.call_llm(
        model_id=model_id, system_prompt="SYS", messages=messages,
        api_keys={"universal": "sk-test"}, stream=False, thinking_level="Off",
        custom_base_url="http://127.0.0.1:9/v1/chat/completions", custom_provider_type="openai",
        pre_fill=pre_fill,
    )
    return captured["payload"]["messages"]

msgs = _call_model(hist, PRE, "google/gemini-3.7-flash")
check("gemini: no trailing assistant pre-fill message",
      msgs[-1]["role"] == "user" and len(msgs) == 1 + len(hist), repr(msgs[-1]))
check("gemini: user message untouched",
      msgs[-1]["content"] == "second question", repr(msgs[-1]["content"]))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
