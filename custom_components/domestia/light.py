"""Light platform for Domestia relays."""

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, override

from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import ATTR_BRIGHTNESS, ColorMode
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import DomestiaError
from .brightness import to_domestia, to_ha
from .const import DOMAIN
from .coordinator import DomestiaConfigEntry, DomestiaCoordinator, LightConfig
from .entity import DomestiaEntity

# The controller handles one request at a time.
PARALLEL_UPDATES = 1

type _Command = Callable[[], Awaitable[None]]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DomestiaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a light per configured relay, except always-on relays."""
    coordinator = entry.runtime_data
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    for light in coordinator.lights.values():
        if light.always_on:
            # A light switched to always-on loses its entity and device, which
            # would otherwise linger as unavailable and could not be deleted.
            if entity_id := entity_registry.async_get_entity_id(
                Platform.LIGHT, DOMAIN, light.subentry_id
            ):
                entity_registry.async_remove(entity_id)
            if device := device_registry.async_get_device_by_identifier(
                (DOMAIN, light.subentry_id), entry.entry_id
            ):
                device_registry.async_remove_device(device.id)
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
        client = self.coordinator.client
        relay = self.light.relay
        commands: list[_Command] = [partial(client.turn_on, relay)]
        # The controller rejects brightness commands for on/off relays with
        # "NOK", so a non-dimmable light only gets the on command.
        if (
            self.light.dimmable
            and (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None
        ):
            # A non-zero brightness must dim the light, not round down to 0
            # and switch it off.
            commands.append(
                partial(client.set_brightness, relay, max(1, to_domestia(brightness)))
            )
        await self._async_commands(commands)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._async_commands(
            [partial(self.coordinator.client.turn_off, self.light.relay)]
        )

    async def _async_commands(self, commands: list[_Command]) -> None:
        """Send commands in order, then refresh the state.

        Every command is sent even if an earlier one failed: a brightness
        command also switches the relay on, so it can still bring the light to
        the requested state. The first failure is raised afterwards.
        """
        first_error: DomestiaError | None = None
        try:
            for command in commands:
                try:
                    await command()
                except DomestiaError as err:
                    first_error = first_error or err
        finally:
            await self.coordinator.async_request_refresh()
        if first_error is not None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={
                    "name": self.light.name,
                    "error": str(first_error),
                },
            ) from first_error
