# Vicon File Sync - Usage Guide

## Quick Start

### Run Full Sync
```bash
cd /home/gomer/viconSync
python3 sync_vicon_rsync.py
```

### Test Without Downloading (Dry Run)
```bash
python3 sync_vicon_rsync.py --dry-run
```

### View Help
```bash
python3 sync_vicon_rsync.py --help
```

## Finding the Vicon PC

The Vicon PC gets a new tailnet address every time Windows is reinstalled, so
nothing in this repo stores one. `vicon_host.py` resolves it at runtime and the
scripts call it on every start. Run the same lookup yourself before any manual
`ssh`, `ping` or `nc` — never reuse an address from an old log or ticket:

```bash
cd /home/gomer/viconSync
python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22))"
```

- Prints `('<ip>', '<node-name>')` — that is the live address, and the port is
  confirmed open, so a working answer here already rules out "PC down" and
  "SSH not listening".
- Raises `ViconOffline` — the message names every known `vicon*` peer and when
  it was last seen. The PC is off, asleep, or not on the tailnet; there is
  nothing to ssh to and no address to try.

Most commands below start by capturing that address:
```bash
VICON=$(python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22)[0])")
```

## What It Does

The sync script:
1. Looks up the Vicon PC's current address on the tailnet (`vicon_host.py`) and
   connects to it via SSH. The address is never hardcoded — it changes every
   time the PC rejoins the tailnet. See [Finding the Vicon PC](#finding-the-vicon-pc).
2. Syncs from **two sources** in order:
   - **E:\Recordings**: Raw Vicon exports (files in `*/*/unreal/` subdirectories)
   - **D:\PostExports\FBX**: Post-processed files (~20,673 FBX files directly in date directories)
3. For E:\Recordings: Scans date directories → recording subdirectories → unreal folders
4. For D:\PostExports\FBX: Scans date directories → files directly
5. Uses SCP to intelligently sync files to `/web/gebarenoverleg_media/fbx/`
6. Only transfers files that are newer or don't exist locally (smart sync via size comparison)
7. Logs all activity to `/home/gomer/viconSync/logs/sync_vicon_rsync.log`

## Automatic Scheduling

The script runs automatically every day at 2:30 AM via the pythonCron scheduler.

### Check Scheduler Status
```bash
systemctl status service-match_vicon_fbx_csv_files_with_mocap_records
```

### View Scheduler Configuration
```bash
cat /home/gomer/pythonCron/config.json | grep -A 10 "sync Vicon"
```

## Viewing Logs

### Tail Live Logs
```bash
tail -f /home/gomer/viconSync/logs/sync_vicon_rsync.log
```

### View Recent Logs
```bash
tail -n 100 /home/gomer/viconSync/logs/sync_vicon_rsync.log
```

### Search Logs for Errors
```bash
grep ERROR /home/gomer/viconSync/logs/sync_vicon_rsync.log
```

### View Today's Sync Activity
```bash
grep "$(date +%Y-%m-%d)" /home/gomer/viconSync/logs/sync_vicon_rsync.log
```

## Understanding Output

### Sync Status Indicators
- `✓ Downloaded` - File was successfully transferred
- `↓ Skipped` - File already exists and is up to date
- `✗ Failed` - Error occurred during transfer

### Summary Statistics
At the end of each sync, you'll see:
```
Downloaded:  15 files
Skipped:     234 files (already up to date)
Errors:      0 files
Total size:  45.67 MB
Duration:    0:02:34
```

## Common Tasks

### Manual Sync After New Recording
```bash
cd /home/gomer/viconSync
python3 sync_vicon_rsync.py
```

### Test Sync Without Downloading
```bash
python3 sync_vicon_rsync.py --dry-run
```
This shows what would be downloaded without actually transferring files.

### Check What Files Exist Locally
```bash
ls -lh /web/gebarenoverleg_media/fbx/ | tail -20
```

### Count Files in Target Directory
```bash
find /web/gebarenoverleg_media/fbx/ -name "*.fbx" | wc -l
find /web/gebarenoverleg_media/fbx/ -name "*.glb" | wc -l
```

### Check Disk Space
```bash
df -h /web/gebarenoverleg_media/fbx/
du -sh /web/gebarenoverleg_media/fbx/
```

## Troubleshooting

### Script Won't Connect to Vicon
**Symptom**: "Failed to list date directories", "Vicon PC unreachable", or SSH
connection errors

**Solution**:
```bash
cd /home/gomer/viconSync

# 1. Can we find the Vicon PC at all? (This also probes port 22.)
python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22))"
```

If that raises `ViconOffline`, the PC is not reachable on the tailnet and there
is nothing further to test locally — check that the machine is powered on and
that Tailscale is running on it. The exception message lists each known `vicon*`
peer with its last-seen time.

If it prints an address, discovery and SSH-port reachability are both fine, so
test the login itself:
```bash
VICON=$(python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22)[0])")
sshpass -p 'CHANGE_ME' ssh vicon@"$VICON" "echo Connection OK"
```

### Rsync Fails
**Symptom**: Files show as failed in logs

**Solution**:
```bash
cd /home/gomer/viconSync
VICON=$(python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22)[0])")

# Test rsync manually with a known file
rsync -avz --dry-run \
  -e "sshpass -p 'CHANGE_ME' ssh -o StrictHostKeyChecking=no" \
  vicon@"$VICON":/e/Recordings/2026-01-14/M20251216_8568_260114_0/unreal/*.fbx \
  /web/gebarenoverleg_media/fbx/

# Check if sshpass is installed
which sshpass

# Check if rsync is installed
which rsync
```

### Permission Denied on Local Directory
**Symptom**: Cannot write to `/web/gebarenoverleg_media/fbx/`

**Solution**:
```bash
# Check directory permissions
ls -ld /web/gebarenoverleg_media/fbx/

# Check if directory exists
stat /web/gebarenoverleg_media/fbx/

# Check disk space
df -h /web/gebarenoverleg_media/fbx/
```

### No Files Found
**Symptom**: "Found 0 date directories" or no files synced

**Solution**:
```bash
cd /home/gomer/viconSync
VICON=$(python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22)[0])")

# Check remote directory manually
sshpass -p 'CHANGE_ME' ssh vicon@"$VICON" "dir E:\\Recordings"

# Check if files exist in a known recording
sshpass -p 'CHANGE_ME' ssh vicon@"$VICON" "dir E:\\Recordings\\2026-01-14\\M20251216_8568_260114_0\\unreal"
```

### Sync Is Slow
**Symptom**: Takes a long time to complete

**Explanation**: This is normal behavior. The script:
1. Scans all date directories (can be 50+ directories)
2. Scans all recording subdirectories (can be 100+ per date)
3. Checks each file individually with rsync

**Tips**:
- Rsync's `--update` flag ensures files are only transferred if needed
- Most files will be skipped on subsequent runs
- First run will take longest as it downloads all new files

### Script Hangs or Timeouts
**Symptom**: Script stops responding

**Solution**:
- Press Ctrl+C to interrupt
- Check network connection to Vicon system
- Review logs for last successful operation
- Try dry-run mode to test: `python3 sync_vicon_rsync.py --dry-run`

## Advanced Usage

### Modify Sync Schedule
Edit `/home/gomer/pythonCron/config.json` and change the `times` array:
```json
{
  "name": "sync Vicon files (rsync)",
  "script": "/home/gomer/viconSync/sync_vicon_rsync.py",
  "times": ["02:30", "14:30"],
  "enabled": true
}
```
Then restart the scheduler service.

### Add More File Types
Edit `sync_vicon_rsync.py` and modify the `FILE_EXTENSIONS` list:
```python
FILE_EXTENSIONS = ['.fbx', '.glb', '.bvh']
```

### Change Target Directory
Edit `sync_vicon_rsync.py` and modify `LOCAL_PATH`:
```python
LOCAL_PATH = "/path/to/new/directory"
```

## File Types Synced

### FBX Files (.fbx)
- Autodesk Filmbox format
- Contains motion capture data
- Primary format from Vicon system
- **Sources**:
  - E:\Recordings: Raw exports from Vicon captures
  - D:\PostExports\FBX: Post-processed/finalized files (~20,673 files)

### GLB Files (.glb)
- GL Transmission Format (binary)
- 3D model format
- May be present in E:\Recordings unreal subdirectories
- Also found in D:\RecordingsUE (not currently synced)

## Integration with Other Scripts

### Vicon Matching Script
After syncing, the matching script can process the files:
```bash
# This runs automatically via scheduler
python3 /home/gomer/viconSync/matchVicon.py
```

### FBX to GLB Conversion
Convert synced FBX files to GLB format:
```bash
python3 /home/gomer/viconSync/viconFBXtoGLB.py
```

## Best Practices

1. **Always test with --dry-run first** when making changes
2. **Monitor logs** after making configuration changes
3. **Check disk space** regularly (`df -h`)
4. **Verify files** after initial sync
5. **Keep backups** of configuration files before editing

## Performance Tips

- First sync will download all files (can take hours depending on file count)
- Subsequent syncs are much faster (only new/modified files)
- Rsync is efficient and resumes interrupted transfers automatically
- Network speed between systems affects transfer time

## Support Files

- **Configuration**: `/home/gomer/viconSync/sync_vicon_rsync.py` (edit `SSH_USER`, `SSH_PASS` and `LOCAL_PATH`; the host is not configured here — see `vicon_host.py`)
- **Logs**: `/home/gomer/viconSync/logs/sync_vicon_rsync.log`
- **Scheduler**: `/home/gomer/pythonCron/config.json`
- **Documentation**: `/home/gomer/viconSync/README.md`

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
