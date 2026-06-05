"""
claire MCP Tools — Utilities
Tools: format_json, word_count
"""

import json


def register(mcp):

    @mcp.tool()
    def format_json(data: str) -> str:
        """Pretty-print a JSON string with 2-space indentation."""
        try:
            parsed = json.loads(data)
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"

    @mcp.tool()
    def word_count(text: str) -> dict:
        """Count characters, words, and lines in a text string."""
        return {
            "characters": len(text),
            "words":      len(text.split()),
            "lines":      len(text.splitlines()),
        }
