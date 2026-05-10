// Cross-task control state for the IR remote (M8): mute, snooze, and a
// one-shot mid-turn cancel flag.
//
// Centralizes what would otherwise be a tangle of globals across
// listen_stream (idles wake-streaming when muted), gesture (idles
// HC-SR04 polling when muted), main.c's cb_cancel (sets BUTTON_CANCEL
// when cancel fires during LISTENING), the SPEAKING countdown loop
// (breaks early on cancel), and the OLED status redraws (shows MUTED /
// snooze countdown).
//
// Mute does NOT persist across reboots — boot always comes up unmuted.

#pragma once

#include <stdbool.h>
#include "esp_err.h"

typedef enum {
    CONTROL_MUTE_NONE        = 0,   // wake sources active
    CONTROL_MUTE_INDEFINITE  = 1,   // muted until user toggles back
    CONTROL_MUTE_SNOOZE      = 2,   // muted until snooze deadline
} control_mute_state_t;

// One-time init. Idempotent.
esp_err_t control_state_init(void);

// True iff wake sources should currently be paused. Combines indefinite
// mute and snooze (auto-clearing snooze on expiry as a side effect).
bool control_is_muted_now(void);

// Current mute mode. Auto-clears expired snooze before returning.
control_mute_state_t control_get_mute_state(void);

// Whole minutes left on an active snooze, rounded up so a snooze of
// 29.1 min still shows "30m left". Returns 0 if not snoozed (or expired).
int control_snooze_minutes_left(void);

// IR mute-button handler: NORMAL → INDEFINITE, INDEFINITE → NORMAL,
// SNOOZE → INDEFINITE (cancels snooze).
void control_toggle_mute(void);

// IR snooze-button handler: any state → SNOOZE with a fresh duration.
// `duration_min` is the snooze length in minutes (Kconfig
// IR_SNOOZE_DURATION_MIN).
void control_start_snooze(int duration_min);

// IR cancel-button handler: set the one-shot cancel flag. Consumed by
// the first call to control_check_and_clear_cancel().
void control_request_cancel(void);

// Check-and-clear the cancel flag. Returns true exactly once per
// request_cancel() call. cb_cancel (LISTENING) checks each frame; the
// SPEAKING countdown loop checks each second; the THINKING-finish
// branch checks once after the response parses.
bool control_check_and_clear_cancel(void);

// Force-clear the cancel flag without consuming. Belt-and-suspenders
// against a stale flag — `do_one_turn` calls this on entry so a press
// that happened during the (state=IDLE) gap between turns can't auto-
// bail the new turn the moment it starts.
void control_clear_cancel(void);

// Post a transient OLED splash (mute toggle, snooze, etc). Stored in
// an internal buffer; rendered by listen_stream's IDLE loop for the
// next ~800 ms. mid-turn splashes queue and render once the device
// returns to IDLE — the splash exists for confirmation, not as critical
// state, so a delayed render is acceptable.
void control_post_splash(const char *msg);

// If a splash is active (deadline not yet passed), copies the message
// into `out` and returns true. Caller is expected to render the
// message and wait briefly before checking again. Splash auto-clears
// after its deadline; no consume on read.
bool control_get_active_splash(char *out, size_t out_size);
