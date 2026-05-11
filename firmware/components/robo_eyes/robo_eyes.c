// SPDX-License-Identifier: GPL-3.0-or-later
//
// robo_eyes — ESP-IDF C port of FluxGarage RoboEyes V1.1.1.
//
// Upstream:  https://github.com/FluxGarage/RoboEyes
// File:      src/FluxGarage_RoboEyes.h  (V1.1.1)
// Author:    Dennis Hoelscher  (www.fluxgarage.com)
// Copyright: (C) 2024-2025 Dennis Hoelscher
// Licensed under the GNU General Public License v3 or later.
//
// This C port preserves the upstream's behavior:
//   * Eye geometry (defaults 36×36, border radius 8, gap 10).
//   * Per-frame tween math: cur = (cur + next) / 2  (line-by-line).
//   * Auto-blinker random interval = interval_s + random(variation_s).
//   * Idle-mode random target in screen-constraint envelope, same.
//   * Mood overlay draw order (tired top, angry top, happy bottom)
//     and shape (tired/angry triangles, happy rounded-rect carve).
//   * Confused / laugh one-shot animations via h/vFlicker for 500 ms.
// The drawing primitives (fill_rect, fill_round_rect, fill_triangle)
// are clean-room implementations of standard rasterization recipes;
// they target a 1024-byte SSD1306-page framebuffer instead of
// Adafruit_GFX, and so don't carry the upstream's Adafruit_GFX
// dependency. Behavior on the wire (which pixels light up) matches.

#include "robo_eyes.h"

#include <string.h>
#include <stdlib.h>
#include "esp_random.h"

// ---------------------------------------------------------------------------
// Internal state. Mirrors the upstream class fields one-to-one. Comments
// from the upstream are preserved where they document the *why*.
// ---------------------------------------------------------------------------

typedef struct {
    // Screen dimensions + framerate gate.
    int      screen_w;
    int      screen_h;
    int      frame_interval_ms;       // 1000 / fps
    uint32_t fps_timer_ms;

    // Display "colors" — for monochrome OLED, BG=0 (off), MAIN=1 (on).
    uint8_t  bgcolor;
    uint8_t  maincolor;

    // Moods + extras.
    bool tired;
    bool angry;
    bool happy;
    bool curious;
    bool cyclops;
    bool eyeL_open;
    bool eyeR_open;

    // EYE LEFT — size, border radius, current/next/default per axis.
    int  eyeLwidthDefault, eyeLheightDefault;
    int  eyeLwidthCurrent, eyeLheightCurrent;
    int  eyeLwidthNext,    eyeLheightNext;
    int  eyeLheightOffset;
    uint8_t eyeLborderRadiusDefault;
    uint8_t eyeLborderRadiusCurrent;
    uint8_t eyeLborderRadiusNext;

    // EYE RIGHT.
    int  eyeRwidthDefault, eyeRheightDefault;
    int  eyeRwidthCurrent, eyeRheightCurrent;
    int  eyeRwidthNext,    eyeRheightNext;
    int  eyeRheightOffset;
    uint8_t eyeRborderRadiusDefault;
    uint8_t eyeRborderRadiusCurrent;
    uint8_t eyeRborderRadiusNext;

    // Eye positions.
    int eyeLxDefault, eyeLyDefault;
    int eyeLx, eyeLy;
    int eyeLxNext, eyeLyNext;
    int eyeRxDefault, eyeRyDefault;
    int eyeRx, eyeRy;
    int eyeRxNext, eyeRyNext;

    // Eyelids.
    uint8_t eyelidsHeightMax;
    uint8_t eyelidsTiredHeight, eyelidsTiredHeightNext;
    uint8_t eyelidsAngryHeight, eyelidsAngryHeightNext;
    uint8_t eyelidsHappyBottomOffsetMax;
    uint8_t eyelidsHappyBottomOffset, eyelidsHappyBottomOffsetNext;

    // Space between eyes.
    int spaceBetweenDefault;
    int spaceBetweenCurrent;
    int spaceBetweenNext;

    // Horizontal / vertical flicker.
    bool    hFlicker, hFlickerAlternate;
    uint8_t hFlickerAmplitude;
    bool    vFlicker, vFlickerAlternate;
    uint8_t vFlickerAmplitude;

    // Auto-blinker.
    bool     autoblinker;
    int      blinkInterval;
    int      blinkIntervalVariation;
    uint32_t blinktimer;

    // Idle mode.
    bool     idle;
    int      idleInterval;
    int      idleIntervalVariation;
    uint32_t idleAnimationTimer;

    // Confused / laugh one-shots.
    bool     confused;
    uint32_t confusedAnimationTimer;
    int      confusedAnimationDuration;
    bool     confusedToggle;
    bool     laugh;
    uint32_t laughAnimationTimer;
    int      laughAnimationDuration;
    bool     laughToggle;

    // "now" reference for this frame — set at the top of update().
    uint32_t now_ms;

    // Framebuffer (SSD1306 horizontal page order: 8 pages × 128 bytes).
    uint8_t fb[ROBO_EYES_FB_BYTES];
} robo_eyes_state_t;

static robo_eyes_state_t s;

// ---------------------------------------------------------------------------
// Framebuffer primitives. Layout matches firmware/main/oled.c so the FB can
// be memcpy'd straight into the FACE OLED's panel.
// ---------------------------------------------------------------------------

static inline void fb_clear(void)
{
    memset(s.fb, 0, sizeof s.fb);
}

static inline void fb_set_pixel(int x, int y, uint8_t color)
{
    if ((unsigned)x >= (unsigned)s.screen_w) return;
    if ((unsigned)y >= (unsigned)s.screen_h) return;
    const int byte_idx = (y / 8) * s.screen_w + x;
    const uint8_t mask = (uint8_t)(1u << (y & 7));
    if (color) s.fb[byte_idx] |= mask;
    else       s.fb[byte_idx] &= (uint8_t)~mask;
}

static void fb_fill_rect(int x, int y, int w, int h, uint8_t color)
{
    if (w <= 0 || h <= 0) return;
    int x0 = x, y0 = y, x1 = x + w, y1 = y + h;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > s.screen_w) x1 = s.screen_w;
    if (y1 > s.screen_h) y1 = s.screen_h;
    for (int py = y0; py < y1; ++py) {
        for (int px = x0; px < x1; ++px) {
            fb_set_pixel(px, py, color);
        }
    }
}

// Filled rounded rectangle. For each pixel inside the bounding rect,
// fill if it's in the middle strip OR side strip OR inside the
// appropriate corner-circle. Brute-force but the OLED is 128×64 so
// this is cheap (<10 µs typical at 36×36 eye size).
static void fb_fill_round_rect(int x, int y, int w, int h, int r, uint8_t color)
{
    if (w <= 0 || h <= 0) return;
    if (r < 0) r = 0;
    int max_r = (w < h ? w : h) / 2;
    if (r > max_r) r = max_r;

    const int r_sq = r * r;
    const int x1 = x + w - 1;
    const int y1 = y + h - 1;

    // Corner-center coordinates (inset by r from each corner).
    const int cx_tl = x  + r,     cy_tl = y  + r;
    const int cx_tr = x1 - r,     cy_tr = y  + r;
    const int cx_bl = x  + r,     cy_bl = y1 - r;
    const int cx_br = x1 - r,     cy_br = y1 - r;

    for (int py = y; py <= y1; ++py) {
        if (py < 0 || py >= s.screen_h) continue;
        const bool in_top    = (py < cy_tl);
        const bool in_bottom = (py > cy_bl);
        for (int px = x; px <= x1; ++px) {
            if (px < 0 || px >= s.screen_w) continue;
            const bool in_left  = (px < cx_tl);
            const bool in_right = (px > cx_tr);
            // Fast path: pixel is in the middle/side strips, draw.
            if (!in_top && !in_bottom) { fb_set_pixel(px, py, color); continue; }
            if (!in_left && !in_right) { fb_set_pixel(px, py, color); continue; }
            // Otherwise it's in one of the four corner boxes — test the
            // matching circle equation.
            int cx, cy;
            if (in_top && in_left)        { cx = cx_tl; cy = cy_tl; }
            else if (in_top && in_right)  { cx = cx_tr; cy = cy_tr; }
            else if (in_bottom && in_left){ cx = cx_bl; cy = cy_bl; }
            else                          { cx = cx_br; cy = cy_br; }
            const int dx = px - cx, dy = py - cy;
            if (dx * dx + dy * dy <= r_sq) fb_set_pixel(px, py, color);
        }
    }
}

// Filled triangle. Scanline rasterization: sort vertices by y, for each
// scanline interpolate x along the two active edges, fill horizontally.
static void fb_fill_triangle(int x0, int y0, int x1, int y1, int x2, int y2, uint8_t color)
{
    // Sort by y so y0 <= y1 <= y2.
    int t;
    if (y0 > y1) { t = y0; y0 = y1; y1 = t; t = x0; x0 = x1; x1 = t; }
    if (y1 > y2) { t = y1; y1 = y2; y2 = t; t = x1; x1 = x2; x2 = t; }
    if (y0 > y1) { t = y0; y0 = y1; y1 = t; t = x0; x0 = x1; x1 = t; }

    if (y0 == y2) {
        // Degenerate: all on one scanline.
        int xa = x0, xb = x0;
        if (x1 < xa) xa = x1;
        if (x1 > xb) xb = x1;
        if (x2 < xa) xa = x2;
        if (x2 > xb) xb = x2;
        fb_fill_rect(xa, y0, xb - xa + 1, 1, color);
        return;
    }

    // Long edge 0->2; short edges 0->1 (upper half) and 1->2 (lower half).
    for (int py = y0; py <= y2; ++py) {
        // Long edge x at py.
        int xa_num = (y2 - y0);
        int xa = x0 + ((x2 - x0) * (py - y0)) / (xa_num == 0 ? 1 : xa_num);
        // Short edge x at py.
        int xb;
        if (py < y1) {
            int d = (y1 - y0);
            xb = x0 + ((x1 - x0) * (py - y0)) / (d == 0 ? 1 : d);
        } else if (py == y1) {
            xb = x1;
        } else {
            int d = (y2 - y1);
            xb = x1 + ((x2 - x1) * (py - y1)) / (d == 0 ? 1 : d);
        }
        if (xa > xb) { int s2 = xa; xa = xb; xb = s2; }
        fb_fill_rect(xa, py, xb - xa + 1, 1, color);
    }
}

// ---------------------------------------------------------------------------
// Helpers ported from upstream getScreenConstraint_*().
// ---------------------------------------------------------------------------

static int screen_constraint_x(void)
{
    return s.screen_w - s.eyeLwidthCurrent - s.spaceBetweenCurrent - s.eyeRwidthCurrent;
}

static int screen_constraint_y(void)
{
    // Upstream uses eyeLheightDefault here (not current), specifically because
    // height varies during blink/curious. Keep that — matters at extremes.
    return s.screen_h - s.eyeLheightDefault;
}

static int rand_range(int n)
{
    if (n <= 0) return 0;
    return (int)(esp_random() % (uint32_t)n);
}

// ---------------------------------------------------------------------------
// Lifecycle.
// ---------------------------------------------------------------------------

void robo_eyes_begin(int screen_w, int screen_h, uint8_t framerate)
{
    memset(&s, 0, sizeof s);

    s.screen_w = screen_w > 0 ? screen_w : ROBO_EYES_DEFAULT_W;
    s.screen_h = screen_h > 0 ? screen_h : ROBO_EYES_DEFAULT_H;

    s.bgcolor   = 0;
    s.maincolor = 1;

    // Geometry defaults — verbatim from upstream.
    s.eyeLwidthDefault         = 36;
    s.eyeLheightDefault        = 36;
    s.eyeLwidthCurrent         = s.eyeLwidthDefault;
    s.eyeLheightCurrent        = 1;     // start with closed eye
    s.eyeLwidthNext            = s.eyeLwidthDefault;
    s.eyeLheightNext           = s.eyeLheightDefault;
    s.eyeLheightOffset         = 0;
    s.eyeLborderRadiusDefault  = 8;
    s.eyeLborderRadiusCurrent  = s.eyeLborderRadiusDefault;
    s.eyeLborderRadiusNext     = s.eyeLborderRadiusDefault;

    s.eyeRwidthDefault         = s.eyeLwidthDefault;
    s.eyeRheightDefault        = s.eyeLheightDefault;
    s.eyeRwidthCurrent         = s.eyeRwidthDefault;
    s.eyeRheightCurrent        = 1;
    s.eyeRwidthNext            = s.eyeRwidthDefault;
    s.eyeRheightNext           = s.eyeRheightDefault;
    s.eyeRheightOffset         = 0;
    s.eyeRborderRadiusDefault  = 8;
    s.eyeRborderRadiusCurrent  = s.eyeRborderRadiusDefault;
    s.eyeRborderRadiusNext     = s.eyeRborderRadiusDefault;

    s.spaceBetweenDefault      = 10;
    s.spaceBetweenCurrent      = s.spaceBetweenDefault;
    s.spaceBetweenNext         = s.spaceBetweenDefault;

    // Position defaults — same formulas as upstream's member initializers.
    s.eyeLxDefault = (s.screen_w - (s.eyeLwidthDefault + s.spaceBetweenDefault + s.eyeRwidthDefault)) / 2;
    s.eyeLyDefault = (s.screen_h - s.eyeLheightDefault) / 2;
    s.eyeLx = s.eyeLxNext = s.eyeLxDefault;
    s.eyeLy = s.eyeLyNext = s.eyeLyDefault;
    s.eyeRxDefault = s.eyeLx + s.eyeLwidthCurrent + s.spaceBetweenDefault;
    s.eyeRyDefault = s.eyeLy;
    s.eyeRx = s.eyeRxNext = s.eyeRxDefault;
    s.eyeRy = s.eyeRyNext = s.eyeRyDefault;

    s.eyelidsHeightMax            = (uint8_t)(s.eyeLheightDefault / 2);
    s.eyelidsTiredHeight          = 0;
    s.eyelidsTiredHeightNext      = 0;
    s.eyelidsAngryHeight          = 0;
    s.eyelidsAngryHeightNext      = 0;
    s.eyelidsHappyBottomOffsetMax = (uint8_t)((s.eyeLheightDefault / 2) + 3);
    s.eyelidsHappyBottomOffset    = 0;
    s.eyelidsHappyBottomOffsetNext = 0;

    s.hFlickerAmplitude = 2;
    s.vFlickerAmplitude = 10;

    s.blinkInterval          = 1;   // upstream defaults
    s.blinkIntervalVariation = 4;
    s.idleInterval           = 1;
    s.idleIntervalVariation  = 3;

    s.confusedAnimationDuration = 500;
    s.confusedToggle            = true;
    s.laughAnimationDuration    = 500;
    s.laughToggle               = true;

    robo_eyes_set_framerate(framerate ? framerate : 50);

    fb_clear();
    s.fps_timer_ms = 0;
}

bool robo_eyes_update(uint32_t now_ms)
{
    s.now_ms = now_ms;
    // Frame-rate gate matching upstream's `millis() - fpsTimer >= frameInterval`.
    if ((int32_t)(now_ms - s.fps_timer_ms) < s.frame_interval_ms) return false;
    s.fps_timer_ms = now_ms;

    // -------- PRE-CALCULATIONS — translated line-by-line from drawEyes() ----

    // Vertical size offset for larger eyes when looking left or right (curious).
    if (s.curious) {
        if (s.eyeLxNext <= 10) {
            s.eyeLheightOffset = 8;
        } else if (s.eyeLxNext >= (screen_constraint_x() - 10) && s.cyclops) {
            s.eyeLheightOffset = 8;
        } else {
            s.eyeLheightOffset = 0;
        }
        if (s.eyeRxNext >= s.screen_w - s.eyeRwidthCurrent - 10) {
            s.eyeRheightOffset = 8;
        } else {
            s.eyeRheightOffset = 0;
        }
    } else {
        s.eyeLheightOffset = 0;
        s.eyeRheightOffset = 0;
    }

    // Left eye height tween + vertical centering.
    s.eyeLheightCurrent = (s.eyeLheightCurrent + s.eyeLheightNext + s.eyeLheightOffset) / 2;
    s.eyeLy += ((s.eyeLheightDefault - s.eyeLheightCurrent) / 2);
    s.eyeLy -= s.eyeLheightOffset / 2;
    // Right eye height tween + vertical centering.
    s.eyeRheightCurrent = (s.eyeRheightCurrent + s.eyeRheightNext + s.eyeRheightOffset) / 2;
    s.eyeRy += (s.eyeRheightDefault - s.eyeRheightCurrent) / 2;
    s.eyeRy -= s.eyeRheightOffset / 2;

    // Open eyes again once they've finished closing.
    if (s.eyeL_open) {
        if (s.eyeLheightCurrent <= 1 + s.eyeLheightOffset) {
            s.eyeLheightNext = s.eyeLheightDefault;
        }
    }
    if (s.eyeR_open) {
        if (s.eyeRheightCurrent <= 1 + s.eyeRheightOffset) {
            s.eyeRheightNext = s.eyeRheightDefault;
        }
    }

    // Widths.
    s.eyeLwidthCurrent = (s.eyeLwidthCurrent + s.eyeLwidthNext) / 2;
    s.eyeRwidthCurrent = (s.eyeRwidthCurrent + s.eyeRwidthNext) / 2;
    // Space between eyes.
    s.spaceBetweenCurrent = (s.spaceBetweenCurrent + s.spaceBetweenNext) / 2;
    // Left eye coordinates.
    s.eyeLx = (s.eyeLx + s.eyeLxNext) / 2;
    s.eyeLy = (s.eyeLy + s.eyeLyNext) / 2;
    // Right eye coordinates — derived from left eye + width + spacing.
    s.eyeRxNext = s.eyeLxNext + s.eyeLwidthCurrent + s.spaceBetweenCurrent;
    s.eyeRyNext = s.eyeLyNext;
    s.eyeRx = (s.eyeRx + s.eyeRxNext) / 2;
    s.eyeRy = (s.eyeRy + s.eyeRyNext) / 2;
    // Border radii.
    s.eyeLborderRadiusCurrent = (uint8_t)((s.eyeLborderRadiusCurrent + s.eyeLborderRadiusNext) / 2);
    s.eyeRborderRadiusCurrent = (uint8_t)((s.eyeRborderRadiusCurrent + s.eyeRborderRadiusNext) / 2);

    // -------- MACRO ANIMATIONS --------

    // Auto-blinker.
    if (s.autoblinker) {
        if (s.now_ms >= s.blinktimer) {
            robo_eyes_blink();
            s.blinktimer = s.now_ms
                + (uint32_t)(s.blinkInterval * 1000)
                + (uint32_t)(rand_range(s.blinkIntervalVariation) * 1000);
        }
    }

    // Laughing — eyes shake up/down for laughAnimationDuration ms.
    if (s.laugh) {
        if (s.laughToggle) {
            robo_eyes_set_vflicker(true, 5);
            s.laughAnimationTimer = s.now_ms;
            s.laughToggle = false;
        } else if (s.now_ms >= s.laughAnimationTimer + (uint32_t)s.laughAnimationDuration) {
            robo_eyes_set_vflicker(false, 0);
            s.laughToggle = true;
            s.laugh = false;
        }
    }

    // Confused — eyes shake left/right for confusedAnimationDuration ms.
    if (s.confused) {
        if (s.confusedToggle) {
            robo_eyes_set_hflicker(true, 20);
            s.confusedAnimationTimer = s.now_ms;
            s.confusedToggle = false;
        } else if (s.now_ms >= s.confusedAnimationTimer + (uint32_t)s.confusedAnimationDuration) {
            robo_eyes_set_hflicker(false, 0);
            s.confusedToggle = true;
            s.confused = false;
        }
    }

    // Idle — pick a random target position within the envelope at random intervals.
    if (s.idle) {
        if (s.now_ms >= s.idleAnimationTimer) {
            s.eyeLxNext = rand_range(screen_constraint_x());
            s.eyeLyNext = rand_range(screen_constraint_y());
            s.idleAnimationTimer = s.now_ms
                + (uint32_t)(s.idleInterval * 1000)
                + (uint32_t)(rand_range(s.idleIntervalVariation) * 1000);
        }
    }

    // Horizontal flicker offset.
    if (s.hFlicker) {
        if (s.hFlickerAlternate) {
            s.eyeLx += s.hFlickerAmplitude;
            s.eyeRx += s.hFlickerAmplitude;
        } else {
            s.eyeLx -= s.hFlickerAmplitude;
            s.eyeRx -= s.hFlickerAmplitude;
        }
        s.hFlickerAlternate = !s.hFlickerAlternate;
    }
    // Vertical flicker offset.
    if (s.vFlicker) {
        if (s.vFlickerAlternate) {
            s.eyeLy += s.vFlickerAmplitude;
            s.eyeRy += s.vFlickerAmplitude;
        } else {
            s.eyeLy -= s.vFlickerAmplitude;
            s.eyeRy -= s.vFlickerAmplitude;
        }
        s.vFlickerAlternate = !s.vFlickerAlternate;
    }

    // Cyclops mode zeros the right eye.
    if (s.cyclops) {
        s.eyeRwidthCurrent = 0;
        s.eyeRheightCurrent = 0;
        s.spaceBetweenCurrent = 0;
    }

    // -------- DRAW --------

    fb_clear();

    // Basic eye rectangles.
    fb_fill_round_rect(s.eyeLx, s.eyeLy, s.eyeLwidthCurrent, s.eyeLheightCurrent,
                       s.eyeLborderRadiusCurrent, s.maincolor);
    if (!s.cyclops) {
        fb_fill_round_rect(s.eyeRx, s.eyeRy, s.eyeRwidthCurrent, s.eyeRheightCurrent,
                           s.eyeRborderRadiusCurrent, s.maincolor);
    }

    // Mood-transition target heights.
    if (s.tired) {
        s.eyelidsTiredHeightNext = (uint8_t)(s.eyeLheightCurrent / 2);
        s.eyelidsAngryHeightNext = 0;
    } else {
        s.eyelidsTiredHeightNext = 0;
    }
    if (s.angry) {
        s.eyelidsAngryHeightNext = (uint8_t)(s.eyeLheightCurrent / 2);
        s.eyelidsTiredHeightNext = 0;
    } else {
        s.eyelidsAngryHeightNext = 0;
    }
    if (s.happy) {
        s.eyelidsHappyBottomOffsetNext = (uint8_t)(s.eyeLheightCurrent / 2);
    } else {
        s.eyelidsHappyBottomOffsetNext = 0;
    }

    // Tired top eyelids (drooping inward).
    s.eyelidsTiredHeight = (uint8_t)((s.eyelidsTiredHeight + s.eyelidsTiredHeightNext) / 2);
    if (!s.cyclops) {
        // Left eye: triangle from top-left along top edge down to bottom-left of eyelid.
        fb_fill_triangle(s.eyeLx, s.eyeLy - 1,
                         s.eyeLx + s.eyeLwidthCurrent, s.eyeLy - 1,
                         s.eyeLx, s.eyeLy + s.eyelidsTiredHeight - 1,
                         s.bgcolor);
        // Right eye: triangle mirrored to drop on the right side.
        fb_fill_triangle(s.eyeRx, s.eyeRy - 1,
                         s.eyeRx + s.eyeRwidthCurrent, s.eyeRy - 1,
                         s.eyeRx + s.eyeRwidthCurrent, s.eyeRy + s.eyelidsTiredHeight - 1,
                         s.bgcolor);
    } else {
        // Cyclops: two half-triangles.
        fb_fill_triangle(s.eyeLx, s.eyeLy - 1,
                         s.eyeLx + (s.eyeLwidthCurrent / 2), s.eyeLy - 1,
                         s.eyeLx, s.eyeLy + s.eyelidsTiredHeight - 1,
                         s.bgcolor);
        fb_fill_triangle(s.eyeLx + (s.eyeLwidthCurrent / 2), s.eyeLy - 1,
                         s.eyeLx + s.eyeLwidthCurrent, s.eyeLy - 1,
                         s.eyeLx + s.eyeLwidthCurrent, s.eyeLy + s.eyelidsTiredHeight - 1,
                         s.bgcolor);
    }

    // Angry top eyelids (angled inward toward the nose).
    s.eyelidsAngryHeight = (uint8_t)((s.eyelidsAngryHeight + s.eyelidsAngryHeightNext) / 2);
    if (!s.cyclops) {
        fb_fill_triangle(s.eyeLx, s.eyeLy - 1,
                         s.eyeLx + s.eyeLwidthCurrent, s.eyeLy - 1,
                         s.eyeLx + s.eyeLwidthCurrent, s.eyeLy + s.eyelidsAngryHeight - 1,
                         s.bgcolor);
        fb_fill_triangle(s.eyeRx, s.eyeRy - 1,
                         s.eyeRx + s.eyeRwidthCurrent, s.eyeRy - 1,
                         s.eyeRx, s.eyeRy + s.eyelidsAngryHeight - 1,
                         s.bgcolor);
    } else {
        fb_fill_triangle(s.eyeLx, s.eyeLy - 1,
                         s.eyeLx + (s.eyeLwidthCurrent / 2), s.eyeLy - 1,
                         s.eyeLx + (s.eyeLwidthCurrent / 2), s.eyeLy + s.eyelidsAngryHeight - 1,
                         s.bgcolor);
        fb_fill_triangle(s.eyeLx + (s.eyeLwidthCurrent / 2), s.eyeLy - 1,
                         s.eyeLx + s.eyeLwidthCurrent, s.eyeLy - 1,
                         s.eyeLx + (s.eyeLwidthCurrent / 2), s.eyeLy + s.eyelidsAngryHeight - 1,
                         s.bgcolor);
    }

    // Happy bottom eyelids — rounded rect in BG color carving the eye from below.
    s.eyelidsHappyBottomOffset = (uint8_t)((s.eyelidsHappyBottomOffset + s.eyelidsHappyBottomOffsetNext) / 2);
    fb_fill_round_rect(s.eyeLx - 1,
                       (s.eyeLy + s.eyeLheightCurrent) - s.eyelidsHappyBottomOffset + 1,
                       s.eyeLwidthCurrent + 2, s.eyeLheightDefault,
                       s.eyeLborderRadiusCurrent, s.bgcolor);
    if (!s.cyclops) {
        fb_fill_round_rect(s.eyeRx - 1,
                           (s.eyeRy + s.eyeRheightCurrent) - s.eyelidsHappyBottomOffset + 1,
                           s.eyeRwidthCurrent + 2, s.eyeRheightDefault,
                           s.eyeRborderRadiusCurrent, s.bgcolor);
    }

    // Note: upstream's "sweat" feature is intentionally omitted from this
    // port. The float-driven drop animation is heavyweight and the v3 plan
    // has no use for it; re-add later if a "stressed" mood enters the
    // state machine.

    return true;
}

const uint8_t *robo_eyes_get_framebuffer(void)
{
    return s.fb;
}

// ---------------------------------------------------------------------------
// Setters (upstream surface).
// ---------------------------------------------------------------------------

void robo_eyes_set_framerate(uint8_t fps)
{
    if (fps == 0) fps = 50;
    s.frame_interval_ms = 1000 / fps;
}

void robo_eyes_set_width(uint8_t left, uint8_t right)
{
    s.eyeLwidthNext = left;
    s.eyeRwidthNext = right;
    s.eyeLwidthDefault = left;
    s.eyeRwidthDefault = right;
}

void robo_eyes_set_height(uint8_t left, uint8_t right)
{
    s.eyeLheightNext = left;
    s.eyeRheightNext = right;
    s.eyeLheightDefault = left;
    s.eyeRheightDefault = right;
}

void robo_eyes_set_borderradius(uint8_t left, uint8_t right)
{
    s.eyeLborderRadiusNext = left;
    s.eyeRborderRadiusNext = right;
    s.eyeLborderRadiusDefault = left;
    s.eyeRborderRadiusDefault = right;
}

void robo_eyes_set_spacebetween(int space)
{
    s.spaceBetweenNext = space;
    s.spaceBetweenDefault = space;
}

void robo_eyes_set_mood(robo_eyes_mood_t mood)
{
    switch (mood) {
    case ROBO_EYES_MOOD_TIRED: s.tired = true;  s.angry = false; s.happy = false; break;
    case ROBO_EYES_MOOD_ANGRY: s.tired = false; s.angry = true;  s.happy = false; break;
    case ROBO_EYES_MOOD_HAPPY: s.tired = false; s.angry = false; s.happy = true;  break;
    default:                   s.tired = false; s.angry = false; s.happy = false; break;
    }
}

void robo_eyes_set_position(robo_eyes_position_t pos)
{
    const int cx = screen_constraint_x();
    const int cy = screen_constraint_y();
    switch (pos) {
    case ROBO_EYES_POS_N:  s.eyeLxNext = cx / 2; s.eyeLyNext = 0;      break;
    case ROBO_EYES_POS_NE: s.eyeLxNext = cx;     s.eyeLyNext = 0;      break;
    case ROBO_EYES_POS_E:  s.eyeLxNext = cx;     s.eyeLyNext = cy / 2; break;
    case ROBO_EYES_POS_SE: s.eyeLxNext = cx;     s.eyeLyNext = cy;     break;
    case ROBO_EYES_POS_S:  s.eyeLxNext = cx / 2; s.eyeLyNext = cy;     break;
    case ROBO_EYES_POS_SW: s.eyeLxNext = 0;      s.eyeLyNext = cy;     break;
    case ROBO_EYES_POS_W:  s.eyeLxNext = 0;      s.eyeLyNext = cy / 2; break;
    case ROBO_EYES_POS_NW: s.eyeLxNext = 0;      s.eyeLyNext = 0;      break;
    default:               s.eyeLxNext = cx / 2; s.eyeLyNext = cy / 2; break;
    }
}

void robo_eyes_set_autoblinker(bool active, int interval_s, int variation_s)
{
    s.autoblinker            = active;
    s.blinkInterval          = interval_s;
    s.blinkIntervalVariation = variation_s;
}

void robo_eyes_set_idle_mode(bool active, int interval_s, int variation_s)
{
    s.idle                  = active;
    s.idleInterval          = interval_s;
    s.idleIntervalVariation = variation_s;
}

void robo_eyes_set_curiosity(bool on) { s.curious = on; }
void robo_eyes_set_cyclops  (bool on) { s.cyclops = on; }

void robo_eyes_set_hflicker(bool on, uint8_t amplitude)
{
    s.hFlicker = on;
    s.hFlickerAmplitude = amplitude;
}

void robo_eyes_set_vflicker(bool on, uint8_t amplitude)
{
    s.vFlicker = on;
    s.vFlickerAmplitude = amplitude;
}

// ---------------------------------------------------------------------------
// Direct eye control.
// ---------------------------------------------------------------------------

void robo_eyes_close(void)
{
    s.eyeLheightNext = 1;
    s.eyeRheightNext = 1;
    s.eyeL_open = false;
    s.eyeR_open = false;
}

void robo_eyes_open(void)
{
    s.eyeL_open = true;
    s.eyeR_open = true;
}

void robo_eyes_blink(void)
{
    robo_eyes_close();
    robo_eyes_open();
}

void robo_eyes_close_lr(bool left, bool right)
{
    if (left)  { s.eyeLheightNext = 1; s.eyeL_open = false; }
    if (right) { s.eyeRheightNext = 1; s.eyeR_open = false; }
}

void robo_eyes_open_lr(bool left, bool right)
{
    if (left)  s.eyeL_open = true;
    if (right) s.eyeR_open = true;
}

void robo_eyes_blink_lr(bool left, bool right)
{
    robo_eyes_close_lr(left, right);
    robo_eyes_open_lr(left, right);
}

void robo_eyes_anim_confused(void) { s.confused = true; }
void robo_eyes_anim_laugh   (void) { s.laugh    = true; }

// ---------------------------------------------------------------------------
// Continuous-gaze extension.
// ---------------------------------------------------------------------------

void robo_eyes_set_position_continuous(float angle_deg, float vertical_frac)
{
    if (angle_deg >  90.0f) angle_deg =  90.0f;
    if (angle_deg < -90.0f) angle_deg = -90.0f;
    if (vertical_frac >  1.0f) vertical_frac =  1.0f;
    if (vertical_frac < -1.0f) vertical_frac = -1.0f;

    const int cx = screen_constraint_x();
    const int cy = screen_constraint_y();
    // angle -90 → x = 0 (west / full left)
    // angle  +90 → x = cx (east / full right)
    // angle   0 → x = cx/2 (center)
    float fx = (angle_deg + 90.0f) / 180.0f;       // [0,1]
    float fy = (vertical_frac + 1.0f) / 2.0f;       // [0,1]
    int nx = (int)(fx * (float)cx + 0.5f);
    int ny = (int)(fy * (float)cy + 0.5f);
    if (nx < 0)  nx = 0;
    if (nx > cx) nx = cx;
    if (ny < 0)  ny = 0;
    if (ny > cy) ny = cy;
    s.eyeLxNext = nx;
    s.eyeLyNext = ny;
}
