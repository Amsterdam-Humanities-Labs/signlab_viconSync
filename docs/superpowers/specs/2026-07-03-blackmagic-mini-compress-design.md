# Blackmagic "Mini" Compressor — Design

**Date:** 2026-07-03
**Status:** Awaiting review

## Problem

Blackmagic camera clips land on this box at:

```
/web/gebarenoverleg_media/studioFiles/blackmagic_files/<date>/*.mp4
```

They are **6K (6144×3456) H.264 @ ~100 Mbps, 30fps** — ~42 GB across 508 files today,
and growing as new recordings sync in (the sync itself is triggered on the Vicon PC via
the existing `BlackmagicManualSync` scheduled task; this project only reads the results).

We want a mechanism that **regularly checks for new Blackmagic files, compresses them,
and writes lightweight "Mini" copies** to:

```
/mnt/bigstorage/blackmagic_filesMini/<date>/<same-basename>.mp4
```

The full-resolution masters in `studioFiles` are never touched.

## Hard constraints

1. **Output path layout is fixed.** Each Mini must land at
   `blackmagic_filesMini/<date>/<same-basename>.mp4` — same date subfolder, same basename,
   `.mp4` container — to match an existing downstream lookup. Resolution/bitrate may change;
   path, basename, and container may not.
2. **Never expose a partial file** at the final path (a downstream consumer reads them).
3. **Source is a network/FUSE mount** and throws transient I/O errors — reads can fail and
   must be retried later, not treated as permanent failures.

## Compression profile

Default (the meaning of "Mini"):

| Setting     | Value                                  |
|-------------|----------------------------------------|
| Scale       | `6144×3456 → 1920×1080` (16:9, exact)  |
| Video codec | `libx265` (HEVC)                       |
| Quality     | `-crf 26 -preset fast`                 |
| Audio       | copy (fallback `aac -b:a 128k`)        |
| FPS         | 30 (unchanged)                         |
| Container   | `.mp4` with `+faststart`               |

Estimated result: ~42 GB → **~1.5 GB** total (>95% reduction). All of scale/CRF/preset are
config values, so switching to 4K UHD or full-6K re-encode is a one-line change.

**Decided:** 1080p is the target (full-res masters remain in `studioFiles`; the
`studioFilesMini` sibling establishes "Mini = lightweight copy").

## Architecture

Runs **once per day** as a `--once` scan, driven by a **systemd timer + oneshot service**
(`OnCalendar=*-*-* 04:00`, `Persistent=true` so a missed day catches up after downtime).
A timer fits a daily cadence better than a 24h-sleeping loop daemon and survives reboots
cleanly. The `--once` scan reuses the exact resumable/heartbeat logic the sibling
`glb_matcher.py` uses, just invoked by the timer instead of an internal sleep loop.

### Components

- **`compress_blackmagic.py`** — one clear job: keep `blackmagic_filesMini` in sync with
  `blackmagic_files` by producing Mini copies of any new/changed source clips. Runs one
  scan-and-encode pass per invocation (`--once`); a `--loop` mode is available for manual
  runs but the timer is the production driver.
- **`monitor_config.json` → `blackmagic_mini` section** — all configuration.
- **`vicon-blackmagic-mini.service`** (Type=oneshot) **+ `vicon-blackmagic-mini.timer`** —
  systemd units.
- **`blackmagic_compress_state.json`** — resumable state.
- **`logs/blackmagic_mini.log`** and a skip log for unreadable/failed sources.

### Config (`monitor_config.json`)

```json
"blackmagic_mini": {
  "enabled": true,
  "source_path": "/web/gebarenoverleg_media/studioFiles/blackmagic_files",
  "dest_path": "/mnt/bigstorage/blackmagic_filesMini",
  "state_file": "blackmagic_compress_state.json",
  "log_file": "logs/blackmagic_mini.log",
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
    "heartbeat_interval": 300
  }
}
```

`encode.ssh_host = "monsterfish"` (decided): the heavy 6K-decode + x265 encode runs on
monsterfish (24 cores) instead of this 4-core box, reusing the SSH-pipe approach proven in
`figshareZNN/compress_obs.py`. Leaving it blank falls back to a local encode.

### Remote encode + clean `.mp4` (the one subtlety)

The source lives on *this* box's network mount, which monsterfish can't see, so the source
is streamed to monsterfish over SSH **stdin** and the result comes back over **stdout** —
exactly `compress_obs.py`'s pattern. But a normal `.mp4` (moov atom at the end) can't be
written to a pipe, so the remote ffmpeg emits a **fragmented** mp4
(`-movflags +frag_keyframe+empty_moov+default_base_moof -f mp4 -`).

To keep the delivered file a clean, universally-seekable `.mp4` (it feeds a downstream
lookup), a **local stream-copy remux** finalizes it — negligible CPU, no re-encode:

```
ffmpeg -i <fragmented-tmp> -c copy -movflags +faststart <final-tmp>
```

Then `<final-tmp>` is atomically `os.replace`d into `dest/<date>/<name>.mp4`. Net effect:
monsterfish does 100% of the expensive work; this box only muxes and moves.

## Data flow (one `--once` scan)

```
for each <date>/<name>.mp4 under source_path:
    rel  = "<date>/<name>.mp4"
    dest = dest_path/<date>/<name>.mp4
    if state[rel] matches current (src_mtime, src_size) AND dest exists:  skip (done)
    if failures[rel] >= max_attempts:                                     skip (log, park)
    else: enqueue rel

for each queued rel (ThreadPoolExecutor, `workers`):
    frag = <scratch>/<uniq>.frag.mp4      # fragmented, from monsterfish
    ssh monsterfish 'ffmpeg -i - -vf scale=W:H -c:v libx265 -crf CRF -preset P
                     -c:a copy(||aac) -movflags +frag_keyframe+empty_moov -f mp4 -'  < src  > frag
    final = <scratch>/<uniq>.mp4          # local stream-copy remux to faststart
    ffmpeg -i frag -c copy -movflags +faststart final
    if ok:  mkdir -p dest_dir; os.replace(final, dest); state[rel] = {mtime,size}
    else:   failures[rel] += 1; log to skip log   # retried next scan until max_attempts

persist state; heartbeat; exit    # the systemd timer starts the next scan tomorrow
```

## Error handling

| Situation                          | Behaviour                                                        |
|------------------------------------|-----------------------------------------------------------------|
| Source read / I/O error (mount)    | ffmpeg fails → log, increment `failures[rel]`, retry next scan   |
| Zero-byte source                   | skip + log, retry next scan                                      |
| Encode failure (bad/corrupt clip)  | log, increment failures; parked after `max_attempts`            |
| SSH to monsterfish unreachable     | scan logs error and exits cleanly; nothing marked done → retried|
| Non-`.mp4` (e.g. stray `.braw`)    | ignored                                                         |
| Source changed after Mini made     | `(mtime,size)` mismatch → re-encoded                            |
| Partial/crashed encode             | temp files never moved into place; discarded                    |
| Overlapping runs                   | systemd oneshot won't start if the prior run is still active    |

Parked files (hit `max_attempts`) are logged and recoverable by clearing their state entry —
no silent data loss.

## Testing / verification

- `--dry-run` — print the plan (counts, GB in, sample paths), encode nothing.
- `--limit N` — encode N files only (smoke test).
- `--once` (default) — one scan then exit, driven by the timer; `--loop` for manual runs.
- Pre-flight SSH check to monsterfish before the scan (fail fast with a clear message).
- Post-encode assertions on sample outputs via `ffprobe`: width=1920, height=1080,
  `codec_name=hevc`, duration within ±0.1s of source, `+faststart` (moov before mdat), and
  path == `dest/<date>/<basename>.mp4`.

## Deliverables

1. `compress_blackmagic.py`
2. `blackmagic_mini` section added to `monitor_config.json`
3. `vicon-blackmagic-mini.service` (oneshot) + `vicon-blackmagic-mini.timer`
   (`OnCalendar=*-*-* 04:00`, `Persistent=true`) + install note in README/USAGE
4. `logs/blackmagic_mini.log` (created at runtime)

## Notes

- `viconSync` is **not** a git repo, so this design is not committed to version control;
  it lives in `docs/superpowers/specs/`.
- No change to the existing FTP-monitor / rsync / GLB-matcher services; this is additive.
