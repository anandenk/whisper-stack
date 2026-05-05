// WiFi STA — connects to the SSID/PASS configured via Kconfig.projbuild
// (CONFIG_WS_WIFI_SSID / CONFIG_WS_WIFI_PASS) and blocks until associated.

#pragma once

#include <stdint.h>
#include "esp_err.h"

// Initialize NVS, the netif/event loop, and start WiFi STA.
// Blocks (with a timeout) until associated and an IPv4 lease is granted.
// Returns ESP_OK on success and writes the dotted-quad address into
// `out_ip` (must be a buffer of at least 16 bytes).
esp_err_t wifi_init_sta_blocking(char *out_ip, size_t out_ip_len);
