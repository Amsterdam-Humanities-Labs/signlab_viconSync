# signlab_viconSync
Pulls mocap recordings off the Vicon PC, files them on the core server, registers them in MySQL, and cleans the Vicon PC up.

## What it does
- `sync_vicon_rsync.py` (primary): SCP over SSH from `E:\Recordings` (raw, `*/*/unreal/`) and `D:\PostExports\FBX` into `/web/gebarenoverleg_media/fbx/` (FBX + GLB, size-compared).
- `ftp_monitor.py` + `db_writer.py`: full FTP scan at startup, then today's dir every 1.5 s; writes `vicon_captures`, `vicon_files`, `vicon_monitor_metadata` (and `monitor_output.json`).
- `glb_matcher.py`: pairs synced FBX with GLBs. `resync_fbx.py`: manual FTP re-download.
- `compress_blackmagic.py`: Blackmagic 6K clips to 1080p HEVC "Mini" copies, encoded on `monsterfish` over SSH.
- `cleanup_vicon.py`: deletes recordings from the Vicon PC once stored locally or on the research drive (rclone check).
- Control port `127.0.0.1:8765` (used by mocapStudio's `triggerSync.php`). Heartbeats go to the Client Monitor API.

## Where it runs
core server, `/home/gomer/viconSync`, as `gomer`. Not deployed by repos.tsv. Talks to the Vicon PC (Windows, tailnet) as a client only.

## Status
production

## How to run / deploy
Python 3, no venv; `requests`, `pymysql`; binaries `ssh`, `scp`, `sshpass`, `rsync`, `tailscale`, `ffmpeg`, `rclone`.

| Entry point | Scheduled by |
|---|---|
| `python3 sync_vicon_rsync.py [--dry-run]` | pythonCron, daily 02:30 |
| `python3 compress_blackmagic.py --once` (`--dry-run`, `--limit 2`) | `vicon-blackmagic-mini.timer`, daily 04:00 |
| `python3 ftp_monitor.py`, `python3 glb_matcher.py` | long-running |
| `python3 cleanup_vicon.py`, `python3 resync_fbx.py [--refresh-cache]` | manual |

pythonCron also schedules a weekly `cleanup_obs.py` that is not in this repo.
Schedules live in signlab_pythonCron, not here. Tests: `python3 -m pytest tests -q` (all remote calls mocked).

Database setup: `./setup_database.sh`, or `mysql admin_gebarenoverleg < create_tables.sql` then `add_glb_path_column.sql` (`create_tables.sql` lacks `glb_path`). Check with `python3 db_config.py` / `python3 db_writer.py`.

## Finding the Vicon PC
It gets a new tailnet node and `100.x` address after every reinstall, so nothing is configured. `vicon_host.py` picks the online `vicon*` peer with the newest suffix and probes the port:
```bash
python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22))"
```
Run this before any manual ssh; `ViconOffline` means the PC is down or off the tailnet.

## Configuration
- `monitor_config.json` (not in git): from `monitor_config.example.json`, fill `ftp.password` (or set `VICON_PASSWORD`). Also tunes monitor, matcher, `blackmagic_mini`.
- `/web/mysql_config.php` (server): parsed by `db_config.py` for MySQL credentials.
- Docroot paths (`/web/...`) resolve through vendored `sc_paths.py`: `SC_WEB_ROOT` env or in `$SC_ENV_FILE`/`/web/.env`, default `/web`. `VICON_BLACKMAGIC_STATUS_PATH` overrides the Vicon PC status-file path. Edit `sc_paths.py` in signlab_signcollect-lib, not here.
- State: `monitor_state.json`, `blackmagic_compress_state.json` (delete an entry to force re-encode, clear `failures` to retry a parked clip).
- Logs: `logs/sync_vicon_rsync.log`, `logs/ftp_monitor.log`, `logs/glb_matcher.log`, `logs/blackmagic_mini*.log`.
- `python_client.py` is a vendored copy of `signlab_client_monitor_api/client`; imports prefer the package. Do not edit it.

## Dependencies
- Vicon PC (SSH + FTP on the tailnet), Tailscale on the server, `monsterfish` GPU box.
- MySQL `admin_gebarenoverleg`; signlab_viconDashboard and sC-Animation-PP read the rows.
- signlab_pythonCron (scheduling); Client Monitor API `https://signcollect.nl/client_monitor_api/api.php` (optional).
- Diagram: `docs/pipeline_overview.html`. Design notes: `docs/superpowers/specs/`.
