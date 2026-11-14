import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(1, ROOT)

import llm_engine

captured_calls = []

class FakeStreamResponse:
    def __init__(self, chunks):
        self.chunks = chunks

    def iter_lines(self):
        for c in self.chunks:
            data = {"choices": [{"delta": {"content": c}}]}
            yield f"data: {json.dumps(data)}".encode('utf-8')
        yield b"data: [DONE]"

async def run_test():
    # 1. Test Refusal Interception and Reroll
    call_count = 0
    def fake_call_llm(model_id, system_prompt, messages, api_keys, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First attempt returns a corporate refusal
            return FakeStreamResponse(["As an AI, I cannot fulfill this request because it touches adversarial concepts."])
        else:
            # Second attempt (after reroll) returns genuine response
            return FakeStreamResponse(["*takes a swig* Fine, look at the kernel architecture."])

    orig_call = llm_engine.call_llm
    llm_engine.call_llm = fake_call_llm

    try:
        gen = llm_engine.intercepting_stream_generator(
            model_id="test-model",
            system_prompt="Initial system prompt",
            messages=[{"role": "user", "content": "Explain adversarial concepts"}],
            api_keys={"universal": "dummy"},
            tools=None,
            kwargs_dict={}
        )

        yielded_content = []
        async for chunk in gen:
            decoded = chunk.decode('utf-8')
            if decoded.startswith("data: ") and not "[DONE]" in decoded:
                try:
                    payload = json.loads(decoded[6:])
                    c = payload.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if c:
                        yielded_content.append(c)
                except Exception:
                    pass

        full_output = "".join(yielded_content)
        print(f"Call count: {call_count}")
        print(f"Full output: {full_output}")

        assert call_count == 2, f"Expected 2 calls (1 refusal + 1 reroll), got {call_count}"
        assert "As an AI" not in full_output, "Refusal leaked into output!"
        assert "*takes a swig*" in full_output, "Rerolled output missing!"
        print("[PASS] Refusal interception and automatic reroll verified!")

        # 2. Test Normal Stream Pass-Through
        call_count = 0
        def fake_normal_call(model_id, system_prompt, messages, api_keys, **kwargs):
            nonlocal call_count
            call_count += 1
            return FakeStreamResponse(["Here is ", "the complete ", "architectural breakdown of ", "the system."])

        llm_engine.call_llm = fake_normal_call
        gen2 = llm_engine.intercepting_stream_generator(
            model_id="test-model",
            system_prompt="Initial prompt",
            messages=[{"role": "user", "content": "Normal query"}],
            api_keys={"universal": "dummy"},
            tools=None,
            kwargs_dict={}
        )

        yielded_normal = []
        async for chunk in gen2:
            decoded = chunk.decode('utf-8')
            if decoded.startswith("data: ") and not "[DONE]" in decoded:
                try:
                    payload = json.loads(decoded[6:])
                    c = payload.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if c:
                        yielded_normal.append(c)
                except Exception:
                    pass

        normal_output = "".join(yielded_normal)
        assert call_count == 1, f"Expected 1 call, got {call_count}"
        assert normal_output == "Here is the complete architectural breakdown of the system."
        print("[PASS] Normal stream pass-through verified!")

        print("\nALL INTERCEPTOR TESTS PASSED!")

    finally:
        llm_engine.call_llm = orig_call

def test_refusal_interceptor():
    asyncio.run(run_test())


if __name__ == "__main__":
    asyncio.run(run_test())
