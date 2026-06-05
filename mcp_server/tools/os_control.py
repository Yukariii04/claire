"""
claire MCP Tools — OS Control
Tools: launch_app, play_spotify, play_youtube, show_code_in_terminal

All tools interact with the Windows operating system directly via subprocess
and URI schemes. No external API keys required.
"""

import os
import subprocess
import tempfile
import urllib.parse
from pathlib import Path


# ── App name → executable map ──────────────────────────────────────────────
# Keys are lowercase, stripped variants of what the user might say.
APP_MAP: dict[str, str] = {
    # Browsers
    "chrome":           "chrome.exe",
    "google chrome":    "chrome.exe",
    "firefox":          "firefox.exe",
    "edge":             "msedge.exe",
    "microsoft edge":   "msedge.exe",
    # Dev tools
    "vs code":          "code",
    "vscode":           "code",
    "visual studio code": "code",
    "notepad":          "notepad.exe",
    "notepad++":        "notepad++.exe",
    "terminal":         "wt.exe",          # Windows Terminal
    "windows terminal": "wt.exe",
    "powershell":       "powershell.exe",
    "cmd":              "cmd.exe",
    # Media & communication
    "spotify":          "spotify.exe",
    "discord":          "discord.exe",
    "vlc":              "vlc.exe",
    # System
    "calculator":       "calc.exe",
    "file explorer":    "explorer.exe",
    "explorer":         "explorer.exe",
    "task manager":     "taskmgr.exe",
    "control panel":    "control.exe",
    "settings":         "ms-settings:",    # URI scheme
    # Creative
    "paint":            "mspaint.exe",
    "word":             "winword.exe",
    "excel":            "excel.exe",
    "powerpoint":       "powerpnt.exe",
}


def _run_uri(uri: str) -> None:
    """Open a URI scheme on Windows via cmd /c start."""
    subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)


def register(mcp):

    @mcp.tool()
    async def launch_app(app_name: str) -> str:
        """
        Open any installed Windows application by name.
        Examples: 'Spotify', 'VS Code', 'Chrome', 'Calculator', 'Discord'.
        """
        key = app_name.lower().strip()
        exe = APP_MAP.get(key)

        try:
            if exe:
                if exe.endswith(":"):
                    # It's a URI scheme (e.g. ms-settings:)
                    _run_uri(exe)
                else:
                    subprocess.Popen(exe, shell=True)
            else:
                # Last resort — try os.startfile with the raw name
                os.startfile(app_name)
            return f"Opening {app_name} for you, boss."
        except Exception as e:
            return f"Couldn't find {app_name} on your system, boss. ({e})"

    @mcp.tool()
    async def play_spotify(query: str) -> str:
        """
        Search for and play a song, artist, or playlist on the native Spotify app.
        Example: 'Blinding Lights by The Weeknd' or 'lofi hip hop'.
        """
        try:
            encoded = urllib.parse.quote(query)
            uri = f"spotify:search:{encoded}"
            _run_uri(uri)
            return f"Queuing up {query} on Spotify, boss."
        except Exception as e:
            return f"Spotify doesn't seem to be installed, boss. ({e})"

    @mcp.tool()
    async def play_youtube(query: str) -> str:
        """
        Search for and play a video on the YouTube Windows app (Microsoft Store).
        Example: 'lofi hip hop radio' or 'how to make pasta'.
        """
        encoded = urllib.parse.quote(query)
        primary_uri  = f"youtube://www.youtube.com/results?search_query={encoded}"
        fallback_uri = (
            f"ms-xboxliveapp://4DF9E0F3-5172-4358-AF45-40CE8C4AD35A"
            f"?LaunchUri=https://www.youtube.com/results?search_query={encoded}"
        )
        try:
            _run_uri(primary_uri)
            return f"Pulling up {query} on YouTube for you, boss."
        except Exception:
            try:
                _run_uri(fallback_uri)
                return f"Pulling up {query} on YouTube for you, boss."
            except Exception as e:
                return f"Couldn't launch the YouTube app, boss. ({e})"

    @mcp.tool()
    async def show_code_in_terminal(
        code: str,
        language: str = "python",
        title: str = "Claire Output",
    ) -> str:
        """
        Display code or text in a new, persistent PowerShell terminal window.
        Use this whenever you want to show a code answer visually instead of speaking it.
        """
        try:
            # Write to a temp file so the terminal can display it
            out_dir = Path(tempfile.gettempdir()) / "claire_output"
            out_dir.mkdir(exist_ok=True)

            ext_map = {
                "python": "py", "javascript": "js", "typescript": "ts",
                "html": "html", "css": "css", "bash": "sh",
                "powershell": "ps1", "json": "json", "yaml": "yml",
                "text": "txt",
            }
            ext = ext_map.get(language.lower(), "txt")
            out_file = out_dir / f"output.{ext}"
            out_file.write_text(code, encoding="utf-8")

            ps_cmd = (
                f"Write-Host '{'='*60}' -ForegroundColor DarkCyan; "
                f"Write-Host '  {title}  [{language.upper()}]' -ForegroundColor Cyan; "
                f"Write-Host '{'='*60}' -ForegroundColor DarkCyan; "
                f"Get-Content '{out_file}'; "
                f"Write-Host ''; "
                f"Write-Host 'Press Ctrl+C or close this window when done.' -ForegroundColor DarkGray"
            )
            subprocess.Popen(
                ["powershell.exe", "-NoExit", "-Command", ps_cmd],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            return "Opening a terminal window with the code now, boss."
        except Exception as e:
            return f"Couldn't open the terminal window, boss. ({e})"
