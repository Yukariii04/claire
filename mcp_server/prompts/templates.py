"""
claire MCP Prompts — Templates
Prompt templates: summarize, explain_code
"""


def register(mcp):

    @mcp.prompt()
    def summarize(text: str) -> str:
        """Produce a concise summary prompt for any text."""
        return f"Summarize the following text concisely:\n\n{text}"

    @mcp.prompt()
    def explain_code(code: str, language: str = "Python") -> str:
        """Produce a prompt that asks for a plain-English explanation of code."""
        return (
            f"Explain the following {language} code in plain English. "
            f"Focus on what it does, not how:\n\n"
            f"```{language.lower()}\n{code}\n```"
        )
