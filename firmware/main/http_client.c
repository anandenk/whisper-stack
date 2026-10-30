// /converse client — see header.
//
// HISTORICAL / UNSUPPORTED (May 2026 Prototype):
// Communicates over unencrypted HTTP, transmitting audio streams and
// API keys in plaintext. Suitable for isolated LAN testing only; does
// not implement TLS encryption.

#include "http_client.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include "esp_log.h"
#include "esp_http_client.h"
#include "cJSON.h"
#include "sdkconfig.h"

static const char *TAG = "http";

// Body accumulator passed via user_data through the http event handler.
// Capacity is sized for the trimmed JSON we get from /converse with
// omit_audio_base64=1: typical replies are <2 KB. 8 KB headroom covers
// long transcripts + reply_text without risking heap pressure.
#define HTTP_RESP_CAP 8192

typedef struct {
    char    *buf;
    size_t   cap;
    size_t   len;
    bool     overflow;
} http_resp_acc_t;

static esp_err_t on_http_event(esp_http_client_event_t *e)
{
    if (e->event_id != HTTP_EVENT_ON_DATA) return ESP_OK;
    http_resp_acc_t *acc = (http_resp_acc_t *)e->user_data;
    if (!acc || !acc->buf) return ESP_OK;

    size_t room = (acc->len < acc->cap) ? (acc->cap - 1 - acc->len) : 0;
    size_t copy = (e->data_len < (int)room) ? (size_t)e->data_len : room;
    if (copy < (size_t)e->data_len) acc->overflow = true;
    if (copy > 0) {
        memcpy(acc->buf + acc->len, e->data, copy);
        acc->len += copy;
        acc->buf[acc->len] = '\0';
    }
    return ESP_OK;
}

static void copy_str(char *dst, size_t dst_sz, const char *src)
{
    if (!dst || dst_sz == 0) return;
    if (!src) { dst[0] = '\0'; return; }
    strncpy(dst, src, dst_sz - 1);
    dst[dst_sz - 1] = '\0';
}

// Forward decl — both http_post_converse (M2 buffered) and the M3 streaming
// finish() share this parser.
static esp_err_t parse_converse_json(const char *body, size_t body_len, converse_resp_t *resp);

esp_err_t http_post_converse(const int16_t *pcm, size_t n_samples, converse_resp_t *resp)
{
    if (!pcm || !resp || n_samples == 0) return ESP_ERR_INVALID_ARG;
    memset(resp, 0, sizeof *resp);

    char url[256];
    int url_n = snprintf(url, sizeof url,
                         "http://%s:%d/converse?omit_audio_base64=1",
                         CONFIG_WS_SERVER_HOST, CONFIG_WS_SERVER_PORT);
    if (CONFIG_WS_SESSION_ID[0] != '\0' && url_n > 0 && url_n < (int)sizeof url) {
        snprintf(url + url_n, sizeof url - url_n, "&session_id=%s", CONFIG_WS_SESSION_ID);
    }

    char *resp_buf = malloc(HTTP_RESP_CAP);
    if (!resp_buf) {
        ESP_LOGE(TAG, "out of memory for response buffer");
        return ESP_ERR_NO_MEM;
    }
    http_resp_acc_t acc = { .buf = resp_buf, .cap = HTTP_RESP_CAP, .len = 0, .overflow = false };

    esp_http_client_config_t cfg = {
        .url            = url,
        .method         = HTTP_METHOD_POST,
        .event_handler  = on_http_event,
        .user_data      = &acc,
        .timeout_ms     = 30000,            // /converse round-trip is normally 1–3s; pad for cold paths.
        .buffer_size    = 1024,
        .buffer_size_tx = 1024,
    };

    esp_http_client_handle_t cli = esp_http_client_init(&cfg);
    if (!cli) {
        ESP_LOGE(TAG, "esp_http_client_init failed");
        free(resp_buf);
        return ESP_FAIL;
    }

    esp_http_client_set_header(cli, "Content-Type", "audio/l16; rate=16000; channels=1");
    if (CONFIG_WS_API_KEY[0] != '\0') {
        esp_http_client_set_header(cli, "X-API-Key", CONFIG_WS_API_KEY);
    }
    esp_http_client_set_post_field(cli, (const char *)pcm, n_samples * sizeof(int16_t));

    ESP_LOGI(TAG, "POST %s (%u bytes)", url, (unsigned)(n_samples * sizeof(int16_t)));
    esp_err_t err = esp_http_client_perform(cli);

    int status = err == ESP_OK ? esp_http_client_get_status_code(cli) : 0;
    resp->http_status = status;
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "perform: %s", esp_err_to_name(err));
        esp_http_client_cleanup(cli);
        free(resp_buf);
        return err;
    }
    if (acc.overflow) ESP_LOGW(TAG, "response truncated at %d B (was bigger)", HTTP_RESP_CAP);
    ESP_LOGI(TAG, "status=%d body_len=%u", status, (unsigned)acc.len);

    if (status / 100 != 2) {
        ESP_LOGE(TAG, "non-2xx; body: %.*s",
                 (int)(acc.len > 200 ? 200 : acc.len), acc.buf);
        esp_http_client_cleanup(cli);
        free(resp_buf);
        return ESP_FAIL;
    }

    esp_err_t parse_err = parse_converse_json(acc.buf, acc.len, resp);
    esp_http_client_cleanup(cli);
    free(resp_buf);
    return parse_err;
}

// --- Streaming /converse upload (M3) --------------------------------------
//
// Pattern, hard-won from reading the ESP-IDF v5.3 source:
//   1. esp_http_client_open(client, -1) auto-injects the
//      `Transfer-Encoding: chunked` header (esp_http_client.c:1532) but
//      does NOT chunk-frame subsequent writes — esp_http_client_write()
//      dumps raw bytes straight to esp_transport_write().
//   2. So WE frame each chunk on the wire ourselves:
//        <hex_size>\r\n<data>\r\n
//      and the terminator at finish:
//        0\r\n\r\n
//   3. After the terminator: esp_http_client_fetch_headers() pulls the
//      response status + headers, then esp_http_client_read() drains
//      the body into our buffer.
//   4. esp_http_client_close + esp_http_client_cleanup tears down.
//
// We do NOT use the event_handler pattern here because fetch_headers /
// read give us synchronous control over response ingestion, which is
// cleaner alongside the manual chunked-write loop.

struct http_converse_stream_s {
    esp_http_client_handle_t cli;
    char                    *url;
    char                    *resp_buf;
    bool                     opened;
};

static esp_err_t parse_converse_json(const char *body, size_t body_len, converse_resp_t *resp)
{
    // Default the M6 fields to "absent" so callers can render placeholder
    // values when the server omitted a field (forward-compat with older
    // server builds).
    resp->route[0] = '\0';
    resp->model[0] = '\0';
    resp->auto_escalated = false;
    resp->stt_s = resp->llm_s = resp->tts_s = resp->total_s = 0.0f;
    resp->input_tokens  = -1;
    resp->output_tokens = -1;
    resp->estimated_cost_usd = 0.0f;

    cJSON *root = cJSON_ParseWithLength(body, body_len);
    if (!root) {
        ESP_LOGE(TAG, "cJSON_Parse failed; body head: %.*s",
                 (int)(body_len > 120 ? 120 : body_len), body);
        return ESP_FAIL;
    }
    const cJSON *t = cJSON_GetObjectItemCaseSensitive(root, "transcript");
    const cJSON *r = cJSON_GetObjectItemCaseSensitive(root, "reply_text");
    const cJSON *d = cJSON_GetObjectItemCaseSensitive(root, "audio_duration_s");
    if (cJSON_IsString(t)) copy_str(resp->transcript, sizeof resp->transcript, t->valuestring);
    if (cJSON_IsString(r)) copy_str(resp->reply_text, sizeof resp->reply_text, r->valuestring);
    if (cJSON_IsNumber(d)) resp->audio_duration_s = (float)d->valuedouble;

    const cJSON *route_node = cJSON_GetObjectItemCaseSensitive(root, "route");
    const cJSON *model_node = cJSON_GetObjectItemCaseSensitive(root, "model");
    const cJSON *esc_node   = cJSON_GetObjectItemCaseSensitive(root, "auto_escalated");
    if (cJSON_IsString(route_node)) copy_str(resp->route, sizeof resp->route, route_node->valuestring);
    if (cJSON_IsString(model_node)) copy_str(resp->model, sizeof resp->model, model_node->valuestring);
    if (cJSON_IsBool(esc_node))     resp->auto_escalated = cJSON_IsTrue(esc_node);

    const cJSON *timings = cJSON_GetObjectItemCaseSensitive(root, "timings");
    if (cJSON_IsObject(timings)) {
        const cJSON *stt = cJSON_GetObjectItemCaseSensitive(timings, "stt");
        const cJSON *llm = cJSON_GetObjectItemCaseSensitive(timings, "llm");
        const cJSON *tts = cJSON_GetObjectItemCaseSensitive(timings, "tts");
        const cJSON *tot = cJSON_GetObjectItemCaseSensitive(timings, "total");
        if (cJSON_IsNumber(stt)) resp->stt_s   = (float)stt->valuedouble;
        if (cJSON_IsNumber(llm)) resp->llm_s   = (float)llm->valuedouble;
        if (cJSON_IsNumber(tts)) resp->tts_s   = (float)tts->valuedouble;
        if (cJSON_IsNumber(tot)) resp->total_s = (float)tot->valuedouble;
    }

    const cJSON *tok = cJSON_GetObjectItemCaseSensitive(root, "llm_tokens");
    if (cJSON_IsObject(tok)) {
        const cJSON *in  = cJSON_GetObjectItemCaseSensitive(tok, "input");
        const cJSON *out = cJSON_GetObjectItemCaseSensitive(tok, "output");
        if (cJSON_IsNumber(in))  resp->input_tokens  = in->valueint;
        if (cJSON_IsNumber(out)) resp->output_tokens = out->valueint;
    }

    const cJSON *cost = cJSON_GetObjectItemCaseSensitive(root, "estimated_cost_usd");
    if (cJSON_IsNumber(cost)) resp->estimated_cost_usd = (float)cost->valuedouble;

    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t http_converse_stream_open(http_converse_stream_handle_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    *out = NULL;

    struct http_converse_stream_s *s = calloc(1, sizeof *s);
    if (!s) return ESP_ERR_NO_MEM;

    s->url = malloc(256);
    s->resp_buf = malloc(HTTP_RESP_CAP);
    if (!s->url || !s->resp_buf) {
        free(s->url); free(s->resp_buf); free(s);
        return ESP_ERR_NO_MEM;
    }
    int url_n = snprintf(s->url, 256, "http://%s:%d/converse?omit_audio_base64=1",
                         CONFIG_WS_SERVER_HOST, CONFIG_WS_SERVER_PORT);
    if (CONFIG_WS_SESSION_ID[0] != '\0' && url_n > 0 && url_n < 256) {
        snprintf(s->url + url_n, 256 - url_n, "&session_id=%s", CONFIG_WS_SESSION_ID);
    }

    esp_http_client_config_t cfg = {
        .url            = s->url,
        .method         = HTTP_METHOD_POST,
        .timeout_ms     = 30000,
        .buffer_size    = 1024,
        .buffer_size_tx = 1024,
    };
    s->cli = esp_http_client_init(&cfg);
    if (!s->cli) {
        free(s->url); free(s->resp_buf); free(s);
        return ESP_FAIL;
    }

    esp_http_client_set_header(s->cli, "Content-Type", "audio/l16; rate=16000; channels=1");
    if (CONFIG_WS_API_KEY[0] != '\0') {
        esp_http_client_set_header(s->cli, "X-API-Key", CONFIG_WS_API_KEY);
    }

    esp_err_t err = esp_http_client_open(s->cli, -1);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "stream open: %s", esp_err_to_name(err));
        esp_http_client_cleanup(s->cli);
        free(s->url); free(s->resp_buf); free(s);
        return err;
    }
    s->opened = true;

    ESP_LOGI(TAG, "stream open: %s (chunked)", s->url);
    *out = s;
    return ESP_OK;
}

// Frame and write one HTTP chunk: "<hex_size>\r\n<data>\r\n".
// Returns ESP_OK iff every byte made it onto the wire.
//
// Coalesced into a single esp_http_client_write so the small header (5 B)
// and trailer (2 B) don't get stuck behind Nagle/delayed-ACK between
// frames on weak-WiFi links. One frame = one TCP send instead of three.
// The static buffer is per-call-site (only one task uses this path at a
// time — listen_stream_task drives both /converse and /listen) so static
// is safe and avoids stack pressure.
static esp_err_t write_chunk(esp_http_client_handle_t cli, const void *data, size_t n)
{
    char hdr[16];
    int hdr_len = snprintf(hdr, sizeof hdr, "%x\r\n", (unsigned)n);
    if (hdr_len < 0 || hdr_len >= (int)sizeof hdr) return ESP_FAIL;

    // Sized for a 1280-sample int16 mono frame (2560 B) plus framing.
    // Falls back to multi-write if a future caller hands us more.
    static uint8_t buf[2600];
    size_t total = (size_t)hdr_len + n + 2;
    if (total <= sizeof buf) {
        memcpy(buf, hdr, hdr_len);
        if (n > 0) memcpy(buf + hdr_len, data, n);
        memcpy(buf + hdr_len + n, "\r\n", 2);
        if (esp_http_client_write(cli, (const char *)buf, (int)total) != (int)total) {
            return ESP_FAIL;
        }
        return ESP_OK;
    }

    // Oversized chunk — fall back to the original 3-write pattern.
    if (esp_http_client_write(cli, hdr, hdr_len) != hdr_len)             return ESP_FAIL;
    if (n > 0 &&
        esp_http_client_write(cli, (const char *)data, n) != (int)n)     return ESP_FAIL;
    if (esp_http_client_write(cli, "\r\n", 2) != 2)                       return ESP_FAIL;
    return ESP_OK;
}

esp_err_t http_converse_stream_write(http_converse_stream_handle_t s,
                                     const int16_t *pcm, size_t n_samples)
{
    if (!s || !s->opened || !pcm) return ESP_ERR_INVALID_STATE;
    if (n_samples == 0) return ESP_OK;
    esp_err_t err = write_chunk(s->cli, pcm, n_samples * sizeof(int16_t));
    if (err != ESP_OK) ESP_LOGE(TAG, "stream chunk write failed (n=%u)", (unsigned)n_samples);
    return err;
}

esp_err_t http_converse_stream_write_preroll(http_converse_stream_handle_t s,
                                             const int16_t *preroll, size_t n_samples)
{
    // Same wire shape — preroll is just audio, identical chunk encoding.
    // Lives as a separate function so future tuning (e.g. mark the
    // preroll boundary in metadata) has somewhere to land without
    // touching the per-frame hot path.
    return http_converse_stream_write(s, preroll, n_samples);
}

esp_err_t http_converse_stream_finish(http_converse_stream_handle_t s, converse_resp_t *resp)
{
    if (!s || !resp) return ESP_ERR_INVALID_ARG;
    memset(resp, 0, sizeof *resp);
    esp_err_t rc = ESP_OK;

    if (!s->opened) { rc = ESP_ERR_INVALID_STATE; goto cleanup; }

    // Chunked-transfer terminator: zero-length chunk + final CRLF.
    if (esp_http_client_write(s->cli, "0\r\n\r\n", 5) != 5) {
        ESP_LOGE(TAG, "stream terminator write failed");
        rc = ESP_FAIL;
        goto cleanup;
    }

    int64_t cl = esp_http_client_fetch_headers(s->cli);
    int status = esp_http_client_get_status_code(s->cli);
    resp->http_status = status;
    ESP_LOGI(TAG, "stream finish: status=%d content_length=%lld", status, (long long)cl);

    size_t body_len = 0;
    int chunk;
    while (body_len < HTTP_RESP_CAP - 1 &&
           (chunk = esp_http_client_read(s->cli, s->resp_buf + body_len, HTTP_RESP_CAP - 1 - body_len)) > 0) {
        body_len += chunk;
    }
    s->resp_buf[body_len] = '\0';

    if (status / 100 != 2) {
        ESP_LOGE(TAG, "stream non-2xx; body: %.*s",
                 (int)(body_len > 200 ? 200 : body_len), s->resp_buf);
        rc = ESP_FAIL;
        goto cleanup;
    }

    rc = parse_converse_json(s->resp_buf, body_len, resp);

cleanup:
    if (s->opened) esp_http_client_close(s->cli);
    esp_http_client_cleanup(s->cli);
    free(s->url); free(s->resp_buf); free(s);
    return rc;
}

void http_converse_stream_abort(http_converse_stream_handle_t s)
{
    if (!s) return;
    if (s->opened) esp_http_client_close(s->cli);
    esp_http_client_cleanup(s->cli);
    free(s->url); free(s->resp_buf); free(s);
}
