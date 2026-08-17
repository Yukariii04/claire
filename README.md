<div align="center">
  <img src="voice_agent/assets/avatar.png" width="130" height="130" alt="Claire AI Voice Assistant" style="border-radius: 50%;" />
  <h1>claire</h1>

  <p><strong>A Real-Time Local AI Voice Assistant for Windows with Dynamic Island Overlay</strong></p>
  <p><em>Calm • Sharp • Native • Zero-Cost</em></p>
</div>

---

## Overview

**Claire** is a native, ultra-responsive AI voice assistant designed specifically for Windows. She listens to your microphone, thinks using fast Groq-hosted open LLMs, speaks back with offline neural TTS, and automates your desktop with native Windows tools and internet search.

Claire runs entirely without WebRTC servers, LiveKit, or browser dependencies — just your mic, your speaker, and a sleek **Dynamic Island** desktop overlay.

---

## 🌟 What Claire Can Do & Current Capabilities

> [!NOTE]
> **Testing & Beta Phase Notice**: Claire is currently in active development and testing. Core voice interactions and desktop automation tools are functional, with ongoing tuning for cross-application edge cases.

### 1. 🎙️ High-Speed Voice Pipeline
- **Continuous Mic Listening**: RMS energy-based Voice Activity Detection (VAD) captures speech chunks automatically.
- **Accurate Speech-to-Text**: Transcribes audio instantly via Groq Whisper (`whisper-large-v3`).
- **Conversational Intelligence**: Powered by Groq's `openai/gpt-oss-20b` (fast, witty, and concise).
- **Offline Neural TTS**: Synthesizes speech locally using `KittenTTS` (default voice `Luna` at `1.20x` natural conversational speed) with zero cloud latency.
- **Barge-In & Echo Guard**: Halts speaking instantly if you speak while she is talking, and prevents self-listening during playback.

### 2. ✨ Dynamic Island Desktop Overlay
- **Minimalist Floating Pill**: A frameless, always-on-top pill at the top of your screen.
- **Custom Pixel-Art Anime Avatar**: Visual avatar featuring an animated glowing aura that changes color with agent state (*Emerald for Listening, Amber for Thinking, Indigo for Speaking, Subtle Halo for Idle*).
- **Smooth Expansion**: Double-click or click the 3D chevron to smoothly expand into a full transcript and control card with spring physics.
- **Draggable Anywhere**: Click and drag the overlay to place it anywhere on your desktop.

### 3. 🛠️ Built-in Tools & Desktop Automation

| Tool | Action | Current Status & Testing Notes |
|:---|:---|:---|
| **`launch_app`** | Opens Windows apps (*Discord, Spotify, VS Code, Chrome, Terminal, Calculator, Notepad, Steam, Obsidian, etc.*) | **Functional**: Resolves Discord via `Update.exe --processStart`, Start Menu shortcuts, and system paths. |
| **`close_app`** | Closes or terminates open Windows applications | **Functional**: Validates running processes and terminates them cleanly using `taskkill /F`. |
| **`control_media`** | Controls media playback (*Play/Pause, Next track, Previous track, Volume up/down, Mute*) | **Functional**: Dispatches Windows hardware scan codes (`MapVirtualKeyW`) universally across Spotify, YouTube, and media players. |
| **`play_spotify`** | Searches and opens tracks, artists, or playlists on Spotify | **Beta / Testing**: Triggers search and playback; track selection depends on active desktop Spotify client focus. |
| **`play_youtube`** | Searches and opens YouTube videos in your default browser | **Functional**: Launches universal browser video searches directly. |
| **`search_web`** | Live internet search via DuckDuckGo | **Functional**: Retrieves instant search summaries without requiring any API keys. |
| **`fetch_url`** | Reads and extracts readable text from web pages | **Functional**: HTML sanitizer that cleans scripts/styles and extracts readable text. |
| **`show_code_in_terminal`** | Pops out code, scripts, or long text in a new PowerShell window | **Functional**: Opens a dedicated styled terminal to display code blocks. |
| **`get_current_time`** | Returns current UTC and local date/time | **Functional**: Accurately provides timestamps and timezones. |
| **`get_system_info`** | Inspects Windows OS, version, and architecture | **Functional**: Returns system platform diagnostics. |

---

## ⚡ Quick Start

### 1. Prerequisites
- **Python**: `3.11.x` or `3.12.x` *(Recommended: 3.12.13)*
- **Free Groq API Key**: Get a free API key at [console.groq.com](https://console.groq.com)

### 2. Installation
Clone the repository and install all dependencies:

```powershell
git clone https://github.com/Yukariii04/claire.git
cd claire
pip install -r requirements.txt
```

*(Running `pip install -r requirements.txt` automatically installs all dependencies and registers the `claire` executable in your environment!)*

### 3. Configuration
Copy `.env.example` to `.env` and set your Groq API key:

```powershell
copy .env.example .env
```

Edit `.env`:
```env
GROQ_API_KEY=gsk_your_groq_api_key_here
LLM_MODEL=openai/gpt-oss-20b
TTS_VOICE=Luna
TTS_SPEED=1.20
```

### 4. Launch Claire

Just type:

```powershell
claire
```

*(You can also use `python claire.py`, `.\claire.bat`, or `.\claire.ps1`)*

The Dynamic Island overlay will pop up at the top of your screen, greet you, and start listening immediately.

---

## 📦 Verified Dependency Matrix

| Package | Version | Purpose |
|:---|:---|:---|
| **`python`** | `3.12.13` *(or 3.11.9)* | Host runtime |
| **`kittentts`** | `0.8.1` | Offline local neural TTS engine |
| **`httpx`** | `0.28.1` | Fast HTTP client for Groq API |
| **`sounddevice`** | `0.5.5` | Low-latency audio I/O stream |
| **`soundfile`** | `0.14.0` | High-fidelity audio file processing |
| **`numpy`** | `2.5.2` *(or 2.4.x)* | Audio buffer array processing & RMS VAD |
| **`customtkinter`** | `6.0.0` | Modern Dynamic Island UI overlay |
| **`pillow`** | `12.3.0` | Custom avatar image rendering & circular masks |
| **`duckduckgo_search`** | `8.1.1` | Free web search integration |
| **`python-dotenv`** | `1.2.3` | Local environment configuration |

---

## 📐 Architecture

```
Microphone → sounddevice → Energy VAD → Groq Whisper STT → Groq LLM (GPT-OSS-20B) → KittenTTS → Speaker
                                                                     ↕
                                                                Tool Engine
                                                               (10 built-in)
```

For full Mermaid sequence diagrams, state machines, and concurrency models, see [ARCHITECTURE.md](file:///c:/Users/ASUS/Documents/Claire/ARCHITECTURE.md).

---

## 📁 Project Structure

```
claire/
├── voice_agent/
│   ├── __init__.py          # Package marker
│   ├── agent_claire.py      # Main entry point & event bridge
│   ├── pipeline.py          # Audio pipeline (VAD → STT → LLM → TTS)
│   ├── overlay.py           # Dynamic Island UI (customtkinter)
│   └── assets/
│       └── avatar.png       # Anime pixel-art avatar image
├── claire.py                # Python root launcher
├── claire.bat               # Windows batch launcher
├── claire.ps1               # PowerShell launcher
├── requirements.txt         # Pip dependency manifest
├── pyproject.toml           # Package metadata & CLI entrypoint
├── ARCHITECTURE.md          # Visual architecture & Mermaid diagrams
├── LICENSE                  # MIT License
├── .env.example             # Environment template
└── README.md
```

---

## 📄 License

This project is open-source software licensed under the [MIT License](file:///c:/Users/ASUS/Documents/Claire/LICENSE).
