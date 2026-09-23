# signlab_viconSync
Copies mocap recordings from the Vicon PC to the core server, registers them in MySQL and cleans up the Vicon PC.

## What it does
- `sync_vicon_files.py` (main job; `sync_vicon_rsync.py` is a stub with its old name): copies FBX and GLB files over SSH (SCP) into `/web/gebarenoverleg_media/fbx/`. Sources: `E:\Recordings` (raw, `*/*/unreal/`) and `D:\PostExports\FBX`. It compares files by size. From `E:\Recordings` it also copies LiveLink CSVs to `llcsv/`, `metadata/*.json` to `metadata/` and Shogun captures to `shogun_live/`. CC exports also go through `cc_pipeline/convert_one.sh`.
- `ftp_monitor.py` + `db_writer.py`: scan FTP fully at startup, then poll today's folder every `monitoring.poll_interval` seconds (1.5 in the example config). They write `vicon_captures`, `vicon_files`, `vicon_monitor_metadata` and `monitor_output.json`.
- `glb_matcher.py` pairs synced FBX files with GLBs. `resync_fbx.py` downloads again over FTP, by hand.
- `compress_blackmagic.py` makes 1080p HEVC "Mini" copies of Blackmagic 6K clips with local ffmpeg.
- `cc_pipeline/` turns CC FBX files into retarget-ready GLBs plus shape-key JSON (FBXtoGLBCompression, Blender, node). See `cc_pipeline/README.md`.
- `cleanup_vicon.py` deletes recordings from the Vicon PC once they are stored locally or on the research drive (rclone check).
- Control port `127.0.0.1:8765` takes sync triggers from `triggerSync.php` in [signlab_mocapStudio](https://github.com/Amsterdam-Humanities-Labs/signlab_mocapStudio). Heartbeats go to the Client Monitor API.

## Where it runs
Core server, `/home/gomer/viconSync`, as user `gomer`. `repos.tsv` does not deploy it. It connects to the Vicon PC (Windows, tailnet) as a client only.

## Status
Production.

## How to run / deploy
Python 3, no venv. Needs `requests`, `pymysql` and the binaries `ssh`, `scp`, `sshpass`, `rsync`, `tailscale`, `ffmpeg`, `rclone`.

| Entry point | Started by |
|---|---|
| `python3 sync_vicon_files.py [--dry-run] [--once] [--full] [--clear-cache]` | pythonCron, daily 02:30. Without `--once` it loops every 24 h and serves the control port |
| `python3 compress_blackmagic.py --once` (`--dry-run`, `--limit 2`) | `vicon-blackmagic-mini.timer`, daily 04:00 |
| `cc_pipeline/convert_all.sh -j 2` | `vicon-cc-pipeline.timer`, hourly (and per CC file from the sync) |
| `python3 ftp_monitor.py`, `python3 glb_matcher.py` | long-running |
| `python3 cleanup_vicon.py [--dry-run]`, `python3 resync_fbx.py [--refresh-cache]` | by hand |

The schedules live in [signlab_pythonCron](https://github.com/Amsterdam-Humanities-Labs/signlab_pythonCron). It also runs a weekly `cleanup_obs.py` that is not in this repo.
Tests: `python3 -m pytest tests -q` (all remote calls are mocked).
Database setup: `./setup_database.sh`. Or run `mysql admin_gebarenoverleg < create_tables.sql`, then `add_glb_path_column.sql` (`create_tables.sql` lacks `glb_path`). Check with `python3 db_config.py` or `python3 db_writer.py`.

The Vicon PC gets a new tailnet node and `100.x` address after every reinstall, so its address is not configured. `vicon_host.py` picks the online `vicon*` peer with the newest suffix and probes the port. Run this before any manual ssh:
```bash
python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22))"
```
`ViconOffline` means the Vicon PC is down or off the tailnet.

## Configuration
- `monitor_config.json` (not in git): copy `monitor_config.example.json` and fill in `ftp.password`, or set `VICON_PASSWORD`. It also tunes the monitor, the matcher and `blackmagic_mini`.
- `/web/mysql_config.php` on the server holds the MySQL credentials. `db_config.py` parses it.
- Docroot paths (`/web/...`) come from the vendored `sc_paths.py`: `SC_WEB_ROOT` from the environment or `$SC_ENV_FILE`/`/web/.env`, default `/web`. Edit `sc_paths.py` in [signlab_signcollect-lib](https://github.com/Amsterdam-Humanities-Labs/signlab_signcollect-lib), not here. `VICON_BLACKMAGIC_STATUS_PATH` overrides the path of the status file on the Vicon PC.
- State: `monitor_state.json` and `blackmagic_compress_state.json`. Delete an entry to force a re-encode. Clear `failures` to retry a parked clip.
- Logs: `logs/sync_vicon_rsync.log` (from `sync_vicon_files.py`; name kept), `logs/cleanup_vicon.log`, `logs/ftp_monitor.log`, `logs/glb_matcher.log`, `logs/blackmagic_mini*.log`.
- `python_client.py` is a vendored copy of `client/` in [signlab_client_monitor_api](https://github.com/Amsterdam-Humanities-Labs/signlab_client_monitor_api). Imports prefer the installed package. Do not edit the copy.

## Dependencies
- Vicon PC (SSH and FTP on the tailnet) and Tailscale on the server. `monsterfish` only for the manual CC backfill.
- MySQL `admin_gebarenoverleg`. [signlab_viconDashboard](https://github.com/Amsterdam-Humanities-Labs/signlab_viconDashboard) and [signlab_mocap-postprocessing](https://github.com/Amsterdam-Humanities-Labs/signlab_mocap-postprocessing) read the rows.
- [signlab_pythonCron](https://github.com/Amsterdam-Humanities-Labs/signlab_pythonCron) for scheduling. Client Monitor API at `https://signcollect.nl/client_monitor_api/api.php` (optional).
- Diagram: `docs/pipeline_overview.html`.
