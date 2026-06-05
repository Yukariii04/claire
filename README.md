# claire 🤖

A **real-time AI voice assistant** that runs 100% locally on Windows.  
Listens → thinks → speaks. Launches apps, plays Spotify, opens YouTube, shows code in a terminal.

**Stack** — all free, no paid APIs (except a free Groq key):

| Component | Technology |
|-----------|-----------|
| STT | Vosk (offline) |
| LLM | Groq Cloud — `llama-3.1-8b-instant` (free tier) |
| TTS | Kokoro-82M (offline) |
| VAD | Silero |
| WebRTC | Local LiveKit Server |
| MCP | FastMCP (SSE) |

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) — `pip install uv`
- [livekit-server binary](https://github.com/livekit/livekit/releases) — download and add to PATH
- Vosk model — download `vosk-model-en-us-0.22` from https://alphacephei.com/vosk/models  
  and unzip into `models/vosk-model-en-us-0.22/`
- Free Groq API key — https://console.groq.com
- YouTube app installed from the Microsoft Store (for `play_youtube` tool)

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

### 4. Download Kokoro model files

On first run, `kokoro-onnx` will automatically download the model (~330 MB).  
Or download manually:
```powershell
uv run python -c "from kokoro_onnx import Kokoro; Kokoro('kokoro-v1_0.onnx', 'voices-v1_0.bin')"
```

### 5. Run (3 terminals)

**Terminal 1 — LiveKit server:**
```powershell
livekit-server --dev
```

**Terminal 2 — MCP server:**
```powershell
uv run claire
```

**Terminal 3 — Voice agent:**
```powershell
uv run claire_voice
```

### 6. Connect

Open https://agents-playground.livekit.io and connect to `ws://localhost:7880` with API key `devkey` and secret `secret`.

---

## Tools

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
| `format_json` | Pretty-prints JSON |
| `word_count` | Counts chars/words/lines |

---

## Adding Tools

1. Create `mcp_server/tools/my_tool.py`
2. Define `def register(mcp):` and use `@mcp.tool()` decorators
3. Import and call `register(mcp)` in `mcp_server/tools/__init__.py`
