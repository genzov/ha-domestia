"""Light platform for Domestia relays."""

from collections.abc import Awaitable
from typing import Any, override

from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import ATTR_BRIGHTNESS, ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import DomestiaError
from .brightness import to_domestia, to_ha
from .const import DOMAIN
from .coordinator import DomestiaConfigEntry, DomestiaCoordinator, LightConfig
from .entity import DomestiaEntity

# The controller handles one request at a time.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: DomestiaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a light per configured relay, except always-on relays."""
    coordinator = entry.runtime_data
    for light in coordinator.lights.values():
        if light.always_on:
            continue
        async_add_entities(
            [DomestiaLight(coordinator, light)], config_subentry_id=light.subentry_id
        )


class DomestiaLight(DomestiaEntity, LightEntity):
    """A light on a Domestia relay."""

    # The light is its device's main feature, so it takes the device name.
    _attr_name = None

    def __init__(self, coordinator: DomestiaCoordinator, light: LightConfig) -> None:
        """Create the light."""
        super().__init__(coordinator, light)
        mode = ColorMode.BRIGHTNESS if light.dimmable else ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}

    @property
    def _raw_brightness(self) -> int:
        return self.coordinator.data.get(self.light.relay, 0)

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the light is on."""
        return self._raw_brightness > 0

    @property
    @override
    def brightness(self) -> int | None:
        """Return the brightness on Home Assistant's 0-255 scale."""
        if not self.light.dimmable:
            return None
        return to_ha(self._raw_brightness)

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, at the requested brightness if it is dimmable."""
        await self._async_command(self._async_turn_on(kwargs.get(ATTR_BRIGHTNESS)))

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._async_command(self.coordinator.client.turn_off(self.light.relay))

    async def _async_turn_on(self, brightness: int | None) -> None:
        client = self.coordinator.client
        relay = self.light.relay
        await client.turn_on(relay)
        if not self.light.dimmable:
            # A non-dimmable relay can be left dimmed; make sure it is fully on.
            await client.set_max_brightness(relay)
        elif brightness is not None:
            # A non-zero brightness must dim the light, not round down to 0
            # and switch it off.
            await client.set_brightness(relay, max(1, to_domestia(brightness)))

    async def _async_command(self, command: Awaitable[None]) -> None:
        """Run a command, then refresh the state whether or not it succeeded."""
        try:
            await command
        except DomestiaError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"name": self.light.name, "error": str(err)},
            ) from err
        finally:
            await self.coordinator.async_request_refresh()
