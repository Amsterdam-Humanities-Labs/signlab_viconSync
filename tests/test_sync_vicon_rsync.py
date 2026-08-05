import sync_vicon_rsync as svr


def make_syncer(tmp_path):
    cache = svr.SyncCache(str(tmp_path / "cache.json"))
    return svr.ViconSync(dry_run=True, cache=cache)


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
