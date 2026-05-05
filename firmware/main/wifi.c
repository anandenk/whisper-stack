// WiFi STA — minimal blocking-connect bring-up for M1.
//
// Why blocking: the device's whole purpose is to talk to the server; if
// there's no network there's nothing useful to do. Failure here is
// terminal — log loudly and let the watchdog reset us, the next boot
// retries.

#include "wifi.h"
#include "app_state.h"

#include <string.h>
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "lwip/ip4_addr.h"

static const char *TAG = "wifi";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1
#define WIFI_MAX_RETRY     8
#define WIFI_TIMEOUT_TICKS pdMS_TO_TICKS(30000)

static EventGroupHandle_t s_evt = NULL;
static int s_retry = 0;
static esp_ip4_addr_t s_ip;

static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_retry < WIFI_MAX_RETRY) {
            esp_wifi_connect();
            s_retry++;
            ESP_LOGW(TAG, "disconnected; retry %d/%d", s_retry, WIFI_MAX_RETRY);
        } else {
            xEventGroupSetBits(s_evt, WIFI_FAIL_BIT);
            ESP_LOGE(TAG, "out of retries — giving up");
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = (ip_event_got_ip_t *)data;
        s_ip = e->ip_info.ip;
        ESP_LOGI(TAG, "got IP: " IPSTR, IP2STR(&s_ip));
        s_retry = 0;
        xEventGroupSetBits(s_evt, WIFI_CONNECTED_BIT);
    }
}

esp_err_t wifi_init_sta_blocking(char *out_ip, size_t out_ip_len)
{
    if (out_ip == NULL || out_ip_len < 16) return ESP_ERR_INVALID_ARG;
    out_ip[0] = '\0';

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    s_evt = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT,   IP_EVENT_STA_GOT_IP, on_wifi_event, NULL, NULL));

    wifi_config_t wifi_cfg = { 0 };
    strncpy((char *)wifi_cfg.sta.ssid,     CONFIG_WS_WIFI_SSID, sizeof(wifi_cfg.sta.ssid) - 1);
    strncpy((char *)wifi_cfg.sta.password, CONFIG_WS_WIFI_PASS, sizeof(wifi_cfg.sta.password) - 1);
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_cfg.sta.pmf_cfg.capable    = true;
    wifi_cfg.sta.pmf_cfg.required   = false;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "connecting to SSID=\"%s\" ...", CONFIG_WS_WIFI_SSID);

    EventBits_t bits = xEventGroupWaitBits(s_evt,
                                           WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                           pdFALSE, pdFALSE, WIFI_TIMEOUT_TICKS);
    if (bits & WIFI_CONNECTED_BIT) {
        snprintf(out_ip, out_ip_len, IPSTR, IP2STR(&s_ip));
        return ESP_OK;
    }
    if (bits & WIFI_FAIL_BIT) return ESP_FAIL;
    return ESP_ERR_TIMEOUT;
}
