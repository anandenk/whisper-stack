// whisper-stack ESP-WROOM-32 firmware — main entry point.
//
// Each subsystem is gated on its ENABLE_X flag in app_state.h. The .c
// files always compile and link, but the peripheral init only runs when
// its flag is 1.
//
// State machine: IDLE → LISTENING → THINKING → SPEAKING → IDLE.
//
// M2 (button + fixed 3s recording, buffered POST) is preserved under
// the M2_BUFFERED_FALLBACK toggle. Default is M3 streaming:
//   BOOT press → VAD-bounded record (peak threshold + silence dwell)
//              → chunked HTTP POST written frame-by-frame
//              → JSON reply parsed
//              → OLED transcript / reply / speaking timer
// During recording, holding the button ≥1 s cancels the round-trip.

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_chip_info.h"
#include "esp_idf_version.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "driver/gpio.h"
#include "sdkconfig.h"

#include "app_state.h"
#include "oled.h"
#include "wifi.h"
#include "i2s_mic.h"
#include "http_client.h"
#include "vad.h"

static const char *TAG = "whisper-stack";

#define BUTTON_GPIO              GPIO_NUM_0
#define BUTTON_POLL_MS           20
#define BUTTON_DEBOUNCE_TICKS    2          // 2 × 20 ms = 40 ms
#define LONG_PRESS_CANCEL_MS     1000       // hold-to-cancel during recording

// Set to 1 to fall back to M2's fixed-length buffered POST instead of
// the M3 streaming path. Useful if the chunked upload turns out to be
// flaky on real WiFi — flipping this define and rebuilding gets us
// back to the M2 known-good behavior without code surgery.
#ifndef M2_BUFFERED_FALLBACK
#define M2_BUFFERED_FALLBACK     0
#endif

#if M2_BUFFERED_FALLBACK
#define M2_RECORD_SECONDS        3
#define M2_RECORD_TIMEOUT_MS     5000
#endif

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
static void oled_show_reply(const converse_resp_t *r, int speaking_remaining_s, const char *ip)
{
    char heard[OLED_CHARS_PER_LINE + 1];
    char said [OLED_CHARS_PER_LINE + 1];
    char timer[OLED_CHARS_PER_LINE + 1];
    const int n = (int)sizeof(heard) - 3;
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
#else
static inline void oled_status(const char *l2, const char *l3, const char *ip) { (void)l2; (void)l3; (void)ip; }
#endif

// --- Round-trip context (frame + cancel callbacks share this) -------------

typedef struct {
    http_converse_stream_handle_t stream;
    int64_t low_started_us;   // wall time when button most recently went LOW (0 = currently released)
    bool    has_released;     // true once we've seen the button HIGH after the recording started
} round_trip_ctx_t;

static esp_err_t cb_frame(const int16_t *pcm, size_t n, void *user)
{
    round_trip_ctx_t *ctx = user;
    return http_converse_stream_write(ctx->stream, pcm, n);
}

static bool cb_cancel(void *user)
{
    round_trip_ctx_t *ctx = user;
    int level = gpio_get_level(BUTTON_GPIO);  // 0 pressed, 1 released
    int64_t now = esp_timer_get_time();
    if (level == 1) {
        ctx->has_released = true;
        ctx->low_started_us = 0;
        return false;
    }
    // pressed; only count toward cancel once we've seen at least one release
    // since the recording started (otherwise the same press that triggered
    // the recording would immediately cancel it).
    if (!ctx->has_released) return false;
    if (ctx->low_started_us == 0) ctx->low_started_us = now;
    return (now - ctx->low_started_us) >= ((int64_t)LONG_PRESS_CANCEL_MS * 1000);
}

#if M2_BUFFERED_FALLBACK
// M2 fallback: fixed-length buffered POST. Kept for A/B if streaming flakes.
static void do_round_trip_buffered(const char *ip)
{
    const size_t n_samples = I2S_MIC_SAMPLE_RATE * M2_RECORD_SECONDS;
    const size_t pcm_bytes = n_samples * sizeof(int16_t);

    int16_t *pcm = malloc(pcm_bytes);
    if (!pcm) {
        ESP_LOGE(TAG, "OOM for %u-byte PCM buffer", (unsigned)pcm_bytes);
        oled_status("error", "no memory", ip);
        return;
    }

    s_state = APP_STATE_LISTENING;
    oled_status("listening...", "speak now", ip);
    esp_err_t err = i2s_mic_capture(pcm, n_samples, M2_RECORD_TIMEOUT_MS);
    if (err != ESP_OK) {
        oled_status("error", "mic capture", ip);
        free(pcm); s_state = APP_STATE_IDLE; return;
    }

    s_state = APP_STATE_THINKING;
    oled_status("uploading...", "/converse", ip);
    converse_resp_t resp;
    err = http_post_converse(pcm, n_samples, &resp);
    free(pcm);
    if (err != ESP_OK) {
        char l3[OLED_CHARS_PER_LINE + 1];
        snprintf(l3, sizeof l3, "status %d", resp.http_status);
        oled_status("server error", l3, ip);
        s_state = APP_STATE_IDLE; return;
    }
    ESP_LOGI(TAG, "transcript: %s", resp.transcript);
    ESP_LOGI(TAG, "reply:      %s", resp.reply_text);

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
#endif // M2_BUFFERED_FALLBACK

static void do_round_trip_streaming(const char *ip)
{
    s_state = APP_STATE_LISTENING;
    oled_status("listening...", "speak now", ip);

    round_trip_ctx_t ctx = { 0 };
    esp_err_t err = http_converse_stream_open(&ctx.stream);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "stream open failed: %s", esp_err_to_name(err));
        oled_status("server error", "open failed", ip);
        s_state = APP_STATE_IDLE;
        return;
    }

    vad_result_t vr;
    err = vad_record(cb_frame, cb_cancel, &ctx, &vr);

    if (err != ESP_OK) {
        // Non-OK stop: cancel / no-speech / cb-error / mic-error.
        http_converse_stream_abort(ctx.stream);
        const char *line2 = "idle";
        const char *line3 = "press BOOT to talk";
        switch (vr.reason) {
            case VAD_STOP_BUTTON_CANCEL:    line2 = "cancelled"; line3 = "press BOOT to talk"; break;
            case VAD_STOP_NO_SPEECH:        line2 = "no speech"; line3 = "press + speak";       break;
            case VAD_STOP_FRAME_CB_ERROR:   line2 = "upload err"; line3 = "check wifi";         break;
            case VAD_STOP_MIC_ERROR:        line2 = "mic error";  line3 = "reboot";              break;
            default:                                                                                break;
        }
        oled_status(line2, line3, ip);
        s_state = APP_STATE_IDLE;
        return;
    }

    s_state = APP_STATE_THINKING;
    oled_status("uploading...", "wait for reply", ip);

    converse_resp_t resp;
    err = http_converse_stream_finish(ctx.stream, &resp);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "stream finish failed: %s status=%d", esp_err_to_name(err), resp.http_status);
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
        .pull_up_en   = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);

    int low_count = 0;
    int prev_pressed = 0;

    while (1) {
        int level = gpio_get_level(BUTTON_GPIO);

        if (level == 0) {
            if (low_count < BUTTON_DEBOUNCE_TICKS) low_count++;
        } else {
            low_count = 0;
        }
        int pressed = (low_count >= BUTTON_DEBOUNCE_TICKS);

        if (pressed && !prev_pressed && s_state == APP_STATE_IDLE) {
            ESP_LOGI(TAG, "button press detected");
#if M2_BUFFERED_FALLBACK
            do_round_trip_buffered(ip);
#else
            do_round_trip_streaming(ip);
#endif
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
    ESP_LOGI(TAG, "subsystems: OLED=%d MIC=%d WIFI=%d  upload=%s",
             ENABLE_OLED, ENABLE_MIC, ENABLE_WIFI,
             M2_BUFFERED_FALLBACK ? "M2-buffered" : "M3-streaming");

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
