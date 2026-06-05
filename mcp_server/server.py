"""
claire MCP Server — Entry Point
Starts FastMCP with SSE transport on port 8000.
Run via:  uv run claire
"""

import os
from dotenv import load_dotenv
from fastmcp import FastMCP

from mcp_server.tools import register_all_tools
from mcp_server.prompts import register_all_prompts
from mcp_server.resources import register_all_resources

load_dotenv()

SERVER_NAME = os.getenv("SERVER_NAME", "Claire")

mcp = FastMCP(
    name=SERVER_NAME,
    instructions=(
        "You are Claire, a real-time AI voice assistant. "
        "You have tools to fetch world news, finance news, open dashboards, "
        "search the web, launch Windows apps, play Spotify music, "
        "play YouTube videos, show code in terminal, and more. "
        "Always call tools silently and respond in natural spoken language."
    ),
)

register_all_tools(mcp)
register_all_prompts(mcp)
register_all_resources(mcp)


def main():
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
