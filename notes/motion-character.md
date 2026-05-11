# Motion character — eye-leads / head-follows

This note documents the third (and currently shipping) architecture
for the v3 gaze coupling demo. The previous two attempts both drove
the eyes from the servo or from a synthetic intention model and felt
mechanical. This one inverts the relationship: the face is the source
of motion; the head follows the eyes.

Earlier attempts are preserved in git history if you want to compare.

## The architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  face_task                                                           │
│      ↓                                                               │
│  robo_eyes_update()                                                  │
│      ↓ (library autonomous behavior)                                 │
│   • autoblinker fires every 3 + rand[0,3] s                          │
│   • idle mode picks new random (x,y) every 1 + rand[0,3] s           │
│   • per-frame (cur+next)/2 tween                                     │
│      ↓                                                               │
│  oled_face_draw_bitmap() + oled_face_flush()                         │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                  ↓
                  robo_eyes_get_eye_x_angle()
                                  ↓
┌──────────────────────────────────────────────────────────────────────┐
│  servo_follow_task                                                   │
│      ↓                                                               │
│   • poll eye angle every 50 ms                                       │
│   • map [-90°, +90°] eye → [-60°, +60°] servo                       │
│   • if |new - last_commanded| > 5°:                                  │
│       servo_move_to(target, 800 ms)                                  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                  ↓
                          SG90 on GPIO 18
```

The library is the prime mover. The follower task just samples eye
state and tells the head where to go.

## Why this is better than the previous attempts

| Previous attempt              | What was wrong                                         |
|-------------------------------|--------------------------------------------------------|
| **Servo sweep + eyes mirror** | Same easing on both, constant motion, no rest          |
| **Intention-first POI model** | Complex (POI manager, dice, VOR, eye-lead); eyes locked to a contrived "target" instead of the library's organic idle motion |

The new model:

- **Matches biology.** Real eyes orient within milliseconds; head
  rotation follows over hundreds of milliseconds. Animating eyes-from-
  head reverses the natural causal order and is visibly wrong even
  when the timing is correct.
- **Leverages the library's existing "alive" defaults.** Upstream
  RoboEyes' autoblinker + idle wander has been tuned by its author
  for natural feel. Re-synthesizing this from scratch (POI dice rolls,
  micro-saccades, blink clusters) was always going to under-perform
  the original.
- **Smaller surface area in our code.** Two new functions in main.c
  (~40 lines) plus one read-only getter in the library (~10 lines).
  No POI manager, no dice, no VOR coefficient, no eye-lead timer, no
  saccade phase, no blink suppression window.
- **Smaller GPL surface.** The library is now near-faithful to
  upstream — one read-only accessor added, no behavioral changes.

## The follower

Implementation: `servo_follow_task` in
[`firmware/main/main.c`](../firmware/main/main.c) (within the
`#if CONFIG_GAZE_COUPLING_DEMO` block).

Polls `robo_eyes_get_eye_x_angle()` every 50 ms. The getter returns
the eye's currently-displayed horizontal angle in degrees, derived
from the library's tweened `eyeLx` value relative to the screen-X
envelope center. Range: [-90°, +90°].

Maps to servo angle via fixed ratio:

```
servo_target = eye_angle × (SERVO_ANGLE_MAX / EYE_ANGLE_MAX)
             = eye_angle × (60 / 90)
             = eye_angle × 0.667
```

Eyes at ±90° (envelope edge) → servo at ±60°. Eyes near center →
servo near center. The narrower servo range makes the head visibly
"lazier" than the eyes — the head doesn't try to follow every micro-
movement.

A 5° threshold prevents the servo from issuing a new command unless
the new target differs meaningfully from the last commanded value.
This keeps the servo quiet during the library's per-frame tween
wobbles (eye position changes by sub-pixel amounts as it eases
toward each idle target) and during blink-related transients.

When a new command is issued, the servo eases over 800 ms (ease-in-
out cubic, courtesy of `servo_move_to`). Slow enough that the head
arriving feels like it's "catching up" to the eyes rather than
moving with them.

## Tuning knobs

Constants live at the top of `servo_follow_task` in main.c:

| Constant | Default | What it does |
|---|---|---|
| `EYE_ANGLE_MAX_DEG` | 90 | Maximum eye angle (matches library's getter range; don't change unless changing the getter too) |
| `SERVO_ANGLE_MAX_DEG` | 60 | Maximum servo angle. Lower = lazier head; raise toward 90 for more committed follow |
| `FOLLOW_THRESHOLD_DEG` | 5 | Minimum eye→target change to trigger a new servo command. Lower = head twitches with micro-movements; higher = head ignores small eye motions |
| `FOLLOW_MOVE_MS` | 800 | How long the servo takes to reach a new target. Lower = snappier; higher = more deliberate |
| `FOLLOW_TICK_MS` | 50 | How often the follower samples. Doesn't need to be fast |

## What the library does (unchanged, fully autonomous)

face_task in main.c calls these on entering `APP_STATE_IDLE` (which
is where the app stays for the duration of this demo):

```c
robo_eyes_set_mood(ROBO_EYES_MOOD_DEFAULT);
robo_eyes_set_idle_mode(true, 2, 3);
```

The `set_autoblinker(true, 3, 3)` call also fires at face_task entry.
That's it — no overrides, no axis masking, no manual position writes.

The library's stock behavior:

- **Autoblinker**: every 3 + rand[0,3] seconds, both eyes close and
  re-open. ~100 ms per blink.
- **Idle wander**: every 1 + rand[0,3] seconds, picks random
  `(eyeLxNext, eyeLyNext)` within the screen envelope (~46×28 px on
  the 128×64 panel), eases toward it via per-frame `(cur+next)/2`.
- **Initial wake**: eyes start closed at boot, animate open in ~100
  ms when face_task calls `robo_eyes_open()` after `begin()`.

These are upstream defaults verbatim. We changed none of them.

## Library divergence summary

After this pivot, `firmware/components/robo_eyes/` diverges from
[github.com/FluxGarage/RoboEyes](https://github.com/FluxGarage/RoboEyes)
V1.1.1 by exactly:

| Divergence | Type | Justification |
|---|---|---|
| C port (vs Arduino C++ template) | Translation, no behavior change | Keeps the firmware ESP-IDF-native, no Arduino-ESP32 component |
| In-memory framebuffer instead of Adafruit_GFX surface | Translation | Same — pixel output is byte-compatible with our SSD1306 driver |
| Sweat-drops feature omitted | Removal | No use case in v3 plan |
| `robo_eyes_get_eye_x_angle()` getter | Additive, read-only | Enables follower architectures without touching motion logic |

Previous extensions (`set_idle_axes`, `set_position_continuous`,
`set_position_x_continuous`) introduced for earlier demo iterations
were removed by this commit. Future upstream syncs are simpler as a
result.

## Optional polish — not yet implemented

> If eyes hold far off-center for >3 s, slowly extend the servo's
> effective range toward that side. If eyes return to center, head
> drifts back. Negotiation between head and eyes rather than rigid
> following.

Not implemented in this baseline. Deferred until the baseline is
confirmed to feel right on real hardware. If the head "always at
60% of eye angle" feels too rigid in practice, this is the next
knob to add — but the baseline alone might be 90% of what's needed.

## Reverting

Disable in menuconfig: **Whisper Stack Device → Gaze coupling**.
The flag defaults `n` and the code is fully gated by
`#if CONFIG_GAZE_COUPLING_DEMO`, so an `=n` build is identical to
the pre-coupling firmware (face renderer running autonomously, no
servo init, no follower task).
