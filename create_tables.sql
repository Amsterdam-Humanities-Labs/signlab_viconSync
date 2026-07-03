-- Vicon FTP Monitor Database Schema
-- Database: admin_gebarenoverleg

-- Table 1: Capture sessions (recording directories)
CREATE TABLE IF NOT EXISTS vicon_captures (
    id INT AUTO_INCREMENT PRIMARY KEY,
    capture_id VARCHAR(255) UNIQUE NOT NULL COMMENT 'e.g., 2026-01-21/M20260115_0568_260121_1',
    date_dir VARCHAR(50) NOT NULL COMMENT 'e.g., 2026-01-21',
    recording_dir VARCHAR(255) NOT NULL COMMENT 'e.g., M20260115_0568_260121_1',
    first_seen DATETIME NOT NULL,
    last_modified DATETIME NOT NULL,
    file_count INT DEFAULT 0,
    total_size_bytes BIGINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_date_dir (date_dir),
    INDEX idx_recording_dir (recording_dir),
    INDEX idx_last_modified (last_modified)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 2: Individual files
CREATE TABLE IF NOT EXISTS vicon_files (
    id INT AUTO_INCREMENT PRIMARY KEY,
    capture_id VARCHAR(255) NOT NULL COMMENT 'Foreign key to vicon_captures.capture_id',
    file_path VARCHAR(1024) NOT NULL COMMENT 'Full Windows path',
    filename VARCHAR(512) NOT NULL,
    subdirectory VARCHAR(100) NOT NULL COMMENT 'unreal, obs, shogun_live, shogun_post, root, etc.',
    size_bytes BIGINT NOT NULL,
    status ENUM('growing', 'complete') DEFAULT 'complete',
    first_seen DATETIME NOT NULL,
    last_modified DATETIME NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY idx_file_path (file_path(255)),
    FOREIGN KEY (capture_id) REFERENCES vicon_captures(capture_id) ON DELETE CASCADE,
    INDEX idx_capture_id (capture_id),
    INDEX idx_subdirectory (subdirectory),
    INDEX idx_filename (filename(191)),
    INDEX idx_status (status),
    INDEX idx_last_modified (last_modified)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 3: Monitor metadata (single row table)
CREATE TABLE IF NOT EXISTS vicon_monitor_metadata (
    id INT PRIMARY KEY DEFAULT 1,
    ftp_host VARCHAR(255),
    monitoring_started DATETIME,
    last_update DATETIME,
    total_captures INT DEFAULT 0,
    total_files INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Insert initial metadata row
INSERT IGNORE INTO vicon_monitor_metadata (id, ftp_host) VALUES (1, '100.83.229.92');
