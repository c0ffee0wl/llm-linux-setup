"""Voice input (speech-to-text) using onnx-asr and sounddevice.

This module provides the VoiceInput class for recording and transcribing
speech input via Ctrl+Space keybinding.
"""

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple

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

