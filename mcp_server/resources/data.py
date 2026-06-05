"""
claire MCP Resources — Static Data
Resource: claire://info
"""


def register(mcp):

    @mcp.resource("claire://info")
    def server_info() -> str:
        """Static info about the Claire MCP server."""
        return (
            "Server: Claire\n"
            "Description: Real-time AI voice assistant — local, free, and fully capable.\n"
            "Framework: FastMCP (SSE transport)\n"
            "Tools: get_world_news, get_world_finance_news, open_world_monitor, "
            "open_finance_world_monitor, search_web, fetch_url, get_current_time, "
            "get_system_info, launch_app, play_spotify, play_youtube, "
            "show_code_in_terminal, format_json, word_count\n"
            "Version: 1.0.0"
        )
