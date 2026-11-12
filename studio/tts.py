"""Voice-over with Kokoro TTS (Apache-2.0, 82M parameters, runs on CPU).

Install once on the pod (setup_pod.sh does this):
    apt-get install -y espeak-ng && pip install "kokoro>=0.9" soundfile

Voices (lang_code a = American, b = British English), for example:
    am_michael, am_adam, af_heart, af_bella, bm_george, bm_lewis, bf_emma
The full list is on huggingface.co/hexgrad/Kokoro-82M (VOICES.md).
"""
import hashlib
from pathlib import Path

_PIPES = {}
RATE = 24000


def _pipeline(lang, device):
    key = (lang, device)
    if key not in _PIPES:
        try:
            from kokoro import KPipeline
        except ImportError as e:
            raise RuntimeError(
                "Kokoro is not installed. On the pod run:  apt-get install -y "
                "espeak-ng && pip install 'kokoro>=0.9' soundfile") from e
        try:
            _PIPES[key] = KPipeline(lang_code=lang, device=device)
        except TypeError:                       # older kokoro: no device arg
            _PIPES[key] = KPipeline(lang_code=lang)
    return _PIPES[key]


def say(text, out_dir, voice="am_michael", speed=1.0, lang=None,
        device="cpu"):
    """Speak `text` into a WAV file (cached: same text+voice+speed = same
    file). Returns the path."""
    import numpy as np
    import soundfile as sf

    lang = lang or voice[0]                     # 'a' / 'b' from the voice id
    h = hashlib.sha1(f"{voice}|{speed}|{text}".encode()).hexdigest()[:12]
    out = Path(out_dir) / f"{voice}_{h}.wav"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    chunks = [np.asarray(audio, dtype="float32") for _, _, audio in
              _pipeline(lang, device)(text, voice=voice, speed=speed)
              if audio is not None]
    if not chunks:
        raise RuntimeError(f"Kokoro returned no audio for: {text[:80]}")
    pause = np.zeros(int(RATE * 0.12), dtype="float32")
    joined = np.concatenate([x for c in chunks for x in (c, pause)][:-1])
    sf.write(out, joined, RATE)
    return out
