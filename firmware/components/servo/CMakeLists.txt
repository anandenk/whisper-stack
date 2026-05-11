idf_component_register(
    SRCS         "servo.c"
    INCLUDE_DIRS "."
    REQUIRES
        esp_driver_ledc
        esp_timer
)
