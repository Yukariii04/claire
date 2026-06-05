"""
claire Voice Agent — Main Agent
Pipeline: Vosk STT → Groq LLM (OpenAI-compatible) → Kokoro TTS + Silero VAD

Run via:  uv run claire_voice
Startup order:
  1. livekit-server --dev      (terminal 1)
  2. uv run claire             (terminal 2 — MCP server)
  3. uv run claire_voice       (terminal 3 — this file)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, cli, mcp
from livekit.plugins import openai as lk_openai
from livekit.plugins import silero

from voice_agent.vosk_stt import VoskSTT
from voice_agent.kokoro_tts import KokoroTTS

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "models/vosk-model-en-us-0.22")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
MCP_SERVER_URL  = "http://127.0.0.1:8000/sse"


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


# ── Claire Agent ───────────────────────────────────────────────────────────

class ClaireAgent(Agent):
    def __init__(self):
        super().__init__(instructions=SYSTEM_PROMPT)

    async def on_enter(self):
        await self.session.say(_greeting(), allow_interruptions=True)


# ── Entry point ────────────────────────────────────────────────────────────

async def entrypoint(ctx: cli.JobContext):
    await ctx.connect()

    session = AgentSession(
        stt=VoskSTT(model_path=VOSK_MODEL_PATH),
        llm=lk_openai.LLM(
            model="llama-3.1-8b-instant",
            api_key=GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        ),
        tts=KokoroTTS(voice="af_heart", speed=1.15),
        vad=silero.VAD.load(),
        turn_detection="vad",
        min_endpointing_delay=0.3,
        mcp_servers=[mcp.MCPServerHTTP(url=MCP_SERVER_URL)],
    )

    await session.start(agent=ClaireAgent(), room=ctx.room)


def dev():
    """CLI wrapper — auto-injects 'dev' arg so users don't need to type it manually."""
    if len(sys.argv) == 1:
        sys.argv.append("dev")
    cli.run_app(cli.WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    dev()
