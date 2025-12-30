"""Silero VAD wrapper using direct ONNX inference.

This module provides Voice Activity Detection using the Silero VAD model
via ONNX Runtime, without requiring PyTorch.

The model is downloaded and cached in ~/.cache/llm-assistant/models/
"""

import os
from pathlib import Path
from typing import Optional

import numpy as np

# Model location
VAD_MODEL_URL = "https://huggingface.co/onnx-community/silero-vad/resolve/main/onnx/model.onnx"
VAD_MODEL_PATH = Path.home() / ".cache/llm-assistant/models/silero_vad.onnx"


class SileroVAD:
    """Silero VAD using ONNX runtime (no PyTorch dependency).

    Uses the Silero VAD v5 ONNX model for accurate speech detection.
    The model is downloaded on first use and cached locally.
    """

    def __init__(self, threshold: float = 0.5, sample_rate: int = 16000, debug: bool = False):
        """Initialize VAD.

        Args:
            threshold: Speech probability threshold (0.0-1.0)
            sample_rate: Audio sample rate in Hz (16000 supported)
            debug: Enable debug output
        """
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.debug = debug
        self.session = None
        # Combined LSTM state tensor - shape (2, 1, 128) for Silero VAD v5
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def _ensure_model(self) -> bool:
        """Download model if not present, load ONNX session.

        Returns:
            True if model is ready, False on error
        """
        if self.session is not None:
            return True

        try:
            import onnxruntime as ort
        except ImportError:
            return False

        if not VAD_MODEL_PATH.exists():
            VAD_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            try:
                import urllib.request
                urllib.request.urlretrieve(VAD_MODEL_URL, VAD_MODEL_PATH)
            except Exception:
                return False

        try:
            self.session = ort.InferenceSession(
                str(VAD_MODEL_PATH),
                providers=['CPUExecutionProvider']
            )
            return True
        except Exception:
            return False

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Check if audio chunk contains speech.

        Args:
            audio_chunk: Audio samples (float32, 16kHz, 30-96ms)

        Returns:
            True if speech detected above threshold
        """
        if not self._ensure_model():
            return False

        # Prepare input - audio must be float32, shape (1, samples)
        audio = audio_chunk.astype(np.float32).reshape(1, -1)
        # Sample rate must be shape (1,) or scalar
        sr = np.array([self.sample_rate], dtype=np.int64)

        try:
            # Run inference - Silero VAD v5 uses combined 'state' input
            outs = self.session.run(
                None,
                {'input': audio, 'state': self._state, 'sr': sr}
            )
            # Output: [probability_array, new_state]
            # probability_array may have shape (1,) or (1,1), need to flatten
            prob = float(outs[0].flatten()[0])
            self._state = outs[1]
            # Debug: print probability occasionally
            if self.debug:
                if not hasattr(self, '_debug_count'):
                    self._debug_count = 0
                self._debug_count += 1
                if self._debug_count % 50 == 0:
                    print(f"[DEBUG] VAD prob: {prob:.3f}, threshold: {self.threshold}")
            return prob > self.threshold
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] VAD exception: {e}")
            return False

    def get_speech_prob(self, audio_chunk: np.ndarray) -> float:
        """Get speech probability for audio chunk.

        Args:
            audio_chunk: Audio samples (float32, 16kHz, 512 samples)

        Returns:
            Speech probability (0.0-1.0), or 0.0 on error
        """
        if not self._ensure_model():
            return 0.0

        audio = audio_chunk.astype(np.float32).reshape(1, -1)
        sr = np.array([self.sample_rate], dtype=np.int64)

        try:
            outs = self.session.run(
                None,
                {'input': audio, 'state': self._state, 'sr': sr}
            )
            prob = float(outs[0].flatten()[0])
            self._state = outs[1]
            return prob
        except Exception:
            return 0.0

    def reset(self):
        """Reset state for new audio stream.

        Call this when starting a new recording session to clear
        any state from previous audio.
        """
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def preload(self) -> bool:
        """Preload the model (download if needed).

        Returns:
            True if model is ready, False on error
        """
        return self._ensure_model()
