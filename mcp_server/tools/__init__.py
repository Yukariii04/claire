"""
claire MCP Server — Tools Registry
Imports and registers all tool modules with the FastMCP instance.
"""

from mcp_server.tools import web, system, utils, os_control


def register_all_tools(mcp):
    web.register(mcp)
    system.register(mcp)
    utils.register(mcp)
    os_control.register(mcp)
