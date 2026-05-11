# Servo motion language

The "character motion" for the v3 prototype is anchored by a single
SG90-style hobby servo that pans the head. The motion vocabulary the
servo speaks is documented here, along with the easing math, idle
drift parameters, and the four named postures.

Implementation: [`firmware/components/servo/`](../firmware/components/servo/).
Standalone demo: [`firmware/examples/servo_test/`](../firmware/examples/servo_test/).

## Hardware (tonight's breadboard build)

| Pin           | Connect                    | Notes                                                                 |
|---------------|----------------------------|-----------------------------------------------------------------------|
| SG90 brown    | GND                        | Common ground with the ESP32.                                         |
| SG90 red      | VIN (5V, USB)              | **Do NOT power from 3V3.** SG90 stalls at < ~4 V; current spikes during stall blow the 3V3 LDO. |
| SG90 yellow   | GPIO 18 (signal)           | Default. Override via `servo_config_t.gpio` if 18 is occupied.        |

Why GPIO 18: free on the DOIT V1 (not used by I²C, I²S, BOOT, IR, or
gesture), no strapping role, easy to reach on the silkscreen.

### 3.3V signal compatibility

SG90's signal input is normally happy with 3.3V logic — the input
threshold is around 2.5 V on most clones, well below the ESP32's
3.3 V high. **However**, a small minority of cheaper SG90 clones
have noisier comparators and exhibit jitter at idle or refuse to
hold position when fed a 3.3 V PWM directly. Symptoms:

- Visible buzzing when commanded to hold a fixed angle.
- Servo refuses to move below a certain duty cycle that *should*
  correspond to a valid angle.
- Position drifts a few degrees over seconds even with constant
  duty.

If you see any of those, add a level-shifter on the signal line:
a 2N2222 or 2N3904 emitter-follower (ESP signal -> base via 1 kΩ,
emitter -> servo signal, collector -> 5 V) lifts the high to ~4.3 V
and resolves it on every clone tested. Document the addition here
if it ends up being needed for tonight's specific unit.

## LEDC config

- Mode: `LEDC_LOW_SPEED_MODE` (LX6 has no high-speed group).
- Timer: `LEDC_TIMER_0`, channel: `LEDC_CHANNEL_0`.
- Frequency: 50 Hz (20 ms period, SG90 spec).
- Resolution: `LEDC_TIMER_14_BIT` (16384 steps -> ~1.2 µs duty resolution at 20 ms period, finer than SG90 cares about).
- Pulse window: 1000 µs (-90°) ... 1500 µs (0°) ... 2000 µs (+90°).
  Adjust `pulse_min_us` / `pulse_max_us` in `servo_config_t` if your
  specific unit's mechanical stops are tighter or looser. SG90 specs
  500–2400 µs in theory, but cheap units bind at the extremes — 1000–2000 µs
  is a safe default.

## Angle convention

- Signed degrees, `[-90, +90]`.
- `-90` = full left (looking left from the device's perspective).
- `0`   = center.
- `+90` = full right.
- Clamped on every public call; out-of-range silently saturates.

## Easing curves

### `servo_move_to(angle, duration_ms)` — ease-in-out cubic

The default move primitive. Smooth start, smooth end. Used for any
non-reactive motion: posture transitions, idle drift pans, gentle
SPEAKING sway.

```
t in [0, 1]
ease_in_out_cubic(t):
  t < 0.5  ->  4 * t^3
  t >= 0.5 ->  1 - ((-2t + 2)^3) / 2
```

Visually: the head starts moving slowly, accelerates through the
middle, decelerates to a stop. No overshoot. Reads as deliberate.

`duration_ms = 0` snaps instantly (used internally when the drift
task wants an immediate angle change without easing).

### `servo_look_toward(angle)` — snap-and-settle

The reaction primitive. Used when something *sudden* happens — a
wake-word fire, an HC-SR04 gesture, or a "look at the speaker" cue.
Total motion takes 600 ms split across two legs:

| Leg                | Duration | Easing            | Covers               |
|--------------------|----------|-------------------|----------------------|
| Snap               | 200 ms   | ease-out cubic    | 0% -> 95% of delta   |
| Settle             | 400 ms   | ease-out cubic    | 95% -> 100% of delta |

Net effect: a fast initial commitment (you can clearly see the
head "decide" to look) followed by a gentle final placement
(no abrupt stop, no overshoot). Mimics how real eyes and heads
orient toward sudden stimulus — *fast initial commit, gentle
final placement*.

Calling `servo_look_toward` while another move is in flight
preempts it. The next interp tick recomputes `move_from_deg` as
the current interpolated angle, so there's no jump.

## Four named postures

`servo_set_posture(p)` switches behavior. It does **not** move the
servo by itself — combine with `servo_move_to` if you also want to
reposition on posture entry.

| Posture       | Behavior                                                              |
|---------------|-----------------------------------------------------------------------|
| `IDLE`        | Random ±5–10° pans every 3–8 s. Duration 600–1000 ms ease-in-out. The alive-feel baseline — without this, an idle device reads as inert. |
| `LISTENING`   | No drift. Servo holds whatever angle it was at, ready to accept `servo_look_toward()` from the wake handler. Drift would compete with the snap-toward-sound reaction. |
| `SPEAKING`    | Gentle ±3–5° sway every ~1.2–1.8 s, 350 ms ease per move. Reads as engaged speech (head bobs slightly as it talks) without being distracting. |
| `SLEEPING`    | No drift. Combine with `servo_move_to(0, 1200)` on entry for a slow ease to center. Stays there until woken. |

The drift task wakes every 250 ms, checks the active posture, and
fires a new move if it's time. Tunables are inline in
[`servo.c`'s `idle_drift_task`](../firmware/components/servo/servo.c):

| Posture   | Magnitude       | Interval                   | Ease duration   |
|-----------|-----------------|----------------------------|-----------------|
| IDLE      | ±5–10°          | 3000–8000 ms (rand)        | 600–1000 ms (rand) |
| SPEAKING  | ±3–5°           | 1200–1800 ms (rand)        | 350 ms          |
| LISTENING | (no drift)      | n/a                        | n/a             |
| SLEEPING  | (no drift)      | n/a                        | n/a             |

Each new drift target is computed *relative* to the current
commanded angle (`here + signed_rand(...)`) so the head wanders
rather than zeroing every cycle. Clamped to ±90° per call.

## Concurrency model

- `servo_init` spawns two FreeRTOS tasks:
  - **`servo_interp`** — 50 Hz tick (matches PWM frequency). Reads
    the current move target and updates LEDC duty. Priority
    `tskIDLE_PRIORITY + 2`.
  - **`servo_drift`** — wakes every 250 ms, decides whether to
    issue a new `servo_move_to`. Priority `tskIDLE_PRIORITY + 1`.
- Communication between callers and the interp task uses C11
  atomics + a monotonic "ticket" counter. A new
  `servo_move_to` / `servo_look_toward` stores the target and
  bumps the ticket; the interp task notices on its next tick
  and snapshots a fresh leg, using `move_from_deg = current_deg`
  so retargeting mid-move doesn't jump.
- `servo_freeze()` sets an atomic flag; the interp task stops
  applying duty (LEDC continues outputting the last value, so the
  servo holds). Idle/sway commands are ignored while frozen.

## Coupling with the face renderer (tomorrow's work)

The face renderer's `gaze` parameter and the servo's commanded
angle are **two axes of the same gesture.** When something
triggers a "look toward X°":

```
oled.set_gaze(X)                # eyes snap immediately (additive layer on whatever state animation is running)
vTaskDelay(150 ms)              # the eye-leads-head gap (notes/gaze-extension.md)
servo_look_toward(X)            # head begins its snap-then-settle
```

The face state (LISTENING / THINKING / SPEAKING / ...) stays in
charge of expression. The gaze parameter is layered on top — it
shifts the pupils within whichever round-eye pose is currently
rendered. The servo follows the eyes.

This decoupling matters: the existing face state machine doesn't
need to know anything about the servo, and the servo doesn't need
to know anything about the face state. They share a target angle
and a timing relationship, that's it.
