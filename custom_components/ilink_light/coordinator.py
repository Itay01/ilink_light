"""Coordinator for the iLink lamp.

Design: the BLE connection is opened once and kept alive for as long as the entity is
loaded (see light_bt_client.py). Commands are written immediately with no
connect/disconnect wrapped around each one, and local state is updated optimistically
the moment a command is sent - the UI reflects your intent instantly rather than
waiting on a round trip to the lamp and back.

A slow background poll (interval configurable, `CONF_SCAN_INTERVAL`) periodically
reads the lamp's actual status, purely to catch changes made outside Home Assistant
(e.g. the physical remote or the official app) - it is not on the path of any command
you send from HA, so it can never make a button/slider feel laggy.
"""

import datetime as dt
from enum import StrEnum

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
)
from homeassistant.helpers import device_registry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .commands import Commands, LampStatus
from .const import CONF_MAC, CONF_NAME, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, LOGGER
from .light_bt_client import LightBtClient


class LightState(StrEnum):
    COLORTEMP = ATTR_COLOR_TEMP_KELVIN
    RGB = ATTR_RGB_COLOR
    BRIGHTNESS = ATTR_BRIGHTNESS
    POWER = "power"


class LightCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, device_id, conf):
        self.device_id = device_id
        self.device_name = conf[CONF_NAME]
        self.address = conf[CONF_MAC]
        self._initialized = False

        poll_interval = int(conf.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            LOGGER,
            name=f"iLink Light: {self.device_name}",
            update_interval=dt.timedelta(seconds=poll_interval),
            update_method=self._background_poll,
        )

        self._client = LightBtClient(hass, self.address, self._status_updated)

        # "Last commanded" values - set only when *you* change something via HA, never
        # touched by the background poll. Used to restore the lamp's color/brightness
        # when turning it back on (see the POWER case below for why this needs to be
        # separate from self.data: self.data gets overwritten by real hardware reads,
        # and if the poll happens to run while the lamp is off and the lamp reports
        # something like brightness=0 while off, we'd otherwise resend that instead of
        # what you actually had set).
        self._last_color_mode: LightState = LightState.COLORTEMP
        self._last_kelvin = 4000
        self._last_rgb = (0xFF, 0xFF, 0xFF)
        self._last_brightness = 255

        # Sensible defaults until the first real read comes back.
        self.data = {
            LightState.COLORTEMP: 4000,
            LightState.BRIGHTNESS: 255,
            LightState.POWER: True,
            LightState.RGB: (0xFF, 0xFF, 0xFF),
        }

    @property
    def state(self) -> dict:
        return self.data

    async def _status_updated(self, status: LampStatus) -> None:
        """Called from the background poll (or any explicit status request) with a
        freshly-read hardware status. Only overwrites brightness/power/rgb - color
        temp is intentionally left alone here, see commands.py for why."""
        self.data[LightState.BRIGHTNESS] = status.brightness
        self.data[LightState.POWER] = status.on
        self.data[LightState.RGB] = status.rgb
        self.async_set_updated_data(self.data)

    async def _background_poll(self):
        if not self._initialized:
            await self._initialize()
        try:
            await self._client.request_status()
        except Exception as e:
            LOGGER.debug("%s: background status poll failed: %s", self.address, e)
        return self.data

    async def _initialize(self):
        try:
            connected = await self._client.ensure_connected()
            if connected and self._client.service_info is not None:
                self._initialized = True
                reg = device_registry.async_get(self.hass)
                reg.async_update_device(
                    self.device_id,
                    name=self._client.service_info.name,
                    manufacturer=self._client.device_manufacturer,
                    hw_version=self._client.device_version,
                )
        except Exception as e:
            LOGGER.warning("%s: failed to initialize: %s", self.address, e)

    async def async_update_state(self, key: LightState, value) -> bool:
        """Send the command immediately and update local state optimistically -
        no connect/disconnect cycle, no waiting on a hardware read-back."""
        match key:
            case LightState.BRIGHTNESS:
                await self._client.set_brightness(int(value))
                self._last_brightness = int(value)
            case LightState.COLORTEMP:
                kelvin = int(value)
                await self._client.set_white_temp_slider(Commands.kelvin_to_slider(kelvin))
                value = kelvin
                self._last_color_mode = LightState.COLORTEMP
                self._last_kelvin = kelvin
            case LightState.RGB:
                await self._client.set_rgb(*value)
                self._last_color_mode = LightState.RGB
                self._last_rgb = tuple(value)
            case LightState.POWER:
                if value:
                    await self._client.turn_on()
                    # The lamp doesn't seem to remember its last color/brightness
                    # across a power cycle on its own (confirmed by testing - turning
                    # it back on with just the plain "on" command comes back at some
                    # default rather than where you left it). Re-send whatever we last
                    # explicitly commanded (not self.data - see __init__ for why) so
                    # "on" actually restores your last color and brightness.
                    if self._last_color_mode == LightState.RGB:
                        await self._client.set_rgb(*self._last_rgb)
                    else:
                        await self._client.set_white_temp_slider(
                            Commands.kelvin_to_slider(self._last_kelvin)
                        )
                    await self._client.set_brightness(self._last_brightness)
                else:
                    await self._client.turn_off()
            case "preset":
                # optional: 1 (6000K cold) .. 5 (3000K warm)
                await self._client.set_white_temp_preset(int(value))
                key = LightState.COLORTEMP
                value = Commands.preset_to_kelvin(int(value))
                self._last_color_mode = LightState.COLORTEMP
                self._last_kelvin = value
            case _:
                return False

        self.data[key] = value
        self.async_set_updated_data(self.data)
        return True

    async def async_shutdown(self) -> None:
        await self._client.disconnect()
        await super().async_shutdown()
