import json
import subprocess

import pytest

import vicon_host


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_tailscale_status_parses_json(monkeypatch):
    monkeypatch.setattr(
        vicon_host.subprocess, "run",
        lambda *a, **k: FakeCompleted(stdout='{"Peer": {}}'),
    )
    assert vicon_host._tailscale_status() == {"Peer": {}}


def test_tailscale_status_raises_when_binary_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("tailscale")
    monkeypatch.setattr(vicon_host.subprocess, "run", boom)
    with pytest.raises(vicon_host.ViconOffline, match="not found on PATH"):
        vicon_host._tailscale_status()


def test_tailscale_status_raises_on_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="tailscale", timeout=10)
    monkeypatch.setattr(vicon_host.subprocess, "run", boom)
    with pytest.raises(vicon_host.ViconOffline, match="timed out"):
        vicon_host._tailscale_status()


def test_tailscale_status_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        vicon_host.subprocess, "run",
        lambda *a, **k: FakeCompleted(returncode=1, stderr="not logged in"),
    )
    with pytest.raises(vicon_host.ViconOffline, match="not logged in"):
        vicon_host._tailscale_status()


def test_tailscale_status_raises_on_malformed_json(monkeypatch):
    monkeypatch.setattr(
        vicon_host.subprocess, "run",
        lambda *a, **k: FakeCompleted(stdout="not json at all"),
    )
    with pytest.raises(vicon_host.ViconOffline, match="unparseable"):
        vicon_host._tailscale_status()


PEER_OFFLINE = {
    "HostName": "VICON-SB001869",
    "DNSName": "vicon-sb001869.taila8bdbd.ts.net.",
    "TailscaleIPs": ["100.83.229.92", "fd7a:115c:a1e0::a535:e55c"],
    "OS": "windows",
    "Online": False,
    "LastSeen": "2026-06-29T18:20:42.1Z",
}

PEER_ONLINE = {
    "HostName": "VICON-SB001869",
    "DNSName": "vicon-sb001869-1.taila8bdbd.ts.net.",
    "TailscaleIPs": ["100.111.64.24", "fd7a:115c:a1e0::d234:4019"],
    "OS": "windows",
    "Online": True,
    "LastSeen": "0001-01-01T00:00:00Z",
}

PEER_PI = {
    "HostName": "raspberrypi",
    "DNSName": "raspberrypi.taila8bdbd.ts.net.",
    "TailscaleIPs": ["100.111.12.119"],
    "Online": True,
    "LastSeen": "0001-01-01T00:00:00Z",
}


def make_status(*peers):
    return {"Peer": {p["DNSName"]: p for p in peers}}


def test_label_strips_domain_and_trailing_dot():
    assert vicon_host._label(PEER_ONLINE) == "vicon-sb001869-1"


def test_candidates_match_vicon_and_exclude_others():
    status = make_status(PEER_OFFLINE, PEER_ONLINE, PEER_PI)
    labels = [vicon_host._label(p) for p in vicon_host._candidates(status)]
    assert labels == ["vicon-sb001869-1", "vicon-sb001869"]


def test_candidates_sorted_newest_rejoin_first():
    newer = dict(PEER_ONLINE, DNSName="vicon-sb001869-2.taila8bdbd.ts.net.")
    status = make_status(PEER_OFFLINE, PEER_ONLINE, newer)
    labels = [vicon_host._label(p) for p in vicon_host._candidates(status)]
    assert labels[0] == "vicon-sb001869-2"


def test_candidates_empty_when_no_vicon_peer():
    assert vicon_host._candidates(make_status(PEER_PI)) == []


def test_candidates_handles_missing_peer_key():
    assert vicon_host._candidates({}) == []


def test_matches_is_case_insensitive():
    peer = {"HostName": "VICON-SB001869", "DNSName": ""}
    assert vicon_host._matches(peer, "vicon") is True


def test_ipv4_of_skips_ipv6():
    assert vicon_host._ipv4_of(PEER_ONLINE) == "100.111.64.24"


def test_ipv4_of_returns_none_without_v4():
    assert vicon_host._ipv4_of({"TailscaleIPs": ["fd7a:115c:a1e0::1"]}) is None


def patch_status(monkeypatch, status):
    monkeypatch.setattr(vicon_host, "_tailscale_status", lambda timeout=10: status)


def patch_probe(monkeypatch, reachable):
    """reachable: set of IPs that accept connections."""
    monkeypatch.setattr(
        vicon_host, "_probe",
        lambda ip, port, timeout: ip in reachable,
    )


def test_resolve_returns_online_peer(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_OFFLINE, PEER_ONLINE, PEER_PI))
    patch_probe(monkeypatch, {"100.111.64.24"})
    assert vicon_host.resolve_vicon_host() == ("100.111.64.24", "vicon-sb001869-1")


def test_resolve_ignores_stale_duplicate_hostname(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_OFFLINE, PEER_ONLINE))
    patch_probe(monkeypatch, {"100.111.64.24", "100.83.229.92"})
    ip, _ = vicon_host.resolve_vicon_host()
    assert ip != "100.83.229.92"


def test_resolve_raises_when_no_vicon_peer(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_PI))
    with pytest.raises(vicon_host.ViconOffline, match="no vicon\\* peer in tailnet"):
        vicon_host.resolve_vicon_host()


def test_resolve_raises_when_all_offline_and_reports_last_seen(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_OFFLINE))
    with pytest.raises(vicon_host.ViconOffline) as exc:
        vicon_host.resolve_vicon_host()
    assert "vicon-sb001869" in str(exc.value)
    assert "2026-06-29" in str(exc.value)


def test_resolve_raises_when_online_but_port_closed(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_ONLINE))
    patch_probe(monkeypatch, set())
    with pytest.raises(vicon_host.ViconOffline, match="port 22 closed"):
        vicon_host.resolve_vicon_host()


def test_resolve_probe_breaks_tie_between_online_peers(monkeypatch):
    newer = dict(PEER_ONLINE, DNSName="vicon-sb001869-2.taila8bdbd.ts.net.",
                 TailscaleIPs=["100.99.99.99"])
    patch_status(monkeypatch, make_status(PEER_ONLINE, newer))
    patch_probe(monkeypatch, {"100.111.64.24"})
    assert vicon_host.resolve_vicon_host() == ("100.111.64.24", "vicon-sb001869-1")


def test_resolve_prefers_newest_when_both_reachable(monkeypatch):
    newer = dict(PEER_ONLINE, DNSName="vicon-sb001869-2.taila8bdbd.ts.net.",
                 TailscaleIPs=["100.99.99.99"])
    patch_status(monkeypatch, make_status(PEER_ONLINE, newer))
    patch_probe(monkeypatch, {"100.111.64.24", "100.99.99.99"})
    assert vicon_host.resolve_vicon_host() == ("100.99.99.99", "vicon-sb001869-2")


def test_resolve_passes_probe_port_through(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_ONLINE))
    seen = {}

    def fake_probe(ip, port, timeout):
        seen["port"] = port
        return True

    monkeypatch.setattr(vicon_host, "_probe", fake_probe)
    vicon_host.resolve_vicon_host(probe_port=21)
    assert seen["port"] == 21


def test_probe_returns_false_on_refused(monkeypatch):
    def boom(addr, timeout=None):
        raise OSError("refused")
    monkeypatch.setattr(vicon_host.socket, "create_connection", boom)
    assert vicon_host._probe("100.0.0.1", 22, 1) is False


def test_cached_reuses_result_within_ttl(monkeypatch):
    vicon_host._clear_cache()
    calls = []
    monkeypatch.setattr(
        vicon_host, "resolve_vicon_host",
        lambda **kw: calls.append(1) or ("100.111.64.24", "vicon-sb001869-1"),
    )
    vicon_host.resolve_vicon_host_cached()
    vicon_host.resolve_vicon_host_cached()
    assert len(calls) == 1


def test_cached_refreshes_after_ttl(monkeypatch):
    vicon_host._clear_cache()
    calls = []
    monkeypatch.setattr(
        vicon_host, "resolve_vicon_host",
        lambda **kw: calls.append(1) or ("100.111.64.24", "vicon-sb001869-1"),
    )
    vicon_host.resolve_vicon_host_cached(ttl=0)
    vicon_host.resolve_vicon_host_cached(ttl=0)
    assert len(calls) == 2


def test_cached_does_not_cache_failures(monkeypatch):
    vicon_host._clear_cache()
    calls = []

    def boom(**kw):
        calls.append(1)
        raise vicon_host.ViconOffline("offline")

    monkeypatch.setattr(vicon_host, "resolve_vicon_host", boom)
    for _ in range(2):
        with pytest.raises(vicon_host.ViconOffline):
            vicon_host.resolve_vicon_host_cached()
    assert len(calls) == 2


def test_cached_keys_on_probe_port(monkeypatch):
    vicon_host._clear_cache()
    ports = []
    monkeypatch.setattr(
        vicon_host, "resolve_vicon_host",
        lambda **kw: ports.append(kw["probe_port"]) or ("100.111.64.24", "n"),
    )
    vicon_host.resolve_vicon_host_cached(probe_port=22)
    vicon_host.resolve_vicon_host_cached(probe_port=21)
    assert ports == [22, 21]
