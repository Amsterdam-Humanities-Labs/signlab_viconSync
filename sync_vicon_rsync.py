#!/usr/bin/env python3
"""
Sync files from Vicon system using SSH/SCP.
Downloads files from multiple sources:
- E:\\Recordings\\*\\*\\unreal\\ (raw Vicon exports: .fbx, .glb)
- E:\\Recordings\\*\\*\\livelink\\ (livelink data: .csv)
- E:\\Recordings\\*\\*\\metadata\\ (recording metadata: .json)
- E:\\Recordings\\*\\*\\shogun_live\\ (shogun captures: .mov, .mcp, .enf, .x2d)
- D:\\PostExports\\FBX\\*\\ (post-processed files)

Uses SCP for file transfer with smart sync (size-based checking).
Integrated with Client Monitor API for health tracking.

Runs continuously with 24-hour intervals between syncs.
"""

import os
import sys
import re
import subprocess
import logging
import time
import json
import threading
import http.server
import vicon_host
from vicon_credentials import get_vicon_password
from sc_paths import sc_path, sc_setting
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List


# The heartbeat client. Prefer the installed package; fall back to the copy
# vendored beside this file, which is what a host that has never run
# client/install.sh from signlab_client_monitor_api will find. The two are
# byte-identical - see the header of python_client.py.
try:
    from signlab_client_monitor import ClientMonitor, setup_rotating_logger
except ImportError:
    from python_client import ClientMonitor, setup_rotating_logger

# Configuration
SSH_USER = "vicon"
# The password is not stored here; get_vicon_password() reads it at call time.

# Remote paths to sync (in order)
REMOTE_PATHS = [
    {
        'name': 'E:\\Recordings (raw Unreal exports)',
        'base_path': 'E:\\Recordings',
        'has_unreal_subdir': True,  # Files are in date/recording/unreal/
        'unreal_is_recording_dir': False,  # unreal is a subdirectory of recording
    },
    {
        'name': 'D:\\PostExports\\FBX (post-processed Unreal)',
        'base_path': 'D:\\PostExports\\FBX',
        'has_unreal_subdir': True,  # Files are in date/unreal/
        'unreal_is_recording_dir': True,  # unreal is treated as the recording directory
    }
]

LOCAL_PATH = sc_path("media_fbx")
FILE_EXTENSIONS = ['.fbx', '.glb']

# Extra recording subdirs to sync from E:\Recordings (beyond unreal)
EXTRA_RECORDING_SUBDIRS = [
    {
        'subdir': 'livelink',
        'extensions': ['.csv'],
        'local_path': sc_path('media', 'llcsv'),
    },
    {
        'subdir': 'unreal',
        'extensions': ['.csv'],
        'local_path': sc_path('media', 'llcsv'),
    },
    {
        'subdir': 'metadata',
        'extensions': ['.json'],
        'local_path': sc_path('media', 'metadata'),
    },
    {
        'subdir': 'shogun_live',
        'extensions': ['.mov', '.mcp', '.enf', '.x2d'],
        'local_path': sc_path('media', 'shogun_live'),
    },
]

LOG_DIR = "/home/gomer/viconSync/logs"
LOG_FILE = f"{LOG_DIR}/sync_vicon_rsync.log"

# Control HTTP API (localhost only, used by /web/sync_trigger.php)
CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 8765
TRIGGER_EVENT = threading.Event()
SYNC_LOCK = threading.Lock()
LAST_RUN = {"state": "starting", "last_run": None, "last_stats": None, "started_at": None}

# Blackmagic camera sync. It runs on the Vicon PC in the interactive desktop
# session (via the BlackmagicManualSync scheduled task) so it can see the S:
# rclone mount, which is not visible to SSH logins. We trigger it with
# `schtasks /run` and read its progress from a status file it writes.
BLACKMAGIC_TASK = "BlackmagicManualSync"
# Path on the Vicon PC; override with VICON_BLACKMAGIC_STATUS_PATH (env or /web/.env).
BLACKMAGIC_STATUS_PATH = sc_setting(
    "VICON_BLACKMAGIC_STATUS_PATH",
    r"C:\Users\VICON\Desktop\Code\tools\BlackmagicExtraTools\blackmagic_RD_sync\bmcam_sync_status.json",
)
BLACKMAGIC_STATUS_STALE_SECONDS = 600
_BM_STATUS_CACHE = {"ts": 0.0, "data": None}

# Cache and optimization settings
CACHE_FILE = "/home/gomer/viconSync/sync_cache.json"
INCREMENTAL_DAYS = 14  # Only scan files from last N days (incremental mode)
FULL_SCAN_INTERVAL = 7 * 86400  # Full scan every 7 days (in seconds)
CACHE_VALIDITY_HOURS = 24  # Skip file checks if synced within N hours
USE_BATCH_OPERATIONS = True  # Use PowerShell batch operations for speed

# Client Monitor API Configuration
CLIENT_MONITOR_API_URL = "https://signcollect.nl/client_monitor_api/api.php"
CLIENT_MONITOR_ID = "vicon-sync-rsync"
CLIENT_MONITOR_NAME = "Vicon File Sync (SSH/SCP)"
CLIENT_MONITOR_DESCRIPTION = "Syncs FBX/GLB files from E:\\Recordings and D:\\PostExports\\FBX via SSH/SCP"
CLIENT_MONITOR_INTERVAL = 86400  # 24 hours (runs daily at 2:30 AM)

# Setup logging. Rotating, because this file used to grow without limit on a
# partition that checkDisk reports on through the same API as the heartbeat.
setup_rotating_logger(LOG_FILE, fmt='[%(asctime)s] [%(levelname)s] %(message)s',
                      stream=sys.stdout)
logger = logging.getLogger(__name__)


class SyncCache:
    """Manages JSON cache for file metadata and sync state."""

    def __init__(self, cache_file: str):
        self.cache_file = cache_file
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict[str, Any]:
        """Load cache from file or create new if doesn't exist."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    cache = json.load(f)
                    logger.info(f"Loaded cache with {len(cache.get('files', {}))} files")
                    return cache
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}, creating new cache")

        return {
            'last_full_scan': None,
            'files': {},
            'date_dirs': {},
            'scan_stats': {
                'last_incremental': None,
                'last_full': None
            }
        }

    def save(self):
        """Save cache to file."""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, indent=2)
            logger.info(f"Cache saved with {len(self.cache.get('files', {}))} files")
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")

    def should_do_full_scan(self) -> bool:
        """Determine if we should do a full scan based on last full scan time."""
        last_full = self.cache['scan_stats'].get('last_full')
        if not last_full:
            return True

        last_full_time = datetime.fromisoformat(last_full)
        elapsed = (datetime.now() - last_full_time).total_seconds()
        return elapsed >= FULL_SCAN_INTERVAL

    def get_file_cache(self, remote_path: str) -> Optional[Dict[str, Any]]:
        """Get cached info for a file."""
        return self.cache['files'].get(remote_path)

    def update_file(self, remote_path: str, remote_size: int, local_size: int, synced: bool):
        """Update file info in cache."""
        self.cache['files'][remote_path] = {
            'remote_size': remote_size,
            'local_size': local_size,
            'last_checked': datetime.now().isoformat(),
            'synced': synced
        }

    def is_file_recently_synced(self, remote_path: str) -> bool:
        """Check if file was recently synced and can be skipped."""
        file_info = self.get_file_cache(remote_path)
        if not file_info or not file_info.get('synced'):
            return False

        last_checked = file_info.get('last_checked')
        if not last_checked:
            return False

        last_checked_time = datetime.fromisoformat(last_checked)
        elapsed = (datetime.now() - last_checked_time).total_seconds()

        # File is recently synced if:
        # 1. Synced within cache validity period
        # 2. Sizes match
        is_recent = elapsed < (CACHE_VALIDITY_HOURS * 3600)
        sizes_match = file_info.get('remote_size') == file_info.get('local_size')

        return is_recent and sizes_match

    def mark_scan_complete(self, scan_type: str):
        """Mark a scan as complete."""
        now = datetime.now().isoformat()
        if scan_type == 'full':
            self.cache['scan_stats']['last_full'] = now
        self.cache['scan_stats']['last_incremental'] = now

    def get_cached_date_dirs(self, base_path: str) -> List[str]:
        """Get cached date directories for a path."""
        return self.cache['date_dirs'].get(base_path, [])

    def update_date_dirs(self, base_path: str, date_dirs: List[str]):
        """Update cached date directories."""
        self.cache['date_dirs'][base_path] = date_dirs


class ViconSync:
    """Handles syncing files from Vicon system via rsync/SSH."""

    def __init__(self, host, dry_run=False, cache: Optional[SyncCache] = None):
        self.host = host
        self.dry_run = dry_run
        self.cache = cache or SyncCache(CACHE_FILE)
        self.stats = {
            'downloaded': 0,
            'skipped': 0,
            'skipped_cache': 0,
            'errors': 0,
            'total_size': 0,
            'ssh_calls': 0,
            'batch_operations': 0
        }

    def ssh_execute(self, command, timeout=60):
        """Execute a command on the remote system via SSH."""
        self.stats['ssh_calls'] += 1
        # Escape double quotes in the command for proper shell execution
        escaped_command = command.replace('"', '\\"')
        full_cmd = f'sshpass -p {get_vicon_password()} ssh -o StrictHostKeyChecking=no {SSH_USER}@{self.host} "{escaped_command}"'

        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                shell=True,
                timeout=timeout
            )
            return result.stdout, result.returncode
        except subprocess.TimeoutExpired:
            logger.error(f"SSH command timeout: {command}")
            return "", 1
        except Exception as e:
            logger.error(f"SSH execution error: {e}")
            return "", 1

    def batch_get_files_powershell(self, base_path: str, date_dirs: List[str], has_unreal_subdir: bool) -> Dict[str, Dict[str, Any]]:
        """
        Use PowerShell to get all files and sizes in one batch operation.
        Only gets files from 'unreal' subfolders.
        Returns: Dict[remote_path, {'size': int, 'date_dir': str, 'recording_dir': str (optional)}]
        """
        if not USE_BATCH_OPERATIONS or not date_dirs:
            return {}

        self.stats['batch_operations'] += 1

        # Build PowerShell command to get all files from unreal subfolders
        # Works for both:
        #   E:\Recordings\date\recording\unreal\*.fbx
        #   D:\PostExports\FBX\date\unreal\*.fbx
        search_patterns = []
        for date_dir in date_dirs:
            for ext in FILE_EXTENSIONS:
                # Flat: date\recording\unreal\*.fbx
                search_patterns.append(f"{base_path}\\{date_dir}\\*\\unreal\\*{ext}")
                # Nested: date\recording\unreal\CC\*.fbx (or Vicon, etc.)
                search_patterns.append(f"{base_path}\\{date_dir}\\*\\unreal\\*\\*{ext}")

        ps_patterns = "','".join(search_patterns)
        ps_cmd = f"powershell -Command \"Get-ChildItem -Path '{ps_patterns}' -File -ErrorAction SilentlyContinue | Select-Object FullName,Length | ConvertTo-Json -Compress\""

        logger.info(f"  Running batch PowerShell operation for {len(date_dirs)} date directories...")
        stdout, returncode = self.ssh_execute(ps_cmd, timeout=300)

        if returncode != 0:
            # A failed call is distinct from a successful call that matched
            # nothing. This is the primary enumeration path, so returning an
            # empty dict silently would make a sync that downloaded nothing
            # report success. Count it as a sync error.
            logger.error(f"  Batch operation failed (rc={returncode}) for {base_path}")
            self.stats['errors'] += 1
            return {}

        if not stdout.strip():
            logger.warning(f"  Batch operation returned no results")
            return {}

        try:
            # Parse JSON output
            files_data = json.loads(stdout.strip())

            # Handle single file (not array) case
            if isinstance(files_data, dict):
                files_data = [files_data]

            # Build result dictionary
            result = {}
            for file_info in files_data:
                full_path = file_info.get('FullName', '')
                size = file_info.get('Length', 0)

                if not full_path:
                    continue

                # Parse path to extract date_dir, recording_dir, and unreal_subdir
                # Structures:
                #   date\recording\unreal\file.fbx (flat)
                #   date\recording\unreal\CC\file.fbx (nested)
                #   date\unreal\file.fbx (PostExports)
                path_parts = full_path.split('\\')

                date_dir = None
                recording_dir = None
                unreal_subdir = None

                # Find the date directory and the directory before "unreal"
                for i, part in enumerate(path_parts):
                    if re.match(r'^\d{4}-\d{2}-\d{2}$', part):
                        date_dir = part
                        # The recording dir is the folder right before "unreal"
                        # Look ahead to find "unreal"
                        for j in range(i + 1, len(path_parts)):
                            if path_parts[j] == 'unreal' and j > i:
                                recording_dir = path_parts[j - 1]
                                # Check if there's a subdirectory between "unreal" and the filename
                                # e.g., ...\unreal\CC\file.fbx -> unreal_subdir = 'CC'
                                filename_idx = len(path_parts) - 1
                                if filename_idx > j + 1:
                                    unreal_subdir = path_parts[j + 1]
                                break
                        break

                if date_dir and recording_dir:
                    result[full_path] = {
                        'size': size,
                        'date_dir': date_dir,
                        'recording_dir': recording_dir,
                        'unreal_subdir': unreal_subdir
                    }

            logger.info(f"  Batch operation found {len(result)} files")
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"  Failed to parse batch operation JSON: {e}")
            return {}
        except Exception as e:
            logger.warning(f"  Batch operation error: {e}")
            return {}

    def list_date_directories(self, base_path):
        """List all date directories in the given base path."""
        logger.info(f"Scanning {base_path} for date directories...")

        stdout, returncode = self.ssh_execute(f'dir "{base_path}" /B /AD')

        if returncode != 0:
            # None means "the call failed", distinct from [] meaning "the
            # directory is genuinely empty". The caller counts only the former
            # as a sync error.
            logger.error(f"Failed to list date directories in {base_path}")
            return None

        # Parse directory names (filter for date-like directories: YYYY-MM-DD)
        dirs = []
        for line in stdout.strip().split('\n'):
            line = line.strip()
            # Match YYYY-MM-DD pattern
            if re.match(r'^\d{4}-\d{2}-\d{2}$', line):
                dirs.append(line)

        logger.info(f"Found {len(dirs)} date directories")
        return sorted(dirs)

    def list_recording_directories(self, base_path, date_dir):
        """List all recording subdirectories within a date directory."""
        remote_path = f"{base_path}\\{date_dir}"
        stdout, returncode = self.ssh_execute(f'dir "{remote_path}" /B /AD')

        if returncode != 0:
            # A failed listing is distinct from a date directory that is
            # genuinely empty; only the former is a sync error. Returning []
            # without a trace is how a broken run looked like a clean one.
            logger.error(f"Failed to list recording directories in {remote_path}")
            self.stats['errors'] += 1
            return []

        # Parse directory names
        dirs = []
        for line in stdout.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('.'):
                dirs.append(line)

        return dirs

    def list_files_in_path(self, file_path):
        """List FBX and GLB files in the given directory path."""
        # Check if directory exists
        stdout, returncode = self.ssh_execute(f'if exist "{file_path}" echo EXISTS')
        if 'EXISTS' not in stdout:
            return []

        # List files in directory
        files = []
        for ext in FILE_EXTENSIONS:
            stdout, returncode = self.ssh_execute(f'dir "{file_path}\\*{ext}" /B 2>nul')
            if returncode == 0 and stdout.strip():
                for line in stdout.strip().split('\n'):
                    filename = line.strip()
                    if filename and filename.lower().endswith(ext):
                        files.append(filename)

        return files

    def list_files(self, base_path, date_dir, recording_dir=None, has_unreal_subdir=True, unreal_is_recording_dir=False):
        """
        List FBX and GLB files based on directory structure.
        Only looks in 'unreal' subfolders, including nested subdirs (CC, Vicon).

        Returns:
            List of (filename, unreal_subdir) tuples.
            unreal_subdir is None for flat structure, 'CC'/'Vicon'/etc for nested.

        Args:
            base_path: Base remote path (e.g., E:\\Recordings)
            date_dir: Date directory name (e.g., 2025-01-14)
            recording_dir: Recording subdirectory name (optional)
            has_unreal_subdir: If True, look in unreal subdirectory
            unreal_is_recording_dir: If True, recording_dir IS 'unreal' (no extra unreal subfolder)
        """
        if not recording_dir:
            return []

        if unreal_is_recording_dir:
            # D:\PostExports\FBX\date\unreal\
            file_path = f"{base_path}\\{date_dir}\\{recording_dir}"
        else:
            # E:\Recordings\date\recording\unreal\
            file_path = f"{base_path}\\{date_dir}\\{recording_dir}\\unreal"

        # Get files in the flat unreal/ directory
        flat_files = self.list_files_in_path(file_path)
        result = [(f, None) for f in flat_files]

        # Scan nested subdirectories (e.g., unreal/CC, unreal/Vicon)
        stdout, returncode = self.ssh_execute(f'dir "{file_path}" /B /AD 2>nul')
        if returncode == 0 and stdout.strip():
            for line in stdout.strip().split('\n'):
                nested_dir = line.strip()
                if nested_dir and not nested_dir.startswith('.'):
                    nested_path = f"{file_path}\\{nested_dir}"
                    nested_files = self.list_files_in_path(nested_path)
                    result.extend([(f, nested_dir) for f in nested_files])

        return result

    def get_remote_file_size(self, remote_file_path):
        """Get file size from remote system."""
        # Use dir command to get file size
        stdout, returncode = self.ssh_execute(
            f'dir "{remote_file_path}" /-C'
        )

        if returncode != 0 or not stdout.strip():
            return None

        try:
            # Parse dir output to extract file size
            lines = stdout.strip().split('\n')
            for line in lines:
                if line.strip() and not line.strip().startswith('Volume') and not line.strip().startswith('Directory'):
                    # Look for lines with file information
                    parts = line.split()
                    if len(parts) >= 4 and parts[2].replace(',', '').isdigit():
                        return int(parts[2].replace(',', ''))
            return None
        except:
            return None

    def sync_file(self, base_path, date_dir, filename, recording_dir=None, has_unreal_subdir=True, remote_size=None, unreal_subdir=None):
        """
        Sync a single file using scp with smart sync logic and cache validation.

        Args:
            base_path: Base remote path
            date_dir: Date directory name
            filename: File name to sync
            recording_dir: Recording subdirectory (optional)
            has_unreal_subdir: If True, file is in unreal subdirectory
            remote_size: Pre-fetched remote file size (from batch operation)
            unreal_subdir: Nested subdir within unreal (e.g., 'CC', 'Vicon'), or None for flat
        """
        # Build remote path based on structure
        if has_unreal_subdir:
            if unreal_subdir:
                remote_path = f"{base_path}\\{date_dir}\\{recording_dir}\\unreal\\{unreal_subdir}\\{filename}"
            else:
                remote_path = f"{base_path}\\{date_dir}\\{recording_dir}\\unreal\\{filename}"
        else:
            remote_path = f"{base_path}\\{date_dir}\\{filename}"

        # Local path preserves subdirectory structure
        if unreal_subdir:
            local_dir = os.path.join(LOCAL_PATH, unreal_subdir)
            os.makedirs(local_dir, exist_ok=True)
            local_file = os.path.join(local_dir, filename)
        else:
            local_dir = LOCAL_PATH
            local_file = os.path.join(LOCAL_PATH, filename)

        # Check cache first - skip if recently synced
        if self.cache.is_file_recently_synced(remote_path):
            self.stats['skipped_cache'] += 1
            cached_info = self.cache.get_file_cache(remote_path)
            logger.info(f"  ⚡ Skipped {filename} (cached, size: {cached_info.get('local_size')} bytes)")
            return True

        # Convert Windows path for scp (E:\... -> E:/...)
        remote_scp_path = remote_path.replace('\\', '/')

        # Check if file exists locally and compare sizes (smart sync)
        should_download = True
        if os.path.exists(local_file):
            local_size = os.path.getsize(local_file)

            # Use pre-fetched size or fetch it now
            if remote_size is None:
                remote_size = self.get_remote_file_size(remote_path)

            if remote_size and local_size == remote_size:
                # File exists and size matches - skip
                self.stats['skipped'] += 1
                logger.info(f"  ↓ Skipped {filename} (already exists, size: {local_size} bytes)")
                # Update cache
                self.cache.update_file(remote_path, remote_size, local_size, True)
                return True
            elif remote_size:
                logger.info(f"  → Updating {filename} (local: {local_size}, remote: {remote_size} bytes)")
            else:
                logger.warning(f"  ? Could not verify remote size for {filename}, re-downloading")

        if self.dry_run:
            if should_download:
                logger.info(f"  [DRY-RUN] Would download {filename}")
                self.stats['downloaded'] += 1
            return True

        # Download file using scp
        try:
            logger.info(f"  ⬇ Downloading {filename}...")

            scp_cmd = f'sshpass -p {get_vicon_password()} scp -o StrictHostKeyChecking=no {SSH_USER}@{self.host}:{remote_scp_path} {local_file}'

            result = subprocess.run(
                scp_cmd,
                capture_output=True,
                text=True,
                shell=True,
                timeout=300  # 5 minute timeout per file
            )

            if result.returncode == 0:
                size = os.path.getsize(local_file)
                self.stats['total_size'] += size
                self.stats['downloaded'] += 1
                logger.info(f"  ✓ Downloaded {filename} ({size} bytes)")
                # Update cache with successful download
                self.cache.update_file(remote_path, size, size, True)
                # Trigger immediate FBX→GLB conversion
                if filename.lower().endswith('.fbx'):
                    self._convert_fbx_to_glb(filename, local_dir)
                return True
            else:
                logger.error(f"  ✗ Failed to download {filename}: {result.stderr}")
                self.stats['errors'] += 1
                # Update cache with failed download
                self.cache.update_file(remote_path, remote_size or 0, 0, False)
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"  ✗ Timeout downloading {filename}")
            self.stats['errors'] += 1
            self.cache.update_file(remote_path, remote_size or 0, 0, False)
            return False
        except Exception as e:
            logger.error(f"  ✗ Error downloading {filename}: {e}")
            self.stats['errors'] += 1
            self.cache.update_file(remote_path, remote_size or 0, 0, False)
            return False

    def _convert_fbx_to_glb(self, filename, directory):
        """
        Convert an FBX file to GLB immediately after download.

        Args:
            filename: Name of the FBX file
            directory: Directory containing the FBX file
        """
        node_bin = "/home/gomer/.nvm/versions/node/v22.20.0/bin/node"
        convert_script = "/home/gomer/node_servers/fbx2glb/convert_single.js"

        try:
            logger.info(f"  Converting {filename} to GLB...")
            result = subprocess.run(
                [node_bin, convert_script, filename, directory],
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )

            if result.returncode == 0:
                glb_filename = filename.rsplit('.', 1)[0] + '.glb'
                logger.info(f"  ✓ Converted {filename} -> {glb_filename}")
            else:
                logger.warning(f"  ✗ GLB conversion failed for {filename}: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            logger.warning(f"  ✗ GLB conversion timeout for {filename}")
        except Exception as e:
            logger.warning(f"  ✗ GLB conversion error for {filename}: {e}")

    def batch_sync_subdir(self, base_path, date_dirs, subdir_config):
        """Batch scan and sync files from a specific recording subdirectory."""
        subdir = subdir_config['subdir']
        extensions = subdir_config['extensions']
        local_path = subdir_config['local_path']

        os.makedirs(local_path, exist_ok=True)
        self.stats['batch_operations'] += 1

        # Build PowerShell search patterns
        search_patterns = []
        for date_dir in date_dirs:
            for ext in extensions:
                search_patterns.append(f"{base_path}\\{date_dir}\\*\\{subdir}\\*{ext}")

        ps_patterns = "','".join(search_patterns)
        ps_cmd = (
            f"powershell -Command \"Get-ChildItem -Path '{ps_patterns}' "
            f"-File -ErrorAction SilentlyContinue "
            f"| Select-Object FullName,Length | ConvertTo-Json -Compress\""
        )

        logger.info(f"  Running batch scan for {subdir} ({', '.join(extensions)})...")
        stdout, returncode = self.ssh_execute(ps_cmd, timeout=300)

        if returncode != 0:
            # An SSH/PowerShell failure was previously reported at INFO level as
            # a benign "no files found". It is a failed call, not an empty
            # result, so it has to count against the cycle.
            logger.error(f"  Failed to scan {subdir} in {base_path} (rc={returncode})")
            self.stats['errors'] += 1
            return

        if not stdout.strip():
            logger.info(f"  No {subdir} files found")
            return

        try:
            files_data = json.loads(stdout.strip())
            if isinstance(files_data, dict):
                files_data = [files_data]
        except json.JSONDecodeError:
            logger.warning(f"  Failed to parse {subdir} batch JSON")
            return

        logger.info(f"  Found {len(files_data)} {subdir} files")

        for file_info in files_data:
            full_path = file_info.get('FullName', '')
            remote_size = file_info.get('Length', 0)

            if not full_path:
                continue

            filename = full_path.split('\\')[-1]

            # Check cache
            if self.cache.is_file_recently_synced(full_path):
                self.stats['skipped_cache'] += 1
                continue

            local_file = os.path.join(local_path, filename)

            # Smart sync: check if file exists with same size
            if os.path.exists(local_file):
                local_size = os.path.getsize(local_file)
                if local_size == remote_size:
                    self.stats['skipped'] += 1
                    self.cache.update_file(full_path, remote_size, local_size, True)
                    continue

            if self.dry_run:
                logger.info(f"    [DRY-RUN] Would download {filename}")
                self.stats['downloaded'] += 1
                continue

            # Download via SCP
            remote_scp_path = full_path.replace('\\', '/')
            scp_cmd = (
                f'sshpass -p {SSH_PASS} scp -o StrictHostKeyChecking=no '
                f'{SSH_USER}@{self.host}:{remote_scp_path} {local_file}'
            )

            try:
                logger.info(f"    ⬇ Downloading {filename}...")
                result = subprocess.run(
                    scp_cmd, capture_output=True, text=True,
                    shell=True, timeout=600
                )

                if result.returncode == 0:
                    size = os.path.getsize(local_file)
                    self.stats['total_size'] += size
                    self.stats['downloaded'] += 1
                    logger.info(f"    ✓ Downloaded {filename} ({size} bytes)")
                    self.cache.update_file(full_path, size, size, True)
                else:
                    logger.error(f"    ✗ Failed: {filename}: {result.stderr}")
                    self.stats['errors'] += 1
                    self.cache.update_file(full_path, remote_size, 0, False)
            except subprocess.TimeoutExpired:
                logger.error(f"    ✗ Timeout: {filename}")
                self.stats['errors'] += 1
            except Exception as e:
                logger.error(f"    ✗ Error: {filename}: {e}")
                self.stats['errors'] += 1

    def run(self):
        """Main sync process with incremental scanning and batch operations."""
        start_time = datetime.now()

        # Determine if we should do a full scan
        do_full_scan = self.cache.should_do_full_scan()
        scan_type = "FULL" if do_full_scan else "INCREMENTAL"

        if self.dry_run:
            logger.info("=== DRY RUN MODE - No files will be transferred ===")

        logger.info(f"Starting Vicon file sync from {self.host}")
        logger.info(f"Scan type: {scan_type}")
        if not do_full_scan:
            logger.info(f"Incremental: Scanning last {INCREMENTAL_DAYS} days")
        logger.info(f"Target directory: {LOCAL_PATH}")
        logger.info(f"File types: {', '.join(FILE_EXTENSIONS)}")
        logger.info(f"Remote sources: {len(REMOTE_PATHS)}")
        logger.info(f"Cache validity: {CACHE_VALIDITY_HOURS} hours")
        logger.info(f"Batch operations: {'Enabled' if USE_BATCH_OPERATIONS else 'Disabled'}")

        # Calculate cutoff date for incremental scan
        if not do_full_scan:
            cutoff_date = (datetime.now() - timedelta(days=INCREMENTAL_DAYS)).strftime('%Y-%m-%d')
            logger.info(f"Cutoff date: {cutoff_date}")

        # Process each remote path
        for remote_config in REMOTE_PATHS:
            base_path = remote_config['base_path']
            has_unreal_subdir = remote_config['has_unreal_subdir']
            source_name = remote_config['name']

            logger.info(f"\n{'='*60}")
            logger.info(f"SOURCE: {source_name}")
            logger.info(f"{'='*60}")

            # Get all date directories
            all_date_dirs = self.list_date_directories(base_path)

            if all_date_dirs is None:
                logger.error(f"Failed to list {base_path} — treating as sync error")
                self.stats['errors'] += 1
                continue

            if not all_date_dirs:
                logger.warning(f"No date directories found in {base_path}")
                continue

            # Cache the date directories
            self.cache.update_date_dirs(base_path, all_date_dirs)

            # Filter date directories based on scan type
            if do_full_scan:
                date_dirs = all_date_dirs
                logger.info(f"Full scan: Processing all {len(date_dirs)} date directories")
            else:
                # Only process recent date directories
                date_dirs = [d for d in all_date_dirs if d >= cutoff_date]
                logger.info(f"Incremental scan: Processing {len(date_dirs)} recent directories (out of {len(all_date_dirs)} total)")

            if not date_dirs:
                logger.info("No directories to process in this range")
                continue

            # Use batch operation if enabled
            if USE_BATCH_OPERATIONS:
                logger.info(f"\n{'='*60}")
                logger.info("BATCH OPERATION MODE")
                logger.info(f"{'='*60}")

                # Get all files at once with batch PowerShell
                batch_files = self.batch_get_files_powershell(base_path, date_dirs, has_unreal_subdir)

                if batch_files:
                    logger.info(f"Processing {len(batch_files)} files from batch operation...")

                    for remote_path, file_info in batch_files.items():
                        # Extract filename from Windows path (use backslash split, not os.path.basename)
                        filename = remote_path.split('\\')[-1]
                        date_dir = file_info['date_dir']
                        recording_dir = file_info.get('recording_dir')
                        remote_size = file_info['size']

                        self.sync_file(
                            base_path,
                            date_dir,
                            filename,
                            recording_dir,
                            has_unreal_subdir,
                            remote_size=remote_size,
                            unreal_subdir=file_info.get('unreal_subdir')
                        )
                else:
                    logger.warning("Batch operation returned no files, falling back to individual operations")
                    # Fall through to individual operations

            # Fall back to individual operations if batch is disabled or failed
            if not USE_BATCH_OPERATIONS or not batch_files:
                # Process each date directory individually (old method)
                unreal_is_recording_dir = remote_config.get('unreal_is_recording_dir', False)

                for date_dir in date_dirs:
                    logger.info(f"\nProcessing {date_dir}...")

                    # Both sources now look for 'unreal' subfolders
                    recording_dirs = self.list_recording_directories(base_path, date_dir)

                    # Filter to only process directories that contain 'unreal' subfolder
                    # or are named 'unreal' themselves
                    valid_recording_dirs = []
                    for recording_dir in recording_dirs:
                        if unreal_is_recording_dir:
                            # For D:\PostExports\FBX: only process if dir IS 'unreal'
                            if recording_dir == 'unreal':
                                valid_recording_dirs.append(recording_dir)
                        else:
                            # For E:\Recordings: process all dirs (will check for unreal subfolder)
                            valid_recording_dirs.append(recording_dir)

                    logger.info(f"  Found {len(valid_recording_dirs)} recording directories with unreal subfolders")

                    for recording_dir in valid_recording_dirs:
                        # List files in unreal subdirectory (returns (filename, unreal_subdir) tuples)
                        files = self.list_files(base_path, date_dir, recording_dir, has_unreal_subdir, unreal_is_recording_dir)

                        if files:
                            logger.info(f"  {recording_dir}: {len(files)} file(s)")

                            for filename, unreal_subdir in files:
                                self.sync_file(base_path, date_dir, filename, recording_dir, has_unreal_subdir, unreal_subdir=unreal_subdir)

            # Sync extra recording subdirs (livelink, metadata, shogun_live)
            # Only for E:\Recordings, not D:\PostExports
            if not remote_config.get('unreal_is_recording_dir', False):
                for subdir_config in EXTRA_RECORDING_SUBDIRS:
                    logger.info(f"\n{'='*60}")
                    logger.info(f"EXTRA SUBDIR: {subdir_config['subdir']}")
                    logger.info(f"{'='*60}")
                    self.batch_sync_subdir(base_path, date_dirs, subdir_config)

        # Mark scan as complete and save cache
        self.cache.mark_scan_complete('full' if do_full_scan else 'incremental')
        if not self.dry_run:
            self.cache.save()

        # Print summary
        duration = datetime.now() - start_time
        logger.info("\n" + "="*60)
        logger.info("SYNC SUMMARY")
        logger.info("="*60)
        logger.info(f"Scan type:       {scan_type}")
        logger.info(f"Downloaded:      {self.stats['downloaded']} files")
        logger.info(f"Skipped (size):  {self.stats['skipped']} files (already up to date)")
        logger.info(f"Skipped (cache): {self.stats['skipped_cache']} files (recently synced)")
        logger.info(f"Errors:          {self.stats['errors']} files")
        logger.info(f"SSH calls:       {self.stats['ssh_calls']}")
        logger.info(f"Batch ops:       {self.stats['batch_operations']}")

        if not self.dry_run and self.stats['total_size'] > 0:
            size_mb = self.stats['total_size'] / (1024 * 1024)
            logger.info(f"Total size:      {size_mb:.2f} MB")

        logger.info(f"Duration:        {duration}")
        logger.info("="*60)

        # Return stats and duration for monitoring
        return {
            'downloaded': self.stats['downloaded'],
            'skipped': self.stats['skipped'],
            'skipped_cache': self.stats['skipped_cache'],
            'errors': self.stats['errors'],
            'total_size_bytes': self.stats['total_size'],
            'duration_seconds': duration.total_seconds(),
            'scan_type': scan_type,
            'ssh_calls': self.stats['ssh_calls'],
            'batch_operations': self.stats['batch_operations']
        }


def _control_ssh(command, timeout=20):
    """Module-level SSH exec for the control API (no per-sync stats).
    Returns (stdout, returncode)."""
    try:
        host, _ = vicon_host.resolve_vicon_host_cached(probe_port=22)
    except vicon_host.ViconOffline as exc:
        logger.warning(f"control SSH skipped, Vicon PC unreachable: {exc}")
        return "", 1
    escaped_command = command.replace('"', '\\"')
    full_cmd = (
        f'sshpass -p {SSH_PASS} ssh -o StrictHostKeyChecking=no '
        f'{SSH_USER}@{host} "{escaped_command}"'
    )
    try:
        result = subprocess.run(
            full_cmd, capture_output=True, text=True, shell=True, timeout=timeout
        )
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        logger.warning(f"control SSH timeout: {command}")
        return "", 1
    except Exception as e:
        logger.warning(f"control SSH error: {e}")
        return "", 1


def trigger_blackmagic_sync():
    """Fire the Blackmagic sync scheduled task on the Vicon PC (fire-and-forget).
    It runs in the interactive desktop session where the S: rclone mount lives.
    Returns (ok: bool, message: str)."""
    out, rc = _control_ssh(f"schtasks /run /tn {BLACKMAGIC_TASK}")
    if rc == 0:
        logger.info("Blackmagic sync task triggered")
        return True, "triggered"
    msg = out.strip() or "ssh/schtasks error"
    logger.warning(f"Blackmagic task trigger failed (rc={rc}): {msg}")
    return False, msg


def get_blackmagic_status():
    """Read bmcam_sync_status.json from the Vicon PC over SSH (cached ~5s).
    Always returns a dict with at least a 'state' key."""
    now = time.monotonic()
    cached = _BM_STATUS_CACHE["data"]
    if cached is not None and (now - _BM_STATUS_CACHE["ts"]) < 5:
        return cached

    out, rc = _control_ssh(f'type "{BLACKMAGIC_STATUS_PATH}"', timeout=15)
    if rc != 0 or not out.strip():
        data = {"state": "unknown", "error": "no status file yet"}
    else:
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = {"state": "unknown", "error": "could not parse status file"}
        else:
            updated = data.get("updated_at")
            if updated:
                try:
                    # updated_at is written as tz-aware UTC by sync_clips.py; the
                    # control server may run in a different local tz, so compare
                    # in UTC. (TypeError guards a legacy naive timestamp.)
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(updated)).total_seconds()
                    data["age_seconds"] = round(age, 1)
                    if data.get("state") == "running" and age > BLACKMAGIC_STATUS_STALE_SECONDS:
                        data["state"] = "stale"
                except (ValueError, TypeError):
                    pass

    _BM_STATUS_CACHE["ts"] = now
    _BM_STATUS_CACHE["data"] = data
    return data


class ControlHandler(http.server.BaseHTTPRequestHandler):
    """Handles localhost-only control API:
       POST /trigger  -> wake up main loop to run a sync now
       GET  /status   -> return last run info + whether a sync is currently running
    """

    def _send_json(self, code, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/trigger":
            running = SYNC_LOCK.locked()
            TRIGGER_EVENT.set()
            bm_ok, bm_msg = trigger_blackmagic_sync()
            self._send_json(200, {
                "ok": True,
                "message": "sync already running, request noted" if running else "sync triggered",
                "was_running": running,
                "blackmagic": {"triggered": bm_ok, "message": bm_msg},
            })
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/status":
            try:
                ip, node_name = vicon_host.resolve_vicon_host_cached(probe_port=22)
                vicon = {"host": ip, "dns_name": node_name, "online": True}
            except vicon_host.ViconOffline as exc:
                vicon = {"host": None, "dns_name": None, "online": False,
                         "error": str(exc)}
            self._send_json(200, {
                "ok": True,
                "vicon": vicon,
                "mocap": {
                    "running": SYNC_LOCK.locked(),
                    "state": LAST_RUN.get("state"),
                    "last_run": LAST_RUN.get("last_run"),
                    "started_at": LAST_RUN.get("started_at"),
                    "last_stats": LAST_RUN.get("last_stats"),
                },
                "blackmagic": get_blackmagic_status(),
            })
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt, *args):
        # Suppress access log spam; let real syncs own the logs.
        pass


def start_control_server():
    """Start the localhost-only control API in a daemon thread."""
    try:
        server = http.server.ThreadingHTTPServer((CONTROL_HOST, CONTROL_PORT), ControlHandler)
    except OSError as e:
        logger.warning(f"Could not bind control API on {CONTROL_HOST}:{CONTROL_PORT}: {e}")
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="control-api")
    thread.start()
    logger.info(f"Control API listening on http://{CONTROL_HOST}:{CONTROL_PORT}")
    return server


def main():
    """Main entry point - runs continuously with 24-hour intervals."""
    dry_run = '--dry-run' in sys.argv or '-n' in sys.argv
    run_once = '--once' in sys.argv
    force_full = '--full' in sys.argv or '-f' in sys.argv
    clear_cache = '--clear-cache' in sys.argv

    if '--help' in sys.argv or '-h' in sys.argv:
        print("Usage: python3 sync_vicon_rsync.py [OPTIONS]")
        print("")
        print("Sync FBX and GLB files from Vicon system via SSH/SCP.")
        print("Runs continuously with 24-hour intervals between syncs.")
        print("Uses smart caching and batch operations for optimal performance.")
        print("")
        print("Options:")
        print("  --dry-run, -n     Show what would be transferred without actually syncing")
        print("  --once            Run once and exit (don't loop)")
        print("  --full, -f        Force a full scan (ignore incremental logic)")
        print("  --clear-cache     Clear cache and start fresh")
        print("  --help, -h        Show this help message")
        print("")
        print(f"Configuration:")
        print(f"  Host:     {SSH_USER}@<discovered vicon* tailnet peer>")
        print(f"  Sources:")
        for config in REMOTE_PATHS:
            print(f"    - {config['name']}: {config['base_path']}")
        print(f"  Local:    {LOCAL_PATH}")
        print(f"  Types:    {', '.join(FILE_EXTENSIONS)}")
        print(f"  Log:      {LOG_FILE}")
        print(f"  Cache:    {CACHE_FILE}")
        print(f"  Interval: {CLIENT_MONITOR_INTERVAL} seconds (24 hours)")
        print("")
        print(f"Optimization:")
        print(f"  Incremental scan:  Last {INCREMENTAL_DAYS} days")
        print(f"  Full scan every:   {FULL_SCAN_INTERVAL // 86400} days")
        print(f"  Cache validity:    {CACHE_VALIDITY_HOURS} hours")
        print(f"  Batch operations:  {'Enabled' if USE_BATCH_OPERATIONS else 'Disabled'}")
        sys.exit(0)

    # Clear cache if requested
    if clear_cache:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
            logger.info(f"Cache cleared: {CACHE_FILE}")
        else:
            logger.info("No cache file to clear")

    # Initialize cache
    cache = SyncCache(CACHE_FILE)

    # Force full scan if requested
    if force_full:
        logger.info("Forcing full scan (--full flag)")
        cache.cache['scan_stats']['last_full'] = None

    # Initialize Client Monitor
    monitor = ClientMonitor(
        api_url=CLIENT_MONITOR_API_URL,
        client_id=CLIENT_MONITOR_ID,
        client_name=CLIENT_MONITOR_NAME,
        description=CLIENT_MONITOR_DESCRIPTION,
        heartbeat_interval=CLIENT_MONITOR_INTERVAL
    )

    logger.info("="*60)
    logger.info("Vicon File Sync - Continuous Mode")
    logger.info("="*60)
    logger.info(f"Sync interval: {CLIENT_MONITOR_INTERVAL} seconds (24 hours)")
    logger.info(f"Run once mode: {run_once}")
    logger.info(f"Dry run mode: {dry_run}")
    logger.info("="*60)

    # Start localhost control API (skip in --once mode)
    if not run_once:
        start_control_server()

    # Main sync loop
    while True:
        try:
            # Clear any pending trigger now that we're starting a fresh cycle.
            TRIGGER_EVENT.clear()

            # Locate the Vicon PC before touching SSH. It rejoins the tailnet
            # under a new node identity (and a new IP) after a reinstall, and
            # an unreachable PC must be an error rather than an empty scan.
            try:
                host, node_name = vicon_host.resolve_vicon_host(probe_port=22)
            except vicon_host.ViconOffline as exc:
                logger.error(f"Vicon PC unreachable: {exc}")
                LAST_RUN["state"] = "offline"
                if not dry_run:
                    monitor.send_heartbeat_with_stats(
                        status="error",
                        message=f"Vicon PC unreachable: {exc}",
                        stats={"error_type": "ViconOffline"},
                    )
                if run_once:
                    sys.exit(1)
                logger.info(
                    f"Retrying in {CLIENT_MONITOR_INTERVAL} seconds (or until /trigger)..."
                )
                if TRIGGER_EVENT.wait(timeout=CLIENT_MONITOR_INTERVAL):
                    logger.info("Manual trigger received — retrying now")
                continue

            logger.info(f"Vicon PC found: {node_name} at {host}")

            # Run sync (lock prevents concurrent runs from rogue invocations)
            with SYNC_LOCK:
                LAST_RUN["state"] = "running"
                LAST_RUN["started_at"] = datetime.now().isoformat()

                logger.info(f"\n{'='*60}")
                logger.info(f"Starting sync cycle at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"{'='*60}")

                syncer = ViconSync(host=host, dry_run=dry_run, cache=cache)
                stats = syncer.run()

                LAST_RUN["state"] = "idle"
                LAST_RUN["last_run"] = datetime.now().isoformat()
                LAST_RUN["last_stats"] = stats

            # Determine status based on errors
            if stats['errors'] > 0:
                status = "warning"
                message = f"Sync completed with {stats['errors']} error(s)"
            else:
                status = "success"
                message = f"Sync completed successfully ({stats['scan_type']} scan)"

            # Send heartbeat with stats (only if not dry-run)
            if not dry_run:
                monitor.send_heartbeat_with_stats(
                    status=status,
                    message=message,
                    stats={
                        "scan_type": stats['scan_type'],
                        "files_downloaded": stats['downloaded'],
                        "files_skipped_size": stats['skipped'],
                        "files_skipped_cache": stats['skipped_cache'],
                        "files_errors": stats['errors'],
                        "total_size_mb": round(stats['total_size_bytes'] / (1024 * 1024), 2),
                        "duration_seconds": round(stats['duration_seconds'], 2),
                        "ssh_calls": stats['ssh_calls'],
                        "batch_operations": stats['batch_operations'],
                        "sources_count": len(REMOTE_PATHS),
                        "sources": [cfg['name'] for cfg in REMOTE_PATHS]
                    }
                )

            # Exit if run-once mode
            if run_once:
                logger.info("Run-once mode: Exiting after single sync")
                sys.exit(0 if stats['errors'] == 0 else 1)

            # Sleep until next sync (interruptible — control API /trigger wakes us up)
            next_sync = datetime.now().timestamp() + CLIENT_MONITOR_INTERVAL
            next_sync_time = datetime.fromtimestamp(next_sync).strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"\n{'='*60}")
            logger.info(f"Sync cycle completed. Next sync at: {next_sync_time}")
            logger.info(f"Sleeping for {CLIENT_MONITOR_INTERVAL} seconds (24 hours, or until /trigger)...")
            logger.info(f"{'='*60}\n")

            if TRIGGER_EVENT.wait(timeout=CLIENT_MONITOR_INTERVAL):
                logger.info("Sync triggered manually via control API — waking up early")

        except KeyboardInterrupt:
            logger.info("\n\nSync interrupted by user (Ctrl+C)")
            monitor.send_heartbeat_with_stats(
                status="error",
                message="Sync interrupted by user",
                stats={"error_type": "KeyboardInterrupt"}
            )
            sys.exit(1)
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            monitor.send_heartbeat_with_stats(
                status="error",
                message=f"Fatal error: {str(e)}",
                stats={"error_type": type(e).__name__}
            )

            # In continuous mode, wait a bit and retry instead of crashing
            if not run_once:
                logger.error("Will retry in 1 hour (or sooner if /trigger)...")
                LAST_RUN["state"] = "error_backoff"
                if TRIGGER_EVENT.wait(timeout=3600):
                    logger.info("Manual trigger received during error backoff — retrying now")
                continue
            else:
                sys.exit(1)


if __name__ == "__main__":
    main()
