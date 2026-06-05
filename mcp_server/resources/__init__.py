"""
claire MCP Resources Registry
"""

from mcp_server.resources import data


def register_all_resources(mcp):
    data.register(mcp)
