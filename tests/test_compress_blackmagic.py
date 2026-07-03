import json
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
