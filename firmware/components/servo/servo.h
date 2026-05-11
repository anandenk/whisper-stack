// SG90-style hobby servo driver — LEDC PWM, ease-in-out targeting, idle
// drift, look-toward-sound reaction, four named postures.
//
// Reusable as an ESP-IDF component. The standalone example at
// firmware/examples/servo_test/ exercises every API; the main firmware
// will pull this in once v3 hardware (S3 + servo neck) lands.
//
// Hardware (v3 prototype, breadboard tonight):
//   * SG90 (or any 50 Hz hobby servo) plastic version from Arduino kit.
//   * VCC -> 5V (USB VIN on the DOIT V1) — DO NOT power from 3V3, the
//     servo stalls.
//   * GND  -> common GND with the ESP32.
//   * SIG  -> SERVO_DEFAULT_GPIO (GPIO 18 unless overridden). 3.3V logic
//     is normally fine on SG90 inputs; if you see jitter at idle or
//     refused commands, that's the rare marginal-trigger case — wire a
//     2N2222 / 2N3904 emitter-follower as a level booster. Documented
//     fully in notes/servo-motion-language.md.

#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

// Angle units: signed degrees, -90 = full left, 0 = center, +90 = full right.
// Internal LEDC duty math clamps to a 1.0 - 2.0 ms pulse window so the
// plastic SG90's mechanical stop isn't hammered.

#define SERVO_ANGLE_MIN_DEG   -90.0f
#define SERVO_ANGLE_MAX_DEG   +90.0f
#define SERVO_DEFAULT_GPIO    18

// Named postures map to a behavior set, not just an angle:
//
//   IDLE       center, small ± random drift every 3-8 s (alive feel)
//   LISTENING  freeze at last commanded angle (no drift); ready to
//              accept servo_look_toward() snaps from the wake handler
//   SPEAKING   small gentle sway ±3-5° every ~1.5 s (engaged feel)
//   SLEEPING   center, no drift, no reaction
typedef enum {
    SERVO_POSTURE_IDLE      = 0,
    SERVO_POSTURE_LISTENING = 1,
    SERVO_POSTURE_SPEAKING  = 2,
    SERVO_POSTURE_SLEEPING  = 3,
} servo_posture_t;

typedef struct {
    int      gpio;                   // SERVO_DEFAULT_GPIO
    uint32_t pulse_min_us;           // 1000 — angle = -90°
    uint32_t pulse_max_us;           // 2000 — angle = +90°
    uint32_t pulse_center_us;        // 1500 — angle =   0°
    uint32_t update_hz;              // 50 — internal interp tick rate
} servo_config_t;

// Sensible defaults. Pass NULL to servo_init() to use these.
servo_config_t servo_default_config(void);

// Bring up LEDC PWM, start the interp + drift tasks, set posture to IDLE.
// Idempotent — a second call is a no-op.
esp_err_t servo_init(const servo_config_t *cfg);

// Move to `angle` (deg) over `duration_ms` along an ease-in-out cubic
// curve. Returns immediately; the move proceeds in the servo task.
// Calling again before the current move finishes retargets — the next
// frame starts from the current interpolated angle, no jump.
void servo_move_to(float angle_deg, uint32_t duration_ms);

// Snap-and-settle reaction: ease-out cubic to 95% of `angle` in 200 ms,
// then slow ease the final 5% over 400 ms. Mimics the way real eyes /
// heads orient toward a sudden stimulus — fast initial commit, gentle
// final placement. Use from a wake-event handler. Overrides any in-
// flight move_to.
void servo_look_toward(float angle_deg);

// Switch to a named posture. Affects drift/sway behavior but does NOT
// move the servo immediately — combine with servo_move_to() if you
// also want to recenter on posture entry.
void servo_set_posture(servo_posture_t p);

// Read-back. Reflects the current commanded (interpolated) angle, not
// the actual physical position (we don't have feedback on an SG90).
float servo_current_angle(void);

// Stop the drift / sway tasks and hold whatever angle is currently
// commanded. Useful for shutdown / power-down without re-jerking.
void servo_freeze(void);

#ifdef __cplusplus
}
#endif
