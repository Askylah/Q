import ast
from typing import List, Tuple

class CodeAlignmentVisitor(ast.NodeVisitor):
    """
    AST Visitor that scans code for violations of the sovereignty and 
    security guidelines of the Persona/Q architecture.
    """
    def __init__(self):
        self.errors: List[str] = []
        # Disallowed imports that bypass the secure docker runner
        self.blocked_imports = {"importlib", "subprocess", "os", "shutil"}
        # Network packages we want to block in local scripts (forces use of http execution type)
        self.blocked_network_libs = {"socket", "urllib", "requests", "httpx", "http"}
        # Built-in functions that bypass structured execution
        self.blocked_builtins = {"eval", "exec", "__import__"}

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            base_module = alias.name.split('.')[0]
            if base_module in self.blocked_imports:
                self.errors.append(
                    f"Violation: Direct use of module '{alias.name}' is blocked. "
                    f"Local scripts must run sandboxed via secure_runner without host system access."
                )
            if base_module in self.blocked_network_libs:
                self.errors.append(
                    f"Violation: Network library '{alias.name}' is blocked in local scripts. "
                    f"To make HTTP calls, configure the tool spec with type='http' instead."
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            base_module = node.module.split('.')[0]
            if base_module in self.blocked_imports:
                self.errors.append(
                    f"Violation: Import from '{node.module}' is blocked. "
                    f"Local scripts must not import host system manipulation libraries."
                )
            if base_module in self.blocked_network_libs:
                self.errors.append(
                    f"Violation: Import from network library '{node.module}' is blocked. "
                    f"Local scripts must remain offline."
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Check for blocked builtins called by name
        if isinstance(node.func, ast.Name):
            if node.func.id in self.blocked_builtins:
                self.errors.append(
                    f"Violation: Use of built-in function '{node.func.id}()' is prohibited. "
                    f"Dynamic code evaluation must be sandboxed."
                )
        # Check for sys.modules manipulation or other nested calls
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "sys" and node.func.attr == "modules":
                self.errors.append("Violation: Modification of sys.modules is blocked.")
        self.generic_visit(node)

def verify_code_alignment(code: str, expected_entry_point: str = "execute") -> Tuple[bool, List[str]]:
    """
    Parses Python code, checks for syntax issues, runs AST alignment policies,
    and checks that the expected entry point is defined.
    
    Returns (is_aligned, list_of_errors).
    """
    errors = []
    
    # 1. Syntax Verification
    try:
        tree = ast.parse(code)
    except SyntaxError as se:
        return False, [f"Syntax Error on line {se.lineno}: {se.msg}\nCode snippet: {se.text.strip() if se.text else ''}"]
    except Exception as e:
        return False, [f"AST parsing failed: {str(e)}"]

    # 2. Policy Analysis (AST Visiting)
    visitor = CodeAlignmentVisitor()
    visitor.visit(tree)
    errors.extend(visitor.errors)

    # 3. Entry Point Verification
    entry_point_found = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == expected_entry_point:
            entry_point_found = True
            
            # Enforce Option A: exactly one positional/keyword parameter named 'args'
            posonly_count = len(getattr(node.args, "posonlyargs", []))
            std_args = node.args.args
            total_positional = posonly_count + len(std_args)
            
            if total_positional != 1:
                errors.append(
                    f"Violation: Entry point function '{expected_entry_point}' must accept exactly one parameter (found {total_positional})."
                )
            else:
                arg_name = std_args[0].arg if std_args else getattr(node.args, "posonlyargs")[0].arg
                if arg_name != "args":
                    errors.append(
                        f"Violation: Entry point parameter must be named 'args' (found '{arg_name}'). Expected: 'def {expected_entry_point}(args):'."
                    )
            
            if node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
                errors.append(
                    f"Violation: Entry point function '{expected_entry_point}' must not accept keyword-only, variable, or keyword-argument parameters."
                )
            break
            
    if not entry_point_found:
        errors.append(
            f"Violation: Expected entry point function '{expected_entry_point}(args)' was not found at the module level."
        )

    return len(errors) == 0, errors

if __name__ == "__main__":
    # Test case
    bad_code = """
import os
import importlib

def run(args):
    eval("print('hello')")
"""
    aligned, errs = verify_code_alignment(bad_code, expected_entry_point="execute")
    print("Aligned:", aligned)
    print("Errors:", errs)
