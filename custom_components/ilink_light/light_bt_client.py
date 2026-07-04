"""BLE client for the iLink lamp.

Design goal: smoothness. The previous version of this integration connected fresh and
disconnected again around *every single command* - a full BLE connect (service
discovery etc.) commonly takes 1-3 seconds, so every brightness tick or slider drag
paid that cost. This version keeps one connection open for as long as the entity is
loaded, and only reconnects when the link actually drops.

Writes are serialized with a lock (mirrors a bug we hit and fixed in a standalone test
script: concurrent reads/writes on the same characteristic from two code paths at once
scramble each other's replies).
"""

import asyncio
from typing import Awaitable, Callable

from bleak import BleakClient, BLEDevice
from bleak.exc import BleakError
from home_assistant_bluetooth import BluetoothServiceInfoBleak

from homeassistant.components import bluetooth

from .commands import (
    CHARACTERISTIC_READ_STATUS,
    CHARACTERISTIC_SEND_CMD,
    Commands,
    LampStatus,
    parse_status,
)
from .const import LOGGER

# small settle delay between write and read-back, matches the timing the official app
# uses and is enough for the lamp to have the reply ready.
_READ_SETTLE_DELAY = 0.08

# how long to wait for a connection attempt before giving up and retrying
_CONNECT_TIMEOUT = 10


class LightBtClient:
    service_info: BluetoothServiceInfoBleak | None = None
    device_manufacturer: str | None = None
    device_version: str | None = None

    def __init__(
        self,
        hass,
        address: str,
        status_callback: Callable[[LampStatus], Awaitable[None]] | None = None,
    ):
        self._hass = hass
        self._address = address
        self._status_callback = status_callback
        self._bt_client: BleakClient | None = None
        self._ble_device: BLEDevice | None = None
        self._write_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._closing = False

    def is_connected(self) -> bool:
        return self._bt_client is not None and self._bt_client.is_connected

    @property
    def busy(self) -> bool:
        # kept for compatibility with callers that check this before making UI
        # decisions - with a persistent connection there's no meaningful "busy" state
        # to report, so this is always False.
        return False

    def _on_disconnect(self, _client: BleakClient) -> None:
        LOGGER.debug("%s: disconnected", self._address)
        # nothing to do here - the next command will call ensure_connected() and
        # reconnect automatically. We deliberately don't spin up our own reconnect
        # loop from inside this callback to avoid racing with an in-progress
        # ensure_connected() call from a command that's already running.

    async def ensure_connected(self, retries: int = 3) -> bool:
        if self.is_connected():
            return True

        async with self._connect_lock:
            # re-check now that we hold the lock - another caller may have already
            # connected while we were waiting for it.
            if self.is_connected():
                return True

            for attempt in range(1, retries + 1):
                try:
                    if self._ble_device is None:
                        self._ble_device = bluetooth.async_ble_device_from_address(
                            self._hass, self._address.upper(), connectable=True
                        )
                    if not self._ble_device:
                        raise BleakError(
                            f"A device with address {self._address} could not be found."
                        )

                    self._bt_client = BleakClient(
                        self._ble_device, disconnected_callback=self._on_disconnect
                    )
                    async with asyncio.timeout(_CONNECT_TIMEOUT):
                        await self._bt_client.connect()

                    LOGGER.debug("%s: connected", self._address)
                    self._read_device_info()
                    return True
                except Exception as e:
                    LOGGER.debug(
                        "%s: connect attempt %d/%d failed: %s",
                        self._address,
                        attempt,
                        retries,
                        e,
                    )
                    self._bt_client = None
                    if attempt < retries:
                        await asyncio.sleep(1)

            LOGGER.info("%s: unable to connect after %d attempts", self._address, retries)
            return False

    def _read_device_info(self) -> None:
        try:
            self.service_info = bluetooth.async_last_service_info(
                self._hass, self._address, connectable=True
            )
            if self.service_info and self.service_info.manufacturer_data:
                md = self.service_info.manufacturer_data
                if value := md.get(5101, None):
                    self.device_version = f"{value[0]}.{value[1]}.{value[2]}.{value[3]}"
                if value := md.get(1494, None):
                    self.device_manufacturer = value.decode("ascii") or None
        except Exception as e:
            LOGGER.debug("%s: couldn't read device info: %s", self._address, e)

    async def connect(self, retries: int = 3) -> bool:
        """Alias of ensure_connected(), kept for config_flow.py's one-off
        'verify this address works' check during device setup."""
        return await self.ensure_connected(retries=retries)

    async def disconnect(self, force: bool = False) -> None:
        self._closing = True
        if self._bt_client is not None:
            try:
                await self._bt_client.disconnect()
            except Exception as e:
                LOGGER.debug("%s: error disconnecting: %s", self._address, e)
        self._bt_client = None

    async def _send(self, data: bytes, read_reply: bool = False) -> bytes | None:
        last_error: Exception | None = None
        for attempt in (1, 2):
            if not await self.ensure_connected():
                raise ConnectionError(f"Not connected to {self._address}")

            try:
                async with self._write_lock:
                    await self._bt_client.write_gatt_char(
                        CHARACTERISTIC_SEND_CMD, data, response=True
                    )
                    if not read_reply:
                        return None
                    await asyncio.sleep(_READ_SETTLE_DELAY)
                    try:
                        return bytes(
                            await self._bt_client.read_gatt_char(
                                CHARACTERISTIC_READ_STATUS
                            )
                        )
                    except Exception as e:
                        LOGGER.debug("%s: status read failed: %s", self._address, e)
                        return None
            except Exception as e:
                last_error = e
                LOGGER.debug(
                    "%s: write failed (attempt %d), will retry once: %s",
                    self._address,
                    attempt,
                    e,
                )
                # the connection likely dropped mid-write - force a fresh connect on
                # the next loop iteration instead of assuming it's still good.
                self._bt_client = None

        raise ConnectionError(f"Failed to send command to {self._address}: {last_error}")

    # --- public API used by the coordinator/entity ---

    async def turn_on(self) -> None:
        await self._send(Commands.on())

    async def turn_off(self) -> None:
        await self._send(Commands.off())

    async def set_brightness(self, value: int) -> None:
        await self._send(Commands.brightness(value))

    async def set_rgb(self, r: int, g: int, b: int) -> None:
        await self._send(Commands.rgb(r, g, b))

    async def set_white_temp_slider(self, value: int) -> None:
        await self._send(Commands.white_temp_slider(value))

    async def set_white_temp_preset(self, level: int) -> None:
        await self._send(Commands.white_temp_preset(level))

    async def request_status(self) -> LampStatus | None:
        reply = await self._send(Commands.status(), read_reply=True)
        if reply is None:
            return None
        status = parse_status(reply)
        if status and self._status_callback:
            await self._status_callback(status)
        return status
