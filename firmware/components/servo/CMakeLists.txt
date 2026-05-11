idf_component_register(
    SRCS         "servo.c"
    INCLUDE_DIRS "."
    REQUIRES
        esp_driver_ledc
        # esp_driver_ledc's public header includes driver/gpio.h, so any
        # consumer of ledc.h needs esp_driver_gpio in REQUIRES (not just
        # PRIV_REQUIRES) — IDF flagged this explicitly at first build.
        esp_driver_gpio
        esp_timer
)
