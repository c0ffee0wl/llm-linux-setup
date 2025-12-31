"""Text-to-speech output using Google Cloud TTS (Chirp3-HD).

This module provides:
- SentenceBuffer: Buffer LLM tokens and yield complete sentences
- get_tts_credentials: Resolve Google Cloud credentials for TTS
- SpeechOutput: Low-latency streaming TTS with progressive playback

Architecture:
- Progressive chunks: Audio queued as it arrives from API (not accumulated)
- Buffer threshold: Playback starts after ~200ms buffered (prevents underruns)
- Single worker: Sequential synthesis maintains sentence order naturally
- Continuous stream: sd.OutputStream with callback for gapless playback
- Pre-warm: Connection can be established before first speech
"""

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple

import llm

from .utils import strip_markdown_for_tts, ConsoleHelper

# Audio playback dependencies - lazily imported to avoid PortAudio initialization
# at module load time (which probes audio devices including microphone)
sd = None
np = None
queue = None  # Also lazy - needed for thread-safe audio queue

# TTS output (optional - requires google-cloud-texttospeech and Vertex credentials)
try:
    from google.cloud import texttospeech
    from google.oauth2 import service_account
    from google.auth import default as google_auth_default
    from google.api_core.client_options import ClientOptions
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    texttospeech = None
    service_account = None
    google_auth_default = None
    ClientOptions = None


class SentenceBuffer:
    """Buffer LLM tokens and yield complete sentences for TTS.

    Accumulates streaming tokens and returns complete sentences when
    sentence-ending punctuation is detected (.!?).

    Code block handling:
    - Tracks fenced code blocks (```) using count-based state
    - Odd fence count = inside code block, even = outside
    - Content inside code blocks is discarded (not spoken)
    - When exiting a code block, buffer is trimmed to after closing fence
    """

    SENTENCE_END = re.compile(r'[.!?]\s*$')

    def __init__(self):
        self.buffer = ""
        self._in_code_block = False

    def add(self, token: str) -> Optional[str]:
        """Add token to buffer. Returns sentence if one is complete.

        Handles streaming code blocks by iteratively stripping complete blocks:
        - Finds and removes complete ``` ... ``` blocks
        - If incomplete block remains (open without close), suppresses output
        - Works for both streaming (fences in separate adds) and batch scenarios
        """
        self.buffer += token

        # Strip complete code blocks iteratively
        # Handles: (1) complete blocks in single add() (2) blocks completed by this token
        while True:
            first_open = self.buffer.find('```')
            if first_open == -1:
                break  # No fences, normal processing
            # Look for closing fence after the opening
            first_close = self.buffer.find('```', first_open + 3)
            if first_close == -1:
                # Incomplete block - we're inside it, wait for more tokens
                self._in_code_block = True
                return None
            # Complete block found - remove it entirely
            self.buffer = self.buffer[:first_open] + self.buffer[first_close + 3:].lstrip('\n')

        # No incomplete blocks remain
        self._in_code_block = False

        # Check for sentence boundary
        if self.SENTENCE_END.search(self.buffer):
            sentence = self.buffer.strip()
            self.buffer = ""
            return sentence if sentence else None
        return None

    def flush(self) -> Optional[str]:
        """Flush remaining buffer content (end of response).

        If still inside a code block (malformed markdown), discards content.
        """
        # If stuck in code block at end of response, discard
        if self._in_code_block:
            self.buffer = ""
            self._in_code_block = False
            return None

        if self.buffer.strip():
            sentence = self.buffer.strip()
            self.buffer = ""
            return sentence
        return None


def get_tts_credentials() -> Tuple[Optional[object], Optional[str]]:
    """
    Get Google Cloud credentials for TTS.

    Resolution order:
    1. GOOGLE_APPLICATION_CREDENTIALS env var
    2. vertex-credentials-path from llm config (set via 'llm vertex set-credentials')
    3. Application Default Credentials (ADC)

    Returns tuple (credentials, method_name) where method_name indicates the source.
    Returns (None, None) if credentials cannot be obtained.
    """
    if not TTS_AVAILABLE:
        return None, None

    # 1. Check GOOGLE_APPLICATION_CREDENTIALS env var
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and os.path.exists(creds_path):
        try:
            credentials = service_account.Credentials.from_service_account_file(
                creds_path,
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            return credentials, "service account (env)"
        except Exception:
            pass  # Fall through to next method

    # 2. Check vertex-credentials-path from llm config
    try:
        config_path = llm.get_key("", "vertex-credentials-path", "")
        if config_path and os.path.exists(config_path):
            credentials = service_account.Credentials.from_service_account_file(
                config_path,
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            return credentials, "service account (llm config)"
    except Exception:
        pass  # Fall through to ADC

    # 3. Fall back to Application Default Credentials
    try:
        credentials, _ = google_auth_default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return credentials, "ADC"
    except Exception:
        return None, None


class SpeechOutput:
    """Text-to-speech output using Google Cloud TTS (Chirp3-HD).

    Architecture for low-latency streaming:
    - Progressive chunks: Audio chunks queued as they arrive from API (not accumulated)
    - Buffer threshold: Playback starts after ~100ms buffered (prevents underruns)
    - Single worker: Sequential synthesis maintains sentence order naturally
    - Continuous stream: sd.OutputStream with callback for gapless playback
    - Pre-warm: Connection can be established before first speech
    """

    # Audio configuration
    SAMPLE_RATE = 24000  # Chirp3-HD native sample rate

    # Buffer threshold before starting playback (in samples)
    # 100ms = 2400 samples - lower latency while still preventing underruns
    BUFFER_THRESHOLD_MS = 100
    BUFFER_THRESHOLD_SAMPLES = int(SAMPLE_RATE * BUFFER_THRESHOLD_MS / 1000)

    def __init__(self, console):
        self.console = console
        self.client = None
        self.voice_name = "de-DE-Chirp3-HD-Laomedeia"  # German HD voice
        self.language_code = "de-DE"
        self.enabled = False
        self.cred_method = None  # Credential method used (for status display)

        # Single-worker executor for sequential synthesis
        self._executor = None

        # Continuous playback via OutputStream
        self._playback_queue = None  # Queue of audio arrays ready for playback
        self._stream = None  # sd.OutputStream
        self._stream_lock = threading.Lock()  # Protects stream start/stop
        self._stopped = False  # Prevents stream restart after explicit stop
        self._current_audio = None  # Current audio array being played
        self._audio_position = 0  # Position within current audio array

    def _lazy_load_client(self) -> bool:
        """Initialize TTS client on first use."""
        global sd, np, queue

        if self.client is not None:
            return True

        if not TTS_AVAILABLE:
            ConsoleHelper.error(self.console, "google-cloud-texttospeech not installed. Re-run install-llm-tools.sh")
            return False

        # Lazy import sounddevice/numpy/queue - avoids PortAudio init at module load
        if sd is None:
            try:
                import sounddevice as _sd
                import numpy as _np
                import queue as _queue
                sd = _sd
                np = _np
                queue = _queue
            except ImportError:
                ConsoleHelper.error(self.console, "sounddevice/numpy not installed. Re-run install-llm-tools.sh")
                return False

        try:
            credentials, self.cred_method = get_tts_credentials()
            # Use EU endpoint for data residency compliance
            client_options = ClientOptions(api_endpoint="eu-texttospeech.googleapis.com")
            self.client = texttospeech.TextToSpeechClient(
                credentials=credentials,
                client_options=client_options
            )
            # If credentials was None but client succeeded, it used ADC internally
            if self.cred_method is None:
                self.cred_method = "ADC (implicit)"
            # Initialize playback queue and single-worker executor
            # Single worker ensures sentences are synthesized in order
            self._playback_queue = queue.Queue()
            self._executor = ThreadPoolExecutor(max_workers=1)
            return True
        except Exception as e:
            ConsoleHelper.error(self.console, f"Failed to initialize TTS client: {e}")
            ConsoleHelper.dim(self.console, "Configure via: llm vertex set-credentials /path/to/sa.json")
            ConsoleHelper.dim(self.console, "Or run: gcloud auth application-default login")
            return False

    def _audio_callback(self, outdata, frames, time_info, status):
        """OutputStream callback - fills buffer with audio samples.

        Called by sounddevice from a separate thread. Pulls audio from
        the playback queue and outputs it continuously. Outputs silence
        when no audio is available.
        """
        output = outdata.reshape(-1)  # Flatten to 1D array
        position = 0

        while position < frames:
            # Get next audio chunk if current is exhausted
            if self._current_audio is None or self._audio_position >= len(self._current_audio):
                try:
                    self._current_audio = self._playback_queue.get_nowait()
                    self._audio_position = 0
                except queue.Empty:
                    # No audio available - output silence for remaining frames
                    output[position:] = 0
                    return

            # Copy samples from current chunk to output
            available = len(self._current_audio) - self._audio_position
            to_copy = min(frames - position, available)
            output[position:position + to_copy] = \
                self._current_audio[self._audio_position:self._audio_position + to_copy]
            position += to_copy
            self._audio_position += to_copy

    def _synthesize_sentence(self, sentence: str):
        """Synthesize a sentence with progressive chunk queueing.

        Chunks are queued as they arrive from the streaming API, not accumulated.
        Playback starts once buffer threshold is reached for low latency.
        """
        try:
            # Strip markdown formatting (removes code blocks entirely)
            clean_text = strip_markdown_for_tts(sentence).strip()
            if not clean_text:
                return  # Nothing to synthesize

            # Streaming synthesis config with explicit audio parameters
            # Note: StreamingAudioConfig doesn't support speaking_rate - only
            # the non-streaming AudioConfig does. This is a Google API limitation.
            streaming_config = texttospeech.StreamingSynthesizeConfig(
                voice=texttospeech.VoiceSelectionParams(
                    name=self.voice_name,
                    language_code=self.language_code,
                ),
                streaming_audio_config=texttospeech.StreamingAudioConfig(
                    audio_encoding=texttospeech.AudioEncoding.PCM,  # PCM for streaming (not LINEAR16)
                    sample_rate_hertz=self.SAMPLE_RATE,
                )
            )

            # Request generator for streaming API
            def request_generator():
                yield texttospeech.StreamingSynthesizeRequest(
                    streaming_config=streaming_config
                )
                yield texttospeech.StreamingSynthesizeRequest(
                    input=texttospeech.StreamingSynthesisInput(text=clean_text)
                )

            # Progressive chunk queueing - queue each chunk as it arrives
            samples_queued = 0
            for response in self.client.streaming_synthesize(request_generator()):
                if response.audio_content:
                    # Convert to float32 audio array
                    audio_array = np.frombuffer(
                        response.audio_content, dtype=np.int16
                    ).astype(np.float32) / 32768.0

                    # Queue immediately for playback
                    self._playback_queue.put(audio_array)
                    samples_queued += len(audio_array)

                    # Start playback once buffer threshold reached
                    self._maybe_start_stream(samples_queued)

            # If sentence was short (< threshold), still start stream
            if samples_queued > 0:
                self._force_start_stream()

        except Exception as e:
            # Log error but don't block subsequent sentences
            import logging
            logging.debug(f"TTS synthesis failed: {e}")

    def _maybe_start_stream(self, samples_queued: int):
        """Start the output stream once buffer threshold is reached."""
        if samples_queued >= self.BUFFER_THRESHOLD_SAMPLES:
            self._force_start_stream()

    def _force_start_stream(self):
        """Start the output stream unconditionally (if not already running)."""
        with self._stream_lock:
            if self._stream is None and not self._stopped:
                self._stream = sd.OutputStream(
                    samplerate=self.SAMPLE_RATE,
                    channels=1,
                    dtype='float32',
                    callback=self._audio_callback,
                    blocksize=1024,  # ~43ms blocks at 24kHz
                )
                self._stream.start()

    def speak_sentence(self, sentence: str):
        """Queue sentence for async synthesis and playback (non-blocking)."""
        if not self._lazy_load_client():
            return

        # Reset stopped flag on new speech
        self._stopped = False

        # Submit for synthesis - single worker ensures order
        # Stream starts automatically once buffer threshold reached
        self._executor.submit(self._synthesize_sentence, sentence)

    def stop(self):
        """Stop playback and clear all queues."""
        # Prevent stream restart by in-flight synthesis
        self._stopped = True

        # Stop and close the output stream (with lock)
        with self._stream_lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

        # Reset playback state
        self._current_audio = None
        self._audio_position = 0

        # Clear playback queue
        if self._playback_queue is not None:
            while not self._playback_queue.empty():
                try:
                    self._playback_queue.get_nowait()
                except queue.Empty:
                    break

    def prewarm(self):
        """Pre-initialize TTS client in background (reduces first-sentence latency).

        Call this when TTS is enabled (e.g., /speech command) to establish
        the Google Cloud connection before the first sentence needs synthesis.
        """
        if self.client is None:
            threading.Thread(target=self._lazy_load_client, daemon=True).start()
