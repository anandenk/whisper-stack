// servo_test — exercises every API in the servo component on the v1
// ESP-WROOM-32 hardware (SG90 plastic from the Arduino starter kit).
//
// Demo script (loops):
//   1. POSTURE IDLE   — center, then let idle drift run for 12 s so
//                       you can see the random ±5-10° pans every
//                       3-8 s. This is the "alive feel" baseline.
//   2. POSTURE LISTENING — freeze on the last drift target. Now fire
//                       three servo_look_toward() reactions to
//                       simulate "sound from -45°", "sound from
//                       +60°", "sound from -10°". Each does a snap
//                       to 95% in 200 ms then settles the last 5%
//                       over 400 ms. Watch the second leg arrive
//                       gently.
//   3. POSTURE SPEAKING — gentle ±3-5° sway every ~1.5 s. The face
//                       on the OLED (when later coupled) will be
//                       talking; the head sways subtly to feel
//                       engaged rather than static.
//   4. POSTURE SLEEPING — slow ease to center, freeze, hold for 6 s.
//
// Wiring (DOIT ESP32 DEVKIT V1):
//   SG90 brown -> GND
//   SG90 red   -> VIN (5V from USB)   — NOT 3V3, the servo stalls
//   SG90 yel.  -> GPIO 18 (signal)    — change via SERVO_DEFAULT_GPIO
//
// Build / flash:
//   cd firmware/examples/servo_test
//   idf.py set-target esp32
//   idf.py build
//   idf.py -p COM3 flash monitor

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

#include "servo.h"

static const char *TAG = "servo_test";

static void log_angle(const char *label)
{
    ESP_LOGI(TAG, "%s — commanded angle: %+.1f deg",
             label, servo_current_angle());
}

void app_main(void)
{
    ESP_LOGI(TAG, "servo_test starting on GPIO %d", SERVO_DEFAULT_GPIO);

    if (servo_init(NULL) != ESP_OK) {
        ESP_LOGE(TAG, "servo_init failed — check LEDC clock config");
        return;
    }

    while (1) {
        // ---- 1. IDLE — center, then 12 s of free drift -------------------
        ESP_LOGI(TAG, "PHASE 1: IDLE posture, alive-feel drift");
        servo_set_posture(SERVO_POSTURE_IDLE);
        servo_move_to(0.0f, 600);
        vTaskDelay(pdMS_TO_TICKS(12000));
        log_angle("post-idle-drift");

        // ---- 2. LISTENING — three look-toward reactions ------------------
        ESP_LOGI(TAG, "PHASE 2: LISTENING posture, look-toward reactions");
        servo_set_posture(SERVO_POSTURE_LISTENING);

        const float SOUND_ANGLES[] = { -45.0f, +60.0f, -10.0f };
        for (size_t i = 0; i < sizeof SOUND_ANGLES / sizeof SOUND_ANGLES[0]; ++i) {
            ESP_LOGI(TAG, "  -> servo_look_toward(%+.0f)", SOUND_ANGLES[i]);
            servo_look_toward(SOUND_ANGLES[i]);
            // snap (200 ms) + settle (400 ms) + a beat of stillness
            vTaskDelay(pdMS_TO_TICKS(200 + 400 + 800));
            log_angle("  after settle");
        }

        // ---- 3. SPEAKING — gentle engaged sway ---------------------------
        ESP_LOGI(TAG, "PHASE 3: SPEAKING posture, gentle sway for 10 s");
        servo_set_posture(SERVO_POSTURE_SPEAKING);
        servo_move_to(0.0f, 500);
        vTaskDelay(pdMS_TO_TICKS(10000));
        log_angle("post-speaking");

        // ---- 4. SLEEPING — ease to center and freeze ---------------------
        ESP_LOGI(TAG, "PHASE 4: SLEEPING posture, hold center 6 s");
        servo_set_posture(SERVO_POSTURE_SLEEPING);
        servo_move_to(0.0f, 1200);
        vTaskDelay(pdMS_TO_TICKS(6000));
        log_angle("post-sleeping");

        ESP_LOGI(TAG, "loop complete; restarting demo");
    }
}
