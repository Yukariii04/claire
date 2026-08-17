"""
Claire Voice Agent — Dynamic Island Overlay  v2
A frameless, always-on-top, pill-shaped floating UI inspired by Apple's Dynamic Island.
Shows agent state, live transcripts, and session controls.

v2 — Premium animated expand/collapse with Canvas-drawn 3D chevron,
    hover glow, buttery-smooth spring-physics animations, and
    the entire pill is clickable to expand.

Runs on the main thread (tkinter requirement) and communicates with the
voice agent via a thread-safe queue.
"""

from __future__ import annotations

import math
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageOps, ImageTk

# ── Constants ──────────────────────────────────────────────────────────────

_ASSETS_DIR      = Path(__file__).parent / "assets"
_AVATAR_PATH     = _ASSETS_DIR / "avatar.png"
_TRANSPARENT_COLOR = "#000001"


# Palette — deep dark with accent glow
_BG_PILL         = "#0A0A0F"
_BG_EXPANDED     = "#0E0E14"
_BG_SURFACE      = "#14141E"    # cards / button bg
_ACCENT_LISTEN   = "#34D399"    # emerald
_ACCENT_THINK    = "#FBBF24"    # amber
_ACCENT_SPEAK    = "#818CF8"    # indigo
_ACCENT_IDLE     = "#6B7280"    # gray
_ACCENT_MUTED    = "#EF4444"    # red
_TEXT_PRIMARY    = "#F0F0F5"
_TEXT_SECONDARY  = "#8B8B9E"
_TEXT_DIMMED      = "#4B4B5E"
_BORDER_COLOR    = "#1A1A28"
_BORDER_GLOW     = "#2A2A40"

# Sizing
_PILL_W          = 360
_PILL_H          = 56
_EXPANDED_W      = 440
_EXPANDED_H      = 340
_CORNER_RADIUS   = 28

# Animation tuning — silky smooth
_ANIM_DURATION_MS = 420         # total expand/collapse time
_ANIM_TICK_MS     = 10          # ~100 fps

# Chevron canvas
_CHEV_SIZE        = 34           # canvas dimension



# ── Data Types ─────────────────────────────────────────────────────────────

class AgentState(Enum):
    IDLE      = "idle"
    LISTENING = "listening"
    THINKING  = "thinking"
    SPEAKING  = "speaking"


@dataclass
class OverlayEvent:
    """Thread-safe message from the voice agent to the overlay."""
    kind: str            # "state" | "user_transcript" | "agent_transcript" | "close"
    value: str | float | None = None


# ── Easing helpers ─────────────────────────────────────────────────────────

def _ease_out_quart(t: float) -> float:
    """Smooth ease-out quartic — fast start, silky deceleration, no bounce."""
    t = min(1.0, max(0.0, t))
    t1 = 1.0 - t
    return 1.0 - t1 * t1 * t1 * t1


def _ease_out_quint(t: float) -> float:
    """Even smoother ease-out quintic — used for chevron rotation."""
    t = min(1.0, max(0.0, t))
    t1 = 1.0 - t
    return 1.0 - t1 * t1 * t1 * t1 * t1


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ── Overlay Window ─────────────────────────────────────────────────────────

class ClaireOverlay:
    """
    Dynamic Island overlay.
    Call `run()` on the main thread (blocking).
    Push OverlayEvents into `event_queue` from any thread.
    """

    def __init__(
        self,
        event_queue: queue.Queue[OverlayEvent],
        on_mute_toggle: Callable[[], None] | None = None,
        on_end_session: Callable[[], None] | None = None,
    ):
        self._eq = event_queue
        self._on_mute = on_mute_toggle
        self._on_end = on_end_session

        # State
        self._state       = AgentState.IDLE
        self._expanded    = False
        self._muted       = False
        self._animating   = False
        self._user_text   = ""
        self._agent_text  = ""
        self._orb_phase   = 0.0
        self._chev_angle  = 0.0        # 0 = pointing down, 180 = pointing up
        self._chev_hover  = False
        self._chev_glow   = 0.0        # 0..1 glow intensity (animated)
        self._drag_data   = {"x": 0, "y": 0}
        self._border_glow_phase = 0.0  # animated border glow
        self._pill_avatar_tk: ImageTk.PhotoImage | None = None
        self._exp_avatar_tk: ImageTk.PhotoImage | None = None

    # ── Public ─────────────────────────────────────────────────────────

    def _load_avatar(self, size: int) -> ImageTk.PhotoImage | None:
        """Load and circular-crop the avatar image with anti-aliasing."""
        try:
            if _AVATAR_PATH.exists():
                img = Image.open(_AVATAR_PATH).convert("RGBA")
                mask_size = size * 3
                mask = Image.new("L", (mask_size, mask_size), 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((0, 0, mask_size - 1, mask_size - 1), fill=255)
                
                cropped = ImageOps.fit(img, (mask_size, mask_size), centering=(0.5, 0.5))
                cropped.putalpha(mask)
                resized = cropped.resize((size, size), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(resized)
        except Exception:
            pass
        return None

    def run(self):
        """Start the overlay — blocks, must run on main thread."""
        ctk.set_appearance_mode("dark")

        self._root = ctk.CTk()
        self._root.title("Claire")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.configure(fg_color=_TRANSPARENT_COLOR)
        self._root.attributes("-transparentcolor", _TRANSPARENT_COLOR)

        # Pre-load custom circular avatar images
        self._pill_avatar_tk = self._load_avatar(32)
        self._exp_avatar_tk = self._load_avatar(36)

        # Centre-top of screen
        screen_w = self._root.winfo_screenwidth()
        x = (screen_w - _PILL_W) // 2
        y = 16
        self._root.geometry(f"{_PILL_W}x{_PILL_H}+{x}+{y}")

        self._build_pill()
        self._build_expanded_panel()
        self._show_pill()


        # Start animation loops
        self._root.after(20, self._poll_events)
        self._root.after(20, self._tick_animations)

        self._root.mainloop()

    def close(self):
        try:
            self._root.after(0, self._root.destroy)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    #  BUILD — Collapsed Pill
    # ══════════════════════════════════════════════════════════════════

    def _build_pill(self):
        self._pill_frame = ctk.CTkFrame(
            self._root,
            width=_PILL_W, height=_PILL_H,
            corner_radius=_CORNER_RADIUS,
            fg_color=_BG_PILL,
            bg_color=_TRANSPARENT_COLOR,
            border_width=1,
            border_color=_BORDER_COLOR,
        )
        self._pill_frame.pack_propagate(False)

        inner = ctk.CTkFrame(self._pill_frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=6)

        # ── Avatar (left)
        self._orb_canvas = tk.Canvas(
            inner, width=38, height=38,
            bg=_BG_PILL, highlightthickness=0, bd=0, cursor="hand2",
        )
        self._orb_canvas.pack(side="left", padx=(0, 10))

        # ── Centre text
        center = ctk.CTkFrame(inner, fg_color="transparent")
        center.pack(side="left", fill="both", expand=True)

        self._pill_title = ctk.CTkLabel(
            center, text="Claire",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=_TEXT_PRIMARY, anchor="w",
        )
        self._pill_title.pack(anchor="w")

        self._pill_subtitle = ctk.CTkLabel(
            center, text="Standing by…",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_TEXT_SECONDARY, anchor="w",
        )
        self._pill_subtitle.pack(anchor="w")

        # ── Animated Chevron Button (right) — Canvas-drawn 3D arrow
        self._pill_chev_canvas = tk.Canvas(
            inner, width=_CHEV_SIZE, height=_CHEV_SIZE,
            bg=_BG_PILL, highlightthickness=0, bd=0, cursor="hand2",
        )
        self._pill_chev_canvas.pack(side="right", padx=(8, 0))
        self._pill_chev_canvas.bind("<Enter>", lambda e: self._chev_enter())
        self._pill_chev_canvas.bind("<Leave>", lambda e: self._chev_leave())
        self._pill_chev_canvas.bind("<Button-1>", lambda e: self._toggle_expand())

        # ── Make the whole pill clickable to expand (double-click)
        for w in (self._pill_frame, inner, center, self._pill_title, self._pill_subtitle, self._orb_canvas):
            w.bind("<Double-Button-1>", lambda e: self._toggle_expand())

        # ── Drag
        for w in (self._pill_frame, inner, center, self._pill_title, self._pill_subtitle, self._orb_canvas):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)


    # ══════════════════════════════════════════════════════════════════
    #  BUILD — Expanded Panel
    # ══════════════════════════════════════════════════════════════════

    def _build_expanded_panel(self):
        self._exp_frame = ctk.CTkFrame(
            self._root,
            width=_EXPANDED_W, height=_EXPANDED_H,
            corner_radius=24,
            fg_color=_BG_EXPANDED,
            bg_color=_TRANSPARENT_COLOR,
            border_width=1,
            border_color=_BORDER_COLOR,
        )
        self._exp_frame.pack_propagate(False)

        # ── Header ────────────────────────────────────────────────────
        header = ctk.CTkFrame(self._exp_frame, fg_color="transparent", height=50)
        header.pack(fill="x", padx=20, pady=(16, 4))
        header.pack_propagate(False)

        # ── Avatar (left)
        self._exp_orb_canvas = tk.Canvas(
            header, width=42, height=42,
            bg=_BG_EXPANDED, highlightthickness=0, bd=0, cursor="hand2",
        )
        self._exp_orb_canvas.pack(side="left", padx=(0, 10))

        hdr_text = ctk.CTkFrame(header, fg_color="transparent")
        hdr_text.pack(side="left", fill="both", expand=True)

        self._exp_title = ctk.CTkLabel(
            hdr_text, text="Claire",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=_TEXT_PRIMARY, anchor="w",
        )
        self._exp_title.pack(anchor="w")

        self._exp_subtitle = ctk.CTkLabel(
            hdr_text, text="Standing by…",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_TEXT_SECONDARY, anchor="w",
        )
        self._exp_subtitle.pack(anchor="w")

        # ── Animated Chevron (collapse) in expanded header
        self._exp_chev_canvas = tk.Canvas(
            header, width=_CHEV_SIZE, height=_CHEV_SIZE,
            bg=_BG_EXPANDED, highlightthickness=0, bd=0, cursor="hand2",
        )
        self._exp_chev_canvas.pack(side="right", padx=(8, 0))
        self._exp_chev_canvas.bind("<Enter>", lambda e: self._chev_enter())
        self._exp_chev_canvas.bind("<Leave>", lambda e: self._chev_leave())
        self._exp_chev_canvas.bind("<Button-1>", lambda e: self._toggle_expand())

        # Drag on header
        for w in (header, hdr_text, self._exp_title, self._exp_orb_canvas):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        # ── Divider
        ctk.CTkFrame(self._exp_frame, fg_color=_BORDER_COLOR, height=1
                      ).pack(fill="x", padx=20, pady=(8, 8))


        # ── Transcript area ───────────────────────────────────────────
        transcript = ctk.CTkFrame(self._exp_frame, fg_color="transparent")
        transcript.pack(fill="both", expand=True, padx=20, pady=(0, 4))

        # User
        u_row = ctk.CTkFrame(transcript, fg_color="transparent")
        u_row.pack(fill="x", pady=(4, 2))

        ctk.CTkLabel(
            u_row, text="YOU",
            font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
            text_color=_ACCENT_LISTEN, width=40, anchor="w",
        ).pack(side="left", anchor="n", padx=(0, 8), pady=(2, 0))

        self._user_transcript = ctk.CTkLabel(
            u_row, text="…",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=_TEXT_SECONDARY, anchor="nw", justify="left",
            wraplength=340,
        )
        self._user_transcript.pack(side="left", fill="x", expand=True)

        # Agent
        a_row = ctk.CTkFrame(transcript, fg_color="transparent")
        a_row.pack(fill="x", pady=(10, 2))

        ctk.CTkLabel(
            a_row, text="CLAIRE",
            font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
            text_color=_ACCENT_SPEAK, width=48, anchor="w",
        ).pack(side="left", anchor="n", padx=(0, 0), pady=(2, 0))

        self._agent_transcript = ctk.CTkLabel(
            a_row, text="…",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=_TEXT_PRIMARY, anchor="nw", justify="left",
            wraplength=330,
        )
        self._agent_transcript.pack(side="left", fill="x", expand=True)

        # ── Divider
        ctk.CTkFrame(self._exp_frame, fg_color=_BORDER_COLOR, height=1
                      ).pack(fill="x", padx=20, pady=(6, 8))

        # ── Controls ──────────────────────────────────────────────────
        controls = ctk.CTkFrame(self._exp_frame, fg_color="transparent", height=46)
        controls.pack(fill="x", padx=20, pady=(0, 16))
        controls.pack_propagate(False)

        # Mute
        self._mute_btn = ctk.CTkButton(
            controls, text="🎤", width=42, height=42, corner_radius=21,
            fg_color=_BG_SURFACE, hover_color="#1E1E30",
            font=ctk.CTkFont(size=16), command=self._handle_mute,
        )
        self._mute_btn.pack(side="left", padx=(0, 8))

        self._mute_label = ctk.CTkLabel(
            controls, text="Mic On",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_TEXT_SECONDARY,
        )
        self._mute_label.pack(side="left", padx=(0, 14))

        # Volume
        ctk.CTkLabel(
            controls, text="🔊",
            font=ctk.CTkFont(size=14), text_color=_TEXT_SECONDARY,
        ).pack(side="left", padx=(0, 4))

        self._vol_slider = ctk.CTkSlider(
            controls, from_=0, to=100, number_of_steps=20,
            width=100, height=16,
            fg_color=_BG_SURFACE, progress_color=_ACCENT_SPEAK,
            button_color=_TEXT_PRIMARY, button_hover_color="#FFFFFF",
        )
        self._vol_slider.set(80)
        self._vol_slider.pack(side="left", padx=(0, 14))

        # Spacer
        ctk.CTkFrame(controls, fg_color="transparent").pack(
            side="left", fill="x", expand=True)

        # End
        self._end_btn = ctk.CTkButton(
            controls, text="End", width=72, height=36, corner_radius=18,
            fg_color="#4C0519", hover_color="#6B0724",
            text_color="#FDA4AF",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._handle_end,
        )
        self._end_btn.pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    #  SHOW / HIDE
    # ══════════════════════════════════════════════════════════════════

    def _show_pill(self):
        self._exp_frame.pack_forget()
        self._pill_frame.pack(fill="both", expand=True)

    def _show_expanded(self):
        self._pill_frame.pack_forget()
        self._exp_frame.pack(fill="both", expand=True)

    # ══════════════════════════════════════════════════════════════════
    #  EXPAND / COLLAPSE  — smooth ease-out animation
    # ══════════════════════════════════════════════════════════════════

    def _toggle_expand(self):
        if self._animating:
            return
        if self._expanded:
            self._run_morph(expand=False)
        else:
            self._run_morph(expand=True)

    def _run_morph(self, expand: bool):
        """Animate the window morph using smooth ease-out at ~100 fps."""
        self._animating = True
        self._expanded = expand

        # Immediately show the target frame
        if expand:
            self._show_expanded()
        # (if collapsing, we keep expanded visible until animation ends)

        geo = self._root.geometry()
        parts = geo.split("+")
        w0, h0 = (int(v) for v in parts[0].split("x"))
        x0, y0 = int(parts[1]), int(parts[2])

        if expand:
            w1, h1 = _EXPANDED_W, _EXPANDED_H
            x1 = x0 - (_EXPANDED_W - w0) // 2
        else:
            w1, h1 = _PILL_W, _PILL_H
            x1 = x0 + (w0 - _PILL_W) // 2
        x1 = max(0, x1)
        y1 = y0

        # Chevron angle targets
        chev_from = self._chev_angle
        chev_to = 180.0 if expand else 0.0

        start_time = time.perf_counter()
        total = _ANIM_DURATION_MS / 1000.0

        def _step():
            elapsed = time.perf_counter() - start_time
            t = min(elapsed / total, 1.0)

            # Smooth ease-out for everything — no bounce, no overshoot
            te = _ease_out_quart(t)

            w = int(_lerp(w0, w1, te))
            h = int(_lerp(h0, h1, te))
            x = int(_lerp(x0, x1, te))
            y = int(_lerp(y0, y1, te))

            # Smooth chevron rotation (slightly slower easing for elegance)
            self._chev_angle = _lerp(chev_from, chev_to, _ease_out_quint(t))

            self._root.geometry(f"{w}x{h}+{x}+{y}")

            if t < 1.0:
                self._root.after(_ANIM_TICK_MS, _step)
            else:
                # Finished
                self._root.geometry(f"{w1}x{h1}+{x1}+{y1}")
                self._chev_angle = chev_to
                self._animating = False
                if not expand:
                    self._show_pill()

        _step()

    # ══════════════════════════════════════════════════════════════════
    #  DRAG
    # ══════════════════════════════════════════════════════════════════

    def _start_drag(self, event):
        self._drag_data["x"] = event.x_root
        self._drag_data["y"] = event.y_root

    def _on_drag(self, event):
        dx = event.x_root - self._drag_data["x"]
        dy = event.y_root - self._drag_data["y"]
        geo = self._root.geometry()
        parts = geo.split("+")
        x = int(parts[1]) + dx
        y = int(parts[2]) + dy
        self._root.geometry(f"{parts[0]}+{x}+{y}")
        self._drag_data["x"] = event.x_root
        self._drag_data["y"] = event.y_root

    # ══════════════════════════════════════════════════════════════════
    #  ANIMATION TICK  — master loop drives all visual animations
    # ══════════════════════════════════════════════════════════════════

    def _tick_animations(self):
        dt = 0.025   # ~40 fps budget

        # Orb pulse
        speed_map = {
            AgentState.IDLE: 0.03,
            AgentState.LISTENING: 0.10,
            AgentState.THINKING: 0.14,
            AgentState.SPEAKING: 0.08,
        }
        self._orb_phase += speed_map.get(self._state, 0.05)

        # Chevron hover glow interpolation
        target_glow = 1.0 if self._chev_hover else 0.0
        self._chev_glow += (target_glow - self._chev_glow) * 0.18

        # Border glow phase
        self._border_glow_phase += 0.04

        # Redraw canvases
        self._draw_avatar(self._orb_canvas, _BG_PILL, is_expanded=False)
        self._draw_avatar(self._exp_orb_canvas, _BG_EXPANDED, is_expanded=True)
        self._draw_chevron(self._pill_chev_canvas, _BG_PILL)
        self._draw_chevron(self._exp_chev_canvas, _BG_EXPANDED)

        self._root.after(25, self._tick_animations)

    # ══════════════════════════════════════════════════════════════════
    #  DRAW — Custom Animated Avatar
    # ══════════════════════════════════════════════════════════════════

    def _draw_avatar(self, canvas: tk.Canvas, bg: str, is_expanded: bool = False):
        canvas.delete("all")
        cx = 19 if not is_expanded else 21
        cy = 19 if not is_expanded else 21
        avatar_img = self._exp_avatar_tk if is_expanded else self._pill_avatar_tk
        avatar_r = 15 if not is_expanded else 17
        accent = self._accent_for_state()
        pulse = 0.5 + 0.5 * math.sin(self._orb_phase)

        # ── State-reactive glowing aura
        if self._state != AgentState.IDLE:
            # Active glowing rings (Listening / Thinking / Speaking)
            for i in range(3):
                ri = avatar_r + 2.0 + pulse * 2.0 + i * 1.5
                glow = self._blend_color(accent, bg, 0.40 + i * 0.20)
                canvas.create_oval(cx - ri, cy - ri, cx + ri, cy + ri,
                                   fill="", outline=glow, width=1.2)
        else:
            # Gentle ambient halo when idle
            halo_glow = self._blend_color(_ACCENT_IDLE, bg, 0.70)
            canvas.create_oval(cx - avatar_r - 2, cy - avatar_r - 2,
                               cx + avatar_r + 2, cy + avatar_r + 2,
                               fill="", outline=halo_glow, width=1.0)

        # ── Custom Anime Avatar Image
        if avatar_img:
            canvas.create_image(cx, cy, image=avatar_img)
            # Glowing accent border ring around avatar
            border_col = self._blend_color(accent, bg, 0.35)
            canvas.create_oval(cx - avatar_r, cy - avatar_r, cx + avatar_r, cy + avatar_r,
                               fill="", outline=border_col, width=1.4)
        else:
            # Fallback circle
            canvas.create_oval(cx - avatar_r, cy - avatar_r, cx + avatar_r, cy + avatar_r,
                               fill=accent, outline="")


    # ══════════════════════════════════════════════════════════════════
    #  DRAW — 3D Animated Chevron
    # ══════════════════════════════════════════════════════════════════

    def _draw_chevron(self, canvas: tk.Canvas, bg: str):
        """
        Draws a perspective-styled chevron that rotates smoothly.
        Has a hover glow ring, 3D depth lines, and smooth angle animation.
        """
        canvas.delete("all")
        cx, cy = _CHEV_SIZE / 2, _CHEV_SIZE / 2
        accent = self._accent_for_state()

        # ── Background circle with hover glow
        # Glow ring (animated)
        if self._chev_glow > 0.02:
            for i in range(3):
                gr = 15 + i * 2.0
                alpha = self._chev_glow * (0.4 - i * 0.12)
                glow_col = self._blend_color(accent, bg, 1.0 - alpha)
                canvas.create_oval(cx - gr, cy - gr, cx + gr, cy + gr,
                                   fill="", outline=glow_col, width=1.2)

        # Button circle
        btn_r = 14
        btn_fill = self._blend_color(_BG_SURFACE, accent,
                                      0.08 + 0.12 * self._chev_glow)
        canvas.create_oval(cx - btn_r, cy - btn_r, cx + btn_r, cy + btn_r,
                           fill=btn_fill, outline=self._blend_color(
                               _BORDER_COLOR, accent, 0.2 * self._chev_glow),
                           width=1)

        # ── Draw the chevron arrows with rotation
        angle_rad = math.radians(self._chev_angle)

        # Chevron geometry (two strokes forming a V)
        arm_len = 5.5
        spread = 0.55       # radians — half-angle of the V
        thickness = 2.0 + 0.5 * self._chev_glow   # thicker on hover

        # The "V" points downward at angle=0, upward at angle=180
        # We rotate the entire V shape
        base_angle = math.pi / 2 + angle_rad  # π/2 = downward

        # Left arm
        lx = cx + arm_len * math.cos(base_angle + spread)
        ly = cy + arm_len * math.sin(base_angle + spread)
        # Right arm
        rx = cx + arm_len * math.cos(base_angle - spread)
        ry = cy + arm_len * math.sin(base_angle - spread)
        # Tip
        tip_len = 4.0
        tx = cx - tip_len * math.cos(base_angle)
        ty = cy - tip_len * math.sin(base_angle)

        # "3D" shadow stroke (offset down-right, darker)
        shadow_off = 1.0
        shadow_col = self._blend_color(accent, "#000000", 0.65)
        canvas.create_line(lx + shadow_off, ly + shadow_off,
                           tx + shadow_off, ty + shadow_off,
                           fill=shadow_col, width=thickness, capstyle="round")
        canvas.create_line(rx + shadow_off, ry + shadow_off,
                           tx + shadow_off, ty + shadow_off,
                           fill=shadow_col, width=thickness, capstyle="round")

        # Main chevron strokes
        line_col = self._blend_color(_TEXT_SECONDARY, accent, 0.3 + 0.5 * self._chev_glow)
        canvas.create_line(lx, ly, tx, ty,
                           fill=line_col, width=thickness, capstyle="round")
        canvas.create_line(rx, ry, tx, ty,
                           fill=line_col, width=thickness, capstyle="round")

        # Highlight stroke (offset up-left, brighter) — gives depth
        hl_off = -0.6
        hl_col = self._blend_color(line_col, "#FFFFFF", 0.25 + 0.15 * self._chev_glow)
        canvas.create_line(lx + hl_off, ly + hl_off,
                           tx + hl_off, ty + hl_off,
                           fill=hl_col, width=max(1.0, thickness - 0.8), capstyle="round")
        canvas.create_line(rx + hl_off, ry + hl_off,
                           tx + hl_off, ty + hl_off,
                           fill=hl_col, width=max(1.0, thickness - 0.8), capstyle="round")

    def _chev_enter(self):
        self._chev_hover = True

    def _chev_leave(self):
        self._chev_hover = False

    # ══════════════════════════════════════════════════════════════════
    #  COLOR HELPERS
    # ══════════════════════════════════════════════════════════════════

    def _accent_for_state(self) -> str:
        if self._muted:
            return _ACCENT_MUTED
        return {
            AgentState.IDLE: _ACCENT_IDLE,
            AgentState.LISTENING: _ACCENT_LISTEN,
            AgentState.THINKING: _ACCENT_THINK,
            AgentState.SPEAKING: _ACCENT_SPEAK,
        }[self._state]

    @staticmethod
    def _blend_color(c1: str, c2: str, t: float) -> str:
        """Linearly blend two hex colors. t=0 → c1, t=1 → c2."""
        t = max(0.0, min(1.0, t))
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        return "#{:02x}{:02x}{:02x}".format(
            int(r1 + (r2 - r1) * t),
            int(g1 + (g2 - g1) * t),
            int(b1 + (b2 - b1) * t),
        )

    # ══════════════════════════════════════════════════════════════════
    #  STATE UPDATES
    # ══════════════════════════════════════════════════════════════════

    def _set_state(self, state: AgentState):
        self._state = state
        labels = {
            AgentState.IDLE:      "Standing by…",
            AgentState.LISTENING: "Listening…",
            AgentState.THINKING:  "Thinking…",
            AgentState.SPEAKING:  "Speaking…",
        }
        text = labels[state]
        color = self._accent_for_state()
        self._pill_subtitle.configure(text=text, text_color=color)
        self._exp_subtitle.configure(text=text, text_color=color)

    def _set_user_transcript(self, text: str):
        self._user_text = text
        self._user_transcript.configure(text=text or "…")
        if self._state == AgentState.LISTENING and text:
            short = text[-50:] if len(text) > 50 else text
            self._pill_subtitle.configure(text=f'"{short}"')

    def _set_agent_transcript(self, text: str):
        self._agent_text = text
        self._agent_transcript.configure(text=text or "…")
        if self._state == AgentState.SPEAKING and text:
            short = text[-50:] if len(text) > 50 else text
            self._pill_subtitle.configure(text=f'"{short}"')

    # ══════════════════════════════════════════════════════════════════
    #  CONTROLS
    # ══════════════════════════════════════════════════════════════════

    def _handle_mute(self):
        self._muted = not self._muted
        if self._muted:
            self._mute_btn.configure(text="🔇", fg_color="#4C0519")
            self._mute_label.configure(text="Mic Off", text_color=_ACCENT_MUTED)
        else:
            self._mute_btn.configure(text="🎤", fg_color=_BG_SURFACE)
            self._mute_label.configure(text="Mic On", text_color=_TEXT_SECONDARY)
        if self._on_mute:
            self._on_mute()

    def _handle_end(self):
        if self._on_end:
            self._on_end()
        self.close()

    # ══════════════════════════════════════════════════════════════════
    #  EVENT QUEUE
    # ══════════════════════════════════════════════════════════════════

    def _poll_events(self):
        try:
            for _ in range(20):    # drain up to 20 per tick
                ev = self._eq.get_nowait()
                self._dispatch(ev)
        except queue.Empty:
            pass
        self._root.after(20, self._poll_events)

    def _dispatch(self, ev: OverlayEvent):
        if ev.kind == "state":
            try:
                self._set_state(AgentState(ev.value))
            except ValueError:
                pass
        elif ev.kind == "user_transcript":
            self._set_user_transcript(str(ev.value or ""))
        elif ev.kind in ("agent_transcript", "agent_speech_text"):
            self._set_agent_transcript(str(ev.value or ""))
        elif ev.kind == "close":
            self.close()


# ── Standalone demo ────────────────────────────────────────────────────────

if __name__ == "__main__":
    q: queue.Queue[OverlayEvent] = queue.Queue()

    def _sim():
        time.sleep(2)
        q.put(OverlayEvent("state", "listening"))
        time.sleep(1.5)
        q.put(OverlayEvent("user_transcript", "What's happening in the world?"))
        time.sleep(2)
        q.put(OverlayEvent("state", "thinking"))
        time.sleep(2.5)
        q.put(OverlayEvent("state", "speaking"))
        q.put(OverlayEvent("agent_transcript",
               "Here's what's going on, boss. There's a lot happening globally today."))
        time.sleep(4)
        q.put(OverlayEvent("state", "idle"))
        time.sleep(3)
        # Loop
        q.put(OverlayEvent("state", "listening"))
        time.sleep(2)
        q.put(OverlayEvent("user_transcript", "Play some music for me"))
        time.sleep(1.5)
        q.put(OverlayEvent("state", "thinking"))
        time.sleep(1)
        q.put(OverlayEvent("state", "speaking"))
        q.put(OverlayEvent("agent_transcript",
               "Queuing up something good on Spotify, boss."))
        time.sleep(3)
        q.put(OverlayEvent("state", "idle"))

    threading.Thread(target=_sim, daemon=True).start()
    ClaireOverlay(event_queue=q).run()
