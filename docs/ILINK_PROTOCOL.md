# iLink BLE Lamp Protocol — Reverse-Engineering Notes

Derived from `btsnoop_hci_202607041622.cfa` (your capture) cross-checked against the
existing `ilink_light` custom component and against strings pulled from the iLink APK
(`classes.dex` references `com.jieli.bluetooth.rcsp` — this is Zhuhai Jieli Technology's
"RCSP" BLE protocol, a common Chinese BLE-audio/lighting SoC stack, which explains the
checksum style and framing).

## Transport

- Service UUID: `0000a032-0000-1000-8000-00805f9b34fb`
- Write characteristic (commands **to** the lamp): `0000a040-0000-1000-8000-00805f9b34fb` (value handle `0x000c` in this capture)
- The official app does **not** rely on notifications. After every write it does a **GATT
  Read Request** on `0000a041-...` (value handle `0x000e`) to fetch the latest reply.
  Your existing `light_bt_client.py` instead subscribes to notifications on
  `0000a042-...` — that also works (the lamp appears to push the same payload both ways),
  so no change needed there, just noting the app itself doesn't use it.
- Every frame (both directions) has the same envelope:

  ```
  55 AA | TYPE | CMD_HI CMD_LO | DATA...  | CRC
  ```

  - `TYPE` on writes is `01` (standard/white commands), `03` (RGB-mode commands), `00`
    or `02` for a few plain get/set commands (see below).
  - `CMD_HI/CMD_LO` select the function.
  - `CRC` = `(0xFF - sum(all preceding bytes)) & 0xFF` (this matches `Commands._crc` in
    your existing code — confirmed against every frame in the capture, no exceptions).
  - On responses, `TYPE` is re-used as a **payload length byte**, and the command echoes
    back as `CMD_HI | 0x80, CMD_LO`. E.g. request `08 15` → response cmd `88 15`.

## Already-known commands (confirmed still correct)

| Feature | Frame | Notes |
|---|---|---|
| Turn on | `55aa 01 0805 01` | matches `Commands.on()` |
| Turn off | `55aa 01 0805 00` | matches `Commands.off()` |
| Brightness (0-255) | `55aa 01 0801 {val}` | matches `Commands.brightness()` |
| RGB | `55aa 03 0802 {r}{g}{b}` | matches `Commands.rgb()`, confirmed by the "shake" traffic |
| White temp preset (1-5) | `55aa 01 0809 {level}` | matches `Commands.white_temp()`, confirmed by "click 5 temp presets" |
| Status query | `55aa 01 0815 06` | matches `Commands.status()` |
| Status response | `55aa 09 8815 {r}{g}{b}{tcode:2}{brightness}{on}{tlvl:2}` | matches `Response.parse_status()` |

## ⚠️ Correction: Scene selection command is different from what's in the repo

Your `commands.py` implements `Commands.scene()` as `55aa 03 0e20 {id}ff32`. **That frame
never appears anywhere in the capture.** When you actually tapped 5 different scenes in
the app, it sent this instead:

```
55aa 01 0806 {scene_id}      (1-byte scene id, 01-93, decimal, no 0xff/0x32 suffix)
```

Observed: `55aa01080601f0` (scene 1 - Rainbow), `...0606eb` (scene 6 - Blue),
`...0607ea` (scene 7 - Alarm), `...0609e8` (scene 9 - Breathing),
`...060ce5` (scene 12 - Rainbow Double way).

The current app version (3.0.28) clearly uses `0806`, not `0e20`. I'd recommend switching
`Commands.scene()` over to this (see updated `commands.py` below) — `0e20` may be dead
code from an earlier firmware revision, or a different product using the same app.

## Newly discovered commands

### Continuous white-temp slider (not the 5 presets)
```
55aa 01 0807 {val}     val 0x00 (coolest/6000K) .. 0xff (warmest/3000K)
```
This is what actually fires while you *drag* the Cool↔Warm slider; the 5 presets
(`0809`) are separate quick-select buttons. Worth exposing as a smoother `color_temp`
control instead of/alongside the 5-level enum.

### "Dim → Bright" slider
```
55aa 01 0808 {val}     val 0x00 .. 0xff
```
Sent while dragging that second slider. I can't tell from traffic alone what it controls
device-side (values climbed smoothly 0x00→0xff same as the brightness slider did), but it
is clearly **not** the same register as `0801` (brightness) or `0807` (temp) — it's its
own thing, most likely a "minimum brightness floor" or a soft-start/curve control. Worth
testing empirically (set it to 0 vs 0xff and see whether normal on/brightness behavior
changes).

### Shake-to-random-color
Not a lamp→phone event — it's the **phone's own accelerometer**. Shaking the phone just
makes the app pick a random RGB value and send it with the normal RGB command (`0802`).
Three shakes → three `55aa0308 02{random rgb}` frames. No dedicated "shake" opcode exists
on the wire.

### Music-reactive mode
"Play music" did **not** send any special "enter music mode" command either — the app
just starts streaming live RGB values via the same `0802` command as fast as the mic
analysis produces them (11 frames captured, ~6-12ms apart). So this is purely a
client-side effect done via rapid plain RGB commands, not a lamp-side mode.
(Note: `scenes.py` already lists 6 scene IDs — 88 through 93 — literally named
"...Music Open Way"/"...Music close Way"; those are a *different*, lamp-native
microphone-reactive mode you select via the `0806` scene command, separate from what
this particular app screen does.)

### Auto-light schedule ("dim to bright" day/night schedule)
```
Query:        55aa 01 0523 06   → 55aa 05 8523 {enabled}{open_h}{open_m}{close_h}{close_m} {crc}
Set open time:  55aa 02 0514 {hour}{min}
Set close time: 55aa 02 0515 {hour}{min}
```
Hour/minute are plain binary (not BCD): `0x0a 0x00` = 10:00, `0x0a 0x1e` = 10:30.
Confirmed: setting open/close to 10:00/10:30 in the app produced exactly
`55aa0205140a00db` and `55aa0205150a1ebc`, and the next status query for `0523` came
back `010a000a1e` = enabled=1, 10:00–10:30. ✅

Two more frames (`0517`, `0516`, no data, type `00`) appeared right around the "turn on
auto light" tap, and a `0102` + data `03` frame appeared at "turn off auto light".
I could not 100% disambiguate which single byte is "the" on/off toggle for auto-light
vs. a second bundled write the app always fires alongside it — see the *Unresolved*
section below. Treat `0102`/`03` as most likely "auto-light disable" and `0517` as most
likely "auto-light enable", but please verify with a controlled single-action capture.

### Auto-music schedule (same structure, different registers)
```
Query:        55aa 01 0522 06   → 55aa 05 8522 {enabled}{open_h}{open_m}{close_h}{close_m} {crc}
Set open time:  55aa 02 0518 {hour}{min}
Set close time: 55aa 02 0519 {hour}{min}
```
Confirmed the same way: set to 11:00/11:30 → `55aa0205180b00d6` /
`55aa0205190b1eb7`, later query for `0522` returned `010b000b1e` = 11:00–11:30. ✅

`0521`/`0520` (no data, type `00`) appeared bundled around the "turn off/on auto music"
taps — same caveat as above about which is precisely on vs. off.

### Alarms (3 slots)
Each alarm slot follows a clean, evenly-spaced register block. Confirmed for slot 1,
inferred by pattern for slots 2/3 (not independently exercised in this capture beyond
on/off — please verify time-set and enable for slots 2/3 if you can):

| Slot | Query (10-byte reply) | Set time | Enable | Disable |
|---|---|---|---|---|
| Alarm 1 | `0504` | `0503 {hour}{min}` | `0505` | `0506` |
| Alarm 2 | `0508` | `0507*` | `0509*` | `050a` |
| Alarm 3 | `050c` | `050b*` | `050d*` | `050e` |

`*` = inferred from the arithmetic pattern (`base-1`=set time, `base+1`=enable,
`base+2`=disable), not directly observed — only the disable ops for slots 2/3 and the
enable op for slot 1 were exercised when you ran "turn off all three alarms, turn on
alarm 1".

Query reply payload (10 bytes): `d0 d1 d2 d3 d4 d5 hour min d8 enabled`. In this capture
`d0..d5` were always `00 14 10 01 01 01` and `d8` was always `00` — these look like
fixed template fields (maybe repeat-days / color / effect-id for a more advanced alarm
mode this app UI doesn't expose), unchanged across every test we ran. `hour`/`min` and
the final `enabled` byte are confirmed live: after off,off,off / on-alarm1 /
alarm1→9:00am, the query for alarm 1 came back `...09000001` (hour=9, enabled=1) and
alarms 2 & 3 came back `...0000` at the very end (enabled=0), matching exactly what
you did. ✅

### Connection handshake
Every connect does, in order:
```
55aa 01 0b01 06      → reply: 2-byte payload, looks like a firmware-version pair (e.g. "2, 19")
55aa 00 0b0a         → reply: 4-byte payload, undecoded (device info / capability bitmask?)
55aa 01 0816 06      → reply: ack only, no payload — purpose unclear, sent once per connect right before the main status query
55aa 01 0815 06      → main status (documented above)
55aa 03 0501 10{xx}{yy}  → an RGB-type query the app also fires once per connect/resync; always got an empty reply in this capture
```
Then, after **every** setting change, the app re-syncs by re-querying:
`0501`, `0523`, `0522`, `0504`, `0508`, `050c`, `0404`, `0414` — the last two
(`0404`→1-byte payload, always `1e`=30; `0414`→5-byte payload, always `3232323232`=
`50,50,50,50,50`) never changed in any of our snapshots, so they're most likely static
device-capability/limit values rather than anything you can set from the app (they may
correspond to a "minimum brightness %" and "5 preset color-temp intensity" limits).

## Unresolved / needs a follow-up capture

To fully pin down these with certainty, it would help to capture **one isolated action at
a time** (toggle only auto-light on, disconnect/reconnect, toggle only auto-light off,
etc.) rather than the whole sequence in one session:

1. Exact on/off polarity for auto-light (candidates: `0516`, `0517`, `0102`/`03`).
2. Exact on/off polarity for auto-music (candidates: `0520`, `0521`).
3. Whether `0102`/`03` is actually a toggle at all, vs. an app-lifecycle heartbeat (it
   also appeared once right when you closed/reopened the app, with no obvious setting
   change nearby).
4. Confirm alarm 2/3's set-time and enable opcodes match the `050b`,`0507`,`0509`,`050d`
   guesses above.
5. What `0808` ("dim to bright" slider) actually changes on the lamp.
