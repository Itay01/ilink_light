"""iLink lamp BLE protocol.

Confirmed byte-for-byte against a real btsnoop capture of the official app
(v3.0.28). See ILINK_PROTOCOL.md in the repo for how each frame was derived.

BLE layout:
- Service:        0000a032-0000-1000-8000-00805f9b34fb
- Write commands to:  0000a040-...  (CHARACTERISTIC_SEND_CMD)
- Read replies from:  0000a041-...  (CHARACTERISTIC_READ_STATUS) - this is what the
  official app does: write a command, then plain GATT-read the reply. There's also a
  notify characteristic (0000a042-...) but the app doesn't use it, so we don't rely on
  it either - a plain read after each write is simpler and is exactly what's confirmed
  to work.

Every frame: 55 AA <type> <cmd_hi> <cmd_lo> <data...> <crc>
  crc = (0xFF - sum(all preceding bytes)) & 0xFF
Replies echo the command as (cmd_hi | 0x80, cmd_lo), and the `type` byte is re-used to
mean "payload length in bytes".
"""

SERVICE_UUID = "0000a032-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_SEND_CMD = "0000a040-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_READ_STATUS = "0000a041-0000-1000-8000-00805f9b34fb"

# White-temp preset levels (only relevant if you use white_temp_preset()).
COLOR_TEMP_MIN_KELVIN = 3000
COLOR_TEMP_MAX_KELVIN = 6000

_PRESET_KELVIN = {
    1: 6000,  # cold white
    2: 5000,  # nature light
    3: 4000,  # sun light
    4: 3500,  # sun set
    5: 3000,  # candle light
}

# Color-temp codes the lamp reports back in its status blob, but only for the 5
# preset levels above - it was never captured returning a code for an in-between
# continuous slider position, so we can't reliably map every possible code back to a
# kelvin value. Status parsing below intentionally does NOT report color temp for this
# reason; the coordinator tracks the last commanded kelvin locally instead (optimistic
# state), which is both simpler and more accurate for a continuous control anyway.
_PRESET_CODES = {
    "ff00": 1,
    "b464": 2,
    "ffff": 3,
    "4bc8": 4,
    "00ff": 5,
}


def _crc(data: bytes) -> int:
    return (0xFF - sum(data)) & 0xFF


def _frame(type_byte: int, cmd: bytes, data: bytes = b"") -> bytes:
    body = bytes([0x55, 0xAA, type_byte]) + cmd + data
    return body + bytes([_crc(body)])


class Commands:
    @staticmethod
    def on() -> bytes:
        return _frame(0x01, bytes([0x08, 0x05]), bytes([0x01]))

    @staticmethod
    def off() -> bytes:
        return _frame(0x01, bytes([0x08, 0x05]), bytes([0x00]))

    @staticmethod
    def brightness(value: int) -> bytes:
        """0-255"""
        value = max(0, min(0xFF, value))
        return _frame(0x01, bytes([0x08, 0x01]), bytes([value]))

    @staticmethod
    def rgb(r: int, g: int, b: int) -> bytes:
        """0-255 each"""
        r, g, b = (max(0, min(0xFF, v)) for v in (r, g, b))
        return _frame(0x03, bytes([0x08, 0x02]), bytes([r, g, b]))

    @staticmethod
    def white_temp_slider(value: int) -> bytes:
        """Continuous white-temp control.
        0x00 = coolest (6000K) .. 0xff = warmest (3000K)."""
        value = max(0, min(0xFF, value))
        return _frame(0x01, bytes([0x08, 0x07]), bytes([value]))

    @staticmethod
    def white_temp_preset(level: int) -> bytes:
        """1 (6000K cold) .. 5 (3000K warm) - the 5 quick-select presets."""
        level = max(1, min(5, level))
        return _frame(0x01, bytes([0x08, 0x09]), bytes([level]))

    @staticmethod
    def status() -> bytes:
        return _frame(0x01, bytes([0x08, 0x15]), bytes([0x06]))

    @staticmethod
    def kelvin_to_slider(kelvin: int) -> int:
        """Map a kelvin value (3000-6000) to the 0-255 continuous slider value."""
        kelvin = max(COLOR_TEMP_MIN_KELVIN, min(COLOR_TEMP_MAX_KELVIN, kelvin))
        span = COLOR_TEMP_MAX_KELVIN - COLOR_TEMP_MIN_KELVIN
        return round((COLOR_TEMP_MAX_KELVIN - kelvin) * 255 / span)

    @staticmethod
    def preset_to_kelvin(level: int) -> int:
        level = max(1, min(5, level))
        return _PRESET_KELVIN[level]


class LampStatus:
    """Parsed result of Commands.status().

    `preset_level` is only populated when the lamp happens to be sitting at one of the
    5 known preset color-temp codes; for any other (continuous-slider) position it's
    None. Callers should track the last kelvin they *commanded* for color temp instead
    of relying on this to reconstruct it exactly.
    """

    def __init__(self, on: bool, brightness: int, rgb: tuple[int, int, int], preset_level: int | None):
        self.on = on
        self.brightness = brightness
        self.rgb = rgb
        self.preset_level = preset_level


_STATUS_HEADER = bytes.fromhex("55aa098815")


def is_status_reply(data: bytes) -> bool:
    return bool(data) and data.startswith(_STATUS_HEADER)


def parse_status(data: bytes) -> LampStatus | None:
    if not is_status_reply(data) or len(data) < 14:
        return None
    r, g, b = data[5], data[6], data[7]
    temp_code = data[8:10].hex()
    brightness = data[10]
    on = data[11] == 1
    preset_level = _PRESET_CODES.get(temp_code)
    return LampStatus(on, brightness, (r, g, b), preset_level)
