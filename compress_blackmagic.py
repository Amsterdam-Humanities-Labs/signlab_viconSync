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
import hashlib
import json
import logging
import logging.handlers
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("blackmagic_mini")

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
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
    h = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]
    safe = rel.replace("/", "_").replace(" ", "_")
    return f"{safe}.{h}"


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
        try:
            from signlab_client_monitor import ClientMonitor
        except ImportError:
            from python_client import ClientMonitor
        return ClientMonitor(
            api_url=cm["api_url"], client_id=cm["client_id"],
            client_name=cm["client_name"], description=cm.get("description", ""),
            heartbeat_interval=cm.get("heartbeat_interval", 86400))
    except Exception as exc:  # noqa: BLE001 - monitoring is best-effort
        logger.warning("client_monitor unavailable: %r", exc)
        return None


def _emit_heartbeat(cfg, status, message, stats):
    monitor = _make_monitor(cfg)
    if monitor is None:
        return
    try:
        monitor.register()
        monitor.send_heartbeat_with_stats(status, message, stats)
    except Exception as exc:  # noqa: BLE001 - monitoring is best-effort
        logger.warning("heartbeat failed: %r", exc)


def run_once(cfg, state_path, limit=None, dry_run=False):
    state = load_state(state_path)
    plan = build_plan(cfg, state)
    items = plan["items"][:limit] if limit else plan["items"]
    logger.info("plan: %d to encode, %d done, %d parked, %d unreadable",
                len(items), plan["n_done"], plan["n_parked"], plan["n_unreadable"])
    stats = {"ok": 0, "err": 0, "n_done": plan["n_done"], "n_parked": plan["n_parked"],
             "n_unreadable": plan["n_unreadable"], "attempted": len(items)}
    if dry_run:
        for it in items:
            logger.info("[DRY] would encode %s", it["rel"])
        return stats

    status, message = "success", "no new clips"
    try:
        if items:
            ok, msg = preflight_ssh(cfg)
            if not ok:
                logger.error("preflight failed: %s", msg)
                status, message = "error", f"preflight failed: {msg}"
                return stats

            scratch = scratch_dir_for(cfg)
            lock = threading.Lock()

            def work(it):
                good, err = encode_one(it, cfg, scratch)
                sig = source_sig(it["src"]) if good else None
                with lock:
                    if good:
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

            if stats["err"]:
                status = "warning"
            message = f"encoded {stats['ok']}, failed {stats['err']}"
    finally:
        _emit_heartbeat(cfg, status, message, stats)
    return stats


# 5 MB x 5, the same policy as setup_rotating_logger in the heartbeat client.
# This script only imports that client lazily for the heartbeat, so logging
# uses the stdlib handler directly rather than depending on it.
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def _rotating_handler(path):
    return logging.handlers.RotatingFileHandler(
        path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)


def _skip_logger(path):
    """A logger that appends raw `rel<TAB>err` lines to `path`, rotated."""
    skip = logging.getLogger("blackmagic_mini.skips." + path)
    if not skip.handlers:
        handler = _rotating_handler(path)
        handler.setFormatter(logging.Formatter("%(message)s"))
        skip.addHandler(handler)
        skip.setLevel(logging.INFO)
        skip.propagate = False
    return skip


def _log_skip(cfg, rel, err):
    path = cfg.get("skip_log")
    if not path:
        return
    try:
        _skip_logger(path).info("%s\t%s", rel, err)
    except OSError:
        pass


def _setup_logging(cfg):
    handlers = [logging.StreamHandler()]
    logf = cfg.get("log_file")
    if logf:
        os.makedirs(os.path.dirname(logf), exist_ok=True)
        handlers.append(_rotating_handler(logf))
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compress Blackmagic clips to 1080p Mini copies")
    ap.add_argument("--config", default="monitor_config.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--once", action="store_true",
                    help="run a single scan then exit (the default; explicit for the systemd unit)")
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
