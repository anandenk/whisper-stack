// INMP441 I²S MEMS mic capture — 16 kHz mono int16 ring buffer.
//
// The driver runs a small audio_in_task that reads I²S0 frames in a loop
// and converts the 24-bit-MSB-aligned-in-32-bit-slot samples to int16 via
// `>> 16` (drops the 8 unused LSBs of the 32-bit slot AND the 8 LSBs of the
// 24-bit sample — that's what the plan calls "discard the bottom 8 bits").
//
// M1 only uses i2s_mic_read_rms() to log peak/RMS for the snap-test.
// M2/M3/M4 will pull pcm directly out of the ring buffer.

#pragma once

#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

#define I2S_MIC_SAMPLE_RATE   16000
#define I2S_MIC_FRAME_SAMPLES 1280     // 80 ms @ 16 kHz; matches openWakeWord's preferred frame.

// Bring up I²S0 and start the audio_in_task. Idempotent.
esp_err_t i2s_mic_init(void);

// Read the most recent frame's RMS (root-mean-square) of int16 samples.
// Returns 0 if no frame yet. Useful for the M1 verification step
// ("snap your fingers near the board, watch RMS spike").
uint32_t i2s_mic_last_rms(void);

// Read the most recent frame's absolute peak (max |sample|).
uint32_t i2s_mic_last_peak(void);
