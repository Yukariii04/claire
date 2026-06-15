# claire 🤖

A **real-time AI voice assistant** that runs locally on Windows.  
Listens → thinks → speaks. Launches apps, plays Spotify, opens YouTube, fetches news, shows code in a terminal.

Now with a **Dynamic Island** floating overlay — no browser needed.

**Stack** — all free, no paid APIs (except a free Groq key):

| Component | Technology |
|-----------|-----------|
| STT | Groq Whisper (`whisper-large-v3`) |
| LLM | Groq Cloud — `llama-3.1-8b-instant` (free tier) |
| TTS | Kokoro-82M (offline, local) |
| VAD | Energy-based (built-in) |
| Audio | sounddevice (direct mic/speaker) |
| UI | Dynamic Island overlay (customtkinter) |

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) — `pip install uv`
- Free Groq API key — https://console.groq.com
- Kokoro TTS models — `kokoro-v1_0.onnx` and `voices-v1_0.bin` in the project root
- YouTube app from the Microsoft Store (for `play_youtube` tool)

### 2. Install

```powershell
cd claire
uv sync
```

### 3. Configure

```powershell
copy .env.example .env
# Edit .env and add your GROQ_API_KEY
```

### 4. Run  ← Just ONE command!

```powershell
uv run claire_voice
```

That's it. The Dynamic Island overlay pops up, Claire greets you, and starts listening.

---

## Tools (12 total — all called automatically by the LLM)

| Tool | What it does |
|------|-------------|
| `get_world_news` | Fetches latest headlines from BBC, CNBC, NYT, Al Jazeera |
| `get_world_finance_news` | Fetches finance news from Bloomberg, Reuters, MarketWatch, etc. |
| `open_world_monitor` | Opens worldmonitor.app in browser |
| `open_finance_world_monitor` | Opens finance.worldmonitor.app in browser |
| `search_web` | DuckDuckGo search — no API key |
| `fetch_url` | Fetches raw text from any URL |
| `get_current_time` | Returns current UTC time |
| `get_system_info` | Returns OS info |
| `launch_app` | Opens any Windows app by name |
| `play_spotify` | Plays music/artist/playlist in Spotify app |
| `play_youtube` | Searches/plays in YouTube Windows app |
| `show_code_in_terminal` | Displays code in a new PowerShell window |

---

## Architecture

```
Microphone → sounddevice → Energy VAD → Groq Whisper STT → Groq LLM → Kokoro TTS → Speaker
                                                              ↕
                                                         Tool Calls
                                                        (12 built-in)
```

No LiveKit. No WebRTC. No browser. No MCP server. Just your mic and speaker.

---

## Project Structure

```
claire/
├── voice_agent/
│   ├── __init__.py          # Package marker
│   ├── agent_claire.py      # Entry point — wires pipeline + overlay
│   ├── pipeline.py          # Audio pipeline (VAD → STT → LLM → TTS)
│   └── overlay.py           # Dynamic Island UI (customtkinter)
├── kokoro-v1_0.onnx         # Kokoro TTS model (gitignored)
├── voices-v1_0.bin          # Kokoro voice data (gitignored)
├── pyproject.toml           # Dependencies & entry point
├── .env.example             # Template for secrets
└── README.md
```

## Adding Tools

Tools are defined inline in `voice_agent/pipeline.py`:
1. Add the tool schema to `TOOL_SCHEMAS`
2. Add the implementation to `_execute_tool()`
