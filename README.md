# ilink_light

A Home Assistant custom component for BLE lamps controlled by the **iLink** app
(commonly sold on AliExpress under names like "Smart RGB Table Lamp"). Reverse-engineered
from a live BLE capture of the official app — see [`docs/ILINK_PROTOCOL.md`](docs/ILINK_PROTOCOL.md)
for the full protocol writeup.

> **This is a fork** of the original [donandren/ilink_light](https://github.com/donandren/ilink_light)
> (MIT licensed). This fork rewrites the BLE layer for responsiveness and
> corrects/extends the reverse-engineered protocol; see
> [Changes from upstream](#changes-from-upstream) below.

## Features

- Bluetooth auto-discovery of nearby iLink lights, or manual add by MAC address
- On/off
- Brightness (0-255)
- RGB color
- Continuous color-temperature control (3000K-6000K), matching the real slider in the
  app rather than snapping to a few fixed points
- Optional white-temp presets (5 quick-select points), exposed as light effects

## Installation

### HACS (recommended)

1. HACS → Integrations → **⋮ → Custom repositories**
2. Add `https://github.com/Itay01/ilink_light`, category **Integration**
3. Find **iLink Light**, install, restart Home Assistant

### Manual

Copy `custom_components/ilink_light` into your Home Assistant `custom_components/`
directory and restart.

Then: **Settings → Devices & Services → Add Integration → iLink Light**.

## Changes from upstream

The biggest behavioral difference is responsiveness. The original integration opened a
fresh BLE connection and tore it down again around every single command — a full BLE
connect (including service discovery) commonly takes 1-3 seconds, so every brightness
tick or slider drag paid that cost. This fork keeps one BLE connection open for as long
as the integration is loaded and writes commands immediately, with local state updated
optimistically (the UI reflects your action right away instead of waiting on a
round-trip read from the lamp). A slow background poll still runs periodically, purely
to catch external changes (e.g. the physical remote), but it's off the path of anything
you do from Home Assistant.

The color-temperature slider now drives the lamp's actual continuous control (the same
one the app's slider uses) instead of snapping to the 5 preset points.

Also dropped in this fork: scenes, day/night auto-light and auto-music schedules, and
alarms. These were present upstream but not something this fork's protocol
investigation focused on completing - see `docs/ILINK_PROTOCOL.md` for what's known
about them if you want to pick that back up.

## Protocol notes

Every command was confirmed byte-for-byte against a real btsnoop capture of the
official app (not guessed from documentation, since there isn't any). Full writeup,
including confidence level per command and how each was derived, is in
[`docs/ILINK_PROTOCOL.md`](docs/ILINK_PROTOCOL.md).

## Credits

- Original integration: [donandren](https://github.com/donandren/ilink_light)
- This fork's BLE-connection rewrite and protocol corrections: [Itay Marom](https://github.com/Itay01)

## License

MIT, see [`LICENSE`](LICENSE).
