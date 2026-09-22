#!/usr/bin/env python3
"""
GLB Matcher for Vicon FTP Monitor
Finds GLB files corresponding to FBX files and updates the database.
"""

import os
import sys
import json
import time
import logging
import socket
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    print("ERROR: pymysql not installed. Run: pip3 install pymysql")
    exit(1)

from db_config import get_db_config
from sc_paths import sc_path


# The heartbeat client. Prefer the installed package; fall back to the copy
# vendored beside this file, which is what a host that has never run
# client/install.sh from signlab_client_monitor_api will find. The two are
# byte-identical - see the header of python_client.py.
try:
    from signlab_client_monitor import ClientMonitor, setup_rotating_logger
except ImportError:
    from python_client import ClientMonitor, setup_rotating_logger


class GlbMatcher:
    """Matches FBX files with corresponding GLB files and updates the database."""

    def __init__(self, config: Dict):
        self.config = config
        self.db_config = get_db_config()
        self.connection = None
        self.logger = logging.getLogger(__name__)

        # GLB search path
        self.glb_base_path = config.get('glb_matcher', {}).get('glb_base_path', sc_path('media_fbx'))

        # Initialize client monitor
        self.client_monitor = None
        if config.get('glb_matcher', {}).get('client_monitor', {}).get('enabled', False):
            try:
                cm_config = config['glb_matcher']['client_monitor']
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
                    "glb_base_path": self.glb_base_path
                })
            except Exception as e:
                self.logger.warning(f"Client monitor initialization failed: {e}")

    def connect(self) -> bool:
        """Establish database connection."""
        try:
            self.connection = pymysql.connect(
                host=self.db_config['host'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                database=self.db_config['database'],
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False
            )
            self.logger.info("Database connected successfully")
            return True
        except Exception as e:
            self.logger.error(f"Database connection failed: {e}")
            return False

    def close(self):
        """Close database connection."""
        if self.connection:
            try:
                self.connection.close()
                self.logger.debug("Database connection closed")
            except:
                pass

    def ensure_connected(self) -> bool:
        """Ensure database connection is active, reconnecting if needed."""
        if self.connection is None:
            return self.connect()

        try:
            self.connection.ping(reconnect=True)
            return True
        except Exception:
            self.logger.warning("Database connection lost, reconnecting...")
            self.connection = None
            return self.connect()

    def get_fbx_files_without_glb(self, limit: int = None) -> List[Dict]:
        """Get all FBX files from database (optionally limited)."""
        if not self.ensure_connected():
            return []

        try:
            with self.connection.cursor() as cursor:
                if limit is not None:
                    # Ensure limit is an integer for safe string formatting
                    limit = int(limit)
                    sql = f"""
                        SELECT id, capture_id, file_path, filename, subdirectory, glb_path
                        FROM vicon_files
                        WHERE filename LIKE '%.fbx'
                        ORDER BY last_modified DESC
                        LIMIT {limit}
                    """
                else:
                    sql = """
                        SELECT id, capture_id, file_path, filename, subdirectory, glb_path
                        FROM vicon_files
                        WHERE filename LIKE '%.fbx'
                        ORDER BY last_modified DESC
                    """
                cursor.execute(sql)
                results = cursor.fetchall()
                return results
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
            self.logger.error(f"Database connection lost during query: {e}")
            self.connection = None
            return []
        except Exception as e:
            self.logger.error(f"Failed to query FBX files: {e}", exc_info=True)
            return []

    def find_glb_file(self, fbx_filename: str, subdirectory: str = '') -> Optional[str]:
        """
        Find corresponding GLB file for an FBX file.

        Args:
            fbx_filename: Name of the FBX file (e.g., "example.fbx")
            subdirectory: Subdirectory from DB (e.g., 'unreal', 'unreal/CC', '')

        Returns:
            Full path to GLB file if found, None otherwise
        """
        # Replace .fbx with .glb
        glb_filename = fbx_filename.rsplit('.', 1)[0] + '.glb'

        # If subdirectory contains a nested path (e.g., unreal/CC), check the leaf subdir first
        if subdirectory and '/' in subdirectory:
            leaf_dir = subdirectory.split('/')[-1]  # 'CC' from 'unreal/CC'
            glb_path = os.path.join(self.glb_base_path, leaf_dir, glb_filename)
            if os.path.exists(glb_path):
                self.logger.debug(f"Found GLB file in subdirectory: {glb_path}")
                return glb_path

        # Check flat base path (backward compat)
        glb_path = os.path.join(self.glb_base_path, glb_filename)
        if os.path.exists(glb_path):
            self.logger.debug(f"Found GLB file: {glb_path}")
            return glb_path

        # Scan all immediate subdirectories as last resort
        try:
            for entry in os.scandir(self.glb_base_path):
                if entry.is_dir():
                    glb_path = os.path.join(entry.path, glb_filename)
                    if os.path.exists(glb_path):
                        self.logger.debug(f"Found GLB file in {entry.name}/: {glb_path}")
                        return glb_path
        except OSError:
            pass

        return None

    def update_glb_path(self, file_id: int, glb_path: str) -> bool:
        """Update the glb_path for a file in the database."""
        if not self.ensure_connected():
            return False

        try:
            with self.connection.cursor() as cursor:
                sql = "UPDATE vicon_files SET glb_path = %s WHERE id = %s"
                cursor.execute(sql, (glb_path, file_id))

            self.connection.commit()
            self.logger.debug(f"Updated file {file_id} with glb_path: {glb_path}")
            return True
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
            self.logger.error(f"Database connection lost during update: {e}")
            self.connection = None
            return False
        except Exception as e:
            self.logger.error(f"Failed to update glb_path for file {file_id}: {e}")
            try:
                if self.connection:
                    self.connection.rollback()
            except:
                self.connection = None
            return False

    def process_batch(self, batch_size: int = None) -> Dict[str, int]:
        """
        Process FBX files and match them with GLB files.

        Args:
            batch_size: Number of files to process (None = all files)

        Returns:
            Statistics dictionary with counts
        """
        stats = {
            'processed': 0,
            'matched': 0,
            'updated': 0,
            'already_linked': 0,
            'not_found': 0,
            'errors': 0
        }

        fbx_files = self.get_fbx_files_without_glb(batch_size)
        self.logger.info(f"Processing {len(fbx_files)} FBX files")

        for fbx_file in fbx_files:
            stats['processed'] += 1

            try:
                filename = fbx_file['filename']
                current_glb_path = fbx_file.get('glb_path')

                # Check if file already has a glb_path and it still exists
                if current_glb_path and os.path.exists(current_glb_path):
                    stats['already_linked'] += 1
                    self.logger.debug(f"Already linked: {filename} -> {current_glb_path}")
                    continue

                # Try to find a GLB file
                glb_path = self.find_glb_file(filename, fbx_file.get('subdirectory', ''))

                if glb_path:
                    if self.update_glb_path(fbx_file['id'], glb_path):
                        if current_glb_path:
                            stats['updated'] += 1
                            self.logger.info(f"Updated: {filename} -> {glb_path}")
                        else:
                            stats['matched'] += 1
                            self.logger.info(f"Matched: {filename} -> {glb_path}")
                    else:
                        stats['errors'] += 1
                else:
                    stats['not_found'] += 1
                    self.logger.debug(f"No GLB found for: {filename}")

            except Exception as e:
                stats['errors'] += 1
                self.logger.error(f"Error processing {fbx_file.get('filename', 'unknown')}: {e}")

        return stats

    def run_continuous(self):
        """Run in continuous mode, checking periodically for all FBX files."""
        self.logger.info("Starting GLB Matcher in continuous mode...")

        interval = int(self.config.get('glb_matcher', {}).get('check_interval', 3600))  # Default: 1 hour
        # Process all files (batch_size = None)
        batch_size = self.config.get('glb_matcher', {}).get('batch_size')
        if batch_size is not None:
            batch_size = int(batch_size) if batch_size != 'all' else None
        else:
            batch_size = None  # Process all files

        if not self.connect():
            self.logger.error("Failed to establish database connection")
            if self.client_monitor:
                self.client_monitor.send_heartbeat_with_stats(
                    status="error",
                    message="Database connection failed",
                    stats={}
                )
            return

        # Send initial heartbeat
        if self.client_monitor:
            self.client_monitor.send_heartbeat_with_stats(
                status="success",
                message="GLB Matcher started",
                stats={"mode": "continuous", "batch_size": batch_size, "interval": interval}
            )

        last_heartbeat_time = time.time()
        heartbeat_interval = int(self.config.get('glb_matcher', {}).get('client_monitor', {}).get('heartbeat_interval', 300))

        try:
            while True:
                cycle_start = time.time()

                try:
                    # Fresh connection each cycle to avoid stale query results
                    self.close()
                    self.connect()

                    # Process batch
                    stats = self.process_batch(batch_size)

                    self.logger.info(
                        f"Batch complete: {stats['processed']} processed, "
                        f"{stats['matched']} newly matched, {stats['updated']} updated, "
                        f"{stats['already_linked']} already linked, {stats['not_found']} not found, "
                        f"{stats['errors']} errors"
                    )

                    # Send heartbeat if enough time has passed
                    current_time = time.time()
                    if self.client_monitor and (current_time - last_heartbeat_time) >= heartbeat_interval:
                        self.client_monitor.send_heartbeat_with_stats(
                            status="success",
                            message="GLB matching active",
                            stats=stats
                        )
                        last_heartbeat_time = current_time

                except Exception as e:
                    self.logger.error(f"Error in processing cycle: {e}", exc_info=True)

                    if self.client_monitor:
                        self.client_monitor.send_heartbeat_with_stats(
                            status="error",
                            message=f"Processing error: {str(e)}",
                            stats={"error_type": type(e).__name__}
                        )

                # Sleep until next check
                elapsed = time.time() - cycle_start
                sleep_time = max(0, interval - elapsed)

                if sleep_time > 0:
                    self.logger.debug(f"Sleeping for {sleep_time:.1f}s")
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            self.logger.info("Shutting down gracefully...")

            if self.client_monitor:
                self.client_monitor.send_heartbeat_with_stats(
                    status="warning",
                    message="GLB Matcher shutting down (manual stop)",
                    stats={"shutdown_reason": "KeyboardInterrupt"}
                )

            self.close()
            self.logger.info("GLB Matcher stopped")

    def run_once(self):
        """Run once and exit, processing all FBX files."""
        self.logger.info("Starting GLB Matcher in one-shot mode...")

        # Process all files (batch_size = None)
        batch_size = self.config.get('glb_matcher', {}).get('batch_size')
        if batch_size is not None:
            batch_size = int(batch_size) if batch_size != 'all' else None
        else:
            batch_size = None  # Process all files

        if not self.connect():
            self.logger.error("Failed to establish database connection")
            if self.client_monitor:
                self.client_monitor.send_heartbeat_with_stats(
                    status="error",
                    message="Database connection failed",
                    stats={}
                )
            return

        try:
            stats = self.process_batch(batch_size)

            self.logger.info(
                f"Processing complete: {stats['processed']} processed, "
                f"{stats['matched']} newly matched, {stats['updated']} updated, "
                f"{stats['already_linked']} already linked, {stats['not_found']} not found, "
                f"{stats['errors']} errors"
            )

            if self.client_monitor:
                self.client_monitor.send_heartbeat_with_stats(
                    status="success",
                    message="GLB matching completed",
                    stats=stats
                )

        except Exception as e:
            self.logger.error(f"Error during processing: {e}", exc_info=True)

            if self.client_monitor:
                self.client_monitor.send_heartbeat_with_stats(
                    status="error",
                    message=f"Processing error: {str(e)}",
                    stats={"error_type": type(e).__name__}
                )
        finally:
            self.close()


def main():
    """Main entry point."""
    # Parse arguments
    config_file = 'monitor_config.json'
    mode = 'once'  # Default mode

    if '--help' in sys.argv or '-h' in sys.argv:
        print("Usage: python3 glb_matcher.py [--config CONFIG_FILE] [--continuous]")
        print("")
        print("Options:")
        print("  --config FILE     Use alternate configuration file (default: monitor_config.json)")
        print("  --continuous      Run continuously, checking periodically (default: run once)")
        print("  --help, -h        Show this help message")
        sys.exit(0)

    if '--config' in sys.argv:
        idx = sys.argv.index('--config')
        if idx + 1 < len(sys.argv):
            config_file = sys.argv[idx + 1]

    if '--continuous' in sys.argv:
        mode = 'continuous'

    if not os.path.exists(config_file):
        print(f"Error: Configuration file '{config_file}' not found")
        sys.exit(1)

    # Load configuration
    with open(config_file, 'r') as f:
        config = json.load(f)

    # Setup logging
    setup_rotating_logger(
        config.get('glb_matcher', {}).get('log_file', 'logs/glb_matcher.log'),
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Create and run matcher
    matcher = GlbMatcher(config)

    if mode == 'continuous':
        matcher.run_continuous()
    else:
        matcher.run_once()


if __name__ == '__main__':
    main()
