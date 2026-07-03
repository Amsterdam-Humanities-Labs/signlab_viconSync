# Vicon Sync Scripts

## Overview
This directory contains scripts for synchronizing motion capture files from the Vicon system to local storage.

## Current Scripts

### `sync_vicon_rsync.py` (PRIMARY)
Main sync script using SSH/SCP protocol.

- **Protocol**: SCP over SSH (secure, encrypted)
- **Sources**:
  - `vicon@100.83.229.92:E:\Recordings` (raw Vicon exports, ~21 date directories)
  - `vicon@100.83.229.92:D:\PostExports\FBX` (post-processed files, ~20,000+ files in 22 date directories)
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

### `sync_fbx.py.old` (DEPRECATED)
Original FTP-based sync script.

- **Status**: Deprecated as of 2026-01-20
- **Replacement**: Use `sync_vicon_rsync.py` instead
- **Reason**: Migrated to SSH/rsync for improved security and efficiency

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
├── sync_fbx.py.old          # Deprecated FTP script
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
SSH_HOST = "100.83.229.92"
SSH_USER = "vicon"
SSH_PASS = "CHANGE_ME"

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
# Test SSH connection manually
sshpass -p 'CHANGE_ME' ssh vicon@100.83.229.92 "echo Connection successful"

# Check SSH keys
ls -la ~/.ssh/
```

### Rsync Issues
```bash
# Test rsync manually with a single file
rsync -avz --dry-run \
  -e "sshpass -p 'CHANGE_ME' ssh -o StrictHostKeyChecking=no" \
  vicon@100.83.229.92:/e/Recordings/2026-01-14/M20251216_8568_260114_0/unreal/*.fbx \
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
