import pytest

import ftp_monitor
import vicon_host


def make_config():
    return {
        "ftp": {"user": "vicon", "password": "pw", "timeout": 1,
                "host_prefix": "vicon", "base_path": "/e/Recordings"},
        "monitoring": {"max_connection_retries": 1, "retry_backoff_base": 1,
                       "retry_backoff_max": 1},
    }


class FakeFTP:
    connected_to = None

    def __init__(self, timeout=None):
        pass

    def connect(self, host):
        FakeFTP.connected_to = host

    def login(self, user, password):
        pass


def test_connect_uses_resolved_host(monkeypatch):
    FakeFTP.connected_to = None
    monkeypatch.setattr(ftp_monitor, "FTP", FakeFTP)
    monkeypatch.setattr(
        ftp_monitor.vicon_host, "resolve_vicon_host",
        lambda **kw: ("100.111.64.24", "vicon-sb001869-1"),
    )
    mgr = ftp_monitor.FtpConnectionManager(make_config())
    assert mgr.connect() is True
    assert FakeFTP.connected_to == "100.111.64.24"


def test_connect_probes_port_21(monkeypatch):
    seen = {}
    monkeypatch.setattr(ftp_monitor, "FTP", FakeFTP)
    monkeypatch.setattr(
        ftp_monitor.vicon_host, "resolve_vicon_host",
        lambda **kw: seen.update(kw) or ("100.111.64.24", "n"),
    )
    ftp_monitor.FtpConnectionManager(make_config()).connect()
    assert seen["probe_port"] == 21


def test_connect_returns_false_when_vicon_offline(monkeypatch):
    def boom(**kw):
        raise vicon_host.ViconOffline("no vicon* peer online")

    monkeypatch.setattr(ftp_monitor.vicon_host, "resolve_vicon_host", boom)
    assert ftp_monitor.FtpConnectionManager(make_config()).connect() is False


def test_config_has_no_hardcoded_ip():
    # The example template is the tracked one; monitor_config.json is local-only.
    source = open("monitor_config.example.json").read()
    assert "100.83.229.92" not in source


def test_connect_stores_resolved_host_on_success(monkeypatch):
    monkeypatch.setattr(ftp_monitor, "FTP", FakeFTP)
    monkeypatch.setattr(
        ftp_monitor.vicon_host, "resolve_vicon_host",
        lambda **kw: ("100.111.64.24", "vicon-sb001869-1"),
    )
    mgr = ftp_monitor.FtpConnectionManager(make_config())
    assert mgr.connect() is True
    assert mgr.host == "100.111.64.24"
    assert mgr.node_name == "vicon-sb001869-1"


def test_connect_leaves_host_none_when_offline(monkeypatch):
    def boom(**kw):
        raise vicon_host.ViconOffline("no vicon* peer online")

    monkeypatch.setattr(ftp_monitor.vicon_host, "resolve_vicon_host", boom)
    mgr = ftp_monitor.FtpConnectionManager(make_config())
    assert mgr.connect() is False
    assert mgr.host is None
    assert mgr.node_name is None
