// IR remote NEC decoder + dispatch — see header.

#include "ir_remote.h"
#include "control_state.h"

#include <string.h>
#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"

static const char *TAG = "ir";

#if CONFIG_IR_ENABLED

#include "driver/rmt_rx.h"

// 1 MHz clock → 1 tick = 1 µs. NEC frames span up to ~110 ms, well
// within the symbol-width limit at this resolution.
#define IR_RMT_RESOLUTION_HZ   1000000
// 64 symbols (256 bytes) is comfortably more than a NEC frame (~34) +
// repeat (~3). esp32 RMT block is 64 symbols/channel; fits in one block.
#define IR_RMT_SYMBOLS         64

// NEC timing windows. Tolerance ~25% to ride out cheap-remote jitter
// without merging adjacent timing buckets.
#define NEC_LEADER_LOW_US      9000
#define NEC_LEADER_HIGH_US     4500
#define NEC_REPEAT_HIGH_US     2250
#define NEC_BIT_LOW_US         562
#define NEC_BIT_ZERO_HIGH_US   562
#define NEC_BIT_ONE_HIGH_US    1687
#define NEC_BIT_THRESHOLD_US   1100   // > this → 1, ≤ this → 0

static inline bool within(uint16_t v, uint16_t target, uint16_t tol)
{
    return (v + tol >= target) && (v <= target + tol);
}

typedef struct {
    rmt_symbol_word_t items[IR_RMT_SYMBOLS];
    size_t            num;
} ir_frame_t;

static rmt_channel_handle_t s_rx_chan = NULL;
static QueueHandle_t        s_rx_queue;
static rmt_symbol_word_t    s_rx_buf[IR_RMT_SYMBOLS];

static const rmt_receive_config_t s_rx_cfg = {
    .signal_range_min_ns =    1250,    // ignore < 1.25 µs glitches
    .signal_range_max_ns = 12000000,   // > 12 ms idle = end of frame
};

static bool IRAM_ATTR on_recv_done(rmt_channel_handle_t chan,
                                   const rmt_rx_done_event_data_t *edata,
                                   void *user_ctx)
{
    BaseType_t high_woken = pdFALSE;
    ir_frame_t f;
    f.num = edata->num_symbols;
    if (f.num > IR_RMT_SYMBOLS) f.num = IR_RMT_SYMBOLS;
    memcpy(f.items, edata->received_symbols, f.num * sizeof(rmt_symbol_word_t));
    xQueueSendFromISR(s_rx_queue, &f, &high_woken);
    return high_woken == pdTRUE;
}

// True if `n` symbols look like a NEC repeat (leader-LOW + 2250 µs gap +
// stop bit). Repeats fire while a remote button is held — we ignore them
// so a held button doesn't toggle mute repeatedly.
static bool looks_like_repeat(const rmt_symbol_word_t *s, size_t n)
{
    return n >= 1
        && within(s[0].duration0, NEC_LEADER_LOW_US,  2000)
        && within(s[0].duration1, NEC_REPEAT_HIGH_US, 800);
}

// Decode a NEC frame (1 leader + 32 bit symbols + stop). Returns true
// and writes the 32-bit code on success.
static bool decode_nec(const rmt_symbol_word_t *s, size_t n, uint32_t *out)
{
    if (n < 33) return false;
    if (!within(s[0].duration0, NEC_LEADER_LOW_US,  2000)) return false;
    if (!within(s[0].duration1, NEC_LEADER_HIGH_US, 1000)) return false;

    uint32_t code = 0;
    for (int i = 0; i < 32; ++i) {
        const rmt_symbol_word_t *b = &s[1 + i];
        if (!within(b->duration0, NEC_BIT_LOW_US, 250)) return false;
        bool bit = b->duration1 > NEC_BIT_THRESHOLD_US;
        code = (code << 1) | (bit ? 1u : 0u);
    }
    *out = code;
    return true;
}

static void dispatch(uint32_t code)
{
    // Pull config every dispatch — Kconfig values are immutable at
    // build time so this is just a load, but it keeps the lookup local
    // and avoids stale-cache surprises if anyone later wires NVS-backed
    // remappings.
    const uint32_t mute_code   = (uint32_t)CONFIG_IR_MUTE_BUTTON_CODE;
    const uint32_t snooze_code = (uint32_t)CONFIG_IR_SNOOZE_BUTTON_CODE;
    const uint32_t cancel_code = (uint32_t)CONFIG_IR_CANCEL_BUTTON_CODE;

    if (mute_code != 0 && code == mute_code) {
        control_toggle_mute();
        control_post_splash(control_is_muted_now() ? "*** MUTED ***" : "*** ACTIVE ***");
    } else if (snooze_code != 0 && code == snooze_code) {
        control_start_snooze(CONFIG_IR_SNOOZE_DURATION_MIN);
        char buf[32];
        snprintf(buf, sizeof buf, "SNOOZE %dm", CONFIG_IR_SNOOZE_DURATION_MIN);
        control_post_splash(buf);
    } else if (cancel_code != 0 && code == cancel_code) {
        control_request_cancel();
        // No splash — cancel's feedback is the turn ending. Posting
        // here would also display during IDLE presses that the cancel
        // handler ignores, which would mislead.
    } else {
        ESP_LOGI(TAG, "rx: 0x%08lX  (unmapped — set in menuconfig if this is mute/snooze/cancel)",
                 (unsigned long)code);
    }
}

static void ir_decode_task(void *arg)
{
    (void)arg;
    int64_t last_dispatch_us = 0;
    uint32_t last_code = 0;

    // Arm the first reception. The RMT driver re-arms after each
    // callback delivers; we re-arm explicitly in the loop below.
    rmt_receive(s_rx_chan, s_rx_buf, sizeof(s_rx_buf), &s_rx_cfg);

    while (1) {
        ir_frame_t f;
        if (xQueueReceive(s_rx_queue, &f, portMAX_DELAY) != pdTRUE) continue;

        // Re-arm RX immediately so we don't miss the next frame while
        // we decode + dispatch.
        rmt_receive(s_rx_chan, s_rx_buf, sizeof(s_rx_buf), &s_rx_cfg);

        if (looks_like_repeat(f.items, f.num)) {
            // Ignore — a held button shouldn't toggle mute repeatedly.
            continue;
        }

        uint32_t code = 0;
        if (!decode_nec(f.items, f.num, &code)) {
            ESP_LOGD(TAG, "non-NEC or malformed frame (n=%u)", (unsigned)f.num);
            continue;
        }

        // Soft debounce: identical codes within 250 ms collapse to one.
        // Some remotes emit a non-repeat full frame back-to-back on a
        // single press; the task spec wants single-shot dispatch.
        int64_t now_us = esp_timer_get_time();
        if (code == last_code && (now_us - last_dispatch_us) < 250000) {
            continue;
        }
        last_code = code;
        last_dispatch_us = now_us;

        dispatch(code);
    }
}

esp_err_t ir_remote_init(void)
{
    if (s_rx_chan) return ESP_OK;

    s_rx_queue = xQueueCreate(8, sizeof(ir_frame_t));
    if (!s_rx_queue) return ESP_ERR_NO_MEM;

    rmt_rx_channel_config_t rx_cfg = {
        .clk_src           = RMT_CLK_SRC_DEFAULT,
        .resolution_hz     = IR_RMT_RESOLUTION_HZ,
        .mem_block_symbols = IR_RMT_SYMBOLS,
        .gpio_num          = CONFIG_IR_GPIO_PIN,
    };
    esp_err_t err = rmt_new_rx_channel(&rx_cfg, &s_rx_chan);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "rmt_new_rx_channel: %s", esp_err_to_name(err));
        return err;
    }

    rmt_rx_event_callbacks_t cbs = { .on_recv_done = on_recv_done };
    err = rmt_rx_register_event_callbacks(s_rx_chan, &cbs, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "register callbacks: %s", esp_err_to_name(err));
        return err;
    }

    err = rmt_enable(s_rx_chan);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "rmt_enable: %s", esp_err_to_name(err));
        return err;
    }

    BaseType_t ok = xTaskCreate(ir_decode_task, "ir_rx", 4096, NULL,
                                tskIDLE_PRIORITY + 2, NULL);
    if (ok != pdPASS) return ESP_ERR_NO_MEM;

    ESP_LOGI(TAG, "ready: gpio=%d  mute=0x%08lX  snooze=0x%08lX  cancel=0x%08lX",
             CONFIG_IR_GPIO_PIN,
             (unsigned long)(uint32_t)CONFIG_IR_MUTE_BUTTON_CODE,
             (unsigned long)(uint32_t)CONFIG_IR_SNOOZE_BUTTON_CODE,
             (unsigned long)(uint32_t)CONFIG_IR_CANCEL_BUTTON_CODE);
    if (CONFIG_IR_MUTE_BUTTON_CODE   == 0 ||
        CONFIG_IR_SNOOZE_BUTTON_CODE == 0 ||
        CONFIG_IR_CANCEL_BUTTON_CODE == 0) {
        ESP_LOGW(TAG, "one or more codes unset — point remote at receiver, "
                       "press buttons, read 'rx: 0x...' lines, set in menuconfig");
    }
    return ESP_OK;
}

#else  // CONFIG_IR_ENABLED

esp_err_t ir_remote_init(void)
{
    ESP_LOGI(TAG, "disabled (CONFIG_IR_ENABLED unset)");
    return ESP_OK;
}

#endif
