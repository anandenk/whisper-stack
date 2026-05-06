// Peak-threshold VAD — drains the I²S ring buffer frame-by-frame and
// invokes a callback with each frame, deciding when to stop based on:
//   * peak amplitude crossing a threshold ("voice detected"),
//   * a continuous silence dwell after voice ("user stopped talking"),
//   * an initial timeout if no voice ever shows up,
//   * a max-record cap as a safety net,
//   * an external cancel hook (button long-press).
//
// The callback approach decouples VAD from where the audio goes —
// M3 streams it to /converse via HTTP chunked-transfer, but the same
// VAD will feed M4's chunked /listen post just as well.

#pragma once

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"

// 300 instead of jarvis.py's 600 because INMP441 has no AGC and mounting
// distance (~12+ in. from the user) puts most conversational-speech peaks
// right around the original 600 threshold — quieter syllables fall below
// it, so the silence-dwell counter resets too rarely and the dwell trips
// mid-utterance. 300 is still ~3× the idle-ambient peak (~100) measured
// in M1's snap test, so room noise won't false-trigger voice detection.
#define VAD_PEAK_THRESHOLD       300
// 2500 ms instead of jarvis.py's 1500 ms because users naturally pause
// >1.5 s mid-sentence when reading from a screen or thinking. The cost
// is 1 s of extra wait at end-of-utterance before upload starts; the
// alternative — being cut off mid-sentence — is the worse failure mode.
#define VAD_SILENCE_DWELL_MS     2500
#define VAD_INITIAL_TIMEOUT_MS   15000
#define VAD_MAX_RECORD_MS        30000   // safety cap; streaming means heap doesn't constrain this

typedef enum {
    VAD_STOP_OK_SILENCE,        // detected silence after voice — normal stop
    VAD_STOP_OK_MAX_DURATION,   // safety cap hit — also a normal "send what we have"
    VAD_STOP_NO_SPEECH,         // initial timeout — user pressed but never spoke
    VAD_STOP_BUTTON_CANCEL,     // long-press detected — abort
    VAD_STOP_FRAME_CB_ERROR,    // callback returned non-OK (HTTP failure, etc)
    VAD_STOP_MIC_ERROR,         // i2s read failed
} vad_stop_t;

typedef struct {
    vad_stop_t reason;
    size_t     total_samples;     // count of int16 samples we passed to the callback
    uint32_t   peak_seen;         // max peak observed across the recording
    uint32_t   duration_ms;       // wall-clock duration of the record
    esp_err_t  cb_error;          // populated if reason==FRAME_CB_ERROR
} vad_result_t;

// Frame callback: receives one mic frame's worth of int16 samples.
// Return ESP_OK to keep going; anything else aborts the record with
// VAD_STOP_FRAME_CB_ERROR (the err is propagated via vad_result_t.cb_error).
typedef esp_err_t (*vad_frame_cb_t)(const int16_t *frame, size_t n_samples, void *user);

// Cancel hook: VAD calls this between frames. Return true to abort the
// record with VAD_STOP_BUTTON_CANCEL. May be NULL — no cancel support
// in that case.
typedef bool (*vad_cancel_cb_t)(void *user);

// Drain the mic ring buffer fresh, then loop frames through `on_frame`
// until a stop condition fires. `on_cancel` is polled between frames.
// `user` is passed to both callbacks. `out` is filled in either way.
// Returns ESP_OK iff the result is one of the OK stops.
esp_err_t vad_record(vad_frame_cb_t on_frame, vad_cancel_cb_t on_cancel,
                     void *user, vad_result_t *out);
