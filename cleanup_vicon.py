#!/usr/bin/env python3
"""Delete synced files from Vicon PC that are confirmed stored elsewhere.

Handles:
- OBS .mkv files: checks against local razerFiles directory
- Shogun .mov files: checks against rclone remote (SharePoint studioFiles/mocapFiles/shogun_live)
"""

import os
import sys
import re
import json
import subprocess
import logging
import argparse
from pathlib import Path

# Configuration (same SSH creds as sync_vicon_rsync.py)
SSH_HOST = "100.83.229.92"
SSH_USER = "vicon"
SSH_PASS = "CHANGE_ME"
REMOTE_BASE = "E:\\Recordings"
LOG_DIR = "/home/gomer/viconSync/logs"
LOG_FILE = f"{LOG_DIR}/cleanup_vicon.log"

# Rclone remote for SharePoint
RCLONE_REMOTE = "signcollect:/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles/mocapFiles"

# Batch size for remote deletions
DELETE_BATCH_SIZE = 20

# File types to clean up
# check_source: 'local' = check local dir, 'rclone' = check rclone remote
CLEANUP_TARGETS = [
    {
        'name': 'OBS recordings',
        'remote_subdir': 'obs',
        'extension': '.mkv',
        'check_source': 'local',
        'check_path': '/web/gebarenoverleg_media/razerFiles',
    },
    {
        'name': 'Shogun .mov',
        'remote_subdir': 'shogun_live',
        'extension': '.mov',
        'check_source': 'rclone',
        'check_path': f"{RCLONE_REMOTE}/shogun_live",
    },
    {
        'name': 'Shogun .x2d',
        'remote_subdir': 'shogun_live',
        'extension': '.x2d',
        'check_source': 'rclone',
        'check_path': f"{RCLONE_REMOTE}/shogun_live",
    },
    {
        'name': 'Shogun .mcp',
        'remote_subdir': 'shogun_live',
        'extension': '.mcp',
        'check_source': 'rclone',
        'check_path': f"{RCLONE_REMOTE}/shogun_live",
    },
    {
        'name': 'Shogun .enf',
        'remote_subdir': 'shogun_live',
        'extension': '.enf',
        'check_source': 'rclone',
        'check_path': f"{RCLONE_REMOTE}/shogun_live",
    },
]

# Setup logging
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def ssh_execute(command, timeout=60):
    """Execute a command on the remote system via SSH.
    Uses list-based subprocess to avoid shell interpretation of $ and other special chars.
    """
    cmd_list = [
        'sshpass', '-p', SSH_PASS,
        'ssh', '-o', 'StrictHostKeyChecking=no',
        f'{SSH_USER}@{SSH_HOST}',
        command
    ]

    try:
        result = subprocess.run(
            cmd_list,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        logger.error(f"SSH command timeout: {command}")
        return "", 1
    except Exception as e:
        logger.error(f"SSH execution error: {e}")
        return "", 1


def get_local_files(local_dir, extension):
    """Scan a local directory, return {filename: size} for files matching extension."""
    local_files = {}
    local_path = Path(local_dir)

    if not local_path.exists():
        logger.error(f"Local directory does not exist: {local_dir}")
        return local_files

    for entry in os.scandir(local_dir):
        if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(extension):
            try:
                local_files[entry.name] = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue

    logger.info(f"Found {len(local_files)} local {extension} files in {local_dir}")
    return local_files


def get_rclone_files(rclone_path, extension):
    """List files on rclone remote, return {filename: size} for files matching extension."""
    cmd = [
        'rclone', 'lsjson', rclone_path,
        '--no-modtime', '--no-mimetype',
        '--include', f'*{extension}',
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        logger.error(f"rclone lsjson timeout for {rclone_path}")
        return {}
    except Exception as e:
        logger.error(f"rclone lsjson error: {e}")
        return {}

    if result.returncode != 0:
        logger.error(f"rclone lsjson failed: {result.stderr.strip()}")
        return {}

    try:
        files = {f['Name']: f['Size'] for f in json.loads(result.stdout) if not f.get('IsDir')}
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse rclone file list: {e}")
        return {}

    logger.info(f"Found {len(files)} {extension} files on rclone remote {rclone_path}")
    return files


def get_date_dirs():
    """List date directories under E:\\Recordings (same approach as sync_vicon_rsync.py)."""
    stdout, returncode = ssh_execute(f'cmd /c dir "{REMOTE_BASE}" /B /AD')
    if returncode != 0:
        logger.error("Failed to list date directories")
        return []

    dirs = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if re.match(r'^\d{4}-\d{2}-\d{2}$', line):
            dirs.append(line)

    logger.info(f"Found {len(dirs)} date directories")
    return sorted(dirs)


def get_remote_files(date_dirs, remote_subdir, extension):
    """Get all files matching extension under E:\\Recordings\\*\\*\\<subdir>\\ with sizes."""
    if not date_dirs:
        return []

    remote_files = []
    BATCH_SIZE = 5
    for i in range(0, len(date_dirs), BATCH_SIZE):
        batch = date_dirs[i:i + BATCH_SIZE]
        patterns = [f"{REMOTE_BASE}\\{d}\\*\\{remote_subdir}\\*{extension}" for d in batch]
        ps_patterns = "','".join(patterns)
        ps_command = (
            f"powershell -Command \"Get-ChildItem -Path '{ps_patterns}' "
            f"-File -ErrorAction SilentlyContinue "
            f"| ForEach-Object {{ $_.FullName + '|' + $_.Length }}\""
        )

        output, returncode = ssh_execute(ps_command, timeout=120)

        if not output.strip():
            continue

        for line in output.strip().splitlines():
            line = line.strip()
            if '|' not in line:
                continue
            path, size_str = line.rsplit('|', 1)
            try:
                size = int(size_str)
            except ValueError:
                logger.warning(f"Could not parse size for: {line}")
                continue
            filename = path.rsplit('\\', 1)[-1]
            remote_files.append({'path': path, 'filename': filename, 'size': size})

        logger.info(f"  Batch {i // BATCH_SIZE + 1}: scanned {len(batch)} date dirs, {len(remote_files)} files so far")

    logger.info(f"Found {len(remote_files)} remote {remote_subdir}/{extension} files total")
    return remote_files


def find_deletable(remote_files, confirmed_files):
    """Compare Vicon files vs confirmed files by name+size. Return (deletable, skipped) lists."""
    deletable = []
    skipped = []

    for rf in remote_files:
        confirmed_size = confirmed_files.get(rf['filename'])
        if confirmed_size is not None and confirmed_size == rf['size']:
            deletable.append(rf)
        else:
            reason = "not confirmed" if confirmed_size is None else f"size mismatch (vicon={rf['size']}, confirmed={confirmed_size})"
            skipped.append({**rf, 'reason': reason})

    return deletable, skipped


def delete_remote_files(deletable, dry_run=False):
    """Delete confirmed files from Vicon PC in batches via PowerShell."""
    if not deletable:
        logger.info("No files to delete")
        return 0, 0

    deleted_count = 0
    error_count = 0

    for i in range(0, len(deletable), DELETE_BATCH_SIZE):
        batch = deletable[i:i + DELETE_BATCH_SIZE]
        remove_cmds = []
        for f in batch:
            escaped_path = f['path'].replace("'", "''")
            remove_cmds.append(f"Remove-Item -LiteralPath '{escaped_path}' -Force")

        ps_script = "; ".join(remove_cmds)
        ps_command = f"powershell -Command \"{ps_script}\""

        if dry_run:
            for f in batch:
                logger.info(f"  [DRY RUN] Would delete: {f['path']} ({f['size']:,} bytes)")
            deleted_count += len(batch)
        else:
            output, returncode = ssh_execute(ps_command, timeout=120)
            if returncode == 0:
                for f in batch:
                    logger.info(f"  Deleted: {f['path']} ({f['size']:,} bytes)")
                deleted_count += len(batch)
            else:
                logger.error(f"  Batch delete failed (exit code {returncode}): {output.strip()}")
                error_count += len(batch)

    return deleted_count, error_count


def main():
    parser = argparse.ArgumentParser(description="Delete synced files from Vicon PC that exist locally (OBS .mkv + Shogun .mov)")
    parser.add_argument('--dry-run', action='store_true', help="List what would be deleted without deleting")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== Vicon Cleanup started ({mode}) ===")

    date_dirs = get_date_dirs()
    if not date_dirs:
        logger.warning("No date directories found — nothing to do")
        return

    total_deleted = 0
    total_skipped = 0
    total_errors = 0
    total_space = 0

    for target in CLEANUP_TARGETS:
        logger.info(f"--- Processing {target['name']} ({target['extension']}) ---")

        # 1. Get confirmed files (local dir or rclone remote)
        if target['check_source'] == 'rclone':
            confirmed_files = get_rclone_files(target['check_path'], target['extension'])
            source_label = f"rclone:{target['check_path']}"
        else:
            confirmed_files = get_local_files(target['check_path'], target['extension'])
            source_label = target['check_path']

        if not confirmed_files:
            logger.warning(f"No {target['extension']} files found in {source_label} — skipping")
            continue

        # 2. Get remote files
        remote_files = get_remote_files(date_dirs, target['remote_subdir'], target['extension'])
        if not remote_files:
            logger.info(f"No remote {target['name']} files found — skipping")
            continue

        # 3. Compare
        deletable, skipped = find_deletable(remote_files, confirmed_files)

        logger.info(f"Matched for deletion: {len(deletable)}")
        logger.info(f"Skipped: {len(skipped)}")

        for s in skipped:
            logger.info(f"  Skipped: {s['filename']} — {s['reason']}")

        # 4. Delete (or dry-run)
        deleted_count, error_count = delete_remote_files(deletable, dry_run=args.dry_run)

        space_freed = sum(f['size'] for f in deletable) if deleted_count > 0 else 0
        total_deleted += deleted_count
        total_skipped += len(skipped)
        total_errors += error_count
        total_space += space_freed

    # 5. Summary
    logger.info("=== Summary ===")
    logger.info(f"  Files deleted: {total_deleted}")
    logger.info(f"  Files skipped: {total_skipped}")
    logger.info(f"  Space freed: {total_space / (1024**3):.2f} GB")
    logger.info(f"  Errors: {total_errors}")
    logger.info(f"=== Vicon Cleanup finished ({mode}) ===")


if __name__ == "__main__":
    main()
