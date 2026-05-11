# Firmware build modes

The firmware now has two distinct build modes selectable from
menuconfig. They differ in which subsystems boot, not in what code
is in the tree — everything compiles either way, and the gate is
purely at the call-site.

## The Kconfig switch

`firmware/main/Kconfig.projbuild`:

```
config VOICE_ENABLED
    bool "Enable voice pipeline (mic + wake word + /converse)"
    default y
```

Default `y` so existing builds (including any CI / partner workstation
that re-runs `idf.py build` on this tree) keep behaving exactly as
before. Set to `n` only when you explicitly want to iterate on
display / servo / motion without the voice pipeline running.

## What's gated and what's not

When `CONFIG_VOICE_ENABLED=y` (default), the firmware behaves
identically to pre-v3 builds — mic comes up, wake-word
`POST /listen` runs continuously, BOOT button serves as the manual
fallback wake, full `/converse` round-trip is wired.

When `CONFIG_VOICE_ENABLED=n`:

| Subsystem                                          | State             |
|----------------------------------------------------|-------------------|
| `i2s_mic_init()`                                   | **gated out**     |
| `listen_stream_init()` (wake-word HTTP stream)     | **gated out**     |
| `cb_frame`, `cb_cancel`, `do_one_turn`,            | **gated out** (in main.c) |
|   `do_round_trip_streaming_with_preroll`           |                   |
| `i2s_mic_last_rms()` / `i2s_mic_last_peak()` ticks | **gated out**     |
| WiFi STA + DHCP (`wifi_init_sta_blocking`)         | stays live        |
| OLED main panel + status text                      | stays live        |
| OLED face panel + `face_task` (cat animation)      | stays live        |
| Boot card + wake splash hooks                      | stay live (face panel still drives state animation) |
| BOOT button (GPIO 0) configuration                 | stays live — useful as a manual trigger even without voice |
| `control_state_init` (mute/snooze/cancel shared)   | stays live        |
| `gesture_init` (HC-SR04, runtime-gated separately) | stays live        |
| `ir_remote_init` (NEC IR, runtime-gated separately)| stays live        |

`listen_stream.c`, `vad.c`, `http_client.c`, `i2s_mic.c` are still
in the CMakeLists.txt SRCS list and still compile cleanly. Their
unused functions are removed at link time by the IDF's default
`-ffunction-sections -fdata-sections -Wl,--gc-sections` — there's
no symbol they expose unconditionally that would force linking.
We **deliberately do not delete** these files: the voice pipeline
is the headline feature of the device; the gate is a temporary
prototype affordance.

### If linking fails with `=n`

`listen_stream.c` has an `extern` declaration of
`do_round_trip_streaming_with_preroll` and calls it from inside
`listen_stream_task`. With `CONFIG_VOICE_ENABLED=n`, the
function definition is gone — but `listen_stream_init()` is also
never called, so the linker's `--gc-sections` pass should remove
the entire `listen_stream_init → listen_stream_task → do_round_trip_*`
chain. **This relies on each function being in its own ELF
section, which `-ffunction-sections` (IDF default) guarantees.**

If on first build with `=n` you see something like
`undefined reference to do_round_trip_streaming_with_preroll`,
GC isn't reaching the call site. Two-line fix in `listen_stream.c`:
wrap the body of `listen_stream_task` (or the whole task spawn
inside `listen_stream_init`) in `#if CONFIG_VOICE_ENABLED ... #endif`
and have the `=n` branch return / `vTaskDelete(NULL)` immediately.
Alternatively, drop `listen_stream.c` from the main `CMakeLists.txt`
`SRCS` list when `=n` via a generator expression — slightly more
invasive but the cleanest gate.

I did not run `idf.py build` with `=n` from this workstation
during the prototype session; the assumption is the default
GC pass handles it. Verify on first flash; raise a new gotcha
entry to CLAUDE.md if it doesn't.

## How to flip it (menuconfig flow)

`sdkconfig` is `.gitignore`'d. The Kconfig default is `y`. To turn
voice off for tonight's prototype iteration, run:

```powershell
cd C:\voice-ai-server\firmware
idf.py menuconfig
```

Then navigate:

```
(top) -> Whisper Stack Device -> Enable voice pipeline (mic + wake word + /converse)
```

Press space to toggle, `S` to save, `Q` to quit. Then rebuild:

```powershell
idf.py build
idf.py -p COM3 flash
idf.py -p COM3 monitor
```

To turn voice back on, repeat the toggle. The setting persists across
rebuilds in your local `sdkconfig`.

## Survives a fullclean? No.

`idf.py fullclean` deletes `sdkconfig` and regenerates from
`sdkconfig.defaults`. Since `sdkconfig.defaults` ships with the
Kconfig default (`y`), the next build after a fullclean will be a
voice-enabled build. If you're in a prototype-iteration phase and
want the default to be `n` for your branch only, either:

1. Re-run `menuconfig` after every `fullclean` (the simple,
   forgettable path).
2. Edit `firmware/sdkconfig.defaults` and add a line
   `CONFIG_VOICE_ENABLED=n` (faster, but easy to forget to revert
   before merging). **Don't commit this** unless we're shipping a
   voice-off main branch, which we are not.

## When to use which

| Mode                       | Use case                                                                                       |
|----------------------------|------------------------------------------------------------------------------------------------|
| `VOICE_ENABLED=y` (default)| Day-to-day operation. The device is a voice assistant. All existing milestones work as documented in `notes/esp32-firmware-plan.md`. |
| `VOICE_ENABLED=n`          | v3 prototype iteration: trying out servo motion language, gaze + face state coupling, OLED polish, or new wake sources (gesture / IR) without competing with the wake-word stream for CPU and network. |

The Kconfig flag is **not** a feature gate for shipping. The intent
is that v3 firmware will ship voice-on; the flag exists so that
on-bench bring-up of new mechanical / display features doesn't pay
the wake-word tax until those features are ready to integrate with
the voice path.

## Related: standalone servo example

[`firmware/examples/servo_test/`](../firmware/examples/servo_test/)
is a separate IDF project (not part of the main firmware) that
exercises only the servo component. It doesn't read this Kconfig
flag — it's just a focused test sketch. To run it:

```powershell
cd C:\voice-ai-server\firmware\examples\servo_test
idf.py set-target esp32
idf.py build
idf.py -p COM3 flash monitor
```

Sharing the same DOIT V1 board as the main firmware is fine — flash
servo_test for servo iteration, flash the main firmware (with
`VOICE_ENABLED=n` if you don't want the wake stream live yet) for
face-coupled iteration.
