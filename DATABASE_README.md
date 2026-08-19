# Vicon FTP Monitor - Database Integration

## Overview
The FTP monitor now writes data directly to MySQL database in real-time while also maintaining the JSON output file.

## Database Schema

### Tables Created

1. **vicon_captures** - Recording session metadata
   - capture_id, date_dir, recording_dir
   - first_seen, last_modified
   - file_count, total_size_bytes

2. **vicon_files** - Individual file information
   - capture_id (foreign key)
   - file_path, filename, subdirectory
   - size_bytes, status (growing/complete)
   - first_seen, last_modified

3. **vicon_monitor_metadata** - Monitor status (single row)
   - ftp_host, monitoring_started, last_update
   - total_captures, total_files

## Setup Instructions

### Option 1: Automatic Setup (Recommended)

```bash
cd /home/gomer/viconSync
./setup_database.sh
```

This will:
1. Install pymysql (if not already installed)
2. Test database configuration
3. Create database tables
4. Verify database connection

### Option 2: Manual Setup

1. **Install pymysql:**
   ```bash
   pip3 install pymysql
   ```

2. **Test configuration:**
   ```bash
   python3 db_config.py
   ```

3. **Create tables:**
   ```bash
   mysql -u user -p admin_gebarenoverleg < create_tables.sql
   ```
   Password: $DB_PASSWORD

4. **Test connection:**
   ```bash
   python3 db_writer.py
   ```

## Running the Monitor

The monitor automatically writes to both JSON and database:

```bash
cd /home/gomer/viconSync
python3 ftp_monitor.py
```

### Monitoring Behavior

**On Startup:**
1. Performs a **full scan of ALL dates** in the FTP server
2. Saves all historical captures and files to database
3. You'll see: `INFO - Initial scan complete: XXX captures, YYYY files`

**After Startup:**
1. Switches to **continuous monitoring of TODAY ONLY**
2. Monitors only today's date directory (2026-01-21)
3. Updates every 1.5 seconds for real-time file tracking
4. You'll see: `INFO - Switched to continuous monitoring mode (today's files only)`

**Example startup logs:**
```
INFO - Starting Vicon FTP Monitor...
INFO - Database connected successfully
INFO - FTP connected successfully
INFO - Performing initial full scan of all dates...
INFO - Initial scan complete: 257 captures, 2904 files
INFO - Switched to continuous monitoring mode (today's files only)
INFO - Continuous monitoring of today's files every 1.5s
INFO - New file in livelink: M20260121_device.csv (325883 bytes)
```

### File Categorization

Files are automatically categorized by their subdirectory:
- **unreal/** - FBX/GLB files → categorized as "unreal"
- **unreal/** - CSV files → categorized as **"livelink"** (special handling)
- **obs/** - All files → categorized as "obs"
- **shogun_live/** - All files → categorized as "shogun_live"
- **shogun_post/** - All files → categorized as "shogun_post"
- **root** - Files at recording root → categorized as "root"

## Useful SQL Queries

### Get all files for a specific recording:
```sql
SELECT f.*, c.date_dir, c.recording_dir
FROM vicon_files f
JOIN vicon_captures c ON f.capture_id = c.capture_id
WHERE c.recording_dir = 'M20260115_0568_260121_1'
ORDER BY f.subdirectory, f.filename;
```

### Get today's captures:
```sql
SELECT * FROM vicon_captures
WHERE date_dir = DATE_FORMAT(NOW(), '%Y-%m-%d')
ORDER BY last_modified DESC;
```

### Get all files in a specific subdirectory (e.g., obs):
```sql
SELECT * FROM vicon_files
WHERE subdirectory = 'obs'
ORDER BY last_modified DESC
LIMIT 100;
```

### Get growing files (still being written):
```sql
SELECT f.*, c.date_dir, c.recording_dir
FROM vicon_files f
JOIN vicon_captures c ON f.capture_id = c.capture_id
WHERE f.status = 'growing'
ORDER BY f.last_modified DESC;
```

### Get capture statistics by date:
```sql
SELECT
    date_dir,
    COUNT(*) as total_captures,
    SUM(file_count) as total_files,
    ROUND(SUM(total_size_bytes) / 1024 / 1024 / 1024, 2) as total_size_gb
FROM vicon_captures
GROUP BY date_dir
ORDER BY date_dir DESC;
```

### Get all files for a capture grouped by subdirectory:
```sql
SELECT
    subdirectory,
    COUNT(*) as file_count,
    ROUND(SUM(size_bytes) / 1024 / 1024, 2) as size_mb
FROM vicon_files
WHERE capture_id = '2026-01-21/M20260115_0568_260121_1'
GROUP BY subdirectory;
```

### Get most recent files across all captures:
```sql
SELECT
    f.filename,
    f.subdirectory,
    c.recording_dir,
    c.date_dir,
    f.size_bytes,
    f.last_modified,
    f.status
FROM vicon_files f
JOIN vicon_captures c ON f.capture_id = c.capture_id
ORDER BY f.last_modified DESC
LIMIT 50;
```

### Get captures with incomplete files:
```sql
SELECT DISTINCT c.*
FROM vicon_captures c
JOIN vicon_files f ON f.capture_id = c.capture_id
WHERE f.status = 'growing';
```

## File Organization

```
/home/gomer/viconSync/
├── ftp_monitor.py           # Main monitor (now writes to DB)
├── db_config.py             # Database configuration parser
├── db_writer.py             # Database operations
├── create_tables.sql        # SQL schema
├── setup_database.sh        # Automated setup script
├── monitor_config.json      # FTP monitor config
├── monitor_output.json      # JSON output (still generated)
├── monitor_state.json       # State persistence
└── logs/
    └── ftp_monitor.log      # Monitor logs
```

## Database Credentials

Database configuration is read from: `/web/mysql_config.php`

- Host: localhost
- User: user
- Password: $DB_PASSWORD
- Database: admin_gebarenoverleg

## How It Works

1. **Real-time Sync**: Every 1.5 seconds (configurable), the monitor:
   - Scans FTP server for changes
   - Updates in-memory state
   - Writes to JSON file
   - **Writes to database** (automatic)

2. **Transaction Safety**: Database writes use transactions to ensure data consistency

3. **Graceful Degradation**: If database connection fails:
   - Monitor continues running
   - JSON output still works
   - Warning logged, but no crash

4. **Auto-reconnect**: Database connection reconnects automatically if lost

## Troubleshooting

### Database connection fails
```bash
# Test configuration
python3 db_config.py

# Test database connection
python3 db_writer.py
```

### Tables don't exist
```bash
# Re-run table creation
mysql -u user -p$DB_PASSWORD admin_gebarenoverleg < create_tables.sql
```

### pymysql not installed
```bash
pip3 install pymysql
```

### Monitor runs without database
- Check logs for "Database writer initialization failed"
- Install pymysql: `pip3 install pymysql`
- Verify /web/mysql_config.php exists
- Check MySQL service is running: `systemctl status mysql`

## Performance

- Database writes are asynchronous within each monitoring cycle
- Minimal impact on monitoring performance (~10-20ms per cycle)
- Uses INSERT ... ON DUPLICATE KEY UPDATE for efficient upserts
- Indexes on key fields for fast queries

## Next Steps

After setup is complete, you can:
1. Start the monitor: `python3 ftp_monitor.py`
2. Query the database using the SQL examples above
3. Build web interfaces or APIs using the database
4. Create reports and analytics from the structured data
