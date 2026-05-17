import subprocess
import os
import sys
import uuid

def run_in_padded_room(code: str, timeout: int = 20, max_output: int = 8192) -> str:
    """
    Executes Python code inside a hardened Docker container.
    Output is capped at max_output chars and wrapped in an untrusted envelope.
    """
    temp_id = str(uuid.uuid4())[:8]
    temp_filename = f"lab_temp_{temp_id}.py"
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
        
        container_cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", "128m",
            "--cpus", "0.5",
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
