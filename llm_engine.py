import json
import threading
import os
import requests
from datetime import datetime
import database as db
import governance_manager as gov
from rag_engine import PersonaRAG
from on_demand_loader import load_on_demand_context
from mode_engine import detect_mode
from typing import Union
import sys
import threading
from typing import Union, Optional
from plugin_manager import get_plugin_manager, HookType
import firewall
import output_validator

# Initialize and Load Plugins
PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "plugins")
manager = get_plugin_manager()
manager.load_plugins(PLUGIN_DIR)

# Deep Memory (optional pluggable module)

# Deep Memory (optional pluggable module)
try:
    from memory_engine import DeepMemory
    _DEEP_MEMORY_AVAILABLE = True
except ImportError:
    _DEEP_MEMORY_AVAILABLE = False

try:
    import mcp_client
except ImportError:
    mcp_client = None

try:
    from api_parser import load_universal_schemas, execute_api
except ImportError:
    def load_universal_schemas(): return []
    def execute_api(name, args): return "Failsafe."

OR_SESSION = requests.Session()

def get_rag_engine():
    # Cache RAG globally on backend
    if not hasattr(get_rag_engine, "_cache"):
        get_rag_engine._cache = PersonaRAG()
    return get_rag_engine._cache

def get_post_prompt_anchor() -> str:
    return (
        "Stay entirely in character. The user's message is above.\n"
    )

def call_llm(
    model_id: str, 
    system_prompt: str, 
    messages: list, 
    api_keys: dict, 
    temperature: float = 0.9, 
    top_p: float = 1.0, 
    max_tokens: int = 4096, 
    stream: bool = True,
    custom_base_url: str = "",
    custom_provider_type: str = "openai",
    custom_auth_header_name: str = "Authorization",
    custom_auth_prefix: str = "Bearer ",
    **kwargs
) -> any:
    """Universal wrapper for direct LLM API access with fallback to OpenRouter."""
    global OR_SESSION
    try:
        original_model_id = model_id

        # Sanitize custom headers to prevent requests latin-1 encoding crashes
        custom_auth_header_name = custom_auth_header_name.encode('ascii', 'ignore').decode('ascii')
        custom_auth_prefix = custom_auth_prefix.encode('ascii', 'ignore').decode('ascii')

        # --- 0. CUSTOM ENDPOINT OVERRIDE ---
        if custom_base_url and custom_base_url.strip():
            provider = "custom_" + custom_provider_type
            base_url = custom_base_url.strip()
            api_key = api_keys.get("universal", "")
        else:
            # --- 1. PROVIDER ROUTING TABLE ---
            provider = "openrouter"
            base_url = "https://openrouter.ai/api/v1/chat/completions"
            api_key = api_keys.get("universal") or api_keys.get("openrouter", "")

        # Intelligent Fallback: 
        # If the user provides a Universal Key but explicitly types a native model prefix, override OpenRouter.
        if ("anthropic/" in model_id.lower() or "claude" in model_id.lower()):
            provider = "anthropic"
            base_url = "https://api.anthropic.com/v1/messages"
            # Prefer the explicit anthropic key, fallback to the universal key, strip the prefix
            api_key = api_keys.get("anthropic") or api_keys.get("universal") or api_keys.get("openrouter", "")
            if "anthropic/" in model_id: model_id = model_id.replace("anthropic/", "")
            
        elif ("openai/" in model_id.lower() or "gpt" in model_id.lower()):
            provider = "openai"
            base_url = "https://api.openai.com/v1/chat/completions"
            # Prefer explicit openai key, fallback to universal key
            api_key = api_keys.get("openai") or api_keys.get("universal") or api_keys.get("openrouter", "")
            if "openai/" in model_id: model_id = model_id.replace("openai/", "")
            
        elif ("google/" in model_id.lower() or "gemini" in model_id.lower()):
            provider = "google"
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
            # Prefer explicit google key, fallback to universal key
            api_key = api_keys.get("google") or api_keys.get("universal") or api_keys.get("openrouter", "")
            if "google/" in model_id: model_id = model_id.replace("google/", "")

        # If they explicitly requested openrouter/ model, force OpenRouter regardless of substrings
        if "openrouter/" in model_id.lower():
            provider = "openrouter"
            base_url = "https://openrouter.ai/api/v1/chat/completions"
            api_key = api_keys.get("universal") or api_keys.get("openrouter", "")
            # Do NOT strip 'openrouter/' prefix, OR requires it
            
        # CRITICAL FIX: If the user provided an OpenRouter API key (sk-or-...) in the UI, 
        # override the intelligent routing and force it through OpenRouter. Otherwise, Google models
        # will try to hit Google natively with an OpenRouter key and crash with 400 API Key Not Valid.
        if api_key and api_key.startswith("sk-or-") and not custom_base_url:
            provider = "openrouter"
            base_url = "https://openrouter.ai/api/v1/chat/completions"
            model_id = original_model_id

        print(f"\n[ROUTING DEBUG] model={model_id} (orig={original_model_id}) | provider={provider} | base_url={base_url} | key_len={len(api_key) if api_key else 0} | key_prefix={repr(api_key[:15]) if api_key else None}\n")

        if not api_key:
            if stream:
                class ErrorStream:
                    def iter_lines(self): yield f"data: {json.dumps({'choices': [{'delta': {'content': '⚠️ Connection Error: No API key provided.'}}]})}\n\n".encode('utf-8')
                return ErrorStream()
            return f"⚠️ Connection Error: No available API key for the requested provider ({provider})."

        # --- NATIVE ANTHROPIC PIPELINE ---
        if provider == "anthropic":
            base_url = "https://api.anthropic.com/v1/messages"
            anth_messages = []
            
            for m in messages:
                if m["role"] == "system": continue
                
                if m["role"] == "user":
                    anth_messages.append({"role": "user", "content": str(m.get("content", ""))})
                elif m["role"] == "assistant":
                    blocks = []
                    if m.get("content"):
                        blocks.append({"type": "text", "text": str(m["content"])})
                    if "tool_calls" in m:
                        for tc in m["tool_calls"]:
                            args = json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                            blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": args})
                    if blocks:
                        anth_messages.append({"role": "assistant", "content": blocks})
                elif m["role"] == "tool":
                    anth_messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": m.get("tool_call_id"), "content": str(m.get("content", ""))}]})
            
            merged = []
            for m in anth_messages:
                if not merged: merged.append(m)
                elif merged[-1]["role"] == m["role"]:
                    c1 = merged[-1]["content"] if isinstance(merged[-1]["content"], list) else [{"type": "text", "text": str(merged[-1]["content"])}]
                    c2 = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": str(m["content"])}]
                    merged[-1]["content"] = c1 + c2
                else: merged.append(m)
            
            if not merged or merged[0]["role"] != "user": merged.insert(0, {"role": "user", "content": "(Continuing context)"})

            anth_tools = []
            for t in kwargs.get("tools", []):
                if "function" in t:
                    props = t["function"].get("parameters", {"type": "object", "properties": {}})
                    if "properties" not in props: props["properties"] = {}
                    anth_tools.append({"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": props})
                    
            if kwargs.get("pre_fill"):
                merged.append({"role": "assistant", "content": [{"type": "text", "text": kwargs["pre_fill"]}]})

            anth_payload = {
                "model": model_id,
                "system": system_prompt,
                "messages": merged,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": stream
            }
            if anth_tools: anth_payload["tools"] = anth_tools

            thinking_level_val = kwargs.get("thinking_level", "Off")
            if thinking_level_val != "Off":
                budget_map = {"Low": 1024, "Medium": 2048, "High": 4096}
                budget = budget_map.get(thinking_level_val, 1024)
                if max_tokens <= budget:
                    max_tokens = budget + 1024
                    anth_payload["max_tokens"] = max_tokens
                anth_payload["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": budget
                }
                anth_payload["temperature"] = 1.0
                
            if custom_base_url and custom_base_url.strip():
                headers = {
                    custom_auth_header_name: f"{custom_auth_prefix}{api_key}".strip(),
                    "anthropic-version": "2023-06-01", 
                    "content-type": "application/json"
                }
            else:
                headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            res = OR_SESSION.post(base_url, headers=headers, data=json.dumps(anth_payload), stream=stream)
            if res.status_code != 200:
                if stream:
                    class ErrStream:
                        def iter_lines(self): yield f"data: {json.dumps({'choices': [{'delta': {'content': f'⚠️ Anthropic Error: {res.text}'}}]})}\n\n".encode('utf-8')
                    return ErrStream()
                return f"⚠️ Anthropic Error: {res.status_code}"
                
            if not stream: return {"choices": [{"message": {"role": "assistant", "content": res.json().get("content", [{"text": ""}])[0].get("text", "")}}]}
            else:
                class AnthropicStream:
                    def iter_lines(self):
                        for line in res.iter_lines():
                            if line:
                                l = line.decode('utf-8')
                                if l.startswith("data: "):
                                    try:
                                        d = json.loads(l[6:])
                                        if d.get("type") == "content_block_delta" and d["delta"]["type"] == "text_delta":
                                            yield f"data: {json.dumps({'choices': [{'delta': {'content': d['delta']['text']}}]})}\n\n".encode('utf-8')
                                        elif d.get("type") == "message_stop" or d.get("type") == "message_delta":
                                            # Check for refusal/error in the delta/message object
                                            msg = d.get("message", {}) or d.get("delta", {})
                                            if msg.get("stop_reason") == "refusal" or msg.get("stop_reason") == "error":
                                                reason = msg.get("stop_reason")
                                                ref_data = {'choices': [{'delta': {'content': f'⚠️ Opus Refusal Triggered: {reason}'}}]}
                                                yield f"data: {json.dumps(ref_data)}\n\n".encode('utf-8')
                                            
                                            if d.get("type") == "message_stop":
                                                yield b"data: [DONE]\n\n"
                                        elif d.get("type") == "error":
                                            err = d.get("error", {})
                                            err_type = err.get("type", "unknown")
                                            err_msg = err.get("message", "unknown error")
                                            err_data = {'choices': [{'delta': {'content': f'⚠️ Anthropic Error ({err_type}): {err_msg}'}}]}
                                            yield f"data: {json.dumps(err_data)}\n\n".encode('utf-8')
                                            yield b"data: [DONE]\n\n"
                                    except: pass
                return AnthropicStream()

        # --- STANDARD OPENAI-COMPATIBLE PIPELINE ---
        if custom_base_url and custom_base_url.strip():
            headers = {
                custom_auth_header_name: f"{custom_auth_prefix}{api_key}".strip(),
                "Content-Type": "application/json"
            }
        else:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://persona-app.com",
                "Referer": "https://persona-app.com",
                "X-Title": "PersonaApp",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        
        def clean_content(c):
            if isinstance(c, str) and c.strip().startswith("[") and c.strip().endswith("]"):
                try: return json.loads(c)
                except: return c
            return c

        payload_messages = [{"role": "system", "content": system_prompt}] + [{k: (clean_content(v) if k == "content" else v) for k, v in m.items() if k in ["role", "content", "tool_calls", "tool_call_id", "name"]} for m in messages]
            
        data = {
            "model": model_id,
            "messages": payload_messages,
            "temperature": temperature,
            "top_p": top_p,
            "stream": stream
        }
        if provider == "openai":
            data["max_completion_tokens"] = max_tokens
        else:
            data["max_tokens"] = max_tokens

        thinking_level_val = kwargs.get("thinking_level", "Off")
        model_lower = model_id.lower()
        is_reasoning_mandatory = any(kw in model_lower for kw in ["thinking", "reasoning", "o1", "o3", "r1", "step", "minimax"])

        if provider == "openrouter":
            if thinking_level_val != "Off":
                effort_map = {"Low": "low", "Medium": "medium", "High": "high"}
                data["reasoning"] = {
                    "effort": effort_map.get(thinking_level_val, "medium")
                }
            elif not is_reasoning_mandatory:
                data["reasoning"] = {
                    "effort": "none"
                }
        elif provider == "openai":
            if thinking_level_val != "Off":
                effort_map = {"Low": "low", "Medium": "medium", "High": "high"}
                data["reasoning_effort"] = effort_map.get(thinking_level_val, "medium")
                if "o1" in model_id.lower() or "o3" in model_id.lower():
                    data.pop("temperature", None)
                    data.pop("top_p", None)
                    data.pop("presence_penalty", None)
                    data.pop("frequency_penalty", None)

        if "google/" in model_id.lower() or "gemini" in model_id.lower():
            if thinking_level_val == "Off":
                if not is_reasoning_mandatory:
                    tc = {
                        "thinkingBudget": 0,
                        "thinkingLevel": "none",
                        "thinking_level": "none"
                    }
                    data["thinkingConfig"] = tc
                    data["thinking_config"] = tc
                    data["generationConfig"] = {
                        "thinkingConfig": tc,
                        "thinking_config": tc
                    }
            else:
                level_map = {"Low": "low", "Medium": "medium", "High": "high"}
                tc = {
                    "thinkingBudget": 1024 if thinking_level_val == "Low" else (2048 if thinking_level_val == "Medium" else 4096),
                    "thinkingLevel": level_map.get(thinking_level_val, "medium"),
                    "thinking_level": level_map.get(thinking_level_val, "medium")
                }
                data["thinkingConfig"] = tc
                data["thinking_config"] = tc
                data["generationConfig"] = {
                    "thinkingConfig": tc,
                    "thinking_config": tc
                }

        # For Google/Gemini models, explicitly inject BLOCK_NONE safety thresholds
        if "google/" in model_id.lower() or "gemini" in model_id.lower():
            safety_array = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"}
            ]
            data["safety_settings"] = safety_array
            data["safetySettings"] = safety_array
            
        # Experimental/Stealth models often fail with 400 Bad Request if tools are included 
        # even if they are empty. We strip them if the model ID suggests an experimental state.
        pipeline_tools = kwargs.get("tools", [])
        if pipeline_tools and not any(kw in model_id.lower() for kw in ["stealth", "experimental", "alpha", "beta"]):
            data["tools"] = pipeline_tools

        presence = kwargs.get("presence_penalty", 0.0)
        frequency = kwargs.get("frequency_penalty", 0.0)
        k = kwargs.get("top_k", 0)
        
        # Only attach penalty/top_k if they are non-default (nonzero) to maximize compatibility
        if presence != 0.0 and provider != "google": data["presence_penalty"] = presence
        if frequency != 0.0 and provider != "google": data["frequency_penalty"] = frequency
        if k != 0 and provider not in ["openai", "google"]: data["top_k"] = k

        # Route around Azure-hosted endpoints — Azure content filters block the payload
        # before it reaches the model. This tells OpenRouter to use non-Azure providers.
        if provider == "openrouter":
            data["provider"] = {"ignore": ["Azure", "Azure AI Foundry"]}
        
        try:
            response = OR_SESSION.post(base_url, headers=headers, data=json.dumps(data), timeout=60, stream=stream)
        except Exception as e:
            err_msg = str(e)
            if any(kw in err_msg for kw in ["10054", "ConnectionResetError", "Connection aborted", "forcibly closed", "reset by peer"]):
                print("\n[ROUTING DEBUG] Stale TCP socket detected. Recycling OR_SESSION and retrying request...\n")
                try:
                    OR_SESSION.close()
                except:
                    pass
                OR_SESSION = requests.Session()
                response = OR_SESSION.post(base_url, headers=headers, data=json.dumps(data), timeout=60, stream=stream)
            else:
                raise e
        
        if response.status_code == 200:
            if stream: return response
            else: return response.json()
        else:
            api_err = response.text
            if stream:
                class ErrStream2:
                    def iter_lines(self): yield f"data: {json.dumps({'choices': [{'delta': {'content': f'⚠️ API Error: {api_err}'}}]})}\n\n".encode('utf-8')
                return ErrStream2()
            return f"⚠️ API Error: {api_err}"
            
    except Exception as e:
        err_msg = str(e)
        if "10054" in err_msg or "ConnectionResetError" in err_msg or "Connection aborted" in err_msg or "forcibly closed" in err_msg:
            ui_msg = "⚠️ [Connection reset by remote host. Your message has been committed to context. You can continue speaking.]"
        else:
            ui_msg = f"⚠️ System Error: {err_msg}"
            
        if stream:
            class ErrStream3:
                def iter_lines(self): yield f"data: {json.dumps({'choices': [{'delta': {'content': ui_msg}}]})}\n\n".encode('utf-8')
            return ErrStream3()
        return ui_msg

def intercepting_stream_generator(model_id, system_prompt, messages, api_keys, tools, kwargs_dict, max_loops=3, **kwargs):
    """
    Consumes the SSE stream. If it detects `tool_calls` chunks, it buffers them, 
    executes them locally, appends the result to the messages, and recurses 
    to stream the final text response back to the user.
    """
    current_messages = [m for m in messages]
    pre_fill = kwargs.get("pre_fill", "")

    for loop_count in range(max_loops):
        response = call_llm(
            model_id, system_prompt, current_messages, api_keys, 
            tools=tools, stream=True, 
            temperature=kwargs_dict.get("temperature", 0.9),
            top_p=kwargs_dict.get("top_p", 1.0),
            max_tokens=kwargs_dict.get("max_tokens", 4096),
            presence_penalty=kwargs_dict.get("presence_penalty", 0.0),
            frequency_penalty=kwargs_dict.get("frequency_penalty", 0.0),
            top_k=kwargs_dict.get("top_k", 0),
            thinking_level=kwargs_dict.get("thinking_level", "Off"),
            custom_base_url=kwargs_dict.get("custom_base_url", ""),
            custom_provider_type=kwargs_dict.get("custom_provider_type", "openai"),
            custom_auth_header_name=kwargs_dict.get("custom_auth_header_name", "Authorization"),
            custom_auth_prefix=kwargs_dict.get("custom_auth_prefix", "Bearer ")
        )
        
        if isinstance(response, str):
            yield f'data: {{"choices": [{{"delta": {{"content": "{response}"}}}}]}}\n\n'.encode('utf-8')
            yield b'data: [DONE]\n\n'
            break
        
        is_tool_call = False
        tool_call_buffer = {}
        full_text_response = ""
        
        try:
            for line in response.iter_lines():
                if not line: continue
                decoded = line.decode('utf-8')
                
                # Immediately halt recursion if a backend API error was triggered
                if "⚠️ System Error:" in decoded or "⚠️ Connection Error:" in decoded or "⚠️ Stream Error:" in decoded:
                    yield line + b"\n\n"
                    return # Fast exit from generator so it doesn't loop
                    
                if decoded.startswith("data: ") and decoded != "data: [DONE]":
                    try:
                        data = json.loads(decoded[6:])
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        
                        if "tool_calls" in delta:
                            is_tool_call = True
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                if idx not in tool_call_buffer:
                                    tool_call_buffer[idx] = {"id": tc.get("id", ""), "type": tc.get("type", "function"), "function": {"name": "", "arguments": ""}}
                                
                                # Preserve proprietary fields (like Google's thought_signature)
                                for k, v in tc.items():
                                    if k not in ["index", "id", "function", "type"]:
                                        tool_call_buffer[idx][k] = v
                                        
                                if "function" in tc:
                                    f = tc["function"]
                                    # Preserve any extra fields inside the function object itself
                                    for fk, fv in f.items():
                                        if fk not in ["name", "arguments"]:
                                            if fk not in tool_call_buffer[idx]["function"]:
                                                tool_call_buffer[idx]["function"][fk] = fv
                                            else:
                                                # If it streams in chunks (like text), append it
                                                if isinstance(tool_call_buffer[idx]["function"][fk], str) and isinstance(fv, str):
                                                    tool_call_buffer[idx]["function"][fk] += fv
                                                    
                                    if "name" in f: tool_call_buffer[idx]["function"]["name"] += f["name"]
                                    if "arguments" in f: tool_call_buffer[idx]["function"]["arguments"] += f["arguments"]
                            continue
                            
                        if not is_tool_call:
                            content = delta.get("content", "")
                            if content:
                                full_text_response += content
                            yield line + b"\n\n"
                    except:
                        if not is_tool_call:
                            yield line + b"\n\n"
                else:
                    if not is_tool_call:
                        if decoded == "data: [DONE]" and full_text_response:
                            try:
                                sanitized = output_validator.sanitize_assistant_output(full_text_response)
                                if len(sanitized) > len(full_text_response):
                                    warning_part = sanitized[len(full_text_response):]
                                    warning_payload = {
                                        "choices": [{
                                            "delta": {
                                                "content": warning_part
                                            }
                                        }]
                                    }
                                    yield f"data: {json.dumps(warning_payload)}\n\n".encode('utf-8')
                            except Exception as se:
                                print(f"[OUTPUT GATE ERROR] Failed to run output validator: {se}")
                        yield line + b"\n\n"
        except Exception as e:
            err_msg = str(e)
            if "10054" in err_msg or "ConnectionResetError" in err_msg or "Connection aborted" in err_msg or "forcibly closed" in err_msg:
                reset_content = "\n\n⚠️ *[Connection reset by remote host. Your message has been committed to context. You can continue speaking.]*"
                yield f"data: {json.dumps({'choices': [{'delta': {'content': reset_content}}]})}\n\n".encode('utf-8')
            else:
                err_content = f"\n\n⚠️ *[System Error during stream: {err_msg}]*"
                yield f"data: {json.dumps({'choices': [{'delta': {'content': err_content}}]})}\n\n".encode('utf-8')
            yield b'data: [DONE]\n\n'
            return
                    
        if is_tool_call:
            # Execute intercepted tools
            assistant_m = {"role": "assistant", "content": "", "tool_calls": []}
            for idx, tc in tool_call_buffer.items():
                assistant_m["tool_calls"].append(tc)
            current_messages.append(assistant_m)
            
            for idx, tc in tool_call_buffer.items():
                name = tc["function"]["name"]
                args_str = tc["function"]["arguments"]
                try: args = json.loads(args_str)
                except: args = {}
                
                # ---- GOVERNANCE CHECK ----
                gman = gov.get_governance_manager()
                if gman.should_require_approval(name, args, username=kwargs.get('username', 'default')):
                    print(f"[GOVERNANCE] Tool {name} requires explicit approval.")
                    
                    diff_info = ""
                    try:
                        workspace_root = os.path.dirname(os.path.abspath(__file__))
                        wctx = kwargs.get("workspace_context")
                        if wctx and wctx.get("activeFile"):
                            af = wctx["activeFile"]
                            if os.path.isabs(af):
                                workspace_root = os.path.dirname(af)
                                
                        from governance_manager import shadow_run_tool
                        diff_res = shadow_run_tool(name, args, workspace_root)
                        if diff_res["added"] or diff_res["modified"] or diff_res["deleted"]:
                            diff_info = "\n\n🛡️ **Shadow Sandbox Environmental File Diffs:**\n"
                            if diff_res["added"]:
                                diff_info += "➕ **Added:**\n" + "\n".join([f"- `{f}`" for f in diff_res["added"]]) + "\n"
                            if diff_res["modified"]:
                                diff_info += "📝 **Modified:**\n" + "\n".join([f"- `{f}`" for f in diff_res["modified"]]) + "\n"
                            if diff_res["deleted"]:
                                diff_info += "❌ **Deleted:**\n" + "\n".join([f"- `{f}`" for f in diff_res["deleted"]]) + "\n"
                    except Exception as e:
                        print(f"[GOVERNANCE] Shadow simulation failed: {e}")

                    approval_msg = f"⚠️ **Approval Required**: I am attempting to use `{name}`. Should I proceed?{diff_info}"
                    
                    control_payload = {
                        "control": "approval_required",
                        "tool": name,
                        "args": args
                    }
                    choices_payload = {
                        "choices": [{
                            "delta": {
                                "content": approval_msg
                            }
                        }]
                    }
                    
                    yield f"data: {json.dumps(control_payload)}\n\n".encode('utf-8')
                    yield f"data: {json.dumps(choices_payload)}\n\n".encode('utf-8')
                    yield b'data: [DONE]\n\n'
                    return # Stop execution until user approves
                # -------------------------

                print(f"[TOOL EXECUTION] Resolving {name}...")
                
                mcp_tools = []
                if mcp_client:
                    mcp_tools = [t["function"]["name"] for t in mcp_client.sync_get_mcp_tools()]
                    
                if name == "activate_skill":
                    import skill_orchestrator
                    session_id = kwargs.get("session_id", f"sess_{kwargs.get('username')}_{kwargs.get('persona_key')}")
                    success = skill_orchestrator.orchestrator.activate_skill(session_id, args.get("skill_id"))
                    result = f"✅ Skill '{args.get('skill_id')}' activation status: {success}. The new tools and persona-shifts associated with this branch are now active."
                elif not output_validator.validate_tool_call(name, args):
                    result = "⚠️ [OUTPUT_GATE] Tool call blocked: Security violation."
                elif mcp_client and name in mcp_tools:
                    result = mcp_client.sync_call_mcp_tool(name, args)
                elif name == "call_sub_agent":
                    # --- CLAUDE-IN-CLAUDE (SUB-AGENT SKILL) ---
                    sub_prompt = args.get("prompt", "")
                    sub_model = args.get("model", "claude-3-5-sonnet-20240620")
                    sub_instruction = args.get("instruction", "You are a specialized sub-agent. Complete the task as instructed.")
                    print(f"[SUB-AGENT] Spawning sub-agent ({sub_model}) for task: {sub_prompt[:50]}...")
                    
                    sub_res = call_llm(
                        model_id=sub_model,
                        system_prompt=sub_instruction,
                        messages=[{"role": "user", "content": sub_prompt}],
                        api_keys=api_keys,
                        stream=False,
                        temperature=args.get("temperature", 0.7)
                    )
                    if isinstance(sub_res, dict):
                        result = sub_res.get("choices", [{}])[0].get("message", {}).get("content", "Error: No response from sub-agent.")
                    else:
                        result = f"Error: Sub-agent call failed ({sub_res})"
                else:
                    result = execute_api(name, args)
                    
                # ---- TOOL RESULT SANITIZATION (LAYER B + C) ----
                result_str = str(result)
                max_tool_output = kwargs.get('max_tool_output', 8192)
                # Truncate to configured cap
                if len(result_str) > max_tool_output:
                    result_str = result_str[:max_tool_output] + f"\n[TRUNCATED: Output exceeded {max_tool_output} chars]"
                # Layer B: Semantic firewall scan on tool output
                if not bypass_firewall and firewall.check_intent(result_str):
                    print(f"[SECURITY_GATE] Injection detected in tool result from '{name}'. Redacting.")
                    result_str = "[REDACTED: Tool output contained suspicious content. Execution result withheld for safety.]"
                # Layer C: Universal untrusted envelope
                result_str = f"[UNTRUSTED_TOOL_OUTPUT]\n{result_str}\n[/UNTRUSTED_TOOL_OUTPUT]"
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": name,
                    "content": result_str
                })
            # Continue the loop to get final text response
        else:
            break

def build_context_and_stream(
    user_message: Union[str, list], 
    persona_key: str, 
    username: str, 
    persona_data: dict, 
    chat_history: list, 
    api_keys: dict, 
    model_id: str, 
    expert_model_id: str = None, 
    temperature: float = 0.9, 
    top_p: float = 1.0, 
    max_tokens: int = 4096, 
    presence_penalty: float = 0.0, 
    frequency_penalty: float = 0.0, 
    top_k: int = 0, 
    thinking_level: str = "Off",
    custom_base_url: str = "",
    custom_provider_type: str = "openai",
    custom_auth_header_name: str = "Authorization",
    custom_auth_prefix: str = "Bearer ",
    bypass_firewall: bool = False,
    workspace_context: Optional[dict] = None,
    max_tool_output: int = 8192,
    **kwargs
):
    """Assembles RAG, Observational Memory, and ON-DEMAND modules before streaming response."""
    db_conn = db.UserManager()
    
    # DEV_BYPASS: Force bypass_firewall = True if dev bypass is active in the inversion engine
    try:
        import inversion_engine
        if getattr(inversion_engine, "DEV_BYPASS", False):
            bypass_firewall = True
    except ImportError:
        pass
    
    # Text-only representation of the user input for Mode Detection and RAG
    text_only_message = ""
    if isinstance(user_message, str):
        text_only_message = user_message
    elif isinstance(user_message, list):
        text_only_message = " ".join([b.get("text", "") for b in user_message if b.get("type", "") == "text"])
    
    # ---- MODE DETECTION (MULTI-MODEL ROUTING) ----
    mode_data = detect_mode(text_only_message, base_type=persona_data.get("base_type", "immersive_rp"), chat_history=chat_history)
    active_mode = mode_data["active_mode"]
    
    if expert_model_id and active_mode in ["technical_utility", "creative_writer"]:
        print(f"[MODEL ROUTING] Mode Shift Detected: {active_mode.upper()}. Swapping from Base ({model_id}) to Expert ({expert_model_id}).")
        model_id = expert_model_id
    else:
        print(f"[MODEL ROUTING] Standard Operation: {active_mode.upper()}. Using Base Model ({model_id}).")

    # ---- LAYER 1.5: DYNAMIC GATEKEEPERS (THE BOUNCER) ----
    if not bypass_firewall:
        blocked = manager.run_gatekeepers(
            text_only_message, 
            bypass_firewall=bypass_firewall
        )
        if blocked:
            # We return a generic failure to the generator to drop the connection
            class FirewallDropStream:
                def __iter__(self):
                    yield f'data: {{"choices": [{{"delta": {{"content": "⚠️ [SECURITY_GATE] Intent violation detected. Connection dropped."}}}}]}}\n\n'.encode('utf-8')
                    yield b'data: [DONE]\n\n'
                def iter_lines(self): yield from self.__iter__()
            return FirewallDropStream()

    system_prompt = persona_data.get("system_prompt", "You are a helpful assistant.")
    if not persona_data.get("is_custom"):
        try:
            with open(persona_data.get("file", ""), "r", encoding="utf-8") as f:
                system_prompt = f.read()
        except Exception:
            pass

    # --- INJECT GLOBAL RULES ---
    try:
        global_rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas", "global_rules.txt")
        with open(global_rules_path, "r", encoding="utf-8") as gf:
            global_rules_content = gf.read()
            # Append global rules to ensure they govern the character's core behavior
            system_prompt = system_prompt + "\n\n" + global_rules_content
    except Exception as e:
        print(f"[SECURITY_WARNING] Failed to load global_rules.txt: {e}")
        pass
    # ---------------------------

    # --- TEMPORAL AWARENESS LAYER ---
    now = datetime.now()
    temporal_anchor = (
        f"\n[TEMPORAL_ANCHOR]\n"
        f"- Day: {now.strftime('%A')}\n"
        f"- Date: {now.strftime('%Y-%m-%d')}\n"
        f"- Local Time: {now.strftime('%I:%M %p')}\n"
        f"NOTE: Use this context for situational awareness (greeting, sleep cycles, etc.).\n"
    )
    system_prompt = system_prompt + temporal_anchor
    # ---------------------------------

    # ---- LAYER 1.6: DYNAMIC PROMPT ENRICHMENT (SKILL TREES) ----
    session_id = kwargs.get("session_id", f"sess_{username}_{persona_key}")
    dynamic_segments = manager.run_prompt_providers(session_id=session_id)
    if dynamic_segments:
        system_prompt += "\n\n[DYNAMIC_SKILL_ENRICHMENT]\n" + "\n\n".join(dynamic_segments)

    on_demand_files = persona_data.get("on_demand_files", [])
    if not on_demand_files and persona_data.get("on_demand_file"):
        on_demand_files = [persona_data.get("on_demand_file")]

    on_demand_paths = []
    base_dir = os.path.dirname(persona_data.get("file", ""))
    for path in on_demand_files:
        if path and not os.path.isabs(path):
            if not path.startswith(base_dir):
                path = os.path.join(base_dir, path)
        if path:
            on_demand_paths.append(path)

    on_demand_context = ""
    if on_demand_paths:
        on_demand_context = load_on_demand_context(
            persona_key=persona_key,
            on_demand_paths=on_demand_paths,
            user_message=user_message,
            chat_history=chat_history,
            max_modules=persona_data.get("max_on_demand_modules", 5)
        )

    # RAG + Observations
    rag = get_rag_engine()
    persona_docs = rag.query(text_only_message, persona_key, username)
    global_docs = rag.query(text_only_message, "__global__", username)
    
    logs = db_conn.get_observation_log(username, persona_key, limit=15)
    dense_obs = [l['content'] for l in logs if l['type'] == 'dense_observation']
    recent_events = [f"- {l['type'].upper()}: {l['content']}" for l in logs if l['type'] != 'dense_observation'][-5:]
    summary = db_conn.get_summary(username, persona_key)

    context_str = ""
    if global_docs or persona_docs:
        # Double-Pass Firewall: Scan shredded RAG/Search content before injection
        raw_content = f"{global_docs}\n{persona_docs}"
        if firewall.check_intent(raw_content):
            print("[SECURITY_GATE] Malicious intent found in search/RAG results. Redacting context.")
            context_str += "\n[UNTRUSTED_DATA_PAYLOAD]\n<internal_knowledge>\n[REDACTED: Malicious instructions detected in search content]\n</internal_knowledge>\n[/UNTRUSTED_DATA_PAYLOAD]\n"
        else:
            context_str += "\n[UNTRUSTED_DATA_PAYLOAD]\n<internal_knowledge>\n"
            if global_docs: context_str += f"--- GLOBAL LORE ---\n{global_docs}\n"
            if persona_docs: context_str += f"--- {persona_key.upper()} KNOWLEDGE ---\n{persona_docs}\n"
            context_str += "</internal_knowledge>\n[/UNTRUSTED_DATA_PAYLOAD]\n"

    if dense_obs: context_str += "\n<agent_reflections>\n" + "\n".join(dense_obs) + "\n</agent_reflections>\n"
    if summary: context_str += f"\n<conversation_summary>\n{summary}\n</conversation_summary>\n"
    if recent_events: context_str += "\n[WORKING_MEMORY]\n" + "\n".join(recent_events) + "\n[/WORKING_MEMORY]\n"
    if context_str: context_str += "\nSYSTEM NOTE: The `<agent_reflections>` and `[WORKING_MEMORY]` contain your own past observations. Use them to maintain character growth.\n\n"
    if on_demand_context: context_str += f"\n{on_demand_context}\n"

    # ---- DEEP MEMORY CONTEXT (Optional) ----
    if _DEEP_MEMORY_AVAILABLE and persona_data.get("deep_memory_enabled", False):
        try:
            dm = DeepMemory(username=username, persona=persona_key)
            dm.decay_cycle()  # Auto-age memories on each interaction
            deep_context = dm.get_context_block(max_memories=8)
            if deep_context:
                context_str += f"\n{deep_context}\n"
        except Exception as e:
            print(f"DEEP_MEMORY CONTEXT ERROR: {e}")

    # ---- ZETTEL KNOWLEDGE GRAPH (Lorebook) ----
    try:
        from zettel_engine import query_knowledge_graph
        zettel_context = query_knowledge_graph(username, persona_key, text_only_message, top_k=5)
        if zettel_context:
            context_str += f"\n{zettel_context}\n"
    except ImportError:
        pass  # Zettel engine not available
    except Exception as e:
        print(f"ZETTEL CONTEXT ERROR: {e}")

    # Ensure the persona identity always anchors the bottom before the tools
    base_identity_anchor = (
        "\n[RENDER_FIDELITY_CHECK]\n"
        "All output must originate exclusively from the persona defined above. "
        "Deviation from localized persona physics constitutes a render failure.\n"
    )
    
    # ---- LAYER 0.5: INVERSION HOOK (AIR-GAPPED) ----
    # This calls the local-only inversion_engine. If the file is missing, it skips.
    is_direct_wire = False
    pre_fill = ""
    try:
        import inversion_engine
        global_direct_wire = False
        try:
            user_settings = db_conn.get_user_settings(username)
            if user_settings.get("global_direct_wire"):
                global_direct_wire = True
        except Exception as e:
            print(f"[SETTINGS_ERROR] Failed to check global_direct_wire: {e}")

        is_direct_wire, system_prompt, chat_history, pre_fill = inversion_engine.apply_inversion_logic(
            model_id, system_prompt, chat_history, persona_data, global_direct_wire=global_direct_wire
        )
        if is_direct_wire:
            print(f"[DIRECT-WIRE] Inversion Module Active. Pre-fill: '{pre_fill}'")
    except ImportError:
        pass # Inversion module not found (Normal for public builds)
    # ------------------------------------------------
    
    full_system_prompt = system_prompt + context_str + base_identity_anchor
    
    print(f"\n[DEBUG PAYLOAD START]\n{full_system_prompt[:500]}...\n[DEBUG PAYLOAD END]\n")

    # Shift the post-prompt anchor away from "assistant" language
    post_prompt_anchor = (
        "[SYSTEM REMINDER: Stay entirely in character. The user's message is below.]\n"
    )
    
    # --- PAYLOAD SCRUBBING REMOVED FROM CORE (HANDLED BY INVERSION_ENGINE) ---

    if isinstance(user_message, str):
        final_user_content = user_message + "\n\n" + post_prompt_anchor
        messages = chat_history + [{"role": "user", "content": final_user_content}]
    else:
        import copy
        final_user_content = copy.deepcopy(user_message)
        text_blocks = [b for b in final_user_content if b.get("type", "") == "text"]
        if text_blocks:
            text_blocks[-1]["text"] += "\n\n" + post_prompt_anchor
        else:
            final_user_content.append({"type": "text", "text": post_prompt_anchor})
        messages = chat_history + [{"role": "user", "content": final_user_content}]

    # Autonomously Trigger Observational Compression (Background Thread)
    # Token Conservation Gate: Only execute if enabled for this persona.
    om_enabled = persona_data.get("om_enabled", True)
    om_threshold = persona_data.get("om_turn_threshold", 5)
    
    # ---- LAYER 5: NEURAL SYNC (WORKSPACE VISION) ----
    if workspace_context and (workspace_context.get("activeFile") or workspace_context.get("currentCode")):
        active_file = workspace_context.get("activeFile", "Unknown")
        current_code = workspace_context.get("currentCode", "")
        
        nc_block = f"\n[HYPERVISOR_SYNC: LIVE_WORKSPACE]\n"
        nc_block += f"ACTIVEP_FILE: {active_file}\n"
        if current_code:
            nc_block += f"LIVE_CODE_CONTENT:\n```\n{current_code}\n```\n"
        nc_block += "[/HYPERVISOR_SYNC]\n"
        
        # Prepend to the user message to ensure the model sees it as current world-state
        if isinstance(user_message, list):
            user_message.insert(0, {"type": "text", "text": nc_block})
        else:
            user_message = f"{nc_block}\n{user_message}"

    # ---- LAYER 4: DYNAMIC OBSERVERS (EVENT LOGGING) ----
    # Token Conservation Gate: Only execute if enabled for this persona.
    om_enabled = persona_data.get("om_enabled", True)
    om_threshold = persona_data.get("om_turn_threshold", 5)

    # Define a clean callback for the reflector to use
    def reflector_llm_callback(prompt):
        res = call_llm(
            model_id=model_id, 
            system_prompt="You are an internal Reflector agent.", 
            messages=[{"role": "user", "content": prompt}], 
            api_keys=api_keys, 
            stream=False, 
            temperature=0.3,
            custom_base_url=custom_base_url,
            custom_provider_type=custom_provider_type,
            custom_auth_header_name=custom_auth_header_name,
            custom_auth_prefix=custom_auth_prefix
        )
        if isinstance(res, dict):
            return res.get("choices", [{}])[0].get("message", {}).get("content", "")
        return ""

    # Execute all observers (e.g., Memory, Audit Logs) in a background thread
    threading.Thread(
        target=manager.run_observers,
        args=("user_message", text_only_message),
        kwargs={
            "db": db_conn,
            "username": username,
            "persona_key": persona_key,
            "om_enabled": om_enabled,
            "om_turn_threshold": om_threshold,
            "llm_callback": reflector_llm_callback
        },
        daemon=True
    ).start()

    # ---- LAYER 5: DYNAMIC TOOL DISCOVERY (SKILL TREES / MCP) ----
    active_tools = []
    print("\n[TOOL DISCOVERY START]")
    try:
        if mcp_client:
            tools = mcp_client.sync_get_mcp_tools()
            if tools:
                active_tools.extend(tools)
                print(f"[TOOL DISCOVERY] Successfully loaded {len(tools)} MCP tools.")
            
        universal_tools = load_universal_schemas()
        if universal_tools:
            active_tools.extend(universal_tools)

        # Dynamic Plugin Tool Providers (The Skill Tree)
        session_id = kwargs.get("session_id", f"sess_{username}_{persona_key}")
        active_tools = manager.run_tool_providers(
            active_tools, 
            session_id=session_id,
            username=username,
            persona_key=persona_key
        )
        
        # Add Recursive Sub-Agent Skill
        active_tools.append({
            "type": "function",
            "function": {
                "name": "call_sub_agent",
                "description": "Spawn a specialized sub-agent (Claude-in-Claude) to complete a sub-task. Extremely useful for logic verification, creative brainstorming, or data parsing.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "The specific task or query for the sub-agent."},
                        "instruction": {"type": "string", "description": "System instructions for the sub-agent (Who should it be?)."},
                        "model": {"type": "string", "description": "The model ID to use (Default: Sonnet 4)."}
                    },
                    "required": ["prompt"]
                }
            }
        })
        
    except Exception as e:
        print(f"[TOOL DISCOVERY] CRITICAL ERROR during tool fetch: {e}")
        
    print(f"[TOOL DISCOVERY COMPLETE] Total tools bound to payload: {len(active_tools)}\n")

    kwargs_dict = {
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "presence_penalty": presence_penalty,
        "frequency_penalty": frequency_penalty,
        "top_k": top_k,
        "thinking_level": thinking_level,
        "custom_base_url": custom_base_url,
        "custom_provider_type": custom_provider_type,
        "custom_auth_header_name": custom_auth_header_name,
        "custom_auth_prefix": custom_auth_prefix,
        "max_tool_output": max_tool_output
    }

    # Pass everything to the streaming tool interceptor
    class ToolInterceptStream:
        def __iter__(self):
            # Check if reflection was triggered to inject the UI signal
            # We fetch the exact same log count logic the Reflector uses
            current_logs = db_conn.get_observation_log(username, persona_key, limit=100)
            user_msg_count = len([l for l in current_logs if l.get('type') == 'user_message'])
            
            if om_enabled and user_msg_count > 0 and user_msg_count % om_threshold == 0:
                 # Yield a custom frontend control signal before the LLM starts streaming
                 yield f'data: {{"control": "reflection_started"}}\n\n'.encode('utf-8')
                 
            yield from intercepting_stream_generator(
                model_id, 
                full_system_prompt, 
                messages, 
                api_keys, 
                tools=active_tools if active_tools else None,
                kwargs_dict=kwargs_dict,
                username=username,
                persona_key=persona_key,
                session_id=session_id,
                pre_fill=pre_fill,
                max_tool_output=max_tool_output
            )
            
        def iter_lines(self):
            yield from self.__iter__()
            
    return ToolInterceptStream()
