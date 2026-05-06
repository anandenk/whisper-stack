// INMP441 I²S MEMS mic capture — 16 kHz mono int16 ring buffer.
//
// The driver runs a small audio_in_task that reads I²S0 frames in a loop
// and converts the 24-bit-MSB-aligned-in-32-bit-slot samples to int16 via
// `>> 16` (drops the 8 unused LSBs of the 32-bit slot AND the 8 LSBs of the
// 24-bit sample — that's what the plan calls "discard the bottom 8 bits").
//
// M1 only used i2s_mic_last_rms() / _peak() for the snap-test. M2+ uses
// i2s_mic_capture() to drain a fixed-length recording from the ring buffer.

#pragma once

#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

#define I2S_MIC_SAMPLE_RATE   16000
#define I2S_MIC_FRAME_SAMPLES 1280     // 80 ms @ 16 kHz; matches openWakeWord's preferred frame.

// Bring up I²S0 and start the audio_in_task. Idempotent.
esp_err_t i2s_mic_init(void);

// Read the most recent frame's RMS (root-mean-square) of int16 samples.
// Returns 0 if no frame yet.
uint32_t i2s_mic_last_rms(void);

// Read the most recent frame's absolute peak (max |sample|).
uint32_t i2s_mic_last_peak(void);

// Drain `n_samples` int16 samples from the mic ring buffer into `out`.
// Discards any stale audio at entry — the captured window starts NOW.
// Blocks until the buffer fills or `timeout_ms` elapses. Returns ESP_OK
// on a complete fill, ESP_ERR_TIMEOUT otherwise. Caller owns `out` (must
// hold n_samples * 2 bytes).
esp_err_t i2s_mic_capture(int16_t *out, size_t n_samples, uint32_t timeout_ms);

// Drop any audio sitting in the ring buffer. M3+ calls this just before
// starting a fresh recording so the captured window starts at "now"
// rather than including idle audio that piled up between recordings.
void i2s_mic_drain(void);

// Pull up to `max_samples` int16 samples from the ring buffer, blocking
// up to `timeout_ms` for the FIRST byte. Returns the number actually
// retrieved via `*out_samples` (may be less than `max_samples` if a
// short chunk was returned — caller is expected to call again for more).
// Used by VAD to peek at one frame at a time without committing to a
// fixed total length up front.
esp_err_t i2s_mic_capture_frame(int16_t *out, size_t max_samples,
                                size_t *out_samples, uint32_t timeout_ms);
