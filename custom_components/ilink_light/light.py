from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityDescription,
    LightEntityFeature,
)
from homeassistant.const import CONF_DEVICES

from .commands import COLOR_TEMP_MAX_KELVIN, COLOR_TEMP_MIN_KELVIN
from .const import CONF_NAME, DOMAIN, LOGGER
from .coordinator import LightCoordinator, LightState
from .entity import iLinkLightBaseEntity

light_description = LightEntityDescription(
    key="light",
    name="Light",
)

# Optional: the 5 hardware white-temp presets, exposed as light effects. Purely a
# convenience shortcut to the same 5 fixed points the color-temp slider can already
# reach - remove PRESET_EFFECTS and the effect handling below if you don't want them.
PRESET_EFFECTS = {
    "Cold (6000K)": 1,
    "Nature (5000K)": 2,
    "Sunlight (4000K)": 3,
    "Sunset (3500K)": 4,
    "Candle (3000K)": 5,
}


async def async_setup_entry(hass, config_entry, async_add_entities):
    ha_entities = []

    for device_id in config_entry.data[CONF_DEVICES]:
        LOGGER.debug(
            "Starting iLink light: %s",
            config_entry.data[CONF_DEVICES][device_id][CONF_NAME],
        )
        coordinator = hass.data[DOMAIN][CONF_DEVICES][device_id]
        ha_entities.append(iLinkLightEntity(coordinator, light_description))

    async_add_entities(ha_entities, True)


class iLinkLightEntity(iLinkLightBaseEntity, LightEntity):
    min_color_temp_kelvin = COLOR_TEMP_MIN_KELVIN
    max_color_temp_kelvin = COLOR_TEMP_MAX_KELVIN

    _attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.RGB}
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = list(PRESET_EFFECTS.keys())
    _attr_effect = None

    def __init__(
        self, coordinator: LightCoordinator, description: LightEntityDescription
    ) -> None:
        super().__init__(coordinator, description)

    @property
    def brightness(self):
        return self.coordinator.state[LightState.BRIGHTNESS]

    @property
    def color_temp_kelvin(self) -> int | None:
        return self.coordinator.state[LightState.COLORTEMP]

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self.coordinator.state[LightState.RGB]

    @property
    def effect(self) -> str | None:
        return self._attr_effect

    @property
    def is_on(self) -> bool:
        return self.coordinator.state[LightState.POWER]

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self.is_on:
            self.coordinator.state[LightState.POWER] = True
            self.async_write_ha_state()
            await self.coordinator.async_update_state(LightState.POWER, True)

        if ATTR_EFFECT in kwargs and kwargs[ATTR_EFFECT] in PRESET_EFFECTS:
            await self.coordinator.async_update_state(
                "preset", PRESET_EFFECTS[kwargs[ATTR_EFFECT]]
            )
            self._attr_effect = kwargs[ATTR_EFFECT]
            self._attr_color_mode = ColorMode.COLOR_TEMP
        elif ATTR_COLOR_TEMP_KELVIN in kwargs:
            await self.coordinator.async_update_state(
                LightState.COLORTEMP, kwargs[ATTR_COLOR_TEMP_KELVIN]
            )
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_effect = None
        elif ATTR_RGB_COLOR in kwargs:
            await self.coordinator.async_update_state(
                LightState.RGB, kwargs[ATTR_RGB_COLOR]
            )
            self._attr_color_mode = ColorMode.RGB
            self._attr_effect = None

        if ATTR_BRIGHTNESS in kwargs:
            await self.coordinator.async_update_state(
                LightState.BRIGHTNESS, kwargs[ATTR_BRIGHTNESS]
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.is_on:
            self.coordinator.state[LightState.POWER] = False
            self.async_write_ha_state()
        await self.coordinator.async_update_state(LightState.POWER, False)
