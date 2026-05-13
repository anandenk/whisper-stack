// v2-M2 on-device wake-word detection — see wake.h for the design rationale.

#include "wake.h"

#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "sdkconfig.h"

#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#include "esp_afe_config.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "model_path.h"

#include "app_state.h"
#include "i2s_mic.h"
#include "wake_source.h"

static const char *TAG = "wake";

// If CONFIG_VOICE_ENABLED=n, the gain Kconfig isn't generated (the file
// is still compiled because it's in SRCS unconditionally). Provide a
// no-op default so any reference compiles cleanly. The actual call sites
// are unreachable in that build because main.c doesn't call wake_init().
#ifndef CONFIG_WAKE_INPUT_GAIN_X10
#define CONFIG_WAKE_INPUT_GAIN_X10 10
#endif

// --- main.c interop ------------------------------------------------------

// Same shared state the rest of the firmware reads. main.c declares
// s_state as `static` but listen_stream.c references it via extern,
// so the symbol is global at link time even though file-scoped in C.
extern app_state_t s_state;

// Wake → round-trip entry point in main.c. Same signature listen_stream
// uses; the wake_source distinguishes VOICE / GESTURE / BUTTON paths.
void do_round_trip_streaming_with_preroll(const char *ip,
                                          const int16_t *preroll_seg1,
                                          size_t preroll_seg1_n,
                                          const int16_t *preroll_seg2,
                                          size_t preroll_seg2_n,
                                          double wake_score,
                                          wake_source_t wake_source);

// --- Module state --------------------------------------------------------

static const esp_afe_sr_iface_t *s_afe_handle = NULL;
static esp_afe_sr_data_t        *s_afe_data   = NULL;
static srmodel_list_t            *s_models    = NULL;
static const char                *s_ip        = NULL;
static int                        s_feed_chunk_n = 0;   // samples per feed call

// Preroll ring — captures ~1 s of raw mic audio before wake fires so
// the start of the user's question (which often runs straight through
// the wake word) isn't lost. Same shape and size as listen_stream's
// preroll. Allocated from PSRAM-eligible heap to keep DRAM available.
#define PREROLL_SAMPLES 16000   // 1.0 s @ 16 kHz mono
static int16_t          *s_preroll      = NULL;
static size_t            s_preroll_wr   = 0;     // next write index
static bool              s_preroll_full = false; // true after first wrap
static SemaphoreHandle_t s_preroll_mu   = NULL;

// --- Helpers -------------------------------------------------------------

static void apply_gain(int16_t *samples, size_t n)
{
    // Defense-in-depth: both the != 10 fast path and the defined-check
    // matter. If CONFIG_VOICE_ENABLED is off the gain macro may not
    // exist; if gain == 10 (1.0×) we want a no-op even when defined.
#if defined(CONFIG_WAKE_INPUT_GAIN_X10) && CONFIG_WAKE_INPUT_GAIN_X10 != 10
    const int gain = CONFIG_WAKE_INPUT_GAIN_X10;
    for (size_t i = 0; i < n; i++) {
        int32_t v = ((int32_t)samples[i] * gain) / 10;
        if (v > 32767) v = 32767;
        else if (v < -32768) v = -32768;
        samples[i] = (int16_t)v;
    }
#else
    (void)samples; (void)n;
#endif
}

static void preroll_write(const int16_t *samples, size_t n)
{
    xSemaphoreTake(s_preroll_mu, portMAX_DELAY);
    while (n > 0) {
        size_t room = PREROLL_SAMPLES - s_preroll_wr;
        size_t chunk = n < room ? n : room;
        memcpy(s_preroll + s_preroll_wr, samples, chunk * sizeof(int16_t));
        s_preroll_wr += chunk;
        samples += chunk;
        n -= chunk;
        if (s_preroll_wr >= PREROLL_SAMPLES) {
            s_preroll_wr = 0;
            s_preroll_full = true;
        }
    }
    xSemaphoreGive(s_preroll_mu);
}

// Snapshot the most recent up-to-PREROLL_SAMPLES samples as up-to-two
// contiguous segments. Caller must invoke this with the feed task
// already paused (i.e., after the wake fires and before resuming feed)
// — segments point into s_preroll directly, no copy. The feed task only
// runs in APP_STATE_IDLE, and a wake event sets s_state to LISTENING
// before this snapshot is taken, so the no-copy is safe.
static void preroll_snapshot(int16_t **seg1, size_t *seg1_n,
                             int16_t **seg2, size_t *seg2_n)
{
    xSemaphoreTake(s_preroll_mu, portMAX_DELAY);
    if (!s_preroll_full) {
        // Buffer not yet wrapped — single segment [0 .. wr).
        *seg1   = s_preroll;
        *seg1_n = s_preroll_wr;
        *seg2   = NULL;
        *seg2_n = 0;
    } else {
        // Wrapped — oldest data is at wr, newest is just before wr.
        // Reconstruct chronological order: [wr .. end) then [0 .. wr).
        *seg1   = s_preroll + s_preroll_wr;
        *seg1_n = PREROLL_SAMPLES - s_preroll_wr;
        *seg2   = s_preroll;
        *seg2_n = s_preroll_wr;
    }
    xSemaphoreGive(s_preroll_mu);
}

// --- Tasks ---------------------------------------------------------------

static void feed_task(void *arg)
{
    (void)arg;

    // Buffers off the stack: scratch holds one i2s_mic_capture_frame
    // (~2.5 KB) and accum holds the residue from one feed plus a fresh
    // mic frame.
    //
    // Sizing accum: after each AFE feed, residue is < s_feed_chunk_n.
    // The next mic capture appends up to I2S_MIC_FRAME_SAMPLES. So
    // worst-case post-memcpy size is (s_feed_chunk_n - 1) +
    // I2S_MIC_FRAME_SAMPLES. Earlier this was wrongly sized as
    // s_feed_chunk_n * 2, which overflows when chunks aren't divisible
    // (heap corruption → crash in unrelated task's malloc).
    const size_t accum_capacity = s_feed_chunk_n + I2S_MIC_FRAME_SAMPLES + 16; // +16 paranoia margin
    int16_t *scratch = malloc(I2S_MIC_FRAME_SAMPLES * sizeof(int16_t));
    int16_t *accum   = malloc(accum_capacity * sizeof(int16_t));
    if (!scratch || !accum) {
        ESP_LOGE(TAG, "feed_task: buffer alloc failed (scratch=%p accum=%p)",
                 (void *)scratch, (void *)accum);
        free(scratch);
        free(accum);
        vTaskDelete(NULL);
        return;
    }
    size_t accum_n = 0;

    ESP_LOGI(TAG, "feed_task running (feed_chunk=%d samples / %d ms)",
             s_feed_chunk_n, s_feed_chunk_n * 1000 / 16000);

    while (true) {
        // State-machine routing: only consume mic in IDLE. During
        // LISTENING/THINKING/SPEAKING, vad_record / http write tasks
        // own the mic ring and we'd race them.
        if (s_state != APP_STATE_IDLE) {
            accum_n = 0;   // drop partial chunk on state exit
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        // Pull whatever's available from the mic ring (blocking up to
        // 100 ms for the first sample). Capacity matches one i2s_mic
        // frame (80 ms / 1280 samples) — typical case is one full frame
        // per call, but the API allows shorter.
        size_t got = 0;
        if (i2s_mic_capture_frame(scratch, I2S_MIC_FRAME_SAMPLES,
                                  &got, 100) != ESP_OK || got == 0) {
            continue;
        }

        apply_gain(scratch, got);

        // Preroll ring captures pre-AFE samples so /converse gets the
        // raw audio that whisper expects (server-side _peak_normalize
        // handles level compensation, not AFE's NS/AGC).
        preroll_write(scratch, got);

        // Drain into AFE in feed_chunk-sized chunks.
        memcpy(accum + accum_n, scratch, got * sizeof(int16_t));
        accum_n += got;
        while (accum_n >= (size_t)s_feed_chunk_n) {
            s_afe_handle->feed(s_afe_data, accum);
            // Shift the residue down — typically a few hundred samples.
            size_t residue = accum_n - s_feed_chunk_n;
            if (residue > 0) {
                memmove(accum, accum + s_feed_chunk_n,
                        residue * sizeof(int16_t));
            }
            accum_n = residue;
        }
    }
}

static void fetch_task(void *arg)
{
    (void)arg;

    ESP_LOGI(TAG, "fetch_task running");

    while (true) {
        afe_fetch_result_t *res = s_afe_handle->fetch(s_afe_data);
        if (!res || res->ret_value < 0) {
            // ret_value == -1 typically means timeout / empty feed.
            // Common during state transitions where the feed task has
            // paused — just back off and retry.
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        if (res->wakeup_state != WAKENET_DETECTED) {
            continue;
        }

        ESP_LOGI(TAG, "wake fired (model_idx=%d word_idx=%d vol=%.1f dB)",
                 res->wakenet_model_index, res->wake_word_index,
                 res->data_volume);

        // Snapshot preroll and hand off to the round-trip helper. The
        // wake event drives main.c's state machine into LISTENING
        // inside do_round_trip, which makes the feed task back off
        // and stop touching the preroll buffer for the duration of
        // the turn. Safe to pass non-copied segments.
        int16_t *seg1 = NULL, *seg2 = NULL;
        size_t seg1_n = 0, seg2_n = 0;
        preroll_snapshot(&seg1, &seg1_n, &seg2, &seg2_n);

        // WakeNet's fetch result doesn't expose a continuous-valued
        // detection score — pass 1.0 so the OLED splash has a
        // sensible-looking number. (Server-side openWakeWord's score
        // was the source of the old non-1.0 numbers; we don't have
        // an equivalent on-device.)
        do_round_trip_streaming_with_preroll(s_ip, seg1, seg1_n,
                                             seg2, seg2_n,
                                             1.0, WAKE_VOICE);

        // Reset AFE internal state for the next wake cycle. The feed
        // task is currently paused (state != IDLE) — when it resumes
        // in IDLE after the turn ends, AFE picks up cleanly.
        if (s_afe_handle->reset_buffer) {
            s_afe_handle->reset_buffer(s_afe_data);
        }

        // Reset our own preroll so the next turn doesn't see leftover
        // audio from the previous one. The next batch of feed_task
        // writes will repopulate.
        xSemaphoreTake(s_preroll_mu, portMAX_DELAY);
        s_preroll_wr = 0;
        s_preroll_full = false;
        xSemaphoreGive(s_preroll_mu);
    }
}

// --- Public init ---------------------------------------------------------

esp_err_t wake_init(const char *ip)
{
    if (s_afe_data) return ESP_OK;   // idempotent

    s_ip = ip;

    // Load models from the dedicated `model` partition (custom CSV
    // packed by movemodel.py during build). Lifetime is forever —
    // AFE holds references to the model data.
    s_models = esp_srmodel_init("model");
    if (!s_models) {
        ESP_LOGE(TAG, "esp_srmodel_init('model') returned NULL");
        return ESP_FAIL;
    }

    char *wn_name = esp_srmodel_filter(s_models, ESP_WN_PREFIX, NULL);
    if (!wn_name) {
        ESP_LOGE(TAG, "no WakeNet (wn*) model packed — check menuconfig");
        esp_srmodel_deinit(s_models);
        s_models = NULL;
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "resolved wake model: %s", wn_name);

    // Single mic, no reference channel → input_format "M". SR type
    // pulls in WakeNet + NS + AGC; LOW_COST mode picks the lighter
    // algorithm variants (sufficient for our use, leaves CPU for WiFi).
    afe_config_t *cfg = afe_config_init("M", s_models,
                                        AFE_TYPE_SR, AFE_MODE_LOW_COST);
    if (!cfg) {
        ESP_LOGE(TAG, "afe_config_init returned NULL");
        esp_srmodel_deinit(s_models);
        s_models = NULL;
        return ESP_FAIL;
    }

    // Tune the defaults for our wake-only use case:
    //   - AEC off: no on-device speaker output yet (M3); reference
    //     channel doesn't exist. AEC stays off until v2-M5.
    //   - VAD off: vad.c has its own VAD on the raw mic ring during
    //     LISTENING. AFE's VAD would only matter during IDLE, where
    //     WakeNet handles the trigger.
    //   - WakeNet on with the resolved model name.
    cfg->aec_init           = false;
    cfg->vad_init           = false;
    cfg->wakenet_init       = true;
    cfg->wakenet_model_name = wn_name;

    // esp-sr 2.4.4 dropped the static ESP_AFE_SR_HANDLE global in favor of
    // a config-aware factory function. The returned iface pointer depends
    // on whether AEC / SE / etc. are enabled.
    s_afe_handle = esp_afe_handle_from_config(cfg);
    if (!s_afe_handle) {
        ESP_LOGE(TAG, "esp_afe_handle_from_config returned NULL — bad config");
        afe_config_free(cfg);
        esp_srmodel_deinit(s_models);
        s_models = NULL;
        return ESP_FAIL;
    }
    s_afe_data = s_afe_handle->create_from_config(cfg);
    afe_config_free(cfg);
    if (!s_afe_data) {
        ESP_LOGE(TAG, "AFE create_from_config returned NULL — OOM?");
        esp_srmodel_deinit(s_models);
        s_models = NULL;
        return ESP_FAIL;
    }

    s_feed_chunk_n = s_afe_handle->get_feed_chunksize(s_afe_data);
    ESP_LOGI(TAG, "AFE ready: feed_chunk=%d samples (%d ms), "
                  "input_gain=%d.%dx",
             s_feed_chunk_n, s_feed_chunk_n * 1000 / 16000,
             CONFIG_WAKE_INPUT_GAIN_X10 / 10,
             CONFIG_WAKE_INPUT_GAIN_X10 % 10);

    // Preroll ring — heap_caps_malloc lets the allocator pick PSRAM
    // if available, keeping internal DRAM free for AFE's hot paths.
    s_preroll = heap_caps_malloc(PREROLL_SAMPLES * sizeof(int16_t),
                                 MALLOC_CAP_8BIT);
    if (!s_preroll) {
        ESP_LOGE(TAG, "preroll buffer alloc (%d B) failed",
                 (int)(PREROLL_SAMPLES * sizeof(int16_t)));
        // AFE handle is going to leak here — but if we can't allocate
        // 32 KB of any heap, the system isn't going to keep running
        // anyway, so cleanup-on-failure isn't load-bearing.
        return ESP_ERR_NO_MEM;
    }
    s_preroll_mu = xSemaphoreCreateMutex();

    // Tasks: priority 5 sits between idle (1) and the OS-critical
    // tasks (≥10). 4 KB stacks blow under AFE's internal call frames
    // (the WakeNet inference call uses several KB of stack just for
    // its work buffers) — bump to 8 KB. feed_task's scratch + accum
    // buffers live on the heap now so the stack is dominated by AFE
    // call frames.
    //
    // Spawn fetch_task FIRST so AFE's internal ring has a draining
    // consumer before feed_task starts pushing. Without this, ~5
    // "Ringbuffer of AFE(FEED) is full" warnings spam the log during
    // the first 50 ms while the consumer races to start. The warnings
    // are benign (AFE drops frames gracefully) but they look alarming
    // and pollute the boot signal-to-noise.
    xTaskCreate(fetch_task, "wake_fetch", 8192, NULL, 5, NULL);
    xTaskCreate(feed_task,  "wake_feed",  8192, NULL, 5, NULL);

    return ESP_OK;
}
