"""Config flow for Domestia.

The user flow asks for the controller and optionally imports the add-on's
lights. Lights are config subentries, one per relay. Reconfigure steps update
the entry or subentry and abort; the entry's update listener reloads it.
"""

from typing import Any, override

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_SCAN_INTERVAL, UnitOfTime
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.util.yaml import parse_yaml

from .api import DomestiaClient, DomestiaError
from .const import (
    CONF_ALWAYS_ON,
    CONF_DIMMABLE,
    CONF_LIGHTS,
    CONF_MODEL,
    CONF_RELAY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_RELAY,
    MIN_RELAY,
    MIN_SCAN_INTERVAL,
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

LIGHTS_SCHEMA = probatio.Schema(
    {probatio.Optional(CONF_LIGHTS): TextSelector(TextSelectorConfig(multiline=True))}
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


class InvalidLightsError(Exception):
    """The lights JSON is invalid; translation_key names the form error."""

    def __init__(self, translation_key: str) -> None:
        """Create the error for a form error key."""
        super().__init__(translation_key)
        self.translation_key = translation_key


def _light_title(name: str, relay: int) -> str:
    """Return a light's title; nameless always-on lights are named after the relay."""
    return name.strip() or f"Relay {relay}"


def _light_subentry(
    name: str, relay: int, *, dimmable: bool, always_on: bool
) -> ConfigSubentryData:
    return ConfigSubentryData(
        data={CONF_RELAY: relay, CONF_DIMMABLE: dimmable, CONF_ALWAYS_ON: always_on},
        subentry_type=SUBENTRY_TYPE_LIGHT,
        title=_light_title(name, relay),
        unique_id=str(relay),
    )


def _parse_light(light: Any) -> ConfigSubentryData:
    """Validate one item of the lights array, except for duplicate relays."""
    if not isinstance(light, dict):
        raise InvalidLightsError("invalid_lights_json")

    relay = light.get(CONF_RELAY)
    # bool is a subclass of int, but true is not a relay number.
    if (
        isinstance(relay, bool)
        or not isinstance(relay, int)
        or not MIN_RELAY <= relay <= MAX_RELAY
    ):
        raise InvalidLightsError("invalid_relay")

    name = light.get(CONF_NAME, "")
    dimmable = light.get(CONF_DIMMABLE, False)
    always_on = light.get(CONF_ALWAYS_ON, False)
    if (
        not isinstance(name, str)
        or not isinstance(dimmable, bool)
        or not isinstance(always_on, bool)
    ):
        raise InvalidLightsError("invalid_lights_json")

    # Always-on lights get no entity, so they do not need a name.
    if not always_on and not name.strip():
        raise InvalidLightsError("name_required")

    return _light_subentry(name, relay, dimmable=dimmable, always_on=always_on)


def parse_lights_json(text: str) -> list[ConfigSubentryData]:
    """Parse the add-on's lights list into light subentries.

    The add-on's Configuration tab shows the list as YAML. YAML is a superset of
    JSON, so both that YAML and a JSON array are accepted.
    """
    try:
        lights = parse_yaml(text)
    except HomeAssistantError as err:
        raise InvalidLightsError("invalid_lights_json") from err
    if not isinstance(lights, list):
        raise InvalidLightsError("invalid_lights_json")

    subentries: list[ConfigSubentryData] = []
    relays: set[str | None] = set()
    for light in lights:
        subentry = _parse_light(light)
        if subentry["unique_id"] in relays:
            raise InvalidLightsError("duplicate_relay")
        relays.add(subentry["unique_id"])
        subentries.append(subentry)
    return subentries


async def _can_connect(host: str) -> bool:
    """Return whether the controller at host answers a get-state request."""
    try:
        # Relay 1, not 0: a header-only reply is not a valid state.
        await DomestiaClient(host).get_state(1)
    except DomestiaError:
        return False
    return True


class DomestiaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Domestia controller."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Start a flow without controller data."""
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return DomestiaOptionsFlow()

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
            if await _can_connect(user_input[CONF_HOST]):
                self._data = user_input
                return await self.async_step_lights()
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally import the lights array from the add-on configuration."""
        errors: dict[str, str] = {}
        if user_input is not None:
            text = user_input.get(CONF_LIGHTS, "").strip()
            try:
                subentries = parse_lights_json(text) if text else []
            except InvalidLightsError as err:
                errors["base"] = err.translation_key
            else:
                return self.async_create_entry(
                    title=self._data[CONF_HOST],
                    data=self._data,
                    subentries=subentries,
                )

        return self.async_show_form(
            step_id="lights",
            data_schema=self.add_suggested_values_to_schema(LIGHTS_SCHEMA, user_input),
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
            if await _can_connect(host):
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


class DomestiaOptionsFlow(OptionsFlow):
    """Change how often the controller is polled."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the polling interval."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: float(user_input[CONF_SCAN_INTERVAL])}
            )

        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        step=0.05,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement=UnitOfTime.SECONDS,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


def _light_from_input(user_input: dict[str, Any]) -> ConfigSubentryData:
    """Build a light subentry from form input; raise InvalidLightsError if invalid."""
    relay = user_input[CONF_RELAY]
    # The selector bounds the relay but does not round it.
    if not float(relay).is_integer():
        raise InvalidLightsError("invalid_relay")
    name: str = user_input.get(CONF_NAME, "")
    always_on: bool = user_input[CONF_ALWAYS_ON]
    if not always_on and not name.strip():
        raise InvalidLightsError("name_required")
    return _light_subentry(
        name, int(relay), dimmable=user_input[CONF_DIMMABLE], always_on=always_on
    )


class LightSubentryFlow(ConfigSubentryFlow):
    """Add or change a light on one of the controller's relays."""

    def _relay_in_use(self, subentry: ConfigSubentryData, own_id: str | None) -> bool:
        """Return whether a light other than own_id already uses the relay."""
        return any(
            other.unique_id == subentry["unique_id"] and other.subentry_id != own_id
            for other in self._get_entry().subentries.values()
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a light."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                subentry = _light_from_input(user_input)
            except InvalidLightsError as err:
                errors["base"] = err.translation_key
            else:
                if self._relay_in_use(subentry, None):
                    return self.async_abort(reason="already_configured")
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
            except InvalidLightsError as err:
                errors["base"] = err.translation_key
            else:
                if self._relay_in_use(subentry, current.subentry_id):
                    return self.async_abort(reason="already_configured")
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
