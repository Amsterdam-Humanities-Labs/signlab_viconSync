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
