// v2-M6 VAD — driven by AFE's vadnet1_medium output instead of the
// pre-v2-M6 peak-threshold detector. See vad.h.
//
// Algorithm:
//   * Pull each ~32 ms AFE-processed frame from wake_listen_next.
//     The frame includes the vadnet1 VAD verdict for that chunk.
//   * If vad_state == VAD_SPEECH, mark "voice has started" and reset
//     the silence-dwell timer.
//   * Stop when silence dwell ≥ VAD_SILENCE_DWELL_MS AFTER voice has
//     been heard.
//   * If no SPEECH frame ever appears within `initial_timeout_ms`,
//     stop with NO_SPEECH so we don't upload an empty record.
//   * Hard cap at VAD_MAX_RECORD_MS as runaway protection.
//
// The pre-v2-M6 peak-threshold path (VAD_PEAK_THRESHOLD constant +
// frame_peak() helper) is gone — vadnet1 discriminates speech from
// transients (typing, clicks, dishes) far better than energy thresholds.
// Recoverable from git tag `pre-esp32-s3-port` if ever needed.

#include "vad.h"
#include "i2s_mic.h"
#include "wake.h"

#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "vad";

esp_err_t vad_record(vad_frame_cb_t on_frame, vad_cancel_cb_t on_cancel,
                     void *user, int initial_timeout_ms, vad_result_t *out)
{
    if (!on_frame || !out) return ESP_ERR_INVALID_ARG;

    *out = (vad_result_t){ .reason = VAD_STOP_MIC_ERROR };

    // i2s_mic STATS (recycled=N etc) are still useful for end-of-turn
    // diagnostics — feed_task (wake.c) drives the producer side now,
    // and we want to see if it falls behind during a turn.
    i2s_mic_reset_stats();

    const int initial_ms = initial_timeout_ms > 0 ? initial_timeout_ms : VAD_INITIAL_TIMEOUT_MS;
    const int64_t start_us            = esp_timer_get_time();
    const int64_t initial_deadline_us = start_us + (int64_t)initial_ms * 1000;
    const int64_t max_deadline_us     = start_us + (int64_t)VAD_MAX_RECORD_MS * 1000;

    bool     voice_started      = false;
    int64_t  last_voice_us      = 0;
    uint32_t peak_seen          = 0;   // retained as a diagnostic; not load-bearing
    size_t   total_samples      = 0;
    uint32_t speech_chunks      = 0;   // how many AFE_VAD_SPEECH chunks we saw
    uint32_t silence_chunks     = 0;   // how many AFE_VAD_SILENCE chunks

    while (1) {
        if (on_cancel && on_cancel(user)) {
            out->reason = VAD_STOP_BUTTON_CANCEL;
            break;
        }

        // Wait up to 200 ms for an AFE frame. Short enough that
        // deadline checks below stay responsive; long enough that we
        // don't spin during the AFE ringbuffer warmup window.
        int16_t    *frame_data   = NULL;
        size_t      frame_n      = 0;
        vad_state_t frame_vad    = VAD_SILENCE;
        esp_err_t err = wake_listen_next(&frame_data, &frame_n, &frame_vad, 200);

        if (err != ESP_OK || frame_n == 0) {
            // No frame yet — check our deadlines and continue.
            int64_t now_us = esp_timer_get_time();
            if (!voice_started && now_us >= initial_deadline_us) {
                out->reason = VAD_STOP_NO_SPEECH;
                break;
            }
            if (now_us >= max_deadline_us) {
                out->reason = VAD_STOP_OK_MAX_DURATION;
                break;
            }
            continue;
        }

        const int64_t now_us = esp_timer_get_time();

        if (frame_vad == VAD_SPEECH) {
            speech_chunks++;
            voice_started = true;
            last_voice_us = now_us;
        } else {
            silence_chunks++;
        }

        // Diagnostic peak — same calculation as the pre-v2-M6 path,
        // useful for spotting clipping / level issues even though it
        // no longer drives the state machine.
        for (size_t i = 0; i < frame_n; ++i) {
            int32_t a = frame_data[i] < 0 ? -frame_data[i] : frame_data[i];
            if ((uint32_t)a > peak_seen) peak_seen = (uint32_t)a;
        }

        // Forward to /converse. on_frame is synchronous — frame_data
        // is safe to free as soon as it returns.
        esp_err_t cb_err = on_frame(frame_data, frame_n, user);
        total_samples += frame_n;
        free(frame_data);

        if (cb_err != ESP_OK) {
            ESP_LOGW(TAG, "frame_cb returned %s; aborting", esp_err_to_name(cb_err));
            out->reason   = VAD_STOP_FRAME_CB_ERROR;
            out->cb_error = cb_err;
            break;
        }

        // Stop conditions checked AFTER forwarding.
        if (voice_started) {
            int64_t silence_ms = (now_us - last_voice_us) / 1000;
            if (silence_ms >= VAD_SILENCE_DWELL_MS) {
                out->reason = VAD_STOP_OK_SILENCE;
                break;
            }
        } else {
            if (now_us >= initial_deadline_us) {
                out->reason = VAD_STOP_NO_SPEECH;
                break;
            }
        }
        if (now_us >= max_deadline_us) {
            out->reason = VAD_STOP_OK_MAX_DURATION;
            break;
        }
    }

    out->total_samples = total_samples;
    out->peak_seen     = peak_seen;
    out->duration_ms   = (uint32_t)((esp_timer_get_time() - start_us) / 1000);

    uint32_t drops = wake_listen_drops_take_and_reset();
    ESP_LOGI(TAG, "vad stop reason=%d samples=%u peak=%lu dur=%lums "
                  "speech_chunks=%u silence_chunks=%u afe_drops=%u",
             (int)out->reason, (unsigned)out->total_samples,
             (unsigned long)out->peak_seen, (unsigned long)out->duration_ms,
             (unsigned)speech_chunks, (unsigned)silence_chunks, (unsigned)drops);
    i2s_mic_dump_stats("vad");

    bool ok = (out->reason == VAD_STOP_OK_SILENCE || out->reason == VAD_STOP_OK_MAX_DURATION);
    return ok ? ESP_OK : ESP_FAIL;
}
