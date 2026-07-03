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
