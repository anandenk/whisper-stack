// SG90 servo driver — implementation. See servo.h for the API contract
// and notes/servo-motion-language.md for the easing-curve + drift design.

#include "servo.h"

#include <stdatomic.h>
#include <stdlib.h>
#include <math.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/ledc.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"

static const char *TAG = "servo";

// LEDC config. 50 Hz is the SG90 spec. 14-bit duty resolution at 50 Hz
// requires CLK = APB (80 MHz) — at 50 Hz, max divider for 14-bit is
// 80e6 / (50 * 2^14) ~= 97.7, well within the 18-bit divider's range.
// 14-bit gives ~0.0061 ms duty resolution at 20 ms period, i.e. ~6 µs —
// finer than the SG90 cares about.
#define SERVO_LEDC_MODE        LEDC_LOW_SPEED_MODE
#define SERVO_LEDC_TIMER       LEDC_TIMER_0
#define SERVO_LEDC_CHANNEL     LEDC_CHANNEL_0
#define SERVO_LEDC_RESOLUTION  LEDC_TIMER_14_BIT
#define SERVO_LEDC_FREQ_HZ     50
#define SERVO_PERIOD_US        20000           // 1 / 50 Hz = 20 ms

// Motion bookkeeping.
typedef struct {
    // Current interpolated angle (deg). Source of truth for the LEDC duty.
    _Atomic float current_deg;
    // Move target + timing (set by servo_move_to / servo_look_toward).
    // Held under a coarse "ticket" counter: when a new command lands,
    // _Atomic_fetch_add bumps the ticket and the interp task notices.
    _Atomic uint32_t cmd_ticket;
    // Snapshot used by the interp loop — only the task reads these so
    // no atomics needed. Updated from current_deg + the new target each
    // time it sees a fresh ticket.
    float    move_from_deg;
    float    move_to_deg;
    int64_t  move_start_us;
    uint32_t move_duration_ms;
    bool     move_is_snap_settle;          // servo_look_toward path
    uint32_t last_seen_ticket;
    // Pending command, written by callers.
    _Atomic float    pending_target_deg;
    _Atomic uint32_t pending_duration_ms;
    _Atomic bool     pending_is_snap_settle;
} motion_t;

static motion_t        s_motion;
static servo_config_t  s_cfg;
static bool            s_inited;
static _Atomic int     s_posture;            // servo_posture_t
static _Atomic bool    s_frozen;
static TaskHandle_t    s_interp_task;
static TaskHandle_t    s_idle_task;

// ---- helpers --------------------------------------------------------------

static float clampf(float v, float lo, float hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

static uint32_t angle_to_duty(float angle_deg)
{
    angle_deg = clampf(angle_deg, SERVO_ANGLE_MIN_DEG, SERVO_ANGLE_MAX_DEG);
    // Linear map: -90° -> pulse_min_us, 0° -> pulse_center_us, +90° -> pulse_max_us
    float frac = (angle_deg - SERVO_ANGLE_MIN_DEG) /
                 (SERVO_ANGLE_MAX_DEG - SERVO_ANGLE_MIN_DEG);
    float pulse_us = (float)s_cfg.pulse_min_us +
                     frac * (float)(s_cfg.pulse_max_us - s_cfg.pulse_min_us);
    float duty_frac = pulse_us / (float)SERVO_PERIOD_US;
    uint32_t max_duty = (1u << SERVO_LEDC_RESOLUTION) - 1u;
    uint32_t duty = (uint32_t)(duty_frac * (float)max_duty + 0.5f);
    if (duty > max_duty) duty = max_duty;
    return duty;
}

static void apply_angle(float angle_deg)
{
    atomic_store(&s_motion.current_deg, angle_deg);
    uint32_t duty = angle_to_duty(angle_deg);
    ledc_set_duty(SERVO_LEDC_MODE, SERVO_LEDC_CHANNEL, duty);
    ledc_update_duty(SERVO_LEDC_MODE, SERVO_LEDC_CHANNEL);
}

// Ease-in-out cubic, t in [0, 1]. Smooth start, smooth end.
static float ease_in_out_cubic(float t)
{
    if (t < 0.0f) return 0.0f;
    if (t > 1.0f) return 1.0f;
    if (t < 0.5f) return 4.0f * t * t * t;
    float p = -2.0f * t + 2.0f;
    return 1.0f - (p * p * p) / 2.0f;
}

// Ease-out cubic — fast initial, gentle finish. Used by snap_settle for
// both legs (the lengths and target offsets differ).
static float ease_out_cubic(float t)
{
    if (t < 0.0f) return 0.0f;
    if (t > 1.0f) return 1.0f;
    float p = 1.0f - t;
    return 1.0f - p * p * p;
}

// snap_settle progress curve. First 200 ms: ease-out cubic to 95% of
// total delta. Next 400 ms: ease the final 5% with another ease-out
// cubic (slower because the remaining delta is smaller).
//
// Returns fraction-of-total-delta covered at elapsed time `t_ms`.
#define SNAP_MS         200
#define SETTLE_MS       400
#define SNAP_PCT        0.95f

static float snap_settle_progress(uint32_t t_ms)
{
    if (t_ms >= (SNAP_MS + SETTLE_MS)) return 1.0f;
    if (t_ms < SNAP_MS) {
        float t = (float)t_ms / (float)SNAP_MS;
        return ease_out_cubic(t) * SNAP_PCT;
    }
    float t = (float)(t_ms - SNAP_MS) / (float)SETTLE_MS;
    return SNAP_PCT + ease_out_cubic(t) * (1.0f - SNAP_PCT);
}

// Pick up a pending command, if any. Snapshots it into the task-local
// motion fields so the interp loop has a stable view for this leg.
static void maybe_pick_up_new_command(void)
{
    uint32_t t = atomic_load(&s_motion.cmd_ticket);
    if (t == s_motion.last_seen_ticket) return;
    s_motion.last_seen_ticket = t;

    s_motion.move_from_deg     = atomic_load(&s_motion.current_deg);
    s_motion.move_to_deg       = atomic_load(&s_motion.pending_target_deg);
    s_motion.move_duration_ms  = atomic_load(&s_motion.pending_duration_ms);
    s_motion.move_is_snap_settle = atomic_load(&s_motion.pending_is_snap_settle);
    s_motion.move_start_us     = esp_timer_get_time();
    if (s_motion.move_duration_ms == 0) {
        // Instant snap — apply now and short-circuit the rest of the
        // tick by aligning start = -duration so the next iteration's
        // elapsed is past end.
        apply_angle(s_motion.move_to_deg);
        s_motion.move_from_deg = s_motion.move_to_deg;
    }
}

// ---- interp task (~50 Hz) -------------------------------------------------

static void interp_task(void *arg)
{
    (void)arg;
    // Tick at the configured update rate. Originally tried vTaskDelayUntil
    // here but it has a documented failure mode where the task can miss
    // its deadline and never block, starving IDLE on this core and
    // tripping the task watchdog. vTaskDelay always yields for the
    // requested duration regardless of timing math — same pacing,
    // strictly more robust.
    TickType_t tick_period =
        pdMS_TO_TICKS(1000 / (s_cfg.update_hz ? s_cfg.update_hz : 50));
    if (tick_period == 0) tick_period = 1;

    while (1) {
        maybe_pick_up_new_command();

        if (!atomic_load(&s_frozen)) {
            int64_t now = esp_timer_get_time();
            uint32_t elapsed_ms = (uint32_t)((now - s_motion.move_start_us) / 1000);

            float t;
            if (s_motion.move_is_snap_settle) {
                t = snap_settle_progress(elapsed_ms);
            } else if (s_motion.move_duration_ms == 0) {
                t = 1.0f;
            } else if (elapsed_ms >= s_motion.move_duration_ms) {
                t = 1.0f;
            } else {
                float lin = (float)elapsed_ms / (float)s_motion.move_duration_ms;
                t = ease_in_out_cubic(lin);
            }

            float ang = s_motion.move_from_deg +
                        (s_motion.move_to_deg - s_motion.move_from_deg) * t;
            apply_angle(ang);
        }

        vTaskDelay(tick_period);
    }
}

// ---- idle drift / posture-based motion task -------------------------------
//
// Wakes up every ~250 ms, decides whether to push a new drift command
// based on the active posture. IDLE: random ±5-10° pans every 3-8 s.
// SPEAKING: gentle ±3-5° sway every ~1.5 s. LISTENING / SLEEPING: no-op.

static uint32_t rand_in_range(uint32_t lo, uint32_t hi)
{
    if (hi <= lo) return lo;
    return lo + (esp_random() % (hi - lo + 1));
}

static float signed_rand(uint32_t mag_min, uint32_t mag_max)
{
    float mag = (float)rand_in_range(mag_min, mag_max);
    return (esp_random() & 1u) ? +mag : -mag;
}

static void idle_drift_task(void *arg)
{
    (void)arg;
    int64_t next_action_us = esp_timer_get_time() +
                             (int64_t)rand_in_range(3000, 8000) * 1000;

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(250));
        if (atomic_load(&s_frozen)) continue;

        int64_t now = esp_timer_get_time();
        if (now < next_action_us) continue;

        int posture = atomic_load(&s_posture);
        float here = atomic_load(&s_motion.current_deg);

        if (posture == SERVO_POSTURE_IDLE) {
            float delta = signed_rand(5, 10);
            float target = clampf(here + delta,
                                  SERVO_ANGLE_MIN_DEG, SERVO_ANGLE_MAX_DEG);
            servo_move_to(target, 600 + rand_in_range(0, 400));
            next_action_us = now + (int64_t)rand_in_range(3000, 8000) * 1000;
        } else if (posture == SERVO_POSTURE_SPEAKING) {
            float delta = signed_rand(3, 5);
            float target = clampf(here + delta,
                                  SERVO_ANGLE_MIN_DEG, SERVO_ANGLE_MAX_DEG);
            servo_move_to(target, 350);
            next_action_us = now + (int64_t)rand_in_range(1200, 1800) * 1000;
        } else {
            // LISTENING / SLEEPING — no drift; check back in a second.
            next_action_us = now + 1000 * 1000;
        }
    }
}

// ---- public API -----------------------------------------------------------

servo_config_t servo_default_config(void)
{
    servo_config_t c = {
        .gpio            = SERVO_DEFAULT_GPIO,
        .pulse_min_us    = 1000,
        .pulse_max_us    = 2000,
        .pulse_center_us = 1500,
        .update_hz       = 50,
    };
    return c;
}

esp_err_t servo_init(const servo_config_t *cfg)
{
    if (s_inited) return ESP_OK;

    s_cfg = cfg ? *cfg : servo_default_config();
    if (s_cfg.update_hz == 0) s_cfg.update_hz = 50;

    ledc_timer_config_t tcfg = {
        .speed_mode      = SERVO_LEDC_MODE,
        .duty_resolution = SERVO_LEDC_RESOLUTION,
        .timer_num       = SERVO_LEDC_TIMER,
        .freq_hz         = SERVO_LEDC_FREQ_HZ,
        .clk_cfg         = LEDC_AUTO_CLK,
    };
    esp_err_t err = ledc_timer_config(&tcfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ledc_timer_config: %d", err);
        return err;
    }

    ledc_channel_config_t ccfg = {
        .gpio_num   = s_cfg.gpio,
        .speed_mode = SERVO_LEDC_MODE,
        .channel    = SERVO_LEDC_CHANNEL,
        .intr_type  = LEDC_INTR_DISABLE,
        .timer_sel  = SERVO_LEDC_TIMER,
        .duty       = angle_to_duty(0.0f),
        .hpoint     = 0,
    };
    err = ledc_channel_config(&ccfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ledc_channel_config: %d", err);
        return err;
    }

    atomic_store(&s_motion.current_deg, 0.0f);
    atomic_store(&s_motion.cmd_ticket, 0);
    atomic_store(&s_motion.pending_target_deg, 0.0f);
    atomic_store(&s_motion.pending_duration_ms, 0);
    atomic_store(&s_motion.pending_is_snap_settle, false);
    s_motion.last_seen_ticket = 0;
    s_motion.move_from_deg = 0.0f;
    s_motion.move_to_deg   = 0.0f;
    s_motion.move_start_us = esp_timer_get_time();
    s_motion.move_duration_ms = 0;
    s_motion.move_is_snap_settle = false;

    atomic_store(&s_posture, SERVO_POSTURE_IDLE);
    atomic_store(&s_frozen, false);

    apply_angle(0.0f);

    xTaskCreate(interp_task,    "servo_interp", 3072, NULL,
                tskIDLE_PRIORITY + 2, &s_interp_task);
    xTaskCreate(idle_drift_task, "servo_drift", 3072, NULL,
                tskIDLE_PRIORITY + 1, &s_idle_task);

    s_inited = true;
    ESP_LOGI(TAG, "servo ready on GPIO %d (50 Hz LEDC, 14-bit duty)",
             s_cfg.gpio);
    return ESP_OK;
}

void servo_move_to(float angle_deg, uint32_t duration_ms)
{
    if (!s_inited) return;
    angle_deg = clampf(angle_deg, SERVO_ANGLE_MIN_DEG, SERVO_ANGLE_MAX_DEG);
    atomic_store(&s_motion.pending_target_deg, angle_deg);
    atomic_store(&s_motion.pending_duration_ms, duration_ms);
    atomic_store(&s_motion.pending_is_snap_settle, false);
    atomic_fetch_add(&s_motion.cmd_ticket, 1);
}

void servo_look_toward(float angle_deg)
{
    if (!s_inited) return;
    angle_deg = clampf(angle_deg, SERVO_ANGLE_MIN_DEG, SERVO_ANGLE_MAX_DEG);
    atomic_store(&s_motion.pending_target_deg, angle_deg);
    atomic_store(&s_motion.pending_duration_ms, SNAP_MS + SETTLE_MS);
    atomic_store(&s_motion.pending_is_snap_settle, true);
    atomic_fetch_add(&s_motion.cmd_ticket, 1);
}

void servo_set_posture(servo_posture_t p)
{
    atomic_store(&s_posture, (int)p);
}

float servo_current_angle(void)
{
    return atomic_load(&s_motion.current_deg);
}

void servo_freeze(void)
{
    atomic_store(&s_frozen, true);
}
