import os
import sys
import json
import logging
import subprocess
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("vpn_rotator")

def get_tailscale_exit_nodes() -> list[str]:
    """Discovers available Tailscale exit nodes dynamically, falling back to environment settings."""
    # 1. Try status discovery
    try:
        res = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            exit_nodes = []
            for peer_id, peer in data.get("Peer", {}).items():
                if peer.get("ExitNodeOption") or "exit-node" in peer.get("HostName", "").lower():
                    # Prefer DNSName, then HostName, then Tailscale IPs
                    name = peer.get("DNSName") or peer.get("HostName")
                    if not name and peer.get("TailscaleIPs"):
                        name = peer.get("TailscaleIPs")[0]
                    if name:
                        exit_nodes.append(name.rstrip('.'))
            if exit_nodes:
                return sorted(list(set(exit_nodes)))
    except Exception as e:
        logger.debug(f"[VPN_ROTATOR] Tailscale JSON status query failed: {e}")

    # 2. Fallback to env var configuration
    env_nodes = os.getenv("TAILSCALE_EXIT_NODES", "")
    if env_nodes:
        return [n.strip() for n in env_nodes.split(",") if n.strip()]
    return []

def cycle_tailscale_exit_node() -> bool:
    """Cycles to the next Tailscale exit node in rotation."""
    nodes = get_tailscale_exit_nodes()
    if not nodes:
        logger.warning("[VPN_ROTATOR] No Tailscale exit nodes found for rotation.")
        return False

    current_node = None
    try:
        import redis_client
        if redis_client.is_active():
            r = redis_client.get_connection()
            val = r.get("q:vpn:current_exit_node")
            if val:
                current_node = val.decode('utf-8')
    except Exception:
        pass

    # Find the next node in the round-robin sequence
    if current_node in nodes:
        idx = nodes.index(current_node)
        next_node = nodes[(idx + 1) % len(nodes)]
    else:
        next_node = nodes[0]

    logger.info(f"[VPN_ROTATOR] Attempting to cycle Tailscale exit node to: {next_node}")
    try:
        # Update Tailscale exit node
        res = subprocess.run(["tailscale", "up", f"--exit-node={next_node}"], capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            try:
                import redis_client
                if redis_client.is_active():
                    r = redis_client.get_connection()
                    r.set("q:vpn:current_exit_node", next_node.encode('utf-8'))
            except Exception:
                pass
            logger.info(f"[VPN_ROTATOR] Tailscale exit node set successfully to {next_node}.")
            return True
        else:
            logger.error(f"[VPN_ROTATOR] Tailscale CLI returned non-zero code {res.returncode}: {res.stderr}")
    except Exception as e:
        logger.error(f"[VPN_ROTATOR ERROR] Failed to change Tailscale exit node: {e}")

    return False

def cycle_wireguard_tunnel() -> bool:
    """Cycles to the next WireGuard tunnel profile in rotation."""
    profiles = [p.strip() for p in os.getenv("WIREGUARD_PROFILES", "wg0").split(",") if p.strip()]
    if not profiles:
        logger.warning("[VPN_ROTATOR] No WireGuard profiles configured.")
        return False

    current_profile = None
    try:
        import redis_client
        if redis_client.is_active():
            r = redis_client.get_connection()
            val = r.get("q:vpn:current_wg_profile")
            if val:
                current_profile = val.decode('utf-8')
    except Exception:
        pass

    if current_profile in profiles:
        idx = profiles.index(current_profile)
        next_profile = profiles[(idx + 1) % len(profiles)]
    else:
        next_profile = profiles[0]

    logger.info(f"[VPN_ROTATOR] Cycling WireGuard interface from '{current_profile or 'None'}' to '{next_profile}'")
    try:
        if os.name == 'nt':
            # Windows tunnel service toggle
            if current_profile:
                logger.info(f"[VPN_ROTATOR] Stopping Windows WireGuard Service: WireGuardTunnel${current_profile}")
                subprocess.run(["net", "stop", f"WireGuardTunnel${current_profile}"], capture_output=True, timeout=10)
            res = subprocess.run(["net", "start", f"WireGuardTunnel${next_profile}"], capture_output=True, timeout=10)
            success = (res.returncode == 0)
        else:
            # Linux wg-quick interface toggle
            if current_profile:
                logger.info(f"[VPN_ROTATOR] Bringing down interface {current_profile}")
                subprocess.run(["wg-quick", "down", current_profile], capture_output=True, timeout=10)
            res = subprocess.run(["wg-quick", "up", next_profile], capture_output=True, timeout=10)
            success = (res.returncode == 0)

        if success:
            try:
                import redis_client
                if redis_client.is_active():
                    r = redis_client.get_connection()
                    r.set("q:vpn:current_wg_profile", next_profile.encode('utf-8'))
            except Exception:
                pass
            logger.info(f"[VPN_ROTATOR] WireGuard profile '{next_profile}' is now active.")
            return True
        else:
            logger.error(f"[VPN_ROTATOR] Failed to toggle WireGuard interface {next_profile}.")
    except Exception as e:
        logger.error(f"[VPN_ROTATOR ERROR] WireGuard interface cycling failed: {e}")

    return False

def rotate_egress_route() -> bool:
    """Trigger rotation across Tailscale mesh exit nodes, falling back to WireGuard interfaces."""
    logger.warning("[VPN_ROTATOR] Connection failures detected. Initiating egress path rotation...")
    
    # 1. Cycle Tailscale exit nodes first
    if cycle_tailscale_exit_node():
        logger.info("[VPN_ROTATOR] Stabilizing new connection (3s delay)...")
        time.sleep(3.0)
        return True

    # 2. Fallback to WireGuard profiles if Tailscale fails
    if cycle_wireguard_tunnel():
        logger.info("[VPN_ROTATOR] Stabilizing new connection (3s delay)...")
        time.sleep(3.0)
        return True

    logger.error("[VPN_ROTATOR] Failover rotation failed. All paths returned errors.")
    return False

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "rotate":
        rotate_egress_route()
    else:
        print("Tailscale exit nodes found:", get_tailscale_exit_nodes())
