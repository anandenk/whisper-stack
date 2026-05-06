# Component-level CMakeLists for main/.
#
# REQUIRES lists components whose headers are #included from main's own
# headers (public deps). PRIV_REQUIRES is for components only #included
# from .c files. For a single-component project the distinction barely
# matters; using REQUIRES keeps CMake auto-discovery happy.
idf_component_register(
    SRCS
        "main.c"
        "oled.c"
        "wifi.c"
        "i2s_mic.c"
        "http_client.c"
    INCLUDE_DIRS "."
    REQUIRES
        # OLED stack
        esp_lcd
        esp_driver_i2c
        # Mic stack
        esp_driver_i2s
        # WiFi stack
        esp_wifi
        esp_event
        esp_netif
        nvs_flash
        lwip
        # WPA2-Enterprise / 802.1X (eduroam, dorm WiFi). Provides
        # esp_eap_client.h. Pulled regardless of auth mode — the
        # enterprise code path is compiled out via Kconfig when PSK
        # is selected, but the header still needs to resolve.
        wpa_supplicant
        # M2: button-driven /converse round-trip
        esp_driver_gpio
        esp_http_client
        json
)
