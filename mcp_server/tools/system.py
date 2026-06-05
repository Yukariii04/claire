"""
claire MCP Tools — System
Tools: get_current_time, get_system_info
"""

import platform
from datetime import datetime, timezone


def register(mcp):

    @mcp.tool()
    def get_current_time() -> str:
        """Return the current date and time in ISO 8601 format (UTC)."""
        return datetime.now(timezone.utc).isoformat()

    @mcp.tool()
    def get_system_info() -> dict:
        """Return basic information about the operating system and hardware."""
        return {
            "os":             platform.system(),
            "os_version":     platform.version(),
            "machine":        platform.machine(),
            "python_version": platform.python_version(),
        }
