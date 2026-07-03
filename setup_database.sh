#!/bin/bash
# Setup script for Vicon FTP Monitor Database

echo "=== Vicon FTP Monitor Database Setup ==="
echo ""

# Check if pymysql is installed
echo "Checking for pymysql..."
python3 -c "import pymysql" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Installing pymysql..."
    pip3 install pymysql
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to install pymysql"
        exit 1
    fi
    echo "✓ pymysql installed"
else
    echo "✓ pymysql already installed"
fi

echo ""

# Test database configuration
echo "Testing database configuration..."
python3 db_config.py
if [ $? -ne 0 ]; then
    echo "ERROR: Database configuration test failed"
    exit 1
fi

echo ""

# Create database tables
echo "Creating database tables..."
echo "Enter MySQL password for user 'user':"
mysql -u user -p admin_gebarenoverleg < create_tables.sql

if [ $? -eq 0 ]; then
    echo "✓ Database tables created successfully"
else
    echo "ERROR: Failed to create database tables"
    exit 1
fi

echo ""

# Test database connection
echo "Testing database connection..."
python3 db_writer.py

if [ $? -eq 0 ]; then
    echo ""
    echo "=== Setup Complete ==="
    echo ""
    echo "Database is ready for use!"
    echo "You can now start the FTP monitor with: python3 ftp_monitor.py"
else
    echo "ERROR: Database connection test failed"
    exit 1
fi
