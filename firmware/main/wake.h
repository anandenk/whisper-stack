// v2-M2 on-device wake-word detection via esp-sr (WakeNet + AFE).
//
// Replaces the server-side /listen stream pattern (listen_stream.c)
// with an in-process inference loop: mic frames flow through AFE's
// preprocessing (NS / AGC) into a WakeNet model that fires a wake
// event when it detects the configured wake phrase.
//
// Architecturally this kills the chronic continuous-/listen-stream
// exposure that drove gotcha #9 in v1 — the only WiFi traffic now
// is per-turn /converse uploads. Server-side /listen + wakeword.py
// stay in tree for the web client + tests/jarvis.py.
//
// Pipeline:
//   i2s_mic ring  ─► feed_task ─► (gain × X10/10) ─► preroll ring
//                                                ─► afe_handle->feed
//   afe_handle->fetch ─► fetch_task ─► on WAKENET_DETECTED:
//                                       do_round_trip_streaming_with_preroll(...)
//
// State-machine routing: the feed task only consumes the mic ring in
// APP_STATE_IDLE. In LISTENING/THINKING/SPEAKING it vTaskDelays and
// vad_record is the sole ring consumer. Polling, not suspend/semaphore —
// see notes/esp32-v2-plan.md for the rationale.

#pragma once

#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

// Spawn the AFE feed + fetch tasks. Idempotent.
//
// `ip` is borrowed (not copied) — used only to redraw the OLED status
// row and passed through to do_round_trip on wake. Lifetime must be
// ≥ the tasks', so pass the static `ip` string from main.c directly.
//
// Returns ESP_OK on success; ESP_FAIL if model/AFE init fails (out
// of memory, model partition missing, no WakeNet model packed, etc).
esp_err_t wake_init(const char *ip);
