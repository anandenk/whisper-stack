// /converse client — see header.

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

esp_err_t http_post_converse(const int16_t *pcm, size_t n_samples, converse_resp_t *resp)
{
    if (!pcm || !resp || n_samples == 0) return ESP_ERR_INVALID_ARG;
    memset(resp, 0, sizeof *resp);

    char url[160];
    snprintf(url, sizeof url,
             "http://%s:%d/converse?omit_audio_base64=1",
             CONFIG_WS_SERVER_HOST, CONFIG_WS_SERVER_PORT);

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

    cJSON *root = cJSON_ParseWithLength(acc.buf, acc.len);
    if (!root) {
        ESP_LOGE(TAG, "cJSON_Parse failed; body head: %.*s",
                 (int)(acc.len > 120 ? 120 : acc.len), acc.buf);
        esp_http_client_cleanup(cli);
        free(resp_buf);
        return ESP_FAIL;
    }

    const cJSON *t = cJSON_GetObjectItemCaseSensitive(root, "transcript");
    const cJSON *r = cJSON_GetObjectItemCaseSensitive(root, "reply_text");
    const cJSON *d = cJSON_GetObjectItemCaseSensitive(root, "audio_duration_s");

    if (cJSON_IsString(t)) copy_str(resp->transcript,  sizeof resp->transcript,  t->valuestring);
    if (cJSON_IsString(r)) copy_str(resp->reply_text,  sizeof resp->reply_text,  r->valuestring);
    if (cJSON_IsNumber(d)) resp->audio_duration_s = (float)d->valuedouble;

    cJSON_Delete(root);
    esp_http_client_cleanup(cli);
    free(resp_buf);
    return ESP_OK;
}
