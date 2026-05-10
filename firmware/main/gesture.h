// HC-SR04 ultrasonic "wave to wake" — M7.
//
// While the device is IDLE, gesture_poll_task pulses the trigger pin
// every WAKE_GESTURE_POLL_INTERVAL_MS, times the echo, and looks for a
// down-up-down crossing of WAKE_GESTURE_THRESHOLD_CM within
// WAKE_GESTURE_WINDOW_MS. That sequence rejects static obstacles
// (a wall close to the sensor crosses below threshold once and stays
// there — won't fire) while accepting a wave (hand passes through the
// beam, leaves, passes again).
//
// On detection, the task signals the listen_stream loop to bail with
// LISTEN_RESULT_GESTURE so the round-trip can run with WAKE_GESTURE.
//
// Hardware:
//   * VCC  → 5V (VIN on the dev board, USB-powered)
//   * GND  → GND
//   * Trig → free GPIO (Kconfig WAKE_GESTURE_TRIG_GPIO)
//   * Echo → voltage divider (1kΩ + 2kΩ) → free GPIO
//            (Kconfig WAKE_GESTURE_ECHO_GPIO)
//
// The voltage divider is REQUIRED — the HC-SR04's echo line is 5V
// logic, the ESP32 inputs are 3.3V tolerant. 1kΩ on the high side +
// 2kΩ to GND drops 5V to ~3.33V at the ESP32 input.
//
// Defaults-OFF: gesture_init() is a no-op when WAKE_GESTURE_ENABLED
// is unset in Kconfig, so existing builds and any board without an
// HC-SR04 wired keep their pre-M7 behavior.

#pragma once

#include <stdbool.h>
#include "esp_err.h"

// Spawn the polling task. No-op if WAKE_GESTURE_ENABLED is not set.
// Idempotent.
esp_err_t gesture_init(void);

// True if the task has detected a wave that hasn't been consumed yet.
// listen_stream polls this each loop iteration; on true, returns
// LISTEN_RESULT_GESTURE so the caller can run the round-trip with
// WAKE_GESTURE. Consuming clears the flag.
bool gesture_check_and_clear(void);
