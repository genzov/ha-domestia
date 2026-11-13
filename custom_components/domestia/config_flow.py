"""Config flow for Domestia.

The user flow asks for the controller. Lights are config subentries, one per
relay, added afterwards. Reconfigure steps update the entry or subentry and
abort; the entry's update listener reloads it.
"""

from typing import Any, override

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .api import DomestiaClient, DomestiaError
from .const import (
    CONF_ALWAYS_ON,
    CONF_DIMMABLE,
    CONF_MODEL,
    CONF_RELAY,
    DOMAIN,
    MAX_RELAY,
    MIN_RELAY,
    MODELS,
    SUBENTRY_TYPE_LIGHT,
)

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): TextSelector(),
        probatio.Optional(CONF_MODEL): SelectSelector(
            SelectSelectorConfig(options=list(MODELS), mode=SelectSelectorMode.DROPDOWN)
        ),
    }
)

LIGHT_SCHEMA = probatio.Schema(
    {
        probatio.Optional(CONF_NAME): TextSelector(),
        probatio.Required(CONF_RELAY): NumberSelector(
            NumberSelectorConfig(
                min=MIN_RELAY, max=MAX_RELAY, step=1, mode=NumberSelectorMode.BOX
            )
        ),
        probatio.Optional(CONF_DIMMABLE, default=False): BooleanSelector(),
        probatio.Optional(CONF_ALWAYS_ON, default=False): BooleanSelector(),
    }
)


class InvalidLightError(Exception):
    """A light is invalid; translation_key names the form error."""

    def __init__(self, translation_key: str) -> None:
        """Create the error for a form error key."""
        super().__init__(translation_key)
        self.translation_key = translation_key


async def _can_connect(client: DomestiaClient) -> bool:
    """Return whether the client's controller answers a get-state request."""
    try:
        # Relay 1, not 0: a header-only reply is not a valid state.
        await client.get_state(1)
    except DomestiaError:
        return False
    return True


class DomestiaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Domestia controller."""

    VERSION = 1
    MINOR_VERSION = 1

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the light subentry flow."""
        return {SUBENTRY_TYPE_LIGHT: LightSubentryFlow}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the controller's address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()
            if await _can_connect(DomestiaClient(user_input[CONF_HOST])):
                return self.async_create_entry(
                    title=user_input[CONF_HOST], data=user_input
                )
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the controller's address or model."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            # The host is the unique id, so it cannot match this entry's own id.
            if any(
                other.unique_id == host and other.entry_id != entry.entry_id
                for other in self._async_current_entries()
            ):
                return self.async_abort(reason="already_configured")
            # The controller handles one request at a time: probe a running
            # entry's controller through its client, which serializes requests.
            client = (
                entry.runtime_data.client
                if entry.state is ConfigEntryState.LOADED
                and host == entry.data[CONF_HOST]
                else DomestiaClient(host)
            )
            if await _can_connect(client):
                self.hass.config_entries.async_update_entry(
                    entry, data=user_input, unique_id=host, title=host
                )
                # The entry's update listener reloads it.
                return self.async_abort(reason="reconfigure_successful")
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, user_input or entry.data
            ),
            errors=errors,
        )


def _light_from_input(user_input: dict[str, Any]) -> ConfigSubentryData:
    """Build a light subentry from form input; raise InvalidLightError if invalid."""
    relay = user_input[CONF_RELAY]
    # The selector bounds the relay but does not round it.
    if not float(relay).is_integer():
        raise InvalidLightError("invalid_relay")
    relay = int(relay)
    name = user_input.get(CONF_NAME, "").strip()
    always_on = user_input[CONF_ALWAYS_ON]
    # Always-on lights get no entity, so they do not need a name; a nameless
    # one is named after its relay.
    if not always_on and not name:
        raise InvalidLightError("name_required")
    return ConfigSubentryData(
        data={
            CONF_RELAY: relay,
            CONF_DIMMABLE: user_input[CONF_DIMMABLE],
            CONF_ALWAYS_ON: always_on,
        },
        subentry_type=SUBENTRY_TYPE_LIGHT,
        title=name or f"Relay {relay}",
        unique_id=str(relay),
    )


class LightSubentryFlow(ConfigSubentryFlow):
    """Add or change a light on one of the controller's relays."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a light."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                subentry = _light_from_input(user_input)
            except InvalidLightError as err:
                errors["base"] = err.translation_key
            else:
                # A relay that already has a light aborts with
                # already_configured: Home Assistant refuses a duplicate
                # subentry unique id.
                return self.async_create_entry(
                    title=subentry["title"],
                    data=subentry["data"],
                    unique_id=subentry["unique_id"],
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(LIGHT_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a light's name, relay or behaviour."""
        current = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                subentry = _light_from_input(user_input)
            except InvalidLightError as err:
                errors["base"] = err.translation_key
            else:
                # Moving to a relay that already has a light aborts with
                # already_configured: Home Assistant refuses a duplicate
                # subentry unique id.
                self.hass.config_entries.async_update_subentry(
                    self._get_entry(),
                    current,
                    title=subentry["title"],
                    data=subentry["data"],
                    unique_id=subentry["unique_id"],
                )
                # The entry's update listener reloads it.
                return self.async_abort(reason="reconfigure_successful")

        suggested = user_input or {CONF_NAME: current.title, **current.data}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(LIGHT_SCHEMA, suggested),
            errors=errors,
        )
