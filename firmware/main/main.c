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
#if CONFIG_FACE_RENDERER_CAT_FACE
#include "cat_face.h"
#endif
#if CONFIG_FACE_RENDERER_ROBO_EYES
#include "robo_eyes.h"
#endif
#if CONFIG_GAZE_COUPLING_DEMO
#include "servo.h"
#endif
#include "wifi.h"
#include "i2s_mic.h"
#include "http_client.h"
#include "vad.h"
#include "listen_stream.h"
#include "wake_source.h"
#include "control_state.h"
#include "gesture.h"
#include "ir_remote.h"

static const char *TAG = "whisper-stack";

#define BUTTON_GPIO              GPIO_NUM_0
#define LONG_PRESS_CANCEL_MS     1000
// M5: how long to wait for a follow-up question before dropping back to
// wake-word listening. Match tests/jarvis.py's WAKEWORD_CONVERSATION_TIMEOUT_S
// default (30 s). The first turn after wake still uses the standard 15 s
// VAD_INITIAL_TIMEOUT_MS — short, in case wake was a false positive.
#define WS_CONVERSATION_TIMEOUT_MS  30000

// The M6 live VU bar on OLED line 6 was removed on 2026-05-10 after the
// loopback control experiment showed its per-frame oled_flush_row() was
// the dominant source of producer-side mic-frame recycle (16.5% → 0.0%
// loss, ~89 → 2 `ring overflow` warnings). The cat-face panel's `?`
// stroke animation gives sufficient "I'm listening" feedback. See
// notes/loopback-test-results.md for the experiment.
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
// Brief flash on every wake event. No explicit delay — the http stream open
// + preroll write that follow this call (~90 ms) hold the splash on screen
// long enough to register before the listening screen overwrites it.
//
// M7/M8: shows which path fired. Voice wake additionally displays the
// openWakeWord score; gesture/button paths just print the source name.
static void oled_wake_splash(wake_source_t src, double score, const char *ip)
{
    char line3[OLED_CHARS_PER_LINE + 1];
    if (src == WAKE_VOICE) {
        snprintf(line3, sizeof line3, "    score %.2f", score);
    } else {
        snprintf(line3, sizeof line3, "    via %s", wake_source_label(src));
    }
    oled_frame_begin(NULL);
    oled_draw_text(2, "    *** WAKE ***");
    oled_draw_text(3, line3);
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
static inline void oled_wake_splash(wake_source_t src, double s, const char *ip) { (void)src; (void)s; (void)ip; }
static inline void oled_boot_screen(const char *ip) { (void)ip; }
#endif

// --- Round-trip context (frame + cancel callbacks share this) -------------
//
// All of cb_frame / cb_cancel / do_one_turn / do_round_trip_streaming_with_preroll
// are gated on CONFIG_VOICE_ENABLED. When voice is disabled, listen_stream
// is never initialized so these are dead code anyway, but gating them out
// prevents unused-function warnings and keeps the build slim while the v3
// prototype iterates on display + servo motion in isolation. See
// notes/build-modes.md.

#if CONFIG_VOICE_ENABLED

typedef struct {
    http_converse_stream_handle_t stream;
    int64_t low_started_us;
    bool    has_released;
} round_trip_ctx_t;

static esp_err_t cb_frame(const int16_t *pcm, size_t n, void *user)
{
    // Hot path — runs at ~80 ms cadence from vad_record. Keep it just HTTP
    // write; any OLED / I²C work here will starve the consumer enough that
    // the mic ring producer recycles frames (proven 2026-05-10, see
    // notes/loopback-test-results.md).
    round_trip_ctx_t *ctx = user;
    return http_converse_stream_write(ctx->stream, pcm, n);
}

static bool cb_cancel(void *user)
{
    round_trip_ctx_t *ctx = user;

    // M8 — IR remote cancel takes precedence and is consumed here so a
    // single press of the cancel button bails out of the current
    // LISTENING turn cleanly (vad_record returns BUTTON_CANCEL).
    if (control_check_and_clear_cancel()) return true;

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
                             const int16_t *preroll_seg1, size_t preroll_seg1_n,
                             const int16_t *preroll_seg2, size_t preroll_seg2_n,
                             int initial_timeout_ms,
                             bool is_followup,
                             vad_result_t *vr_out)
{
    // Belt-and-suspenders: control_request_cancel ignores presses
    // outside a turn, so the flag should already be clear. Force-clear
    // anyway in case a press landed in the s_state-update window.
    control_clear_cancel();

    s_state = APP_STATE_LISTENING;
    oled_status(is_followup ? "follow-up..." : "listening...",
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
    // Two segments because the source ring buffer wraps; each is a
    // direct view into the listen_stream task's preroll memory (no
    // intermediate copy). Follow-up turns pass NULL/0 for both.
    if (preroll_seg1 && preroll_seg1_n > 0) {
        if (http_converse_stream_write_preroll(ctx.stream, preroll_seg1, preroll_seg1_n) != ESP_OK) {
            ESP_LOGW(TAG, "preroll seg1 write failed; continuing");
        }
    }
    if (preroll_seg2 && preroll_seg2_n > 0) {
        if (http_converse_stream_write_preroll(ctx.stream, preroll_seg2, preroll_seg2_n) != ESP_OK) {
            ESP_LOGW(TAG, "preroll seg2 write failed; continuing");
        }
    }
    if ((preroll_seg1 && preroll_seg1_n > 0) || (preroll_seg2 && preroll_seg2_n > 0)) {
        ESP_LOGI(TAG, "preroll: %u + %u samples",
                 (unsigned)preroll_seg1_n, (unsigned)preroll_seg2_n);
    }

    // Ring-drain policy. vad_record no longer drains at entry — it's now
    // the caller's responsibility, because the right behavior depends on
    // why we're here.
    //
    //   First turn (post-wake): the i2s_mic ring contains audio captured
    //   between wake-fire and now (~300-500 ms in normal conditions). That's
    //   the start of the user's question, which often runs straight through
    //   "hey jarvis" with no pause. Don't drain — let vad_record's first
    //   capture_frame call consume that queued audio naturally and continue
    //   into live capture. The previous "bridge" approach (a separate
    //   pre-vad drain loop) had no upper bound and on slow WiFi turned into
    //   an unbounded streamer that chased the producer for minutes.
    //
    //   Follow-up turn: the ring contains TTS-playback echo captured during
    //   the SPEAKING countdown (workstation speaker leaking back into the
    //   mic), NOT user speech. Drain it before vad_record.
    if (is_followup) {
        i2s_mic_drain();
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

    // M8 — best-effort cancel-during-THINKING. We can't kill the HTTP
    // request mid-flight cleanly, so a cancel that arrived during the
    // THINKING wait gets observed here, the moment finish() returns.
    // The server's already started playback (play_locally=true means
    // synth+play kicks off before the response posts back) so the
    // workstation will finish its current sentence — but we skip the
    // SPEAKING countdown and exit the continuous loop so the device
    // doesn't lock into a turn the user already cancelled.
    if (control_check_and_clear_cancel()) {
        ESP_LOGI(TAG, "cancel observed post-finish — skipping SPEAKING");
        oled_status("cancelled", "(playback cont.)", ip);
        vr_out->reason = VAD_STOP_BUTTON_CANCEL;
        return ESP_FAIL;
    }

    s_state = APP_STATE_SPEAKING;
    int speak_s = (int)(resp.audio_duration_s + 0.5f);
    if (speak_s < 1) speak_s = 1;

#if ENABLE_OLED
    for (int rem = speak_s; rem > 0; --rem) {
        // M8 — cancel during SPEAKING breaks the countdown early so
        // continuous-mode follow-ups don't auto-engage.
        if (control_check_and_clear_cancel()) {
            ESP_LOGI(TAG, "cancel during SPEAKING — exiting turn early");
            oled_status("cancelled", "(playback cont.)", ip);
            vr_out->reason = VAD_STOP_BUTTON_CANCEL;
            return ESP_FAIL;
        }
        oled_show_reply(&resp, rem, ip);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
#else
    for (int rem = speak_s; rem > 0; --rem) {
        if (control_check_and_clear_cancel()) {
            vr_out->reason = VAD_STOP_BUTTON_CANCEL;
            return ESP_FAIL;
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
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
                                          const int16_t *preroll_seg1,
                                          size_t preroll_seg1_n,
                                          const int16_t *preroll_seg2,
                                          size_t preroll_seg2_n,
                                          double wake_score,
                                          wake_source_t wake_source)
{
    // M6/M7/M8: brief splash on every wake event so the user has positive
    // feedback that the wake fired before the listening screen takes
    // over. Now shows the source label (VOICE/GESTURE/BUTTON) for all
    // paths — pre-M7 only voice turns got a splash.
    //
    // The 200 ms hold makes the splash readable. Audio cost: vad_record
    // calls i2s_mic_drain() at start, so live audio captured during the
    // hold is dropped — but the 500 ms preroll already covers the wake
    // word + a bit after, so as long as the user doesn't keep speaking
    // *immediately* through the wake without pause, this is invisible.
    // If users routinely run "hey jarvis what's the weather" with no
    // gap, lower this; default human pacing has a brief pause after
    // the wake word.
    ESP_LOGI(TAG, "round-trip starting via %s (score=%.2f)",
             wake_source_label(wake_source), wake_score);
    oled_wake_splash(wake_source, wake_score, ip);
    vTaskDelay(pdMS_TO_TICKS(200));

    vad_result_t vr;
    esp_err_t err = do_one_turn(ip,
                                preroll_seg1, preroll_seg1_n,
                                preroll_seg2, preroll_seg2_n,
                                /*initial_timeout_ms=*/0, /*is_followup=*/false, &vr);

    while (err == ESP_OK) {
        // Last turn returned a real reply. Stay open for a follow-up.
        err = do_one_turn(ip, NULL, 0, NULL, 0,
                          WS_CONVERSATION_TIMEOUT_MS, /*is_followup=*/true, &vr);
        if (err != ESP_OK && vr.reason == VAD_STOP_NO_SPEECH) {
            // Conversation timed out — normal exit, not an error.
            ESP_LOGI(TAG, "continuous mode: silent %dms; back to wake mode",
                     WS_CONVERSATION_TIMEOUT_MS);
            break;
        }
        if (err != ESP_OK && vr.reason == VAD_STOP_BUTTON_CANCEL) {
            // M8 — cancel exits the continuous loop too. Don't let a
            // cancelled turn auto-engage another follow-up.
            ESP_LOGI(TAG, "continuous mode: cancelled; back to wake mode");
            break;
        }
    }

    s_state = APP_STATE_IDLE;
    oled_status("idle", "say 'hey jarvis'", ip);
}

#endif  // CONFIG_VOICE_ENABLED

#if ENABLE_OLED
#if CONFIG_FACE_RENDERER_CAT_FACE
// Cat-face animation task. Reads s_state, plays the matching per-state
// animation from cat_face.h on the FACE OLED, transitioning instantly to
// the new animation when state changes (resets to frame 0 of the new state's
// loop). The animation tables are auto-generated by tests/oled_dump_c.py
// from the Python preview tool — to retune timings or change poses, edit
// tests/oled_animate.py and rerun the dump.
static void face_task(void *arg)
{
    int frame_idx = 0;
    app_state_t prev = (app_state_t)-1;
    const size_t n_states = sizeof(cat_anim_by_state) /
                            sizeof(cat_anim_by_state[0]);

    while (1) {
        app_state_t cur = s_state;
        if ((size_t)cur >= n_states) cur = APP_STATE_IDLE;

        if (cur != prev) {
            // State change — start the new state's animation from frame 0.
            frame_idx = 0;
            prev = cur;
        }

        const face_frame_t *anim = cat_anim_by_state[cur];
        size_t n_frames = cat_anim_len_by_state[cur];
        if (frame_idx >= (int)n_frames) frame_idx = 0;

        const face_frame_t *f = &anim[frame_idx];
        oled_face_draw_bitmap(f->bitmap);
        oled_face_flush();

        vTaskDelay(pdMS_TO_TICKS(f->duration_ms));
        frame_idx = (frame_idx + 1) % (int)n_frames;
    }
}
#endif  // CONFIG_FACE_RENDERER_CAT_FACE

#if CONFIG_FACE_RENDERER_ROBO_EYES
// RoboEyes face task — drives the FluxGarage RoboEyes port on the FACE
// OLED. State → mood mapping (see notes/face-renderer.md):
//
//   BOOT       → DEFAULT  (settling)
//   IDLE       → DEFAULT  + autoblinker + idle-mode random wander
//   LISTENING  → DEFAULT  + autoblinker, idle-mode OFF so gaze can be
//                          driven externally by the S3 servo coupling
//   THINKING   → TIRED    (drooping lids read as "processing")
//   SPEAKING   → HAPPY    (lifted lower lids — engaged smile)
//
// ANGRY is reserved for a future error/timeout state and not mapped
// here. The robo_eyes API is single-threaded — call setters and update
// only from this task. Tick at 50 Hz; the library's internal frame-rate
// gate matches.
static void face_task(void *arg)
{
    robo_eyes_begin(OLED_W, OLED_H, /*framerate=*/50);
    // Upstream begins with eyes closed; Arduino examples manually open
    // them after begin(). We do the same here so the device boots with
    // a nice "wake up" tween from closed -> open over the first ~120 ms.
    robo_eyes_open();
    robo_eyes_set_autoblinker(true, /*interval_s=*/3, /*variation_s=*/3);
    robo_eyes_set_idle_mode  (true, /*interval_s=*/2, /*variation_s=*/3);

    app_state_t prev = (app_state_t)-1;
    const int64_t boot_us = esp_timer_get_time();

    while (1) {
        app_state_t cur = s_state;
        if (cur != prev) {
            switch (cur) {
            case APP_STATE_BOOT:
            case APP_STATE_IDLE:
                robo_eyes_set_mood(ROBO_EYES_MOOD_DEFAULT);
                robo_eyes_set_idle_mode(true, 2, 3);
                break;
            case APP_STATE_LISTENING:
                robo_eyes_set_mood(ROBO_EYES_MOOD_DEFAULT);
                // Freeze idle wander so the gaze can be driven externally
                // by setPosition / setPositionContinuous from the wake/
                // servo coupling (tomorrow's S3 work).
                robo_eyes_set_idle_mode(false, 0, 0);
                break;
            case APP_STATE_THINKING:
                robo_eyes_set_mood(ROBO_EYES_MOOD_TIRED);
                robo_eyes_set_idle_mode(false, 0, 0);
                break;
            case APP_STATE_SPEAKING:
                robo_eyes_set_mood(ROBO_EYES_MOOD_HAPPY);
                robo_eyes_set_idle_mode(false, 0, 0);
                break;
            default:
                robo_eyes_set_mood(ROBO_EYES_MOOD_DEFAULT);
                break;
            }
            prev = cur;
        }

        const uint32_t now_ms = (uint32_t)((esp_timer_get_time() - boot_us) / 1000);
        if (robo_eyes_update(now_ms)) {
            oled_face_draw_bitmap(robo_eyes_get_framebuffer());
            oled_face_flush();
        }
        // Task tick at ~50 Hz; the library's internal frame-rate gate
        // additionally rate-limits redraws if we ever poll faster.
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
#endif  // CONFIG_FACE_RENDERER_ROBO_EYES
#endif  // ENABLE_OLED

#if CONFIG_GAZE_COUPLING_DEMO
// Eye-leads / head-follows architecture. See notes/motion-character.md.
//
// Face is the source of motion. The RoboEyes library runs entirely on
// its own — autoblinker on, idle mode on both axes, default intervals.
// The library's autonomous behavior provides the "alive" feel that two
// previous demos failed to synthesize from scratch.
//
// This task just polls the eye's current commanded angle on a slow
// tick and sends the head after it. Threshold prevents the servo from
// twitching on micro-movements; the head waits until the eyes commit
// to a direction before catching up.
//
// Two tasks total in this build: face_task (renders + autonomous
// motion) and servo_follow_task (this one).

// --- Tuning constants ------------------------------------------------

// Eye angular range → servo angular range. Eyes have wider range
// (±90° per the library's screen-X envelope mapping); servo follows
// over a narrower range so the head moves "lazily" relative to the
// eyes. EYE_ANGLE_MAX 90, SERVO_ANGLE_MAX 60 → mapping ratio 0.667.
// Increase SERVO_ANGLE_MAX toward 90 for a more committed follow;
// decrease toward 30 for an even lazier head.
#define EYE_ANGLE_MAX_DEG    90.0f
#define SERVO_ANGLE_MAX_DEG  60.0f

// Threshold below which we don't issue a new servo command. Prevents
// the head from twitching on the library's micro-tween wobbles and
// blink-related transients. 5° is roughly one full saccade-step.
#define FOLLOW_THRESHOLD_DEG 5.0f

// Servo move duration when the head decides to follow. Slow enough
// to feel like the head is reacting to the eyes, not driving them.
#define FOLLOW_MOVE_MS       800

// Follower tick. Doesn't need to be fast — 50 ms is plenty since the
// servo's own interp_task runs at 50 Hz internally.
#define FOLLOW_TICK_MS       50

static void servo_follow_task(void *arg)
{
    (void)arg;
    // Let face_task complete its BOOT → IDLE transition before we
    // start sampling eye position. During the transition the library
    // is still in "eyes closed" pose, which would map to a strange
    // initial follow target.
    vTaskDelay(pdMS_TO_TICKS(500));

    float last_commanded_deg = 0.0f;
    bool  first              = true;

    while (1) {
        const float eye_angle = robo_eyes_get_eye_x_angle();
        // Map eye angular range → servo angular range. Linear scale,
        // not clamped here because the getter never exceeds its own
        // ±EYE_ANGLE_MAX_DEG range.
        const float servo_target = eye_angle
            * (SERVO_ANGLE_MAX_DEG / EYE_ANGLE_MAX_DEG);

        const float diff = servo_target - last_commanded_deg;
        const float adiff = (diff < 0) ? -diff : diff;
        if (first || adiff > FOLLOW_THRESHOLD_DEG) {
            servo_move_to(servo_target, FOLLOW_MOVE_MS);
            ESP_LOGI(TAG, "head follow: eye %+5.1f° → servo %+5.1f° "
                          "(Δ %.1f°)",
                     (double)eye_angle, (double)servo_target, (double)adiff);
            last_commanded_deg = servo_target;
            first              = false;
        }

        vTaskDelay(pdMS_TO_TICKS(FOLLOW_TICK_MS));
    }
}
#endif  // CONFIG_GAZE_COUPLING_DEMO

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
    // Bring up the second OLED (face panel, I²C bus 1, GPIO 16/17).
    // The face_task started below renders the per-state animation; which
    // renderer it uses depends on CONFIG_FACE_RENDERER (cat_face.h or the
    // FluxGarage RoboEyes port). See notes/face-renderer.md.
    static bool s_face_ok = false;
    if (oled_face_init() == ESP_OK) {
        s_face_ok = true;
#if CONFIG_FACE_RENDERER_CAT_FACE
        // Draw idle pose immediately so the panel doesn't stay dark while
        // the face_task starts up and the boot/IP card runs on the main OLED.
        oled_face_draw_bitmap(cat_idle_open);
        oled_face_flush();
#else
        // RoboEyes: face_task runs robo_eyes_begin() on entry and the
        // first frame appears within ~20 ms of task start — fast enough
        // that no preroll bitmap is needed.
#endif
    } else {
        ESP_LOGW(TAG, "face OLED init failed — wiring or address issue");
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

#if ENABLE_MIC && CONFIG_VOICE_ENABLED
    if (i2s_mic_init() != ESP_OK) {
        ESP_LOGE(TAG, "mic init failed");
    }
#elif ENABLE_MIC
    ESP_LOGI(TAG, "voice disabled — skipping mic init");
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

    // M7 + M8 — shared mute/snooze/cancel state plus the two opt-in
    // peripherals. control_state_init must run before either gesture
    // or IR comes up since both refer to the shared mutex. gesture
    // and ir_remote both check their *_ENABLED Kconfig internally and
    // log a "disabled" message + return ESP_OK when off — keeping the
    // call sites unconditional matches how oled_face_init() handles
    // an unwired second OLED.
    if (control_state_init() != ESP_OK) {
        ESP_LOGE(TAG, "control_state_init failed");
    }
    if (gesture_init() != ESP_OK) {
        ESP_LOGW(TAG, "gesture_init failed (HC-SR04 wiring?)");
    }
    if (ir_remote_init() != ESP_OK) {
        ESP_LOGW(TAG, "ir_remote_init failed (RMT setup?)");
    }

    s_state = APP_STATE_IDLE;
    oled_status("idle", "say 'hey jarvis'", ip);

    ESP_LOGI(TAG, "free heap: %u B", (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT));

#if ENABLE_OLED
    if (s_face_ok) {
        // Cat-face animation task — drives the second OLED based on s_state.
        // Stack 3 KB (only holds a few ints + bitmap pointer; OLED flush
        // is via I²C and doesn't need much). Priority 1 so it never
        // preempts audio / network work.
        xTaskCreate(face_task, "face", 3072, NULL, tskIDLE_PRIORITY + 1, NULL);
        ESP_LOGI(TAG, "face_task spawned");
    }
#endif

#if ENABLE_MIC && ENABLE_WIFI && CONFIG_VOICE_ENABLED
    if (listen_stream_init(ip) != ESP_OK) {
        ESP_LOGE(TAG, "listen_stream_init failed");
    }
#elif ENABLE_MIC && ENABLE_WIFI
    ESP_LOGI(TAG, "voice disabled — skipping listen_stream init");
#endif

#if CONFIG_GAZE_COUPLING_DEMO
    if (servo_init(NULL) == ESP_OK) {
        ESP_LOGI(TAG, "head-follows-eyes coupling enabled — SG90 on GPIO 18");
        // LISTENING posture keeps the servo's internal drift task quiet so
        // it doesn't fight with our explicit follow commands.
        servo_set_posture(SERVO_POSTURE_LISTENING);
        xTaskCreate(servo_follow_task, "servo_follow", 3072, NULL,
                    tskIDLE_PRIORITY + 1, NULL);
    } else {
        ESP_LOGE(TAG, "servo_init failed — coupling disabled");
    }
#endif

    int n = 0;
    while (1) {
#if ENABLE_MIC && CONFIG_VOICE_ENABLED
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
