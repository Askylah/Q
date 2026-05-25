import os
import shutil
import subprocess
import uuid
import pathlib
import difflib
import json
from datetime import datetime
from typing import Optional, Dict, Any


# Global staging directory — all staged writes land here regardless of target location.
_STAGING_DIR = pathlib.Path(__file__).resolve().parent / ".staging"


class SecurityViolation(Exception):
    """Raised when a path escapes the designated workspace root or execution limits are breached."""
    pass


class SafeWorkspace:
    """
    Root-jailed filesystem and execution interface.
    Implements 'Strict' security architecture with pathlib resolution and Docker containment.
    """

    def __init__(self, root: str):
        # Resolve the root to an absolute, real path immediately
        self.root = pathlib.Path(root).resolve()
        if not self.root.exists():
            os.makedirs(self.root, exist_ok=True)

    def _resolve(self, path: str) -> pathlib.Path:
        """
        Resolve an inbound path using pathlib and assert it stays inside self.root.
        Neutralizes symlink attacks and path traversal (../).
        """
        try:
            # Join and resolve to catch traversal before checking prefix
            requested_path = pathlib.Path(path)
            if not requested_path.is_absolute():
                resolved = (self.root / requested_path).resolve()
            else:
                resolved = requested_path.resolve()

            # Strict prefix check
            if not str(resolved).startswith(str(self.root)):
                raise SecurityViolation(
                    f"Access denied: '{path}' resolves outside the workspace root '{self.root}'."
                )
            return resolved
        except Exception as e:
            if isinstance(e, SecurityViolation):
                raise
            raise SecurityViolation(f"Path resolution failure for '{path}': {str(e)}")

    # ------------------------------------------------------------------ #
    #  Filesystem API                                                      #
    # ------------------------------------------------------------------ #

    def get_file_tree(self, start_path: str) -> dict:
        safe_path = self._resolve(start_path)
        ignore_list = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.DS_Store'}

        node = {
            "name": safe_path.name or str(safe_path),
            "path": str(safe_path),
            "type": "directory",
            "children": []
        }

        try:
            items = sorted(os.listdir(safe_path))
            for item in items:
                if item in ignore_list:
                    continue
                full_path = safe_path / item
                try:
                    child_safe = self._resolve(str(full_path))
                except SecurityViolation:
                    continue
                    
                if child_safe.is_dir():
                    node["children"].append(self.get_file_tree(str(child_safe)))
                else:
                    node["children"].append({
                        "name": item,
                        "path": str(child_safe),
                        "type": "file"
                    })
        except Exception:
            pass

        return node

    def read_file_content(self, path: str) -> str:
        safe_path = self._resolve(path)
        try:
            return safe_path.read_text(encoding='utf-8')
        except Exception as e:
            return f"Error reading file: {str(e)}"

    def save_file_content(self, path: str, content: str) -> dict:
        safe_path = self._resolve(path)
        try:
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            safe_path.write_text(content, encoding='utf-8')
            return {"success": True, "message": f"File {safe_path.name} saved."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_item(self, path: str, item_type: str = "file") -> dict:
        safe_path = self._resolve(path)
        try:
            if item_type == "directory":
                safe_path.mkdir(parents=True, exist_ok=True)
            else:
                if not safe_path.exists():
                    safe_path.parent.mkdir(parents=True, exist_ok=True)
                    safe_path.write_text("")
            return {"success": True, "path": str(safe_path)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_item(self, path: str) -> dict:
        safe_path = self._resolve(path)
        try:
            if safe_path.is_dir():
                shutil.rmtree(safe_path)
            else:
                safe_path.unlink()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    #  Execution API (The Lab Bench)                                      #
    # ------------------------------------------------------------------ #

    def run_code_secure(self, code: str, timeout: int = 30, max_output: int = 8192) -> str:
        """
        Executes code inside a hardened Docker container with 'Strict' constraints.
        - Memory: 512MB
        - CPU: 1.0
        - PIDs: 64 (Fork bomb protection)
        - Network: None
        - User: persona-user (Non-root)
        - Temporal Guillotine: 30s
        - Output Cap: max_output chars (default 8192)
        """
        temp_id = str(uuid.uuid4())[:8]
        temp_filename = f"lab_exec_{temp_id}.py"
        # Place temp file in the workspace root to ensure it's mountable and safe
        temp_path = self.root / temp_filename
        image_name = "persona-padded-room"
        
        try:
            # 1. Build check (ensures image exists)
            check_image = subprocess.run(["docker", "images", "-q", image_name], capture_output=True, text=True)
            if not check_image.stdout.strip():
                # Dockerfile is expected in the same directory as the engine
                engine_dir = pathlib.Path(__file__).parent.resolve()
                subprocess.run(
                    ["docker", "build", "-t", image_name, "-f", str(engine_dir / "Dockerfile.padded_room"), str(engine_dir)], 
                    capture_output=True, text=True
                )

            # 2. Write payload
            temp_path.write_text(code, encoding='utf-8')

            # 3. 'Strict' Execution Command
            container_cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "--memory", "512m",
                "--memory-swap", "512m",
                "--cpus", "1.0",
                "--pids-limit", "64",
                "--security-opt", "no-new-privileges",
                "-v", f"{temp_path.absolute()}:/sandbox/exec.py:ro",
                image_name,
                "/sandbox/exec.py"
            ]

            result = subprocess.run(
                container_cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            output = result.stdout
            if result.returncode != 0:
                output += f"\n[SANDBOX_EXIT_CODE]: {result.returncode}"
            if result.stderr:
                output += f"\n[SANDBOX_STDERR]\n{result.stderr}"

            if not output:
                return "Execution complete (No output returned)."

            # Layer A: Truncate + Untrusted Envelope at the source
            if len(output) > max_output:
                output = output[:max_output] + f"\n[TRUNCATED: Output exceeded {max_output} chars]"
            return f"<untrusted_tool_output>\n{output}\n</untrusted_tool_output>"

        except subprocess.TimeoutExpired:
            return "CRITICAL FAILURE: Temporal Guillotine triggered. Execution timed out (Possible infinite loop)."
        except Exception as e:
            return f"ENGINE_ERROR: {str(e)}"
        finally:
            if temp_path.exists():
                temp_path.unlink()


    # ------------------------------------------------------------------ #
    #  Staged Write API                                                  #
    # ------------------------------------------------------------------ #

    def stage_write(self, path: str, new_content: str) -> dict:
        """
        Stages a file write for approval. Does NOT touch the target file.
        Writes proposed content to .staging/<id>.tmp and generates a unified diff.

        Returns:
            staging_id:   Opaque token for commit/discard.
            target_path:  Absolute path of the intended write.
            diff:         Unified diff (current → proposed).
            is_new_file:  True if the target does not yet exist.
            control:      Always 'approval_required'.
        """
        safe_path = self._resolve(path)

        # Read existing content for diff
        if safe_path.exists():
            try:
                current_content = safe_path.read_text(encoding="utf-8")
            except Exception:
                current_content = ""
            is_new_file = False
        else:
            current_content = ""
            is_new_file = True

        diff_lines = list(difflib.unified_diff(
            current_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{safe_path.name}",
            tofile=f"b/{safe_path.name}",
            lineterm="",
        ))
        diff_str = "\n".join(diff_lines) if diff_lines else "(No changes detected)"

        staging_id = str(uuid.uuid4())[:12]
        _STAGING_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = _STAGING_DIR / f"{staging_id}.tmp"
        meta_path = _STAGING_DIR / f"{staging_id}.meta.json"

        tmp_path.write_text(new_content, encoding="utf-8")
        meta_path.write_text(json.dumps({
            "staging_id": staging_id,
            "target_path": str(safe_path),
            "workspace_root": str(self.root),
            "staged_at": datetime.now().isoformat(),
            "is_new_file": is_new_file,
        }, indent=2), encoding="utf-8")

        return {
            "staging_id": staging_id,
            "target_path": str(safe_path),
            "diff": diff_str,
            "is_new_file": is_new_file,
            "control": "approval_required",
        }

    def commit_staged_write(self, staging_id: str) -> dict:
        """
        Commits a staged write:
          1. Backs up the current file to <name><ext>.bak (N=1, overwrites previous .bak).
          2. Atomically moves .tmp into the target path.
          3. Cleans up the .meta.json.
        """
        # Sanitize — no path traversal in staging_id
        staging_id = "".join(c for c in staging_id if c.isalnum() or c == "-")
        tmp_path = _STAGING_DIR / f"{staging_id}.tmp"
        meta_path = _STAGING_DIR / f"{staging_id}.meta.json"

        if not tmp_path.exists() or not meta_path.exists():
            return {"success": False, "error": f"No staged write found for ID: {staging_id}"}

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            target_path = pathlib.Path(meta["target_path"])
            workspace_root = pathlib.Path(meta["workspace_root"]).resolve()

            # Re-validate target path hasn't drifted outside its original workspace root
            if not str(target_path.resolve()).startswith(str(workspace_root)):
                return {"success": False, "error": "Security violation: target path outside workspace root."}

            # N=1 backup — copy current file to .bak, overwriting any previous .bak
            if target_path.exists():
                bak_path = target_path.with_suffix(target_path.suffix + ".bak")
                shutil.copy2(str(target_path), str(bak_path))

            # Ensure parent dirs exist, then atomic move
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_path), str(target_path))

            meta_path.unlink(missing_ok=True)
            return {"success": True, "committed_path": str(target_path)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def discard_staged_write(self, staging_id: str) -> dict:
        """Discards a pending staged write without touching any real files."""
        staging_id = "".join(c for c in staging_id if c.isalnum() or c == "-")
        tmp_path = _STAGING_DIR / f"{staging_id}.tmp"
        meta_path = _STAGING_DIR / f"{staging_id}.meta.json"

        if not tmp_path.exists() and not meta_path.exists():
            return {"success": False, "error": f"No staged write found for ID: {staging_id}"}

        tmp_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return {"success": True}

    def list_staged_writes(self) -> list:
        """Returns metadata for all pending staged writes."""
        results = []
        if not _STAGING_DIR.exists():
            return results
        for meta_file in sorted(_STAGING_DIR.glob("*.meta.json")):
            try:
                results.append(json.loads(meta_file.read_text(encoding="utf-8")))
            except Exception:
                pass
        return results


def get_safe_workspace(root: str) -> SafeWorkspace:
    return SafeWorkspace(root)
