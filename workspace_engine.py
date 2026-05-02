import os
import shutil


class SecurityViolation(Exception):
    """Raised when a path escapes the designated workspace root."""
    pass


class SafeWorkspace:
    """
    Root-jailed filesystem interface.
    All paths are resolved and validated against self.root before any I/O.
    Traversal attempts (../) raise SecurityViolation.
    """

    def __init__(self, root: str):
        self.root = os.path.realpath(os.path.abspath(root))

    def _resolve(self, path: str) -> str:
        """
        Resolve an inbound path and assert it stays inside self.root.
        Accepts absolute paths or paths relative to self.root.
        """
        if os.path.isabs(path):
            resolved = os.path.realpath(os.path.abspath(path))
        else:
            resolved = os.path.realpath(os.path.join(self.root, path))

        try:
            common = os.path.commonpath([self.root, resolved])
        except ValueError:
            # commonpath raises ValueError on mixed drives (Windows)
            raise SecurityViolation(f"Path '{path}' is on a different drive than the workspace root.")

        if common != self.root:
            raise SecurityViolation(
                f"Access denied: '{path}' resolves outside the workspace root '{self.root}'."
            )
        return resolved

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def get_file_tree(self, start_path: str) -> dict:
        """
        Recursively builds a dictionary representing the file forest.
        start_path must be within the workspace root.
        """
        safe_path = self._resolve(start_path)
        ignore_list = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.DS_Store'}

        node = {
            "name": os.path.basename(safe_path) or safe_path,
            "path": safe_path,
            "type": "directory",
            "children": []
        }

        try:
            items = sorted(os.listdir(safe_path))
            for item in items:
                if item in ignore_list:
                    continue
                full_path = os.path.join(safe_path, item)
                # Resolve child — if it somehow escapes (symlink attack), skip it silently
                try:
                    child_safe = self._resolve(full_path)
                except SecurityViolation:
                    continue
                if os.path.isdir(child_safe):
                    node["children"].append(self.get_file_tree(child_safe))
                else:
                    node["children"].append({
                        "name": item,
                        "path": child_safe,
                        "type": "file"
                    })
        except Exception:
            pass

        return node

    def read_file_content(self, path: str) -> str:
        """Securely reads file content for the Monaco editor."""
        safe_path = self._resolve(path)
        try:
            with open(safe_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {str(e)}"

    def save_file_content(self, path: str, content: str) -> dict:
        """Writes content back to the filesystem."""
        safe_path = self._resolve(path)
        try:
            os.makedirs(os.path.dirname(safe_path), exist_ok=True)
            with open(safe_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return {"success": True, "message": f"File {os.path.basename(safe_path)} saved."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_item(self, path: str, item_type: str = "file") -> dict:
        """Creates a new file or directory."""
        safe_path = self._resolve(path)
        try:
            if item_type == "directory":
                os.makedirs(safe_path, exist_ok=True)
            else:
                if not os.path.exists(safe_path):
                    os.makedirs(os.path.dirname(safe_path), exist_ok=True)
                    with open(safe_path, 'w') as f:
                        f.write("")
            return {"success": True, "path": safe_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_item(self, path: str) -> dict:
        """Deletes a file or directory recursively."""
        safe_path = self._resolve(path)
        try:
            if os.path.isdir(safe_path):
                shutil.rmtree(safe_path)
            else:
                os.remove(safe_path)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ------------------------------------------------------------------ #
#  Module-level convenience shim                                       #
#  (Used by main.py endpoints that pass a root dynamically)           #
# ------------------------------------------------------------------ #

def get_safe_workspace(root: str) -> SafeWorkspace:
    """Factory: returns a SafeWorkspace jailed to the given root."""
    return SafeWorkspace(root)
