"""Face preview with a gaze slider and eye-leads-head simulation.

Live tkinter preview that mirrors the existing C-side rendering loop
(state -> per-frame bitmap -> flush) plus the new `gaze` parameter
added to oled_animate.py. Use it to iterate on:

  * gaze offset mapping (degree -> pupil pixel offset)
  * eye-leads-head timing (the eyes snap to the slider immediately;
    a simulated "head angle" follower lags ~150 ms behind, which is
    where the SG90 servo will eventually be commanded from on the v3
    prototype)
  * pose visual checks across all six existing states

NO firmware flash needed; NO regeneration of cat_face.h. Tonight is
software-only iteration on a workstation. Tomorrow's S3 work picks
this up and either pre-renders gaze variants into cat_face.h or adds
a runtime pupil-draw API in oled.c.

Controls:
    1..6        switch state (idle / listening / thinking / speaking
                / error / sleep)
    drag slider gaze in [-90, +90] degrees
    space       reset gaze to 0
    Esc         quit

Usage:
    .venv/Scripts/python.exe tools/face_preview.py
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    sys.exit("Install Pillow: .venv/Scripts/python.exe -m pip install Pillow")

import oled_animate as fa  # noqa: E402  — tests/oled_animate.py


WINDOW_SCALE = 8
TARGET_FPS = 60

# Eye-leads-head: the simulated servo follower lags this many ms behind
# the gaze slider. 150 ms was picked so the eyes "anticipate" the head
# motion the way real character animation does (saccades precede head
# turns by ~100-200 ms in humans). Edit and re-run to feel different
# values; the right number on real hardware depends on the SG90 step
# rate and ease curve and will be tuned on the v3 prototype.
EYE_LEAD_MS = 150


# ---------------------------------------------------------------------------
# State animators — call the pose builders fresh on each frame so the
# current slider gaze applies. Mirrors oled_dump_c.py's ANIMATIONS dict.
# Each entry is a list of (frame_fn, duration_ms) where frame_fn takes
# `gaze` and returns a PIL Image. The function-of-gaze indirection is
# what lets the slider drive the rendered output live.
# ---------------------------------------------------------------------------

FrameFn = Callable[[float | None], Image.Image]


def _idle_open_f(g):     return fa.make_idle_open(gaze=g)
def _idle_squint_f(_g):  return fa.make_idle_squint()
def _idle_closed_f(_g):  return fa.make_idle_closed()
def _listen_q_f(stage):
    return lambda g, s=stage: fa.make_listening(stage=s, gaze=g)
def _listen_blink_f(_g): return fa.make_listening_blink(stage=fa.Q_STAGE_FULL)
def _think_f(n):         return lambda g, k=n: fa.make_thinking(k, gaze=g)
def _speak_omega_f(_g):  return fa.make_speak_omega()
def _speak_o_f(_g):      return fa.make_speak_o()
def _speak_quiet_f(_g):  return fa.make_speak_quiet()
def _error_open_f(_g):   return fa.make_error_open()
def _error_blink_f(_g):  return fa.make_error_blink()
def _sleep_base_f(_g):   return fa.make_sleep_base()
def _sleep_z_f(drift):
    return lambda _g, k=drift: fa.make_sleep_with_z(k)


ANIMATIONS: dict[str, list[tuple[FrameFn, int]]] = {
    "idle": [
        (_idle_open_f,    4000),
        (_idle_squint_f,    50),
        (_idle_closed_f,   100),
        (_idle_squint_f,    50),
    ],
    "listening": [
        (_listen_q_f(0),     250),
        (_listen_q_f(1),     130),
        (_listen_q_f(2),     130),
        (_listen_q_f(3),     130),
        (_listen_q_f(4),     130),
        (_listen_q_f(5),    1500),
        (_listen_blink_f,     80),
        (_listen_q_f(5),     600),
        (_listen_q_f(4),      80),
        (_listen_q_f(3),      80),
        (_listen_q_f(2),      80),
        (_listen_q_f(1),      80),
        (_listen_q_f(0),     250),
    ],
    "thinking": [
        (_think_f(0), 250),
        (_think_f(1), 320),
        (_think_f(2), 320),
        (_think_f(3), 600),
    ],
    "speaking": [
        (_speak_omega_f, 240),
        (_speak_o_f,     240),
        (_speak_omega_f, 240),
        (_speak_quiet_f, 180),
    ],
    "error": [
        (_error_open_f,  1500),
        (_error_blink_f,  100),
        (_error_open_f,   800),
    ],
    "sleep": [
        (_sleep_base_f, 2000),
        *[(_sleep_z_f(d), 220) for d in range(0, 16, 2)],
        (_sleep_base_f, 1500),
    ],
}


# ---------------------------------------------------------------------------
# Eye-leads-head follower. Linear interpolation toward target with a
# fixed-rate update each tick. Time constant tuned so the follower
# reaches the target gaze ~EYE_LEAD_MS after the slider settles.
# ---------------------------------------------------------------------------

class HeadFollower:
    def __init__(self, lag_ms: int = EYE_LEAD_MS) -> None:
        self.lag_ms = lag_ms
        self._cur = 0.0           # current "head" angle (deg)
        self._last_t = time.monotonic()

    def update(self, target_deg: float) -> float:
        now = time.monotonic()
        dt_ms = max(1, int((now - self._last_t) * 1000))
        self._last_t = now
        # exponential approach: cur += (target - cur) * (dt / lag), capped at 1
        alpha = min(1.0, dt_ms / self.lag_ms)
        self._cur += (target_deg - self._cur) * alpha
        return self._cur


# ---------------------------------------------------------------------------
# Render — face on the left, gaze/head indicators on the right.
# ---------------------------------------------------------------------------

def _bitmap_to_photo(bitmap: Image.Image, scale: int) -> ImageTk.PhotoImage:
    L = fa.ACTIVE_LAYOUT
    big = bitmap.resize((L.W * scale, L.H * scale), Image.NEAREST)
    rgb = big.convert("L").point(lambda v: 255 if v else 0).convert("RGB")
    return ImageTk.PhotoImage(rgb)


def main() -> None:
    L = fa.ACTIVE_LAYOUT
    state_name = ["idle"]
    frame_idx = [0]
    next_frame_due_ms = [0]    # monotonic ms when current frame expires
    last_state = [""]

    gaze_target = [0.0]        # slider value, deg
    pupil_on    = [False]      # when False, gaze is None → no pupil (pre-extension look)
    follower = HeadFollower()

    root = tk.Tk()
    root.title("face_preview — gaze + eye-leads-head simulation")
    root.configure(bg="#101010")

    # ---- face canvas ------------------------------------------------------
    face_w = L.W * WINDOW_SCALE
    face_h = L.H * WINDOW_SCALE

    top = tk.Frame(root, bg="#101010")
    top.pack(padx=16, pady=12)

    face_canvas = tk.Canvas(
        top, width=face_w, height=face_h, bg="black", highlightthickness=0
    )
    face_canvas.pack(side="left")
    face_img_id = face_canvas.create_image(0, 0, anchor="nw")
    face_photo_ref: list[ImageTk.PhotoImage | None] = [None]

    # ---- right-side info panel -------------------------------------------
    info = tk.Frame(top, bg="#101010")
    info.pack(side="left", padx=(16, 0), fill="y")

    state_lbl = tk.Label(
        info, text="STATE: IDLE", bg="#101010", fg="#aaffaa",
        font=("Consolas", 14, "bold"),
    )
    state_lbl.pack(anchor="w")

    gaze_val_lbl = tk.Label(
        info, text="gaze:  +0.0 deg", bg="#101010", fg="#ffeeaa",
        font=("Consolas", 12),
    )
    gaze_val_lbl.pack(anchor="w", pady=(8, 0))

    head_val_lbl = tk.Label(
        info, text="head: +0.0 deg (lag 150 ms)", bg="#101010", fg="#88ccff",
        font=("Consolas", 12),
    )
    head_val_lbl.pack(anchor="w")

    diff_lbl = tk.Label(
        info, text="diff: +0.0 deg", bg="#101010", fg="#cccccc",
        font=("Consolas", 11),
    )
    diff_lbl.pack(anchor="w", pady=(4, 0))

    # Simple horizontal bar to visualize gaze + head angles relative to
    # the -90 / +90 range. Eyes (yellow) snap to slider; head (blue)
    # lags. Lets you eyeball the lead/lag timing without staring at
    # numbers.
    bar_w, bar_h = 360, 36
    bar = tk.Canvas(info, width=bar_w, height=bar_h, bg="#202020",
                    highlightthickness=0)
    bar.pack(anchor="w", pady=(10, 0))
    bar.create_line(bar_w // 2, 2, bar_w // 2, bar_h - 2,
                    fill="#444", dash=(2, 2))
    eye_marker = bar.create_polygon(0, 0, 0, 0, 0, 0, fill="#ffd866")
    head_marker = bar.create_polygon(0, 0, 0, 0, 0, 0, fill="#66bbff")
    eye_lbl_txt = bar.create_text(8, 8, anchor="nw", text="eyes",
                                  fill="#ffd866", font=("Consolas", 9))
    head_lbl_txt = bar.create_text(8, 20, anchor="nw", text="head",
                                   fill="#66bbff", font=("Consolas", 9))

    hint_lbl = tk.Label(
        info,
        text=("1 idle  2 listening  3 thinking\n"
              "4 speaking  5 error  6 sleep\n"
              "show-pupil off = pre-extension look\n"
              "drag slider for gaze; space=center; Esc=quit\n"
              "note: gaze only affects round-eye states\n"
              "(idle/listening/thinking)"),
        bg="#101010", fg="#888888", justify="left",
        font=("Consolas", 10),
    )
    hint_lbl.pack(anchor="w", pady=(12, 0))

    # ---- gaze slider ------------------------------------------------------
    slider_frame = tk.Frame(root, bg="#101010")
    slider_frame.pack(fill="x", padx=16, pady=(0, 16))

    # Pupil on/off — when off, the renderer falls back to the
    # pre-extension look (solid filled eye, no pupil). Lets you A/B the
    # additive layer visually. Slider value still tracks; it just
    # doesn't drive the renderer until you tick the box.
    pupil_var = tk.BooleanVar(value=False)
    pupil_check = tk.Checkbutton(
        slider_frame, text="show pupil", variable=pupil_var,
        bg="#101010", fg="#cccccc", selectcolor="#202020",
        activebackground="#101010", activeforeground="#ffffff",
        font=("Consolas", 11),
    )
    pupil_check.pack(side="left")

    def on_pupil(*_):
        pupil_on[0] = bool(pupil_var.get())
    pupil_var.trace_add("write", on_pupil)

    tk.Label(slider_frame, text="  gaze (deg):", bg="#101010",
             fg="#cccccc", font=("Consolas", 11)).pack(side="left")

    slider_var = tk.DoubleVar(value=0.0)
    slider = tk.Scale(
        slider_frame, from_=-90, to=+90, resolution=1, orient="horizontal",
        variable=slider_var, length=520, bg="#202020", fg="#cccccc",
        troughcolor="#303030", highlightthickness=0,
        showvalue=False,
    )
    slider.pack(side="left", padx=(8, 0))

    def on_slider(*_):
        gaze_target[0] = float(slider_var.get())
        # First slider touch implicitly turns the pupil on — saves
        # the "I dragged the slider and nothing happened" reaction.
        if not pupil_on[0]:
            pupil_var.set(True)
    slider_var.trace_add("write", on_slider)

    # ---- state switching --------------------------------------------------
    def set_state(name: str) -> None:
        if name == state_name[0]:
            return
        state_name[0] = name
        frame_idx[0] = 0
        next_frame_due_ms[0] = 0       # force immediate frame draw
        state_lbl.config(text=f"STATE: {name.upper()}")

    for key, name in (
        ("1", "idle"), ("2", "listening"), ("3", "thinking"),
        ("4", "speaking"), ("5", "error"), ("6", "sleep"),
    ):
        root.bind(f"<KeyPress-{key}>", lambda _e, n=name: set_state(n))
    def reset_gaze(_e=None):
        slider_var.set(0.0)
        pupil_var.set(False)
    root.bind("<space>", reset_gaze)
    root.bind("<Escape>", lambda _e: root.destroy())

    # ---- frame loop -------------------------------------------------------
    min_ms = 1000 // TARGET_FPS

    def now_ms() -> int:
        return int(time.monotonic() * 1000)

    def tick() -> None:
        anim = ANIMATIONS[state_name[0]]
        if now_ms() >= next_frame_due_ms[0]:
            # advance to next frame in the state animation
            frame_idx[0] = (frame_idx[0] + 1) % len(anim) if next_frame_due_ms[0] else 0
            _, dur_ms = anim[frame_idx[0]]
            next_frame_due_ms[0] = now_ms() + dur_ms

        fn, _dur = anim[frame_idx[0]]
        slider_gaze = gaze_target[0]
        # Renderer gets None when the user hasn't activated the pupil
        # yet → pre-extension look. Head follower tracks slider_gaze
        # regardless so the head/eyes timing visual stays meaningful
        # in either mode.
        render_gaze = slider_gaze if pupil_on[0] else None
        head = follower.update(slider_gaze)

        # Re-render bitmap with current gaze. For states that don't have
        # round irises (speaking/error/sleep), fn ignores the gaze kwarg.
        bitmap = fn(render_gaze)
        face_photo_ref[0] = _bitmap_to_photo(bitmap, WINDOW_SCALE)
        face_canvas.itemconfig(face_img_id, image=face_photo_ref[0])

        gaze_label = f"{slider_gaze:+6.1f} deg" if pupil_on[0] else "  (off) "
        gaze_val_lbl.config(text=f"gaze: {gaze_label}")
        head_val_lbl.config(text=f"head: {head:+6.1f} deg (lag {EYE_LEAD_MS} ms)")
        diff_lbl.config(text=f"diff: {slider_gaze - head:+6.1f} deg")

        # Update bar markers
        cx_eye = int((slider_gaze + 90) / 180 * bar_w)
        cx_head = int((head + 90) / 180 * bar_w)
        bar.coords(eye_marker,
                   cx_eye - 6, 4,  cx_eye + 6, 4,  cx_eye, 16)
        bar.coords(head_marker,
                   cx_head - 6, bar_h - 4,  cx_head + 6, bar_h - 4,
                   cx_head, bar_h - 16)
        bar.coords(eye_lbl_txt, max(2, cx_eye - 18), 0)
        bar.coords(head_lbl_txt, max(2, cx_head - 18), bar_h - 14)

        root.after(min_ms, tick)

    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
