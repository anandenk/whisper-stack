// Shared app state for the whisper-stack ESP-32 firmware.
//
// The state machine matches the plan's IDLE → LISTENING → THINKING →
// SPEAKING cycle. M1 only uses APP_STATE_IDLE; later milestones add the
// transitions.

#pragma once

#include <stdint.h>

typedef enum {
    APP_STATE_BOOT       = 0,  // pre-init, before tasks start
    APP_STATE_IDLE       = 1,  // ready / listening for wake (M4+)
    APP_STATE_LISTENING  = 2,  // VAD-bounded recording (M3+)
    APP_STATE_THINKING   = 3,  // /converse round-trip in progress (M2+)
    APP_STATE_SPEAKING   = 4,  // workstation playing reply (M2+)
} app_state_t;

// --- Subsystem enable toggles ---------------------------------------------
// One digit per subsystem. Step 4 builds with everything off; Step 5/6/7
// flip these on incrementally as wiring lands. Each subsystem's init call
// in main.c is guarded by the matching #if.
#define ENABLE_OLED 1   // Step 5 wires OLED + flashes this build
#define ENABLE_MIC  1   // Step 6 enables I²S mic capture
#define ENABLE_WIFI 1   // Step 7 enables WiFi STA + connects
