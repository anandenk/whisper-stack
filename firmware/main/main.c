// whisper-stack ESP-WROOM-32 firmware — main entry point.
//
// Each subsystem is gated on its ENABLE_X flag in app_state.h. The .c
// files always compile and link, but the peripheral init only runs when
// its flag is 1. Steps 5/6/7 incrementally flip the digits as the
// breadboard wiring lands.

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_chip_info.h"
#include "esp_idf_version.h"
#include "sdkconfig.h"

#include "app_state.h"
#include "oled.h"
#include "wifi.h"
#include "i2s_mic.h"

static const char *TAG = "whisper-stack";

void app_main(void)
{
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    ESP_LOGI(TAG, "boot: ESP32 rev %d, %d core%s, IDF %s",
             chip.revision, chip.cores, chip.cores > 1 ? "s" : "",
             esp_get_idf_version());
    ESP_LOGI(TAG, "subsystems: OLED=%d MIC=%d WIFI=%d",
             ENABLE_OLED, ENABLE_MIC, ENABLE_WIFI);

#if ENABLE_OLED
    if (oled_init() == ESP_OK) {
        oled_clear();
        oled_draw_text(0, "whisper-stack");
        oled_draw_text(2, "Hello!");
        oled_draw_text(7, "boot...");
        oled_flush();
    }
#endif

#if ENABLE_WIFI
    char ip[16] = "ERR";
    esp_err_t werr = wifi_init_sta_blocking(ip, sizeof ip);
    if (werr == ESP_OK) {
        ESP_LOGI(TAG, "wifi up; IP=%s", ip);
    } else {
        ESP_LOGE(TAG, "wifi failed: %s — staying offline", esp_err_to_name(werr));
    }
#endif

#if ENABLE_MIC
    if (i2s_mic_init() != ESP_OK) {
        ESP_LOGE(TAG, "mic init failed");
    }
#endif

#if ENABLE_OLED
    // Refresh display with whatever's known after subsystems came up.
    oled_clear();
    oled_draw_text(0, "whisper-stack");
    oled_draw_text(2, "ready");
#if ENABLE_WIFI
    char ip_line[OLED_CHARS_PER_LINE + 1];
    snprintf(ip_line, sizeof ip_line, "ip:%s", ip);
    oled_draw_text(4, ip_line);
#endif
    oled_flush();
#endif

    // M1 verification heartbeat. With ENABLE_MIC=1, also surfaces live
    // mic RMS/peak on the OLED (lines 5/6) so the snap-test is visible
    // without opening a serial monitor.
    int n = 0;
    while (1) {
#if ENABLE_MIC
        uint32_t rms  = i2s_mic_last_rms();
        uint32_t peak = i2s_mic_last_peak();
        ESP_LOGI(TAG, "tick %d  rms=%lu peak=%lu",
                 n++, (unsigned long)rms, (unsigned long)peak);
#  if ENABLE_OLED
        char rms_line[OLED_CHARS_PER_LINE + 1];
        char peak_line[OLED_CHARS_PER_LINE + 1];
        snprintf(rms_line,  sizeof rms_line,  "rms : %lu",  (unsigned long)rms);
        snprintf(peak_line, sizeof peak_line, "peak: %lu", (unsigned long)peak);
        oled_draw_text(5, rms_line);
        oled_draw_text(6, peak_line);
        oled_flush();
#  endif
#else
        ESP_LOGI(TAG, "tick %d (idle)", n++);
#endif
        vTaskDelay(pdMS_TO_TICKS(500));   // 2 Hz refresh — fast enough to see snap
    }
}
