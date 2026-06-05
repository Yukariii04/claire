"""
claire MCP Server — Config
Loads and exposes all environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SERVER_NAME: str = os.getenv("SERVER_NAME", "Claire")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
