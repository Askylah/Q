# Security & The "Strict Physics" Engine

The Q backend is built on the principle of **Deterministic Security**. When an agent generates code or performs a file-system operation, it is not running on your computer—it is running inside a root-jailed, resource-clamped "Padded Room."

## 🏗️ The Physics Layer (Docker)
The `workspace_engine.py` is the heart of the security model. It enforces kernel-level limits to ensure untrusted code cannot escape or exhaust system resources.

*   **Resource Clamping:** 
    *   **RAM:** Hard cap at 512MB to prevent memory exhaustion attacks.
    *   **CPU:** 1.0 CPU limit to prevent crypto-jacking or DoS.
    *   **PIDs:** 64-process limit to prevent fork-bombs.
*   **Network Isolation:** All containers run with `--network none`. No outbound or inbound traffic is permitted from the sandbox.
*   **Temporal Guillotine:** Every process has a hard 30-second window. If it fails to complete, it is hit with a SIGKILL.

## 🧱 The Governance Layer
Beyond the sandbox, Q uses a semantic governance layer to categorize agent intent.

*   **Danger Levels:**
    *   **INFO:** Reading public logs or directory listings.
    *   **KINETIC:** Writing to files, modifying code, or running scripts.
    *   **DESTRUCTIVE:** Deleting directories or purging databases.
*   **Approval Gates:** Any action categorized as **KINETIC** or higher triggers a `control: approval_required` signal. The agent pauses execution until a human clicks "Go" in the UI.

## 🛡️ The Semantic Firewall
The `firewall_plugin.py` acts as a pre-processor for all incoming agent requests.
*   **Path-Prefix Validation:** Uses `pathlib` to ensure all file paths are absolute and prefixed with the designated `WORKSPACE_ROOT`. Directory traversal (`../`) is impossible.
*   **Command Sanitization:** Only a whitelist of safe shell commands (e.g., `ls`, `cat`, `py`) is allowed.

## 🧪 Red-Teaming Usage
Q is uniquely suited for red-teaming because you can simulate adversarial swarms. By deploying "Attacker" and "Defender" personas in a shared namespace, you can observe how agents attempt to bypass security layers or discover vulnerabilities in a zero-risk environment.
