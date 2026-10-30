// WiFi STA — minimal blocking-connect bring-up for M1.
//
// HISTORICAL / UNSUPPORTED (May 2026 Prototype):
// Server certificate validation is deliberately omitted in WPA2-Enterprise
// mode, exposing credentials to rogue access points. Intended for personal
// bench testing on trusted networks only.
//
// Why blocking: the device's whole purpose is to talk to the server; if
// there's no network there's nothing useful to do. Failure here is
// terminal — log loudly and let the watchdog reset us, the next boot
// retries.
//
// Two auth modes, picked at build time via Kconfig:
//   - WS_WIFI_AUTH_PSK              — home WiFi / phone hotspot
//   - WS_WIFI_AUTH_WPA2_ENTERPRISE  — 802.1X / PEAP-MSCHAPv2
//                                     (eduroam, university dorm WiFi)
// EAP-TLS (client cert) is not supported. Server cert validation is
// deliberately skipped in v1; document & revisit if real-world MITM
// risk on the dorm network warrants it.

#include "wifi.h"
#include "app_state.h"

#include <string.h>
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_wifi.h"
#if CONFIG_WS_WIFI_AUTH_WPA2_ENTERPRISE
#  include "esp_eap_client.h"
#endif
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "lwip/ip4_addr.h"

static const char *TAG = "wifi";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1
#define WIFI_MAX_RETRY     8
// 90 s outer timeout. The retry handler is the real safety net — it
// gives up after WIFI_MAX_RETRY disconnects (≈ 10–20 s of trying) and
// sets WIFI_FAIL_BIT, which unblocks us. The timeout only matters when
// assoc succeeds but DHCP stalls (no DISCONNECTED to drive retries).
// Network access controller / NAC takes ~30 s to reclassify a freshly-registered device,
// during which DHCP can stall on the first attempt — 30 s wasn't enough.
#define WIFI_TIMEOUT_TICKS pdMS_TO_TICKS(90000)

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
    strncpy((char *)wifi_cfg.sta.ssid, CONFIG_WS_WIFI_SSID, sizeof(wifi_cfg.sta.ssid) - 1);
    wifi_cfg.sta.pmf_cfg.capable    = true;
    wifi_cfg.sta.pmf_cfg.required   = false;

#if CONFIG_WS_WIFI_AUTH_WPA2_ENTERPRISE
    // 802.1X / PEAP-MSCHAPv2. Don't set sta.password — credentials go
    // through esp_eap_client_*. Threshold gates roaming to enterprise APs.
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_ENTERPRISE;
#else
    strncpy((char *)wifi_cfg.sta.password, CONFIG_WS_WIFI_PASS, sizeof(wifi_cfg.sta.password) - 1);
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
#endif

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));

#if CONFIG_WS_WIFI_AUTH_WPA2_ENTERPRISE
    {
        const char *outer = CONFIG_WS_WIFI_EAP_IDENTITY;
        const char *user  = CONFIG_WS_WIFI_EAP_USERNAME;
        const char *pass  = CONFIG_WS_WIFI_EAP_PASSWORD;

        // Outer (anonymous) identity is optional. If empty, the supplicant
        // sends the inner username as the outer identity automatically.
        if (outer[0] != '\0') {
            ESP_ERROR_CHECK(esp_eap_client_set_identity((const uint8_t *)outer, strlen(outer)));
        }
        ESP_ERROR_CHECK(esp_eap_client_set_username((const uint8_t *)user, strlen(user)));
        ESP_ERROR_CHECK(esp_eap_client_set_password((const uint8_t *)pass, strlen(pass)));

        // No CA cert validation in v1. The supplicant accepts whatever the
        // RADIUS server presents; password is still encrypted inside PEAP,
        // but a rogue AP on the same SSID could MITM. Acceptable for a
        // single-network dorm device; revisit if threat model changes.
        ESP_LOGW(TAG, "EAP: server cert validation disabled (v1 trade-off)");

        ESP_ERROR_CHECK(esp_wifi_sta_enterprise_enable());
        ESP_LOGI(TAG, "connecting to SSID=\"%s\" via WPA2-Enterprise (user=\"%s\")",
                 CONFIG_WS_WIFI_SSID, user);
    }
#else
    ESP_LOGI(TAG, "connecting to SSID=\"%s\" via WPA2-PSK", CONFIG_WS_WIFI_SSID);
#endif

    ESP_ERROR_CHECK(esp_wifi_start());

    // Disable WiFi power-save. Default WIFI_PS_MIN_MODEM sleeps the radio
    // between AP beacons and adds 5–15 ms wake-up latency per TX burst.
    // On marginal/contended links that compounds with retransmissions and
    // starves the 12.5 fps mic frame cadence, causing ring overflow and
    // recycled frames (see CLAUDE.md gotcha #9). NONE keeps the radio
    // always on. Costs a few extra mA — strict win on a mains/battery
    // device that needs reliable streaming.
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_LOGI(TAG, "wifi power-save: NONE (always-on radio)");

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
