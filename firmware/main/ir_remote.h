// IR remote — NEC-protocol decoder via ESP-IDF's RMT peripheral (M8).
//
// Receives 32-bit NEC frames on CONFIG_IR_GPIO_PIN, compares against
// the user-configured mute/snooze/cancel codes (Kconfig
// IR_MUTE_BUTTON_CODE / IR_SNOOZE_BUTTON_CODE / IR_CANCEL_BUTTON_CODE),
// and dispatches into control_state. Unrecognized codes are logged with
// their hex value so first-time setup can identify which remote button
// is which: flash with all three Kconfig codes at 0, point remote at
// receiver, press buttons, read "IR rx: 0x..." lines from the serial
// monitor, set them in menuconfig, reflash.
//
// Hardware:
//   * VCC    → 3.3V (most kit IR receivers are 3-5V tolerant; pick 3V3
//              to avoid level-shifting the signal output).
//   * GND    → GND
//   * Signal → free GPIO (Kconfig IR_GPIO_PIN). The IR receiver
//              demodulates the 38 kHz carrier internally and outputs
//              an active-low envelope — directly readable by the ESP32.
//
// Defaults-OFF: ir_remote_init() is a no-op when IR_ENABLED is unset
// in Kconfig, so existing builds and any board without an IR receiver
// wired keep their pre-M8 behavior.

#pragma once

#include "esp_err.h"

// Spawn the RMT-RX channel + decode task. No-op if IR_ENABLED is
// not set. Idempotent.
esp_err_t ir_remote_init(void);
