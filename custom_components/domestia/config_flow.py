"""Config flow for Domestia.

Phase 0 skeleton: the user step only. WP-C adds the lights import step,
reconfigure, the options flow and the light subentry flow.
"""

from typing import Any

import probatio
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
)
from homeassistant.const import CONF_HOST
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .api import DomestiaClient, DomestiaError
from .const import CONF_MODEL, DOMAIN, MODELS

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): TextSelector(),
        probatio.Optional(CONF_MODEL): SelectSelector(
            SelectSelectorConfig(options=list(MODELS), mode=SelectSelectorMode.DROPDOWN)
        ),
    }
)


class InvalidLightsError(Exception):
    """The lights JSON is invalid; translation_key names the form error."""

    def __init__(self, translation_key: str) -> None:
        """Create the error for a form error key."""
        super().__init__(translation_key)
        self.translation_key = translation_key


def parse_lights_json(text: str) -> list[ConfigSubentryData]:
    """Parse the add-on's lights array into light subentries."""
    raise NotImplementedError  # WP-C


class DomestiaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Domestia controller."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the controller's address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()
            try:
                await DomestiaClient(user_input[CONF_HOST]).get_state(1)
            except DomestiaError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_HOST], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )
