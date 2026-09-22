#!/usr/bin/env python3
"""
Re-sync FBX files from Vicon FTP server based on post_processed directory.
For each .fbx in post_processed, finds and downloads the matching file from FTP.
Always overwrites existing files in the target directory.

Uses a local cache of FTP files for fast lookups.
Run with --refresh-cache to rebuild the cache.
"""

import os
import sys
import json
from ftplib import FTP

import vicon_host
from vicon_credentials import get_vicon_password
from sc_paths import sc_path

# Configuration
FTP_USER = "vicon"
# The password is not stored here; get_vicon_password() reads it at call time.
FTP_BASE_PATH = "/e/Recordings"
POST_PROCESSED_PATH = sc_path("media_fbx", "post_processed")
TARGET_PATH = sc_path("media_fbx")
CACHE_FILE = "/home/gomer/viconSync/ftp_cache.json"


def vicon_ftp_host():
    """The Vicon PC's current tailnet address. Raises ViconOffline if it is
    not reachable."""
    host, _ = vicon_host.resolve_vicon_host(probe_port=21)
    return host


def connect_ftp():
    """Establish FTP connection and return the FTP object."""
    host = vicon_ftp_host()
    print(f"Connecting to {host}...")
    ftp = FTP(host)
    ftp.login(FTP_USER, get_vicon_password())
    print("Connected successfully")
    return ftp


def list_directories(ftp, path):
    """List directories in the given path."""
    dirs = []
    try:
        ftp.cwd(path)
        items = ftp.nlst()
        for item in items:
            try:
                ftp.cwd(item)
                dirs.append(item)
                ftp.cwd("..")
            except:
                pass  # Not a directory
    except:
        pass
    return dirs


def build_ftp_cache(ftp):
    """Build a cache of all FBX files on the FTP server."""
    print(f"\nBuilding FTP file cache...")
    cache = {}  # filename -> remote_path

    ftp.cwd(FTP_BASE_PATH)
    date_dirs = list_directories(ftp, FTP_BASE_PATH)
    print(f"Found {len(date_dirs)} date directories")

    for i, date_dir in enumerate(sorted(date_dirs), 1):
        print(f"  Scanning [{i}/{len(date_dirs)}] {date_dir}...")
        date_path = f"{FTP_BASE_PATH}/{date_dir}"
        sub_dirs = list_directories(ftp, date_path)

        for sub_dir in sub_dirs:
            unreal_path = f"{date_path}/{sub_dir}/unreal"
            try:
                ftp.cwd(unreal_path)
                items = ftp.nlst()
                for item in items:
                    if item.lower().endswith('.fbx'):
                        cache[item] = unreal_path
            except:
                pass  # unreal directory doesn't exist

    print(f"Cached {len(cache)} FBX files")
    return cache


def save_cache(cache):
    """Save cache to JSON file."""
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)
    print(f"Cache saved to {CACHE_FILE}")


def load_cache():
    """Load cache from JSON file."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)
        print(f"Loaded cache with {len(cache)} files")
        return cache
    return None


def get_post_processed_files():
    """Get list of .fbx filenames from post_processed directory."""
    fbx_files = []
    for filename in os.listdir(POST_PROCESSED_PATH):
        if filename.lower().endswith('.fbx') and not filename.startswith('.'):
            fbx_files.append(filename)
    return sorted(fbx_files)


def download_file(ftp, remote_path, filename):
    """Download a file from FTP to target directory (overwrites existing)."""
    local_file = os.path.join(TARGET_PATH, filename)
    try:
        ftp.cwd(remote_path)
        with open(local_file, 'wb') as f:
            ftp.retrbinary(f'RETR {filename}', f.write)
        return True
    except Exception as e:
        print(f"  Error downloading {filename}: {e}")
        if os.path.exists(local_file):
            os.remove(local_file)
        return False


def main():
    """Main resync function."""
    refresh_cache = '--refresh-cache' in sys.argv or '--refresh' in sys.argv

    # Load or build cache
    cache = None
    if not refresh_cache:
        cache = load_cache()

    ftp = connect_ftp()

    try:
        if cache is None or refresh_cache:
            cache = build_ftp_cache(ftp)
            save_cache(cache)

        # Get files to sync
        print(f"\nScanning {POST_PROCESSED_PATH}...")
        files_to_sync = get_post_processed_files()
        print(f"Found {len(files_to_sync)} FBX files in post_processed\n")

        if not files_to_sync:
            print("No files to sync.")
            return

        downloaded = 0
        not_found = 0
        errors = 0

        for i, filename in enumerate(files_to_sync, 1):
            remote_path = cache.get(filename)

            if remote_path:
                print(f"[{i}/{len(files_to_sync)}] {filename} -> downloading...")
                if download_file(ftp, remote_path, filename):
                    downloaded += 1
                else:
                    errors += 1
            else:
                print(f"[{i}/{len(files_to_sync)}] {filename} -> NOT IN CACHE")
                not_found += 1

        print(f"\n--- Summary ---")
        print(f"Downloaded: {downloaded}")
        print(f"Not found:  {not_found}")
        print(f"Errors:     {errors}")

    finally:
        ftp.quit()


if __name__ == "__main__":
    if '--help' in sys.argv or '-h' in sys.argv:
        print("Usage: python3 resync_fbx.py [--refresh-cache]")
        print("")
        print("Options:")
        print("  --refresh-cache  Rebuild the FTP file cache before syncing")
        print("")
        print("First run will automatically build the cache.")
        sys.exit(0)
    main()
