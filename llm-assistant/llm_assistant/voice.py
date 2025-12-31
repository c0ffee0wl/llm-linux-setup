"""Voice input (speech-to-text) using onnx-asr and sounddevice.

This module provides the VoiceInput class for recording and transcribing
speech input via Ctrl+Space keybinding, with optional voice loop mode
using VAD (Voice Activity Detection).
"""

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Tuple

from .utils import ConsoleHelper, is_handy_running

# Voice input (optional - graceful degradation if not installed)
VOICE_AVAILABLE = False
VOICE_UNAVAILABLE_REASON = None  # None = available, string = reason unavailable
sd = None
np = None

def _suppress_stderr():
    """Context manager to suppress stderr (for PortAudio messages)."""
    devnull_fd = None
    old_stderr_fd = None
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        old_stderr_fd = os.dup(2)
        os.dup2(devnull_fd, 2)
        return devnull_fd, old_stderr_fd
    except OSError:
        # Cleanup on failure
        if devnull_fd is not None:
            os.close(devnull_fd)
        if old_stderr_fd is not None:
            os.close(old_stderr_fd)
        return None, None

def _restore_stderr(devnull_fd, old_stderr_fd):
    """Restore stderr after suppression."""
    if old_stderr_fd is not None:
        os.dup2(old_stderr_fd, 2)
        os.close(old_stderr_fd)
    if devnull_fd is not None:
        os.close(devnull_fd)

# Check for Handy FIRST - skip sounddevice import entirely if Handy handles STT
# (avoids PortAudio initialization errors during import)
if is_handy_running():
    VOICE_UNAVAILABLE_REASON = "Handy running"
else:
    try:
        # Suppress PortAudio stderr during import (can print debug messages)
        _devnull_fd, _old_stderr_fd = _suppress_stderr()
        try:
            import sounddevice as sd
            import numpy as np
            VOICE_AVAILABLE = True
        finally:
            _restore_stderr(_devnull_fd, _old_stderr_fd)
    except ImportError:
        VOICE_UNAVAILABLE_REASON = "not installed"


class VoiceInput:
    """Speech-to-text input using onnx-asr and sounddevice."""

    # Animation frames for visual feedback
    RECORDING_FRAMES = ["●", "◉"]  # Pulsing circle
    TRANSCRIBING_FRAMES = ["⟳", "↻"]  # Circular arrows
    ANIMATION_INTERVAL = 0.5  # 500ms per frame

    def __init__(self, console, debug: bool = False):
        self.console = console
        self.debug = debug
        self.recording = False
        self.audio_chunks = []
        self.sample_rate = 16000
        self.model = None
        self.stream = None
        self.preserved_text = ""  # Text to preserve across recording
        self.status_message = ""  # For dynamic prompt display
        self._app = None  # prompt_toolkit app for invalidate()
        # Animation state
        self._animation_frame = 0
        self._animation_thread = None
        self._stop_animation = threading.Event()
        # Post-processing (set by session.py when using Gemini/Vertex)
        self.post_process_enabled = False
        self.post_process_model = "gemini-2.5-flash-lite"  # Default, overridden by session
        # Voice loop mode with VAD
        self.loop_mode = False
        self._loop_stop_event = threading.Event()
        self._loop_thread = None
        self._vad = None
        self._loop_callback = None  # Callback to submit transcribed text

    def _animate(self, frames, state: str):
        """Background thread that cycles animation frames."""
        while not self._stop_animation.is_set():
            symbol = frames[self._animation_frame % len(frames)]
            self.status_message = f"{symbol} {state}..."
            self._animation_frame += 1
            if self._app:
                self._app.invalidate()
            self._stop_animation.wait(self.ANIMATION_INTERVAL)

    def _start_animation(self, frames, state: str):
        """Start animation in background thread."""
        self._stop_animation.clear()
        self._animation_frame = 0
        # Set initial frame synchronously to avoid race with render()
        self.status_message = f"{frames[0]} {state}..."
        self._animation_thread = threading.Thread(
            target=self._animate,
            args=(frames, state),
            daemon=True
        )
        self._animation_thread.start()

    def _stop_animation_thread(self):
        """Stop animation thread."""
        if self._animation_thread:
            self._stop_animation.set()
            self._animation_thread.join(timeout=0.5)
            self._animation_thread = None

    def _lazy_load_model(self) -> bool:
        """Load ASR model on first use (avoids startup delay)."""
        if self.model is None:
            try:
                import onnx_asr
            except ImportError:
                ConsoleHelper.error(self.console, "onnx-asr not installed. Re-run install-llm-tools.sh")
                return False
            try:
                # Use shared model path (same as Handy) - must be pre-downloaded by install script
                model_path = os.path.expanduser("~/.local/share/com.pais.handy/models/parakeet-tdt-0.6b-v3-int8")
                encoder_path = os.path.join(model_path, "encoder-model.int8.onnx")

                if not os.path.isfile(encoder_path):
                    ConsoleHelper.error(self.console, "Speech model not found. Run install-llm-tools.sh to download.")
                    return False

                # Disable HuggingFace auto-download (model must be pre-installed)
                os.environ["HF_HUB_OFFLINE"] = "1"
                self.model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", model_path, quantization="int8")
            except Exception as e:
                ConsoleHelper.error(self.console, f"Failed to load speech model: {e}")
                return False
        return True

    def _audio_callback(self, indata, frames, time_info, status):
        """Callback for sounddevice stream."""
        if self.recording:
            self.audio_chunks.append(indata.copy())

    def start(self) -> bool:
        """Start recording audio."""
        if not VOICE_AVAILABLE:
            ConsoleHelper.error(self.console, "Voice input unavailable. Re-run install-llm-tools.sh")
            return False

        if self.recording:
            return False

        self.audio_chunks = []
        self.recording = True

        try:
            # Suppress PortAudio stderr messages during stream creation
            # (PortAudio prints debug messages like "paTimedOut" to fd 2)
            devnull_fd, old_stderr_fd = _suppress_stderr()
            try:
                self.stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype=np.float32,
                    callback=self._audio_callback
                )
                self.stream.start()
            finally:
                _restore_stderr(devnull_fd, old_stderr_fd)
            # Start recording animation
            self._start_animation(self.RECORDING_FRAMES, "Recording")
            return True
        except Exception as e:
            ConsoleHelper.error(self.console, f"Failed to start recording: {e}")
            if self.stream:
                try:
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
            self.recording = False
            return False

    def stop(self) -> Optional[str]:
        """Stop recording and transcribe."""
        if not self.recording:
            return None

        self.recording = False
        self._stop_animation_thread()
        self.status_message = ""  # Clear immediately after stopping animation

        if self.stream:
            # Suppress PortAudio stderr during stream cleanup
            devnull_fd, old_stderr_fd = _suppress_stderr()
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                _restore_stderr(devnull_fd, old_stderr_fd)
            self.stream = None

        if not self.audio_chunks:
            return None

        # Combine audio chunks
        audio = np.concatenate(self.audio_chunks, axis=0).flatten()

        # Start transcribing animation
        self._start_animation(self.TRANSCRIBING_FRAMES, "Transcribing")

        # Helper to poll while rendering animation
        def poll_future(future):
            while not future.done():
                if self._app:
                    self._app.renderer.render(self._app, self._app.layout)
                time.sleep(0.1)
            return future.result()

        # Run model loading and transcription in background to allow animation
        text = None
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                # Load model if needed (can take seconds on first use)
                if self.model is None:
                    future = executor.submit(self._lazy_load_model)
                    if not poll_future(future):
                        self._stop_animation_thread()
                        self.status_message = ""
                        return None

                # Run transcription
                future = executor.submit(
                    self.model.recognize, audio, sample_rate=self.sample_rate
                )
                result = poll_future(future)
                text = result.strip() if result else None
        except Exception as e:
            self._stop_animation_thread()
            self.status_message = ""
            ConsoleHelper.error(self.console, f"Transcription failed: {e}")
            return None

        self._stop_animation_thread()
        self.status_message = ""
        return text

    def toggle(self) -> Tuple[bool, Optional[str]]:
        """Toggle recording state. Returns (is_recording, transcribed_text)."""
        if self.recording:
            text = self.stop()
            return (False, text)
        else:
            started = self.start()
            return (started, None)

    def post_process_transcript(self, text: str) -> str:
        """Clean transcript using LLM (Gemini/Vertex only).

        Uses gemini-2.5-flash-lite (vertex/ or gemini- prefix based on session).
        Only runs if post_process_enabled is True.
        """
        if not self.post_process_enabled or not text:
            return text

        try:
            import llm
            from jinja2 import Environment, PackageLoader

            env = Environment(loader=PackageLoader('llm_assistant', 'templates/prompts'))
            template = env.get_template('voice_clean.j2')
            prompt = template.render(text=text)

            model = llm.get_model(self.post_process_model)
            response = model.prompt(prompt)
            cleaned = response.text().strip()
            return cleaned if cleaned else text
        except Exception:
            # Fallback to original text on any error
            return text

    # Voice loop mode with VAD
    VAD_THRESHOLD = 0.5  # Speech probability threshold
    SILENCE_DURATION_MS = 800  # Silence duration to end speech
    VAD_CHUNK_SAMPLES = 512  # Silero VAD requires 512 samples at 16kHz (32ms)

    def _lazy_load_vad(self) -> bool:
        """Load VAD model on first use."""
        if self._vad is not None:
            return True

        try:
            from .vad import SileroVAD
            self._vad = SileroVAD(threshold=self.VAD_THRESHOLD, sample_rate=self.sample_rate, debug=self.debug)
            return self._vad.preload()
        except Exception as e:
            ConsoleHelper.error(self.console, f"VAD load error: {e}")
            return False

    def start_loop(self, callback: Callable[[str], None]) -> bool:
        """Start voice loop mode with VAD.

        Args:
            callback: Function to call with transcribed text

        Returns:
            True if loop started successfully
        """
        if not VOICE_AVAILABLE:
            ConsoleHelper.error(self.console, "Voice input unavailable")
            return False

        if self.loop_mode:
            return False  # Already running

        if not self._lazy_load_vad():
            ConsoleHelper.error(self.console, "Failed to load VAD model")
            return False

        self._loop_callback = callback
        self._loop_stop_event.clear()
        self.loop_mode = True

        self._loop_thread = threading.Thread(target=self._voice_loop, daemon=True)
        self._loop_thread.start()
        return True

    def stop_loop(self):
        """Stop voice loop mode."""
        if not self.loop_mode:
            return

        self.loop_mode = False
        self._loop_stop_event.set()

        if self._loop_thread:
            self._loop_thread.join(timeout=2.0)
            self._loop_thread = None

        self._loop_callback = None

    def _voice_loop(self):
        """Main voice loop thread - continuous listening with VAD.

        Architecture (per sounddevice best practices):
        1. Audio callback: ONLY copies data to queue (non-blocking, real-time safe)
        2. This thread: Reads queue, runs VAD, accumulates speech, transcribes

        Reference: https://python-sounddevice.readthedocs.io/en/latest/usage.html
        """
        import queue

        chunk_samples = self.VAD_CHUNK_SAMPLES  # 512 samples = 32ms at 16kHz
        chunk_ms = (chunk_samples / self.sample_rate) * 1000  # 32ms
        silence_chunks = int(self.SILENCE_DURATION_MS / chunk_ms)  # ~25 chunks for 800ms

        # Thread-safe queue for audio chunks (callback -> processing thread)
        audio_queue = queue.Queue()

        callback_count = [0]  # Mutable container for closure

        def audio_callback(indata, frames, time_info, status):
            """Callback runs in PortAudio's audio thread - must be non-blocking!"""
            if self._loop_stop_event.is_set():
                raise sd.CallbackStop()
            # Only copy data to queue - no processing here!
            audio_queue.put_nowait(indata.copy())
            if self.debug:
                callback_count[0] += 1
                if callback_count[0] % 100 == 0:  # Print every 100 callbacks (~3 sec)
                    print(f"[DEBUG] Callback count: {callback_count[0]}, queue size: {audio_queue.qsize()}")

        # Create callback-based stream
        devnull_fd, old_stderr_fd = _suppress_stderr()
        try:
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype=np.float32,
                blocksize=chunk_samples,
                callback=audio_callback
            )
            stream.start()
        except Exception as e:
            _restore_stderr(devnull_fd, old_stderr_fd)
            ConsoleHelper.error(self.console, f"Failed to open audio stream: {e}")
            self.loop_mode = False
            return
        finally:
            _restore_stderr(devnull_fd, old_stderr_fd)

        try:
            while not self._loop_stop_event.is_set():
                try:
                    # Reset for new utterance
                    audio_chunks = []
                    speech_started = False
                    silent_count = 0
                    self._vad.reset()

                    ConsoleHelper.dim(self.console, "🎤 Listening...")

                    # Process audio from queue (VAD runs here, not in callback)
                    while not self._loop_stop_event.is_set():
                        try:
                            # Get chunk from queue with timeout
                            chunk = audio_queue.get(timeout=0.1)
                            chunk = chunk.flatten()

                            # Run VAD here in processing thread (not callback!)
                            is_speech = self._vad.is_speech(chunk)
                            if is_speech and self.debug:
                                print("[DEBUG] VAD: speech detected!")

                            if is_speech:
                                if not speech_started:
                                    speech_started = True
                                    ConsoleHelper.dim(self.console, "● Recording...")
                                audio_chunks.append(chunk)
                                silent_count = 0
                            elif speech_started:
                                audio_chunks.append(chunk)
                                silent_count += 1
                                if silent_count >= silence_chunks:
                                    if self.debug:
                                        print(f"[DEBUG] Silence detected, breaking. Chunks: {len(audio_chunks)}")
                                    break  # Utterance complete

                        except queue.Empty:
                            continue

                    if self.debug:
                        print(f"[DEBUG] Inner loop exited. stop_event={self._loop_stop_event.is_set()}, chunks={len(audio_chunks)}")
                    if self._loop_stop_event.is_set():
                        break

                    if not audio_chunks:
                        if self.debug:
                            print("[DEBUG] No audio chunks, continuing...")
                        continue

                    if self.debug:
                        print(f"[DEBUG] Got {len(audio_chunks)} chunks, concatenating...")
                    audio = np.concatenate(audio_chunks, axis=0)
                    if self.debug:
                        print(f"[DEBUG] Audio shape: {audio.shape}, transcribing...")

                    # Transcribe
                    ConsoleHelper.dim(self.console, "⟳ Transcribing...")

                    if not self._lazy_load_model():
                        continue

                    try:
                        result = self.model.recognize(audio, sample_rate=self.sample_rate)
                        text = result.strip() if result else None
                    except Exception:
                        text = None

                    if text and self._loop_callback:
                        if self.post_process_enabled:
                            ConsoleHelper.dim(self.console, "✨ Cleaning transcript...")
                        text = self.post_process_transcript(text)
                        self._loop_callback(text)

                except Exception as e:
                    ConsoleHelper.error(self.console, f"Voice loop error: {e}")
        finally:
            # Clean up stream when loop ends
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

