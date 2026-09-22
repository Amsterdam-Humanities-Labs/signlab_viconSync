# Vicon FTP Monitor - Web Dashboard

## Overview
Real-time web dashboard to visualize Vicon recording captures and files. Built with PHP backend API and vanilla JavaScript frontend.

## Features

- 📊 **Live Statistics** - Total captures, files, storage usage
- 📁 **Today's Captures** - Real-time view of current recordings
- 🔍 **Search & Filter** - Find captures by date, recording name
- 📂 **File Browser** - Browse files by subdirectory (unreal, livelink, obs, shogun_live)
- 📈 **Charts** - Visual statistics by date and file type
- ⚡ **Auto-refresh** - Updates every 5 seconds

## File Structure

```
/web/vicon_dashboard/
├── index.html              # Main dashboard page
├── api/
│   ├── stats.php           # Get overall statistics
│   ├── captures.php        # Get captures list
│   ├── files.php           # Get files for a capture
│   └── search.php          # Search captures
├── js/
│   ├── dashboard.js        # Main dashboard logic
│   └── charts.js           # Chart rendering (Chart.js)
├── css/
│   └── dashboard.css       # Dashboard styles
└── README.md               # This file
```

## Setup Instructions

### 1. Create Dashboard Directory

```bash
sudo mkdir -p /web/vicon_dashboard/api /web/vicon_dashboard/js /web/vicon_dashboard/css
sudo chown -R www-data:www-data /web/vicon_dashboard
```

### 2. Create Database Connection Helper

Create `/web/vicon_dashboard/api/db.php`:

```php
<?php
// Database connection using existing MySQL config
require_once('/web/mysql_config.php');

function getDbConnection() {
    global $servername, $username, $password, $database;

    $conn = new mysqli($servername, $username, $password, $database);

    if ($conn->connect_error) {
        die(json_encode(['error' => 'Database connection failed']));
    }

    $conn->set_charset('utf8mb4');
    return $conn;
}

function jsonResponse($data) {
    header('Content-Type: application/json');
    echo json_encode($data);
    exit;
}
?>
```

### 3. Create API Endpoints

#### `/web/vicon_dashboard/api/stats.php`
Get overall statistics:

```php
<?php
require_once('db.php');

$conn = getDbConnection();

// Get metadata
$metadata = $conn->query("SELECT * FROM vicon_monitor_metadata WHERE id = 1")->fetch_assoc();

// Get today's stats
$today = date('Y-m-d');
$todayStats = $conn->query("
    SELECT
        COUNT(*) as today_captures,
        SUM(file_count) as today_files,
        SUM(total_size_bytes) as today_size
    FROM vicon_captures
    WHERE date_dir = '$today'
")->fetch_assoc();

// Get file type breakdown
$fileTypes = [];
$result = $conn->query("
    SELECT
        subdirectory,
        COUNT(*) as count,
        SUM(size_bytes) as total_size
    FROM vicon_files
    GROUP BY subdirectory
    ORDER BY count DESC
");
while ($row = $result->fetch_assoc()) {
    $fileTypes[] = $row;
}

// Get recent activity (last 10 files)
$recentFiles = [];
$result = $conn->query("
    SELECT
        f.filename,
        f.subdirectory,
        f.size_bytes,
        f.last_modified,
        c.recording_dir,
        c.date_dir
    FROM vicon_files f
    JOIN vicon_captures c ON f.capture_id = c.capture_id
    ORDER BY f.last_modified DESC
    LIMIT 10
");
while ($row = $result->fetch_assoc()) {
    $recentFiles[] = $row;
}

jsonResponse([
    'metadata' => $metadata,
    'today' => $todayStats,
    'file_types' => $fileTypes,
    'recent_files' => $recentFiles
]);
?>
```

#### `/web/vicon_dashboard/api/captures.php`
Get captures list with pagination:

```php
<?php
require_once('db.php');

$conn = getDbConnection();

// Get parameters
$date = isset($_GET['date']) ? $_GET['date'] : date('Y-m-d');
$limit = isset($_GET['limit']) ? intval($_GET['limit']) : 50;
$offset = isset($_GET['offset']) ? intval($_GET['offset']) : 0;

// Get captures for date
$stmt = $conn->prepare("
    SELECT
        capture_id,
        date_dir,
        recording_dir,
        first_seen,
        last_modified,
        file_count,
        total_size_bytes
    FROM vicon_captures
    WHERE date_dir = ?
    ORDER BY last_modified DESC
    LIMIT ? OFFSET ?
");
$stmt->bind_param('sii', $date, $limit, $offset);
$stmt->execute();
$result = $stmt->get_result();

$captures = [];
while ($row = $result->fetch_assoc()) {
    $captures[] = $row;
}

// Get total count
$stmt = $conn->prepare("SELECT COUNT(*) as total FROM vicon_captures WHERE date_dir = ?");
$stmt->bind_param('s', $date);
$stmt->execute();
$total = $stmt->get_result()->fetch_assoc()['total'];

jsonResponse([
    'captures' => $captures,
    'total' => $total,
    'limit' => $limit,
    'offset' => $offset,
    'date' => $date
]);
?>
```

#### `/web/vicon_dashboard/api/files.php`
Get files for a specific capture:

```php
<?php
require_once('db.php');

$conn = getDbConnection();

$captureId = isset($_GET['capture_id']) ? $_GET['capture_id'] : '';

if (empty($captureId)) {
    jsonResponse(['error' => 'capture_id required']);
}

// Get capture info
$stmt = $conn->prepare("SELECT * FROM vicon_captures WHERE capture_id = ?");
$stmt->bind_param('s', $captureId);
$stmt->execute();
$capture = $stmt->get_result()->fetch_assoc();

if (!$capture) {
    jsonResponse(['error' => 'Capture not found']);
}

// Get files grouped by subdirectory
$stmt = $conn->prepare("
    SELECT
        filename,
        subdirectory,
        size_bytes,
        status,
        first_seen,
        last_modified
    FROM vicon_files
    WHERE capture_id = ?
    ORDER BY subdirectory, filename
");
$stmt->bind_param('s', $captureId);
$stmt->execute();
$result = $stmt->get_result();

$filesBySubdir = [];
while ($row = $result->fetch_assoc()) {
    $subdir = $row['subdirectory'];
    if (!isset($filesBySubdir[$subdir])) {
        $filesBySubdir[$subdir] = [];
    }
    $filesBySubdir[$subdir][] = $row;
}

jsonResponse([
    'capture' => $capture,
    'files' => $filesBySubdir
]);
?>
```

#### `/web/vicon_dashboard/api/search.php`
Search captures:

```php
<?php
require_once('db.php');

$conn = getDbConnection();

$query = isset($_GET['q']) ? $_GET['q'] : '';

if (empty($query)) {
    jsonResponse(['results' => []]);
}

$searchTerm = "%{$query}%";
$stmt = $conn->prepare("
    SELECT
        capture_id,
        date_dir,
        recording_dir,
        first_seen,
        last_modified,
        file_count,
        total_size_bytes
    FROM vicon_captures
    WHERE recording_dir LIKE ?
    ORDER BY last_modified DESC
    LIMIT 20
");
$stmt->bind_param('s', $searchTerm);
$stmt->execute();
$result = $stmt->get_result();

$results = [];
while ($row = $result->fetch_assoc()) {
    $results[] = $row;
}

jsonResponse(['results' => $results]);
?>
```

### 4. Create Frontend HTML

Create `/web/vicon_dashboard/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vicon FTP Monitor Dashboard</title>
    <link rel="stylesheet" href="css/dashboard.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
    <div class="container">
        <header>
            <h1>🎥 Vicon FTP Monitor</h1>
            <div class="header-stats">
                <div class="stat-badge">
                    <span id="status-indicator" class="status-dot"></span>
                    <span id="last-update">Loading...</span>
                </div>
            </div>
        </header>

        <!-- Statistics Cards -->
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-icon">📊</div>
                <div class="stat-content">
                    <div class="stat-value" id="total-captures">-</div>
                    <div class="stat-label">Total Captures</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">📁</div>
                <div class="stat-content">
                    <div class="stat-value" id="total-files">-</div>
                    <div class="stat-label">Total Files</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">📈</div>
                <div class="stat-content">
                    <div class="stat-value" id="today-captures">-</div>
                    <div class="stat-label">Today's Captures</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">💾</div>
                <div class="stat-content">
                    <div class="stat-value" id="total-storage">-</div>
                    <div class="stat-label">Total Storage</div>
                </div>
            </div>
        </div>

        <!-- Search & Filter -->
        <div class="toolbar">
            <input type="search" id="search-input" placeholder="Search recordings...">
            <input type="date" id="date-filter" value="">
            <button id="refresh-btn">🔄 Refresh</button>
        </div>

        <!-- File Type Distribution Chart -->
        <div class="chart-container">
            <h2>File Type Distribution</h2>
            <canvas id="fileTypeChart"></canvas>
        </div>

        <!-- Captures List -->
        <div class="captures-section">
            <h2>Captures</h2>
            <div id="captures-list" class="captures-list">
                <div class="loading">Loading captures...</div>
            </div>
        </div>

        <!-- Recent Files -->
        <div class="recent-section">
            <h2>Recent Files</h2>
            <div id="recent-files" class="recent-files">
                <div class="loading">Loading recent files...</div>
            </div>
        </div>

        <!-- File Details Modal -->
        <div id="file-modal" class="modal">
            <div class="modal-content">
                <span class="close">&times;</span>
                <h2 id="modal-title">Loading...</h2>
                <div id="modal-body"></div>
            </div>
        </div>
    </div>

    <script src="js/dashboard.js"></script>
</body>
</html>
```

### 5. Create Dashboard JavaScript

Create `/web/vicon_dashboard/js/dashboard.js`:

```javascript
// Dashboard state
let refreshInterval;
const API_BASE = '/vicon_dashboard/api';
const REFRESH_RATE = 5000; // 5 seconds

// Initialize dashboard
document.addEventListener('DOMContentLoaded', () => {
    initializeDashboard();
    setupEventListeners();
    startAutoRefresh();
});

function initializeDashboard() {
    // Set today's date as default
    document.getElementById('date-filter').valueAsDate = new Date();

    // Load initial data
    loadStats();
    loadCaptures();
}

function setupEventListeners() {
    document.getElementById('search-input').addEventListener('input', handleSearch);
    document.getElementById('date-filter').addEventListener('change', loadCaptures);
    document.getElementById('refresh-btn').addEventListener('click', () => {
        loadStats();
        loadCaptures();
    });

    // Modal close
    document.querySelector('.close').addEventListener('click', closeModal);
    window.addEventListener('click', (e) => {
        if (e.target.classList.contains('modal')) closeModal();
    });
}

function startAutoRefresh() {
    refreshInterval = setInterval(() => {
        loadStats();
        loadCaptures();
    }, REFRESH_RATE);
}

// API Functions
async function loadStats() {
    try {
        const response = await fetch(`${API_BASE}/stats.php`);
        const data = await response.json();

        updateStats(data);
        updateRecentFiles(data.recent_files);
        updateFileTypeChart(data.file_types);

        // Update status indicator
        document.getElementById('status-indicator').classList.add('active');
        document.getElementById('last-update').textContent =
            `Updated: ${new Date().toLocaleTimeString()}`;
    } catch (error) {
        console.error('Failed to load stats:', error);
        document.getElementById('status-indicator').classList.remove('active');
    }
}

async function loadCaptures() {
    const date = document.getElementById('date-filter').value;

    try {
        const response = await fetch(`${API_BASE}/captures.php?date=${date}`);
        const data = await response.json();

        displayCaptures(data.captures);
    } catch (error) {
        console.error('Failed to load captures:', error);
    }
}

async function loadCaptureFiles(captureId) {
    try {
        const response = await fetch(`${API_BASE}/files.php?capture_id=${encodeURIComponent(captureId)}`);
        const data = await response.json();

        showFileModal(data);
    } catch (error) {
        console.error('Failed to load files:', error);
    }
}

async function handleSearch(e) {
    const query = e.target.value.trim();

    if (query.length < 2) {
        loadCaptures();
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/search.php?q=${encodeURIComponent(query)}`);
        const data = await response.json();

        displayCaptures(data.results);
    } catch (error) {
        console.error('Search failed:', error);
    }
}

// Display Functions
function updateStats(data) {
    document.getElementById('total-captures').textContent =
        data.metadata?.total_captures || '0';
    document.getElementById('total-files').textContent =
        data.metadata?.total_files || '0';
    document.getElementById('today-captures').textContent =
        data.today?.today_captures || '0';
    document.getElementById('total-storage').textContent =
        formatBytes(data.today?.today_size || 0);
}

function displayCaptures(captures) {
    const container = document.getElementById('captures-list');

    if (!captures || captures.length === 0) {
        container.innerHTML = '<div class="empty">No captures found</div>';
        return;
    }

    container.innerHTML = captures.map(capture => `
        <div class="capture-card" onclick="loadCaptureFiles('${capture.capture_id}')">
            <div class="capture-header">
                <h3>${capture.recording_dir}</h3>
                <span class="capture-date">${capture.date_dir}</span>
            </div>
            <div class="capture-stats">
                <span>📁 ${capture.file_count} files</span>
                <span>💾 ${formatBytes(capture.total_size_bytes)}</span>
            </div>
            <div class="capture-time">
                Last modified: ${formatDate(capture.last_modified)}
            </div>
        </div>
    `).join('');
}

function updateRecentFiles(files) {
    const container = document.getElementById('recent-files');

    if (!files || files.length === 0) {
        container.innerHTML = '<div class="empty">No recent files</div>';
        return;
    }

    container.innerHTML = files.map(file => `
        <div class="recent-file">
            <div class="file-icon">${getFileIcon(file.subdirectory)}</div>
            <div class="file-info">
                <div class="file-name">${file.filename}</div>
                <div class="file-meta">
                    ${file.subdirectory} • ${formatBytes(file.size_bytes)} • ${formatDate(file.last_modified)}
                </div>
            </div>
        </div>
    `).join('');
}

function showFileModal(data) {
    const modal = document.getElementById('file-modal');
    const title = document.getElementById('modal-title');
    const body = document.getElementById('modal-body');

    title.textContent = data.capture.recording_dir;

    let html = `
        <div class="modal-stats">
            <div>📅 ${data.capture.date_dir}</div>
            <div>📁 ${data.capture.file_count} files</div>
            <div>💾 ${formatBytes(data.capture.total_size_bytes)}</div>
        </div>
    `;

    // Group files by subdirectory
    for (const [subdir, files] of Object.entries(data.files)) {
        html += `
            <div class="file-group">
                <h3>${getFileIcon(subdir)} ${subdir} (${files.length})</h3>
                <div class="file-list">
                    ${files.map(file => `
                        <div class="file-item">
                            <span class="file-name">${file.filename}</span>
                            <span class="file-size">${formatBytes(file.size_bytes)}</span>
                            ${file.status === 'growing' ? '<span class="badge growing">Growing</span>' : ''}
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    body.innerHTML = html;
    modal.style.display = 'block';
}

function closeModal() {
    document.getElementById('file-modal').style.display = 'none';
}

function updateFileTypeChart(fileTypes) {
    const canvas = document.getElementById('fileTypeChart');
    const ctx = canvas.getContext('2d');

    // Destroy existing chart if it exists
    if (window.fileTypeChartInstance) {
        window.fileTypeChartInstance.destroy();
    }

    window.fileTypeChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: fileTypes.map(ft => ft.subdirectory),
            datasets: [{
                data: fileTypes.map(ft => ft.count),
                backgroundColor: [
                    '#FF6384',
                    '#36A2EB',
                    '#FFCE56',
                    '#4BC0C0',
                    '#9966FF',
                    '#FF9F40'
                ]
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    position: 'right'
                }
            }
        }
    });
}

// Utility Functions
function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString();
}

function getFileIcon(subdirectory) {
    const icons = {
        'unreal': '🎮',
        'livelink': '📡',
        'obs': '🎥',
        'shogun_live': '🎬',
        'shogun_post': '🎞️',
        'root': '📄'
    };
    return icons[subdirectory] || '📁';
}
```

### 6. Create Dashboard CSS

Create `/web/vicon_dashboard/css/dashboard.css`:

```css
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
    background: #f5f7fa;
    color: #333;
    line-height: 1.6;
}

.container {
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px;
}

header {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 30px;
    border-radius: 10px;
    margin-bottom: 30px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

header h1 {
    font-size: 2em;
}

.header-stats {
    display: flex;
    gap: 20px;
}

.stat-badge {
    background: rgba(255, 255, 255, 0.2);
    padding: 10px 20px;
    border-radius: 20px;
    display: flex;
    align-items: center;
    gap: 10px;
}

.status-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #ccc;
}

.status-dot.active {
    background: #4ade80;
    animation: pulse 2s infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: 20px;
    margin-bottom: 30px;
}

.stat-card {
    background: white;
    padding: 25px;
    border-radius: 10px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    display: flex;
    align-items: center;
    gap: 15px;
}

.stat-icon {
    font-size: 3em;
}

.stat-value {
    font-size: 2em;
    font-weight: bold;
    color: #667eea;
}

.stat-label {
    color: #6b7280;
    font-size: 0.9em;
}

.toolbar {
    background: white;
    padding: 20px;
    border-radius: 10px;
    margin-bottom: 30px;
    display: flex;
    gap: 15px;
}

.toolbar input,
.toolbar button {
    padding: 10px 15px;
    border: 1px solid #ddd;
    border-radius: 5px;
    font-size: 1em;
}

.toolbar input[type="search"] {
    flex: 1;
}

.toolbar button {
    background: #667eea;
    color: white;
    border: none;
    cursor: pointer;
    transition: background 0.3s;
}

.toolbar button:hover {
    background: #5568d3;
}

.chart-container {
    background: white;
    padding: 30px;
    border-radius: 10px;
    margin-bottom: 30px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.chart-container h2 {
    margin-bottom: 20px;
    color: #333;
}

.captures-section,
.recent-section {
    background: white;
    padding: 30px;
    border-radius: 10px;
    margin-bottom: 30px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.captures-section h2,
.recent-section h2 {
    margin-bottom: 20px;
    color: #333;
}

.captures-list {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 15px;
}

.capture-card {
    background: #f9fafb;
    padding: 20px;
    border-radius: 8px;
    border-left: 4px solid #667eea;
    cursor: pointer;
    transition: transform 0.2s, box-shadow 0.2s;
}

.capture-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
}

.capture-header {
    display: flex;
    justify-content: space-between;
    align-items: start;
    margin-bottom: 10px;
}

.capture-header h3 {
    font-size: 1.1em;
    color: #333;
}

.capture-date {
    background: #667eea;
    color: white;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.8em;
}

.capture-stats {
    display: flex;
    gap: 15px;
    margin-bottom: 10px;
    color: #6b7280;
    font-size: 0.9em;
}

.capture-time {
    color: #9ca3af;
    font-size: 0.85em;
}

.recent-files {
    display: flex;
    flex-direction: column;
    gap: 10px;
}

.recent-file {
    display: flex;
    align-items: center;
    gap: 15px;
    padding: 15px;
    background: #f9fafb;
    border-radius: 8px;
}

.file-icon {
    font-size: 2em;
}

.file-info {
    flex: 1;
}

.file-name {
    font-weight: 500;
    color: #333;
}

.file-meta {
    font-size: 0.85em;
    color: #6b7280;
}

.modal {
    display: none;
    position: fixed;
    z-index: 1000;
    left: 0;
    top: 0;
    width: 100%;
    height: 100%;
    background-color: rgba(0,0,0,0.5);
}

.modal-content {
    background-color: white;
    margin: 5% auto;
    padding: 30px;
    border-radius: 10px;
    width: 80%;
    max-width: 900px;
    max-height: 80vh;
    overflow-y: auto;
}

.close {
    color: #aaa;
    float: right;
    font-size: 28px;
    font-weight: bold;
    cursor: pointer;
}

.close:hover {
    color: #000;
}

.modal-stats {
    display: flex;
    gap: 20px;
    margin: 20px 0;
    padding: 20px;
    background: #f9fafb;
    border-radius: 8px;
}

.file-group {
    margin: 20px 0;
}

.file-group h3 {
    color: #667eea;
    margin-bottom: 10px;
}

.file-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.file-item {
    display: flex;
    justify-content: space-between;
    padding: 10px;
    background: #f9fafb;
    border-radius: 5px;
}

.badge {
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.8em;
}

.badge.growing {
    background: #fbbf24;
    color: white;
}

.loading,
.empty {
    text-align: center;
    padding: 40px;
    color: #6b7280;
}

@media (max-width: 768px) {
    .stats-grid {
        grid-template-columns: 1fr;
    }

    .captures-list {
        grid-template-columns: 1fr;
    }

    .toolbar {
        flex-direction: column;
    }
}
```

## Usage

### Access Dashboard

1. **Navigate to dashboard:**
   ```
   http://your-server/vicon_dashboard/
   ```

2. **Features:**
   - View real-time statistics
   - Browse captures by date
   - Search for specific recordings
   - View files grouped by type (unreal, livelink, obs, etc.)
   - Auto-refreshes every 5 seconds

### API Endpoints

- `GET /vicon_dashboard/api/stats.php` - Overall statistics
- `GET /vicon_dashboard/api/captures.php?date=YYYY-MM-DD` - Captures for date
- `GET /vicon_dashboard/api/files.php?capture_id=XXX` - Files for capture
- `GET /vicon_dashboard/api/search.php?q=query` - Search captures

## Customization

### Change Refresh Rate

Edit `js/dashboard.js`:
```javascript
const REFRESH_RATE = 10000; // 10 seconds
```

### Add More Charts

Add to `js/dashboard.js`:
```javascript
function updateStorageChart(data) {
    // Chart.js code
}
```

### Custom Styling

Edit `css/dashboard.css` to match your branding.

## Security Notes

⚠️ **Important:**
1. Add authentication to API endpoints
2. Validate all input parameters
3. Use prepared statements (already implemented)
4. Consider adding CORS headers if needed
5. Use HTTPS in production

### Example Authentication

Add to top of each API file:
```php
<?php
session_start();
if (!isset($_SESSION['user_id'])) {
    http_response_code(401);
    die(json_encode(['error' => 'Unauthorized']));
}
?>
```

## Troubleshooting

### CORS Issues
Add to API files:
```php
header('Access-Control-Allow-Origin: *');
```

### Permission Denied
```bash
sudo chown -R www-data:www-data /web/vicon_dashboard
sudo chmod -R 755 /web/vicon_dashboard
```

### Database Connection Failed
Check `/web/mysql_config.php` credentials.

## Next Steps

1. ✅ Create dashboard files
2. ✅ Test API endpoints
3. ✅ Add authentication
4. ⚡ Add export functionality (CSV/JSON)
5. 📊 Add more chart types
6. 🔔 Add notifications for new captures
7. 📱 Make mobile responsive (already included)

## Support

For issues or questions, check the FTP monitor logs:
```bash
tail -f /home/gomer/viconSync/logs/ftp_monitor.log
```
