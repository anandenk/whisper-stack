// Which input fired the most recent wake / round-trip start.
//
// Threaded through do_round_trip_streaming_with_preroll() so the OLED
// splash and serial logs can show what triggered the turn (M7/M8).
// WAKE_IR is reserved — the IR remote (M8) controls mute/snooze/cancel
// rather than waking, but the enum slot is allocated in case a future
// "wake on remote button" mapping is added.

#pragma once

typedef enum {
    WAKE_VOICE   = 0,   // server-side openWakeWord fired (/listen)
    WAKE_GESTURE = 1,   // HC-SR04 detected a wave (M7)
    WAKE_BUTTON  = 2,   // BOOT button manual fallback
    WAKE_IR      = 3,   // reserved — IR remote does not currently wake
} wake_source_t;

static inline const char *wake_source_label(wake_source_t s)
{
    switch (s) {
        case WAKE_VOICE:   return "VOICE";
        case WAKE_GESTURE: return "GESTURE";
        case WAKE_BUTTON:  return "BUTTON";
        case WAKE_IR:      return "IR";
        default:           return "?";
    }
}
