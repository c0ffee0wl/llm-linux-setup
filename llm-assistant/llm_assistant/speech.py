"""Text-to-speech output using Google Cloud TTS (Chirp3-HD).

This module provides:
- SentenceBuffer: Buffer LLM tokens and yield complete sentences
- get_tts_credentials: Resolve Google Cloud credentials for TTS
- SpeechOutput: Streaming TTS with background synthesis and playback
"""

import os
import re
import threading
from typing import Optional, Tuple

import llm

from .utils import strip_markdown_for_tts

# Voice/audio dependencies (shared with voice.py)
try:
    import sounddevice as sd
    import numpy as np
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False
    sd = None
    np = None

# TTS output (optional - requires google-cloud-texttospeech and Vertex credentials)
try:
    from google.cloud import texttospeech
    from google.oauth2 import service_account
    from google.auth import default as google_auth_default
    from google.api_core.client_options import ClientOptions
    import queue
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    texttospeech = None
    service_account = None
    google_auth_default = None
    ClientOptions = None
    queue = None


class SentenceBuffer:
    """Buffer LLM tokens and yield complete sentences for TTS.

    Accumulates streaming tokens and returns complete sentences when
    sentence-ending punctuation is detected (.!?).
    """

    SENTENCE_END = re.compile(r'[.!?]\s*$')

    def __init__(self):
        self.buffer = ""

    def add(self, token: str) -> Optional[str]:
        """Add token to buffer. Returns sentence if one is complete."""
        self.buffer += token

        # Check for sentence boundary
        if self.SENTENCE_END.search(self.buffer):
            sentence = self.buffer.strip()
            self.buffer = ""
            return sentence
        return None

    def flush(self) -> Optional[str]:
        """Flush remaining buffer content (end of response)."""
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

    Synthesizes sentences as they complete during LLM streaming.
    Both synthesis and playback happen in background threads to avoid
    blocking the main LLM streaming loop.
    """

    def __init__(self, console):
        self.console = console
        self.client = None
        self.voice_name = "de-DE-Chirp3-HD-Laomedeia"  # German HD voice
        self.language_code = "de-DE"
        self.enabled = False
        self.audio_queue = None  # For synthesized audio
        self.worker_thread = None
        self._executor = None  # ThreadPoolExecutor for async synthesis
        self.cred_method = None  # Credential method used (for status display)

    def _lazy_load_client(self) -> bool:
        """Initialize TTS client on first use."""
        if self.client is not None:
            return True

        if not TTS_AVAILABLE:
            self.console.print("[red]google-cloud-texttospeech not installed. Re-run install-llm-tools.sh[/]")
            return False

        if not AUDIO_AVAILABLE:
            self.console.print("[red]sounddevice/numpy not installed. Re-run install-llm-tools.sh[/]")
            return False

        try:
            from concurrent.futures import ThreadPoolExecutor
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
            self.audio_queue = queue.Queue()
            self._executor = ThreadPoolExecutor(max_workers=1)  # Must be 1 to preserve sentence order
            return True
        except Exception as e:
            self.console.print(f"[red]Failed to initialize TTS client: {e}[/]")
            self.console.print("[dim]Configure via: llm vertex set-credentials /path/to/sa.json[/]")
            self.console.print("[dim]Or run: gcloud auth application-default login[/]")
            return False

    def _synthesize_and_queue(self, sentence: str):
        """Synthesize speech using streaming API and queue chunks for immediate playback."""
        try:
            # Strip markdown formatting (removes code blocks entirely)
            clean_text = strip_markdown_for_tts(sentence).strip()
            if not clean_text:
                return  # Skip empty text after stripping

            # Streaming synthesis config
            streaming_config = texttospeech.StreamingSynthesizeConfig(
                voice=texttospeech.VoiceSelectionParams(
                    name=self.voice_name,
                    language_code=self.language_code,
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

            # Queue each audio chunk immediately as it arrives (true streaming)
            for response in self.client.streaming_synthesize(request_generator()):
                if response.audio_content:
                    self.audio_queue.put(response.audio_content)
        except Exception:
            pass  # Silently skip on error

    def _playback_loop(self):
        """Background thread for sequential audio playback."""
        while True:
            audio_data = self.audio_queue.get()
            if audio_data is None:  # Sentinel to stop
                break
            try:
                audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
                sd.play(audio_array, samplerate=24000)
                sd.wait()
            except Exception:
                pass

    def speak_sentence(self, sentence: str):
        """Queue sentence for async synthesis and playback (non-blocking)."""
        if not self._lazy_load_client():
            return

        # Start playback thread if needed
        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.worker_thread = threading.Thread(target=self._playback_loop, daemon=True)
            self.worker_thread.start()

        # Submit synthesis to thread pool (non-blocking)
        self._executor.submit(self._synthesize_and_queue, sentence)

    def stop(self):
        """Stop playback and clear queue."""
        if self.audio_queue:
            # Clear queue
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except:
                    break
            # Stop current playback
            try:
                sd.stop()
            except:
                pass
