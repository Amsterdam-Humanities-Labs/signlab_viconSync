import sync_vicon_rsync as svr


def make_syncer(tmp_path):
    cache = svr.SyncCache(str(tmp_path / "cache.json"))
    return svr.ViconSync(host="100.0.0.1", dry_run=True, cache=cache)


def test_list_date_directories_returns_none_on_ssh_failure(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: ("", 1))
    assert syncer.list_date_directories("E:\\Recordings") is None


def test_list_date_directories_returns_empty_list_when_no_dirs(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: ("", 0))
    assert syncer.list_date_directories("E:\\Recordings") == []


def test_list_date_directories_parses_and_sorts_dates(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    out = "2026-06-15\n2026-05-11\nnot-a-date\n"
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: (out, 0))
    assert syncer.list_date_directories("E:\\Recordings") == ["2026-05-11", "2026-06-15"]


import vicon_host


def test_syncer_uses_injected_host_in_ssh_command(tmp_path, monkeypatch):
    cache = svr.SyncCache(str(tmp_path / "cache.json"))
    syncer = svr.ViconSync(host="100.111.64.24", dry_run=True, cache=cache)
    seen = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(svr.subprocess, "run", fake_run)
    syncer.ssh_execute("dir")
    assert "100.111.64.24" in seen["cmd"]


def test_control_ssh_returns_error_when_vicon_offline(monkeypatch):
    def boom(**kwargs):
        raise vicon_host.ViconOffline("no vicon* peer online")

    monkeypatch.setattr(svr.vicon_host, "resolve_vicon_host_cached", boom)
    assert svr._control_ssh("schtasks /run /tn X") == ("", 1)


def test_module_has_no_hardcoded_vicon_ip():
    source = open("sync_vicon_rsync.py").read()
    assert "100.83.229.92" not in source
