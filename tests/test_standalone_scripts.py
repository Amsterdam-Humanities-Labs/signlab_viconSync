import pytest

import cleanup_vicon
import resync_fbx
import vicon_host


def test_cleanup_vicon_has_no_hardcoded_ip():
    assert "100.83.229.92" not in open("cleanup_vicon.py").read()


def test_resync_fbx_has_no_hardcoded_ip():
    assert "100.83.229.92" not in open("resync_fbx.py").read()


def test_cleanup_vicon_resolves_host(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cleanup_vicon.vicon_host, "resolve_vicon_host",
        lambda **kw: seen.update(kw) or ("100.111.64.24", "vicon-sb001869-1"),
    )
    assert cleanup_vicon.vicon_ssh_host() == "100.111.64.24"
    assert seen["probe_port"] == 22


def test_resync_fbx_resolves_host(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        resync_fbx.vicon_host, "resolve_vicon_host",
        lambda **kw: seen.update(kw) or ("100.111.64.24", "vicon-sb001869-1"),
    )
    assert resync_fbx.vicon_ftp_host() == "100.111.64.24"
    assert seen["probe_port"] == 21


def test_cleanup_vicon_propagates_offline(monkeypatch):
    def boom(**kw):
        raise vicon_host.ViconOffline("no vicon* peer online")

    monkeypatch.setattr(cleanup_vicon.vicon_host, "resolve_vicon_host", boom)
    with pytest.raises(vicon_host.ViconOffline):
        cleanup_vicon.vicon_ssh_host()


def test_resync_fbx_propagates_offline(monkeypatch):
    def boom(**kw):
        raise vicon_host.ViconOffline("no vicon* peer online")

    monkeypatch.setattr(resync_fbx.vicon_host, "resolve_vicon_host", boom)
    with pytest.raises(vicon_host.ViconOffline):
        resync_fbx.vicon_ftp_host()
