// Shared mute/snooze/cancel state — see header.

#include "control_state.h"
#include "app_state.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_timer.h"
#include "esp_log.h"

static const char *TAG = "control";

#define SPLASH_HOLD_US     (800LL * 1000)   // 800 ms
#define SPLASH_BUF_LEN     32

static SemaphoreHandle_t s_mu;
static control_mute_state_t s_mute = CONTROL_MUTE_NONE;
static int64_t              s_snooze_deadline_us = 0;
static bool                 s_cancel_flag = false;
static char                 s_splash[SPLASH_BUF_LEN] = "";
static int64_t              s_splash_deadline_us = 0;

extern app_state_t s_state;  // defined in main.c

esp_err_t control_state_init(void)
{
    if (s_mu) return ESP_OK;
    s_mu = xSemaphoreCreateMutex();
    return s_mu ? ESP_OK : ESP_ERR_NO_MEM;
}

// Caller must hold s_mu. Demote SNOOZE → NONE if its deadline passed.
static void expire_snooze_locked(void)
{
    if (s_mute == CONTROL_MUTE_SNOOZE && esp_timer_get_time() >= s_snooze_deadline_us) {
        s_mute = CONTROL_MUTE_NONE;
        s_snooze_deadline_us = 0;
        ESP_LOGI(TAG, "snooze expired — wake sources resuming");
    }
}

bool control_is_muted_now(void)
{
    if (!s_mu) return false;
    bool muted;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    expire_snooze_locked();
    muted = (s_mute != CONTROL_MUTE_NONE);
    xSemaphoreGive(s_mu);
    return muted;
}

control_mute_state_t control_get_mute_state(void)
{
    if (!s_mu) return CONTROL_MUTE_NONE;
    control_mute_state_t r;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    expire_snooze_locked();
    r = s_mute;
    xSemaphoreGive(s_mu);
    return r;
}

int control_snooze_minutes_left(void)
{
    if (!s_mu) return 0;
    int mins = 0;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    expire_snooze_locked();
    if (s_mute == CONTROL_MUTE_SNOOZE) {
        int64_t left_us = s_snooze_deadline_us - esp_timer_get_time();
        if (left_us > 0) {
            // Round up — 29 min 6 s left should still show "30m" rather
            // than dropping to "29m" the moment the snooze starts.
            mins = (int)((left_us + 59999999LL) / 60000000LL);
        }
    }
    xSemaphoreGive(s_mu);
    return mins;
}

// Set the cancel flag if the device is mid-turn. Caller must hold s_mu.
// Used by mute/snooze transitions so a "shut up" press also kills the
// current LISTENING/THINKING/SPEAKING — matches the standard voice-
// assistant mute behavior. No-op if state is IDLE/BOOT (so muting while
// idle doesn't leave a stale flag for the next turn).
static void cancel_active_turn_locked(void)
{
    app_state_t st = s_state;
    if (st == APP_STATE_LISTENING ||
        st == APP_STATE_THINKING  ||
        st == APP_STATE_SPEAKING) {
        s_cancel_flag = true;
    }
}

void control_toggle_mute(void)
{
    if (!s_mu) return;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    expire_snooze_locked();
    bool now_muted = false;
    switch (s_mute) {
        case CONTROL_MUTE_NONE:
            s_mute = CONTROL_MUTE_INDEFINITE;
            now_muted = true;
            ESP_LOGI(TAG, "mute → INDEFINITE");
            break;
        case CONTROL_MUTE_INDEFINITE:
            s_mute = CONTROL_MUTE_NONE;
            s_snooze_deadline_us = 0;
            ESP_LOGI(TAG, "mute → NONE");
            break;
        case CONTROL_MUTE_SNOOZE:
            // Snoozing → mute toggle cancels the timer, switches to
            // indefinite. (User intent: "I want it to stay off, the
            // snooze countdown is no longer right.")
            s_mute = CONTROL_MUTE_INDEFINITE;
            s_snooze_deadline_us = 0;
            now_muted = true;
            ESP_LOGI(TAG, "mute → INDEFINITE (cancelled snooze)");
            break;
    }
    if (now_muted) cancel_active_turn_locked();
    xSemaphoreGive(s_mu);
}

void control_start_snooze(int duration_min)
{
    if (!s_mu) return;
    if (duration_min <= 0) duration_min = 1;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    s_mute = CONTROL_MUTE_SNOOZE;
    s_snooze_deadline_us = esp_timer_get_time() + (int64_t)duration_min * 60LL * 1000000LL;
    cancel_active_turn_locked();
    ESP_LOGI(TAG, "snooze → %d min", duration_min);
    xSemaphoreGive(s_mu);
}

void control_request_cancel(void)
{
    if (!s_mu) return;

    // Mid-turn cancel only — task spec is clear that cancel applies
    // during LISTENING / THINKING / SPEAKING. Without this guard a
    // press while IDLE would set a sticky flag, and the next wake-
    // triggered turn would see it the first time cb_cancel polled
    // and immediately bail out. Refuse the press silently otherwise.
    app_state_t st = s_state;
    if (st != APP_STATE_LISTENING &&
        st != APP_STATE_THINKING  &&
        st != APP_STATE_SPEAKING) {
        ESP_LOGI(TAG, "cancel ignored (not in a turn, state=%d)", (int)st);
        return;
    }

    xSemaphoreTake(s_mu, portMAX_DELAY);
    s_cancel_flag = true;
    xSemaphoreGive(s_mu);
    ESP_LOGI(TAG, "cancel requested");
}

bool control_check_and_clear_cancel(void)
{
    if (!s_mu) return false;
    bool c;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    c = s_cancel_flag;
    s_cancel_flag = false;
    xSemaphoreGive(s_mu);
    return c;
}

void control_clear_cancel(void)
{
    if (!s_mu) return;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    s_cancel_flag = false;
    xSemaphoreGive(s_mu);
}

void control_post_splash(const char *msg)
{
    if (!s_mu || !msg) return;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    strncpy(s_splash, msg, SPLASH_BUF_LEN - 1);
    s_splash[SPLASH_BUF_LEN - 1] = 0;
    s_splash_deadline_us = esp_timer_get_time() + SPLASH_HOLD_US;
    xSemaphoreGive(s_mu);
    ESP_LOGD(TAG, "splash: %s", msg);
}

bool control_get_active_splash(char *out, size_t out_size)
{
    if (!s_mu || !out || out_size == 0) return false;
    bool active = false;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    if (s_splash_deadline_us > 0 && esp_timer_get_time() < s_splash_deadline_us) {
        strncpy(out, s_splash, out_size - 1);
        out[out_size - 1] = 0;
        active = true;
    }
    xSemaphoreGive(s_mu);
    return active;
}
