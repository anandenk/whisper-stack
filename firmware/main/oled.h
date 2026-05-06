// SSD1306 128x64 I²C OLED driver wrapper around ESP-IDF's esp_lcd_panel_ssd1306.
//
// Public API is intentionally minimal — enough for status text and an IP
// address line during M1, plus a VU bar for M6.

#pragma once

#include <stdint.h>
#include "esp_err.h"

#define OLED_W 128
#define OLED_H 64
#define OLED_CHARS_PER_LINE 21  // 128 / 6 (font width) = 21
#define OLED_LINES 8            // 64 / 8 (font height) = 8

// Bring up I²C bus + SSD1306 panel. Idempotent — a second call is a no-op.
esp_err_t oled_init(void);

// Wipe the framebuffer to all-off.
void oled_clear(void);

// Render `text` at line `line` (0..OLED_LINES-1). Long strings are clipped
// at OLED_CHARS_PER_LINE. Non-printable chars (<0x20 or >0x7E) render as ' '.
void oled_draw_text(int line, const char *text);

// Render a horizontal bar on `line` (0..OLED_LINES-1).
//   fill_px:    width of the filled portion in pixels [0..OLED_W]. Drawn as
//               a 4-pixel-tall bar centered vertically in the row.
//   marker_px:  x-coordinate of a 1-px-wide full-height marker tick that
//               always renders, regardless of fill. Use to mark the VAD
//               threshold so the user can see "is my voice clearing it".
//               Pass <0 to omit the marker.
// Overwrites the entire row (clears any text previously rendered there).
void oled_draw_bar(int line, int fill_px, int marker_px);

// Push the entire framebuffer to the panel (~25 ms over I²C @ 400 kHz).
void oled_flush(void);

// Push only one row (8-pixel page) to the panel (~3.5 ms). Useful for live
// updates like the VU bar where a full-frame flush would flicker.
void oled_flush_row(int line);
