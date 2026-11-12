"""Tests for setting up and unloading the integration."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry


async def test_setup_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Lights are created for all but always-on relays, and the entry unloads."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert hass.states.get("light.garden") is not None
    assert hass.states.get("light.hall") is not None
    assert hass.states.get("light.wardrobe") is None

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.NOT_LOADED
