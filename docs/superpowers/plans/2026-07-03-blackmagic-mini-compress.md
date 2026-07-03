# Blackmagic "Mini" Compressor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily job that finds new Blackmagic 6K clips, compresses them to 1080p HEVC "Mini" copies on monsterfish, and writes them to `/mnt/bigstorage/blackmagic_filesMini/<date>/<name>.mp4`.

**Architecture:** A single Python script (`compress_blackmagic.py`) with small pure functions (source discovery, path mapping, plan building, command construction) plus an I/O layer (SSH-pipe encode on monsterfish → local faststart remux → atomic move). Driven once per day by a systemd timer + oneshot service. Resumable via a JSON state file. Mirrors the existing `figshareZNN/compress_obs.py` encode pattern and the `glb_matcher.py` client-monitor heartbeat pattern.

**Tech Stack:** Python 3.12, `ffmpeg`/`libx265` (local + monsterfish), SSH with ControlMaster multiplexing, `python_client.ClientMonitor`, pytest (pure-logic tests only), systemd timer.

## Global Constraints

- **Output path is fixed:** every Mini lands at exactly `<dest_root>/<date>/<same-basename>.mp4` — same date subfolder, same basename, `.mp4` container. Copied verbatim from spec.
- **Never expose a partial file** at the final path — encode to a temp file on the *same filesystem* as the destination, then `os.replace` (atomic).
- **Source is a flaky network mount** — any read/ffmpeg failure is logged and retried on the next run, never treated as permanent (until `max_attempts`).
- **Encode runs on monsterfish** (`ssh_host` from config), key-based SSH / `BatchMode=yes`, no password.
- **Compression profile (config-driven):** `scale=1920:1080`, `libx265 -crf 26 -preset fast`, audio copy with AAC fallback, `.mp4` with `+faststart`.
- **viconSync is NOT a git repository.** The template's "Commit" step is therefore replaced in every task by "run the full test suite" as the checkpoint. (If you want VCS, run `git init` in `/home/gomer/viconSync` first and re-enable commits.)
- **pytest is not installed** — Task 1 installs it (`python3 -m pip install --user pytest`). Run tests with `python3 -m pytest`.
- All paths below are absolute and rooted at `/home/gomer/viconSync`.

---

## File Structure

- Create: `/home/gomer/viconSync/compress_blackmagic.py` — the whole feature (discovery, plan, encode, orchestration, CLI).
- Modify: `/home/gomer/viconSync/monitor_config.json` — add `blackmagic_mini` section.
- Create: `/home/gomer/viconSync/tests/test_compress_blackmagic.py` — pytest for pure functions.
- Create: `/home/gomer/viconSync/vicon-blackmagic-mini.service` — systemd oneshot unit.
- Create: `/home/gomer/viconSync/vicon-blackmagic-mini.timer` — systemd daily timer.
- Modify: `/home/gomer/viconSync/USAGE.md` — install/operate note.
- Runtime (not created by hand): `/home/gomer/viconSync/blackmagic_compress_state.json`, `/home/gomer/viconSync/logs/blackmagic_mini.log`, `/home/gomer/viconSync/logs/blackmagic_mini_skipped.log`.

**State file schema** (`blackmagic_compress_state.json`):
```json
{
  "done":     { "<date>/<name>.mp4": [<src_mtime_float>, <src_size_int>] },
  "failures": { "<date>/<name>.mp4": <attempt_count_int> }
}
```

---

## Task 1: Config section + module skeleton + state persistence

**Files:**
- Modify: `/home/gomer/viconSync/monitor_config.json`
- Create: `/home/gomer/viconSync/compress_blackmagic.py`
- Test: `/home/gomer/viconSync/tests/test_compress_blackmagic.py`

**Interfaces:**
- Produces: `load_state(path: str) -> dict` (always returns `{"done": {}, "failures": {}}` shape), `save_state(path: str, state: dict) -> None` (atomic via temp+replace), `load_config(config_path: str) -> dict` (returns the `blackmagic_mini` sub-dict).

- [ ] **Step 1: Install pytest**

Run: `python3 -m pip install --user pytest`
Expected: ends with `Successfully installed ... pytest-...` (or "already satisfied").

- [ ] **Step 2: Add the `blackmagic_mini` config section**

In `/home/gomer/viconSync/monitor_config.json`, add this key as a new top-level sibling of `glb_matcher` (mind the trailing comma on the preceding `}`):

```json
  "blackmagic_mini": {
    "enabled": true,
    "source_path": "/web/gebarenoverleg_media/studioFiles/blackmagic_files",
    "dest_path": "/mnt/bigstorage/blackmagic_filesMini",
    "state_file": "blackmagic_compress_state.json",
    "log_file": "logs/blackmagic_mini.log",
    "skip_log": "logs/blackmagic_mini_skipped.log",
    "workers": 3,
    "max_attempts": 5,
    "encode": {
      "ssh_host": "monsterfish",
      "scale_width": 1920,
      "scale_height": 1080,
      "codec": "libx265",
      "crf": 26,
      "preset": "fast"
    },
    "client_monitor": {
      "enabled": true,
      "api_url": "https://signcollect.nl/client_monitor_api/api.php",
      "client_id": "vicon-blackmagic-mini",
      "client_name": "Vicon Blackmagic Mini Compressor",
      "description": "Compresses Blackmagic 6K clips to 1080p Mini copies",
      "heartbeat_interval": 86400
    }
  }
```

Verify it parses: `python3 -c "import json; json.load(open('/home/gomer/viconSync/monitor_config.json')); print('ok')"` → `ok`.

- [ ] **Step 3: Write the failing test**

Create `/home/gomer/viconSync/tests/test_compress_blackmagic.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd /home/gomer/viconSync && python3 -m pytest tests/test_compress_blackmagic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'compress_blackmagic'`.

- [ ] **Step 5: Create the module skeleton with state + config helpers**

Create `/home/gomer/viconSync/compress_blackmagic.py`:

```python
#!/usr/bin/env python3
"""Compress new Blackmagic 6K clips to 1080p HEVC "Mini" copies.

Source clips live at:
    <source_path>/<date>/<name>.mp4   (6144x3456 H.264 ~100 Mbps)
Mini copies are written to:
    <dest_path>/<date>/<name>.mp4     (1920x1080 HEVC, faststart)

The heavy encode runs on monsterfish over an SSH pipe (source streamed via
stdin, fragmented mp4 back via stdout); a local stream-copy remux restores a
clean +faststart mp4, then the result is atomically moved into place.

Config lives in monitor_config.json -> "blackmagic_mini".
Resumable via blackmagic_compress_state.json. Runs one scan per invocation
(--once, the default, driven by the systemd timer); --loop is for manual use.
"""
import argparse
import json
import logging
import os

logger = logging.getLogger("blackmagic_mini")


def load_config(config_path):
    """Return the 'blackmagic_mini' sub-dict from monitor_config.json."""
    with open(config_path) as f:
        return json.load(f)["blackmagic_mini"]


def load_state(path):
    """Return {'done': {...}, 'failures': {...}}; empty shape if file absent."""
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        data.setdefault("done", {})
        data.setdefault("failures", {})
        return data
    return {"done": {}, "failures": {}}


def save_state(path, state):
    """Atomically write state (temp file in the same dir, then os.replace)."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=0, sort_keys=True)
    os.replace(tmp, path)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /home/gomer/viconSync && python3 -m pytest tests/test_compress_blackmagic.py -v`
Expected: 3 passed.

- [ ] **Step 7: Checkpoint** — full suite green: `cd /home/gomer/viconSync && python3 -m pytest -v`. Expected: 3 passed.

---

## Task 2: Source discovery, path mapping, source signature

**Files:**
- Modify: `/home/gomer/viconSync/compress_blackmagic.py`
- Test: `/home/gomer/viconSync/tests/test_compress_blackmagic.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `iter_source_clips(source_path: str) -> list[str]` — sorted relative paths (`"<date>/<name>.mp4"`) of every `*.mp4` (case-insensitive) under `source_path`; skips all non-`.mp4` files.
  - `dest_path_for(rel: str, dest_root: str) -> str` — `os.path.join(dest_root, rel)`.
  - `source_sig(abs_path: str) -> tuple[float, int] | None` — `(mtime, size)`; `None` if unreadable (`OSError`) or zero-byte.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_compress_blackmagic.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/gomer/viconSync && python3 -m pytest tests/test_compress_blackmagic.py -k "iter_source or dest_path or source_sig" -v`
Expected: FAIL — `AttributeError: module 'compress_blackmagic' has no attribute 'iter_source_clips'`.

- [ ] **Step 3: Implement discovery/mapping/signature**

Append to `compress_blackmagic.py`:

```python
def iter_source_clips(source_path):
    """Sorted list of '<date>/<name>.mp4' relative paths under source_path."""
    rels = []
    for dirpath, _dirs, files in os.walk(source_path):
        for name in files:
            if name.lower().endswith(".mp4"):
                abspath = os.path.join(dirpath, name)
                rels.append(os.path.relpath(abspath, source_path))
    return sorted(rels)


def dest_path_for(rel, dest_root):
    return os.path.join(dest_root, rel)


def source_sig(abs_path):
    """(mtime, size) or None if missing/unreadable/zero-byte."""
    try:
        st = os.stat(abs_path)
    except OSError:
        return None
    if st.st_size <= 0:
        return None
    return (st.st_mtime, st.st_size)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/gomer/viconSync && python3 -m pytest tests/test_compress_blackmagic.py -v`
Expected: 8 passed.

- [ ] **Step 5: Checkpoint** — `cd /home/gomer/viconSync && python3 -m pytest -v` → 8 passed.

---

## Task 3: Plan building (done / parked / to-encode)

**Files:**
- Modify: `/home/gomer/viconSync/compress_blackmagic.py`
- Test: `/home/gomer/viconSync/tests/test_compress_blackmagic.py`

**Interfaces:**
- Consumes: `iter_source_clips`, `dest_path_for`, `source_sig` (Task 2); state shape (Task 1).
- Produces:
  - `is_done(rel, sig, dest_root, state) -> bool` — `True` iff `state["done"].get(rel) == [sig[0], sig[1]]` **and** the dest file exists.
  - `build_plan(cfg, state) -> dict` — returns
    `{"items": [ {"rel", "src", "dest"} ... ], "n_done": int, "n_parked": int, "n_unreadable": int}`.
    A clip is: **done** (skip) if `is_done`; **parked** (skip) if `state["failures"].get(rel, 0) >= cfg["max_attempts"]`; **unreadable** (skip) if `source_sig` is `None`; otherwise an **item** to encode. Items sorted by `rel`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_compress_blackmagic.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/gomer/viconSync && python3 -m pytest tests/test_compress_blackmagic.py -k "is_done or build_plan" -v`
Expected: FAIL — `AttributeError: ... 'is_done'`.

- [ ] **Step 3: Implement plan building**

Append to `compress_blackmagic.py`:

```python
def is_done(rel, sig, dest_root, state):
    recorded = state["done"].get(rel)
    if recorded is None or list(recorded) != [sig[0], sig[1]]:
        return False
    return os.path.exists(dest_path_for(rel, dest_root))


def build_plan(cfg, state):
    src_root = cfg["source_path"]
    dest_root = cfg["dest_path"]
    max_attempts = cfg["max_attempts"]
    items, n_done, n_parked, n_unreadable = [], 0, 0, 0
    for rel in iter_source_clips(src_root):
        src = os.path.join(src_root, rel)
        sig = source_sig(src)
        if sig is None:
            n_unreadable += 1
            continue
        if is_done(rel, sig, dest_root, state):
            n_done += 1
            continue
        if state["failures"].get(rel, 0) >= max_attempts:
            n_parked += 1
            continue
        items.append({"rel": rel, "src": src, "dest": dest_path_for(rel, dest_root)})
    return {"items": items, "n_done": n_done, "n_parked": n_parked,
            "n_unreadable": n_unreadable}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/gomer/viconSync && python3 -m pytest tests/test_compress_blackmagic.py -v`
Expected: 13 passed.

- [ ] **Step 5: Checkpoint** — `cd /home/gomer/viconSync && python3 -m pytest -v` → 13 passed.

---

## Task 4: Encode command construction + `encode_one`

**Files:**
- Modify: `/home/gomer/viconSync/compress_blackmagic.py`
- Test: `/home/gomer/viconSync/tests/test_compress_blackmagic.py`

**Interfaces:**
- Consumes: `cfg["encode"]` (Task 1 config).
- Produces:
  - `remote_ffmpeg_cmd(cfg, audio_args: list[str]) -> str` — the ffmpeg command string that runs on monsterfish, reading `-` (stdin) and writing fragmented mp4 to `-` (stdout). `audio_args` is either `["-c:a", "copy"]` or `["-c:a", "aac", "-b:a", "128k"]`.
  - `ssh_argv(cfg, remote_cmd: str) -> list[str]` — `["ssh", <opts...>, ssh_host, remote_cmd]`.
  - `remux_faststart_argv(frag_path: str, out_path: str) -> list[str]` — local `ffmpeg -c copy -movflags +faststart`.
  - `encode_one(item: dict, cfg: dict, scratch_dir: str) -> tuple[bool, str]` — full pipeline for one clip, atomic move into `item["dest"]`. Returns `(ok, err_message)`.

- [ ] **Step 1: Write the failing tests** (string-builders only — no ffmpeg invoked)

Append to `tests/test_compress_blackmagic.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/gomer/viconSync && python3 -m pytest tests/test_compress_blackmagic.py -k "remote_ffmpeg or ssh_argv or remux" -v`
Expected: FAIL — `AttributeError: ... 'remote_ffmpeg_cmd'`.

- [ ] **Step 3: Implement command builders + `encode_one`**

Add near the top of `compress_blackmagic.py` (after the imports):

```python
import subprocess
import time

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
    "-o", "ControlMaster=auto",
    "-o", "ControlPath=/tmp/bm_mini_ssh_%C",
    "-o", "ControlPersist=300",
    "-o", "ServerAliveInterval=30",
]
```

Append the builders and pipeline:

```python
def remote_ffmpeg_cmd(cfg, audio_args):
    """ffmpeg command (runs on monsterfish): stdin -> 1080p HEVC -> fragmented
    mp4 on stdout. Fragmented movflags are required to stream mp4 over a pipe."""
    e = cfg["encode"]
    scale = f"scale={e['scale_width']}:{e['scale_height']}"
    parts = [
        "ffmpeg", "-hide_banner", "-v", "error", "-i", "-",
        "-vf", scale,
        "-c:v", e["codec"], "-crf", str(e["crf"]), "-preset", e["preset"],
        *audio_args,
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "-",
    ]
    return " ".join(parts)


def ssh_argv(cfg, remote_cmd):
    return ["ssh", *SSH_OPTS, cfg["encode"]["ssh_host"], remote_cmd]


def remux_faststart_argv(frag_path, out_path):
    """Local stream-copy remux to restore a normal +faststart mp4 (no re-encode)."""
    return ["ffmpeg", "-hide_banner", "-v", "error", "-y",
            "-i", frag_path, "-c", "copy", "-movflags", "+faststart", out_path]


def _run_remote_encode(src, frag_out, cfg, audio_args):
    """Stream src into monsterfish ffmpeg, capture fragmented mp4 to frag_out."""
    argv = ssh_argv(cfg, remote_ffmpeg_cmd(cfg, audio_args))
    with open(src, "rb") as fin, open(frag_out, "wb") as fout:
        r = subprocess.run(argv, stdin=fin, stdout=fout,
                           stderr=subprocess.PIPE, timeout=1800)
    ok = r.returncode == 0 and os.path.isfile(frag_out) and os.path.getsize(frag_out) > 0
    err = "" if ok else (r.stderr.decode("utf-8", "replace")[:300] if r.stderr else "no output")
    return ok, err


def _uniq(rel):
    return rel.replace("/", "_").replace(" ", "_")


def encode_one(item, cfg, scratch_dir):
    """Encode one clip on monsterfish, remux to faststart locally, atomically
    move into item['dest']. scratch_dir MUST be on the same filesystem as dest
    so the final os.replace is atomic. Returns (ok, err)."""
    src, dest = item["src"], item["dest"]
    base = _uniq(item["rel"])
    frag = os.path.join(scratch_dir, base + ".frag.mp4")
    final = os.path.join(scratch_dir, base + ".mp4")
    try:
        ok, err = _run_remote_encode(src, frag, cfg, ["-c:a", "copy"])
        if not ok:
            # Blackmagic clips are often PCM audio, which mp4 can't copy — re-encode.
            ok, err = _run_remote_encode(src, frag, cfg, ["-c:a", "aac", "-b:a", "128k"])
            if not ok:
                return False, "remote-encode: " + err
        r = subprocess.run(remux_faststart_argv(frag, final),
                           stderr=subprocess.PIPE, timeout=300)
        if r.returncode != 0 or not os.path.isfile(final) or os.path.getsize(final) == 0:
            return False, "remux: " + (r.stderr.decode("utf-8", "replace")[:300] if r.stderr else "")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.replace(final, dest)
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:  # noqa: BLE001 - report any failure, retry next run
        return False, repr(exc)
    finally:
        for p in (frag, final):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/gomer/viconSync && python3 -m pytest tests/test_compress_blackmagic.py -v`
Expected: 17 passed.

- [ ] **Step 5: Checkpoint** — `cd /home/gomer/viconSync && python3 -m pytest -v` → 17 passed.

---

## Task 5: Orchestration (`run_once`), CLI, heartbeat + real smoke test

**Files:**
- Modify: `/home/gomer/viconSync/compress_blackmagic.py`
- Test: `/home/gomer/viconSync/tests/test_compress_blackmagic.py`

**Interfaces:**
- Consumes: everything above; `python_client.ClientMonitor`.
- Produces:
  - `scratch_dir_for(cfg) -> str` — `<dest_path>/.blackmagic_mini_tmp` (created if missing; same FS as dest → atomic move).
  - `preflight_ssh(cfg) -> tuple[bool, str]` — runs `ssh <opts> <host> "ffmpeg -version"`; returns `(ok, msg)`.
  - `run_once(cfg, state_path, limit=None, dry_run=False) -> dict` — builds plan, (unless dry-run) encodes items with a `ThreadPoolExecutor(cfg["workers"])`, updates + saves state and the failure counters, best-effort heartbeat. Returns a stats dict `{"ok", "err", "n_done", "n_parked", "n_unreadable", "attempted"}`.
  - `main(argv=None) -> int` — argparse: `--config` (default `monitor_config.json`), `--dry-run`, `--limit N`, `--once` (default), `--loop`, `--interval` (seconds, for `--loop`; default 86400).

- [ ] **Step 1: Write the failing test** (orchestration with encode monkeypatched — no ffmpeg)

Append to `tests/test_compress_blackmagic.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/gomer/viconSync && python3 -m pytest tests/test_compress_blackmagic.py -k "run_once" -v`
Expected: FAIL — `AttributeError: ... 'run_once'`.

- [ ] **Step 3: Implement orchestration + CLI**

Add `from concurrent.futures import ThreadPoolExecutor, as_completed` and `import threading` to the imports. Append:

```python
def scratch_dir_for(cfg):
    d = os.path.join(cfg["dest_path"], ".blackmagic_mini_tmp")
    os.makedirs(d, exist_ok=True)
    return d


def preflight_ssh(cfg):
    host = cfg["encode"]["ssh_host"]
    if not host:
        return True, "local encode (no ssh_host)"
    try:
        r = subprocess.run(["ssh", *SSH_OPTS, host, "ffmpeg -version"],
                           capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return False, f"ssh {host}: {exc!r}"
    if r.returncode != 0:
        return False, f"ssh {host} failed: {r.stderr[:200]}"
    return True, f"ssh {host} ok"


def _make_monitor(cfg):
    cm = cfg.get("client_monitor", {})
    if not cm.get("enabled"):
        return None
    try:
        from python_client import ClientMonitor
        return ClientMonitor(
            api_url=cm["api_url"], client_id=cm["client_id"],
            client_name=cm["client_name"], description=cm.get("description", ""),
            heartbeat_interval=cm.get("heartbeat_interval", 86400))
    except Exception as exc:  # noqa: BLE001 - monitoring is best-effort
        logger.warning("client_monitor unavailable: %r", exc)
        return None


def run_once(cfg, state_path, limit=None, dry_run=False):
    state = load_state(state_path)
    plan = build_plan(cfg, state)
    items = plan["items"][:limit] if limit else plan["items"]
    logger.info("plan: %d to encode, %d done, %d parked, %d unreadable",
                len(items), plan["n_done"], plan["n_parked"], plan["n_unreadable"])
    stats = {"ok": 0, "err": 0, "n_done": plan["n_done"], "n_parked": plan["n_parked"],
             "n_unreadable": plan["n_unreadable"], "attempted": len(items)}
    if dry_run or not items:
        for it in items:
            logger.info("[DRY] would encode %s", it["rel"])
        return stats

    ok, msg = preflight_ssh(cfg)
    if not ok:
        logger.error("preflight failed: %s", msg)
        return stats

    scratch = scratch_dir_for(cfg)
    lock = threading.Lock()

    def work(it):
        good, err = encode_one(it, cfg, scratch)
        with lock:
            if good:
                sig = source_sig(it["src"])
                if sig is not None:
                    state["done"][it["rel"]] = [sig[0], sig[1]]
                state["failures"].pop(it["rel"], None)
                stats["ok"] += 1
                logger.info("OK  %s", it["rel"])
            else:
                state["failures"][it["rel"]] = state["failures"].get(it["rel"], 0) + 1
                stats["err"] += 1
                logger.warning("FAIL %s (attempt %d): %s",
                               it["rel"], state["failures"][it["rel"]], err)
                _log_skip(cfg, it["rel"], err)
            save_state(state_path, state)

    with ThreadPoolExecutor(max_workers=cfg.get("workers", 3)) as ex:
        for _ in as_completed([ex.submit(work, it) for it in items]):
            pass
    save_state(state_path, state)

    monitor = _make_monitor(cfg)
    if monitor is not None:
        try:
            monitor.register()
            status = "success" if stats["err"] == 0 else "warning"
            monitor.send_heartbeat_with_stats(
                status, f"encoded {stats['ok']}, failed {stats['err']}", stats)
        except Exception as exc:  # noqa: BLE001
            logger.warning("heartbeat failed: %r", exc)
    return stats


def _log_skip(cfg, rel, err):
    path = cfg.get("skip_log")
    if not path:
        return
    try:
        with open(path, "a") as f:
            f.write(f"{rel}\t{err}\n")
    except OSError:
        pass


def _setup_logging(cfg):
    handlers = [logging.StreamHandler()]
    logf = cfg.get("log_file")
    if logf:
        os.makedirs(os.path.dirname(logf), exist_ok=True)
        handlers.append(logging.FileHandler(logf))
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compress Blackmagic clips to 1080p Mini copies")
    ap.add_argument("--config", default="monitor_config.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--loop", action="store_true", help="run forever, sleeping --interval between scans")
    ap.add_argument("--interval", type=int, default=86400)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    _setup_logging(cfg)
    if not cfg.get("enabled", True):
        logger.info("blackmagic_mini disabled in config; exiting")
        return 0
    state_path = cfg["state_file"]

    if not args.loop:
        run_once(cfg, state_path, limit=args.limit, dry_run=args.dry_run)
        return 0
    while True:
        try:
            run_once(cfg, state_path, limit=args.limit, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 - never let the loop die
            logger.exception("run_once crashed: %r", exc)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run unit tests to verify pass**

Run: `cd /home/gomer/viconSync && python3 -m pytest tests/test_compress_blackmagic.py -v`
Expected: 20 passed.

- [ ] **Step 5: Dry-run against the real source**

Run: `cd /home/gomer/viconSync && python3 compress_blackmagic.py --dry-run`
Expected: a log line like `plan: N to encode, 0 done, 0 parked, M unreadable` with N > 0 (there are 508 source clips today). No files written to `/mnt/bigstorage/blackmagic_filesMini`.

- [ ] **Step 6: Real smoke test — encode 2 clips on monsterfish**

Run: `cd /home/gomer/viconSync && python3 compress_blackmagic.py --limit 2`
Expected: two `OK <date>/<name>.mp4` log lines, `stats ok=2`.

Verify the outputs are correct 1080p HEVC faststart mp4s at the right paths:

```bash
find /mnt/bigstorage/blackmagic_filesMini -name '*.mp4' | head -2 | while read f; do
  echo "== $f"
  ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height,codec_name -of default=noprint_wrappers=1 "$f"
  # faststart => moov appears before mdat
  python3 - "$f" <<'PY'
import sys
d=open(sys.argv[1],'rb').read(200000)
print("faststart:", d.find(b'moov') < d.find(b'mdat'))
PY
done
```

Expected per file: `width=1920`, `height=1080`, `codec_name=hevc`, `faststart: True`, and the path is `.../blackmagic_filesMini/<date>/<same-basename>.mp4`.

- [ ] **Step 7: Idempotency check**

Run `python3 compress_blackmagic.py --dry-run` again.
Expected: `n_done` increased by 2 and those two clips are no longer in the "to encode" count (they won't be re-encoded).

- [ ] **Step 8: Checkpoint** — `cd /home/gomer/viconSync && python3 -m pytest -v` → 20 passed, plus the smoke evidence above.

---

## Task 6: systemd timer + service + operator docs

**Files:**
- Create: `/home/gomer/viconSync/vicon-blackmagic-mini.service`
- Create: `/home/gomer/viconSync/vicon-blackmagic-mini.timer`
- Modify: `/home/gomer/viconSync/USAGE.md`

**Interfaces:** none (deployment only).

- [ ] **Step 1: Determine the run user + python path**

Run: `systemctl show vicon-glb-matcher.service -p User -p ExecStart`
Expected: note the `User=` and the python interpreter path used by the sibling service; reuse the same values in Step 2 (this keeps the new job consistent with the existing ones).

- [ ] **Step 2: Create the oneshot service unit**

Create `/home/gomer/viconSync/vicon-blackmagic-mini.service` (substitute `<USER>` and `<PYTHON>` from Step 1; `<PYTHON>` is typically `/usr/bin/python3`):

```ini
[Unit]
Description=Vicon Blackmagic Mini Compressor
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=<USER>
WorkingDirectory=/home/gomer/viconSync
ExecStart=<PYTHON> /home/gomer/viconSync/compress_blackmagic.py --once
# One daily encode of the whole backlog can be long; don't let systemd kill it.
TimeoutStartSec=0
```

- [ ] **Step 3: Create the daily timer unit**

Create `/home/gomer/viconSync/vicon-blackmagic-mini.timer`:

```ini
[Unit]
Description=Run Vicon Blackmagic Mini Compressor daily

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Install and enable**

```bash
sudo cp /home/gomer/viconSync/vicon-blackmagic-mini.service /etc/systemd/system/
sudo cp /home/gomer/viconSync/vicon-blackmagic-mini.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vicon-blackmagic-mini.timer
```

- [ ] **Step 5: Verify the timer is scheduled**

Run: `systemctl list-timers vicon-blackmagic-mini.timer --all`
Expected: one row showing the next 04:00 activation and `vicon-blackmagic-mini.service` as UNIT.

- [ ] **Step 6: Verify a manual service run works end-to-end**

Run: `sudo systemctl start vicon-blackmagic-mini.service && journalctl -u vicon-blackmagic-mini.service -n 20 --no-pager`
Expected: log shows a `plan:` line and (if new clips exist) `OK ...` lines; the unit finishes `Deactivated successfully` (oneshot success).

- [ ] **Step 7: Document it in USAGE.md**

Append a section to `/home/gomer/viconSync/USAGE.md`:

```markdown
## Blackmagic Mini Compressor

Daily job that compresses new Blackmagic 6K clips
(`/web/gebarenoverleg_media/studioFiles/blackmagic_files/<date>/*.mp4`) to 1080p
HEVC "Mini" copies at `/mnt/bigstorage/blackmagic_filesMini/<date>/<name>.mp4`.
The encode runs on monsterfish over SSH; the source masters are never modified.

- Config: `monitor_config.json` → `blackmagic_mini`.
- Schedule: `vicon-blackmagic-mini.timer` (daily 04:00, `Persistent=true`).
- Manual run:   `python3 compress_blackmagic.py --once`
- Preview plan: `python3 compress_blackmagic.py --dry-run`
- Smoke test:   `python3 compress_blackmagic.py --limit 2`
- State: `blackmagic_compress_state.json` (delete an entry to force re-encode).
- Logs: `logs/blackmagic_mini.log`, failures in `logs/blackmagic_mini_skipped.log`.
- A clip that fails `max_attempts` (default 5) times is parked; clear its
  `failures` entry in the state file to retry.
```

- [ ] **Step 8: Checkpoint** — `systemctl list-timers vicon-blackmagic-mini.timer --all` shows the scheduled timer and `python3 -m pytest -v` still passes (20).

---

## Self-Review Notes

- **Spec coverage:** source discovery + date-subfolder output mapping (T2), 1080p/x265/faststart profile (T4), monsterfish SSH-pipe + fragmented-mp4 → local faststart remux (T4), atomic same-FS move (T4/T5), resumable state + `(mtime,size)` re-encode trigger (T1/T3/T5), flaky-mount retry + `max_attempts` parking (T3/T5), non-mp4 ignored (T2), client-monitor heartbeat (T5), daily timer + oneshot (T6), `--dry-run`/`--limit`/`--once`/`--loop` (T5), ffprobe verification (T5). All spec sections map to a task.
- **No placeholders:** every code/test step contains complete code and exact commands.
- **Type consistency:** `build_plan` item keys `rel/src/dest` are consumed unchanged by `encode_one`/`run_once`; state schema `{"done","failures"}` is identical across T1/T3/T5; `encode_one(item,cfg,scratch)` signature matches its call site and the monkeypatch in tests.
