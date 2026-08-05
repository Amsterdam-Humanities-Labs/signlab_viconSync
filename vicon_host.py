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


def _label(peer):
    """The peer's unique short name.

    'vicon-sb001869-1.taila8bdbd.ts.net.' -> 'vicon-sb001869-1'. Falls back to
    HostName, which is NOT unique - both Vicon nodes report VICON-SB001869.
    """
    dns = (peer.get("DNSName") or "").strip(".")
    if dns:
        return dns.split(".")[0]
    return (peer.get("HostName") or "").strip()


def _matches(peer, prefix):
    p = prefix.lower()
    return _label(peer).lower().startswith(p) or (peer.get("HostName") or "").lower().startswith(p)


def _candidates(status, prefix=DEFAULT_PREFIX):
    """Every vicon* peer, newest rejoin suffix first.

    Descending label order puts vicon-sb001869-2 before -1 before the bare
    name, so when a stale node is briefly online alongside its replacement the
    newest is tried first. The comparison is lexicographic, which would order
    -10 before -2; ten rejoins is not a case worth complicating this for.
    """
    peers = (status or {}).get("Peer") or {}
    matched = [p for p in peers.values() if _matches(p, prefix)]
    return sorted(matched, key=_label, reverse=True)


def _ipv4_of(peer):
    """The 100.x address. TailscaleIPs is [v4, v6]; never return the fd7a: one."""
    for addr in peer.get("TailscaleIPs") or []:
        if "." in addr:
            return addr
    return None
