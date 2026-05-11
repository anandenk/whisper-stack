idf_component_register(
    SRCS         "robo_eyes.c"
    INCLUDE_DIRS "."
    # esp_random.h lives in esp_system / esp_hw_support depending on IDF
    # version; both are pulled in transitively by the base IDF build, so
    # no explicit REQUIRES needed beyond what we use directly.
)
