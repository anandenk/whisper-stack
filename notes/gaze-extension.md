# Face gaze extension

Tonight's prototype work added an optional `gaze` parameter to the
Python face renderer in [`tests/oled_animate.py`](../tests/oled_animate.py)
and a live iteration tool at [`tools/face_preview.py`](../tools/face_preview.py).
The intent is to validate the gaze-mapping math and the eye-leads-head
timing in software before we touch v3 hardware (S3 + neck servo) tomorrow.

This change is strictly additive. The existing face renderer's behavior
when no gaze is passed is byte-for-byte identical to before — every
existing call site (`oled_dump_c.py`, the existing animations, the
historical preview tool) keeps working unchanged.

## What changed

- `tests/oled_animate.py`
  - New helper `_draw_eye_with_pupil(d, cx, cy, r, gaze)`.
  - New constants `PUPIL_R_FRAC`, `GAZE_FULL_DEG`, `GAZE_VERTICAL_DROOP_FRAC`.
  - Added a `gaze: float | None = None` kwarg to the three round-eye
    pose builders that have a visible iris:
    - `make_idle_open(gaze=None)`
    - `make_listening(stage=Q_STAGE_FULL, gaze=None)`
    - `make_thinking(num_dots, gaze=None)`
  - **New module-level API: `set_gaze(angle: float | None)` /
    `get_gaze() -> float | None`.** Stores a module default that any
    pose builder falls back to when no `gaze=` kwarg is passed.
    Default value is `None` → no pupil → byte-identical pre-extension
    rendering. This is the literal `set_gaze()` symbol the v3 plan
    asks for; tomorrow's C-side port mirrors it as
    `oled_set_gaze(angle)`. The kwarg-overrides-module-default
    pattern means the dump pipeline (which never passes a kwarg)
    stays unaffected as long as no upstream code calls `set_gaze`
    before invoking the dump.
  - Squint / closed / happy-arc / X / sleep poses are unchanged —
    they have no round iris to attach a pupil to.
- `tools/face_preview.py` — new live preview with state buttons +
  gaze slider + eye-leads-head visualization + a "show pupil" toggle
  that A/Bs the additive layer (off = pre-extension look, on = pupil
  follows slider).

## What did NOT change

- **`firmware/main/cat_face.h` is not regenerated.** The C-side
  firmware path is byte-identical. Tonight is software-only.
- **`tests/oled_dump_c.py` is unchanged.** It calls the pose builders
  with no `gaze` kwarg, gets the existing solid-disc eyes, and emits
  the same 16 unique frames it always did.
- **The on-device `face_task` is unchanged.** It still walks the
  per-state animation table and `memcpy`s pre-rendered framebuffers.

The S3 port will introduce one of two paths to make gaze visible on
hardware (decision deferred to that session):

1. **Pre-render variants** — quantize gaze into ~9 discrete angles
  (-90, -67.5, -45, -22.5, 0, +22.5, +45, +67.5, +90), regenerate
  `cat_face.h` with all variants, and pick the right framebuffer in
  `face_task` based on a `set_gaze(angle)` C API. Cheapest on-device
  CPU; multiplies flash usage of `cat_face.h` by 9 (still tiny).
2. **Runtime pupil draw** — keep the existing framebuffers, expose
  `oled_face_draw_bitmap_with_gaze(angle, fb)` in `oled.c` that
  applies the inverted-pupil punch directly to the framebuffer
  before flushing. More CPU per flush; one framebuffer set.

## Gaze mapping (degree -> pixel offset)

The pupil is a small inverted ("hole punched") circle drawn on top
of the existing solid-fill eye disc. At `gaze=0` it sits at the eye
center. At `gaze=±90°` it sits at the iris edge.

Constants (`tests/oled_animate.py`):

| Symbol                       | Value | Meaning                                       |
|------------------------------|-------|-----------------------------------------------|
| `GAZE_FULL_DEG`              | 90.0  | Angle that maps to maximum horizontal offset. |
| `PUPIL_R_FRAC`               | 0.28  | Pupil radius as a fraction of eye radius.    |
| `GAZE_VERTICAL_DROOP_FRAC`   | 0.05  | Tiny y-drop at extreme angles, biomimetic.   |

Map:

```
max_dx = eye_r - pupil_r - 1                # keep pupil inside iris
dx     = round(gaze / 90 * max_dx)          # signed, in pixels
dy     = round(abs(gaze) / 90 * eye_r * 0.05)   # always downward
```

On the current 128×64 layout, `eye_r = 12 px`, `pupil_r = 3 px`,
`max_dx = 8 px`, max vertical droop ≈ 1 px. At gaze ±90° the pupil
clears the iris edge cleanly; at gaze 0 it's centered. Values
outside `[-90, +90]` are clamped (`_clamp_gaze`).

The slight vertical droop at extremes is intentional — real eyes
follow a slightly curved track at large horizontal angles, and on
a 64-px-tall display the 1-px drop reads as natural. Setting
`GAZE_VERTICAL_DROOP_FRAC = 0` removes it for a strictly horizontal
mapping if that turns out to read better on the round display.

## Eye-leads-head timing

When the wake handler hears a sound and points the head at the
source, the visual hierarchy matters: **eyes snap first, head
follows.** In real humans, saccades precede head rotation by
~100–200 ms during gaze shifts; mimicking that gap reads as
"alive" rather than "robotic." It also masks the inevitable
SG90 mechanical lag — by the time the servo is mid-move, the
eyes are already pointing at the target, and the brain
interprets the whole gesture as one continuous motion.

Default: `EYE_LEAD_MS = 150` (`tools/face_preview.py`).

The preview's bar at the bottom of the window makes this concrete:
the yellow marker (`eyes`) snaps instantly to the slider value;
the blue marker (`head`) eases toward it with a time constant of
`EYE_LEAD_MS`. Drag the slider quickly and you see the lead
clearly; drag it slowly and the markers stay together.

On the v3 prototype tomorrow, the wiring will be:

```
wake/look-toward event
    -> oled.set_gaze(target)         # immediate, pupil snaps
    -> after 150 ms delay (timer):
       servo_look_toward(target)     # snap 200 ms + settle 400 ms
```

The 150 ms gap is a single FreeRTOS `vTaskDelay` (or an esp_timer
oneshot for non-blocking dispatch from an ISR-adjacent context).

## Sanity check

The Python preview was validated by exercising every pose builder
with `gaze` ∈ {None, -90, -45, -1, 0, 1, 45, 90, +200 (clamps)} and
confirming each returns a `128×64` 1-bit image. The dump pipeline
(`tests/oled_dump_c.py`) was also rerun in-memory; it produces the
same 16 frames as before (no gaze leakage into the auto-generated
`cat_face.h` path).

## Tunables to revisit on real S3 hardware

- `PUPIL_R_FRAC`: 0.28 reads cleanly at 12 px eye radius. On the
  future round display (`ROUND_240` in `oled_animate.py`,
  `eye_r ≈ 31 px`), the same ratio gives a ~9 px pupil which may
  look heavy — try 0.20.
- `GAZE_VERTICAL_DROOP_FRAC`: barely visible at 64 px height. On
  the round display it'll be more obvious; either keep at 0.05 or
  raise to 0.08 once you can see it move.
- `EYE_LEAD_MS`: 150 ms is "standard saccade lead" — anything in
  [100, 250] reads natural. Faster feels twitchy; slower reads as
  disconnected.
