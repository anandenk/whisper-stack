"""Live OLED animation preview with state-machine simulation.

Six states, each with its own looping animation:
    1 — IDLE       gentle blink every ~4 s
    2 — LISTENING  '?' symbol pops above the head, occasional blink
    3 — THINKING   cloud-shaped thought bubble, dots build "..."
    4 — SPEAKING   happy ^_^ eyes + ω/o mouth pulses (cute meow)
    5 — ERROR      X eyes + tiny frown
    6 — SLEEP      closed eyes + drifting Z

Press the digit to switch states. Esc to quit.

LAYOUT
------
All positions and sizes are derived from a `Layout` object so the same
pose code renders correctly to any target screen — current 128x64
monochrome OLED, future circular display (`ROUND_240`), or any other
canvas. Switch by setting `ACTIVE_LAYOUT` at the top of the file.

The face poses themselves stay identical across layouts; what changes
are the absolute pixel positions and the size of the symbolic indicators
(`?`, thought bubble), which scale via `indicator_scale`.

Usage:
    .venv/Scripts/python.exe tests/oled_animate.py
"""
from __future__ import annotations

import sys
import tkinter as tk
from dataclasses import dataclass, field
from typing import Callable, Iterator

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    sys.exit(
        "Install Pillow:  .venv/Scripts/python.exe -m pip install Pillow"
    )

WINDOW_SCALE = 8        # Tk window pixels per OLED pixel — for visibility
TARGET_FPS = 60


# ===========================================================================
# Layout — all positions are fractions of canvas dimensions; the bubble and
# question-mark glyphs scale via `indicator_scale`. Adjust per target.
# ===========================================================================

@dataclass(frozen=True)
class Layout:
    name: str
    width: int
    height: int
    is_circular: bool = False

    # Eye placement (fractions of canvas)
    eye_x_left:  float = 0.297     # left eye horizontal center
    eye_x_right: float = 0.703     # right eye horizontal center
    eye_y:       float = 0.406     # vertical center of both eyes
    eye_r:       float = 0.188     # eye radius as fraction of height

    # Mouth placement
    mouth_y:     float = 0.781

    # Indicator placement above the head (used by thinking dots, ears, etc.)
    bubble_cx:            float = 0.500   # x center of "above-head" indicators
    bubble_cy:            float = 0.070   # y of the floating-dots row
    indicator_scale:      int   = 1       # bitmap scale for ears, dots, etc.

    # ----- Computed pixel positions -----

    @property
    def W(self) -> int: return self.width
    @property
    def H(self) -> int: return self.height
    @property
    def eye_x_left_px(self)  -> int: return int(self.width  * self.eye_x_left)
    @property
    def eye_x_right_px(self) -> int: return int(self.width  * self.eye_x_right)
    @property
    def eye_y_px(self)       -> int: return int(self.height * self.eye_y)
    @property
    def eye_r_px(self)       -> int: return max(4, int(self.height * self.eye_r))
    @property
    def mouth_x_px(self)     -> int: return self.width // 2
    @property
    def mouth_y_px(self)     -> int: return int(self.height * self.mouth_y)
    @property
    def mouth_w_px(self)     -> int: return max(3, self.eye_r_px * 5 // 12)
    @property
    def mouth_h_px(self)     -> int: return max(2, self.eye_r_px      // 4)
    @property
    def bubble_cx_px(self)   -> int: return int(self.width  * self.bubble_cx)
    @property
    def bubble_cy_px(self)   -> int: return int(self.height * self.bubble_cy)


# ----- Presets -------------------------------------------------------------

# Current 0.96" SSD1306 OLED — 2:1 horizontal monochrome.
OLED_128x64 = Layout(
    name="oled_128x64",
    width=128, height=64,
    indicator_scale=1,
)

# Future round display (e.g. 1.28" GC9A01A or similar). Specs guessed —
# real tuning happens when the actual hardware arrives. Eye positions
# pulled inward and slightly higher to fit inside the visible circle;
# bubble pushed lower so it stays within the circular bezel.
ROUND_240 = Layout(
    name="round_240",
    width=240, height=240,
    is_circular=True,
    eye_x_left=0.32, eye_x_right=0.68,
    eye_y=0.43,
    eye_r=0.13,
    mouth_y=0.72,
    bubble_cy=0.18,
    indicator_scale=3,
)

# CHANGE THIS LINE to retarget the preview to a different screen layout.
ACTIVE_LAYOUT: Layout = OLED_128x64


# ===========================================================================
# Helpers
# ===========================================================================

def _blank() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    L = ACTIVE_LAYOUT
    img = Image.new("1", (L.W, L.H), color=0)
    return img, ImageDraw.Draw(img)


def _filled_circle(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=1)


# ---------------------------------------------------------------------------
# Gaze — optional. Pupils are an *additive* layer on the existing round-eye
# poses (IDLE / LISTENING-open / THINKING). When `gaze` is None (the default
# everywhere — including oled_dump_c.py) the eye renders exactly as before:
# a solid filled disc, no pupil. When `gaze` is a float in [-90, +90], a
# small inverted "pupil hole" is punched inside the disc, shifted along x
# proportional to the angle. -90 = looking far left, 0 = centered,
# +90 = looking far right.
#
# We deliberately do NOT regenerate firmware/main/cat_face.h with these
# variants tonight — the firmware C side stays byte-for-byte the same.
# The S3 port will add a `set_gaze()` C API + the necessary pre-rendered
# bitmap variants (or a runtime draw path); tonight is iteration-only.
#
# Pupil offset is computed as a fraction of `max_pupil_offset_px`, which is
# the eye radius minus the pupil radius — so the pupil always stays
# entirely inside the disc. PUPIL_R_FRAC keeps the pupil readable on the
# 128x64 panel (~3 px at the current eye size).
# ---------------------------------------------------------------------------

PUPIL_R_FRAC = 0.28        # pupil radius as a fraction of eye radius
GAZE_FULL_DEG = 90.0       # angle that maps to max horizontal offset
GAZE_VERTICAL_DROOP_FRAC = 0.05  # tiny y drop at extreme angles, biomimetic


def _clamp_gaze(angle: float | None) -> float | None:
    if angle is None:
        return None
    if angle > GAZE_FULL_DEG:
        return GAZE_FULL_DEG
    if angle < -GAZE_FULL_DEG:
        return -GAZE_FULL_DEG
    return float(angle)


# Module-level fallback. Pose builders prefer the per-call `gaze` kwarg
# when it's given; otherwise they fall back to _DEFAULT_GAZE. This is the
# literal `set_gaze(angle)` API the v3 plan asks for — and the symbol
# tomorrow's C-side port will mirror as `oled_set_gaze(angle)`. The
# kwarg-takes-precedence model means the dump pipeline (which passes
# no kwargs) is unaffected as long as the module default stays None,
# which it does by default.
_DEFAULT_GAZE: float | None = None


def set_gaze(angle: float | None) -> None:
    """Set the module-default gaze applied by any pose builder that
    doesn't receive an explicit `gaze=` kwarg. Pass None to disable
    the pupil layer entirely (default — restores pre-extension look)."""
    global _DEFAULT_GAZE
    _DEFAULT_GAZE = _clamp_gaze(angle)


def get_gaze() -> float | None:
    """Inspect the current module-default gaze."""
    return _DEFAULT_GAZE


def _resolve_gaze(arg: float | None) -> float | None:
    """Pose-builder helper: kwarg wins; otherwise fall back to module default."""
    return _clamp_gaze(arg) if arg is not None else _DEFAULT_GAZE


def _draw_eye_with_pupil(
    d: ImageDraw.ImageDraw, cx: int, cy: int, r: int, gaze: float | None,
) -> None:
    """Filled-disc eye. If `gaze` is not None, punches an inverted pupil
    hole offset along x by `gaze / GAZE_FULL_DEG * (r - pupil_r)`."""
    _filled_circle(d, cx, cy, r)
    if gaze is None:
        return
    pr = max(2, int(round(r * PUPIL_R_FRAC)))
    max_dx = r - pr - 1                      # keep pupil fully inside the iris
    dx = int(round(gaze / GAZE_FULL_DEG * max_dx))
    dy = int(round(abs(gaze) / GAZE_FULL_DEG * r * GAZE_VERTICAL_DROOP_FRAC))
    px, py = cx + dx, cy + dy
    d.ellipse((px - pr, py - pr, px + pr, py + pr), fill=0)


def _mouth_dot(d: ImageDraw.ImageDraw, *, dy: int = 0) -> None:
    L = ACTIVE_LAYOUT
    mx, my = L.mouth_x_px, L.mouth_y_px + dy
    w, h = L.mouth_w_px, L.mouth_h_px
    d.rectangle((mx - w // 2, my, mx + w // 2, my + h - 1), fill=1)


def _draw_bitmap_scaled(
    d: ImageDraw.ImageDraw, x: int, y: int,
    pattern: tuple[str, ...], scale: int,
) -> None:
    """Stamp a string-art bitmap with each glyph-pixel rendered as a scale×scale block."""
    for dy, row in enumerate(pattern):
        for dx, c in enumerate(row):
            if c == "#":
                px = x + dx * scale
                py = y + dy * scale
                d.rectangle((px, py, px + scale - 1, py + scale - 1), fill=1)


# ===========================================================================
# IDLE — relaxed, gentle blink loop
# ===========================================================================

def make_idle_open(gaze: float | None = None) -> Image.Image:
    L = ACTIVE_LAYOUT
    g = _resolve_gaze(gaze)
    img, d = _blank()
    _draw_eye_with_pupil(d, L.eye_x_left_px,  L.eye_y_px, L.eye_r_px, g)
    _draw_eye_with_pupil(d, L.eye_x_right_px, L.eye_y_px, L.eye_r_px, g)
    _mouth_dot(d)
    return img


def make_idle_squint() -> Image.Image:
    L = ACTIVE_LAYOUT
    img, d = _blank()
    h = max(2, L.eye_r_px // 3)
    for cx in (L.eye_x_left_px, L.eye_x_right_px):
        d.ellipse((cx - L.eye_r_px, L.eye_y_px - h,
                   cx + L.eye_r_px, L.eye_y_px + h), fill=1)
    _mouth_dot(d)
    return img


def make_idle_closed() -> Image.Image:
    L = ACTIVE_LAYOUT
    img, d = _blank()
    h = max(1, L.eye_r_px // 12)
    for cx in (L.eye_x_left_px, L.eye_x_right_px):
        d.rectangle((cx - L.eye_r_px, L.eye_y_px - h,
                     cx + L.eye_r_px, L.eye_y_px + h), fill=1)
    _mouth_dot(d)
    return img


# ===========================================================================
# LISTENING — IDLE face + a question mark that traces itself out stroke by
# stroke above the right eye, holds, erases in reverse, redraws. The drawing
# animation is the state cue: looks like the cat is actively sketching out
# "?" as it tries to parse what you said. Hand-drawn pixel art, six stages
# (blank → top → hook → curve → post → full).
# ===========================================================================

# Each stage is a 9-wide × 10-tall bitmap. Each subsequent stage adds more
# pixels in the order a person would draw a "?" — the top horizontal first,
# the right hook, curving down, the vertical stem, and finally the dot.
_Q_STAGES: tuple[tuple[str, ...], ...] = (
    # 0 — blank
    (
        ".........", ".........", ".........", ".........", ".........",
        ".........", ".........", ".........", ".........", ".........",
    ),
    # 1 — top horizontal stroke
    (
        "..####...", ".........", ".........", ".........", ".........",
        ".........", ".........", ".........", ".........", ".........",
    ),
    # 2 — top stroke + hook curling down on the right
    (
        "..####...", ".##..##..", "......##.", ".........", ".........",
        ".........", ".........", ".........", ".........", ".........",
    ),
    # 3 — hook curving back down toward the stem
    (
        "..####...", ".##..##..", "......##.", ".....##..", "....##...",
        ".........", ".........", ".........", ".........", ".........",
    ),
    # 4 — full curve + vertical stem (still missing the dot)
    (
        "..####...", ".##..##..", "......##.", ".....##..", "....##...",
        "....##...", "....##...", ".........", ".........", ".........",
    ),
    # 5 — full question mark including the dot below
    (
        "..####...", ".##..##..", "......##.", ".....##..", "....##...",
        "....##...", "....##...", ".........", "....##...", "....##...",
    ),
)
Q_STAGE_FULL = len(_Q_STAGES) - 1


def _question_position() -> tuple[int, int]:
    """Top-left of the '?' bitmap — just past the right eye, near the top."""
    L = ACTIVE_LAYOUT
    qx = L.eye_x_right_px + L.eye_r_px + 1
    qy = max(1, int(L.height * 0.03))
    return qx, qy


def _draw_question_stage(d: ImageDraw.ImageDraw, stage: int) -> None:
    if not (0 <= stage < len(_Q_STAGES)):
        return
    L = ACTIVE_LAYOUT
    qx, qy = _question_position()
    _draw_bitmap_scaled(d, qx, qy, _Q_STAGES[stage], L.indicator_scale)


def make_listening(stage: int = Q_STAGE_FULL, gaze: float | None = None) -> Image.Image:
    """stage: 0..5, where 0 is blank and 5 is the complete '?'."""
    L = ACTIVE_LAYOUT
    g = _resolve_gaze(gaze)
    img, d = _blank()
    _draw_eye_with_pupil(d, L.eye_x_left_px,  L.eye_y_px, L.eye_r_px, g)
    _draw_eye_with_pupil(d, L.eye_x_right_px, L.eye_y_px, L.eye_r_px, g)
    _mouth_dot(d)
    _draw_question_stage(d, stage)
    return img


def make_listening_blink(stage: int = Q_STAGE_FULL) -> Image.Image:
    """Mid-blink with whatever amount of '?' is currently visible."""
    L = ACTIVE_LAYOUT
    img, d = _blank()
    h = max(2, L.eye_r_px // 3)
    for cx in (L.eye_x_left_px, L.eye_x_right_px):
        d.ellipse((cx - L.eye_r_px, L.eye_y_px - h,
                   cx + L.eye_r_px, L.eye_y_px + h), fill=1)
    _mouth_dot(d)
    _draw_question_stage(d, stage)
    return img


# ===========================================================================
# THINKING — IDLE eyes + three floating dots above the head, no bubble.
# Universal comic-strip "thinking" cue. Dots build .  →  ..  →  ...  → rest.
# Hand-positioned, no procedural shape generation — what you see is what
# renders.
# ===========================================================================

THINKING_DOT_SPACING = 10   # px between dot centers (at scale=1)


def _draw_thinking_dots(d: ImageDraw.ImageDraw, num_visible: int) -> None:
    L = ACTIVE_LAYOUT
    s = L.indicator_scale
    cx0 = L.bubble_cx_px           # row of dots horizontally centered above face
    cy  = L.bubble_cy_px           # row's vertical position
    spacing = THINKING_DOT_SPACING * s
    half = max(1, s)               # dot half-size in px
    # Dots appear left → middle → right in sequence.
    for i in range(num_visible):
        x = cx0 + (i - 1) * spacing
        d.rectangle((x - half, cy - half, x + half, cy + half), fill=1)


def make_thinking(num_dots: int, gaze: float | None = None) -> Image.Image:
    L = ACTIVE_LAYOUT
    g = _resolve_gaze(gaze)
    img, d = _blank()
    _draw_eye_with_pupil(d, L.eye_x_left_px,  L.eye_y_px, L.eye_r_px, g)
    _draw_eye_with_pupil(d, L.eye_x_right_px, L.eye_y_px, L.eye_r_px, g)
    _mouth_dot(d)
    _draw_thinking_dots(d, num_dots)
    return img


# ===========================================================================
# SPEAKING — happy ^_^ eyes + alternating ω / o / quiet mouth
# ===========================================================================

def _draw_happy_eye(d: ImageDraw.ImageDraw, cx: int) -> None:
    L = ACTIVE_LAYOUT
    r = max(6, int(L.eye_r_px * 0.85))
    points = [
        (cx - r,        L.eye_y_px + 2),
        (cx - r // 2,   L.eye_y_px - 1),
        (cx,            L.eye_y_px - r // 2 - 1),
        (cx + r // 2,   L.eye_y_px - 1),
        (cx + r,        L.eye_y_px + 2),
    ]
    d.line(points, fill=1, width=max(2, L.eye_r_px // 6))


def _draw_omega_mouth(d: ImageDraw.ImageDraw) -> None:
    L = ACTIVE_LAYOUT
    s = max(1, L.eye_r_px // 12)
    mx, my = L.mouth_x_px, L.mouth_y_px + s
    pixels = [
        (-4,  1), (-3,  0), (-2, -1), (-1,  0),
        ( 0,  0),
        ( 1,  0), ( 2, -1), ( 3,  0), ( 4,  1),
    ]
    for dx, dy in pixels:
        px = mx + dx * s
        py = my + dy * s
        d.rectangle((px, py, px + s - 1, py + s - 1), fill=1)


def _draw_o_mouth(d: ImageDraw.ImageDraw) -> None:
    L = ACTIVE_LAYOUT
    s = max(1, L.eye_r_px // 12)
    mx, my = L.mouth_x_px, L.mouth_y_px + s
    d.ellipse((mx - 2 * s, my - 2 * s,
               mx + 2 * s, my + 2 * s), fill=1)
    d.ellipse((mx - 1 * s, my - 1 * s,
               mx + 1 * s, my + 1 * s), fill=0)


def make_speak_omega() -> Image.Image:
    L = ACTIVE_LAYOUT
    img, d = _blank()
    _draw_happy_eye(d, L.eye_x_left_px)
    _draw_happy_eye(d, L.eye_x_right_px)
    _draw_omega_mouth(d)
    return img


def make_speak_o() -> Image.Image:
    L = ACTIVE_LAYOUT
    img, d = _blank()
    _draw_happy_eye(d, L.eye_x_left_px)
    _draw_happy_eye(d, L.eye_x_right_px)
    _draw_o_mouth(d)
    return img


def make_speak_quiet() -> Image.Image:
    L = ACTIVE_LAYOUT
    img, d = _blank()
    _draw_happy_eye(d, L.eye_x_left_px)
    _draw_happy_eye(d, L.eye_x_right_px)
    _mouth_dot(d)
    return img


# ===========================================================================
# ERROR — X eyes + tiny frown
# ===========================================================================

def _draw_x_eye(d: ImageDraw.ImageDraw, cx: int) -> None:
    L = ACTIVE_LAYOUT
    s = max(4, int(L.eye_r_px * 0.7))
    w = max(2, L.eye_r_px // 6)
    cy = L.eye_y_px
    d.line((cx - s, cy - s, cx + s, cy + s), fill=1, width=w)
    d.line((cx + s, cy - s, cx - s, cy + s), fill=1, width=w)


def _draw_frown(d: ImageDraw.ImageDraw) -> None:
    L = ACTIVE_LAYOUT
    mx = L.mouth_x_px
    my = L.mouth_y_px + 2
    arm = max(3, L.mouth_w_px - 1)
    drop = max(2, L.mouth_h_px - 1)
    d.line([(mx - arm, my), (mx, my + drop), (mx + arm, my)], fill=1, width=1)


def make_error_open() -> Image.Image:
    L = ACTIVE_LAYOUT
    img, d = _blank()
    _draw_x_eye(d, L.eye_x_left_px)
    _draw_x_eye(d, L.eye_x_right_px)
    _draw_frown(d)
    return img


def make_error_blink() -> Image.Image:
    L = ACTIVE_LAYOUT
    img, d = _blank()
    s = max(3, int(L.eye_r_px * 0.6))
    w = max(2, L.eye_r_px // 6)
    for cx in (L.eye_x_left_px, L.eye_x_right_px):
        d.line((cx - s, L.eye_y_px, cx + s, L.eye_y_px), fill=1, width=w)
    _draw_frown(d)
    return img


# ===========================================================================
# SLEEP — closed eyes + drifting Z
# ===========================================================================

def _draw_closed_eyes(d: ImageDraw.ImageDraw) -> None:
    L = ACTIVE_LAYOUT
    s = max(6, int(L.eye_r_px * 0.85))
    h = max(1, L.eye_r_px // 12)
    for cx in (L.eye_x_left_px, L.eye_x_right_px):
        d.rectangle((cx - s, L.eye_y_px,
                     cx + s, L.eye_y_px + h), fill=1)


def make_sleep_base() -> Image.Image:
    img, d = _blank()
    _draw_closed_eyes(d)
    _mouth_dot(d)
    return img


def make_sleep_with_z(z_drift: int) -> Image.Image:
    """z_drift: 0..N rises and shifts the Z to suggest it's floating up."""
    L = ACTIVE_LAYOUT
    img, d = _blank()
    _draw_closed_eyes(d)
    _mouth_dot(d)
    s = L.indicator_scale
    z_size = 4 * s
    # Anchor near the upper-right corner; drift up and slightly right.
    zx0 = int(L.W * 0.80)
    zy0 = int(L.H * 0.28)
    zx = zx0 + (z_drift // 2) * s
    zy = zy0 - z_drift * s
    if zy >= 0:
        line_w = max(1, s)
        d.line((zx,        zy,        zx + z_size, zy),         fill=1, width=line_w)
        d.line((zx + z_size, zy,      zx,        zy + z_size),  fill=1, width=line_w)
        d.line((zx,        zy + z_size, zx + z_size, zy + z_size), fill=1, width=line_w)
    return img


# ===========================================================================
# State animation generators — each yields (bitmap, ms) forever.
# ===========================================================================

def idle_anim() -> Iterator[tuple[Image.Image, int]]:
    while True:
        yield make_idle_open(),   4000
        yield make_idle_squint(),   50
        yield make_idle_closed(),  100
        yield make_idle_squint(),   50


def listening_anim() -> Iterator[tuple[Image.Image, int]]:
    """Trace '?' forward stroke-by-stroke, hold complete, erase in reverse,
    pause, repeat. ~3.5 s cycle."""
    DRAW_MS = 130
    ERASE_MS = 80
    while True:
        # Pause on blank (about to start drawing).
        yield make_listening(stage=0), 250
        # Trace forward — each new stage adds one more stroke.
        for stage in range(1, Q_STAGE_FULL + 1):
            yield make_listening(stage=stage), DRAW_MS
        # Hold complete.
        yield make_listening(stage=Q_STAGE_FULL), 1500
        # Quick blink with the full '?' showing — keeps the cat feeling alive.
        yield make_listening_blink(stage=Q_STAGE_FULL), 80
        yield make_listening(stage=Q_STAGE_FULL), 600
        # Erase in reverse — same shape disappearing stroke by stroke.
        for stage in range(Q_STAGE_FULL - 1, -1, -1):
            yield make_listening(stage=stage), ERASE_MS


def thinking_anim() -> Iterator[tuple[Image.Image, int]]:
    while True:
        yield make_thinking(0), 250
        yield make_thinking(1), 320
        yield make_thinking(2), 320
        yield make_thinking(3), 600


def speaking_anim() -> Iterator[tuple[Image.Image, int]]:
    while True:
        yield make_speak_omega(), 240
        yield make_speak_o(),     240
        yield make_speak_omega(), 240
        yield make_speak_quiet(), 180


def error_anim() -> Iterator[tuple[Image.Image, int]]:
    while True:
        yield make_error_open(),  1500
        yield make_error_blink(),  100
        yield make_error_open(),   800


def sleep_anim() -> Iterator[tuple[Image.Image, int]]:
    while True:
        yield make_sleep_base(), 2000
        for drift in range(0, 16, 2):
            yield make_sleep_with_z(drift), 220
        yield make_sleep_base(), 1500


STATES: dict[str, Callable[[], Iterator[tuple[Image.Image, int]]]] = {
    "idle":      idle_anim,
    "listening": listening_anim,
    "thinking":  thinking_anim,
    "speaking":  speaking_anim,
    "error":     error_anim,
    "sleep":     sleep_anim,
}


# ===========================================================================
# Tkinter app
# ===========================================================================

def _bitmap_to_photo(bitmap: Image.Image) -> ImageTk.PhotoImage:
    L = ACTIVE_LAYOUT
    big = bitmap.resize((L.W * WINDOW_SCALE, L.H * WINDOW_SCALE), Image.NEAREST)
    rgb = big.convert("L").point(lambda v: 255 if v else 0).convert("RGB")
    return ImageTk.PhotoImage(rgb)


def main() -> None:
    L = ACTIVE_LAYOUT
    state_name: list[str] = ["idle"]
    gen: list[Iterator[tuple[Image.Image, int]]] = [STATES["idle"]()]

    root = tk.Tk()
    root.configure(bg="#101010")
    canvas = tk.Canvas(
        root,
        width=L.W * WINDOW_SCALE,
        height=L.H * WINDOW_SCALE,
        bg="black",
        highlightthickness=0,
    )
    canvas.pack(padx=20, pady=20)
    img_id = canvas.create_image(0, 0, anchor="nw")
    photo_ref: list[ImageTk.PhotoImage | None] = [None]

    def update_title() -> None:
        root.title(
            f"OLED [{L.name}] — state: {state_name[0].upper()}   "
            f"(1=idle 2=listen 3=think 4=speak 5=error 6=sleep, Esc=quit)"
        )

    def set_state(name: str) -> None:
        state_name[0] = name
        gen[0] = STATES[name]()
        update_title()

    update_title()

    for key, name in (
        ("1", "idle"), ("2", "listening"), ("3", "thinking"),
        ("4", "speaking"), ("5", "error"), ("6", "sleep"),
    ):
        root.bind(f"<KeyPress-{key}>", lambda _e, n=name: set_state(n))
    root.bind("<Escape>", lambda _e: root.destroy())

    min_ms = 1000 // TARGET_FPS

    def tick() -> None:
        bitmap, dur = next(gen[0])
        photo_ref[0] = _bitmap_to_photo(bitmap)
        canvas.itemconfig(img_id, image=photo_ref[0])
        root.after(max(dur, min_ms), tick)

    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
