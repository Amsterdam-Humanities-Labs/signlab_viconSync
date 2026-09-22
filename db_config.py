#!/usr/bin/env python3
"""
Database configuration parser.
Reads MySQL credentials from <root>/mysql_config.php (normally /web/mysql_config.php)
"""

import re
import os

from sc_paths import sc_path


def get_db_config():
    """Parse PHP config file and return database credentials."""
    php_config_file = sc_path('mysql_config.php')

    if not os.path.exists(php_config_file):
        raise FileNotFoundError(f"MySQL config file not found: {php_config_file}")

    config = {}

    with open(php_config_file, 'r') as f:
        content = f.read()

    # Extract values using regex
    patterns = {
        'host': r'\$servername\s*=\s*["\']([^"\']+)["\']',
        'user': r'\$username\s*=\s*["\']([^"\']+)["\']',
        'password': r'\$password\s*=\s*["\']([^"\']+)["\']',
        'database': r'\$database\s*=\s*["\']([^"\']+)["\']'
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, content)
        if match:
            config[key] = match.group(1)
        else:
            raise ValueError(f"Could not find {key} in {php_config_file}")

    return config


if __name__ == '__main__':
    # Test configuration parsing
    config = get_db_config()
    print("Database Configuration:")
    print(f"  Host: {config['host']}")
    print(f"  User: {config['user']}")
    print(f"  Password: {'*' * len(config['password'])}")
    print(f"  Database: {config['database']}")
