"""Light platform for Domestia relays."""

from typing import Any

from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .brightness import to_ha
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
    """A light on a Domestia relay.

    Phase 0 skeleton: on/off only. WP-D adds brightness commands, policy for
    non-dimmable relays and translated errors.
    """

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
    def is_on(self) -> bool:
        """Return whether the light is on."""
        return self._raw_brightness > 0

    @property
    def brightness(self) -> int | None:
        """Return the brightness on Home Assistant's 0-255 scale."""
        if not self.light.dimmable:
            return None
        return to_ha(self._raw_brightness)

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn the light on."""
        await self.coordinator.client.turn_on(self.light.relay)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn the light off."""
        await self.coordinator.client.turn_off(self.light.relay)
        await self.coordinator.async_request_refresh()
