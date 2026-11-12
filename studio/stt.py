"""Speech-to-text for the dashboard with faster-whisper (MIT licence).

Install on the pod (setup_pod.sh does this):  pip install faster-whisper
The model downloads on first use. Settings (environment variables):
    STUDIO_STT_MODEL   small (default) | base | medium | large-v3
    STUDIO_STT_DEVICE  cpu (default, keeps the GPU for ComfyUI) | cuda
    STUDIO_STT_LANG    en (default) | de | auto
"""
import importlib.util
import os
import tempfile
import threading

_MODEL = {"m": None}
_LOCK = threading.Lock()

# Words the studio uses, so Whisper spells them right.
HINT = ("Approve. Option two. Retake. New options. Skip. Revise: the music "
        "is too loud, the narration is too long, make the ship larger. "
        "Keyframe, clip, shot s03, ComfyUI, LTX, Kokoro, ACE-Step.")


def available():
    return importlib.util.find_spec("faster_whisper") is not None


def _model():
    with _LOCK:
        if _MODEL["m"] is None:
            from faster_whisper import WhisperModel
            dev = os.environ.get("STUDIO_STT_DEVICE", "cpu")
            _MODEL["m"] = WhisperModel(
                os.environ.get("STUDIO_STT_MODEL", "small"), device=dev,
                compute_type="int8" if dev == "cpu" else "float16")
        return _MODEL["m"]


def transcribe(data, suffix=".webm"):
    """Bytes of a short recording (webm/ogg/mp4/wav) -> text."""
    lang = os.environ.get("STUDIO_STT_LANG", "en")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        path = f.name
    try:
        segs, _ = _model().transcribe(
            path, language=None if lang == "auto" else lang, beam_size=5,
            vad_filter=True, initial_prompt=HINT)
        return " ".join(s.text.strip() for s in segs).strip()
    finally:
        os.unlink(path)
