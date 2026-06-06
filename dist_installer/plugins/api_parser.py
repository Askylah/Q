import os
import json
import requests

# Global map to link generated tool names to execution specs (HTTP or local script)
EXECUTION_MAP = {}

# Ensure PyYAML is optional if the user hasn't installed it yet
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# Resolve the schemas directory relative to this file
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMAS_DIR = os.path.join(PLUGIN_DIR, "schemas")

def load_universal_schemas():
    """
    Scans the plugins/schemas directory for openapi.json or openapi.yaml files,
    queries Redis for dynamic tool registrations if active, and builds a
    thread-safe isolated execution map before performing an atomic reference swap.
    """
    global EXECUTION_MAP
    tools = []
    temp_execution_map = {}
    
    # 1. Load static schemas from local files
    if os.path.exists(SCHEMAS_DIR):
        for filename in os.listdir(SCHEMAS_DIR):
            filepath = os.path.join(SCHEMAS_DIR, filename)
            
            try:
                schema_data = None
                if filename.endswith(".json"):
                    with open(filepath, "r", encoding="utf-8") as f:
                        schema_data = json.load(f)
                elif filename.endswith(".yaml") or filename.endswith(".yml"):
                    if HAS_YAML:
                        with open(filepath, "r", encoding="utf-8") as f:
                            schema_data = yaml.safe_load(f)
                    else:
                        print(f"[API_PARSER] PyYAML not installed. Skipping {filename}.")
                        
                if schema_data:
                    parsed_tools = _parse_openapi(schema_data, temp_execution_map)
                    tools.extend(parsed_tools)
            except Exception as e:
                print(f"[API_PARSER] Error parsing schema {filename}: {e}")

    # 2. Load dynamic tools from Redis if active
    try:
        import redis_client
        if redis_client.is_active():
            conn = redis_client.get_connection()
            if conn:
                dynamic_tools_raw = conn.hgetall("q:tools:dynamic")
                if dynamic_tools_raw:
                    for field_bytes, val_bytes in dynamic_tools_raw.items():
                        field_name = field_bytes.decode("utf-8")
                        try:
                            schema_data = json.loads(val_bytes.decode("utf-8"))
                            
                            # Check if this is an OpenAPI specification
                            if "paths" in schema_data:
                                parsed = _parse_openapi(schema_data, temp_execution_map)
                                tools.extend(parsed)
                                print(f"[API_PARSER] Loaded dynamic OpenAPI tool from Redis: {field_name} (found {len(parsed)} endpoints)")
                            
                            # Check if it is a pre-formatted OpenAI tool spec
                            elif "function" in schema_data and "name" in schema_data["function"]:
                                func_name = schema_data["function"]["name"]
                                tools.append(schema_data)
                                
                                # Setup custom execution spec
                                exec_spec = schema_data.get("_execution", {})
                                exec_type = exec_spec.get("type", "http").lower()
                                
                                if exec_type == "local_script":
                                    temp_execution_map[func_name] = {
                                        "type": "local_script",
                                        "script_path": exec_spec.get("script_path"),
                                        "entry_point": exec_spec.get("entry_point", "execute")
                                    }
                                else:
                                    temp_execution_map[func_name] = {
                                        "type": "http",
                                        "base_url": exec_spec.get("url", "http://localhost"),
                                        "path": exec_spec.get("path", "/"),
                                        "method": exec_spec.get("method", "GET").lower(),
                                        "parameters_spec": exec_spec.get("parameters", [])
                                    }
                                print(f"[API_PARSER] Loaded dynamic function tool ({exec_type}) from Redis: {func_name}")
                        except Exception as e:
                            print(f"[API_PARSER] Error loading dynamic tool '{field_name}' from Redis: {e}")
    except Exception as e:
        print(f"[API_PARSER] Redis dynamic tool query failed: {e}")

    # 3. Thread-safe atomic reference swap
    EXECUTION_MAP = temp_execution_map
    return tools

def _parse_openapi(schema, execution_map=None):
    """
    Takes a raw OpenAPI JSON dictionary and converts its paths/methods 
    into a flat list of LLM-compatible tool definitions, writing execution
    routes to the provided isolated map.
    """
    if execution_map is None:
        execution_map = EXECUTION_MAP
        
    tools = []
    paths = schema.get("paths", {})
    
    # Extract base URL
    base_url = "http://localhost"
    servers = schema.get("servers", [])
    if servers and isinstance(servers, list):
        base_url = servers[0].get("url", base_url)
    
    for path, methods in paths.items():
        for method, details in methods.items():
            method_lower = method.lower()
            if method_lower not in ["get", "post", "put", "delete", "patch"]:
                continue
            
            # 1. Determine the function name
            operation_id = details.get("operationId")
            if not operation_id:
                clean_path = path.replace('/', '_').replace('{', '').replace('}', '').strip('_')
                operation_id = f"api_{method_lower}_{clean_path}"
                
            function_name = operation_id.replace("-", "_").replace(".", "_")
            if len(function_name) > 64:
                function_name = function_name[:64].strip("_")
            
            # 2. Extract description
            description = details.get("summary", details.get("description", f"Execute {method.upper()} on {path} endpoint."))
            
            # 3. Construct parameters payload
            parameters = {
                "type": "object",
                "properties": {},
                "required": []
            }
            
            # 3a. Handle explicit path/query parameters
            params_list = details.get("parameters", [])
            for param in params_list:
                name = param.get("name")
                if not name: continue
                
                param_schema = param.get("schema", {"type": "string"})
                param_desc = param.get("description", "")
                in_type = param.get("in", "query")
                full_desc = f"[{in_type.upper()}] {param_desc}".strip()
                
                parameters["properties"][name] = {
                    "type": param_schema.get("type", "string"),
                    "description": full_desc
                }
                
                if param.get("required"):
                    parameters["required"].append(name)
                    
            # 3b. Handle JSON request body payloads
            if "requestBody" in details:
                content = details["requestBody"].get("content", {})
                json_content = content.get("application/json", {})
                req_schema = json_content.get("schema", {})
                
                if "properties" in req_schema:
                    for prop_name, prop_details in req_schema["properties"].items():
                        parameters["properties"][prop_name] = {
                            "type": prop_details.get("type", "string"),
                            "description": prop_details.get("description", "")
                        }
                    
                    if "required" in req_schema and isinstance(req_schema["required"], list):
                        parameters["required"].extend(req_schema["required"])

            # 4. Compile the final tool definition
            tool = {
                "type": "function",
                "function": {
                    "name": function_name,
                    "description": description,
                    "parameters": parameters
                }
            }
            tools.append(tool)
            
            # Map the function for execution
            execution_map[function_name] = {
                "type": "http",
                "base_url": base_url,
                "path": path,
                "method": method_lower,
                "parameters_spec": params_list
            }
            
    return tools

def execute_api(func_name, kwargs):
    """
    Looks up the generated func_name in the execution map and routes the execution
    to either a sandboxed local python script or an external HTTP call.
    """
    if func_name not in EXECUTION_MAP:
        return f"Error: API function {func_name} not found in execution map."
        
    spec = EXECUTION_MAP[func_name]
    exec_type = spec.get("type", "http")
    
    # ── CASE 1: LOCAL SANDBOXED PYTHON EXECUTION ──
    if exec_type == "local_script":
        script_path = spec.get("script_path")
        entry_point = spec.get("entry_point", "execute")
        
        # Resolve script path relative to the application root (parent of plugins directory)
        app_root = os.path.dirname(PLUGIN_DIR)
        resolved_path = script_path
        if not os.path.isabs(resolved_path):
            rel_path = resolved_path.lstrip("/\\")
            resolved_path = os.path.join(app_root, rel_path)
            
        if not os.path.exists(resolved_path):
            return f"Error: Local execution script not found at target path: {resolved_path}"
            
        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                script_code = f.read()
        except Exception as e:
            return f"Error reading local script: {e}"

        # Run AST alignment verification
        try:
            # Add app root to path to load alignment_engine cleanly
            import sys
            sys.path.append(app_root)
            from alignment_engine import verify_code_alignment
            aligned, errors = verify_code_alignment(script_code, entry_point)
            if not aligned:
                return f"Verification Failure (Alignment Violation):\n" + "\n".join(errors)
        except Exception as ae:
            print(f"[API_PARSER] Failed to perform AST alignment check: {ae}")
            
        # Append transient execution payload wrapper
        args_json_repr = repr(json.dumps(kwargs))
        wrapper = (
            f"\n\n# ── TRANSIENT SANDBOX EXECUTION WRAPPER ──\n"
            f"if __name__ == '__main__':\n"
            f"    import sys\n"
            f"    import json\n"
            f"    import inspect\n"
            f"    try:\n"
            f"        args_dict = json.loads({args_json_repr})\n"
            f"        sig = inspect.signature({entry_point})\n"
            f"        params = list(sig.parameters.values())\n"
            f"        if len(params) == 1 and params[0].kind in [inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD] and params[0].name not in args_dict:\n"
            f"            res = {entry_point}(args_dict)\n"
            f"        else:\n"
            f"            res = {entry_point}(**args_dict)\n"
            f"        if res is not None:\n"
            f"            print(res)\n"
            f"    except Exception as e:\n"
            f"        import traceback\n"
            f"        traceback.print_exc()\n"
            f"        sys.exit(1)\n"
        )
        full_code = script_code + wrapper
        
        try:
            from secure_runner import run_in_padded_room
            return run_in_padded_room(full_code)
        except ImportError:
            return "Error: secure_runner module is not available in current environment."
        except Exception as e:
            return f"Error executing local script in padded room: {e}"
            
    # ── CASE 2: REMOTE HTTP API CALL ──
    else:
        base_url = spec["base_url"].rstrip('/')
        path = spec["path"]
        method = spec["method"].upper()
        
        # Inject path parameters
        query_params = {}
        json_data = {}
        
        for k, v in kwargs.items():
            placeholder = f"{{{k}}}"
            if placeholder in path:
                path = path.replace(placeholder, str(v))
            else:
                if method in ["GET", "DELETE"]:
                    query_params[k] = v
                else:
                    json_data[k] = v
                    
        url = f"{base_url}{path}"
        
        try:
            res = requests.request(method, url, params=query_params, json=json_data, timeout=15)
            try:
                return json.dumps(res.json(), indent=2)
            except:
                return res.text
        except Exception as e:
            return f"HTTP Request Failed: {str(e)}"

if __name__ == "__main__":
    res = load_universal_schemas()
    print(f"Schema Registry Initialized at: {SCHEMAS_DIR}")
    print(f"Loaded {len(res)} tools.")
