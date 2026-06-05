"""
claire MCP Prompts Registry
"""

from mcp_server.prompts import templates


def register_all_prompts(mcp):
    templates.register(mcp)
