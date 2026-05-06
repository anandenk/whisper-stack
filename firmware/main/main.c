// whisper-stack ESP-WROOM-32 firmware — main entry point.
//
// Each subsystem is gated on its ENABLE_X flag in app_state.h. The .c
// files always compile and link, but the peripheral init only runs when
// its flag is 1.
//
// M2 adds a button-driven /converse round-trip:
//   BOOT button (GPIO0) press  →  3-second I²S capture
//                              →  POST audio/l16 to /converse?omit_audio_base64=1
//                              →  parse JSON
//                              →  OLED shows transcript + speaking timer
//                              →  workstation speaks the reply (play_locally=true)

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_chip_info.h"
#include "esp_idf_version.h"
#include "esp_heap_caps.h"
#include "driver/gpio.h"
#include "sdkconfig.h"

#include "app_state.h"
#include "oled.h"
#include "wifi.h"
#include "i2s_mic.h"
#include "http_client.h"

static const char *TAG = "whisper-stack";

#define BUTTON_GPIO            GPIO_NUM_0
#define BUTTON_POLL_MS         20
#define BUTTON_DEBOUNCE_TICKS  2          // 2 × 20 ms = 40 ms — solid for a tactile switch
#define RECORD_SECONDS         3
#define RECORD_TIMEOUT_MS      5000       // capture timeout = recording_len + slack

static volatile app_state_t s_state = APP_STATE_BOOT;

#if ENABLE_OLED
static void oled_status(const char *line2, const char *line3, const char *ip)
{
    oled_clear();
    oled_draw_text(0, "whisper-stack");
    if (line2) oled_draw_text(2, line2);
    if (line3) oled_draw_text(3, line3);
    if (ip) {
        char ip_line[OLED_CHARS_PER_LINE + 1];
        snprintf(ip_line, sizeof ip_line, "ip:%s", ip);
        oled_draw_text(7, ip_line);
    }
    oled_flush();
}
#else
static inline void oled_status(const char *l2, const char *l3, const char *ip) { (void)l2; (void)l3; (void)ip; }
#endif

#if ENABLE_OLED
// Render up to two text lines from the server response (transcript on
// line 5, reply on line 6). Clipped to OLED_CHARS_PER_LINE by the
// driver — full text already logged to serial.
static void oled_show_reply(const converse_resp_t *r, int speaking_remaining_s, const char *ip)
{
    char heard[OLED_CHARS_PER_LINE + 1];
    char said [OLED_CHARS_PER_LINE + 1];
    char timer[OLED_CHARS_PER_LINE + 1];
    // Explicit precision — both fields are intentionally truncated to fit
    // the 21-char OLED line; the full text already went to serial.
    const int n = (int)sizeof(heard) - 3;   // leave room for "u:" prefix + NUL
    snprintf(heard, sizeof heard, "u:%.*s", n, r->transcript);
    snprintf(said,  sizeof said,  "a:%.*s", n, r->reply_text);
    snprintf(timer, sizeof timer, "speaking %ds", speaking_remaining_s);

    oled_clear();
    oled_draw_text(0, "whisper-stack");
    oled_draw_text(2, timer);
    oled_draw_text(5, heard);
    oled_draw_text(6, said);
    if (ip) {
        char ip_line[OLED_CHARS_PER_LINE + 1];
        snprintf(ip_line, sizeof ip_line, "ip:%s", ip);
        oled_draw_text(7, ip_line);
    }
    oled_flush();
}
#endif

// Allocate, capture, post, display. All heavy work happens here off the
// button task so the task can poll for releases / re-presses cleanly.
static void do_round_trip(const char *ip)
{
    const size_t n_samples = I2S_MIC_SAMPLE_RATE * RECORD_SECONDS;
    const size_t pcm_bytes = n_samples * sizeof(int16_t);

    int16_t *pcm = malloc(pcm_bytes);
    if (!pcm) {
        ESP_LOGE(TAG, "out of memory for %u-byte PCM buffer (free=%u)",
                 (unsigned)pcm_bytes, (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT));
        oled_status("error", "no memory", ip);
        return;
    }

    s_state = APP_STATE_LISTENING;
    ESP_LOGI(TAG, "LISTENING — recording %ds (%u bytes)", RECORD_SECONDS, (unsigned)pcm_bytes);
    oled_status("listening...", "speak now", ip);

    esp_err_t err = i2s_mic_capture(pcm, n_samples, RECORD_TIMEOUT_MS);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "capture: %s", esp_err_to_name(err));
        oled_status("error", "mic capture", ip);
        free(pcm);
        s_state = APP_STATE_IDLE;
        return;
    }

    s_state = APP_STATE_THINKING;
    ESP_LOGI(TAG, "THINKING — POST /converse");
    oled_status("uploading...", "/converse", ip);

    converse_resp_t resp;
    err = http_post_converse(pcm, n_samples, &resp);
    free(pcm);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "POST failed: %s (status=%d)", esp_err_to_name(err), resp.http_status);
        char l3[OLED_CHARS_PER_LINE + 1];
        snprintf(l3, sizeof l3, "status %d", resp.http_status);
        oled_status("server error", l3, ip);
        s_state = APP_STATE_IDLE;
        return;
    }

    ESP_LOGI(TAG, "transcript: %s", resp.transcript);
    ESP_LOGI(TAG, "reply:      %s", resp.reply_text);
    ESP_LOGI(TAG, "audio_duration_s=%.2f", resp.audio_duration_s);

    s_state = APP_STATE_SPEAKING;
    int speak_s = (int)(resp.audio_duration_s + 0.5f);
    if (speak_s < 1) speak_s = 1;

#if ENABLE_OLED
    for (int rem = speak_s; rem > 0; --rem) {
        oled_show_reply(&resp, rem, ip);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
#else
    vTaskDelay(pdMS_TO_TICKS(speak_s * 1000));
#endif

    s_state = APP_STATE_IDLE;
    oled_status("idle", "press BOOT to talk", ip);
}

static void button_task(void *arg)
{
    const char *ip = (const char *)arg;

    gpio_config_t io = {
        .pin_bit_mask = 1ULL << BUTTON_GPIO,
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_ENABLE,    // BOOT has external pull-up but belt-and-suspenders
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);

    int low_count = 0;
    int prev_pressed = 0;

    while (1) {
        int level = gpio_get_level(BUTTON_GPIO);   // 0 = pressed, 1 = released

        if (level == 0) {
            if (low_count < BUTTON_DEBOUNCE_TICKS) low_count++;
        } else {
            low_count = 0;
        }
        int pressed = (low_count >= BUTTON_DEBOUNCE_TICKS);

        // Falling edge (release → press transition) and only when idle
        if (pressed && !prev_pressed && s_state == APP_STATE_IDLE) {
            ESP_LOGI(TAG, "button press detected");
            do_round_trip(ip);
        }
        prev_pressed = pressed;

        vTaskDelay(pdMS_TO_TICKS(BUTTON_POLL_MS));
    }
}

void app_main(void)
{
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    ESP_LOGI(TAG, "boot: ESP32 rev %d, %d core%s, IDF %s",
             chip.revision, chip.cores, chip.cores > 1 ? "s" : "",
             esp_get_idf_version());
    ESP_LOGI(TAG, "subsystems: OLED=%d MIC=%d WIFI=%d",
             ENABLE_OLED, ENABLE_MIC, ENABLE_WIFI);

#if ENABLE_OLED
    if (oled_init() == ESP_OK) {
        oled_clear();
        oled_draw_text(0, "whisper-stack");
        oled_draw_text(2, "boot...");
        oled_flush();
    }
#endif

    static char ip[16] = "";
#if ENABLE_WIFI
    esp_err_t werr = wifi_init_sta_blocking(ip, sizeof ip);
    if (werr == ESP_OK) {
        ESP_LOGI(TAG, "wifi up; IP=%s", ip);
    } else {
        ESP_LOGE(TAG, "wifi failed: %s — staying offline", esp_err_to_name(werr));
        strncpy(ip, "OFFLINE", sizeof ip - 1);
    }
#endif

#if ENABLE_MIC
    if (i2s_mic_init() != ESP_OK) {
        ESP_LOGE(TAG, "mic init failed");
    }
#endif

    s_state = APP_STATE_IDLE;
    oled_status("idle", "press BOOT to talk", ip);

    ESP_LOGI(TAG, "free heap: %u B", (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT));

#if ENABLE_MIC && ENABLE_WIFI
    BaseType_t ok = xTaskCreate(button_task, "button", 8192, ip, 4, NULL);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "xTaskCreate(button) failed");
    }
#endif

    // Heartbeat: log mic levels + state every 2 seconds. Less noisy than
    // M1's 500 ms tick now that we have real work to do.
    int n = 0;
    while (1) {
#if ENABLE_MIC
        ESP_LOGD(TAG, "tick %d state=%d rms=%lu peak=%lu",
                 n++, (int)s_state,
                 (unsigned long)i2s_mic_last_rms(),
                 (unsigned long)i2s_mic_last_peak());
#else
        ESP_LOGD(TAG, "tick %d (idle)", n++);
#endif
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}
