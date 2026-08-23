import subprocess
import os
import sys
import uuid

def run_in_padded_room(code: str, timeout: int = 20, max_output: int = 8192) -> str:
    """
    Executes Python code inside a hardened Docker container.
    Output is capped at max_output chars and wrapped in an untrusted envelope.
    """
    import tempfile, pathlib
    temp_id = str(uuid.uuid4())[:8]
    # FIX(cwd-write): the payload used to be written into the process CWD
    # (frequently the project root, which the filesystem MCP also watches).
    # Stage it in an isolated temp dir instead so a lab run never drops a
    # .py into your source tree.
    _lab_tmp = pathlib.Path(tempfile.gettempdir()) / "padded_room_payloads"
    _lab_tmp.mkdir(parents=True, exist_ok=True)
    temp_path_obj = _lab_tmp / f"lab_temp_{temp_id}.py"
    temp_filename = str(temp_path_obj)
    image_name = "persona-padded-room"
    
    try:
        # 1. Ensure the Padded Room image exists
        # In a real environment, we'd do this once. Here we check existence.
        check_image = subprocess.run(["docker", "images", "-q", image_name], capture_output=True, text=True)
        if not check_image.stdout.strip():
            print(f"[SECURE_RUNNER] Building image {image_name}...")
            # Assuming Dockerfile.padded_room is in the same directory
            build_res = subprocess.run(["docker", "build", "-t", image_name, "-f", "Dockerfile.padded_room", "."], capture_output=True, text=True)
            if build_res.returncode != 0:
                return f"ENGINE ERROR: Failed to build padded room. {build_res.stderr}"

        # 2. Write the payload to a local temporary file
        with open(temp_filename, "w", encoding="utf-8") as f:
            f.write(code)

        # 3. Execution Command
        # --rm: Clean up container after run
        # --network none: No internet access
        # --memory 128m: Limit RAM
        # --cpus 0.5: Limit CPU
        # -v ...: Mount only the specific temp file
        abs_temp_path = os.path.abspath(temp_filename)
        
        # Hardened to match workspace_engine.run_code_secure. Adds pids-limit
        # (fork-bomb), no-new-privileges (setuid escalation), read-only rootfs
        # + small writable tmpfs, and an explicit non-root user in case the
        # image's USER directive is ever dropped. --memory-swap == --memory
        # disables swap so the memory cap is real.
        container_cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", "512m",
            "--memory-swap", "512m",
            "--cpus", "1.0",
            "--pids-limit", "64",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--tmpfs", "/tmp:size=32m",
            "--user", "persona-user",
            "-v", f"{abs_temp_path}:/sandbox/exec.py:ro",
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
        if result.stderr:
            output += f"\n--- LAB OVERFLOW (Errors) ---\n{result.stderr}"

        if not output:
            return "Execution complete (No output returned)."

        # Layer A: Truncate + Untrusted Envelope at the source
        if len(output) > max_output:
            output = output[:max_output] + f"\n[TRUNCATED: Output exceeded {max_output} chars]"
        return f"[UNTRUSTED_TOOL_OUTPUT]\n{output}\n[/UNTRUSTED_TOOL_OUTPUT]"

    except subprocess.TimeoutExpired:
        return "CRITICAL FAILURE: Execution timed out (Possible infinite loop or resource exhaustion)."
    except Exception as e:
        return f"SECURE RUNNER ERROR: {str(e)}"
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

if __name__ == "__main__":
    # Test call
    test_code = "print(1 + 1)"
    print(run_in_padded_room(test_code))
