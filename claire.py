"""
Claire Voice Assistant — Root Entry Point
Allows starting the agent via: python claire.py
"""

import os
import sys
import warnings

# Suppress all C/C++ audio callback and deprecation warnings before importing anything
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
warnings.simplefilter("ignore")
warnings.showwarning = lambda *args, **kwargs: None

from voice_agent.agent_claire import main

if __name__ == "__main__":
    main()

