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


def test_control_ssh_passes_vicon_password(monkeypatch):
    seen = {}

    class FakeCompleted:
        returncode = 0
        stdout = "ok"

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(svr.vicon_host, "resolve_vicon_host_cached",
                        lambda **kwargs: ("100.0.0.1", "vicon-test"))
    monkeypatch.setattr(svr.subprocess, "run", fake_run)
    assert svr._control_ssh("dir") == ("ok", 0)
    assert "sshpass -p test-password " in seen["cmd"]


def test_module_references_no_undefined_globals():
    # Guards against renames that leave a stale global behind, like the
    # SSH_PASS references that crashed every sync after the password moved
    # to get_vicon_password().
    import builtins
    import symtable

    source = open("sync_vicon_rsync.py").read()
    defined = set(vars(svr)) | set(dir(builtins))
    missing = set()

    def walk(table):
        for sym in table.get_symbols():
            if sym.is_referenced() and sym.is_global() and sym.get_name() not in defined:
                missing.add(sym.get_name())
        for child in table.get_children():
            walk(child)

    walk(symtable.symtable(source, "sync_vicon_rsync.py", "exec"))
    assert missing == set()


def test_run_counts_failed_listing_as_error(tmp_path):
    syncer = make_syncer(tmp_path)
    syncer.list_date_directories = lambda base_path: None
    assert syncer.run()['errors'] == len(svr.REMOTE_PATHS)


def test_run_does_not_count_empty_listing_as_error(tmp_path):
    syncer = make_syncer(tmp_path)
    syncer.list_date_directories = lambda base_path: []
    assert syncer.run()['errors'] == 0


def test_batch_get_files_powershell_counts_failure_as_error(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: ("", 1))
    assert syncer.batch_get_files_powershell("E:\\Recordings", ["2026-06-15"], True) == {}
    assert syncer.stats['errors'] == 1


def test_batch_get_files_powershell_empty_result_is_not_an_error(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: ("", 0))
    assert syncer.batch_get_files_powershell("E:\\Recordings", ["2026-06-15"], True) == {}
    assert syncer.stats['errors'] == 0


def test_list_recording_directories_counts_failure_as_error(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: ("", 1))
    assert syncer.list_recording_directories("E:\\Recordings", "2026-06-15") == []
    assert syncer.stats['errors'] == 1


def test_list_recording_directories_empty_is_not_an_error(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: ("", 0))
    assert syncer.list_recording_directories("E:\\Recordings", "2026-06-15") == []
    assert syncer.stats['errors'] == 0


def make_subdir_config(tmp_path):
    return {
        'subdir': 'metadata',
        'extensions': ['.json'],
        'local_path': str(tmp_path / "metadata"),
    }


def test_batch_sync_subdir_counts_failure_as_error(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: ("", 1))
    assert syncer.batch_sync_subdir("E:\\Recordings", ["2026-06-15"], make_subdir_config(tmp_path)) is None
    assert syncer.stats['errors'] == 1


def test_batch_sync_subdir_empty_result_is_not_an_error(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: ("", 0))
    assert syncer.batch_sync_subdir("E:\\Recordings", ["2026-06-15"], make_subdir_config(tmp_path)) is None
    assert syncer.stats['errors'] == 0


def test_module_has_no_hardcoded_vicon_ip():
    source = open("sync_vicon_rsync.py").read()
    assert "100.83.229.92" not in source


def test_cc_pipeline_runs_for_cc_subdir_fbx(tmp_path, monkeypatch):
    """CC exports get the FBXtoGLBCompression pipeline on top of the legacy one."""
    syncer = make_syncer(tmp_path)
    calls = []
    monkeypatch.setattr(syncer, "_convert_fbx_to_glb", lambda f, d: calls.append(("legacy", f)))
    monkeypatch.setattr(syncer, "_convert_cc_pipeline", lambda f, d: calls.append(("cc", f)))

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(svr.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    monkeypatch.setattr(svr.os.path, "getsize", lambda p: 123)
    monkeypatch.setattr(syncer.cache, "update_file", lambda *a, **k: None)

    syncer.dry_run = False
    syncer.sync_file("E:\\Recordings", "2026-08-31", "clip.fbx",
                     recording_dir="rec1", unreal_subdir="CC")

    assert calls == [("legacy", "clip.fbx"), ("cc", "clip.fbx")]


def test_cc_pipeline_skipped_for_non_cc_subdir(tmp_path, monkeypatch):
    """Vicon/flat exports have no CC skeleton, so the pipeline must not run."""
    syncer = make_syncer(tmp_path)
    calls = []
    monkeypatch.setattr(syncer, "_convert_fbx_to_glb", lambda f, d: calls.append(("legacy", f)))
    monkeypatch.setattr(syncer, "_convert_cc_pipeline", lambda f, d: calls.append(("cc", f)))

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(svr.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    monkeypatch.setattr(svr.os.path, "getsize", lambda p: 123)
    monkeypatch.setattr(syncer.cache, "update_file", lambda *a, **k: None)

    syncer.dry_run = False
    syncer.sync_file("E:\\Recordings", "2026-08-31", "clip.fbx",
                     recording_dir="rec1", unreal_subdir="Vicon")

    assert calls == [("legacy", "clip.fbx")]
