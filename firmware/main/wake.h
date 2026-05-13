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
#include "esp_vad.h"   // for vad_state_t (VAD_SILENCE / VAD_SPEECH)

// Spawn the AFE feed + fetch tasks. Idempotent.
//
// `ip` is borrowed (not copied) — used only to redraw the OLED status
// row and passed through to do_round_trip on wake. Lifetime must be
// ≥ the tasks', so pass the static `ip` string from main.c directly.
//
// Returns ESP_OK on success; ESP_FAIL if model/AFE init fails (out
// of memory, model partition missing, no WakeNet model packed, etc).
esp_err_t wake_init(const char *ip);

// v2-M6: pull one AFE-processed audio frame from the LISTENING output
// queue. Each frame is the AFE pipeline's output (post-NS / post-AGC /
// 30 ms chunk) plus the vadnet1_medium VAD verdict for that chunk.
//
// Consumed by vad_record during LISTENING. The wake module's
// wake_fetch_task pushes frames into this queue while s_state ==
// APP_STATE_LISTENING; in other states frames are dropped (and counted
// via wake_listen_drops_take_and_reset).
//
// On success, sets:
//   *data       — heap-allocated int16 samples (caller MUST `free` it)
//   *n_samples  — number of int16 samples in `*data`
//   *vad_state  — VAD_SILENCE or VAD_SPEECH for this chunk
//
// Returns:
//   ESP_OK              — frame returned, ownership transferred
//   ESP_ERR_TIMEOUT     — no frame within timeout_ms
//   ESP_ERR_INVALID_STATE — wake module not initialized
esp_err_t wake_listen_next(int16_t **data, size_t *n_samples,
                           vad_state_t *vad_state, uint32_t timeout_ms);

// Read + clear the drop counter for the LISTENING output queue.
// Frames are dropped (newest-drop policy) when the queue fills up
// because vad_record fell behind. Logged at end of each vad_record
// call alongside i2s_mic's recycled=N (gotcha #9 pattern).
uint32_t wake_listen_drops_take_and_reset(void);
