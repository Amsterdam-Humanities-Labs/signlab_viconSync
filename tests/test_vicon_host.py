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
