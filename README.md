# signlab_viconSync

Pulls motion-capture recordings off the Vicon PC in the lab, files them on the
signcollect core server, registers them in MySQL, and cleans the Vicon PC up
again once a copy is safe elsewhere.

## What it does

The Vicon capture PC in the studio writes recordings to its own local disks
(`E:\Recordings` for raw exports, `D:\PostExports\FBX` for post-processed
FBX). Nothing downstream can see those disks, so this repo is the bridge. It
holds several small programs that between them:

- **sync** FBX/GLB exports over SSH/SCP into `/web/gebarenoverleg_media/fbx/`
  (`sync_vicon_rsync.py`, the primary job);
- **watch** the Vicon FTP server continuously and write what it sees — captures,
  files, sizes, whether a file is still growing — into MySQL
  (`ftp_monitor.py` + `db_writer.py`, see `DATABASE_README.md`);
- **match** synced FBX files against their GLB counterparts and record the pairing
  (`glb_matcher.py`);
- **compress** Blackmagic 6K studio clips into 1080p HEVC "Mini" copies
  (`compress_blackmagic.py`, encode offloaded over SSH to the `monsterfish` GPU box);
- **clean up** the Vicon PC by deleting recordings that are confirmed stored
  locally or on the research drive (`cleanup_vicon.py`).

Each long-running piece reports a heartbeat to the Client Monitor API on
signcollect.nl, so a silently dead job shows up as a missing heartbeat.

That heartbeat client used to be pasted into `ftp_monitor.py`, `glb_matcher.py`
and `sync_vicon_rsync.py` as three separately drifted copies of the class in
`python_client.py`. It is now the `signlab-client-monitor` package, which lives
in `signlab_client_monitor_api/client`. `python_client.py` here is a verbatim
vendored copy of it, kept so the scripts keep working on a host where the
package has never been installed; the imports prefer the package and fall back
to it. Do not edit `python_client.py` - refresh it from the package, per the
instructions in its own header.

## Where it runs

**The signcollect core server (production VPS).** Everything in this repo is
deployed to `/home/gomer/viconSync` and runs there — not on the Vicon PC:

- targets and reads server-local paths (`/web/gebarenoverleg_media/fbx`,
  `/mnt/bigstorage/blackmagic_filesMini`);
- reads MySQL credentials from `/web/mysql_config.php` (`db_config.py`);
- is scheduled by the estate's `signlab_pythonCron` scheduler and by systemd
  units that run as `User=gomer`;
- reaches the Vicon PC *remotely*, over the tailnet, via SSH/SCP and FTP.

The Vicon PC itself is a Windows machine on the tailnet; this code only ever
talks to it as a client. Its address is not configured anywhere — see
[Host Discovery](#host-discovery).

## Status

**Production.** `sync_vicon_rsync.py` runs nightly and is the supported path;
`resync_fbx.py` is a manual FTP-based utility retained for one-off re-downloads.

## How to run it

Python 3 (`/usr/bin/python3` on the server), no virtualenv in production.
Third-party imports: `requests`, `pymysql`. External binaries: `ssh`, `scp`,
`sshpass`, `rsync`, `tailscale`, `ffmpeg` (for the compressor), `rclone` (for
`cleanup_vicon.py`'s SharePoint check).

| Entry point | Invocation | Scheduled by |
|---|---|---|
| `sync_vicon_rsync.py` | `python3 sync_vicon_rsync.py [--dry-run]` | pythonCron, daily 02:30 |
| `cleanup_vicon.py` | `python3 cleanup_vicon.py` | manual; see note below |
| `compress_blackmagic.py` | `python3 compress_blackmagic.py --once` | systemd `vicon-blackmagic-mini.timer`, daily 04:00 |
| `ftp_monitor.py` | `python3 ftp_monitor.py` | long-running monitor (see `DATABASE_README.md`) |
| `glb_matcher.py` | `python3 glb_matcher.py` | long-running matcher |
| `resync_fbx.py` | `python3 resync_fbx.py [--refresh-cache]` | manual only |

`signlab_pythonCron` is the estate scheduler. Its `config.json` carries two
entries pointing into `/home/gomer/viconSync/`: the nightly 02:30
`sync_vicon_rsync.py` above, and a weekly 04:00 "Cleanup OBS from Vicon PC"
job running `cleanup_obs.py`. **TODO: confirm what `cleanup_obs.py` is** — that
filename has never existed in this repo (only `cleanup_vicon.py` has), so the
scheduled file is either a deploy-only leftover or an older name for it.
Changing a schedule means editing the pythonCron repo, not this one.

Tests are pytest and mock everything remote: `python3 -m pytest tests -q`.

## Configuration

- `monitor_config.json` — **not in git.** FTP/SSH password plus the tuning for
  the monitor, GLB matcher and Blackmagic compressor. Copy it from
  `monitor_config.example.json` and fill in `ftp.password`. `VICON_PASSWORD` in
  the environment overrides it (`vicon_credentials.py`).
- `/web/mysql_config.php` — **not in git, lives on the server.** The MySQL
  credentials are parsed out of the site's PHP config by `db_config.py`; there
  is no database password in this repo.
- The Vicon PC's address — not configured at all, discovered at runtime.

## Dependencies

- The **Vicon capture PC** (Windows), on the tailnet, with its SSH and FTP
  services up.
- **Tailscale** on the server: `vicon_host.py` shells out to `tailscale status --json`.
- **MySQL** on the core server (schema in `create_tables.sql`; see
  `DATABASE_README.md`).
- **`signlab_pythonCron`** — schedules the sync and cleanup jobs.
- **`monsterfish`** (GPU box, SSH-reachable) — does the HEVC encode for
  `compress_blackmagic.py`.
- **Client Monitor API** (`https://signcollect.nl/client_monitor_api/api.php`) —
  optional; failures never block a sync.

Further reading: `USAGE.md` (operator guide), `DATABASE_README.md` (schema and
the FTP monitor), `DASHBOARD_README.md` (the dashboard fed by this data),
`docs/pipeline_overview.html`.

## Setup

`monitor_config.json` holds the FTP credentials and is not tracked in git. Create
it from the template before running anything:

```bash
cp monitor_config.example.json monitor_config.json
```

Then fill in `ftp.password` (the template ships `CHANGE_ME`). The remaining
values — paths, poll intervals, client-monitor endpoints — are safe defaults and
usually need no change. Everything else the scripts need (the Vicon PC's address)
is discovered at runtime; see below.

## Host Discovery

The Vicon PC's address is **not** configured anywhere. It rejoins the tailnet
under a new node identity (and a new `100.x` address) after every Windows
reinstall, so `vicon_host.py` looks it up at runtime: it reads
`tailscale status --json`, picks the online `vicon*` peer with the newest
rejoin suffix, and TCP-probes the port it needs (22 for SSH, 21 for FTP).

Every consumer — `sync_vicon_rsync.py`, `ftp_monitor.py`, `cleanup_vicon.py`
and `resync_fbx.py` — resolves through it. If no `vicon*` peer answers, they
raise/report `ViconOffline` instead of hanging on a dead address.

Ask it where the Vicon PC is right now:
```bash
cd /home/gomer/viconSync
python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22))"
```
It prints `('<ip>', '<node-name>')`, or raises `ViconOffline` naming each known
peer and when it was last seen. **Use this before any manual `ssh`/`ping`/`nc`
against the Vicon PC** — an address copied from an old log or ticket is very
likely dead.

## Current Scripts

### `sync_vicon_rsync.py` (PRIMARY)
Main sync script using SSH/SCP protocol.

- **Protocol**: SCP over SSH (secure, encrypted)
- **Host**: resolved at runtime via `vicon_host.py` (see [Host Discovery](#host-discovery)); below, `$VICON` stands for whatever address it returns
- **Sources**:
  - `vicon@$VICON:E:\Recordings` (raw Vicon exports, ~21 date directories)
  - `vicon@$VICON:D:\PostExports\FBX` (post-processed files, ~20,000+ files in 22 date directories)
- **Target**: `/web/gebarenoverleg_media/fbx/`
- **File Types**: FBX and GLB files
- **Features**:
  - Smart sync (only transfers newer/modified files via size comparison)
  - Handles multiple directory structures automatically
  - Recursive directory scanning
  - Comprehensive logging
  - Dry-run mode for testing
  - **Health monitoring** via Client Monitor API (reports every 24 hours)

**Usage**:
```bash
# Normal sync
python3 sync_vicon_rsync.py

# Dry run (test without transferring)
python3 sync_vicon_rsync.py --dry-run

# View help
python3 sync_vicon_rsync.py --help
```

### `resync_fbx.py` (UTILITY)
Re-downloads files from the FTP server based on post_processed directory contents.

- **Protocol**: FTP (legacy, retained for compatibility)
- **Purpose**: Re-download specific files that were post-processed
- **Note**: Still uses FTP; may be migrated to SSH/rsync in the future

## Deprecated Scripts

### `sync_fbx.py` (REMOVED)
Original FTP-based sync script, deprecated 2026-01-20 and deleted from the working
tree when the repo was published. Superseded by `sync_vicon_rsync.py`, which uses
SSH/rsync for improved security and efficiency. Recover it from git history if
needed: `git log --all --diff-filter=D -- sync_fbx.py.old`.

## Migration History

### 2026-01-20: FTP to SSH/SCP Migration + D: Drive Integration + Monitoring
- Replaced FTP protocol with SCP over SSH (rsync not available on Windows)
- Added GLB file support (in addition to existing FBX support)
- **Added D:\PostExports\FBX source** (20,673 post-processed FBX files)
- Improved security (encrypted transfers, no plaintext credentials)
- Added smart sync capabilities (size-based comparison)
- Multi-source support with flexible directory structure handling
- **Integrated Client Monitor API** for automated health tracking (24-hour heartbeat)
- Integrated with existing systemd scheduler

## Directory Structure

```
/home/gomer/viconSync/
├── sync_vicon_rsync.py     # Main sync script (SSH/rsync)
├── resync_fbx.py            # Re-sync utility (FTP)
├── vicon_host.py            # Runtime address discovery
├── vicon_credentials.py     # Password lookup (config / $VICON_PASSWORD)
├── monitor_config.example.json  # Config template; copy to monitor_config.json
├── README.md                # This file
├── USAGE.md                 # User guide
└── logs/                    # Log files
    └── sync_vicon_rsync.log
```

## Remote Directory Structure

### Source 1: E:\Recordings (Raw Exports)
```
E:\Recordings/
├── 2026-01-14/
│   ├── M20251216_8568_260114_0/
│   │   └── unreal/
│   │       ├── M20251216_8568_260114_0.fbx
│   │       └── *.glb (if present)
│   └── [other recordings]/
└── [other dates]/
```

### Source 2: D:\PostExports\FBX (Post-Processed)
```
D:\PostExports\FBX/
├── 2024-12-11/
│   ├── 1.fbx
│   ├── 100-A_241220_0.fbx
│   ├── 1000-B_241213_0.fbx
│   └── [~20,673 total FBX files across all dates]
├── 2025-01-24/
└── [22 date directories total]/
```

## Scheduling

The sync script runs automatically via the pythonCron scheduler:
- **Schedule**: Daily at 2:30 AM
- **Service**: Configured in `/home/gomer/pythonCron/config.json`

## Health Monitoring

The script integrates with the **Client Monitor API** for automated health tracking:
- **Client ID**: `vicon-sync-rsync`
- **Heartbeat Interval**: 86400 seconds (24 hours)
- **Reports**:
  - Files downloaded/skipped/errors
  - Total transfer size
  - Execution duration
  - Source locations
  - Success/warning/error status

The monitoring system will automatically alert if:
- No heartbeat received within 24 hours (indicates script failure)
- Script reports errors during sync
- Unexpected crashes or interruptions

**Note**: Monitoring is optional. If the API is unavailable, the script will log a warning and continue normally. Sync operations are never blocked by monitoring failures.

## Logs

Sync logs are stored in `/home/gomer/viconSync/logs/sync_vicon_rsync.log`

View recent activity:
```bash
tail -f /home/gomer/viconSync/logs/sync_vicon_rsync.log
```

## Configuration

Edit the configuration section in `sync_vicon_rsync.py`:
```python
# No host constant: the address comes from vicon_host.resolve_vicon_host()
# at startup (see "Host Discovery" above) and is passed to ViconSync(host=...).
# No password constant either: get_vicon_password() reads it from
# monitor_config.json (or $VICON_PASSWORD) at call time. See "Setup" above.
SSH_USER = "vicon"

# Remote paths to sync (in order)
REMOTE_PATHS = [
    {
        'name': 'E:\\Recordings (raw exports)',
        'base_path': 'E:\\Recordings',
        'has_unreal_subdir': True,  # Files in */*/unreal/
    },
    {
        'name': 'D:\\PostExports\\FBX (post-processed)',
        'base_path': 'D:\\PostExports\\FBX',
        'has_unreal_subdir': False,  # Files directly in date dirs
    }
]

LOCAL_PATH = "/web/gebarenoverleg_media/fbx"
FILE_EXTENSIONS = ['.fbx', '.glb']

# Client Monitor API Configuration
CLIENT_MONITOR_API_URL = "https://signcollect.nl/client_monitor_api/api.php"
CLIENT_MONITOR_ID = "vicon-sync-rsync"
CLIENT_MONITOR_NAME = "Vicon File Sync (SSH/SCP)"
CLIENT_MONITOR_DESCRIPTION = "Syncs FBX/GLB files from E:\\Recordings and D:\\PostExports\\FBX via SSH/SCP"
CLIENT_MONITOR_INTERVAL = 86400  # 24 hours
```

To add more sources, simply add another dictionary to the `REMOTE_PATHS` list.

To disable monitoring, set `CLIENT_MONITOR_API_URL` to an empty string or remove the monitoring calls from `main()`.

## Troubleshooting

### SSH Connection Issues
```bash
cd /home/gomer/viconSync

# First: where is the Vicon PC right now? This is the same lookup the scripts do.
python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22))"

# If that raises ViconOffline, the PC is down or off the tailnet — stop here.
# Otherwise test SSH manually against the address it just resolved:
VICON=$(python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22)[0])")
VICON_PW=$(python3 -c "from vicon_credentials import get_vicon_password; print(get_vicon_password())")
sshpass -p "$VICON_PW" ssh vicon@"$VICON" "echo Connection successful"

# Check SSH keys
ls -la ~/.ssh/
```

### Rsync Issues
```bash
cd /home/gomer/viconSync
VICON=$(python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22)[0])")
VICON_PW=$(python3 -c "from vicon_credentials import get_vicon_password; print(get_vicon_password())")

# Test rsync manually with a single file
rsync -avz --dry-run \
  -e "sshpass -p $VICON_PW ssh -o StrictHostKeyChecking=no" \
  vicon@"$VICON":/e/Recordings/2026-01-14/M20251216_8568_260114_0/unreal/*.fbx \
  /web/gebarenoverleg_media/fbx/
```

### Permission Issues
```bash
# Check local directory permissions
ls -la /web/gebarenoverleg_media/fbx/

# Check disk space
df -h /web/gebarenoverleg_media/fbx/
```

## Security Notes

- SSH password is stored in the script for automated execution
- Future enhancement: Migrate to SSH key-based authentication
- FTP credentials in deprecated scripts should be removed after migration verification

## Future Enhancements

1. **SSH Key Authentication**: Replace password with key-based auth
2. **D:\allRecordings Support**: Add secondary remote path
3. **GLB Conversion Integration**: Auto-convert FBX to GLB after sync
4. **Database Integration**: Auto-register synced files in MySQL
5. **Resync Script Migration**: Update `resync_fbx.py` to use SSH/rsync

## Support

For issues or questions:
- Check logs: `/home/gomer/viconSync/logs/sync_vicon_rsync.log`
- Review this README and USAGE.md
- Test with `--dry-run` before making changes
