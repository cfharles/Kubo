"""Local 16 kHz speech recognition and American-English playback. Never logs audio or words."""

from functools import lru_cache
import io
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import wave

from fastapi import HTTPException

RATE = 16_000
MAX_BYTES = 1_000_000
MAX_SECONDS = 30
DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "models" / "vosk-model-small-en-us-0.15"
# The open vocabulary recognizer can drop a single "hint" or hear "hand".
# Keep neighboring sounds and unknown words in this *secondary* decoder:
# a closed command-only grammar would force novel answers into commands.
HINT_FORMS = {"hint", "give me a hint", "hint please", "a hint"}
HINT_GRAMMAR = json.dumps(sorted(HINT_FORMS | {"give me a hand", "hand", "him", "and", "hen", "hit",
                                               "head", "end", "tent", "sand", "it", "in", "thin", "print", "[unk]"}))


@lru_cache(maxsize=1)
def _model(path):
    if not (Path(path) / "am" / "final.mdl").is_file():
        raise HTTPException(503, "Speech recognition needs its English model. Run bash scripts/setup-speech.sh on the Mac.")
    try:
        import vosk
        vosk.SetLogLevel(-1)
        return vosk.Model(str(path))
    except Exception as exc:
        # The Vosk binding raises a plain Exception for an invalid model directory.
        raise HTTPException(503, "Local speech recognition is unavailable. Check the KUBO speech setup on the Mac.") from exc


def transcribe(wav_bytes: bytes) -> dict:
    """One bounded mono PCM clip. No temporary file and no recording history."""
    if not isinstance(wav_bytes, bytes) or not (44 <= len(wav_bytes) <= MAX_BYTES):
        raise HTTPException(422, "Please record a new answer of 30 seconds or less.")
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            count = wav.getnframes()
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, RATE, "NONE"):
                raise ValueError("unsupported format")
            if not 0 < count <= RATE * MAX_SECONDS:
                raise ValueError("invalid duration")
            pcm = wav.readframes(count)
            if len(pcm) != count * 2:
                raise ValueError("truncated")
    except (EOFError, ValueError, wave.Error):
        raise HTTPException(422, "Please record a new 16 kHz mono WAV answer of 30 seconds or less.") from None
    model = _model(os.environ.get("KUBO_VOSK_MODEL") or str(DEFAULT_MODEL))
    from vosk import KaldiRecognizer
    text, confidence = _decode(KaldiRecognizer(model, RATE), pcm)
    unclear_hint = confidence < .65 and (text in HINT_FORMS or text in {"end", "and", "him", "hit", "hand", "hen"})
    if count <= 3 * RATE and (unclear_hint or text in {"", "give me a hand", "a hand"}):
        command, command_confidence = _decode(KaldiRecognizer(model, RATE, HINT_GRAMMAR), pcm)
        minimum = .94 if command == "hint" else .85
        if command in HINT_FORMS and command_confidence >= minimum:
            text, confidence = command, command_confidence
    if not text or not math.isfinite(confidence) or confidence < .65:
        raise HTTPException(422, "I couldn’t hear that clearly. Please try again. Nothing was scored.")
    return {"text": text, "confidence": round(confidence, 3)}


def _decode(decoder, pcm):
    decoder.SetWords(True)
    segments = []
    # Collect complete segments as well as the last partial sentence after a pause.
    for start in range(0, len(pcm), 8000):
        if decoder.AcceptWaveform(pcm[start:start + 8000]):
            segments.append(json.loads(decoder.Result()))
    segments.append(json.loads(decoder.FinalResult()))
    text = " ".join(s["text"] for s in segments if s.get("text")).strip()
    words = [word for segment in segments for word in segment.get("result", [])]
    confidence = sum(w.get("conf", 0) for w in words) / len(words) if words else 0.
    return text, confidence


def speak(approved_text: str) -> bytes:
    """Only call with the saved, checked server prompt. Never speak arbitrary query text."""
    if not isinstance(approved_text, str) or not approved_text.strip() or len(approved_text) > 1400 or "[[" in approved_text:
        raise HTTPException(422, "There is no approved prompt to play.")
    try:
        # macOS say needs a file target. Only outgoing approved tutor audio is
        # written, in an OS-private temporary directory removed before returning.
        with tempfile.TemporaryDirectory(prefix="kubo-prompt-") as tmp:
            aiff, wav = Path(tmp) / "prompt.aiff", Path(tmp) / "prompt.wav"
            subprocess.run(["/usr/bin/say", "-v", "Samantha", "-r", "160", "-o", str(aiff), "-f", "-"],
                           input=approved_text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=25, check=True)
            subprocess.run(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True)
            with wave.open(str(wav), "rb") as result:
                if result.getnframes() == 0:
                    raise ValueError("empty output")
            return wav.read_bytes()
    except (OSError, subprocess.SubprocessError, EOFError, ValueError, wave.Error):
        raise HTTPException(503, "KUBO could not speak. Check that the Samantha voice is available on this Mac, then retry.") from None
