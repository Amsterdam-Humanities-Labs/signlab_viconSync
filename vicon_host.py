"""Resolve the Vicon PC's current address from the Tailscale node list.

The Vicon PC rejoins the tailnet under a new node identity after a reinstall
(vicon-sb001869 -> vicon-sb001869-1 -> ...). Each rejoin assigns a fresh 100.x
address and leaves the previous node behind as a permanently-offline peer
carrying the *same* Windows HostName. Pinning an address or a full node name
therefore breaks on the next rejoin, so we scan for the node instead.
"""

import json
import socket
import subprocess
import time

TAILSCALE_BIN = "tailscale"
DEFAULT_PREFIX = "vicon"
CACHE_TTL_SECONDS = 30


class ViconOffline(Exception):
    """No reachable vicon* peer in the tailnet."""


def _tailscale_status(timeout=10):
    """Return the parsed `tailscale status --json` document.

    A missing CLI, a non-zero exit, a timeout and unparseable output all raise
    ViconOffline: to every caller they mean the same thing, which is that we
    cannot locate the Vicon PC right now.
    """
    argv = [TAILSCALE_BIN, "status", "--json"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ViconOffline(f"{TAILSCALE_BIN} not found on PATH")
    except subprocess.TimeoutExpired:
        raise ViconOffline(f"{TAILSCALE_BIN} status timed out after {timeout}s")
    if r.returncode != 0:
        raise ViconOffline(f"{TAILSCALE_BIN} status failed: {r.stderr.strip()[:200]}")
    try:
        return json.loads(r.stdout)
    except ValueError as exc:
        raise ViconOffline(f"{TAILSCALE_BIN} status returned unparseable JSON: {exc}")
