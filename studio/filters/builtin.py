"""The filters that ship with the studio.

Each one exists because it turns an entry on the "challenging" list into
something reachable: the model generates a clean plate in a style it is
good at, and the look is imposed here, deterministically, where it cannot
drift between frames."""
import numpy as np
from PIL import Image

from . import register


def _img(a):
    return Image.fromarray(a.astype(np.uint8), "RGB")


def _arr(im):
    return np.asarray(im.convert("RGB"), dtype=np.uint8)


# ------------------------------------------------------------- pixelate
@register("pixelate")
class Pixelate:
    """Real pixel art, which a diffusion model cannot produce directly:
    it works in continuous space and smears the grid that is the whole
    point of the style. Downsample and upsample with nearest neighbour
    and the grid is exact."""

    def __init__(self, block=6):
        self.block = max(1, int(block))

    def apply(self, f):
        h, w = f.shape[:2]
        sw, sh = max(1, w // self.block), max(1, h // self.block)
        small = _img(f).resize((sw, sh), Image.NEAREST)
        return _arr(small.resize((w, h), Image.NEAREST))


# ------------------------------------------------------------- quantise
@register("quantise")
class Quantise:
    """Collapse to a fixed palette.

    The palette is computed ONCE from a sample of frames and then reused.
    Quantising each frame independently picks a slightly different palette
    every time and the clip strobes."""

    def __init__(self, colours=24, dither=False):
        self.colours = max(2, int(colours))
        self.dither = bool(dither)
        self.pal = None

    def prime(self, frames):
        if not frames:
            return
        # One tall strip of samples, so the palette sees the whole clip.
        strip = np.concatenate(frames, axis=0)
        self.pal = _img(strip).quantize(
            colors=self.colours, method=Image.MEDIANCUT)

    def apply(self, f):
        if self.pal is None:
            return f
        d = Image.FLOYDSTEINBERG if self.dither else Image.NONE
        return _arr(_img(f).quantize(palette=self.pal, dither=d))


# ---------------------------------------------------------------- grain
@register("grain")
class Grain:
    def __init__(self, amount=8, mono=True, seed=7):
        self.amount = float(amount)
        self.mono = bool(mono)
        self.rng = np.random.default_rng(int(seed))

    def apply(self, f):
        h, w = f.shape[:2]
        shape = (h, w, 1) if self.mono else (h, w, 3)
        n = self.rng.normal(0, self.amount, shape)
        return np.clip(f.astype(np.float32) + n, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- grade
@register("grade")
class Grade:
    """Lift / gamma / gain plus contrast and saturation. Cheap, and the
    fastest way to make ten shots generated hours apart look like one
    film."""

    def __init__(self, contrast=1.0, saturation=1.0,
                 lift=0.0, gamma=1.0, gain=1.0, tint=None):
        self.contrast = float(contrast)
        self.saturation = float(saturation)
        self.lift = float(lift)
        self.gamma = float(gamma)
        self.gain = float(gain)
        self.tint = tint          # e.g. [1.02, 1.0, 0.97] warm

    def apply(self, f):
        x = f.astype(np.float32) / 255.0
        x = (x + self.lift) * self.gain
        x = np.clip(x, 0, 1) ** (1.0 / max(.01, self.gamma))
        x = np.clip((x - .5) * self.contrast + .5, 0, 1)
        if self.saturation != 1.0:
            lum = x @ np.array([.2126, .7152, .0722], np.float32)
            x = np.clip(lum[..., None] +
                        (x - lum[..., None]) * self.saturation, 0, 1)
        if self.tint:
            x = np.clip(x * np.array(self.tint, np.float32), 0, 1)
        return (x * 255).astype(np.uint8)


# ------------------------------------------------------------- halftone
def _screen(h, w, dot, angle):
    """A rotated dot screen as a 0..1 threshold field."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = np.deg2rad(angle)
    u = (xx * np.cos(a) - yy * np.sin(a)) * (np.pi / max(1.0, dot))
    v = (xx * np.sin(a) + yy * np.cos(a)) * (np.pi / max(1.0, dot))
    return (np.sin(u) * np.sin(v) + 1.0) * 0.5


@register("halftone")
class Halftone:
    """Benday dots. Mono by default; `colour=True` screens each channel at
    its own angle, which is what a real four-colour press does."""

    def __init__(self, dot=4, angle=15, colour=False, ink=None):
        self.dot = float(dot)
        self.angle = float(angle)
        self.colour = bool(colour)
        self.ink = ink or [16, 16, 20]
        self._cache = {}

    def _field(self, h, w, angle):
        key = (h, w, angle)
        if key not in self._cache:
            self._cache[key] = _screen(h, w, self.dot, angle)
        return self._cache[key]

    def apply(self, f):
        h, w = f.shape[:2]
        x = f.astype(np.float32) / 255.0
        if self.colour:
            out = np.empty_like(x)
            for i, ang in enumerate((self.angle, self.angle + 30,
                                     self.angle + 60)):
                out[..., i] = (x[..., i] > self._field(h, w, ang))
            return (out * 255).astype(np.uint8)
        lum = x @ np.array([.2126, .7152, .0722], np.float32)
        on = lum > self._field(h, w, self.angle)
        ink = np.array(self.ink, np.float32)
        return np.where(on[..., None], 255.0, ink).astype(np.uint8)


# ------------------------------------------------------------------ riso
@register("riso")
class Riso:
    """Risograph: two or three spot inks, deliberate misregistration and
    paper grain. The misregistration is the look, so a channel drifting is
    a feature rather than a defect."""

    def __init__(self, colours=4, offset=2, grain=10, paper=None):
        self.q = Quantise(colours=colours)
        self.offset = int(offset)
        self.grain = Grain(amount=grain)
        self.paper = np.array(paper or [244, 240, 230], np.float32)

    def prime(self, frames):
        self.q.prime(frames)

    def apply(self, f):
        x = self.q.apply(f).astype(np.float32)
        o = self.offset
        if o:
            x[..., 0] = np.roll(x[..., 0], o, axis=1)
            x[..., 2] = np.roll(x[..., 2], -o, axis=1)
        x = x * 0.88 + self.paper * 0.12          # ink sits on paper
        return self.grain.apply(np.clip(x, 0, 255).astype(np.uint8))


# ------------------------------------------------------------------- vhs
@register("vhs")
class VHS:
    """Chroma bleed, scanlines, head noise. Everything analogue video got
    wrong, reproduced on purpose."""

    def __init__(self, chroma=3, scanline=0.12, noise=6,
                 jitter=1, seed=11):
        self.chroma = int(chroma)
        self.scanline = float(scanline)
        self.noise = float(noise)
        self.jitter = int(jitter)
        self.rng = np.random.default_rng(int(seed))

    def apply(self, f):
        h, w = f.shape[:2]
        x = f.astype(np.float32)
        if self.chroma:
            x[..., 0] = np.roll(x[..., 0], self.chroma, axis=1)
            x[..., 2] = np.roll(x[..., 2], -self.chroma, axis=1)
        if self.jitter:                            # a few lines slip
            rows = self.rng.integers(0, h, size=max(1, h // 60))
            for r in rows:
                x[r] = np.roll(x[r],
                               int(self.rng.integers(-self.jitter * 4,
                                                     self.jitter * 4 + 1)),
                               axis=0)
        if self.scanline:
            mask = np.ones((h, 1, 1), np.float32)
            mask[::2] = 1.0 - self.scanline
            x *= mask
        if self.noise:
            x += self.rng.normal(0, self.noise, (h, w, 1))
        return np.clip(x, 0, 255).astype(np.uint8)


# ----------------------------------------------------------------- bloom
@register("bloom")
class Bloom:
    """Soft halation around highlights. Takes the digital edge off a plate
    without costing detail anywhere else."""

    def __init__(self, threshold=0.75, amount=0.35, radius=12):
        self.threshold = float(threshold)
        self.amount = float(amount)
        self.radius = int(radius)

    def apply(self, f):
        from PIL import ImageFilter
        x = f.astype(np.float32) / 255.0
        lum = x @ np.array([.2126, .7152, .0722], np.float32)
        hi = np.clip((lum - self.threshold) /
                     max(.01, 1 - self.threshold), 0, 1)[..., None] * x
        blur = _arr(_img(hi * 255).filter(
            ImageFilter.GaussianBlur(self.radius))).astype(np.float32) / 255
        return (np.clip(x + blur * self.amount, 0, 1) * 255).astype(np.uint8)
