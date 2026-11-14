import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(1, ROOT)

import llm_engine

test_chunks = [
    'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}',
    'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Evaluating matrix..."}}',
    'data: {"type": "content_block_stop", "index": 0}',
    'data: {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_123", "name": "query_zettel", "input": {}}}',
    'data: {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\\"query\\": \\"memory\\"}"}}',
    'data: {"type": "content_block_stop", "index": 1}',
    'data: {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": ""}}',
    'data: {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "Processing completed."}}',
    'data: {"type": "content_block_stop", "index": 2}',
    'data: {"type": "message_stop"}'
]


class MockSession:
    def post(self, *args, **kwargs):
        class Resp:
            status_code = 200

            def iter_lines(self):
                for c in test_chunks:
                    yield c.encode('utf-8')
        return Resp()


def test_anthropic_stream():
    old_post = llm_engine.OR_SESSION.post
    llm_engine.OR_SESSION.post = MockSession().post
    try:
        stream = llm_engine.call_llm(
            model_id="claude-opus-5.5",
            system_prompt="test",
            messages=[{"role": "user", "content": "hi"}],
            api_keys={"anthropic": "sk-ant-test123456789"},
            provider="anthropic",
            stream=True
        )
        yielded = [y.decode("utf-8").strip() for y in stream.iter_lines()]
        print(f"Successfully yielded {len(yielded)} events")
        for y in yielded:
            print("CHUNK:", y)

        assert len(yielded) == 5, f"Expected 5 SSE events, got {len(yielded)}"
        assert yielded[-1] == "data: [DONE]", f"Expected [DONE] terminator, got {yielded[-1]}"

        payloads = [json.loads(line[6:]) for line in yielded[:-1]]
        assert payloads[0]["choices"][0]["delta"]["thinking"] == "Evaluating matrix..."
        tc0 = payloads[1]["choices"][0]["delta"]["tool_calls"][0]
        assert tc0["id"] == "toolu_123"
        assert tc0["function"]["name"] == "query_zettel"
        tc1 = payloads[2]["choices"][0]["delta"]["tool_calls"][0]
        assert tc1["function"]["arguments"] == '{"query": "memory"}'
        assert payloads[3]["choices"][0]["delta"]["content"] == "Processing completed."
    finally:
        llm_engine.OR_SESSION.post = old_post


if __name__ == "__main__":
    test_anthropic_stream()
    print("\nALL ANTHROPIC STREAM TESTS PASSED!")
