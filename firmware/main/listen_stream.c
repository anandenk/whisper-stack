// M4 wake-word listener. See listen_stream.h.

#include "listen_stream.h"
#include "i2s_mic.h"
#include "http_client.h"
#include "vad.h"
#include "app_state.h"
#include "wake_source.h"
#include "control_state.h"
#include "gesture.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "driver/gpio.h"
#include "sdkconfig.h"

#include "lwip/sockets.h"
#include "lwip/netdb.h"
#include "lwip/inet.h"
#include "cJSON.h"

#include "oled.h"

static const char *TAG = "listen";

#define BUTTON_GPIO            GPIO_NUM_0
// Preroll: how much pre-wake audio we keep around so the start of the
// user's question (which often starts while they're still saying "...jarvis"
// or just before the wake-word model fires) isn't lost. 512 ms wasn't
// enough — short lead-ins like "what is the X" got cut off when wake fired
// mid-"jarvis". 16384 samples (~1 s, 32 KB DRAM) comfortably covers a full
// "hey jarvis <question>" preamble and recovers the lead-in.
#define PREROLL_SAMPLES        16384     // ~1024 ms @ 16 kHz, 32 KB DRAM
#define LISTEN_FRAME_SAMPLES   1280      // 80 ms @ 16 kHz — matches openWakeWord's frame size
#define LISTEN_RESP_BUF_SIZE   1024      // server reply is ~50-100 bytes
#define BACKOFF_MS             1000      // pause before retrying after a /listen error

extern app_state_t s_state;  // defined in main.c

// Forward-declared in main.c. The preroll is passed as TWO segments
// because the source ring buffer wraps — segment 1 is start..end, segment
// 2 is the wrap-around 0..write_idx. Either segment may be NULL/0; non-
// voice wake sources (button, gesture) pass NULL/0 for both. Avoids the
// always-live 32 KB snapshot copy that previously sat alongside the
// preroll ring for no reason but to flatten it. wake_source records
// which input fired (VOICE/GESTURE/BUTTON) so the OLED splash and serial
// logs can distinguish paths.
extern void do_round_trip_streaming_with_preroll(const char *ip,
                                                 const int16_t *preroll_seg1,
                                                 size_t preroll_seg1_n,
                                                 const int16_t *preroll_seg2,
                                                 size_t preroll_seg2_n,
                                                 double wake_score,
                                                 wake_source_t wake_source);

// --- Pre-roll ring buffer -------------------------------------------------

typedef struct {
    int16_t *buf;
    size_t   capacity;
    size_t   write_idx;
    size_t   total;     // monotonic count of samples ever pushed
} preroll_t;

static esp_err_t preroll_init(preroll_t *p, size_t capacity_samples)
{
    p->buf = heap_caps_malloc(capacity_samples * sizeof(int16_t), MALLOC_CAP_8BIT);
    if (!p->buf) return ESP_ERR_NO_MEM;
    p->capacity = capacity_samples;
    p->write_idx = 0;
    p->total = 0;
    return ESP_OK;
}

static void preroll_push(preroll_t *p, const int16_t *src, size_t n)
{
    while (n > 0) {
        size_t room = p->capacity - p->write_idx;
        size_t copy = n < room ? n : room;
        memcpy(p->buf + p->write_idx, src, copy * sizeof(int16_t));
        p->write_idx = (p->write_idx + copy) % p->capacity;
        p->total += copy;
        src += copy;
        n -= copy;
    }
}

// Compute the two contiguous segments that together form the preroll
// in chronological order. Either may be empty. Caller streams seg1 first,
// then seg2. Avoids allocating a snapshot copy — the ring buffer's own
// memory is what we hand to the HTTP write.
//
// Lifetime: the segments are valid until preroll_push() is next called.
// During a turn the listen_stream task isn't pushing (it's running the
// round-trip in do_one_turn), so segments stay stable across the writes.
static void preroll_segments(const preroll_t *p,
                             const int16_t **seg1, size_t *seg1_n,
                             const int16_t **seg2, size_t *seg2_n)
{
    size_t avail = p->total < p->capacity ? p->total : p->capacity;
    size_t start = p->total < p->capacity ? 0 : p->write_idx;
    size_t first_cap = p->capacity - start;
    size_t a_n = avail < first_cap ? avail : first_cap;
    *seg1 = p->buf + start;
    *seg1_n = a_n;
    *seg2 = p->buf;
    *seg2_n = avail - a_n;
}

static void preroll_free(preroll_t *p)
{
    free(p->buf);
    p->buf = NULL;
}

// --- Socket helpers -------------------------------------------------------

static int connect_listen_socket(void)
{
    struct sockaddr_in srv = { 0 };
    srv.sin_family = AF_INET;
    srv.sin_port   = htons(CONFIG_WS_SERVER_PORT);
    srv.sin_addr.s_addr = inet_addr(CONFIG_WS_SERVER_HOST);

    int sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock < 0) {
        ESP_LOGE(TAG, "socket: errno=%d", errno);
        return -1;
    }
    if (connect(sock, (struct sockaddr *)&srv, sizeof srv) != 0) {
        ESP_LOGE(TAG, "connect %s:%d errno=%d", CONFIG_WS_SERVER_HOST, CONFIG_WS_SERVER_PORT, errno);
        close(sock);
        return -1;
    }

    // Aggressive TCP keepalive — lwIP's defaults (2 hours) are useless
    // for catching a dropped /listen connection. Get dead-AP detection
    // within ~45 s instead of forever.
    int yes = 1;
    setsockopt(sock, SOL_SOCKET, SO_KEEPALIVE, &yes, sizeof yes);
    int idle = 30, intvl = 5, cnt = 3;
    setsockopt(sock, IPPROTO_TCP, TCP_KEEPIDLE,  &idle,  sizeof idle);
    setsockopt(sock, IPPROTO_TCP, TCP_KEEPINTVL, &intvl, sizeof intvl);
    setsockopt(sock, IPPROTO_TCP, TCP_KEEPCNT,   &cnt,   sizeof cnt);

    // Recv timeout only — send blocks until the server drains, which
    // is what we want for sustained streaming. (We initially also set
    // SO_SNDTIMEO=5 s as defense in depth, but it caused periodic
    // EAGAIN every ~17 s when transient TCP backpressure was normal,
    // which dropped wake-word audio during reconnects. The server
    // now has its own FastFailHandler + lock-acquire timeout, so a
    // wedged server can't wedge us; we're safe to wait indefinitely
    // on send.)
    struct timeval tv = { .tv_sec = 5, .tv_usec = 0 };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);

    // SO_LINGER with l_linger=0: close() sends TCP RST and discards any
    // unACK'd data instead of queuing FIN behind it. Critical for the
    // failure-recovery path: when SO_SNDTIMEO fires, lwIP's tx buffer
    // has ~5 KB of unACK'd audio sitting in it. Without LINGER, FIN
    // queues behind that data, the server's request.stream.read keeps
    // blocking on a connection that's effectively dead, and wakeword.
    // _LOCK stays held for many seconds — every reconnect queues
    // behind the zombie. With LINGER=0, close() sends RST, server's
    // recv returns ECONNRESET on the next read, treats as
    // client_closed, releases the lock immediately. RST is appropriate
    // here because we have nothing to flush — the in-flight audio is
    // already old and the server doesn't care about it.
    struct linger lg = { .l_onoff = 1, .l_linger = 0 };
    setsockopt(sock, SOL_SOCKET, SO_LINGER, &lg, sizeof lg);

    return sock;
}

static int send_all(int sock, const void *buf, size_t len)
{
    const uint8_t *p = buf;
    size_t left = len;
    while (left > 0) {
        int n = send(sock, p, left, 0);
        if (n <= 0) return -1;
        p += n;
        left -= n;
    }
    return 0;
}

static int send_request_headers(int sock)
{
    // Build the HTTP request line + headers. Body is sent as separate
    // chunks via send_chunk() on each audio frame.
    char hdr[512];
    int n = snprintf(hdr, sizeof hdr,
        "POST /listen HTTP/1.1\r\n"
        "Host: %s:%d\r\n"
        "User-Agent: whisper-stack-esp32/0.1\r\n"
        "Content-Type: audio/l16; rate=16000; channels=1\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Connection: close\r\n"
        "%s%s%s"
        "\r\n",
        CONFIG_WS_SERVER_HOST, CONFIG_WS_SERVER_PORT,
        CONFIG_WS_API_KEY[0] ? "X-API-Key: " : "",
        CONFIG_WS_API_KEY[0] ? CONFIG_WS_API_KEY : "",
        CONFIG_WS_API_KEY[0] ? "\r\n" : ""
    );
    if (n < 0 || n >= (int)sizeof hdr) return -1;
    return send_all(sock, hdr, n);
}

static int send_chunk(int sock, const void *data, size_t n)
{
    char hdr[16];
    int hlen = snprintf(hdr, sizeof hdr, "%x\r\n", (unsigned)n);
    if (send_all(sock, hdr, hlen) != 0) return -1;
    if (n > 0 && send_all(sock, data, n) != 0) return -1;
    if (send_all(sock, "\r\n", 2) != 0) return -1;
    return 0;
}

// True if the socket has bytes ready to read (server has started replying).
static bool sock_has_response(int sock)
{
    fd_set rfds;
    FD_ZERO(&rfds);
    FD_SET(sock, &rfds);
    struct timeval tv = { 0, 0 };
    int n = select(sock + 1, &rfds, NULL, NULL, &tv);
    return n > 0 && FD_ISSET(sock, &rfds);
}

// Slurp the response until the server FINs (recv == 0) or buffer fills.
// Then locate the JSON body and parse the wake field. Returns true if
// `wake==true` in the response, false otherwise (logs the reason).
// On wake==true, also writes the openWakeWord score (0..1) to *score_out
// so the caller can display it on the OLED splash.
static bool read_and_parse_response(int sock, double *score_out)
{
    if (score_out) *score_out = 0.0;

    static char buf[LISTEN_RESP_BUF_SIZE];
    size_t total = 0;
    while (total < sizeof(buf) - 1) {
        int n = recv(sock, buf + total, sizeof(buf) - 1 - total, 0);
        if (n <= 0) break;
        total += n;
    }
    buf[total] = 0;
    if (total == 0) {
        ESP_LOGW(TAG, "empty response");
        return false;
    }

    // Status line
    int status = 0;
    sscanf(buf, "HTTP/1.%*d %d", &status);

    // Body starts after the first blank line. /listen replies are short
    // JSON (~80 B) — even if Werkzeug occasionally chunk-encodes them,
    // the JSON itself is contiguous text we can find by locating the
    // first "{". Robust to either encoding without a chunked decoder.
    char *body = strstr(buf, "\r\n\r\n");
    if (!body) {
        ESP_LOGW(TAG, "no header terminator; status=%d head:%.80s", status, buf);
        return false;
    }
    body += 4;

    char *json_start = strchr(body, '{');
    char *json_end   = strrchr(body, '}');
    if (!json_start || !json_end || json_end <= json_start) {
        ESP_LOGW(TAG, "no JSON in body; status=%d body head:%.80s", status, body);
        return false;
    }
    *(json_end + 1) = 0;

    cJSON *root = cJSON_Parse(json_start);
    if (!root) {
        ESP_LOGW(TAG, "JSON parse failed: %.80s", json_start);
        return false;
    }
    cJSON *wake   = cJSON_GetObjectItemCaseSensitive(root, "wake");
    cJSON *score  = cJSON_GetObjectItemCaseSensitive(root, "score");
    cJSON *reason = cJSON_GetObjectItemCaseSensitive(root, "reason");
    bool wake_b = cJSON_IsTrue(wake);
    if (wake_b) {
        double s = cJSON_IsNumber(score) ? score->valuedouble : 0.0;
        if (score_out) *score_out = s;
        ESP_LOGI(TAG, "WAKE fired (score=%.2f)", s);
    } else {
        ESP_LOGI(TAG, "no wake (reason=%s)",
                 cJSON_IsString(reason) ? reason->valuestring : "unknown");
    }
    cJSON_Delete(root);
    return wake_b;
}

// --- Main loop ------------------------------------------------------------

typedef enum {
    LISTEN_RESULT_WAKE,           // server fired wake=true (voice)
    LISTEN_RESULT_BUTTON,         // user pressed BOOT to short-circuit
    LISTEN_RESULT_GESTURE,        // M7 — HC-SR04 detected a wave
    LISTEN_RESULT_MUTED,          // M8 — control_state went muted; close socket and idle
    LISTEN_RESULT_RECONNECT,      // server replied wake=false (idle/closed) or transport error
} listen_result_t;

// Stream audio to /listen until something terminates the connection.
// On WAKE or BUTTON, the preroll is left populated for the caller to
// snapshot. On RECONNECT, the preroll is best-effort (caller will
// reset it on the next arm). On WAKE, *score_out gets the openWakeWord
// score (0..1); zeroed otherwise.
static listen_result_t run_listen_session(int sock, preroll_t *preroll,
                                          double *score_out)
{
    if (score_out) *score_out = 0.0;
    static int16_t frame[LISTEN_FRAME_SAMPLES];
    int prev_button_low = (gpio_get_level(BUTTON_GPIO) == 0) ? 1 : 0;
    uint32_t frames_sent = 0;
    int64_t  last_heartbeat_us = esp_timer_get_time();

    i2s_mic_drain();

    while (1) {
        // Falling-edge button detection — manual wake / fallback path.
        int now_low = (gpio_get_level(BUTTON_GPIO) == 0) ? 1 : 0;
        if (now_low && !prev_button_low) {
            ESP_LOGI(TAG, "button-triggered wake (manual)");
            return LISTEN_RESULT_BUTTON;
        }
        prev_button_low = now_low;

        // M7 — gesture polling task signals via a one-shot flag.
        if (gesture_check_and_clear()) {
            ESP_LOGI(TAG, "gesture-triggered wake (HC-SR04 wave)");
            return LISTEN_RESULT_GESTURE;
        }

        // M8 — if mute was requested, close the /listen socket cleanly
        // and let the outer loop handle drawing the muted screen.
        if (control_is_muted_now()) {
            ESP_LOGI(TAG, "muted — closing /listen, will idle until unmuted");
            return LISTEN_RESULT_MUTED;
        }

        size_t got = 0;
        esp_err_t err = i2s_mic_capture_frame(frame, LISTEN_FRAME_SAMPLES, &got, 100);
        if (err == ESP_OK && got > 0) {
            preroll_push(preroll, frame, got);
            if (send_chunk(sock, frame, got * sizeof(int16_t)) != 0) {
                ESP_LOGW(TAG, "send_chunk failed (errno=%d) — reconnecting", errno);
                return LISTEN_RESULT_RECONNECT;
            }
            frames_sent++;
        }

        // Heartbeat every 30 s so we can spot hangs in the serial monitor.
        // Silent listen_stream looks identical to a stuck listen_stream
        // without this; it bit us during M4 bench-up.
        int64_t now_us = esp_timer_get_time();
        if (now_us - last_heartbeat_us >= 30LL * 1000 * 1000) {
            ESP_LOGI(TAG, "alive — %lu frames streamed (free heap %u B)",
                     (unsigned long)frames_sent,
                     (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT));
            last_heartbeat_us = now_us;
        }

        if (sock_has_response(sock)) {
            return read_and_parse_response(sock, score_out) ? LISTEN_RESULT_WAKE : LISTEN_RESULT_RECONNECT;
        }
    }
}

#if ENABLE_OLED
static void oled_listen_status(const char *label, const char *ip)
{
    oled_clear();
    oled_draw_text(0, "whisper-stack");
    oled_draw_text(2, label);
    oled_draw_text(3, "say 'hey jarvis'");
    if (ip) {
        char ip_line[OLED_CHARS_PER_LINE + 1];
        snprintf(ip_line, sizeof ip_line, "ip:%s", ip);
        oled_draw_text(7, ip_line);
    }
    oled_flush();
}
// M8 — IDLE-screen variant for the muted state. Replaces the wake-word
// hint with a MUTED indicator + (when snoozed) a "Xm left" countdown
// the listen_stream main loop redraws every second.
static void oled_muted_status(control_mute_state_t st, int mins_left, const char *ip)
{
    oled_clear();
    oled_draw_text(0, "whisper-stack");
    oled_draw_text(2, "*** MUTED ***");
    if (st == CONTROL_MUTE_SNOOZE) {
        char buf[OLED_CHARS_PER_LINE + 1];
        snprintf(buf, sizeof buf, "    %dm left", mins_left);
        oled_draw_text(3, buf);
    } else {
        oled_draw_text(3, "    press mute btn");
    }
    if (ip) {
        char ip_line[OLED_CHARS_PER_LINE + 1];
        snprintf(ip_line, sizeof ip_line, "ip:%s", ip);
        oled_draw_text(7, ip_line);
    }
    oled_flush();
}
// M8 — transient mute/snooze toggle splash, ~800 ms. Replaces the
// idle screen entirely so the user gets visible confirmation that the
// IR press registered (especially on toggles where the IDLE state
// before and after looks similar — e.g. INDEFINITE → SNOOZE).
static void oled_splash(const char *msg, const char *ip)
{
    oled_clear();
    oled_draw_text(0, "whisper-stack");
    oled_draw_text(3, msg ? msg : "");
    if (ip) {
        char ip_line[OLED_CHARS_PER_LINE + 1];
        snprintf(ip_line, sizeof ip_line, "ip:%s", ip);
        oled_draw_text(7, ip_line);
    }
    oled_flush();
}
#else
static inline void oled_listen_status(const char *l, const char *ip) { (void)l; (void)ip; }
static inline void oled_muted_status(control_mute_state_t st, int m, const char *ip) {
    (void)st; (void)m; (void)ip;
}
static inline void oled_splash(const char *msg, const char *ip) { (void)msg; (void)ip; }
#endif

static void listen_stream_task(void *arg)
{
    const char *ip = (const char *)arg;

    preroll_t preroll;
    if (preroll_init(&preroll, PREROLL_SAMPLES) != ESP_OK) {
        ESP_LOGE(TAG, "preroll alloc failed; task exiting");
        vTaskDelete(NULL);
        return;
    }
    // No snapshot buffer — preroll segments are streamed straight from
    // the ring memory. Was 32 KB always-live; eliminating it lets us
    // afford a bigger i2s_mic ring buffer (the actual fix to the
    // chunked-upload-backpressure audio corruption).

    while (1) {
        // Only stream while idle. M3 round-trip mutates s_state through
        // LISTENING/THINKING/SPEAKING and returns it to IDLE.
        if (s_state != APP_STATE_IDLE) {
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        // M8 — transient toggle splash takes priority over both the
        // muted screen and the live /listen path, so a mute/snooze
        // press during an active /listen session shows confirmation
        // the moment run_listen_session bails. Polled every 100 ms
        // while active so the message visibly holds for ~800 ms before
        // the underlying screen redraws.
        char splash_buf[40];
        if (control_get_active_splash(splash_buf, sizeof splash_buf)) {
            oled_splash(splash_buf, ip);
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        // M8 — when muted, idle the streaming entirely and update the
        // OLED with the snooze countdown once a second. Both wake
        // sources (voice via /listen, gesture via gesture.c) are
        // expected to remain quiet until mute clears. Gesture polling
        // already self-gates on control_is_muted_now(); we self-gate
        // /listen here.
        if (control_is_muted_now()) {
            // Also poll BOOT button while muted — even with mute on,
            // the manual wake should still work as an escape hatch
            // if e.g. the IR remote is lost. Falling-edge detection.
            static int prev_btn_low_muted = 0;
            int now_low = (gpio_get_level(BUTTON_GPIO) == 0) ? 1 : 0;
            if (now_low && !prev_btn_low_muted) {
                ESP_LOGI(TAG, "BUTTON pressed while muted — overriding to wake");
                prev_btn_low_muted = now_low;
                preroll.write_idx = 0; preroll.total = 0;
                do_round_trip_streaming_with_preroll(ip, NULL, 0, NULL, 0, 0.0, WAKE_BUTTON);
                continue;
            }
            prev_btn_low_muted = now_low;

            control_mute_state_t st = control_get_mute_state();
            int mins = control_snooze_minutes_left();
            oled_muted_status(st, mins, ip);
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        oled_listen_status("listening...", ip);

        // Reset preroll for a fresh session.
        preroll.write_idx = 0;
        preroll.total = 0;

        int sock = connect_listen_socket();
        if (sock < 0) {
            oled_listen_status("server unreachable", ip);
            vTaskDelay(pdMS_TO_TICKS(BACKOFF_MS));
            continue;
        }

        if (send_request_headers(sock) != 0) {
            ESP_LOGE(TAG, "send headers failed (errno=%d)", errno);
            close(sock);
            vTaskDelay(pdMS_TO_TICKS(BACKOFF_MS));
            continue;
        }

        double wake_score = 0.0;
        listen_result_t r = run_listen_session(sock, &preroll, &wake_score);
        close(sock);

        switch (r) {
            case LISTEN_RESULT_WAKE: {
                const int16_t *seg1, *seg2;
                size_t n1, n2;
                preroll_segments(&preroll, &seg1, &n1, &seg2, &n2);
                ESP_LOGI(TAG, "WAKE (voice) → round-trip with %u-sample preroll",
                         (unsigned)(n1 + n2));
                do_round_trip_streaming_with_preroll(ip, seg1, n1, seg2, n2,
                                                     wake_score, WAKE_VOICE);
                break;
            }
            case LISTEN_RESULT_BUTTON:
                ESP_LOGI(TAG, "BUTTON → round-trip without preroll");
                do_round_trip_streaming_with_preroll(ip, NULL, 0, NULL, 0, 0.0, WAKE_BUTTON);
                break;
            case LISTEN_RESULT_GESTURE:
                ESP_LOGI(TAG, "GESTURE → round-trip without preroll");
                do_round_trip_streaming_with_preroll(ip, NULL, 0, NULL, 0, 0.0, WAKE_GESTURE);
                break;
            case LISTEN_RESULT_MUTED:
                // Outer loop will handle the muted-screen redraw on
                // the next iteration; just back off briefly so we
                // don't spin reconnect-close cycles if mute clears
                // the same tick we tore the socket down.
                vTaskDelay(pdMS_TO_TICKS(100));
                break;
            case LISTEN_RESULT_RECONNECT:
                vTaskDelay(pdMS_TO_TICKS(BACKOFF_MS));
                break;
        }
    }
}

esp_err_t listen_stream_init(const char *ip)
{
    BaseType_t ok = xTaskCreate(listen_stream_task, "listen", 8192, (void *)ip, 4, NULL);
    return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
