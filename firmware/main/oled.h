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

// Push the framebuffer to the panel.
void oled_flush(void);
