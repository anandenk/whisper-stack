// whisper-stack ESP-WROOM-32 firmware — main entry point.
//
// Each subsystem is gated on its ENABLE_X flag in app_state.h.
//
// State machine: IDLE → LISTENING → THINKING → SPEAKING → IDLE.
//
// M4: while IDLE, listen_stream_task holds an open chunked-transfer
// POST to /listen, draining the audio_in ring buffer. On wake fired
// (or BOOT button as a manual fallback), it transitions to LISTENING
// and calls do_round_trip_streaming_with_preroll(). The pre-roll is
// the last ~500 ms of audio that was already streamed to /listen so
// the start of the user's question (which often overlaps the tail of
// "...jarvis") isn't lost when VAD takes over.
//
// M5: after each reply finishes SPEAKING, do_round_trip_streaming_with_preroll
// loops into another VAD recording (no preroll, longer initial timeout)
// so follow-up questions don't need re-waking. Silent for
// WS_CONVERSATION_TIMEOUT_MS → return to IDLE and let listen_stream_task
// re-arm /listen for the next wake word. Mirrors tests/jarvis.py 6B.
//
// M3 path is preserved as do_round_trip_streaming_with_preroll(...,
// NULL, 0) — i.e. preroll-less. M2's buffered fallback is still gated
// on M2_BUFFERED_FALLBACK if the streaming path needs to be reverted.

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
#include "listen_stream.h"

static const char *TAG = "whisper-stack";

#define BUTTON_GPIO              GPIO_NUM_0
#define LONG_PRESS_CANCEL_MS     1000
// M5: how long to wait for a follow-up question before dropping back to
// wake-word listening. Match tests/jarvis.py's WAKEWORD_CONVERSATION_TIMEOUT_S
// default (30 s). The first turn after wake still uses the standard 15 s
// VAD_INITIAL_TIMEOUT_MS — short, in case wake was a false positive.
#define WS_CONVERSATION_TIMEOUT_MS  30000

// M6: VU-bar tuning.
//   * Bar lives on OLED line 6 during LISTENING/follow-up turns; line 7
//     stays "ip:..." footer; lines 0-5 stay the status text.
//   * BAR_PEAK_DIVISOR maps mic peak (uint16) to pixels (0..128).
//     Linear: 200 → 12 px (threshold marker), 600 → 37 px, 2048 → 128 px.
//     Tune at bench if real conversational peaks bunch up at one end.
//   * BAR_REDRAW_EVERY_N throttles updates so OLED I²C doesn't dominate
//     the VAD frame budget (~80 ms/frame). Every 3 frames = ~240 ms.
#define WS_BAR_LINE              6
#define WS_BAR_PEAK_DIVISOR      16
#define WS_BAR_REDRAW_EVERY_N    3
// Boot card displayed for ~2 s before transitioning to idle, so the user
// can confirm what server the device is talking to without serial monitor.
#define WS_BOOT_SCREEN_HOLD_MS   2000

#ifndef M2_BUFFERED_FALLBACK
#define M2_BUFFERED_FALLBACK     0
#endif

// shared state — listen_stream.c references s_state via extern
app_state_t s_state = APP_STATE_BOOT;

#if ENABLE_OLED
// All on-screen frames share this header + footer wrapper. Header is
// always "whisper-stack"; footer is the device IP (so the user can ping
// /confirm reachability at a glance from any state).
static void oled_frame_begin(const char *header)
{
    oled_clear();
    oled_draw_text(0, header ? header : "whisper-stack");
}
static void oled_frame_end(const char *ip)
{
    if (ip && *ip) {
        char ip_line[OLED_CHARS_PER_LINE + 1];
        snprintf(ip_line, sizeof ip_line, "ip:%s", ip);
        oled_draw_text(7, ip_line);
    }
    oled_flush();
}
static void oled_status(const char *line2, const char *line3, const char *ip)
{
    oled_frame_begin(NULL);
    if (line2) oled_draw_text(2, line2);
    if (line3) oled_draw_text(3, line3);
    oled_frame_end(ip);
}
// LISTENING / follow-up screen — same layout as oled_status but seeds line 6
// with an empty VU bar (0 px filled, threshold marker visible). Subsequent
// cb_frame calls update the bar via oled_flush_row(WS_BAR_LINE).
static void oled_listening_screen(const char *line2, const char *line3, const char *ip)
{
    oled_frame_begin(NULL);
    if (line2) oled_draw_text(2, line2);
    if (line3) oled_draw_text(3, line3);
    int marker_px = VAD_PEAK_THRESHOLD / WS_BAR_PEAK_DIVISOR;
    oled_draw_bar(WS_BAR_LINE, 0, marker_px);
    oled_frame_end(ip);
}
// Brief flash on every wake event. No explicit delay — the http stream open
// + preroll write that follow this call (~90 ms) hold the splash on screen
// long enough to register before the listening screen overwrites it.
static void oled_wake_splash(double score, const char *ip)
{
    char score_line[OLED_CHARS_PER_LINE + 1];
    snprintf(score_line, sizeof score_line, "    score %.2f", score);
    oled_frame_begin(NULL);
    oled_draw_text(2, "    *** WAKE ***");
    oled_draw_text(3, score_line);
    oled_frame_end(ip);
}
// Boot card — held for WS_BOOT_SCREEN_HOLD_MS so the user can confirm
// what server / wake model / session id the device booted with.
// Buffer is intentionally larger than OLED_CHARS_PER_LINE; long hostnames
// or session IDs get truncated visually by oled_draw_text without losing
// the leading prefix. Avoids -Wformat-truncation on Kconfig strings of
// arbitrary length.
static void oled_boot_screen(const char *ip)
{
    char line[80];
    oled_frame_begin("whisper-stack v0.1");
    snprintf(line, sizeof line, "ip:    %s", ip[0] ? ip : "(none)");
    oled_draw_text(2, line);
    snprintf(line, sizeof line, "srv:   %s:%d", CONFIG_WS_SERVER_HOST, CONFIG_WS_SERVER_PORT);
    oled_draw_text(3, line);
    oled_draw_text(4, "wake:  hey_jarvis");
    snprintf(line, sizeof line, "ses:   %s", CONFIG_WS_SESSION_ID);
    oled_draw_text(5, line);
    oled_draw_text(7, "booting...");
    oled_flush();
}
// SPEAKING-state screen — model name + token usage. The full transcript +
// reply still go to the serial log; the OLED is for at-a-glance "what
// model is the server using and how much did this turn cost".
static void oled_show_reply(const converse_resp_t *r, int speaking_remaining_s, const char *ip)
{
    char buf[64];

    oled_frame_begin(NULL);
    snprintf(buf, sizeof buf, "speaking %ds", speaking_remaining_s);
    oled_draw_text(2, buf);

    // Model name — truncated to fit by oled_draw_text (clips at 21 chars).
    // "claude-sonnet-4-6" fits whole; "llama3.1:8b-instruct-q5_K_M" gets
    // clipped after "instruct" which still tells you it was Llama.
    oled_draw_text(4, r->model[0] ? r->model : "(model unknown)");

    // Per-call cost in USD. Server returns 0.0 for local Llama and unknown
    // models. 4 decimals shows Haiku replies (~$0.0001) without losing
    // resolution; Sonnet's typical ~$0.001-0.05 fits the same width. Long
    // queries that push past $0.99 just print as $1.0234 etc.
    snprintf(buf, sizeof buf, "cost  $%.4f", (double)r->estimated_cost_usd);
    oled_draw_text(5, buf);

    // "in:42 out:128" — server sends -1 for either if absent.
    if (r->input_tokens >= 0 || r->output_tokens >= 0) {
        snprintf(buf, sizeof buf, "in:%d out:%d",
                 r->input_tokens, r->output_tokens);
        oled_draw_text(6, buf);
    }

    oled_frame_end(ip);
}
#else
static inline void oled_status(const char *l2, const char *l3, const char *ip) { (void)l2; (void)l3; (void)ip; }
static inline void oled_listening_screen(const char *l2, const char *l3, const char *ip) { (void)l2; (void)l3; (void)ip; }
static inline void oled_wake_splash(double s, const char *ip) { (void)s; (void)ip; }
static inline void oled_boot_screen(const char *ip) { (void)ip; }
#endif

// --- Round-trip context (frame + cancel callbacks share this) -------------

typedef struct {
    http_converse_stream_handle_t stream;
    int64_t low_started_us;
    bool    has_released;
} round_trip_ctx_t;

static esp_err_t cb_frame(const int16_t *pcm, size_t n, void *user)
{
    round_trip_ctx_t *ctx = user;
    esp_err_t err = http_converse_stream_write(ctx->stream, pcm, n);

#if ENABLE_OLED
    // Live VU-bar update — every Nth frame so OLED I²C doesn't dominate
    // the VAD frame budget. ~25 ms full flush would, but oled_flush_row
    // is ~3.5 ms so this is comfortable even at every-frame cadence; we
    // throttle anyway to reduce visible bar quantization noise.
    static unsigned frame_counter = 0;
    if ((frame_counter++ % WS_BAR_REDRAW_EVERY_N) == 0) {
        uint32_t peak = i2s_mic_last_peak();
        int fill_px = (int)(peak / WS_BAR_PEAK_DIVISOR);
        if (fill_px > OLED_W) fill_px = OLED_W;
        int marker_px = VAD_PEAK_THRESHOLD / WS_BAR_PEAK_DIVISOR;
        oled_draw_bar(WS_BAR_LINE, fill_px, marker_px);
        oled_flush_row(WS_BAR_LINE);
    }
#endif

    return err;
}

static bool cb_cancel(void *user)
{
    round_trip_ctx_t *ctx = user;
    int level = gpio_get_level(BUTTON_GPIO);
    int64_t now = esp_timer_get_time();
    if (level == 1) {
        ctx->has_released = true;
        ctx->low_started_us = 0;
        return false;
    }
    if (!ctx->has_released) return false;
    if (ctx->low_started_us == 0) ctx->low_started_us = now;
    return (now - ctx->low_started_us) >= ((int64_t)LONG_PRESS_CANCEL_MS * 1000);
}

// --- One round-trip turn (capture → upload → reply → speak countdown) -----
//
// Returns ESP_OK iff the turn completed successfully (we got a reply and
// finished SPEAKING). vr_out is always populated so the caller can see the
// VAD stop reason — a NO_SPEECH on a follow-up turn is the M5 signal to
// drop back to wake-word listening, not an error.

static esp_err_t do_one_turn(const char *ip,
                             const int16_t *preroll, size_t preroll_n,
                             int initial_timeout_ms,
                             bool is_followup,
                             vad_result_t *vr_out)
{
    s_state = APP_STATE_LISTENING;
    oled_listening_screen(is_followup ? "follow-up..." : "listening...",
                          is_followup ? "speak or wait"  : "speak now", ip);

    round_trip_ctx_t ctx = { 0 };
    esp_err_t err = http_converse_stream_open(&ctx.stream);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "stream open failed: %s", esp_err_to_name(err));
        oled_status("server error", "open failed", ip);
        return err;
    }

    // Ship the preroll first so the server's STT sees the lead-in to the
    // user's question (the tail of "...jarvis, what's the weather").
    // Follow-up turns never have a preroll.
    if (preroll && preroll_n > 0) {
        ESP_LOGI(TAG, "preroll: %u samples", (unsigned)preroll_n);
        if (http_converse_stream_write_preroll(ctx.stream, preroll, preroll_n) != ESP_OK) {
            ESP_LOGW(TAG, "preroll write failed; continuing");
        }
    }

    err = vad_record(cb_frame, cb_cancel, &ctx, initial_timeout_ms, vr_out);

    if (err != ESP_OK) {
        http_converse_stream_abort(ctx.stream);
        const char *line2 = "idle";
        const char *line3 = "press BOOT to talk";
        switch (vr_out->reason) {
            case VAD_STOP_BUTTON_CANCEL:    line2 = "cancelled"; break;
            case VAD_STOP_NO_SPEECH:        line2 = "no speech"; line3 = "press + speak"; break;
            case VAD_STOP_FRAME_CB_ERROR:   line2 = "upload err"; line3 = "check wifi";   break;
            case VAD_STOP_MIC_ERROR:        line2 = "mic error";  line3 = "reboot";        break;
            default: break;
        }
        oled_status(line2, line3, ip);
        return err;
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
        return err;
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

    return ESP_OK;
}

// --- Public round-trip entry point (called from listen_stream) ------------
//
// First turn uses the supplied preroll (from /listen's pre-roll ring) and
// the standard 15 s NO_SPEECH timeout — short, in case wake fired falsely.
// On a successful reply, M5 keeps the conversation hot: subsequent turns
// run preroll-less with a 30 s timeout so the user can ask follow-ups
// without re-saying "hey jarvis". Silent for the timeout, button cancel,
// or any error → exit to IDLE and listen_stream_task re-arms /listen.

void do_round_trip_streaming_with_preroll(const char *ip,
                                          const int16_t *preroll,
                                          size_t preroll_n,
                                          double wake_score)
{
    // M6: brief splash on every wake event so the user has positive
    // feedback that the wake fired before the listening screen takes
    // over. Skipped (score <= 0) for button-triggered turns.
    //
    // The 200 ms hold makes the splash readable. Audio cost: vad_record
    // calls i2s_mic_drain() at start, so live audio captured during the
    // hold is dropped — but the 500 ms preroll already covers the wake
    // word + a bit after, so as long as the user doesn't keep speaking
    // *immediately* through the wake without pause, this is invisible.
    // If users routinely run "hey jarvis what's the weather" with no
    // gap, lower this; default human pacing has a brief pause after
    // the wake word.
    if (wake_score > 0) {
        oled_wake_splash(wake_score, ip);
        vTaskDelay(pdMS_TO_TICKS(200));
    }

    vad_result_t vr;
    esp_err_t err = do_one_turn(ip, preroll, preroll_n,
                                /*initial_timeout_ms=*/0, /*is_followup=*/false, &vr);

    while (err == ESP_OK) {
        // Last turn returned a real reply. Stay open for a follow-up.
        err = do_one_turn(ip, NULL, 0,
                          WS_CONVERSATION_TIMEOUT_MS, /*is_followup=*/true, &vr);
        if (err != ESP_OK && vr.reason == VAD_STOP_NO_SPEECH) {
            // Conversation timed out — normal exit, not an error.
            ESP_LOGI(TAG, "continuous mode: silent %dms; back to wake mode",
                     WS_CONVERSATION_TIMEOUT_MS);
            break;
        }
    }

    s_state = APP_STATE_IDLE;
    oled_status("idle", "say 'hey jarvis'", ip);
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
             M2_BUFFERED_FALLBACK ? "M2-buffered" : "M3-streaming + M4-wake");

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

#if ENABLE_OLED
    // M6 boot card — held for ~2 s so the user can confirm IP / server
    // host / wake model / session id at a glance before the device drops
    // into the idle screen and starts /listen.
    oled_boot_screen(ip);
    vTaskDelay(pdMS_TO_TICKS(WS_BOOT_SCREEN_HOLD_MS));
#endif

    // BOOT button — needs to be configured before listen_stream_task starts
    // since listen_stream polls GPIO 0 directly for the manual-wake fallback.
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << BUTTON_GPIO,
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);

    s_state = APP_STATE_IDLE;
    oled_status("idle", "say 'hey jarvis'", ip);

    ESP_LOGI(TAG, "free heap: %u B", (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT));

#if ENABLE_MIC && ENABLE_WIFI
    if (listen_stream_init(ip) != ESP_OK) {
        ESP_LOGE(TAG, "listen_stream_init failed");
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
