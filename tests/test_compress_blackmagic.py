import json
import os

import compress_blackmagic as cb


def test_load_state_missing_returns_empty_shape(tmp_path):
    state = cb.load_state(str(tmp_path / "nope.json"))
    assert state == {"done": {}, "failures": {}}


def test_state_roundtrip_is_atomic_and_lossless(tmp_path):
    p = str(tmp_path / "state.json")
    state = {"done": {"2026-06-12/a.mp4": [123.5, 42]}, "failures": {"2026-06-12/b.mp4": 2}}
    cb.save_state(p, state)
    assert json.load(open(p)) == state
    assert cb.load_state(p) == state


def test_load_config_returns_blackmagic_section(tmp_path):
    cfgp = str(tmp_path / "cfg.json")
    json.dump({"blackmagic_mini": {"workers": 3}}, open(cfgp, "w"))
    assert cb.load_config(cfgp)["workers"] == 3


def _touch(path, content=b"x"):
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)


def test_iter_source_clips_only_mp4_sorted(tmp_path):
    root = tmp_path / "src"
    _touch(str(root / "2026-05-11" / "a.mp4"))
    _touch(str(root / "2026-05-11" / "b.MP4"))
    _touch(str(root / "2026-05-11" / "note.txt"))
    _touch(str(root / "2026-05-11" / "raw.braw"))
    _touch(str(root / "2026-06-12" / "c.mp4"))
    rels = cb.iter_source_clips(str(root))
    assert rels == ["2026-05-11/a.mp4", "2026-05-11/b.MP4", "2026-06-12/c.mp4"]


def test_dest_path_for_joins_under_root():
    assert cb.dest_path_for("2026-06-12/x.mp4", "/mnt/dest") == "/mnt/dest/2026-06-12/x.mp4"


def test_source_sig_none_for_zero_byte(tmp_path):
    p = str(tmp_path / "z.mp4")
    _touch(p, content=b"")
    assert cb.source_sig(p) is None


def test_source_sig_none_for_missing(tmp_path):
    assert cb.source_sig(str(tmp_path / "gone.mp4")) is None


def test_source_sig_returns_mtime_size(tmp_path):
    p = str(tmp_path / "v.mp4")
    _touch(p, content=b"abcde")
    sig = cb.source_sig(p)
    assert sig is not None and sig[1] == 5


def _cfg(src, dest, max_attempts=5):
    return {"source_path": src, "dest_path": dest, "max_attempts": max_attempts}


def test_is_done_true_when_sig_matches_and_dest_exists(tmp_path):
    dest = tmp_path / "dest"
    _touch(str(dest / "2026-06-12/a.mp4"))
    state = {"done": {"2026-06-12/a.mp4": [10.0, 5]}, "failures": {}}
    assert cb.is_done("2026-06-12/a.mp4", (10.0, 5), str(dest), state) is True


def test_is_done_false_when_dest_missing(tmp_path):
    state = {"done": {"2026-06-12/a.mp4": [10.0, 5]}, "failures": {}}
    assert cb.is_done("2026-06-12/a.mp4", (10.0, 5), str(tmp_path / "dest"), state) is False


def test_is_done_false_when_sig_changed(tmp_path):
    dest = tmp_path / "dest"
    _touch(str(dest / "2026-06-12/a.mp4"))
    state = {"done": {"2026-06-12/a.mp4": [10.0, 5]}, "failures": {}}
    assert cb.is_done("2026-06-12/a.mp4", (99.0, 5), str(dest), state) is False


def test_build_plan_classifies_clips(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    # done clip (dest exists + sig recorded)
    _touch(str(src / "2026-06-12/done.mp4"), content=b"abc")
    _touch(str(dest / "2026-06-12/done.mp4"))
    # fresh clip to encode
    _touch(str(src / "2026-06-12/new.mp4"), content=b"abcd")
    # parked clip (too many failures)
    _touch(str(src / "2026-06-12/bad.mp4"), content=b"ab")
    # unreadable clip (zero-byte)
    _touch(str(src / "2026-06-12/empty.mp4"), content=b"")

    done_sig = list(cb.source_sig(str(src / "2026-06-12/done.mp4")))
    state = {
        "done": {"2026-06-12/done.mp4": done_sig},
        "failures": {"2026-06-12/bad.mp4": 5},
    }
    plan = cb.build_plan(_cfg(str(src), str(dest)), state)
    assert [i["rel"] for i in plan["items"]] == ["2026-06-12/new.mp4"]
    assert plan["items"][0]["src"] == str(src / "2026-06-12/new.mp4")
    assert plan["items"][0]["dest"] == str(dest / "2026-06-12/new.mp4")
    assert plan["n_done"] == 1
    assert plan["n_parked"] == 1
    assert plan["n_unreadable"] == 1


def _enc_cfg():
    return {"encode": {"ssh_host": "monsterfish", "scale_width": 1920,
                       "scale_height": 1080, "codec": "libx265",
                       "crf": 26, "preset": "fast"}}


def test_remote_ffmpeg_cmd_has_scale_codec_crf_and_fragmented_mp4():
    cmd = cb.remote_ffmpeg_cmd(_enc_cfg(), ["-c:a", "copy"])
    assert "scale=1920:1080" in cmd
    assert "-c:v libx265" in cmd
    assert "-crf 26" in cmd
    assert "-preset fast" in cmd
    assert "-c:a copy" in cmd
    assert "frag_keyframe" in cmd and "-f mp4 -" in cmd
    assert cmd.strip().startswith("ffmpeg")


def test_remote_ffmpeg_cmd_aac_fallback_args():
    cmd = cb.remote_ffmpeg_cmd(_enc_cfg(), ["-c:a", "aac", "-b:a", "128k"])
    assert "-c:a aac -b:a 128k" in cmd


def test_ssh_argv_targets_host_with_batchmode():
    argv = cb.ssh_argv(_enc_cfg(), "ffmpeg -i - ...")
    assert argv[0] == "ssh"
    assert "monsterfish" in argv
    assert "BatchMode=yes" in argv
    assert argv[-1] == "ffmpeg -i - ..."


def test_remux_faststart_argv():
    argv = cb.remux_faststart_argv("/t/frag.mp4", "/t/out.mp4")
    assert argv[:3] == ["ffmpeg", "-hide_banner", "-v"]
    assert "-movflags" in argv and "+faststart" in argv
    assert argv[-1] == "/t/out.mp4"
    assert "/t/frag.mp4" in argv


def test_run_once_encodes_pending_and_records_state(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _touch(str(src / "2026-06-12/new.mp4"), content=b"abcd")
    cfg = {
        "source_path": str(src), "dest_path": str(dest), "max_attempts": 5,
        "workers": 1,
        "encode": {"ssh_host": "monsterfish", "scale_width": 1920,
                   "scale_height": 1080, "codec": "libx265", "crf": 26, "preset": "fast"},
        "client_monitor": {"enabled": False},
    }
    state_path = str(tmp_path / "state.json")

    def fake_encode_one(item, c, scratch):
        os.makedirs(os.path.dirname(item["dest"]), exist_ok=True)
        with open(item["dest"], "wb") as f:
            f.write(b"mini")
        return True, ""

    monkeypatch.setattr(cb, "encode_one", fake_encode_one)
    stats = cb.run_once(cfg, state_path)
    assert stats["ok"] == 1 and stats["err"] == 0
    assert os.path.exists(str(dest / "2026-06-12/new.mp4"))
    saved = cb.load_state(state_path)
    assert "2026-06-12/new.mp4" in saved["done"]


def test_run_once_increments_failures_on_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _touch(str(src / "2026-06-12/bad.mp4"), content=b"abcd")
    cfg = {
        "source_path": str(src), "dest_path": str(dest), "max_attempts": 5,
        "workers": 1,
        "encode": {"ssh_host": "monsterfish", "scale_width": 1920,
                   "scale_height": 1080, "codec": "libx265", "crf": 26, "preset": "fast"},
        "client_monitor": {"enabled": False},
    }
    state_path = str(tmp_path / "state.json")
    monkeypatch.setattr(cb, "encode_one", lambda i, c, s: (False, "boom"))
    stats = cb.run_once(cfg, state_path)
    assert stats["err"] == 1
    saved = cb.load_state(state_path)
    assert saved["failures"]["2026-06-12/bad.mp4"] == 1
    assert "2026-06-12/bad.mp4" not in saved["done"]


def test_run_once_dry_run_encodes_nothing(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _touch(str(src / "2026-06-12/new.mp4"), content=b"abcd")
    cfg = {
        "source_path": str(src), "dest_path": str(dest), "max_attempts": 5,
        "workers": 1,
        "encode": {"ssh_host": "monsterfish", "scale_width": 1920,
                   "scale_height": 1080, "codec": "libx265", "crf": 26, "preset": "fast"},
        "client_monitor": {"enabled": False},
    }
    called = {"n": 0}
    monkeypatch.setattr(cb, "encode_one", lambda i, c, s: called.__setitem__("n", called["n"] + 1) or (True, ""))
    stats = cb.run_once(cfg, str(tmp_path / "state.json"), dry_run=True)
    assert called["n"] == 0
    assert not os.path.exists(str(dest / "2026-06-12/new.mp4"))
