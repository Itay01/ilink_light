# Changelog

## 2.0.0

Rewrite of the BLE layer and protocol implementation, based on reverse-engineering a
live btsnoop capture of the official iLink app (v3.0.28).

**Changed**
- Persistent BLE connection instead of connect/disconnect around every command
  (this was the main source of sluggishness before)
- Local state now updates optimistically the instant a command is sent, instead of
  waiting for a hardware status round-trip
- Color-temperature slider now drives the lamp's real continuous control (`0807`)
  instead of snapping to 5 preset points
- Corrected the scene-selection command (previous `55aa030e20{id}ff32` frame never
  appeared in a live capture; real command is `55aa0108 06{id}`) — moot in this
  release since scenes were dropped, but documented in `docs/ILINK_PROTOCOL.md` in
  case you want to re-add them

**Removed** (not carried over from upstream; see `docs/ILINK_PROTOCOL.md` for what's
known if you want to reimplement any of these)
- Scenes/effects list
- Auto-light / auto-music day-night schedules
- Alarms

## 1.0.0

Initial release (see [donandren/ilink_light](https://github.com/donandren/ilink_light)).
