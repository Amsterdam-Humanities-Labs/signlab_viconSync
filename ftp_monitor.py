#!/usr/bin/env python3
"""
Continuous FTP Monitor for Vicon Recordings
Monitors Vicon FTP server for file changes in real-time, categorizing files by type
and grouping them by capture session.
"""

import os
import sys
import json
import time
import logging
import requests
import socket
from ftplib import FTP, error_perm
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any
from pathlib import Path

import vicon_host

try:
    from db_writer import DatabaseWriter
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    print("Warning: Database module not available. Install pymysql: pip3 install pymysql")


class ClientMonitor:
    """
    Client Monitor API wrapper for Python scripts
    """

    def __init__(
        self,
        api_url: str,
        client_id: str,
        client_name: str,
        description: str = "",
        heartbeat_interval: int = 3600
    ):
        """
        Initialize the client monitor

        Args:
            api_url: Base URL of the Client Monitor API
            client_id: Unique identifier for this client
            client_name: Display name for the client
            description: Description of what this client does
            heartbeat_interval: Expected heartbeat interval in seconds (default: 3600)
        """
        self.api_url = api_url
        self.client_id = client_id
        self.client_name = client_name
        self.description = description
        self.heartbeat_interval = heartbeat_interval
        self.hostname = socket.gethostname()
        self.logger = logging.getLogger(__name__)

    def register(self, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Register this client with the monitoring system

        Args:
            metadata: Optional custom metadata dictionary

        Returns:
            API response dictionary

        Raises:
            requests.exceptions.RequestException: If the API call fails
        """
        data = {
            "client_id": self.client_id,
            "client_name": self.client_name,
            "description": self.description,
            "heartbeat_interval": self.heartbeat_interval,
            "metadata": metadata or {
                "hostname": self.hostname,
                "python_version": sys.version.split()[0],
                "registered_at": datetime.now().isoformat()
            }
        }

        try:
            response = requests.post(
                f"{self.api_url}?action=register",
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            response.raise_for_status()

            result = response.json()
            if result.get("success"):
                self.logger.info(f"Client registered successfully: {self.client_id}")
            else:
                self.logger.warning(f"Registration failed: {result.get('errors')}")

            return result
        except Exception as e:
            self.logger.error(f"Failed to register client: {e}")
            return {"success": False, "errors": [str(e)]}

    def send_heartbeat(self, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Send a heartbeat to update the last_seen timestamp

        Args:
            metadata: Optional custom metadata to include with the heartbeat

        Returns:
            API response dictionary

        Raises:
            requests.exceptions.RequestException: If the API call fails
        """
        data = {
            "client_id": self.client_id,
            "metadata": metadata or {
                "last_run": datetime.now().isoformat(),
                "hostname": self.hostname
            }
        }

        try:
            response = requests.post(
                f"{self.api_url}?action=heartbeat",
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            response.raise_for_status()

            result = response.json()
            if result.get("success"):
                status = result.get("data", {}).get("status", "unknown")
                self.logger.debug(f"Heartbeat sent successfully - Status: {status}")
            else:
                self.logger.warning(f"Heartbeat failed: {result.get('errors')}")

            return result
        except Exception as e:
            self.logger.debug(f"Failed to send heartbeat: {e}")
            return {"success": False, "errors": [str(e)]}

    def send_heartbeat_with_stats(
        self,
        status: str,
        message: str,
        stats: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Send a heartbeat with status and statistics

        Args:
            status: Status of the execution (e.g., 'success', 'error', 'warning')
            message: Description message
            stats: Optional statistics dictionary

        Returns:
            API response dictionary
        """
        metadata = {
            "last_run": datetime.now().isoformat(),
            "hostname": self.hostname,
            "status": status,
            "message": message
        }

        if stats:
            metadata.update(stats)

        return self.send_heartbeat(metadata)


class FtpConnectionManager:
    """Manages persistent FTP connection with auto-reconnect."""

    def __init__(self, config: Dict):
        self.config = config
        self.ftp: Optional[FTP] = None
        self.last_operation = time.time()
        self.connection_attempts = 0
        self.logger = logging.getLogger(__name__)

    def connect(self) -> bool:
        """Establish FTP connection with retry logic."""
        retry_count = 0
        max_retries = self.config['monitoring']['max_connection_retries']
        backoff_base = self.config['monitoring']['retry_backoff_base']
        backoff_max = self.config['monitoring']['retry_backoff_max']

        while retry_count < max_retries:
            try:
                # The Vicon PC changes tailnet address when it rejoins, so
                # resolve on every attempt rather than trusting a fixed host.
                host, node_name = vicon_host.resolve_vicon_host(
                    prefix=self.config['ftp'].get('host_prefix', vicon_host.DEFAULT_PREFIX),
                    probe_port=21,
                    timeout=self.config['ftp']['timeout'],
                )
                self.logger.info(f"Connecting to FTP server {node_name} at {host}...")
                self.ftp = FTP(timeout=self.config['ftp']['timeout'])
                self.ftp.connect(host)
                self.ftp.login(self.config['ftp']['user'], self.config['ftp']['password'])
                self.logger.info("FTP connected successfully")
                self.connection_attempts = 0
                self.last_operation = time.time()
                return True
            except Exception as e:
                retry_count += 1
                self.connection_attempts += 1
                backoff = min(backoff_base * (2 ** (retry_count - 1)), backoff_max)
                self.logger.error(f"Connection failed (attempt {retry_count}/{max_retries}): {e}")
                if retry_count < max_retries:
                    self.logger.info(f"Retrying in {backoff} seconds...")
                    time.sleep(backoff)

        return False

    def ensure_connected(self) -> bool:
        """Ensure connection is alive, reconnect if needed."""
        if self.ftp is None:
            return self.connect()

        try:
            # Send NOOP to check connection
            self.ftp.voidcmd("NOOP")
            self.last_operation = time.time()
            return True
        except:
            self.logger.warning("Connection lost, reconnecting...")
            return self.connect()

    def execute_with_retry(self, operation, *args, max_retries=3, **kwargs):
        """Execute FTP operation with retry logic."""
        for attempt in range(max_retries):
            if not self.ensure_connected():
                continue

            try:
                result = operation(*args, **kwargs)
                self.last_operation = time.time()
                return result
            except error_perm as e:
                # Permanent errors (550, 553, etc.) - don't retry
                error_msg = str(e)
                if error_msg.startswith('550') or error_msg.startswith('553'):
                    self.logger.debug(f"Skipping due to permission/access error: {e}")
                    raise  # Don't retry, just raise
                # Other permission errors, retry
                self.logger.error(f"Operation failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    self.ftp = None  # Force reconnect
            except Exception as e:
                self.logger.error(f"Operation failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    self.ftp = None  # Force reconnect

        raise Exception(f"Operation failed after {max_retries} attempts")

    def close(self):
        """Close FTP connection gracefully."""
        if self.ftp:
            try:
                self.ftp.quit()
            except:
                pass
            self.ftp = None


class StateManager:
    """Manages in-memory state and periodic persistence."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self.state = {
            'known_files': {},  # path -> {size, last_modified, first_seen}
            'captures': {},  # capture_id -> capture_data
            'last_save': None
        }
        self.save_interval = 60  # Save every 60 seconds
        self.last_save_time = time.time()
        self.logger = logging.getLogger(__name__)
        self.load_state()

    def load_state(self):
        """Load state from disk."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    self.state = json.load(f)
                self.logger.info(f"Loaded state with {len(self.state['known_files'])} files")
            except Exception as e:
                self.logger.error(f"Failed to load state: {e}")

    def save_state(self, force=False):
        """Save state to disk (atomic write)."""
        now = time.time()
        if not force and (now - self.last_save_time) < self.save_interval:
            return

        try:
            temp_file = f"{self.state_file}.tmp"
            self.state['last_save'] = datetime.now().isoformat()

            with open(temp_file, 'w') as f:
                json.dump(self.state, f, indent=2)

            os.replace(temp_file, self.state_file)
            self.last_save_time = now
            self.logger.debug("State saved successfully")
        except Exception as e:
            self.logger.error(f"Failed to save state: {e}")

    def get_file_info(self, path: str) -> Optional[Dict]:
        """Get file info from state."""
        return self.state['known_files'].get(path)

    def update_file_info(self, path: str, size: int, status: str = 'complete', subdir: str = ''):
        """Update file info in state."""
        now = datetime.now().isoformat()

        if path not in self.state['known_files']:
            self.state['known_files'][path] = {
                'size': size,
                'first_seen': now,
                'last_modified': now,
                'status': status,
                'subdir': subdir
            }
        else:
            self.state['known_files'][path]['size'] = size
            self.state['known_files'][path]['last_modified'] = now
            self.state['known_files'][path]['status'] = status
            self.state['known_files'][path]['subdir'] = subdir

    def get_capture(self, capture_id: str) -> Optional[Dict]:
        """Get capture data from state."""
        return self.state['captures'].get(capture_id)

    def update_capture(self, capture_id: str, capture_data: Dict):
        """Update capture data in state."""
        self.state['captures'][capture_id] = capture_data


class FileClassifier:
    """Classifies files by type based on path and extension."""

    def __init__(self, config: Dict):
        self.config = config
        self.logger = logging.getLogger(__name__)

    def classify(self, file_path: str, filename: str) -> str:
        """Classify file type based on path and extension."""
        # Normalize path to use forward slashes for consistent checking
        normalized_path = file_path.replace('\\', '/').lower()

        # Unreal: Files in */unreal/ subdirectory (.fbx, .glb)
        if '/unreal/' in normalized_path:
            for ext in self.config['file_types']['unreal']:
                if filename.lower().endswith(ext):
                    return 'unreal'

        # OBS: MKV video files anywhere in recording
        for ext in self.config['file_types']['obs']:
            if filename.lower().endswith(ext):
                return 'obs'

        # Vicon: FBX files NOT in unreal subdirectory
        for ext in self.config['file_types']['vicon']:
            if filename.lower().endswith(ext):
                return 'vicon'

        # All other files
        return 'other'


class ChangeDetector:
    """Detects changes in files (new, modified, deleted)."""

    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        self.logger = logging.getLogger(__name__)
        self.recent_sizes = {}  # path -> [(timestamp, size)]

    def detect_change(self, path: str, size: int) -> Optional[str]:
        """Detect if file is new, modified, or growing."""
        known_file = self.state_manager.get_file_info(path)

        # New file
        if known_file is None:
            return 'new'

        # Check if file size changed
        if known_file['size'] != size:
            # Track size changes to detect growing files
            now = time.time()
            if path not in self.recent_sizes:
                self.recent_sizes[path] = []

            self.recent_sizes[path].append((now, size))

            # Keep only last 10 seconds of data
            self.recent_sizes[path] = [
                (t, s) for t, s in self.recent_sizes[path]
                if now - t < 10
            ]

            # If size changed recently, mark as growing
            if len(self.recent_sizes[path]) > 1:
                return 'growing'

            return 'modified'

        return None

    def get_file_status(self, path: str, size: int) -> str:
        """Determine if file is complete or still growing."""
        if path not in self.recent_sizes or len(self.recent_sizes[path]) < 2:
            return 'complete'

        # Check if size stabilized (no changes in last 5 seconds)
        now = time.time()
        recent = [s for t, s in self.recent_sizes[path] if now - t < 5]

        if len(recent) > 1 and len(set(recent)) > 1:
            return 'growing'

        return 'complete'


class JsonOutputWriter:
    """Handles atomic JSON output writes."""

    def __init__(self, output_file: str):
        self.output_file = output_file
        self.logger = logging.getLogger(__name__)

    def write_output(self, data: Dict):
        """Write JSON output atomically."""
        try:
            temp_file = f"{self.output_file}.tmp"

            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)

            os.replace(temp_file, self.output_file)
            self.logger.debug(f"Output written to {self.output_file}")
        except Exception as e:
            self.logger.error(f"Failed to write output: {e}")


class DirectoryScanner:
    """Scans FTP directories with three-tier polling strategy."""

    def __init__(self, ftp_manager: FtpConnectionManager, config: Dict,
                 file_classifier: FileClassifier, change_detector: ChangeDetector):
        self.ftp_manager = ftp_manager
        self.config = config
        self.file_classifier = file_classifier
        self.change_detector = change_detector
        self.logger = logging.getLogger(__name__)

        # Caching
        self.date_dirs_cache = []
        self.date_dirs_cache_time = 0
        self.recording_dirs_cache = {}  # date_dir -> (recordings, timestamp)
        self.active_captures = set()  # Set of capture_ids being actively monitored

        # Monitoring mode: 'initial_scan' or 'continuous'
        self.monitoring_mode = 'initial_scan'
        self.initial_scan_complete = False

    def should_refresh_date_cache(self) -> bool:
        """Check if date directory cache should be refreshed."""
        ttl = self.config['monitoring']['date_dir_cache_ttl']
        return (time.time() - self.date_dirs_cache_time) > ttl

    def should_refresh_recording_cache(self, date_dir: str) -> bool:
        """Check if recording directory cache should be refreshed."""
        if date_dir not in self.recording_dirs_cache:
            return True

        _, cached_time = self.recording_dirs_cache[date_dir]
        ttl = self.config['monitoring']['recording_dir_cache_ttl']
        return (time.time() - cached_time) > ttl

    def is_recent_date(self, date_dir: str) -> bool:
        """Check if date directory is recent enough to monitor."""
        try:
            date_obj = datetime.strptime(date_dir, '%Y-%m-%d')

            # Initial scan mode: accept all dates
            if self.monitoring_mode == 'initial_scan':
                return True

            # Continuous mode: only today
            today = datetime.now().date()
            return date_obj.date() == today
        except:
            return False

    def get_date_directories(self) -> List[str]:
        """Get list of date directories (Tier 1 - cached)."""
        if not self.should_refresh_date_cache():
            return self.date_dirs_cache

        try:
            base_path = self.config['ftp']['base_path']

            def list_dirs():
                self.ftp_manager.ftp.cwd(base_path)
                items = self.ftp_manager.ftp.nlst()
                dirs = []
                for item in items:
                    try:
                        self.ftp_manager.ftp.cwd(item)
                        dirs.append(item)
                        self.ftp_manager.ftp.cwd('..')
                    except:
                        pass
                return dirs

            dirs = self.ftp_manager.execute_with_retry(list_dirs)
            # Filter to only recent dates and valid date formats
            recent_dirs = [d for d in dirs if self.is_recent_date(d)]

            # Sort with today's date first, then reverse chronological
            today_str = datetime.now().strftime('%Y-%m-%d')

            # Separate today from other dates
            today_list = [d for d in recent_dirs if d == today_str]
            other_dates = [d for d in recent_dirs if d != today_str]

            # Sort other dates in reverse chronological order
            other_dates.sort(reverse=True)

            # Combine: today first, then others
            self.date_dirs_cache = today_list + other_dates
            self.date_dirs_cache_time = time.time()

            mode_info = "all dates" if self.monitoring_mode == 'initial_scan' else "today only"
            self.logger.debug(f"Found {len(self.date_dirs_cache)} date directories ({mode_info})")
            return self.date_dirs_cache
        except Exception as e:
            self.logger.error(f"Failed to get date directories: {e}")
            return self.date_dirs_cache  # Return cached version on error

    def get_recording_directories(self, date_dir: str) -> List[str]:
        """Get list of recording directories for a date (Tier 2 - cached)."""
        if not self.should_refresh_recording_cache(date_dir):
            recordings, _ = self.recording_dirs_cache[date_dir]
            return recordings

        try:
            base_path = self.config['ftp']['base_path']
            date_path = f"{base_path}/{date_dir}"

            def list_dirs():
                self.ftp_manager.ftp.cwd(date_path)
                items = self.ftp_manager.ftp.nlst()
                dirs = []
                for item in items:
                    try:
                        self.ftp_manager.ftp.cwd(item)
                        dirs.append(item)
                        self.ftp_manager.ftp.cwd('..')
                    except:
                        pass
                return dirs

            dirs = self.ftp_manager.execute_with_retry(list_dirs)
            self.recording_dirs_cache[date_dir] = (dirs, time.time())
            self.logger.debug(f"Found {len(dirs)} recording directories in {date_dir}")
            return dirs
        except Exception as e:
            self.logger.error(f"Failed to get recording directories for {date_dir}: {e}")
            # Return cached version if available
            if date_dir in self.recording_dirs_cache:
                recordings, _ = self.recording_dirs_cache[date_dir]
                return recordings
            return []

    def scan_recording_files(self, date_dir: str, recording_dir: str) -> List[Dict]:
        """Scan files in a recording directory (Tier 3 - always fresh)."""
        files = []
        base_path = self.config['ftp']['base_path']
        recording_path = f"{base_path}/{date_dir}/{recording_dir}"

        try:
            # Scan root level files
            root_files = self._scan_directory_files(recording_path, date_dir, recording_dir, '')
            files.extend(root_files)

            # Get all subdirectories in the recording
            def get_subdirs():
                self.ftp_manager.ftp.cwd(recording_path)
                items = self.ftp_manager.ftp.nlst()
                subdirs = []
                for item in items:
                    try:
                        # Try to change into it - if successful, it's a directory
                        self.ftp_manager.ftp.cwd(item)
                        subdirs.append(item)
                        self.ftp_manager.ftp.cwd('..')
                    except:
                        pass  # Not a directory
                return subdirs

            subdirs = self.ftp_manager.execute_with_retry(get_subdirs)

            # Scan all subdirectories (unreal, obs, shogun_live, etc.)
            for subdir in subdirs:
                try:
                    subdir_path = f"{recording_path}/{subdir}"
                    subdir_files = self._scan_directory_files(subdir_path, date_dir, recording_dir, subdir)
                    files.extend(subdir_files)

                    # Scan nested subdirectories (e.g., unreal/CC, unreal/Vicon)
                    def get_nested_subdirs(parent_path=subdir_path):
                        self.ftp_manager.ftp.cwd(parent_path)
                        items = self.ftp_manager.ftp.nlst()
                        nested = []
                        for item in items:
                            try:
                                self.ftp_manager.ftp.cwd(item)
                                nested.append(item)
                                self.ftp_manager.ftp.cwd('..')
                            except:
                                pass
                        return nested

                    nested_subdirs = self.ftp_manager.execute_with_retry(get_nested_subdirs)
                    for nested in nested_subdirs:
                        try:
                            nested_path = f"{subdir_path}/{nested}"
                            composite_subdir = f"{subdir}/{nested}"
                            nested_files = self._scan_directory_files(nested_path, date_dir, recording_dir, composite_subdir)
                            files.extend(nested_files)
                        except Exception as e:
                            self.logger.debug(f"Could not scan nested subdirectory {subdir}/{nested}: {e}")

                except Exception as e:
                    self.logger.debug(f"Could not scan subdirectory {subdir}: {e}")

        except error_perm as e:
            # Permission/access errors - just skip this recording
            error_msg = str(e)
            if error_msg.startswith('550') or error_msg.startswith('553'):
                self.logger.debug(f"Skipping inaccessible recording {date_dir}/{recording_dir}: {e}")
            else:
                self.logger.error(f"Permission error scanning {date_dir}/{recording_dir}: {e}")
        except Exception as e:
            self.logger.error(f"Failed to scan recording {date_dir}/{recording_dir}: {e}")

        return files

    def _scan_directory_files(self, ftp_path: str, date_dir: str,
                              recording_dir: str, subdir: str) -> List[Dict]:
        """Scan files in a specific directory."""
        files = []

        def scan():
            self.ftp_manager.ftp.cwd(ftp_path)
            items = self.ftp_manager.ftp.nlst()

            for item in items:
                try:
                    # Try to get file size (if it's a file, not directory)
                    size = self.ftp_manager.ftp.size(item)
                    if size is not None:
                        # Construct full path
                        if subdir:
                            # Normalize subdir separators for Windows path (unreal/CC -> unreal\\CC)
                            win_subdir = subdir.replace('/', '\\')
                            full_path = f"E:\\Recordings\\{date_dir}\\{recording_dir}\\{win_subdir}\\{item}"
                            ftp_full_path = f"{ftp_path}/{item}"
                        else:
                            full_path = f"E:\\Recordings\\{date_dir}\\{recording_dir}\\{item}"
                            ftp_full_path = f"{ftp_path}/{item}"

                        # Special handling: CSV files in unreal/ are LiveLink data
                        actual_subdir = subdir
                        if (subdir == 'unreal' or subdir.startswith('unreal/')) and item.lower().endswith('.csv'):
                            actual_subdir = 'livelink'

                        files.append({
                            'filename': item,
                            'path': full_path,
                            'ftp_path': ftp_full_path,
                            'size': size,
                            'date_dir': date_dir,
                            'recording_dir': recording_dir,
                            'subdir': actual_subdir
                        })
                except:
                    pass  # Skip directories or inaccessible files

            return files

        return self.ftp_manager.execute_with_retry(scan)

    def switch_to_continuous_mode(self):
        """Switch from initial scan to continuous monitoring (today only)."""
        if self.monitoring_mode == 'initial_scan':
            self.monitoring_mode = 'continuous'
            self.initial_scan_complete = True
            self.date_dirs_cache = []  # Clear cache to force refresh with new mode
            self.date_dirs_cache_time = 0
            self.logger.info("Switched to continuous monitoring mode (today's files only)")

    def scan_all(self) -> List[Dict]:
        """Perform full scan and return all changes."""
        changes = []

        date_dirs = self.get_date_directories()

        for date_dir in date_dirs:
            recording_dirs = self.get_recording_directories(date_dir)

            for recording_dir in recording_dirs:
                capture_id = f"{date_dir}/{recording_dir}"
                files = self.scan_recording_files(date_dir, recording_dir)

                for file_info in files:
                    # Classify file
                    file_type = self.file_classifier.classify(
                        file_info['path'],
                        file_info['filename']
                    )

                    # Detect changes
                    change_type = self.change_detector.detect_change(
                        file_info['path'],
                        file_info['size']
                    )

                    file_status = self.change_detector.get_file_status(
                        file_info['path'],
                        file_info['size']
                    )

                    changes.append({
                        'capture_id': capture_id,
                        'file_info': file_info,
                        'file_type': file_type,
                        'change_type': change_type,
                        'status': file_status
                    })

        return changes


class ViconFtpMonitor:
    """Main orchestrator for FTP monitoring."""

    def __init__(self, config_file: str):
        # Load configuration
        with open(config_file, 'r') as f:
            self.config = json.load(f)

        # Setup logging
        self._setup_logging()

        # Initialize components
        self.ftp_manager = FtpConnectionManager(self.config)
        self.state_manager = StateManager(self.config['output']['state_file'])
        self.file_classifier = FileClassifier(self.config)
        self.change_detector = ChangeDetector(self.state_manager)
        self.json_writer = JsonOutputWriter(self.config['output']['json_file'])
        self.scanner = DirectoryScanner(
            self.ftp_manager,
            self.config,
            self.file_classifier,
            self.change_detector
        )

        # Initialize database writer
        self.db_writer = None
        if DB_AVAILABLE:
            try:
                self.db_writer = DatabaseWriter()
            except Exception as e:
                self.logger = logging.getLogger(__name__)
                self.logger.warning(f"Database writer initialization failed: {e}")

        # Initialize client monitor
        self.client_monitor = None
        if self.config.get('client_monitor', {}).get('enabled', False):
            try:
                cm_config = self.config['client_monitor']
                self.client_monitor = ClientMonitor(
                    api_url=cm_config['api_url'],
                    client_id=cm_config['client_id'],
                    client_name=cm_config['client_name'],
                    description=cm_config.get('description', ''),
                    heartbeat_interval=cm_config.get('heartbeat_interval', 3600)
                )
                # Register on startup
                self.client_monitor.register({
                    "hostname": socket.gethostname(),
                    "python_version": sys.version.split()[0],
                    "ftp_host": self.config['ftp'].get('host_prefix', vicon_host.DEFAULT_PREFIX)
                })
            except Exception as e:
                self.logger = logging.getLogger(__name__)
                self.logger.warning(f"Client monitor initialization failed: {e}")

        self.monitoring_started = datetime.now().isoformat()
        self.logger = logging.getLogger(__name__)
        self.last_heartbeat_time = time.time()
        self.cycle_count = 0

    def _setup_logging(self):
        """Setup logging configuration."""
        log_file = self.config['output']['log_file']
        log_dir = os.path.dirname(log_file)

        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )

    def get_output_data(self) -> Dict:
        """Generate output data structure."""
        captures = {}

        # Group files by capture
        for file_path, file_info in self.state_manager.state['known_files'].items():
            # Parse path to extract capture_id
            # E:\Recordings\2026-01-14\M20251216_8568_260114_0\...
            try:
                parts = file_path.split('\\')
                if len(parts) >= 4:
                    date_dir = parts[2]
                    recording_dir = parts[3]
                    capture_id = f"{date_dir}/{recording_dir}"
                    filename = parts[-1]

                    if capture_id not in captures:
                        capture_data = self.state_manager.get_capture(capture_id)
                        if capture_data:
                            captures[capture_id] = capture_data
                        else:
                            captures[capture_id] = {
                                'date_dir': date_dir,
                                'recording_dir': recording_dir,
                                'first_seen': file_info['first_seen'],
                                'last_modified': file_info['last_modified'],
                                'file_count': 0,
                                'total_size_bytes': 0,
                                'files': {}
                            }
            except:
                continue

        # Add file details to captures
        for file_path, file_info in self.state_manager.state['known_files'].items():
            try:
                parts = file_path.split('\\')
                if len(parts) >= 4:
                    date_dir = parts[2]
                    recording_dir = parts[3]
                    capture_id = f"{date_dir}/{recording_dir}"
                    filename = parts[-1]

                    # Get subdirectory (use 'root' for files at recording root)
                    subdir = file_info.get('subdir', '')
                    subdir_key = subdir if subdir else 'root'

                    if capture_id in captures:
                        # Initialize subdirectory array if it doesn't exist
                        if subdir_key not in captures[capture_id]['files']:
                            captures[capture_id]['files'][subdir_key] = []

                        file_detail = {
                            'filename': filename,
                            'path': file_path,
                            'size_bytes': file_info['size'],
                            'first_seen': file_info['first_seen'],
                            'last_modified': file_info['last_modified'],
                            'status': file_info.get('status', 'complete')
                        }

                        captures[capture_id]['files'][subdir_key].append(file_detail)
                        captures[capture_id]['file_count'] += 1
                        captures[capture_id]['total_size_bytes'] += file_info['size']

                        # Update last_modified at capture level
                        if file_info['last_modified'] > captures[capture_id]['last_modified']:
                            captures[capture_id]['last_modified'] = file_info['last_modified']
            except:
                continue

        # Build output
        output = {
            'metadata': {
                'last_update': datetime.now().isoformat(),
                'ftp_host': self.config['ftp'].get('host_prefix', vicon_host.DEFAULT_PREFIX),
                'monitoring_started': self.monitoring_started,
                'total_captures': len(captures),
                'total_files': len(self.state_manager.state['known_files'])
            },
            'captures': captures
        }

        return output

    def process_changes(self, changes: List[Dict]):
        """Process detected changes and update state."""
        for change in changes:
            file_info = change['file_info']
            path = file_info['path']
            size = file_info['size']
            status = change['status']
            subdir = file_info.get('subdir', '')

            # Update file state
            self.state_manager.update_file_info(path, size, status, subdir)

            # Log new files
            if change['change_type'] == 'new':
                subdir_display = subdir if subdir else 'root'
                self.logger.info(f"New file in {subdir_display}: {file_info['filename']} ({size} bytes)")
            elif change['change_type'] == 'growing':
                self.logger.debug(f"Growing file: {file_info['filename']} ({size} bytes)")

    def run(self):
        """Main monitoring loop."""
        self.logger.info("Starting Vicon FTP Monitor...")

        # Initial connection
        if not self.ftp_manager.connect():
            self.logger.error("Failed to establish initial FTP connection")
            return

        # Perform initial full scan of all dates
        self.logger.info("Performing initial full scan of all dates...")
        try:
            changes = self.scanner.scan_all()
            self.process_changes(changes)

            output_data = self.get_output_data()
            self.json_writer.write_output(output_data)

            if self.db_writer:
                self.db_writer.sync_all_data(output_data)

            self.state_manager.save_state()

            self.logger.info(f"Initial scan complete: {output_data['metadata']['total_captures']} captures, {output_data['metadata']['total_files']} files")

            # Send initial heartbeat with stats
            if self.client_monitor:
                self.client_monitor.send_heartbeat_with_stats(
                    status="success",
                    message=f"Initial scan complete",
                    stats={
                        "total_captures": output_data['metadata']['total_captures'],
                        "total_files": output_data['metadata']['total_files'],
                        "scan_type": "initial"
                    }
                )
                self.last_heartbeat_time = time.time()
        except Exception as e:
            self.logger.error(f"Initial scan failed: {e}", exc_info=True)

            # Send error heartbeat
            if self.client_monitor:
                self.client_monitor.send_heartbeat_with_stats(
                    status="error",
                    message=f"Initial scan failed: {str(e)}",
                    stats={"error_type": type(e).__name__}
                )

        # Switch to continuous monitoring (today only)
        self.scanner.switch_to_continuous_mode()

        self.logger.info(f"Continuous monitoring of today's files every {self.config['monitoring']['poll_interval']}s")

        last_full_scan_time = time.time()
        full_scan_interval = self.config.get('monitoring', {}).get('full_rescan_interval', 604800)  # Default: 7 days

        try:
            while True:
                cycle_start = time.time()

                try:
                    # Periodic full rescan of all dates
                    if (time.time() - last_full_scan_time) >= full_scan_interval:
                        self.logger.info("Starting periodic full rescan of all dates...")
                        self.scanner.monitoring_mode = 'initial_scan'
                        self.scanner.date_dirs_cache = []
                        self.scanner.date_dirs_cache_time = 0
                        self.scanner.recording_dirs_cache = {}

                    # Scan for changes
                    changes = self.scanner.scan_all()

                    # Process changes
                    self.process_changes(changes)

                    # Write JSON output
                    output_data = self.get_output_data()
                    self.json_writer.write_output(output_data)

                    # Write to database
                    if self.db_writer:
                        self.db_writer.sync_all_data(output_data)

                    # Switch back to continuous mode after full rescan
                    if self.scanner.monitoring_mode == 'initial_scan':
                        self.logger.info(f"Full rescan complete: {output_data['metadata']['total_captures']} captures, {output_data['metadata']['total_files']} files")
                        self.scanner.switch_to_continuous_mode()
                        last_full_scan_time = time.time()

                    # Periodic state save
                    self.state_manager.save_state()

                    # Send periodic heartbeat if enough time has passed
                    self.cycle_count += 1
                    if self.client_monitor:
                        current_time = time.time()
                        heartbeat_interval = self.config.get('client_monitor', {}).get('heartbeat_interval', 90)

                        if (current_time - self.last_heartbeat_time) >= heartbeat_interval:
                            send_stats = self.config.get('client_monitor', {}).get('send_stats', True)

                            if send_stats:
                                self.client_monitor.send_heartbeat_with_stats(
                                    status="success",
                                    message=f"Monitoring active",
                                    stats={
                                        "total_captures": output_data['metadata']['total_captures'],
                                        "total_files": output_data['metadata']['total_files'],
                                        "cycle_count": self.cycle_count,
                                        "scan_type": "continuous"
                                    }
                                )
                            else:
                                self.client_monitor.send_heartbeat()

                            self.last_heartbeat_time = current_time

                except Exception as e:
                    self.logger.error(f"Error in monitoring cycle: {e}", exc_info=True)

                    # Send error heartbeat
                    if self.client_monitor:
                        self.client_monitor.send_heartbeat_with_stats(
                            status="error",
                            message=f"Monitoring cycle error: {str(e)}",
                            stats={
                                "error_type": type(e).__name__,
                                "cycle_count": self.cycle_count
                            }
                        )

                # Sleep to maintain interval
                elapsed = time.time() - cycle_start
                sleep_time = max(0, self.config['monitoring']['poll_interval'] - elapsed)

                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            self.logger.info("Shutting down gracefully...")

            # Send shutdown heartbeat
            if self.client_monitor:
                self.client_monitor.send_heartbeat_with_stats(
                    status="warning",
                    message="Monitor shutting down (manual stop)",
                    stats={"shutdown_reason": "KeyboardInterrupt"}
                )

            self.state_manager.save_state(force=True)
            self.ftp_manager.close()
            if self.db_writer:
                self.db_writer.close()
            self.logger.info("Monitor stopped")


def main():
    """Main entry point."""
    config_file = 'monitor_config.json'

    if '--help' in sys.argv or '-h' in sys.argv:
        print("Usage: python3 ftp_monitor.py [--config CONFIG_FILE]")
        print("")
        print("Options:")
        print("  --config FILE    Use alternate configuration file (default: monitor_config.json)")
        print("  --help, -h       Show this help message")
        sys.exit(0)

    if '--config' in sys.argv:
        idx = sys.argv.index('--config')
        if idx + 1 < len(sys.argv):
            config_file = sys.argv[idx + 1]

    if not os.path.exists(config_file):
        print(f"Error: Configuration file '{config_file}' not found")
        sys.exit(1)

    monitor = ViconFtpMonitor(config_file)
    monitor.run()


if __name__ == '__main__':
    main()
