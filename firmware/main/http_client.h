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
esp_err_t http_post_converse(const int16_t *pcm, size_t n_samples, converse_resp_t *resp);
