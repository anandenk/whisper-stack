// v2-M2 on-device wake-word detection — see wake.h for the design rationale.

#include "wake.h"

#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "sdkconfig.h"

#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#include "esp_afe_config.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "esp_vadn_models.h"
#include "esp_vad.h"          // for vad_state_t (VAD_SILENCE / VAD_SPEECH)
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

// v2-M6-b: wake event queue. The fetch task posts events here on
// WAKENET_DETECTED instead of calling do_round_trip directly — that
// call blocks for the multi-second LISTENING → THINKING → SPEAKING
// cycle, which would freeze AFE fetching. The round_trip_driver_task
// is the only consumer; it owns the blocking call.
typedef struct {
    int16_t      *preroll;        // heap-allocated contiguous copy of the
                                  // preroll ring at wake-fire time. Driver
                                  // task frees this after do_round_trip.
    size_t        preroll_n;      // sample count in `preroll`
    double        score;          // wake score (1.0 since WakeNet doesn't expose one)
    wake_source_t source;         // always WAKE_VOICE from this path
} wake_event_t;

static QueueHandle_t s_wake_event_queue = NULL;

// v2-M6-c: AFE output queue. wake_fetch_task pushes one entry per
// fetch result while s_state == APP_STATE_LISTENING. vad_record
// (now AFE-driven) consumes via wake_listen_next() and frees the
// `data` pointer when done.
//
// Sized for ~2 s of audio at 30 ms chunk cadence (~67 chunks). If the
// consumer falls behind, drop-newest with the counter incremented —
// same pattern as i2s_mic's recycled=N (gotcha #9).
typedef struct {
    int16_t    *data;        // heap-allocated copy; consumer frees
    size_t      n_samples;
    vad_state_t vad_state;   // VAD_SILENCE / VAD_SPEECH for this chunk
} afe_frame_t;

#define AFE_OUT_QUEUE_DEPTH 67
static QueueHandle_t s_afe_out_queue = NULL;
static uint32_t      s_afe_out_drops = 0;   // single-writer (fetch_task)

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
        // v2-M6: feed_task runs in all states (was IDLE-only in M2).
        // The mic ring's single-consumer invariant holds because
        // vad_record was refactored to consume from afe_out_queue
        // instead of i2s_mic directly (see vad.c). AFE is now the
        // sole source of LISTENING audio.

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

// Drain any pending entries from afe_out_queue. Called on transitions
// into LISTENING so frames from the SPEAKING window don't contaminate
// the new turn's audio. Producer side (this task) is the sole writer,
// so a quick poll-drain race-frees pending data with no consumer
// fighting us.
static void afe_out_queue_drain(void)
{
    if (!s_afe_out_queue) return;
    afe_frame_t frame;
    while (xQueueReceive(s_afe_out_queue, &frame, 0) == pdTRUE) {
        free(frame.data);
    }
    s_afe_out_drops = 0;
}

static void fetch_task(void *arg)
{
    (void)arg;

    ESP_LOGI(TAG, "fetch_task running");

    // v2-M6: track VAD state transitions for logging. Per-chunk vad_state
    // is fired ~30× per second; only log when it actually flips, which
    // happens at speech onset / offset (typically a few times per turn).
    vad_state_t last_vad = VAD_SILENCE;

    // v2-M6-d: track app_state to detect SPEAKING/THINKING/IDLE → LISTENING
    // transitions. On entry to LISTENING we reset AFE's internal state
    // (so residual SPEECH classification from TTS echo during SPEAKING
    // doesn't carry over) and drain afe_out_queue. Without this, follow-up
    // turns hear the previous reply's TTS echo as user speech and loop.
    app_state_t prev_state = s_state;

    while (true) {
        afe_fetch_result_t *res = s_afe_handle->fetch(s_afe_data);

        // State transition handling — done BEFORE the fetch result is
        // examined so the reset takes effect for this iteration's
        // queue push decisions.
        app_state_t cur_state = s_state;
        if (cur_state == APP_STATE_LISTENING && prev_state != APP_STATE_LISTENING) {
            // Entering LISTENING. Reset AFE so vad_state starts fresh
            // at SILENCE (otherwise the ~992 ms vadnet1 noise dwell
            // would tag frames as SPEECH from residual TTS echo).
            if (s_afe_handle->reset_buffer) {
                s_afe_handle->reset_buffer(s_afe_data);
            }
            afe_out_queue_drain();
            last_vad = VAD_SILENCE;
            ESP_LOGI(TAG, "entering LISTENING — AFE state reset");
        }
        prev_state = cur_state;
        if (!res || res->ret_value < 0) {
            // ret_value == -1 typically means timeout / empty feed.
            // Common during state transitions where the feed task has
            // paused — just back off and retry.
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        // VAD transition log — useful for M6-a verification, also for
        // diagnosing later VAD-related bugs. Only logs on flip; quiet
        // by default.
        if (res->vad_state != last_vad) {
            ESP_LOGI(TAG, "vad: %s → %s (vol=%.1f dB)",
                     last_vad == VAD_SPEECH ? "SPEECH" : "SILENCE",
                     res->vad_state == VAD_SPEECH ? "SPEECH" : "SILENCE",
                     res->data_volume);
            last_vad = res->vad_state;
        }

        // v2-M6-c: route LISTENING audio through afe_out_queue. Copy
        // the data because AFE will reuse the `data` pointer on the
        // next fetch. Drop-newest on full so vad_record's fall-behind
        // doesn't break the pipeline — counter for diagnostics.
        if (s_state == APP_STATE_LISTENING && res->data && res->data_size > 0) {
            size_t n_samples = res->data_size / sizeof(int16_t);
            int16_t *copy = malloc(res->data_size);
            if (!copy) {
                s_afe_out_drops++;   // out of memory — treat as drop
            } else {
                memcpy(copy, res->data, res->data_size);
                afe_frame_t frame = {
                    .data       = copy,
                    .n_samples  = n_samples,
                    .vad_state  = res->vad_state,
                };
                if (xQueueSend(s_afe_out_queue, &frame, 0) != pdTRUE) {
                    free(copy);
                    s_afe_out_drops++;
                }
            }
        }

        if (res->wakeup_state != WAKENET_DETECTED) {
            continue;
        }

        ESP_LOGI(TAG, "wake fired (model_idx=%d word_idx=%d vol=%.1f dB)",
                 res->wakenet_model_index, res->wake_word_index,
                 res->data_volume);

        // v2-M6-b: don't call do_round_trip directly — that blocks
        // for the multi-second cycle and would freeze AFE fetching.
        // Instead: snapshot preroll into a heap-allocated contiguous
        // buffer (so feed_task can't overwrite it later), post a
        // wake_event_t to the queue, and continue. The
        // round_trip_driver_task is the one that blocks.
        int16_t *seg1 = NULL, *seg2 = NULL;
        size_t seg1_n = 0, seg2_n = 0;
        preroll_snapshot(&seg1, &seg1_n, &seg2, &seg2_n);

        wake_event_t evt = {
            .preroll = NULL,
            .preroll_n = seg1_n + seg2_n,
            .score = 1.0,
            .source = WAKE_VOICE,
        };
        if (evt.preroll_n > 0) {
            evt.preroll = malloc(evt.preroll_n * sizeof(int16_t));
            if (evt.preroll) {
                if (seg1_n > 0) memcpy(evt.preroll, seg1, seg1_n * sizeof(int16_t));
                if (seg2_n > 0) memcpy(evt.preroll + seg1_n, seg2, seg2_n * sizeof(int16_t));
            } else {
                ESP_LOGW(TAG, "preroll malloc(%u) failed — proceeding with no preroll",
                         (unsigned)(evt.preroll_n * sizeof(int16_t)));
                evt.preroll_n = 0;
            }
        }

        // Non-blocking enqueue (drop if full — can't handle two
        // concurrent rounds anyway since do_round_trip is serial).
        if (xQueueSend(s_wake_event_queue, &evt, 0) != pdTRUE) {
            ESP_LOGW(TAG, "wake_event_queue full — dropping wake event");
            free(evt.preroll);
        }

        // Reset AFE internal state for the next wake cycle (cleaner
        // than letting it accumulate while the round-trip runs).
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

// v2-M6-b: round-trip driver. Owns the multi-second blocking
// do_round_trip call, decoupled from wake_fetch_task so AFE can
// keep processing during LISTENING / THINKING / SPEAKING.
static void round_trip_driver_task(void *arg)
{
    (void)arg;
    ESP_LOGI(TAG, "round_trip_driver_task running");

    while (true) {
        wake_event_t evt;
        if (xQueueReceive(s_wake_event_queue, &evt, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        // Pass preroll as a single contiguous segment (the queue stored
        // the seg1+seg2 concatenation). do_round_trip's seg2/seg2_n
        // parameters accept NULL/0 for "no second segment."
        do_round_trip_streaming_with_preroll(s_ip,
                                             evt.preroll, evt.preroll_n,
                                             NULL, 0,
                                             evt.score, evt.source);

        free(evt.preroll);
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

    // v2-M6: resolve the vadnet model name. NULL is acceptable — AFE
    // falls back to WebRTC VAD if the ML model isn't packed.
    char *vadn_name = esp_srmodel_filter(s_models, ESP_VADN_PREFIX, NULL);
    if (vadn_name) {
        ESP_LOGI(TAG, "resolved vad model: %s (ML-based vadnet)", vadn_name);
    } else {
        ESP_LOGI(TAG, "no vadnet model packed — AFE will use built-in WebRTC VAD");
    }

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

    // Tune the defaults for our use case:
    //   - AEC off: no on-device speaker output yet (M3); reference
    //     channel doesn't exist. AEC stays off until v2-M5.
    //   - VAD on (v2-M6): vadnet1_medium ML VAD discriminates real
    //     speech from typing/clicks. The fetch result's vad_state
    //     drives LISTENING audio capture in M6-c+; for M6-a we just
    //     log the transitions.
    //   - WakeNet on with the resolved model name.
    //   - SE task pinned to core 1: WiFi pins to core 0, and AFE's
    //     SE task fighting WiFi on the same core is a known latency
    //     cliff in other esp-sr projects.
    cfg->aec_init               = false;
    cfg->vad_init               = true;
    cfg->vad_model_name         = vadn_name;   // NULL → WebRTC fallback
    cfg->vad_min_speech_ms      = CONFIG_WAKE_VAD_MIN_SPEECH_MS;
    cfg->wakenet_init           = true;
    cfg->wakenet_model_name     = wn_name;
    cfg->afe_perferred_core     = 1;
    cfg->afe_perferred_priority = 5;

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

    // v2-M6-b wake event queue. Depth 1 — wake events are serialized
    // (do_round_trip blocks), so a deeper queue would just store
    // events we can't process anyway. Drop-newest on full instead.
    s_wake_event_queue = xQueueCreate(1, sizeof(wake_event_t));
    if (!s_wake_event_queue) {
        ESP_LOGE(TAG, "wake_event_queue create failed");
        return ESP_ERR_NO_MEM;
    }

    // v2-M6-c AFE output queue. Sized for ~2 s of audio at the
    // 32 ms feed cadence (~63 chunks). Drop-newest on full;
    // s_afe_out_drops surfaces consumer-lag via wake_listen_drops_take_and_reset.
    s_afe_out_queue = xQueueCreate(AFE_OUT_QUEUE_DEPTH, sizeof(afe_frame_t));
    if (!s_afe_out_queue) {
        ESP_LOGE(TAG, "afe_out_queue create failed");
        return ESP_ERR_NO_MEM;
    }

    // Tasks: priority 5 sits between idle (1) and the OS-critical
    // tasks (≥10). 4 KB stacks blow under AFE's internal call frames
    // (the WakeNet inference call uses several KB of stack just for
    // its work buffers) — bump to 8 KB. feed_task's scratch + accum
    // buffers live on the heap now so the stack is dominated by AFE
    // call frames.
    //
    // Spawn fetch_task FIRST so AFE's internal ring has a draining
    // consumer before feed_task starts pushing. Without this, several
    // "Ringbuffer of AFE(FEED) is full" warnings spam the log during
    // the first ~100 ms while the consumer races to start. The warnings
    // are benign (AFE drops frames gracefully) but they pollute the
    // boot signal-to-noise.
    //
    // round_trip_driver gets a slightly bigger stack because do_round_trip
    // dives into vad_record + http_client which together have a deeper
    // call tree than AFE fetch.
    xTaskCreate(fetch_task,              "wake_fetch",  8192, NULL, 5, NULL);
    xTaskCreate(feed_task,               "wake_feed",   8192, NULL, 5, NULL);
    xTaskCreate(round_trip_driver_task,  "rt_driver",  12288, NULL, 5, NULL);

    return ESP_OK;
}

// --- Public API (v2-M6-c) -----------------------------------------------

esp_err_t wake_listen_next(int16_t **data, size_t *n_samples,
                           vad_state_t *vad_state, uint32_t timeout_ms)
{
    if (!s_afe_out_queue) return ESP_ERR_INVALID_STATE;
    if (!data || !n_samples || !vad_state) return ESP_ERR_INVALID_ARG;

    afe_frame_t frame;
    TickType_t ticks = (timeout_ms == UINT32_MAX) ? portMAX_DELAY
                                                  : pdMS_TO_TICKS(timeout_ms);
    if (xQueueReceive(s_afe_out_queue, &frame, ticks) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    *data      = frame.data;        // ownership transferred to caller
    *n_samples = frame.n_samples;
    *vad_state = frame.vad_state;
    return ESP_OK;
}

uint32_t wake_listen_drops_take_and_reset(void)
{
    // Single-writer (fetch_task) + single-reader (vad_record at end of
    // turn) — no atomicity concerns. Worst case the reader picks up a
    // slightly stale value if a drop happens mid-read; that's fine.
    uint32_t v = s_afe_out_drops;
    s_afe_out_drops = 0;
    return v;
}
