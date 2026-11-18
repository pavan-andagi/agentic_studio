"""Style profiles: the object every downstream agent reads.

A style used to be a sentence handed to the image model, and nothing after
that point knew what the sentence implied. A profile carries the three
failure axes as data instead, so one config edit changes mode selection,
shot length, text routing and post-processing together rather than four
prompts drifting out of step.

The axes, each 0 (does not need it), 1 (partly), 2 (built on it):

  reveal  does the style need content to accumulate over time?
          The model has no memory of what it already drew, so it morphs
          instead of adding.
  glyph   does it need readable letters, numbers or symbols?
  lock    does it need frame-exact precision - a pixel grid, tracked
          footage, repeated linework, a face that stays the same face?

Adding a style is a config block. There is no Python to write."""

AXES = ("reveal", "glyph", "lock")

# Axis thresholds. These are the whole reason the axes are data.
LOCK_SHORT_SHOT = 3        # lock >= 2  -> shots no longer than this
REVEAL_FORCES_FLF2V = 2    # reveal >= this -> bracket the shot
GLYPH_BANS_PLATE_TEXT = 1  # glyph >= this -> text goes to an overlay


class Profile:
    def __init__(self, name, d):
        d = dict(d or {})
        self.name = name
        self.line = (d.get("line") or "").strip()
        self.axes = {a: int((d.get("axes") or {}).get(a, 0)) for a in AXES}
        self.filters = d.get("filters") or []
        self.caption = d.get("caption") or {}
        self.start_hint = d.get("start_hint") or ""
        self._max_shot_s = d.get("max_shot_s")
        self._prefer_mode = d.get("prefer_mode")
        self._force_mode = d.get("force_mode")
        self._text_in_plate = d.get("text_in_plate")

    # -- what the axes decide -------------------------------------------
    @property
    def max_shot_s(self):
        """Identity failure is a function of duration: a face holds for
        two seconds and dissolves by six. A high lock score caps the shot
        regardless of what the config asked for."""
        cap = int(self._max_shot_s or 6)
        if self.axes["lock"] >= 2:
            cap = min(cap, LOCK_SHORT_SHOT)
        return max(1, cap)

    @property
    def forced_mode(self):
        if self._force_mode:
            return self._force_mode
        if self.axes["reveal"] >= REVEAL_FORCES_FLF2V:
            return "flf2v"
        return None

    @property
    def prefer_mode(self):
        return self.forced_mode or self._prefer_mode or "i2v"

    @property
    def text_in_plate(self):
        if self._text_in_plate is not None:
            return bool(self._text_in_plate)
        return self.axes["glyph"] < GLYPH_BANS_PLATE_TEXT

    @property
    def filtered(self):
        """True when the final look is imposed after generation, which
        means the plate is deliberately generated in a different style
        from the finished video."""
        return bool(self.filters)

    # -- what the agents are told ---------------------------------------
    def script_rules(self):
        """Injected into the Script agent prompt."""
        out = []
        if self.line:
            out.append(
                f"VISUAL STYLE, which overrides any other style described "
                f"anywhere else: {self.line} Write every start and end image "
                f"prompt in this style.")
            if self.filtered:
                out.append(
                    "Do NOT describe the finished look of the video in the "
                    "image prompts. The final treatment is applied "
                    "afterwards in code, so the pictures you ask for are "
                    "the plate described above and nothing more.")
        out.append(
            f"Keep every shot to {self.max_shot_s} seconds or less. "
            "This is a limit on how long ONE shot may be, and it is "
            "not a reason to add shots: use the shot count the plan "
            "asks for. If the plan wants shots longer than this "
            "limit, the video comes out shorter - never split a beat "
            "into extra shots, and never invent beats to fill a "
            "planned length.")
        m = self.forced_mode
        if m == "flf2v":
            out.append(
                "EVERY shot must use mode flf2v: give both a start image "
                "prompt and an end image prompt. The model cannot make "
                "content accumulate on its own, so bracket the shot and "
                "let it find the path between the two.")
            if self.start_hint:
                out.append(f"Every start image prompt describes "
                           f"{self.start_hint}.")
        elif self._prefer_mode:
            out.append(f"Prefer mode {self._prefer_mode} unless a shot "
                       f"clearly needs the other.")
        if not self.text_in_plate:
            out.append(
                "NEVER put text, letters, numbers, labels, captions or "
                "credits in an image prompt - this style cannot render "
                "them. Anything that must be read goes in the shot's "
                "overlays list, which is composited afterwards.")
            out.append(
                "For any shot carrying an overlay, set safe_area to the "
                "part of the frame to keep uncluttered (lower-third, top, "
                "center) and compose the image prompt to leave that area "
                "quiet.")
        if self.axes["lock"] >= 2:
            out.append(
                "Avoid shots that need a reflection, a rigid collision, a "
                "crowd of similar figures, or the same face held for a "
                "long time: this style cannot hold them.")
        return "\n".join(out)

    def plate_line(self):
        """The style string the Images agent actually generates with.

        For a filtered style this is NOT the final look. Pixel art is
        generated as clean flat illustration and quantised afterwards,
        because a diffusion model works in continuous space and smears
        the grid that is the entire point."""
        return self.line

    def __repr__(self):
        return (f"<Profile {self.name} axes={self.axes} "
                f"max={self.max_shot_s}s mode={self.prefer_mode} "
                f"filters={len(self.filters)}>")


DEFAULT = Profile("plain", {"line": "", "axes": {}})


def load(cfg, name=None):
    """Resolve a profile from config. Falls back to a permissive default
    so an existing project with no styles block keeps working."""
    styles = dict(cfg.get("styles") or {})
    default = styles.pop("default", None)
    pick = name or default
    if pick and pick in styles:
        return Profile(pick, styles[pick])
    if pick:
        raise ValueError(f"unknown style {pick!r}; have {sorted(styles)}")
    return DEFAULT


def names(cfg):
    return sorted(k for k in (cfg.get("styles") or {}) if k != "default")


def validate(cfg):
    """Check every profile at startup, so a typo fails loudly on line one
    instead of quietly four stages in."""
    from . import filters as F
    problems = []
    for n in names(cfg):
        p = load(cfg, n)
        for a, v in p.axes.items():
            if v not in (0, 1, 2):
                problems.append(f"{n}: axis {a} is {v}, must be 0, 1 or 2")
        for spec in p.filters:
            fn = (spec or {}).get("name")
            if fn not in F.REGISTRY:
                problems.append(f"{n}: unknown filter {fn!r}")
        if not p.line:
            problems.append(f"{n}: no style line")
    return problems
