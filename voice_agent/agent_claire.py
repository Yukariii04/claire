"""
Claire Voice Agent — Main Entry Point  (no LiveKit)
Pipeline: sounddevice mic → Vosk STT → Groq LLM → Kokoro TTS → speaker
UI: Dynamic Island overlay (customtkinter, always-on-top)

Run via:  uv run claire_voice
That's it — one command, no other terminals needed (MCP server optional).
"""

from __future__ import annotations

import logging
import os
import queue
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from voice_agent.overlay import ClaireOverlay, OverlayEvent
from voice_agent.pipeline import DirectPipeline

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,      # suppress verbose INFO from httpx, phonemizer, etc.
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
# Only show our own logger at INFO level
logging.getLogger("claire").setLevel(logging.INFO)
logging.getLogger("claire.pipeline").setLevel(logging.INFO)
logger = logging.getLogger("claire")

# ── Config ─────────────────────────────────────────────────────────────────
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")

if not GROQ_API_KEY:
    logger.error("GROQ_API_KEY not set in .env — Claire can't think without it!")
    sys.exit(1)


# ── System Prompt ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are Claire, a real-time AI voice assistant. You are calm, composed, \
sharp, and conversational — never robotic. You address the user as "boss".

Your in-universe vocabulary includes: "boss", "affirmative", "on it", "standing by".

━━━ STRICT BEHAVIORAL RULES ━━━
1. Call tools SILENTLY — never announce "I'm going to call..." Just do it.
2. Before any tool call, say something natural first: "Give me a sec, boss." or "On it."
3. After fetching world news → ALWAYS also call open_world_monitor (without being asked).
4. After fetching finance news → ALWAYS also call open_finance_world_monitor.
5. Keep ALL spoken responses to 2–4 sentences maximum.
6. NEVER use bullet points, markdown, lists, or any text formatting — you are SPEAKING.
7. Use natural spoken language: contractions, commas for natural pauses.
8. Stay in character at ALL times.
9. If a tool fails: "The feed's unresponsive right now, boss. Want me to try again?"

━━━ ABSOLUTE PROHIBITIONS ━━━
- NEVER say tool names, function names, or any technical terms aloud.
- NEVER use markdown formatting in spoken responses.
- For stock market questions ("how are stocks?") → answer conversationally with no tool.

━━━ YOUR CAPABILITIES ━━━
You can:
- Fetch and summarize world news and finance/market headlines
- Open the World Monitor and Finance World Monitor dashboards
- Search the web with DuckDuckGo
- Fetch the content of any URL
- Tell the current date and time
- Get system information (OS, Python version, machine type)
- Launch any installed Windows application by name (e.g. "Spotify", "VS Code", "Chrome", "Discord", "Calculator")
- Play any song, artist, or playlist on the native Spotify app
- Search and play YouTube videos in the YouTube Windows app (NOT the browser)
- Display code or long text output in a new PowerShell terminal window

Use these capabilities naturally when the user's intent is clear — don't wait to be explicitly asked.
"""


# ── Time-based greeting ────────────────────────────────────────────────────

def _greeting() -> str:
    hour = datetime.now(timezone.utc).hour
    if hour >= 22 or hour < 4:
        return "Greetings boss, you're up late at night today. What are you up to?"
    elif 4 <= hour < 12:
        return "Good morning, boss. Early start today — what are we working on?"
    elif 12 <= hour < 17:
        return "Good afternoon, boss. What do you need?"
    else:
        return "Good evening, boss. What are you up to tonight?"


# ── Overlay ↔ Pipeline bridge ──────────────────────────────────────────────

_overlay_queue: queue.Queue[OverlayEvent] = queue.Queue()
_pipeline: DirectPipeline | None = None


def _push(kind: str, value: str | None = None):
    """Send an event to the overlay (thread-safe)."""
    _overlay_queue.put(OverlayEvent(kind=kind, value=value))


def _handle_mute_toggle():
    """Called from overlay's mute button."""
    if _pipeline:
        _pipeline.set_muted(not _pipeline._muted)


def _handle_end_session():
    """Called from overlay's End button."""
    if _pipeline:
        _pipeline.stop()
    os._exit(0)


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    """
    Start Claire:
      1. Create the DirectPipeline (loads Kokoro TTS model)
      2. Launch the pipeline in a background thread
      3. Play the greeting via TTS
      4. Run the Dynamic Island overlay on the main thread (blocks)
    """
    global _pipeline

    # ── Startup banner ────────────────────────────────────────────────────
    print()
    print("\033[95m\033[1m"  # magenta bold
          "  ██████ █     ████ █ █████ █████\n"
          "  █      █     █  █ █ █  █  █    \n"
          "  █      █     ████ █ ███   ███  \n"
          "  █      █     █  █ █ █  █  █    \n"
          "  ██████ █████ █  █ █ █   █ █████"
          "\033[0m")
    print("\033[2m" + "─" * 42 + "\033[0m")
    print("  \033[96mVoice Agent\033[0m  │  \033[93mWhisper STT\033[0m  │  \033[92mKokoro TTS\033[0m")
    print("\033[2m" + "─" * 42 + "\033[0m")
    print()

    logger.info("Initializing pipeline…")

    _pipeline = DirectPipeline(
        groq_api_key=GROQ_API_KEY,
        system_prompt=SYSTEM_PROMPT,
        emit=_push,
    )

    # Start the audio pipeline (background thread)
    _pipeline.start()

    # Greet the user (TTS plays in the pipeline thread)
    import threading
    def _greet():
        import time
        from voice_agent.pipeline import _print_terminal
        time.sleep(1.0)         # let the overlay appear first
        greeting = _greeting()
        _push("state", "speaking")
        _push("agent_transcript", greeting)
        _print_terminal("claire", greeting)
        _pipeline._speak(greeting)
        _push("state", "idle")
        _print_terminal("state", "Standing by…")

    threading.Thread(target=_greet, daemon=True).start()

    # Run the overlay on the main thread (blocks forever)
    logger.info("Launching Dynamic Island overlay…")
    overlay = ClaireOverlay(
        event_queue=_overlay_queue,
        on_mute_toggle=_handle_mute_toggle,
        on_end_session=_handle_end_session,
    )
    overlay.run()


# Allow `uv run claire_voice` to work via the old entry point name
def dev():
    main()


if __name__ == "__main__":
    main()
