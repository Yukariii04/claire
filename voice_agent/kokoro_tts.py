"""
claire Voice Agent — Custom Kokoro TTS Plugin for LiveKit Agents v1.5+
Correctly implements ChunkedStream with AudioEmitter for livekit-agents>=1.5.1.
"""

from __future__ import annotations

import asyncio
import uuid

import numpy as np
from kokoro_onnx import Kokoro
from livekit.agents import APIConnectOptions, tts
import logging

logger = logging.getLogger("claire.kokoro_tts")

# Kokoro outputs 24 kHz float32 mono audio
_SAMPLE_RATE = 24000
_NUM_CHANNELS = 1

# Singleton — heavy model loads once, reused for every synthesis call
_kokoro_instance: Kokoro | None = None
_kokoro_lock = asyncio.Lock()


async def _get_kokoro() -> Kokoro:
    global _kokoro_instance
    if _kokoro_instance is None:
        async with _kokoro_lock:
            if _kokoro_instance is None:
                logger.info("Loading Kokoro model…")
                loop = asyncio.get_event_loop()
                _kokoro_instance = await loop.run_in_executor(
                    None, lambda: Kokoro("kokoro-v1_0.onnx", "voices-v1_0.bin")
                )
                logger.info("Kokoro model loaded.")
    return _kokoro_instance


class KokoroTTS(tts.TTS):
    """Local, offline TTS using Kokoro-82M via kokoro-onnx."""

    def __init__(self, *, voice: str = "af_heart", speed: float = 1.15):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=_SAMPLE_RATE,
            num_channels=_NUM_CHANNELS,
        )
        self._voice = voice
        self._speed = speed

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = APIConnectOptions(),
    ) -> "KokoroChunkedStream":
        return KokoroChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
            voice=self._voice,
            speed=self._speed,
        )


class KokoroChunkedStream(tts.ChunkedStream):
    """Synthesizes one text segment and pushes raw PCM bytes to the AudioEmitter."""

    def __init__(
        self,
        *,
        tts: KokoroTTS,
        input_text: str,
        conn_options: APIConnectOptions,
        voice: str,
        speed: float,
    ):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._voice = voice
        self._speed = speed

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        """
        Runs Kokoro synthesis in a thread executor (it's synchronous C++/ONNX),
        converts float32 audio to raw int16 PCM, then pushes bytes to AudioEmitter.
        """
        kokoro = await _get_kokoro()

        def _synth():
            samples, sr = kokoro.create(
                self._input_text,
                voice=self._voice,
                speed=self._speed,
                lang="en-us",
            )
            # float32 → int16 PCM
            pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
            return pcm.tobytes(), sr

        loop = asyncio.get_event_loop()
        pcm_bytes, sample_rate = await loop.run_in_executor(None, _synth)

        # Initialize the emitter: raw PCM, 16-bit, 24 kHz mono
        output_emitter.initialize(
            request_id=str(uuid.uuid4()),
            sample_rate=sample_rate,
            num_channels=_NUM_CHANNELS,
            mime_type="audio/pcm",
        )

        # Push the raw PCM bytes — AudioEmitter handles framing internally
        output_emitter.push(pcm_bytes)
        output_emitter.end_input()
