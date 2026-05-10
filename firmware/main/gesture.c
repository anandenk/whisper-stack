// HC-SR04 ultrasonic wave-to-wake — see header.

#include "gesture.h"
#include "control_state.h"
#include "app_state.h"

#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "sdkconfig.h"

static const char *TAG = "gesture";

extern app_state_t s_state;  // defined in main.c

#if CONFIG_WAKE_GESTURE_ENABLED

// One-shot wake flag the listen_stream main loop polls. atomic — written
// by gesture_poll_task, read by listen_stream_task.
static atomic_bool s_wake_pending = false;

// HC-SR04 datasheet: trig requires ≥10 µs HIGH pulse; echo HIGH duration
// = round-trip time, distance (cm) = pulse_us / 58.
#define HCSR04_TRIG_US           12        // 10 µs minimum + slack
#define HCSR04_ECHO_TIMEOUT_US   30000     // ~5 m round-trip; sensor sees max ~4 m

// Returns distance in cm, or -1 on echo timeout (no obstacle within range
// or sensor not wired). Busy-waits with esp_timer_get_time() bounds —
// task priority is 1 so audio + mic preempt freely.
static int hcsr04_measure_cm(void)
{
    gpio_set_level(CONFIG_WAKE_GESTURE_TRIG_GPIO, 0);
    esp_rom_delay_us(2);
    gpio_set_level(CONFIG_WAKE_GESTURE_TRIG_GPIO, 1);
    esp_rom_delay_us(HCSR04_TRIG_US);
    gpio_set_level(CONFIG_WAKE_GESTURE_TRIG_GPIO, 0);

    int64_t t0 = esp_timer_get_time();
    while (gpio_get_level(CONFIG_WAKE_GESTURE_ECHO_GPIO) == 0) {
        if (esp_timer_get_time() - t0 > HCSR04_ECHO_TIMEOUT_US) return -1;
    }
    int64_t high_start = esp_timer_get_time();
    while (gpio_get_level(CONFIG_WAKE_GESTURE_ECHO_GPIO) == 1) {
        if (esp_timer_get_time() - high_start > HCSR04_ECHO_TIMEOUT_US) return -1;
    }
    int64_t high_end = esp_timer_get_time();
    return (int)((high_end - high_start) / 58);
}

// State machine for "wave" detection. We need a down-up-down crossing
// of the threshold inside a window — that filters out static obstacles
// (a wall close to the sensor crosses below once and stays there — never
// fires) while accepting a hand passing through the beam twice.
typedef enum {
    GS_IDLE       = 0,   // waiting for first below-threshold reading
    GS_FIRST_LOW  = 1,   // saw below; waiting for above
    GS_GAP_HIGH   = 2,   // saw above after first low; waiting for second below
} gs_t;

static void gesture_poll_task(void *arg)
{
    (void)arg;
    gs_t state = GS_IDLE;
    int64_t window_started_us = 0;
    const int     threshold_cm = CONFIG_WAKE_GESTURE_THRESHOLD_CM;
    const int64_t window_us    = (int64_t)CONFIG_WAKE_GESTURE_WINDOW_MS * 1000;

    ESP_LOGI(TAG, "gesture poll: trig=GPIO%d echo=GPIO%d threshold=%dcm window=%dms",
             CONFIG_WAKE_GESTURE_TRIG_GPIO, CONFIG_WAKE_GESTURE_ECHO_GPIO,
             threshold_cm, CONFIG_WAKE_GESTURE_WINDOW_MS);

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(CONFIG_WAKE_GESTURE_POLL_INTERVAL_MS));

        // Idle the polling while not in IDLE — gesture wake is a wake
        // source, no point sampling during a turn. Also pause when
        // muted (mute is supposed to disable both wake sources).
        if (s_state != APP_STATE_IDLE) { state = GS_IDLE; continue; }
        if (control_is_muted_now())   { state = GS_IDLE; continue; }
        // Don't measure again until the previous detection has been
        // consumed — otherwise a held hand keeps re-firing.
        if (atomic_load(&s_wake_pending)) { state = GS_IDLE; continue; }

        int cm = hcsr04_measure_cm();
        if (cm < 0) {
            // Echo timeout = nothing in range; equivalent to "above threshold".
            cm = 1000;
        }

        int64_t now_us = esp_timer_get_time();
        bool below = (cm > 0 && cm <= threshold_cm);

        // Window expiry — if any state has been pending too long, reset.
        if (state != GS_IDLE && (now_us - window_started_us) > window_us) {
            state = GS_IDLE;
        }

        switch (state) {
            case GS_IDLE:
                if (below) {
                    state = GS_FIRST_LOW;
                    window_started_us = now_us;
                }
                break;
            case GS_FIRST_LOW:
                if (!below) state = GS_GAP_HIGH;
                break;
            case GS_GAP_HIGH:
                if (below) {
                    ESP_LOGI(TAG, "WAVE detected (cm=%d)", cm);
                    atomic_store(&s_wake_pending, true);
                    state = GS_IDLE;
                }
                break;
        }
    }
}

esp_err_t gesture_init(void)
{
    gpio_config_t out = {
        .pin_bit_mask = 1ULL << CONFIG_WAKE_GESTURE_TRIG_GPIO,
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    if (gpio_config(&out) != ESP_OK) return ESP_FAIL;

    gpio_config_t in = {
        .pin_bit_mask = 1ULL << CONFIG_WAKE_GESTURE_ECHO_GPIO,
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    if (gpio_config(&in) != ESP_OK) return ESP_FAIL;
    gpio_set_level(CONFIG_WAKE_GESTURE_TRIG_GPIO, 0);

    BaseType_t ok = xTaskCreate(gesture_poll_task, "gesture", 3072, NULL,
                                tskIDLE_PRIORITY + 1, NULL);
    return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

bool gesture_check_and_clear(void)
{
    bool expected = true;
    return atomic_compare_exchange_strong(&s_wake_pending, &expected, false);
}

#else  // CONFIG_WAKE_GESTURE_ENABLED

esp_err_t gesture_init(void)
{
    ESP_LOGI(TAG, "disabled (CONFIG_WAKE_GESTURE_ENABLED unset)");
    return ESP_OK;
}

bool gesture_check_and_clear(void) { return false; }

#endif
