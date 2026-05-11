// SPDX-License-Identifier: GPL-3.0-or-later
//
// robo_eyes — ESP-IDF C port of FluxGarage RoboEyes V1.1.1.
//
// Upstream:  https://github.com/FluxGarage/RoboEyes
// Original C++ class:  src/FluxGarage_RoboEyes.h
// Original author:     Dennis Hoelscher (www.fluxgarage.com)
// Original copyright:  © 2024-2025 Dennis Hoelscher
//
// This port translates the upstream Arduino library (which uses
// Adafruit_GFX as its drawing surface) to plain C against an
// in-memory 128×64 SSD1306-page framebuffer. The geometry, tween
// math, animation timings, and mood overlay shapes are preserved
// verbatim from the upstream so the rendered output matches
// "stock RoboEyes" pixel-for-pixel modulo screen size.
//
// The only deliberate semantic change: `robo_eyes_set_position`
// is the upstream's 9-cell discrete API; `robo_eyes_set_position_continuous`
// is a new extension that accepts a continuous angle and feeds the
// existing tween machinery without rounding to a corner. The S3
// servo coupling will call the continuous variant.
//
// License: GPL-3.0-or-later (inherited from upstream — see /LICENSE).

#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// --- Mood -----------------------------------------------------------------
// Numeric values match the upstream's preprocessor #defines so log lines
// and any external callers that hardcode the integer are compatible.
typedef enum {
    ROBO_EYES_MOOD_DEFAULT = 0,
    ROBO_EYES_MOOD_TIRED   = 1,
    ROBO_EYES_MOOD_ANGRY   = 2,
    ROBO_EYES_MOOD_HAPPY   = 3,
} robo_eyes_mood_t;

// --- Position (discrete, upstream API) ------------------------------------
// 0 = center; 1..8 walk the compass clockwise from N. Numeric values match
// the upstream's #defines exactly.
typedef enum {
    ROBO_EYES_POS_DEFAULT = 0,
    ROBO_EYES_POS_N       = 1,
    ROBO_EYES_POS_NE      = 2,
    ROBO_EYES_POS_E       = 3,
    ROBO_EYES_POS_SE      = 4,
    ROBO_EYES_POS_S       = 5,
    ROBO_EYES_POS_SW      = 6,
    ROBO_EYES_POS_W       = 7,
    ROBO_EYES_POS_NW      = 8,
} robo_eyes_position_t;

// --- Framebuffer dimensions ----------------------------------------------
#define ROBO_EYES_DEFAULT_W   128
#define ROBO_EYES_DEFAULT_H   64
#define ROBO_EYES_FB_BYTES    ((ROBO_EYES_DEFAULT_W * ROBO_EYES_DEFAULT_H) / 8)

// --- Lifecycle -----------------------------------------------------------

// Initialize state with defaults matching the upstream:
//   eye 36×36, border radius 8, gap 10, autoblinker off, idle mode off,
//   mood DEFAULT, position DEFAULT, eyes start CLOSED (height 1) and
//   open on the first `update` call.
//
// `screen_w` and `screen_h` must match the actual OLED dimensions
// because position offsets are computed from them. `framerate` caps
// the redraw rate (upstream default: 50).
void robo_eyes_begin(int screen_w, int screen_h, uint8_t framerate);

// Tick the animation. Pass current wall-clock milliseconds (from
// `esp_timer_get_time() / 1000` or any monotonic source). The
// upstream's frame-rate gate is preserved — only redraws when at
// least `frame_interval` ms have elapsed.
//
// On a frame tick the function clears the internal framebuffer,
// tweens current → next geometry by one step, applies the mood
// overlays, and leaves the framebuffer ready to flush. Caller flushes
// via `robo_eyes_get_framebuffer()` + their own OLED driver.
//
// Returns true iff a redraw happened this call (i.e. the caller
// should flush). Returns false when the frame-rate gate skipped it.
bool robo_eyes_update(uint32_t now_ms);

// Pointer to the internal 1024-byte framebuffer in SSD1306 horizontal-
// page byte order (8 pages × screen_w bytes; bit 0 of each byte = top
// pixel of that page). Lifetime is the program; copy or flush before
// the next `update` call mutates the buffer.
const uint8_t *robo_eyes_get_framebuffer(void);

// --- Setters (upstream surface) ------------------------------------------

void robo_eyes_set_framerate(uint8_t fps);
void robo_eyes_set_width(uint8_t left, uint8_t right);
void robo_eyes_set_height(uint8_t left, uint8_t right);
void robo_eyes_set_borderradius(uint8_t left, uint8_t right);
void robo_eyes_set_spacebetween(int space);

void robo_eyes_set_mood(robo_eyes_mood_t mood);
void robo_eyes_set_position(robo_eyes_position_t pos);

void robo_eyes_set_autoblinker(bool active, int interval_s, int variation_s);
void robo_eyes_set_idle_mode(bool active, int interval_s, int variation_s);

void robo_eyes_set_curiosity(bool on);
void robo_eyes_set_cyclops(bool on);
void robo_eyes_set_hflicker(bool on, uint8_t amplitude);
void robo_eyes_set_vflicker(bool on, uint8_t amplitude);

// --- Direct eye control (upstream "BASIC ANIMATION METHODS") --------------

void robo_eyes_close(void);                          // close both
void robo_eyes_open(void);                           // open both
void robo_eyes_blink(void);                          // close + open
void robo_eyes_close_lr(bool left, bool right);
void robo_eyes_open_lr(bool left, bool right);
void robo_eyes_blink_lr(bool left, bool right);

// --- One-shot macro animations (upstream) --------------------------------

void robo_eyes_anim_confused(void);
void robo_eyes_anim_laugh(void);

// --- Continuous-gaze extension (NOT upstream) ----------------------------
//
// Accepts a continuous angle in [-90, +90] degrees plus optional
// vertical fraction in [-1, +1]. Maps the angle linearly across the
// screen-X envelope (the same envelope the upstream's `setPosition`
// snaps to corners of). Returns smoothly because the upstream's
// per-frame `(cur+next)/2` tween already does sub-pixel ease-toward-
// target — we just feed it a continuous target instead of a corner.
//
// Use this from the S3 gaze coupling: when the servo is commanded to
// angle θ, call this with θ ~150 ms BEFORE issuing the servo move so
// the eyes lead the head (see notes/face-renderer.md).
void robo_eyes_set_position_continuous(float angle_deg, float vertical_frac);

#ifdef __cplusplus
}
#endif
