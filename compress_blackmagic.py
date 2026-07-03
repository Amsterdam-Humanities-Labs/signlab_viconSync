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
import subprocess
import time

logger = logging.getLogger("blackmagic_mini")

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
    "-o", "ControlMaster=auto",
    "-o", "ControlPath=/tmp/bm_mini_ssh_%C",
    "-o", "ControlPersist=300",
    "-o", "ServerAliveInterval=30",
]


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
