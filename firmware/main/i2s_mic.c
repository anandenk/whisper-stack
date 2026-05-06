// INMP441 → I²S0 → 16 kHz mono int16 ring buffer.
//
// Pin map (from notes/esp32-firmware-plan.md):
//   I²S0 BCK (SCK) = GPIO 26
//   I²S0 WS  (LRCK)= GPIO 25
//   I²S0 SD  (DOUT)= GPIO 33
//   INMP441 L/R    = tied to GND (mono left-channel slot)
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
#include "driver/i2s_std.h"

static const char *TAG = "i2s_mic";

#define I2S_BCK_GPIO 26
#define I2S_WS_GPIO  25
#define I2S_SD_GPIO  33

// 16 KB byte buffer = 8192 int16 samples = ~512 ms at 16 kHz. Plenty of
// slack between producer and a real-time consumer; small enough that
// idle memory cost is negligible.
#define MIC_RINGBUF_BYTES (16 * 1024)

static i2s_chan_handle_t s_rx = NULL;
static volatile uint32_t s_last_rms = 0;
static volatile uint32_t s_last_peak = 0;
static RingbufHandle_t   s_rb = NULL;
static bool s_initialized = false;

// Convert one 32-bit I²S slot containing a 24-bit MSB-aligned signed sample
// into int16 by dropping the bottom 16 bits.
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
            // Non-blocking — drop the frame if no consumer is draining.
            (void)xRingbufferSend(s_rb, pcm, n * sizeof(int16_t), 0);
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

        size_t chunk_n = 0;
        void *chunk = xRingbufferReceive(s_rb, &chunk_n, wait);
        if (!chunk) break;

        size_t copy = bytes_needed - bytes_got;
        if (copy > chunk_n) copy = chunk_n;
        memcpy((uint8_t *)out + bytes_got, chunk, copy);
        bytes_got += copy;
        vRingbufferReturnItem(s_rb, chunk);
    }

    return (bytes_got == bytes_needed) ? ESP_OK : ESP_ERR_TIMEOUT;
}

esp_err_t i2s_mic_capture_frame(int16_t *out, size_t max_samples,
                                size_t *out_samples, uint32_t timeout_ms)
{
    if (!s_rb || !out || !out_samples) return ESP_ERR_INVALID_STATE;
    *out_samples = 0;

    size_t chunk_n = 0;
    void *chunk = xRingbufferReceive(s_rb, &chunk_n, pdMS_TO_TICKS(timeout_ms));
    if (!chunk) return ESP_ERR_TIMEOUT;

    size_t bytes_max = max_samples * sizeof(int16_t);
    size_t copy = chunk_n < bytes_max ? chunk_n : bytes_max;
    memcpy(out, chunk, copy);
    vRingbufferReturnItem(s_rb, chunk);

    *out_samples = copy / sizeof(int16_t);
    return ESP_OK;
}
