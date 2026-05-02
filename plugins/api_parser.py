import os
import json
import requests

# Global map to link generated tool names to their raw HTTP route specs
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
    parses them, and returns a list of OpenAI-formatted tool dictionaries.
    """
    tools = []
    
    # Initialize directory if it doesn't exist
    if not os.path.exists(SCHEMAS_DIR):
        os.makedirs(SCHEMAS_DIR, exist_ok=True)
        return tools

    for filename in os.listdir(SCHEMAS_DIR):
        filepath = os.path.join(SCHEMAS_DIR, filename)
        
        try:
            if filename.endswith(".json"):
                with open(filepath, "r", encoding="utf-8") as f:
                    schema_data = json.load(f)
                    tools.extend(_parse_openapi(schema_data))
                    
            elif filename.endswith(".yaml") or filename.endswith(".yml"):
                if HAS_YAML:
                    with open(filepath, "r", encoding="utf-8") as f:
                        schema_data = yaml.safe_load(f)
                        tools.extend(_parse_openapi(schema_data))
                else:
                    print(f"[API_PARSER] PyYAML not installed. Skipping {filename}. Run: pip install pyyaml")
        except Exception as e:
            print(f"[API_PARSER] Error parsing schema {filename}: {e}")

    return tools

def _parse_openapi(schema):
    """
    Takes a raw OpenAPI JSON dictionary and converts its paths/methods 
    into a flat list of LLM-compatible tool definitions.
    """
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
            
            # If no operationId exists, construct a fallback name from the path and method
            if not operation_id:
                clean_path = path.replace('/', '_').replace('{', '').replace('}', '').strip('_')
                operation_id = f"api_{method_lower}_{clean_path}"
                
            # Replace hyphens with underscores, as OpenRouter/OpenAI tool names must be regex ^[a-zA-Z0-9_-]{1,64}$
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
                
                # Tag it depending on where it sits in the HTTP spec
                in_type = param.get("in", "query")
                full_desc = f"[{in_type.upper()}] {param_desc}".strip()
                
                parameters["properties"][name] = {
                    "type": param_schema.get("type", "string"),
                    "description": full_desc
                }
                
                if param.get("required"):
                    parameters["required"].append(name)
                    
            # 3b. Handle JSON request body payloads
            # Note: This is an elementary extraction. Deeply nested schemas would require resolving $ref
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
            EXECUTION_MAP[function_name] = {
                "base_url": base_url,
                "path": path,
                "method": method_lower,
                "parameters_spec": params_list
            }
            
    return tools
def execute_api(func_name, kwargs):
    """
    Looks up the generated func_name in the execution map and fires the actual HTTP request.
    """
    if func_name not in EXECUTION_MAP:
        return f"Error: API function {func_name} not found in execution map."
        
    spec = EXECUTION_MAP[func_name]
    base_url = spec["base_url"].rstrip('/')
    path = spec["path"]
    method = spec["method"].upper()
    
    # 1. Inject path parameters (e.g. /users/{id} -> /users/123)
    query_params = {}
    json_data = {}
    
    for k, v in kwargs.items():
        placeholder = f"{{{k}}}"
        if placeholder in path:
            path = path.replace(placeholder, str(v))
        else:
            # If not a path parameter, route it to query string or body
            if method in ["GET", "DELETE"]:
                query_params[k] = v
            else:
                json_data[k] = v
                
    url = f"{base_url}{path}"
    
    try:
        # Phase 3 bypasses authentication for strictly public APIs.
        # Future phases will inject headers/tokens here from a vault.
        res = requests.request(method, url, params=query_params, json=json_data, timeout=15)
        
        # We try to return pretty JSON if the server responds with it
        try:
            return json.dumps(res.json(), indent=2)
        except:
            return res.text
            
    except Exception as e:
        return f"HTTP Request Failed: {str(e)}"

if __name__ == "__main__":
    # Test script to confirm directory generation
    res = load_universal_schemas()
    print(f"Schema Registry Initialized at: {SCHEMAS_DIR}")
    print(f"Loaded {len(res)} tools.")
