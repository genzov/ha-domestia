"""Polls the Domestia controller."""

from dataclasses import dataclass
from datetime import timedelta
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MAX_BRIGHTNESS, DomestiaClient, DomestiaError, normalize_brightness
from .brightness import describe_state
from .const import (
    CONF_ALWAYS_ON,
    CONF_DIMMABLE,
    CONF_RELAY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_CONSECUTIVE_FAILURES,
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

    Each poll also enforces policy: always-on relays are turned back on at full
    brightness, and non-dimmable relays found dimmed are reset to full
    brightness. The data holds the enforced value for those relays.

    Relays the controller did not report are absent from the data, and raise
    the relay_not_reported repair issue.
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
        self._consecutive_failures = 0
        self._issue_id = f"relay_not_reported_{entry.entry_id}"

    @property
    def consecutive_failures(self) -> int:
        """Return the number of polls that failed in a row."""
        return self._consecutive_failures

    @override
    async def _async_update_data(self) -> dict[int, int]:
        """Poll the controller and enforce the lights' policy."""
        # The reply is complete once the highest configured relay has arrived.
        highest_relay = max(self.lights, default=1)
        try:
            state = await self.client.get_state(highest_relay)
        except DomestiaError as err:
            return self._handle_failure(err)
        self._consecutive_failures = 0

        self._update_missing_relays_issue(state)

        data: dict[int, int] = {}
        for relay, light in self.lights.items():
            if relay not in state:
                continue
            brightness = normalize_brightness(state[relay], dimmable=light.dimmable)
            data[relay] = await self._enforce_policy(light, brightness)

        previous = self.data or {}
        for relay, brightness in data.items():
            if previous.get(relay) != brightness:
                light = self.lights[relay]
                LOGGER.info(
                    "Light state changed: %s is %s",
                    light.name,
                    describe_state(brightness, dimmable=light.dimmable),
                )
        return data

    def _handle_failure(self, err: DomestiaError) -> dict[int, int]:
        """Keep the last data until the controller failed repeatedly.

        Every request uses a fresh connection, so single failures are usually
        transient. Without data from an earlier poll there is nothing to keep.
        """
        self._consecutive_failures += 1
        if self.data is None or self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        LOGGER.debug(
            "Failed to fetch controller state (attempt %s): %s",
            self._consecutive_failures,
            err,
        )
        return self.data

    async def _enforce_policy(self, light: LightConfig, brightness: int) -> int:
        """Return the brightness to report, correcting the relay if needed.

        Corrected relays report the enforced full brightness rather than the
        state that is being corrected. Errors are logged, and the next poll
        tries again.
        """
        relay = light.relay
        if light.always_on:
            if brightness == MAX_BRIGHTNESS:
                return brightness
            LOGGER.info("Turning always-on light %s back on", light.name)
            try:
                await self.client.turn_on(relay)
            except DomestiaError as err:
                LOGGER.warning(
                    "Failed to turn on always-on light %s: %s", light.name, err
                )
            try:
                await self.client.set_max_brightness(relay)
            except DomestiaError as err:
                LOGGER.warning(
                    "Failed to set always-on light %s to max brightness: %s",
                    light.name,
                    err,
                )
            return MAX_BRIGHTNESS

        if not light.dimmable and 0 < brightness < MAX_BRIGHTNESS:
            LOGGER.info(
                "Non-dimmable light %s dimmed to %s, resetting", light.name, brightness
            )
            try:
                await self.client.set_max_brightness(relay)
            except DomestiaError as err:
                LOGGER.warning(
                    "Failed to reset non-dimmable light %s: %s", light.name, err
                )
            return MAX_BRIGHTNESS

        return brightness

    def _update_missing_relays_issue(self, state: dict[int, int]) -> None:
        """Raise or clear the repair issue for configured relays not reported."""
        missing = sorted(relay for relay in self.lights if relay not in state)
        if not missing:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            config_entry_id=self.config_entry.entry_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="relay_not_reported",
            translation_placeholders={
                "host": self.client.host,
                "relays": ", ".join(str(relay) for relay in missing),
            },
        )
