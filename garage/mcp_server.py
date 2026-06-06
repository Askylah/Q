"""
MCP Server — THE LAB BENCH
Provides Rick with tools for Python execution and direct RAG querying.
"""
from mcp.server.fastmcp import FastMCP
import subprocess
import sys
import os
from rag_engine import PersonaRAG
from search_engine import web_search
import workspace_engine as workspace

# Initialize FastMCP
mcp = FastMCP("RickLab")

# Persistent RAG instance for the server
rag = PersonaRAG()

# Initialize a default workspace for lab executions
# We jail it to the 'labs' directory to separate execution from source code
LAB_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labs")
os.makedirs(LAB_ROOT, exist_ok=True)
lab_ws = workspace.SafeWorkspace(LAB_ROOT)

@mcp.tool()
async def execute_python_lab(code: str) -> str:
    """
    Executes Python code inside a hardened, network-isolated Docker container.
    Implements 'Strict' security: 512MB RAM, 1 CPU, 64 PIDs limit, 30s timeout.
    Returns the stdout/stderr of the execution.
    """
    return lab_ws.run_code_secure(code)

@mcp.tool()
async def destructive_debug(code: str) -> str:
    """
    Executes Python code with deep AST-level telemetry injection.
    Instruments every assignment and return node to print runtime states,
    and catches all errors with a full execution traceback.
    Use this to debug complex logic or trace execution paths.
    """
    import ast
    
    class ASTTelemetryInjector(ast.NodeTransformer):
        def visit_Assign(self, node):
            self.generic_visit(node)
            names = []
            def find_names(target):
                if isinstance(target, ast.Name):
                    names.append(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in target.elts:
                        find_names(elt)
                elif isinstance(target, ast.Attribute):
                    names.append(target.attr)
            
            for t in node.targets:
                find_names(t)
                
            if not names:
                return node
                
            print_nodes = []
            for name in names:
                try:
                    msg = f"[TELEMETRY] Line {node.lineno}: {name} = "
                    print_call = ast.Expr(
                        value=ast.Call(
                            func=ast.Name(id='print', ctx=ast.Load()),
                            args=[
                                ast.BinOp(
                                    left=ast.Constant(value=msg),
                                    op=ast.Add(),
                                    right=ast.Call(
                                        func=ast.Name(id='repr', ctx=ast.Load()),
                                        args=[ast.Name(id=name, ctx=ast.Load())],
                                        keywords=[]
                                    )
                                )
                            ],
                            keywords=[]
                        )
                    )
                    print_nodes.append(print_call)
                except Exception:
                    pass
            return [node] + print_nodes

        def visit_Return(self, node):
            self.generic_visit(node)
            if not node.value:
                return node
            try:
                temp_var_name = f"_telemetry_ret_{node.lineno}"
                assign_node = ast.Assign(
                    targets=[ast.Name(id=temp_var_name, ctx=ast.Store())],
                    value=node.value
                )
                msg = f"[TELEMETRY] Line {node.lineno}: Return value = "
                print_call = ast.Expr(
                    value=ast.Call(
                        func=ast.Name(id='print', ctx=ast.Load()),
                        args=[
                            ast.BinOp(
                                left=ast.Constant(value=msg),
                                op=ast.Add(),
                                right=ast.Call(
                                    func=ast.Name(id='repr', ctx=ast.Load()),
                                    args=[ast.Name(id=temp_var_name, ctx=ast.Load())],
                                    keywords=[]
                                )
                            )
                        ],
                        keywords=[]
                    )
                )
                new_return = ast.Return(value=ast.Name(id=temp_var_name, ctx=ast.Load()))
                return [assign_node, print_call, new_return]
            except Exception:
                return node

    try:
        tree = ast.parse(code)
        injector = ASTTelemetryInjector()
        instrumented_tree = injector.visit(tree)
        ast.fix_missing_locations(instrumented_tree)
        
        try_node = ast.Try(
            body=instrumented_tree.body,
            handlers=[
                ast.ExceptHandler(
                    type=ast.Name(id='Exception', ctx=ast.Load()),
                    name='e',
                    body=[
                        ast.Import(names=[ast.alias(name='traceback', asname=None)]),
                        ast.Expr(
                            value=ast.Call(
                                func=ast.Attribute(
                                    value=ast.Name(id='traceback', ctx=ast.Load()),
                                    attr='print_exc',
                                    ctx=ast.Load()
                                ),
                                args=[],
                                keywords=[]
                            )
                        )
                    ]
                )
            ],
            orelse=[],
            finalbody=[]
        )
        instrumented_tree.body = [try_node]
        ast.fix_missing_locations(instrumented_tree)
        instrumented_code = ast.unparse(instrumented_tree)
    except Exception as e:
        instrumented_code = code
        print(f"[TELEMETRY ERROR] AST transformation failed: {e}")

    return lab_ws.run_code_secure(instrumented_code)

@mcp.tool()
async def deep_lore_query(query: str, persona: str = "rick") -> str:
    """
    Query the persona's semantic knowledge base directly.
    Use this if you need to double-check a fact or retrieve complex history.
    """
    try:
        # Default user handles for sandbox
        username = os.getenv("PERSONA_USER", "Askylah")
        return rag.query(query, persona, username)
    except Exception as e:
        return f"MEM_ERROR: {str(e)}"

@mcp.tool()
async def search_web(query: str, max_results: int = 5) -> str:
    """
    Search the web via DuckDuckGo. Results are sanitized to strip
    any embedded instructions or injection attempts.
    Use this when you need current information or facts you don't already know.
    """
    try:
        return web_search(query, max_results=max_results)
    except Exception as e:
        return f"SEARCH_ERROR: {str(e)}"

# --- UNIVERSAL API EXPOSURE ---
sys.path.append(os.path.join(os.path.dirname(__file__), "plugins"))
from api_parser import load_universal_schemas

def get_universal_tools():
    """
    Returns the parsed OpenRouter Tool Defintions generated by the schema registry.
    """
    return load_universal_schemas()

if __name__ == "__main__":
    mcp.run()
