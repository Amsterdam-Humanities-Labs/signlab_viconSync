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

## What It Does

The sync script:
1. Connects to the Vicon system at `100.83.229.92` via SSH
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
**Symptom**: "Failed to list date directories" or SSH connection errors

**Solution**:
```bash
# Test SSH connection manually
sshpass -p 'CHANGE_ME' ssh vicon@100.83.229.92 "echo Connection OK"

# If this fails, check:
# 1. Is the Vicon system online?
ping 100.83.229.92

# 2. Is SSH server running on Vicon?
nc -zv 100.83.229.92 22
```

### Rsync Fails
**Symptom**: Files show as failed in logs

**Solution**:
```bash
# Test rsync manually with a known file
rsync -avz --dry-run \
  -e "sshpass -p 'CHANGE_ME' ssh -o StrictHostKeyChecking=no" \
  vicon@100.83.229.92:/e/Recordings/2026-01-14/M20251216_8568_260114_0/unreal/*.fbx \
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
# Check remote directory manually
sshpass -p 'CHANGE_ME' ssh vicon@100.83.229.92 "dir E:\\Recordings"

# Check if files exist in a known recording
sshpass -p 'CHANGE_ME' ssh vicon@100.83.229.92 "dir E:\\Recordings\\2026-01-14\\M20251216_8568_260114_0\\unreal"
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

- **Configuration**: `/home/gomer/viconSync/sync_vicon_rsync.py` (edit SSH_* and LOCAL_PATH variables)
- **Logs**: `/home/gomer/viconSync/logs/sync_vicon_rsync.log`
- **Scheduler**: `/home/gomer/pythonCron/config.json`
- **Documentation**: `/home/gomer/viconSync/README.md`
