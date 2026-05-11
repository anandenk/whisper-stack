// SSD1306 128x64 I²C OLED driver wrapper around ESP-IDF's esp_lcd_panel_ssd1306.
//
// TWO PANELS supported:
//   * MAIN — status display (text, VU bar). On I²C bus 0, GPIO 21/22, addr 0x3C.
//     Public API is the unprefixed `oled_*` functions, unchanged from M1-M6.
//   * FACE — animated cat face. On I²C bus 1, GPIO 16/17, addr 0x3C.
//     Public API is the `oled_face_*` functions added below.
// Both panels share the SSD1306 framebuffer layout (8 pages × 128 bytes,
// bit 0 = top pixel of each page) so bitmaps designed for one drop into
// the other.

#pragma once

#include <stdint.h>
#include "esp_err.h"

#define OLED_W 128
#define OLED_H 64
#define OLED_FB_BYTES ((OLED_W * OLED_H) / 8)  // 1024
#define OLED_CHARS_PER_LINE 21  // 128 / 6 (font width) = 21
#define OLED_LINES 8            // 64 / 8 (font height) = 8

// ---------------------------------------------------------------------------
// MAIN panel — status display.
// ---------------------------------------------------------------------------

// Bring up I²C bus + SSD1306 panel. Idempotent — a second call is a no-op.
esp_err_t oled_init(void);

// Wipe the framebuffer to all-off.
void oled_clear(void);

// Render `text` at line `line` (0..OLED_LINES-1). Long strings are clipped
// at OLED_CHARS_PER_LINE. Non-printable chars (<0x20 or >0x7E) render as ' '.
void oled_draw_text(int line, const char *text);

// Push the entire framebuffer to the panel (~25 ms over I²C @ 400 kHz).
void oled_flush(void);

// ---------------------------------------------------------------------------
// FACE panel — animated cat face on the second OLED.
// ---------------------------------------------------------------------------

// Bring up I²C bus 1 + the second SSD1306 panel. Idempotent.
// Independent from oled_init() — either can be called without the other
// (e.g. if the face panel isn't wired, oled_init() still works fine).
esp_err_t oled_face_init(void);

// Wipe the face framebuffer to all-off.
void oled_face_clear(void);

// Fill the face framebuffer with a single byte pattern. 0x00 = all off,
// 0xFF = all on, 0xAA / 0x55 = horizontal stripes. Useful for boot tests.
void oled_face_fill(uint8_t pattern);

// Copy a full-screen 1-bit bitmap into the face framebuffer. The bytes
// must be exactly OLED_FB_BYTES long, in SSD1306 horizontal addressing
// order (8 pages × 128 bytes, bit 0 = top pixel of each page) — same
// layout that tests/oled_animate.py emits.
void oled_face_draw_bitmap(const uint8_t *bytes);

// Push the face framebuffer to the panel (~25 ms).
void oled_face_flush(void);
