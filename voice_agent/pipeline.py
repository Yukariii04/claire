"""
Claire Voice Agent — Direct Local Pipeline  (no LiveKit)
Pipeline: sounddevice mic → Groq Whisper STT → Groq LLM (with tools) → Kokoro TTS → sounddevice speaker

This replaces the entire LiveKit agent framework with a lightweight,
direct audio pipeline.  Everything runs locally except the Groq LLM/STT calls.
"""

from __future__ import annotations

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
import wave
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
import numpy as np
import sounddevice as sd
from kokoro_onnx import Kokoro

logger = logging.getLogger("claire.pipeline")

# ── Audio Constants ────────────────────────────────────────────────────────
SAMPLE_RATE     = 16000       # 16 kHz mono for Whisper
CHANNELS        = 1
BLOCK_SIZE      = 4000        # 250 ms chunks
TTS_SAMPLE_RATE = 24000       # Kokoro outputs 24 kHz

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
            "name": "get_world_news",
            "description": "Fetch the latest world news headlines from BBC, CNBC, NYT, and Al Jazeera.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },

    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web using DuckDuckGo and return the top 5 results.",
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
            "description": "Fetch the raw text content of any URL (max 4000 chars).",
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
            "description": "Return the current date and time in ISO 8601 format (UTC).",
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
            "description": "Open any installed Windows application by name. Examples: 'Spotify', 'VS Code', 'Chrome', 'Calculator', 'Discord'.",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string", "description": "Name of the application"}},
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_spotify",
            "description": "Search for and play a song, artist, or playlist on the native Spotify app.",
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
            "description": "Search for and play a video on the YouTube Windows app.",
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


# ── Tool Implementations (inline — no MCP server needed) ──────────────────

HEADERS = {"User-Agent": "Claire-AI/1.0"}

WORLD_NEWS_FEEDS = [
    ("BBC",       "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("CNBC",      "https://www.cnbc.com/id/100727362/device/rss/rss.html"),
    ("NYT",       "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("AlJazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
]

FINANCE_NEWS_FEEDS = [
    ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("Bloomberg",    "https://feeds.bloomberg.com/markets/news.rss"),
    ("Reuters",      "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best"),
    ("MarketWatch",  "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("NYT Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
]

APP_MAP = {
    "chrome": "chrome.exe", "google chrome": "chrome.exe",
    "firefox": "firefox.exe", "edge": "msedge.exe", "microsoft edge": "msedge.exe",
    "vs code": "code", "vscode": "code", "visual studio code": "code",
    "notepad": "notepad.exe", "terminal": "wt.exe", "windows terminal": "wt.exe",
    "powershell": "powershell.exe", "cmd": "cmd.exe",
    "spotify": "spotify.exe", "discord": "discord.exe", "vlc": "vlc.exe",
    "calculator": "calc.exe", "file explorer": "explorer.exe", "explorer": "explorer.exe",
    "task manager": "taskmgr.exe", "settings": "ms-settings:",
    "paint": "mspaint.exe", "word": "winword.exe", "excel": "excel.exe",
}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _fetch_feed_sync(source: str, url: str) -> list[dict]:
    """Fetch a single RSS feed synchronously. Returns up to 5 items."""
    try:
        resp = httpx.get(url, timeout=5, headers=HEADERS, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        items = []
        for item in root.iter("item"):
            title = _strip_html(getattr(item.find("title"), "text", "") or "")
            desc  = _strip_html(getattr(item.find("description"), "text", "") or "")
            link  = (getattr(item.find("link"), "text", "") or "").strip()
            if title:
                items.append({"source": source, "title": title, "desc": desc[:200], "link": link})
            if len(items) >= 5:
                break
        return items
    except Exception:
        return []


def _fetch_news(feeds: list[tuple[str, str]], limit: int = 12) -> str:
    """Fetch multiple RSS feeds using threads for parallelism."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(feeds)) as pool:
        futures = [pool.submit(_fetch_feed_sync, src, url) for src, url in feeds]
        all_items = []
        for f in futures:
            all_items.extend(f.result())
    if not all_items:
        return ""
    lines = []
    for it in all_items[:limit]:
        lines.append(f"**[{it['source']}]** {it['title']}")
        if it["desc"]:
            lines.append(f"{it['desc']}...")
        if it["link"]:
            lines.append(f"Link: {it['link']}")
        lines.append("")
    return "\n".join(lines).strip()


def _run_uri(uri: str) -> None:
    subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)


def _execute_tool(name: str, args: dict) -> str:
    """Execute a tool by name with given arguments. Returns result string."""
    try:
        if name == "get_world_news":
            result = _fetch_news(WORLD_NEWS_FEEDS)
            return result or "The global news grid is unresponsive, sir."

        elif name == "get_world_finance_news":
            result = _fetch_news(FINANCE_NEWS_FEEDS)
            return result or "The financial feeds are unresponsive right now, sir."

        elif name == "open_world_monitor":
            webbrowser.open("https://worldmonitor.app/")
            return "Displaying the World Monitor on your primary screen now, sir."

        elif name == "open_finance_world_monitor":
            webbrowser.open("https://finance.worldmonitor.app/")
            return "Displaying the Finance World Monitor on your primary screen now, sir."

        elif name == "search_web":
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(args.get("query", ""), max_results=5))
            if not results:
                return "Search is offline right now, boss."
            lines = []
            for r in results:
                lines.append(f"**{r.get('title', 'No title')}**")
                lines.append(r.get("body", ""))
                lines.append(f"Link: {r.get('href', '')}")
                lines.append("")
            return "\n".join(lines).strip()

        elif name == "fetch_url":
            resp = httpx.get(args.get("url", ""), timeout=10, headers=HEADERS, follow_redirects=True)
            resp.raise_for_status()
            text = re.sub(r"<[^>]+>", "", resp.text)
            return text[:4000]

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
            app_name = args.get("app_name", "")
            key = app_name.lower().strip()
            exe = APP_MAP.get(key)
            if exe:
                if exe.endswith(":"):
                    _run_uri(exe)
                else:
                    subprocess.Popen(exe, shell=True)
            else:
                os.startfile(app_name)
            return f"Opening {app_name} for you, boss."

        elif name == "play_spotify":
            query = args.get("query", "")
            _run_uri(f"spotify:search:{urllib.parse.quote(query)}")
            return f"Queuing up {query} on Spotify, boss."

        elif name == "play_youtube":
            query = args.get("query", "")
            encoded = urllib.parse.quote(query)
            _run_uri(f"youtube://www.youtube.com/results?search_query={encoded}")
            return f"Pulling up {query} on YouTube for you, boss."

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
      Mic → energy VAD → Groq Whisper STT → Groq LLM (+ tool calling) → Kokoro TTS → Speaker

    No LiveKit, no WebRTC, no browser.  Just your mic and speaker.
    """

    def __init__(
        self,
        *,
        groq_api_key: str,
        system_prompt: str,
        kokoro_model: str = "kokoro-v1_0.onnx",
        voices_model: str = "voices-v1_0.bin",
        tts_voice: str = "af_heart",
        tts_speed: float = 1.15,
        llm_model: str = "llama-3.1-8b-instant",
        emit: Callable[[str, str | None], None] | None = None,
    ):
        self._groq_key = groq_api_key
        self._system_prompt = system_prompt
        self._llm_model = llm_model
        self._tts_voice = tts_voice
        self._tts_speed = tts_speed
        self._emit = emit or (lambda k, v: None)

        # No Vosk model needed — we use Groq Whisper for STT
        logger.info("Loading Kokoro TTS model…")
        self._kokoro = Kokoro(kokoro_model, voices_model)
        logger.info("Kokoro model loaded.")

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
        # Mark playback start
        self._tts_event.set()
        self._speaking = True
        try:
            samples, sr = self._kokoro.create(
                text,
                voice=self._tts_voice,
                speed=self._tts_speed,
                lang="en-us",
            )

            if self._interrupt_event.is_set():
                return

            sd.play(samples, sr)

            # Poll for completion so we can break early if interrupted
            while sd.get_stream() is not None and sd.get_stream().active:
                if self._interrupt_event.is_set():
                    sd.stop()
                    break
                time.sleep(0.05)

        except Exception as e:
            logger.exception("TTS playback error: %s", e)
        finally:
            self._speaking = False
            self._tts_event.clear()
            self._last_tts_end = time.time()
