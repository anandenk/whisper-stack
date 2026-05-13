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
        "vad.c"
        # v2-M2: on-device wake word via esp-sr (WakeNet + AFE).
        # Replaced listen_stream.c (server-side /listen client) in v2-M2-c.
        # The /listen server route + wakeword.py stay in tree for the
        # web client + tests/jarvis.py. The firmware path is now
        # entirely on-device.
        "wake.c"
        # M7 — HC-SR04 ultrasonic gesture wake (compiled-in always;
        # runtime-gated on CONFIG_WAKE_GESTURE_ENABLED inside gesture.c).
        "gesture.c"
        # M8 — IR remote (NEC decoder via RMT). Same pattern as gesture.c —
        # compiled-in always, runtime-gated on CONFIG_IR_ENABLED.
        "ir_remote.c"
        # Cross-task mute/snooze/cancel state shared by M8, listen_stream,
        # main.c's cancel hook, and the SPEAKING countdown.
        "control_state.c"
    INCLUDE_DIRS "."
    REQUIRES
        # FACE renderer — RoboEyes port (gated by CONFIG_FACE_RENDERER).
        # Lives at ../components/robo_eyes/. Required header is robo_eyes.h.
        # When CONFIG_FACE_RENDERER_CAT_FACE is selected this component
        # still links but its symbols aren't referenced from main.c, so
        # --gc-sections drops them.
        robo_eyes
        # SG90 servo driver — only referenced when CONFIG_GAZE_COUPLING_DEMO
        # is set; otherwise --gc-sections strips it.
        servo
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
        # M3: VAD timing (esp_timer_get_time)
        esp_timer
        # M8: NEC IR decoding via the RMT peripheral
        esp_driver_rmt
        # v2-M2: on-device wake-word via esp-sr (managed component).
        # Provides model_path.h + esp_wn_models.h + the AFE/WakeNet APIs.
        espressif__esp-sr
)
