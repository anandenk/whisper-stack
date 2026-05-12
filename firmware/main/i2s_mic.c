// INMP441 → I²S0 → 16 kHz mono int16 ring buffer.
//
// Pin map (v2, ESP32-S3-DevKitC-1 N8R8 — see notes/esp32-firmware-plan.md):
//   I²S0 BCK (SCK) = GPIO 4
//   I²S0 WS  (LRCK)= GPIO 5
//   I²S0 SD  (DOUT)= GPIO 6
//   INMP441 L/R    = tied to GND (mono left-channel slot)
//
// v1 used 26/25/33 on the WROOM-32; those GPIOs are claimed by flash +
// octal PSRAM on the S3 N8R8 and cannot be reused. The v1 pinout is
// recoverable at git tag `pre-esp32-s3-port`.
//
// The new driver/i2s_std.h replaces the legacy driver/i2s.h API. Tutorials
// from before ~2023 won't apply.
//
// Ring buffer policy: audio_in_task pushes int16 frames non-blocking; if
// the buffer is full (no consumer reading) the new frame is dropped. That
// keeps memory bounded during idle. M2's capture API drains stale audio
// at entry, so the captured window starts cleanly from the moment of the
// call regardless of how long the buffer has been idling.

#include "i2s_mic.h"
#include "app_state.h"

#include <string.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "driver/i2s_std.h"

static const char *TAG = "i2s_mic";

#define I2S_BCK_GPIO 4
#define I2S_WS_GPIO  5
#define I2S_SD_GPIO  6

// 64 KB byte buffer = 32768 int16 samples = ~2.0 s at 16 kHz. Absorbs
// chunked-upload backpressure under WiFi jitter that previously dropped
// 80 ms frames at 12/sec — original "wha~ what is the in mon~mongolia"
// audio corruption.
//
// **Heap math:** an earlier attempt at 64 KB broke boot because the
// listen_stream task held an always-live 32 KB snapshot buffer on top
// of a 32 KB preroll ring, so total audio heap was 64 + 32 + 32 = 128 KB
// and the second 32 KB heap_caps_malloc would fail. Eliminated the
// snapshot (preroll segments stream straight from the ring memory now —
// see preroll_segments() in listen_stream.c), so total is 64 + 32 =
// 96 KB. Net heap: 16 KB *less* than the pre-fix steady state of 80 KB
// after counting the freed snapshot.
#define MIC_RINGBUF_BYTES (64 * 1024)

static i2s_chan_handle_t s_rx = NULL;
static volatile uint32_t s_last_rms = 0;
static volatile uint32_t s_last_peak = 0;
static RingbufHandle_t   s_rb = NULL;
static bool s_initialized = false;

// Diagnostic counters — see i2s_mic_reset_stats / i2s_mic_dump_stats below.
// Used to localize an audio-corruption bug: distinguish producer-side
// recycle drops from consumer-side BYTEBUF over-consumption.
static volatile uint32_t s_frames_pushed;
static volatile uint32_t s_frames_recycled;
static volatile uint32_t s_capture_calls;
static volatile uint32_t s_capture_bytes_dropped;
static volatile uint32_t s_capture_max_chunk_n;

void i2s_mic_reset_stats(void)
{
    s_frames_pushed = 0;
    s_frames_recycled = 0;
    s_capture_calls = 0;
    s_capture_bytes_dropped = 0;
    s_capture_max_chunk_n = 0;
}

void i2s_mic_dump_stats(const char *label)
{
    ESP_LOGI(TAG, "STATS[%s] pushed=%u recycled=%u capture_calls=%u "
                  "bytes_dropped=%u max_chunk_n=%u",
             label ? label : "?",
             (unsigned)s_frames_pushed, (unsigned)s_frames_recycled,
             (unsigned)s_capture_calls, (unsigned)s_capture_bytes_dropped,
             (unsigned)s_capture_max_chunk_n);
}

// Defined in main.c. Used only by the targeted overflow-during-LISTENING
// log below — a real audio-loss signal vs the harmless idle-period
// recycling that happens during SPEAKING/muted/etc.
extern app_state_t s_state;
static inline app_state_t s_state_for_overflow_log(void) { return s_state; }

// Convert one 32-bit I²S slot containing a 24-bit MSB-aligned signed sample
// into int16 by dropping the bottom 16 bits. This is the safest conversion
// (it's just the top 16 bits of the 24-bit data, can't clip) but it leaves
// conversational speech quite quiet at typical distance. We compensate
// server-side via peak-normalization in app.py — auto-scales every received
// recording to a target peak level, never clips, tunable without reflashing.
static inline int16_t slot32_to_int16(int32_t s) { return (int16_t)(s >> 16); }

static void audio_in_task(void *arg)
{
    static int32_t raw[I2S_MIC_FRAME_SAMPLES];
    static int16_t pcm[I2S_MIC_FRAME_SAMPLES];

    while (1) {
        size_t got = 0;
        esp_err_t err = i2s_channel_read(s_rx, raw, sizeof raw, &got, portMAX_DELAY);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "i2s_channel_read: %s", esp_err_to_name(err));
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        size_t n = got / sizeof(int32_t);
        if (n == 0) continue;

        uint64_t sumsq = 0;
        uint32_t peak = 0;
        for (size_t i = 0; i < n; ++i) {
            int16_t s = slot32_to_int16(raw[i]);
            pcm[i] = s;
            int32_t a = s < 0 ? -s : s;
            if ((uint32_t)a > peak) peak = (uint32_t)a;
            sumsq += (uint64_t)((int32_t)s * (int32_t)s);
        }
        s_last_peak = peak;
        s_last_rms  = (uint32_t)sqrt((double)sumsq / (double)n);

        if (s_rb) {
            // Non-blocking send. If the ring is full (no consumer is
            // draining — happens during SPEAKING countdown, muted state,
            // any idle gap between turns) recycle the oldest chunk and
            // retry, so the ring stays full of the *most recent* audio.
            //
            // This is semantically what we want: both real consumers
            // (run_listen_session and vad_record) call i2s_mic_drain()
            // on entry to discard stale audio anyway, so nothing
            // valuable is lost when we toss old chunks here. Earlier
            // versions logged each drop, but that's a 13 Hz log flood
            // through every passive period and the audio quality
            // doesn't actually suffer.
            //
            // The targeted log below only fires if recycle-on-overflow
            // happens during LISTENING (a real consumer is supposed to
            // be active) — that's the only state where it'd corrupt
            // the user's speech, so we want to know about it.
            BaseType_t ok = xRingbufferSend(s_rb, pcm, n * sizeof(int16_t), 0);
            if (ok != pdTRUE) {
                // Drop only one frame's worth, not the entire contiguous
                // block at the read pointer (which could be many frames).
                // ReceiveUpTo bounds the drop to what we need to make room
                // for the new frame. Same BYTEBUF rationale as the consumer
                // path — see i2s_mic_capture_frame.
                size_t old_n;
                void *old = xRingbufferReceiveUpTo(s_rb, &old_n, 0,
                                                   n * sizeof(int16_t));
                if (old) vRingbufferReturnItem(s_rb, old);
                xRingbufferSend(s_rb, pcm, n * sizeof(int16_t), 0);
                s_frames_recycled++;

                if (s_state_for_overflow_log() == APP_STATE_LISTENING) {
                    static int64_t last_log_us = 0;
                    int64_t now_us = esp_timer_get_time();
                    if (now_us - last_log_us > 1000000) {
                        ESP_LOGW(TAG, "ring overflow during LISTENING — "
                                       "real audio loss; HTTP/WiFi too slow");
                        last_log_us = now_us;
                    }
                }
            }
            s_frames_pushed++;
        }
    }
}

esp_err_t i2s_mic_init(void)
{
    if (s_initialized) return ESP_OK;

    s_rb = xRingbufferCreate(MIC_RINGBUF_BYTES, RINGBUF_TYPE_BYTEBUF);
    if (!s_rb) {
        ESP_LOGE(TAG, "xRingbufferCreate failed");
        return ESP_ERR_NO_MEM;
    }

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num  = 4;
    chan_cfg.dma_frame_num = 1024;     // ~64 ms of slack at 16 kHz
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, NULL, &s_rx));

    i2s_std_config_t std_cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(I2S_MIC_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_BCK_GPIO,
            .ws   = I2S_WS_GPIO,
            .dout = I2S_GPIO_UNUSED,
            .din  = I2S_SD_GPIO,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_rx, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(s_rx));

    BaseType_t ok = xTaskCreate(audio_in_task, "audio_in", 4096, NULL, 5, NULL);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "xTaskCreate(audio_in) failed");
        return ESP_ERR_NO_MEM;
    }

    s_initialized = true;
    ESP_LOGI(TAG, "I²S0 init OK (BCK=%d WS=%d SD=%d, %d Hz mono, rb=%d B)",
             I2S_BCK_GPIO, I2S_WS_GPIO, I2S_SD_GPIO, I2S_MIC_SAMPLE_RATE, MIC_RINGBUF_BYTES);
    return ESP_OK;
}

uint32_t i2s_mic_last_rms(void)  { return s_last_rms; }
uint32_t i2s_mic_last_peak(void) { return s_last_peak; }

void i2s_mic_drain(void)
{
    if (!s_rb) return;
    size_t stale_n;
    void *stale;
    while ((stale = xRingbufferReceive(s_rb, &stale_n, 0)) != NULL) {
        vRingbufferReturnItem(s_rb, stale);
    }
}

esp_err_t i2s_mic_capture(int16_t *out, size_t n_samples, uint32_t timeout_ms)
{
    if (!s_rb || !out) return ESP_ERR_INVALID_STATE;
    i2s_mic_drain();

    const size_t bytes_needed = n_samples * sizeof(int16_t);
    size_t bytes_got = 0;
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(timeout_ms);

    while (bytes_got < bytes_needed) {
        TickType_t now = xTaskGetTickCount();
        if ((int32_t)(deadline - now) <= 0) break;
        TickType_t wait = deadline - now;

        // ReceiveUpTo so leftover bytes stay in the ring for the next
        // iteration — the BYTEBUF version of xRingbufferReceive frees
        // everything it returns regardless of how much we used. See the
        // longer note in i2s_mic_capture_frame.
        size_t want = bytes_needed - bytes_got;
        size_t chunk_n = 0;
        void *chunk = xRingbufferReceiveUpTo(s_rb, &chunk_n, wait, want);
        if (!chunk) break;

        memcpy((uint8_t *)out + bytes_got, chunk, chunk_n);
        bytes_got += chunk_n;
        vRingbufferReturnItem(s_rb, chunk);
    }

    return (bytes_got == bytes_needed) ? ESP_OK : ESP_ERR_TIMEOUT;
}

esp_err_t i2s_mic_capture_frame(int16_t *out, size_t max_samples,
                                size_t *out_samples, uint32_t timeout_ms)
{
    if (!s_rb || !out || !out_samples) return ESP_ERR_INVALID_STATE;
    *out_samples = 0;

    // BYTEBUF semantics: xRingbufferReceive ignores any size argument and
    // hands back the entire contiguous block from read ptr to write ptr (or
    // wrap), and vRingbufferReturnItem then frees ALL of it regardless of
    // how much we used. With a slow consumer that's silent multi-frame data
    // loss. xRingbufferReceiveUpTo advances the read pointer by exactly the
    // bytes we asked for, so leftover frames stay in the ring for the next
    // call. Verified against esp-idf v5.3.5 components/esp_ringbuf/ringbuf.c.
    size_t chunk_n = 0;
    size_t bytes_max = max_samples * sizeof(int16_t);
    void *chunk = xRingbufferReceiveUpTo(s_rb, &chunk_n,
                                         pdMS_TO_TICKS(timeout_ms),
                                         bytes_max);
    if (!chunk) return ESP_ERR_TIMEOUT;

    s_capture_calls++;
    if (chunk_n > s_capture_max_chunk_n) s_capture_max_chunk_n = chunk_n;
    // Post-fix invariant: chunk_n <= bytes_max, so the truncation arm of the
    // pre-fix code can no longer fire. We keep the counter conditional so a
    // future regression (or contract change in IDF) would surface as
    // bytes_dropped > 0 in the diagnostic dump rather than silent loss.
    if (chunk_n > bytes_max) {
        s_capture_bytes_dropped += (uint32_t)(chunk_n - bytes_max);
        chunk_n = bytes_max;
    }
    memcpy(out, chunk, chunk_n);
    vRingbufferReturnItem(s_rb, chunk);

    *out_samples = chunk_n / sizeof(int16_t);
    return ESP_OK;
}
