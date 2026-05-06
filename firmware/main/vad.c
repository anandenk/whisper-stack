// Peak-threshold VAD — see header.
//
// Algorithm matches tests/jarvis.py's defaults:
//   * Compute frame peak = max |sample|.
//   * If peak ≥ threshold, mark "voice has started" and reset the
//     silence-dwell timer; otherwise increment it.
//   * Stop when silence dwell ≥ 1500 ms AFTER voice has been heard.
//   * If no voice ever crosses threshold within the initial timeout
//     (15 s), stop with NO_SPEECH so we don't upload an empty record.
//   * Hard cap at MAX_RECORD_MS as a runaway-protection safety net.

#include "vad.h"
#include "i2s_mic.h"

#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "vad";

// 1280 samples = 80 ms @ 16 kHz, the size audio_in_task pushes.
#define VAD_FRAME_SAMPLES 1280

static inline uint32_t frame_peak(const int16_t *p, size_t n)
{
    uint32_t pk = 0;
    for (size_t i = 0; i < n; ++i) {
        int32_t a = p[i] < 0 ? -p[i] : p[i];
        if ((uint32_t)a > pk) pk = (uint32_t)a;
    }
    return pk;
}

esp_err_t vad_record(vad_frame_cb_t on_frame, vad_cancel_cb_t on_cancel,
                     void *user, vad_result_t *out)
{
    if (!on_frame || !out) return ESP_ERR_INVALID_ARG;

    static int16_t frame[VAD_FRAME_SAMPLES];

    *out = (vad_result_t){ .reason = VAD_STOP_MIC_ERROR };

    i2s_mic_drain();

    const int64_t start_us       = esp_timer_get_time();
    const int64_t initial_deadline_us = start_us + (int64_t)VAD_INITIAL_TIMEOUT_MS * 1000;
    const int64_t max_deadline_us     = start_us + (int64_t)VAD_MAX_RECORD_MS * 1000;

    bool     voice_started      = false;
    int64_t  last_voice_us      = 0;
    uint32_t peak_seen          = 0;
    size_t   total_samples      = 0;

    while (1) {
        if (on_cancel && on_cancel(user)) {
            out->reason = VAD_STOP_BUTTON_CANCEL;
            break;
        }

        size_t got = 0;
        esp_err_t err = i2s_mic_capture_frame(frame, VAD_FRAME_SAMPLES, &got, 200);
        if (err != ESP_OK || got == 0) {
            // Mic timeout for one wait window is fine; just check our deadlines.
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

        uint32_t pk = frame_peak(frame, got);
        if (pk > peak_seen) peak_seen = pk;

        const int64_t now_us = esp_timer_get_time();
        if (pk >= VAD_PEAK_THRESHOLD) {
            voice_started = true;
            last_voice_us = now_us;
        }

        // Always forward frames once recording starts. Frames before voice
        // are still forwarded so the server gets a small lead-in (helps
        // STT pick up the first phoneme even if it's slightly below the
        // peak threshold).
        esp_err_t cb_err = on_frame(frame, got, user);
        if (cb_err != ESP_OK) {
            ESP_LOGW(TAG, "frame_cb returned %s; aborting", esp_err_to_name(cb_err));
            out->reason   = VAD_STOP_FRAME_CB_ERROR;
            out->cb_error = cb_err;
            total_samples += got;
            break;
        }
        total_samples += got;

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

    ESP_LOGI(TAG, "vad stop reason=%d samples=%u peak=%lu dur=%lums",
             (int)out->reason, (unsigned)out->total_samples,
             (unsigned long)out->peak_seen, (unsigned long)out->duration_ms);

    bool ok = (out->reason == VAD_STOP_OK_SILENCE || out->reason == VAD_STOP_OK_MAX_DURATION);
    return ok ? ESP_OK : ESP_FAIL;
}
