"""
Claire Voice Agent — Direct Local Pipeline  (no LiveKit)
Pipeline: sounddevice mic → Groq Whisper STT → Groq LLM (with tools) → KittenTTS → sounddevice speaker

This replaces the entire LiveKit agent framework with a lightweight,
direct audio pipeline.  Everything runs locally except the Groq LLM/STT calls.
"""

import ctypes
import glob
import io
import json
import logging
import os
import platform
import queue
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse
import warnings
import wave
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Suppress NumPy, sounddevice, and package deprecation/runtime warnings
warnings.filterwarnings("ignore")


import httpx
import numpy as np
import sounddevice as sd
from kittentts import KittenTTS

logger = logging.getLogger("claire.pipeline")


# ── Audio Constants ────────────────────────────────────────────────────────
SAMPLE_RATE     = 16000       # 16 kHz mono for Whisper
CHANNELS        = 1
BLOCK_SIZE      = 4000        # 250 ms chunks
TTS_SAMPLE_RATE = 24000       # KittenTTS outputs 24 kHz


# ── VAD Constants ──────────────────────────────────────────────────────────
ENERGY_THRESHOLD   = 300      # RMS energy to consider "speech"
SILENCE_TIMEOUT_S  = 1.2      # Seconds of silence to end an utterance
SPEECH_MIN_LEN     = 0.5      # Minimum speech duration (seconds) to process
MIN_AUDIO_SAMPLES  = 8000     # Minimum audio samples (0.5s at 16kHz) to send to Whisper

# Common Whisper hallucination phrases (noise transcribed as speech)
_WHISPER_HALLUCINATIONS = {
    "thank you", "thanks", "you", "yes", "yeah", "bye", "okay",
    "hmm", "uh", "um", "ah", "oh", "eh", "the", "a", "i",
    "thank you.", "thanks.", "yes.", "bye.", "okay.",
    "late", "the song", ".", "",
}

# ── Terminal Output (ANSI color codes) ─────────────────────────────────────
_RESET   = "\033[0m"
_BOLD    = "\033[1m"
_DIM     = "\033[2m"
_CYAN    = "\033[96m"    # user speech
_MAGENTA = "\033[95m"    # claire response
_YELLOW  = "\033[93m"    # tool calls
_GREEN   = "\033[92m"    # success / state
_RED     = "\033[91m"    # errors
_BLUE    = "\033[94m"    # info / whisper
_WHITE   = "\033[97m"


def _print_terminal(kind: str, text: str = "") -> None:
    """Print a formatted, color-coded line to the terminal."""
    ts = time.strftime("%H:%M:%S")
    if kind == "user":
        print(f"\n{_DIM}{ts}{_RESET}  {_CYAN}{_BOLD}YOU{_RESET}  {_WHITE}{text}{_RESET}")
    elif kind == "claire":
        print(f"{_DIM}{ts}{_RESET}  {_MAGENTA}{_BOLD}CLAIRE{_RESET}  {_WHITE}{text}{_RESET}")
    elif kind == "tool":
        print(f"{_DIM}{ts}{_RESET}  {_YELLOW}🔧 {text}{_RESET}")
    elif kind == "state":
        print(f"{_DIM}{ts}  ● {text}{_RESET}", end="\r")
    elif kind == "error":
        print(f"{_DIM}{ts}{_RESET}  {_RED}✗ {text}{_RESET}")
    elif kind == "whisper":
        print(f"{_DIM}{ts}  🎙  STT → {_BLUE}{text}{_RESET}")
    elif kind == "separator":
        print(f"{_DIM}{'─' * 60}{_RESET}")

# ── Tool Definitions (OpenAI function-calling format for Groq) ─────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web using DuckDuckGo and return the top search results.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch readable text content from any website or URL.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to fetch"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Return the current date and time (UTC and ISO 8601).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": "Return basic information about the operating system and hardware.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": "Open or launch any installed Windows application by name (e.g., 'Discord', 'Spotify', 'VS Code', 'Chrome', 'Calculator', 'Notepad', 'Terminal', 'Steam').",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string", "description": "Name of the application to open"}},
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_app",
            "description": "Close or terminate an open Windows application by name (e.g., 'Discord', 'Spotify', 'Chrome', 'Notepad', 'Calculator', 'VS Code').",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string", "description": "Name of the application to close"}},
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_media",
            "description": "Control Windows system and app media playback (Spotify, YouTube, video/music players). Actions: 'play_pause', 'next', 'previous', 'stop', 'volume_up', 'volume_down', 'mute'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action to perform: 'play_pause', 'next', 'previous', 'stop', 'volume_up', 'volume_down', 'mute'",
                        "enum": ["play_pause", "next", "previous", "stop", "volume_up", "volume_down", "mute"],
                    }
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_spotify",
            "description": "Search for and open a song, artist, album, or playlist on Spotify.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Song, artist, or playlist name"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_youtube",
            "description": "Search for and open a video on YouTube.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Video search query"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_code_in_terminal",
            "description": "Display code or text in a new, persistent PowerShell terminal window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The code or text to display"},
                    "language": {"type": "string", "description": "Programming language", "default": "python"},
                    "title": {"type": "string", "description": "Window title", "default": "Claire Output"},
                },
                "required": ["code"],
            },
        },
    },
]


# ── Tool Implementations (Native Windows & Web) ───────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Claire/1.0"}

# Virtual key codes for Windows media control
VK_VOLUME_MUTE      = 0xAD
VK_VOLUME_DOWN      = 0xAE
VK_VOLUME_UP        = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP       = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3


def _send_virtual_key(vk_code: int):
    """Simulate a native Windows virtual key press using hardware scan code."""
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP       = 0x0002
    try:
        u32 = ctypes.windll.user32
        scan = u32.MapVirtualKeyW(vk_code, 0)
        u32.keybd_event(vk_code, scan, KEYEVENTF_EXTENDEDKEY, 0)
        time.sleep(0.05)
        u32.keybd_event(vk_code, scan, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    except Exception as e:
        logger.warning("Virtual key simulation failed: %s", e)



def _control_media(action: str) -> str:
    """Control Windows media playback."""
    act = action.lower().strip().replace("-", "_").replace(" ", "_")
    if act in ("play", "pause", "play_pause", "toggle"):
        _send_virtual_key(VK_MEDIA_PLAY_PAUSE)
        return "Toggled media playback, boss."
    elif act in ("next", "next_track", "skip"):
        _send_virtual_key(VK_MEDIA_NEXT_TRACK)
        return "Skipped to the next track, boss."
    elif act in ("prev", "previous", "prev_track", "back"):
        _send_virtual_key(VK_MEDIA_PREV_TRACK)
        return "Went back to the previous track, boss."
    elif act in ("stop",):
        _send_virtual_key(VK_MEDIA_STOP)
        return "Stopped media playback, boss."
    elif act in ("volume_up", "vol_up", "louder"):
        for _ in range(3):
            _send_virtual_key(VK_VOLUME_UP)
        return "Turned the volume up, boss."
    elif act in ("volume_down", "vol_down", "quieter"):
        for _ in range(3):
            _send_virtual_key(VK_VOLUME_DOWN)
        return "Turned the volume down, boss."
    elif act in ("mute", "unmute", "toggle_mute"):
        _send_virtual_key(VK_VOLUME_MUTE)
        return "Toggled volume mute, boss."
    else:
        return f"Unknown media action: {action}"


def _run_uri(uri: str) -> None:
    """Open a URI or URL via Windows shell."""
    try:
        subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)
    except Exception:
        webbrowser.open(uri)


def _launch_windows_app(app_name: str) -> str:
    """Robust application launcher for Windows."""
    raw = app_name.strip()
    key = raw.lower()

    local_appdata = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

    known_targets: dict[str, list[str]] = {
        "discord": [
            os.path.join(local_appdata, "Discord", "Update.exe --processStart Discord.exe"),
            "discord:",
        ],
        "spotify": [
            "spotify:",
            os.path.join(appdata, "Spotify", "Spotify.exe"),
        ],
        "chrome": [
            os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
            "chrome.exe",
        ],
        "google chrome": [
            os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
            "chrome.exe",
        ],
        "firefox": [
            os.path.join(program_files, "Mozilla Firefox", "firefox.exe"),
            "firefox.exe",
        ],
        "edge": ["msedge.exe"],
        "microsoft edge": ["msedge.exe"],
        "vs code": [
            os.path.join(local_appdata, "Programs", "Microsoft VS Code", "Code.exe"),
            "code.cmd",
            "code",
        ],
        "vscode": [
            os.path.join(local_appdata, "Programs", "Microsoft VS Code", "Code.exe"),
            "code.cmd",
            "code",
        ],
        "code": [
            os.path.join(local_appdata, "Programs", "Microsoft VS Code", "Code.exe"),
            "code.cmd",
            "code",
        ],
        "notepad": ["notepad.exe"],
        "calculator": ["calc.exe"],
        "calc": ["calc.exe"],
        "terminal": ["wt.exe", "powershell.exe"],
        "windows terminal": ["wt.exe"],
        "powershell": ["powershell.exe"],
        "cmd": ["cmd.exe"],
        "task manager": ["taskmgr.exe"],
        "settings": ["ms-settings:"],
        "steam": [
            "steam:",
            os.path.join(program_files_x86, "Steam", "steam.exe"),
        ],
        "obsidian": [
            os.path.join(local_appdata, "Obsidian", "Obsidian.exe"),
        ],
        "telegram": [
            "tg:",
            os.path.join(appdata, "Telegram Desktop", "Telegram.exe"),
        ],
        "vlc": [
            os.path.join(program_files, "VideoLAN", "VLC", "vlc.exe"),
            "vlc.exe",
        ],
        "explorer": ["explorer.exe"],
        "file explorer": ["explorer.exe"],
        "paint": ["mspaint.exe"],
        "word": ["winword.exe"],
        "excel": ["excel.exe"],
    }

    # 1. Check known candidates
    if key in known_targets:
        for target in known_targets[key]:
            if target.endswith(":") or target.startswith(("http:", "https:", "discord:", "spotify:", "tg:", "steam:", "ms-settings:")):
                _run_uri(target)
                return f"Opening {raw} for you, boss."
            
            # Executable with arguments (e.g. Update.exe --processStart Discord.exe)
            if " --" in target:
                exe_part = target.split(" --")[0]
                if os.path.exists(exe_part):
                    subprocess.Popen(target, shell=True)
                    return f"Opening {raw} for you, boss."
            elif os.path.exists(target):
                subprocess.Popen(f'"{target}"', shell=True)
                return f"Opening {raw} for you, boss."

    # 2. Check Discord app-* directory wildcard if Discord was requested
    if "discord" in key:
        discord_apps = glob.glob(os.path.join(local_appdata, "Discord", "app-*", "Discord.exe"))
        if discord_apps:
            subprocess.Popen(f'"{discord_apps[-1]}"', shell=True)
            return f"Opening Discord for you, boss."

    # 3. Search Start Menu shortcuts
    start_menu_dirs = [
        os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
    ]
    for sm_dir in start_menu_dirs:
        if os.path.exists(sm_dir):
            for lnk in glob.glob(os.path.join(sm_dir, "**", "*.lnk"), recursive=True):
                lnk_name = os.path.basename(lnk).lower().replace(".lnk", "")
                if key in lnk_name or lnk_name in key:
                    try:
                        os.startfile(lnk)
                        return f"Opening {raw} for you, boss."
                    except Exception:
                        pass

    # 4. Fallback: try direct startfile or shell command
    try:
        os.startfile(raw)
        return f"Opening {raw} for you, boss."
    except Exception:
        pass

    try:
        subprocess.Popen(raw, shell=True)
        return f"Opening {raw} for you, boss."
    except Exception as e:
        return f"Could not launch '{raw}': {e}"


def _close_windows_app(app_name: str) -> str:
    """Close/terminate an application by name on Windows."""
    raw = app_name.strip()
    key = raw.lower()

    if not key or len(key) < 2:
        return "Which application would you like me to close, boss?"

    proc_map: dict[str, list[str]] = {
        "discord": ["Discord.exe"],
        "spotify": ["Spotify.exe"],
        "chrome": ["chrome.exe"],
        "google chrome": ["chrome.exe"],
        "firefox": ["firefox.exe"],
        "edge": ["msedge.exe"],
        "microsoft edge": ["msedge.exe"],
        "vs code": ["Code.exe"],
        "vscode": ["Code.exe"],
        "code": ["Code.exe"],
        "visual studio code": ["Code.exe"],
        "notepad": ["notepad.exe", "Notepad.exe"],
        "calculator": ["CalculatorApp.exe", "calc.exe"],
        "calc": ["CalculatorApp.exe", "calc.exe"],
        "terminal": ["WindowsTerminal.exe", "wt.exe"],
        "windows terminal": ["WindowsTerminal.exe"],
        "powershell": ["powershell.exe"],
        "cmd": ["cmd.exe"],
        "task manager": ["Taskmgr.exe", "taskmgr.exe"],
        "vlc": ["vlc.exe"],
        "steam": ["steam.exe", "Steam.exe"],
        "obsidian": ["Obsidian.exe"],
        "telegram": ["Telegram.exe"],
    }

    try:
        tasklist_out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False).stdout.lower()
    except Exception:
        tasklist_out = ""

    targets = proc_map.get(key, [f"{key}.exe", key])
    killed = False
    for target in targets:
        target_clean = target if target.endswith(".exe") else f"{target}.exe"
        if target_clean.lower() in tasklist_out or not tasklist_out:
            res = subprocess.run(
                ["taskkill", "/F", "/IM", target_clean],
                capture_output=True,
                text=True,
                check=False
            )
            if res.returncode == 0:
                killed = True

    if killed:
        return f"Closed {raw} for you, boss."

    # Search running processes in tasklist matching key
    if tasklist_out:
        matched = []
        for line in tasklist_out.splitlines():
            parts = line.split(",")
            if parts:
                pname = parts[0].strip('"').lower()
                if key in pname:
                    matched.append(pname)
        if matched:
            for m in set(matched):
                subprocess.run(["taskkill", "/F", "/IM", m], capture_output=True, text=True, check=False)
            return f"Closed {raw} for you, boss."

    return f"I couldn't find '{raw}' running on your system, boss."



def _fetch_url(url: str) -> str:
    """Fetch clean, readable text from a URL."""
    try:
        u = url.strip()
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        resp = httpx.get(u, timeout=8, headers=HEADERS, follow_redirects=True)
        if resp.status_code >= 400:
            return f"Could not access page (HTTP {resp.status_code}), boss."
        text = re.sub(r"<script.*?</script>", "", resp.text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = " ".join(text.split())
        return text[:3000] if text else "The webpage appears to have no readable text, boss."
    except Exception as e:
        return f"Could not fetch that website: {e}"


def _search_web(query: str) -> str:
    """Search DuckDuckGo safely without throwing errors."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query.strip(), max_results=5))
        if not results:
            return f"No search results found for '{query}', boss."
        lines = []
        for r in results:
            title = r.get("title", "No title")
            body = r.get("body", "")
            href = r.get("href", "")
            lines.append(f"**{title}**\n{body}\nLink: {href}\n")
        return "\n".join(lines).strip()
    except Exception as e:
        return f"Search is temporarily unavailable: {e}"


def _execute_tool(name: str, args: dict) -> str:
    """Execute a tool by name with given arguments. Returns result string."""
    try:
        if name == "search_web":
            return _search_web(args.get("query", ""))

        elif name == "fetch_url":
            return _fetch_url(args.get("url", ""))

        elif name == "get_current_time":
            return datetime.now(timezone.utc).isoformat()

        elif name == "get_system_info":
            return json.dumps({
                "os": platform.system(),
                "os_version": platform.version(),
                "machine": platform.machine(),
                "python_version": platform.python_version(),
            })

        elif name == "launch_app":
            return _launch_windows_app(args.get("app_name", ""))

        elif name == "close_app":
            return _close_windows_app(args.get("app_name", ""))

        elif name == "control_media":
            return _control_media(args.get("action", "play_pause"))

        elif name == "play_spotify":
            query = args.get("query", "").strip()
            if not query or query.lower() in ("play", "resume", "music", "unpause"):
                _send_virtual_key(VK_MEDIA_PLAY_PAUSE)
                return "Toggled playback, boss."
            _run_uri(f"spotify:search:{urllib.parse.quote(query)}")
            time.sleep(0.5)
            _send_virtual_key(VK_MEDIA_PLAY_PAUSE)
            return f"Playing '{query}' on Spotify, boss."


        elif name == "play_youtube":
            query = args.get("query", "").strip()
            encoded = urllib.parse.quote(query)
            webbrowser.open(f"https://www.youtube.com/results?search_query={encoded}")
            return f"Pulling up '{query}' on YouTube for you, boss."

        elif name == "show_code_in_terminal":
            code = args.get("code", "")
            language = args.get("language", "python")
            title = args.get("title", "Claire Output")
            out_dir = Path(tempfile.gettempdir()) / "claire_output"
            out_dir.mkdir(exist_ok=True)
            ext_map = {"python": "py", "javascript": "js", "typescript": "ts",
                        "html": "html", "css": "css", "json": "json", "text": "txt"}
            ext = ext_map.get(language.lower(), "txt")
            out_file = out_dir / f"output.{ext}"
            out_file.write_text(code, encoding="utf-8")
            ps_cmd = (
                f"Write-Host '{'='*60}' -ForegroundColor DarkCyan; "
                f"Write-Host '  {title}  [{language.upper()}]' -ForegroundColor Cyan; "
                f"Write-Host '{'='*60}' -ForegroundColor DarkCyan; "
                f"Get-Content '{out_file}'"
            )
            subprocess.Popen(["powershell.exe", "-NoExit", "-Command", ps_cmd],
                            creationflags=subprocess.CREATE_NEW_CONSOLE)
            return "Opening a terminal window with the code now, boss."

        else:
            return f"Unknown tool: {name}"

    except Exception as e:
        return f"Tool '{name}' failed: {e}"



# ── Pipeline ──────────────────────────────────────────────────────────────

# ── Regex to detect raw function-call XML that LLMs sometimes emit as text ─
_FUNC_XML_RE = re.compile(
    r"<function=([\w]+)>\s*(\{.*?\})\s*</function>",
    re.DOTALL,
)


def _strip_function_xml(text: str) -> str:
    """Remove raw <function=name>{...}</function> markup from LLM output.
    Returns only the natural-language portion of the text."""
    cleaned = _FUNC_XML_RE.sub("", text).strip()
    # Also strip any leftover angle-bracket fragments
    cleaned = re.sub(r"</?function[^>]*>", "", cleaned).strip()
    return cleaned


def _extract_inline_tool_calls(text: str) -> list[tuple[str, dict]]:
    """Parse any <function=name>{args}</function> blocks in the text.
    Returns list of (function_name, args_dict)."""
    calls: list[tuple[str, dict]] = []
    for m in _FUNC_XML_RE.finditer(text):
        fn_name = m.group(1)
        try:
            fn_args = json.loads(m.group(2))
        except json.JSONDecodeError:
            fn_args = {}
        calls.append((fn_name, fn_args))
    return calls


class DirectPipeline:
    """
    Direct local voice pipeline:
      Mic → energy VAD → Groq Whisper STT → Groq LLM (+ tool calling) → KittenTTS → Speaker

    No LiveKit, no WebRTC, no browser.  Just your mic and speaker.
    """

    def __init__(
        self,
        *,
        groq_api_key: str,
        system_prompt: str,
        tts_model: str = "KittenML/kitten-tts-mini-0.8",
        tts_voice: str = "Luna",
        tts_speed: float = 1.20,
        llm_model: str = "openai/gpt-oss-20b",
        emit: Callable[[str, str | None], None] | None = None,
    ):

        self._groq_key = groq_api_key
        self._system_prompt = system_prompt
        self._llm_model = llm_model
        self._tts_voice = tts_voice
        self._tts_speed = tts_speed
        self._emit = emit or (lambda k, v: None)


        # Initialize KittenTTS
        logger.info("Loading KittenTTS model (%s)…", tts_model)
        import contextlib
        f_init = io.StringIO()
        with contextlib.redirect_stdout(f_init), contextlib.redirect_stderr(f_init):
            self._tts = KittenTTS(tts_model)
        logger.info("KittenTTS model loaded. Voice: %s", self._tts_voice)


        # State
        self._running = False
        self._muted = False
        self._chat_history: list[dict] = []
        self._speaking = False         # True while TTS audio is playing
        self._tts_event = threading.Event()
        self._interrupt_event = threading.Event()
        self._processing_lock = threading.Lock()
        self._last_interrupt = 0.0     # time of last interruption (seconds)
        self._last_tts_end = 0.0


    # ── Public API ─────────────────────────────────────────────────────

    def start(self):
        """Start the pipeline in a background daemon thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="claire-pipeline")
        self._thread.start()
        logger.info("Pipeline started.")

    def stop(self):
        """Gracefully stop the pipeline."""
        self._running = False
        self._interrupt_event.set()
        sd.stop()
        logger.info("Pipeline stopping…")

    def set_muted(self, muted: bool):
        self._muted = muted

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Main Loop ──────────────────────────────────────────────────────

    def _run_loop(self):
        """
        Core audio loop.  Runs in a dedicated thread.
        Captures mic audio via energy-based VAD, then sends complete
        utterances to Groq Whisper for accurate STT.
        """
        is_speaking = False
        speech_start: float | None = None
        silence_start: float | None = None
        audio_q: queue.Queue[np.ndarray] = queue.Queue()
        speech_chunks: list[np.ndarray] = []   # accumulate speech audio

        def _audio_cb(indata, frames, time_info, status):
            if status:
                logger.debug("Audio status: %s", status)
            audio_q.put(indata[:, 0].copy())   # mono int16

        self._emit("state", "idle")

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=BLOCK_SIZE,
                callback=_audio_cb,
            ):
                logger.info("Mic stream opened.")
                while self._running:
                    # ── Get audio chunk
                    try:
                        chunk = audio_q.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    # Post-interrupt mute: ignore audio for 2.0 s after interruption
                    if time.time() - self._last_interrupt < 2.0:
                        continue

                    # Post-playback mute: ignore audio for 1.0 s after TTS
                    if time.time() - self._last_tts_end < 1.0:
                        continue

                    # Skip VAD while TTS is playing (prevent self-listening)
                    if self._tts_event.is_set() or self._speaking:
                        continue

                    if self._muted:
                        continue

                    # ── Energy-based VAD
                    rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))

                    if rms > ENERGY_THRESHOLD:
                        if not is_speaking:
                            is_speaking = True
                            speech_start = time.monotonic()
                            speech_chunks.clear()
                            self._emit("state", "listening")
                        speech_chunks.append(chunk)
                        silence_start = None
                    elif is_speaking:
                        # Still accumulate trailing audio for context
                        speech_chunks.append(chunk)
                        if silence_start is None:
                            silence_start = time.monotonic()

                    # ── Silence timeout: finalize utterance and send to Whisper
                    if (is_speaking
                            and silence_start is not None
                            and (time.monotonic() - silence_start) > SILENCE_TIMEOUT_S):
                        duration = time.monotonic() - (speech_start or 0)
                        if duration >= SPEECH_MIN_LEN and speech_chunks:
                            # Combine all speech chunks into one audio buffer
                            full_audio = np.concatenate(speech_chunks)
                            text = self._whisper_transcribe(full_audio)
                            if text:
                                self._trigger_utterance(text)
                        is_speaking = False
                        silence_start = None
                        speech_chunks.clear()

        except Exception as e:
            logger.exception("Pipeline loop crashed: %s", e)
        finally:
            self._emit("state", "idle")
            logger.info("Pipeline loop exited.")

    # ── Groq Whisper STT ──────────────────────────────────────────────

    def _whisper_transcribe(self, audio: np.ndarray) -> str:
        """Send an int16 PCM buffer to Groq Whisper and return the text."""
        try:
            # Convert int16 numpy array to a WAV file in memory
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)  # int16 = 2 bytes
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio.tobytes())
            buf.seek(0)

            resp = httpx.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._groq_key}"},
                data={"model": "whisper-large-v3", "language": "en"},
                files={"file": ("audio.wav", buf, "audio/wav")},
                timeout=10.0,
            )
            resp.raise_for_status()
            text = resp.json().get("text", "").strip()
            if not text or text.lower().strip() in _WHISPER_HALLUCINATIONS:
                return ""
            _print_terminal("whisper", text)
            return text
        except Exception as e:
            logger.exception("Whisper STT error: %s", e)
            _print_terminal("error", f"STT failed: {e}")
            return ""


    # ── Process Utterance ──────────────────────────────────────────────

    def _trigger_utterance(self, text: str):
        """Spawns processing in a thread so the mic loop keeps running."""
        self._emit("user_transcript", text)
        _print_terminal("user", text)
        self._interrupt_event.set()  # Cancel any ongoing processing
        sd.stop()
        threading.Thread(target=self._process_utterance_thread, args=(text,), daemon=True).start()

    def _process_utterance_thread(self, text: str):
        """Send user speech to Groq LLM, handle tool calls, speak response."""
        # Ensure only one processing thread runs at a time
        with self._processing_lock:
            self._interrupt_event.clear()
            self._chat_history.append({"role": "user", "content": text})
            self._emit("state", "thinking")
            _print_terminal("state", "Thinking…")

            try:
                response_text = self._llm_with_tools()
                if self._interrupt_event.is_set():
                    return

                if response_text:
                    # ── Strip any raw function-call XML the LLM may have emitted
                    inline_calls = _extract_inline_tool_calls(response_text)
                    clean_text = _strip_function_xml(response_text)

                    # Execute any inline tool calls the LLM embedded as text
                    for fn_name, fn_args in inline_calls:
                        _print_terminal("tool", f"{fn_name}({fn_args})")
                        _execute_tool(fn_name, fn_args)

                    if clean_text:
                        self._chat_history.append({"role": "assistant", "content": clean_text})
                        self._emit("state", "speaking")
                        self._emit("agent_transcript", clean_text)
                        _print_terminal("claire", clean_text)
                        self._speak(clean_text)
                    elif inline_calls:
                        # Tool was called but no spoken text — add silent ack
                        ack = "Done, boss."
                        self._chat_history.append({"role": "assistant", "content": ack})
                        self._emit("state", "speaking")
                        self._emit("agent_transcript", ack)
                        _print_terminal("claire", ack)
                        self._speak(ack)
            except Exception as e:
                if self._interrupt_event.is_set():
                    return
                logger.exception("LLM/TTS error: %s", e)
                _print_terminal("error", str(e))
                fallback = "Something went wrong on my end, boss. Give me a sec."
                self._emit("state", "speaking")
                self._emit("agent_transcript", fallback)
                _print_terminal("claire", fallback)
                self._speak(fallback)

            if not self._interrupt_event.is_set() and self._running:
                self._emit("state", "idle")
                _print_terminal("state", "Standing by…")

    # ── LLM with Tool Calling ─────────────────────────────────────────

    def _sanitize_history(self, messages: list[dict]) -> list[dict]:
        """Ensure tool messages always follow an assistant message with
        matching tool_call_id.  Orphaned tool messages cause 400 errors."""
        clean: list[dict] = []
        for msg in messages:
            if msg.get("role") == "tool":
                # Only keep if preceding message is assistant with tool_calls
                if clean and clean[-1].get("role") == "assistant" and clean[-1].get("tool_calls"):
                    clean.append(msg)
                elif clean and clean[-1].get("role") == "tool":
                    # consecutive tool results (multi-tool call) — keep
                    clean.append(msg)
                else:
                    # orphaned tool message — skip it
                    logger.debug("Dropped orphaned tool message: %s", msg.get("tool_call_id"))
            else:
                clean.append(msg)
        return clean

    def _llm_with_tools(self, depth: int = 0) -> str:
        """
        Call Groq LLM.  If it returns tool_calls, execute them and
        feed the results back (up to 3 rounds of tool calling).
        Includes retry with backoff for 429 rate limits.
        """
        if depth > 3 or self._interrupt_event.is_set():
            return ""

        messages = [{"role": "system", "content": self._system_prompt}]
        history_slice = self._sanitize_history(self._chat_history[-20:])
        messages.extend(history_slice)

        payload: dict = {
            "model": self._llm_model,
            "messages": messages,
            "max_tokens": 400,
            "temperature": 0.7,
            "tools": TOOL_SCHEMAS,
            "tool_choice": "auto",
        }

        # Retry with exponential backoff for rate limits
        max_retries = 3
        for attempt in range(max_retries + 1):
            resp = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._groq_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30.0,
            )
            if resp.status_code == 429 and attempt < max_retries:
                wait = 2 ** attempt  # 1s, 2s, 4s
                _print_terminal("error", f"Rate limited — retrying in {wait}s…")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break

        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        if self._interrupt_event.is_set():
            return ""

        # ── Check for tool calls
        tool_calls = message.get("tool_calls")
        if tool_calls:
            self._chat_history.append(message)

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"].get("arguments", "{}"))
                _print_terminal("tool", f"{fn_name}({fn_args})")

                result = _execute_tool(fn_name, fn_args)

                self._chat_history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

            return self._llm_with_tools(depth=depth + 1)

        return message.get("content", "").strip()

    # ── TTS + Playback ─────────────────────────────────────────────────

    def _speak(self, text: str):
        """Play synthesized speech with playback guard.
        The guard prevents the mic loop from processing audio while the TTS
        output is active, eliminating self-listening.
        """
        if not text or not text.strip():
            return

        # Mark playback start
        self._tts_event.set()
        self._speaking = True
        try:
            import contextlib
            f_out = io.StringIO()
            with contextlib.redirect_stdout(f_out), contextlib.redirect_stderr(f_out):
                samples = self._tts.generate(
                    text,
                    voice=self._tts_voice,
                    speed=self._tts_speed,
                )

            if self._interrupt_event.is_set() or samples is None or len(samples) == 0:
                return

            samples = np.ascontiguousarray(samples, dtype=np.float32)
            sr = TTS_SAMPLE_RATE

            sd.play(samples, sr)

            # Poll for completion so we can break early if interrupted
            while sd.get_stream() is not None and sd.get_stream().active:
                if self._interrupt_event.is_set():
                    sd.stop()
                    break
                time.sleep(0.04)

        except Exception as e:
            logger.exception("TTS playback error: %s", e)
        finally:
            self._speaking = False
            self._tts_event.clear()
            self._last_tts_end = time.time()


