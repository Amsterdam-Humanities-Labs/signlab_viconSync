#!/usr/bin/env python3
"""
Client Monitor API - Python Integration Example

This script demonstrates how to integrate your Python scripts with the
Client Monitor system.

Usage:
    1. Copy the ClientMonitor class to your project
    2. Initialize with your client details
    3. Call send_heartbeat() after successful execution

Example cron job (runs every hour):
    0 * * * * /usr/bin/python3 /path/to/your-script.py
"""

import requests
import json
import sys
import socket
from datetime import datetime
from typing import Optional, Dict, Any


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

        response = requests.post(
            f"{self.api_url}?action=register",
            json=data,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()

        result = response.json()
        if result.get("success"):
            print(f"✓ Client registered successfully: {self.client_id}")
        else:
            print(f"✗ Registration failed: {result.get('errors')}")

        return result

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

        response = requests.post(
            f"{self.api_url}?action=heartbeat",
            json=data,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()

        result = response.json()
        if result.get("success"):
            status = result.get("data", {}).get("status", "unknown")
            print(f"✓ Heartbeat sent successfully - Status: {status}")
        else:
            print(f"✗ Heartbeat failed: {result.get('errors')}")

        return result

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


# ==============================================================================
# EXAMPLE USAGE
# ==============================================================================

def main():
    """
    Example script showing how to use the ClientMonitor class
    """
    # Configuration
    API_URL = "http://localhost/client_monitor_api/api.php"
    CLIENT_ID = "python-data-processor"
    CLIENT_NAME = "Python Data Processor"
    DESCRIPTION = "Processes daily data exports and generates reports"
    HEARTBEAT_INTERVAL = 3600  # 1 hour

    # Initialize monitor
    monitor = ClientMonitor(
        api_url=API_URL,
        client_id=CLIENT_ID,
        client_name=CLIENT_NAME,
        description=DESCRIPTION,
        heartbeat_interval=HEARTBEAT_INTERVAL
    )

    print("=" * 60)
    print(f"Starting: {CLIENT_NAME}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    try:
        # YOUR SCRIPT LOGIC HERE
        # Example:

        # Step 1: Load data
        print("Loading data...")
        records_processed = 0

        # Step 2: Process data
        print("Processing data...")
        for i in range(100):
            # Simulate processing
            records_processed += 1

        # Step 3: Generate reports
        print("Generating reports...")
        reports_generated = 5

        # Send heartbeat on success
        monitor.send_heartbeat_with_stats(
            status="success",
            message=f"Processed {records_processed} records",
            stats={
                "records_processed": records_processed,
                "reports_generated": reports_generated,
                "execution_time_seconds": 10
            }
        )

        print("\n✓ Script completed successfully")
        return 0

    except Exception as e:
        print(f"\n✗ Script failed: {e}")

        # Send heartbeat on error
        try:
            monitor.send_heartbeat_with_stats(
                status="error",
                message=f"Script failed: {str(e)}",
                stats={"error_type": type(e).__name__}
            )
        except Exception as heartbeat_error:
            print(f"✗ Failed to send error heartbeat: {heartbeat_error}")

        return 1


# ==============================================================================
# USAGE EXAMPLES
# ==============================================================================

def example_simple_usage():
    """Example 1: Simple usage with minimal configuration"""
    monitor = ClientMonitor(
        api_url="http://localhost/client_monitor_api/api.php",
        client_id="simple-script",
        client_name="Simple Script"
    )

    # Do your work...
    print("Doing some work...")

    # Send heartbeat
    monitor.send_heartbeat()


def example_with_metadata():
    """Example 2: Usage with custom metadata"""
    monitor = ClientMonitor(
        api_url="http://localhost/client_monitor_api/api.php",
        client_id="metadata-script",
        client_name="Script with Metadata"
    )

    # Do your work...
    files_processed = 42

    # Send heartbeat with custom metadata
    monitor.send_heartbeat({
        "files_processed": files_processed,
        "database_version": "5.7",
        "custom_field": "value"
    })


def example_registration():
    """Example 3: Register a new client (run once)"""
    monitor = ClientMonitor(
        api_url="http://localhost/client_monitor_api/api.php",
        client_id="new-client",
        client_name="New Client Script",
        description="This is a new script to monitor",
        heartbeat_interval=1800  # 30 minutes
    )

    # Register the client
    monitor.register({
        "version": "2.0.0",
        "environment": "production"
    })


if __name__ == "__main__":
    sys.exit(main())
