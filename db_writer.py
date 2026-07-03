#!/usr/bin/env python3
"""
Database writer for Vicon FTP Monitor.
Handles all MySQL database operations.
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    print("ERROR: pymysql not installed. Run: pip3 install pymysql")
    exit(1)

from db_config import get_db_config


class DatabaseWriter:
    """Handles database operations for FTP monitor data."""

    def __init__(self):
        self.config = get_db_config()
        self.connection = None
        self.logger = logging.getLogger(__name__)
        self.enabled = True  # Can be disabled if connection fails

    def connect(self) -> bool:
        """Establish database connection."""
        try:
            self.connection = pymysql.connect(
                host=self.config['host'],
                user=self.config['user'],
                password=self.config['password'],
                database=self.config['database'],
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
        """Ensure database connection is active."""
        if not self.enabled:
            return False

        if self.connection is None:
            return self.connect()

        try:
            self.connection.ping(reconnect=True)
            return True
        except:
            return self.connect()

    def update_metadata(self, metadata: Dict):
        """Update monitor metadata in database."""
        if not self.ensure_connected():
            return

        try:
            with self.connection.cursor() as cursor:
                sql = """
                    INSERT INTO vicon_monitor_metadata
                    (id, ftp_host, monitoring_started, last_update, total_captures, total_files)
                    VALUES (1, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        last_update = VALUES(last_update),
                        total_captures = VALUES(total_captures),
                        total_files = VALUES(total_files)
                """
                cursor.execute(sql, (
                    metadata['ftp_host'],
                    metadata['monitoring_started'],
                    metadata['last_update'],
                    metadata['total_captures'],
                    metadata['total_files']
                ))

            self.connection.commit()
            self.logger.debug("Metadata updated in database")
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
            self.logger.error(f"Database connection lost during metadata update: {e}")
            self.connection = None
        except Exception as e:
            self.logger.error(f"Failed to update metadata: {e}")
            try:
                if self.connection:
                    self.connection.rollback()
            except:
                self.connection = None

    def upsert_capture(self, capture_id: str, capture_data: Dict):
        """Insert or update a capture record."""
        if not self.ensure_connected():
            return

        try:
            with self.connection.cursor() as cursor:
                sql = """
                    INSERT INTO vicon_captures
                    (capture_id, date_dir, recording_dir, first_seen, last_modified,
                     file_count, total_size_bytes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        last_modified = VALUES(last_modified),
                        file_count = VALUES(file_count),
                        total_size_bytes = VALUES(total_size_bytes)
                """
                cursor.execute(sql, (
                    capture_id,
                    capture_data['date_dir'],
                    capture_data['recording_dir'],
                    capture_data['first_seen'],
                    capture_data['last_modified'],
                    capture_data['file_count'],
                    capture_data['total_size_bytes']
                ))

            self.connection.commit()
            self.logger.debug(f"Capture {capture_id} updated in database")
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
            self.logger.error(f"Database connection lost during capture upsert: {e}")
            self.connection = None
        except Exception as e:
            self.logger.error(f"Failed to upsert capture {capture_id}: {e}")
            try:
                if self.connection:
                    self.connection.rollback()
            except:
                self.connection = None

    def upsert_file(self, capture_id: str, subdirectory: str, file_data: Dict):
        """Insert or update a file record."""
        if not self.ensure_connected():
            return

        try:
            with self.connection.cursor() as cursor:
                sql = """
                    INSERT INTO vicon_files
                    (capture_id, file_path, filename, subdirectory, size_bytes,
                     status, first_seen, last_modified)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        size_bytes = VALUES(size_bytes),
                        status = VALUES(status),
                        last_modified = VALUES(last_modified)
                """
                cursor.execute(sql, (
                    capture_id,
                    file_data['path'],
                    file_data['filename'],
                    subdirectory,
                    file_data['size_bytes'],
                    file_data['status'],
                    file_data['first_seen'],
                    file_data['last_modified']
                ))

            self.connection.commit()
            self.logger.debug(f"File {file_data['filename']} updated in database")
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
            self.logger.error(f"Database connection lost during file upsert: {e}")
            self.connection = None
        except Exception as e:
            self.logger.error(f"Failed to upsert file {file_data['filename']}: {e}")
            try:
                if self.connection:
                    self.connection.rollback()
            except:
                self.connection = None

    def sync_capture_with_files(self, capture_id: str, capture_data: Dict):
        """Sync a complete capture with all its files in a single transaction."""
        if not self.ensure_connected():
            return

        try:
            with self.connection.cursor() as cursor:
                # Upsert capture
                capture_sql = """
                    INSERT INTO vicon_captures
                    (capture_id, date_dir, recording_dir, first_seen, last_modified,
                     file_count, total_size_bytes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        last_modified = VALUES(last_modified),
                        file_count = VALUES(file_count),
                        total_size_bytes = VALUES(total_size_bytes)
                """
                cursor.execute(capture_sql, (
                    capture_id,
                    capture_data['date_dir'],
                    capture_data['recording_dir'],
                    capture_data['first_seen'],
                    capture_data['last_modified'],
                    capture_data['file_count'],
                    capture_data['total_size_bytes']
                ))

                # Upsert all files
                file_sql = """
                    INSERT INTO vicon_files
                    (capture_id, file_path, filename, subdirectory, size_bytes,
                     status, first_seen, last_modified)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        size_bytes = VALUES(size_bytes),
                        status = VALUES(status),
                        last_modified = VALUES(last_modified)
                """

                for subdirectory, files in capture_data['files'].items():
                    for file_data in files:
                        cursor.execute(file_sql, (
                            capture_id,
                            file_data['path'],
                            file_data['filename'],
                            subdirectory,
                            file_data['size_bytes'],
                            file_data['status'],
                            file_data['first_seen'],
                            file_data['last_modified']
                        ))

            self.connection.commit()
            self.logger.debug(f"Synced capture {capture_id} with {capture_data['file_count']} files")
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
            self.logger.error(f"Database connection lost during capture sync: {e}")
            self.connection = None
        except Exception as e:
            self.logger.error(f"Failed to sync capture {capture_id}: {e}")
            try:
                if self.connection:
                    self.connection.rollback()
            except:
                self.connection = None

    def sync_all_data(self, output_data: Dict):
        """Sync all data from monitor output to database.

        Uses separate INSERT and UPDATE queries to avoid burning auto_increment
        IDs on duplicate key conflicts. Existing files are updated in place;
        only genuinely new files consume a new ID.
        """
        if not self.ensure_connected():
            return

        try:
            with self.connection.cursor() as cursor:
                # Update metadata
                meta = output_data['metadata']
                meta_sql = """
                    INSERT INTO vicon_monitor_metadata
                    (id, ftp_host, monitoring_started, last_update, total_captures, total_files)
                    VALUES (1, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        last_update = VALUES(last_update),
                        total_captures = VALUES(total_captures),
                        total_files = VALUES(total_files)
                """
                cursor.execute(meta_sql, (
                    meta['ftp_host'],
                    meta['monitoring_started'],
                    meta['last_update'],
                    meta['total_captures'],
                    meta['total_files']
                ))

                # Load existing capture_ids and file_paths to avoid INSERT on duplicates
                cursor.execute("SELECT capture_id FROM vicon_captures")
                existing_captures = {row['capture_id'] for row in cursor.fetchall()}

                cursor.execute("SELECT file_path FROM vicon_files")
                existing_files = {row['file_path'] for row in cursor.fetchall()}

                # SQL for captures
                capture_insert_sql = """
                    INSERT INTO vicon_captures
                    (capture_id, date_dir, recording_dir, first_seen, last_modified,
                     file_count, total_size_bytes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                capture_update_sql = """
                    UPDATE vicon_captures
                    SET last_modified = %s, file_count = %s, total_size_bytes = %s
                    WHERE capture_id = %s
                """

                # SQL for files
                file_insert_sql = """
                    INSERT INTO vicon_files
                    (capture_id, file_path, filename, subdirectory, size_bytes,
                     status, first_seen, last_modified)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """
                file_update_sql = """
                    UPDATE vicon_files
                    SET size_bytes = %s, status = %s, last_modified = %s
                    WHERE file_path = %s
                """

                for capture_id, capture_data in output_data['captures'].items():
                    # Insert or update capture
                    if capture_id in existing_captures:
                        cursor.execute(capture_update_sql, (
                            capture_data['last_modified'],
                            capture_data['file_count'],
                            capture_data['total_size_bytes'],
                            capture_id
                        ))
                    else:
                        cursor.execute(capture_insert_sql, (
                            capture_id,
                            capture_data['date_dir'],
                            capture_data['recording_dir'],
                            capture_data['first_seen'],
                            capture_data['last_modified'],
                            capture_data['file_count'],
                            capture_data['total_size_bytes']
                        ))
                        existing_captures.add(capture_id)

                    # Insert or update files
                    for subdirectory, files in capture_data['files'].items():
                        for file_data in files:
                            file_path = file_data['path']
                            if file_path in existing_files:
                                cursor.execute(file_update_sql, (
                                    file_data['size_bytes'],
                                    file_data['status'],
                                    file_data['last_modified'],
                                    file_path
                                ))
                            else:
                                cursor.execute(file_insert_sql, (
                                    capture_id,
                                    file_path,
                                    file_data['filename'],
                                    subdirectory,
                                    file_data['size_bytes'],
                                    file_data['status'],
                                    file_data['first_seen'],
                                    file_data['last_modified']
                                ))
                                existing_files.add(file_path)

            self.connection.commit()
            self.logger.info(f"Synced {meta['total_captures']} captures with {meta['total_files']} files to database")
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
            self.logger.error(f"Database connection lost during data sync: {e}")
            self.connection = None
        except Exception as e:
            self.logger.error(f"Failed to sync data to database: {e}")
            try:
                if self.connection:
                    self.connection.rollback()
            except:
                self.connection = None


if __name__ == '__main__':
    # Test database connection
    logging.basicConfig(level=logging.INFO)
    db = DatabaseWriter()
    if db.connect():
        print("✓ Database connection successful")
        db.close()
    else:
        print("✗ Database connection failed")
