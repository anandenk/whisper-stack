// HTTP client for the whisper-stack /converse endpoint.
//
// M2: POST raw int16 LE PCM @ 16 kHz mono to
//   http://<host>:<port>/converse?omit_audio_base64=1
// with X-API-Key auth, parse the small JSON reply, return the fields
// the OLED needs to display + the audio duration the workstation
// will play.
//
// `audio_base64` is omitted server-side via the query param so JSON
// stays under a few KB and parses cleanly on a chip with ~250 KB heap.

#pragma once

#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

#define CONVERSE_TRANSCRIPT_MAX 192
#define CONVERSE_REPLY_MAX      384

typedef struct {
    int   http_status;             // 200 on success
    char  transcript[CONVERSE_TRANSCRIPT_MAX];
    char  reply_text[CONVERSE_REPLY_MAX];
    float audio_duration_s;        // 0 if absent / not played
} converse_resp_t;

// POST `pcm` (n_samples int16) to /converse, fill `resp`.
// Returns ESP_OK on a 2xx response that parsed successfully. The HTTP
// status (or 0 on transport failure) is in resp->http_status either way.
//
// M2 buffered path: caller holds the entire recording in heap before
// calling. Simple but caps recording length at the heap budget (~96 KB
// = 3 sec for v1). Kept alongside the streaming API as a fallback in
// case the chunked-transfer path turns out to be flaky on real WiFi.
esp_err_t http_post_converse(const int16_t *pcm, size_t n_samples, converse_resp_t *resp);

// --- Streaming /converse upload (M3) --------------------------------------
//
// Open the connection, write int16 frames as they arrive from VAD, then
// finish to flush the request, parse the JSON response, and tear down.
// Removes the heap cap on recording length and prepares the same machinery
// M4 will reuse for /listen.
//
// Lifecycle:
//   http_converse_stream_open() -> handle
//   http_converse_stream_write(h, pcm, n) ... [as many times as needed]
//   http_converse_stream_finish(h, &resp) | http_converse_stream_abort(h)
//
// If finish() fails, abort() is NOT additionally needed — finish always
// tears the handle down. Likewise abort() is a teardown.

typedef struct http_converse_stream_s *http_converse_stream_handle_t;

esp_err_t http_converse_stream_open  (http_converse_stream_handle_t *out);
esp_err_t http_converse_stream_write (http_converse_stream_handle_t h,
                                      const int16_t *pcm, size_t n_samples);
esp_err_t http_converse_stream_finish(http_converse_stream_handle_t h,
                                      converse_resp_t *resp);
void      http_converse_stream_abort (http_converse_stream_handle_t h);
