// M4 wake-word listener — raw-socket chunked-transfer POST to /listen
// while the device is IDLE, with a 500 ms audio pre-roll retained so
// the start of the user's question (which often overlaps the tail of
// "...jarvis") isn't lost when LISTENING begins.
//
// Why raw sockets and not esp_http_client: /listen is a full-duplex
// pattern — the firmware writes chunks indefinitely and needs to peek
// for a server response mid-stream. esp_http_client doesn't expose the
// underlying transport so we can't `select()` on it. lwIP sockets give
// us the control we need at the cost of ~150 lines of HTTP plumbing.
//
// /converse stays on esp_http_client (M3 streaming) because it's a
// classic "send all, then read response" pattern that the IDF handles
// fine. They're deliberately different.

#pragma once

#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

// Spawn the listen_stream_task. Idempotent.
//
// `ip` is borrowed (not copied) and used only for OLED status redraws.
// Lifetime must be ≥ the task's, so pass the static `ip` string from
// main.c directly.
esp_err_t listen_stream_init(const char *ip);
