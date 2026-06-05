"""
claire Voice Agent — Custom Vosk STT Plugin for LiveKit Agents v1.5+
Correctly implements RecognizeStream for livekit-agents>=1.5.1.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading

import vosk
from livekit import rtc
from livekit.agents import APIConnectOptions, stt
import logging

logger = logging.getLogger("claire.vosk_stt")


class VoskSTT(stt.STT):
    """Offline, local STT using Vosk / KaldiRecognizer."""

    def __init__(
        self,
        *,
        model_path: str = "models/vosk-model-en-us-0.22",
        sample_rate: int = 16000,
    ):
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=True, interim_results=True)
        )
        self._sample_rate = sample_rate
        if not os.path.isdir(model_path):
            raise FileNotFoundError(
                f"Vosk model not found at '{model_path}'. "
                "Download from https://alphacephei.com/vosk/models and unzip there."
            )
        logger.info("Loading Vosk model from '%s'…", model_path)
        self._vosk_model = vosk.Model(model_path)
        logger.info("Vosk model loaded.")

    def stream(
        self,
        *,
        language: str | None = None,
        conn_options: APIConnectOptions = APIConnectOptions(),
    ) -> "VoskRecognizeStream":
        return VoskRecognizeStream(
            stt=self,
            vosk_model=self._vosk_model,
            sample_rate=self._sample_rate,
            conn_options=conn_options,
        )


class VoskRecognizeStream(stt.RecognizeStream):
    """
    Reads LiveKit AudioFrames from self._input_ch (provided by base class),
    runs Vosk in a background daemon thread, and pushes SpeechEvents to
    self._event_ch (consumed by the LiveKit pipeline).
    """

    _FlushSentinel = stt.RecognizeStream._FlushSentinel  # re-export for clarity

    def __init__(
        self,
        *,
        stt: VoskSTT,
        vosk_model: vosk.Model,
        sample_rate: int,
        conn_options: APIConnectOptions,
    ):
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=sample_rate)
        self._vosk_model = vosk_model
        self._sample_rate = sample_rate

    async def _run(self) -> None:
        """
        Bridge between async LiveKit pipeline and synchronous Vosk.
        We spin a daemon thread for Vosk and use asyncio queues to communicate.
        """
        loop = asyncio.get_event_loop()
        result_queue: asyncio.Queue[stt.SpeechEvent | None] = asyncio.Queue()

        # Thread-safe queue for feeding PCM bytes to the Vosk thread
        pcm_queue: queue.Queue[bytes | None] = queue.Queue()

        def _vosk_thread():
            rec = vosk.KaldiRecognizer(self._vosk_model, self._sample_rate)
            rec.SetWords(False)

            while True:
                data = pcm_queue.get()
                if data is None:
                    # Flush final result then exit
                    text = json.loads(rec.FinalResult()).get("text", "").strip()
                    if text:
                        loop.call_soon_threadsafe(
                            result_queue.put_nowait,
                            stt.SpeechEvent(
                                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                                alternatives=[stt.SpeechData(text=text, language="en")],
                            ),
                        )
                    loop.call_soon_threadsafe(result_queue.put_nowait, None)
                    return

                if rec.AcceptWaveform(data):
                    text = json.loads(rec.Result()).get("text", "").strip()
                    if text:
                        loop.call_soon_threadsafe(
                            result_queue.put_nowait,
                            stt.SpeechEvent(
                                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                                alternatives=[stt.SpeechData(text=text, language="en")],
                            ),
                        )
                    # Reset for next utterance
                    rec = vosk.KaldiRecognizer(self._vosk_model, self._sample_rate)
                    rec.SetWords(False)
                else:
                    partial = json.loads(rec.PartialResult()).get("partial", "").strip()
                    if partial:
                        loop.call_soon_threadsafe(
                            result_queue.put_nowait,
                            stt.SpeechEvent(
                                type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                                alternatives=[stt.SpeechData(text=partial, language="en")],
                            ),
                        )

        thread = threading.Thread(target=_vosk_thread, daemon=True)
        thread.start()

        try:
            # Drain LiveKit audio frames from self._input_ch and forward to Vosk thread
            async for frame in self._input_ch:
                if isinstance(frame, self._FlushSentinel):
                    # End of user utterance — signal Vosk to finalize
                    pcm_queue.put(None)
                    # Drain results until we see the sentinel None
                    while True:
                        event = await result_queue.get()
                        if event is None:
                            break
                        self._event_ch.send_nowait(event)
                else:
                    # Convert float32/int16 AudioFrame to raw int16 bytes for Vosk
                    pcm_queue.put(frame.data.tobytes())
                    # Drain any available results without blocking
                    while not result_queue.empty():
                        event = result_queue.get_nowait()
                        if event is not None:
                            self._event_ch.send_nowait(event)
        finally:
            pcm_queue.put(None)
            thread.join(timeout=2.0)
