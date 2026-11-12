"""Polls the Domestia controller."""

from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DomestiaClient, DomestiaError, normalize_brightness
from .const import (
    CONF_ALWAYS_ON,
    CONF_DIMMABLE,
    CONF_RELAY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    REFRESH_COOLDOWN,
    SUBENTRY_TYPE_LIGHT,
)

type DomestiaConfigEntry = ConfigEntry[DomestiaCoordinator]


@dataclass(frozen=True, slots=True)
class LightConfig:
    """A light configured as a subentry."""

    subentry_id: str
    name: str
    relay: int
    dimmable: bool
    always_on: bool


class DomestiaCoordinator(DataUpdateCoordinator[dict[int, int]]):
    """Fetches normalized brightness (0-64) by relay for the configured lights.

    Phase 0 skeleton: poll and normalize only. WP-B adds the three-strike
    availability, policy enforcement and the relay_not_reported repair issue.
    """

    config_entry: DomestiaConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: DomestiaConfigEntry,
        client: DomestiaClient,
        controller_device_id: str,
    ) -> None:
        """Create a coordinator for the entry's controller and lights.

        controller_device_id is the device registry id of the controller
        device, which the light devices link to.
        """
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
            request_refresh_debouncer=Debouncer(
                hass, LOGGER, cooldown=REFRESH_COOLDOWN, immediate=True
            ),
        )
        self.client = client
        self.controller_device_id = controller_device_id
        self.lights: dict[int, LightConfig] = {
            subentry.data[CONF_RELAY]: LightConfig(
                subentry_id=subentry.subentry_id,
                name=subentry.title,
                relay=subentry.data[CONF_RELAY],
                dimmable=subentry.data.get(CONF_DIMMABLE, False),
                always_on=subentry.data.get(CONF_ALWAYS_ON, False),
            )
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_LIGHT
        }

    async def _async_update_data(self) -> dict[int, int]:
        # The reply is complete once the highest configured relay has arrived.
        highest_relay = max(self.lights, default=1)
        try:
            state = await self.client.get_state(highest_relay)
        except DomestiaError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

        return {
            relay: normalize_brightness(state[relay], dimmable=light.dimmable)
            for relay, light in self.lights.items()
            if relay in state
        }
