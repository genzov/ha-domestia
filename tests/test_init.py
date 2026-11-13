"""Tests for setting up and unloading the integration."""

from types import MappingProxyType
from unittest.mock import AsyncMock

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.typing import WebSocketGenerator

from custom_components.domestia import async_remove_config_entry_device
from custom_components.domestia.api import DomestiaConnectionError
from custom_components.domestia.const import (
    CONF_ALWAYS_ON,
    CONF_DIMMABLE,
    CONF_RELAY,
    DOMAIN,
    SUBENTRY_TYPE_LIGHT,
)

from .conftest import ENTRY_ID, GARDEN, HALL, STATE


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


async def test_setup_retry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """An unreachable controller makes setup retry later."""
    mock_client.get_state.side_effect = DomestiaConnectionError("refused")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.states.get("light.garden") is None


async def test_add_subentry(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Adding a light subentry reloads the entry and adds its entity and device."""
    mock_client.get_state.return_value = {**STATE, 4: 64}
    subentry = ConfigSubentry(
        data=MappingProxyType(
            {CONF_RELAY: 4, CONF_DIMMABLE: False, CONF_ALWAYS_ON: False}
        ),
        subentry_type=SUBENTRY_TYPE_LIGHT,
        title="Porch",
        unique_id="4",
    )

    assert hass.config_entries.async_add_subentry(init_integration, subentry)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.LOADED
    state = hass.states.get("light.porch")
    assert state is not None
    assert state.state == "on"
    entity_id = er.async_get(hass).async_get_entity_id(
        LIGHT_DOMAIN, DOMAIN, subentry.subentry_id
    )
    assert entity_id == "light.porch"
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry.subentry_id), ENTRY_ID
    )
    assert device is not None
    assert device.name == "Porch"


async def test_remove_subentry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Removing a light subentry removes its entity and device."""
    assert hass.config_entries.async_remove_subentry(init_integration, HALL)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.LOADED
    assert hass.states.get("light.hall") is None
    assert er.async_get(hass).async_get_entity_id(LIGHT_DOMAIN, DOMAIN, HALL) is None
    assert (
        dr.async_get(hass).async_get_device_by_identifier((DOMAIN, HALL), ENTRY_ID)
        is None
    )
    assert hass.states.get("light.garden") is not None


async def test_remove_config_entry_device(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Only devices of lights that no longer exist may be removed."""
    registry = dr.async_get(hass)
    stale = registry.async_get_or_create(
        config_entry_id=ENTRY_ID, identifiers={(DOMAIN, "removed_subentry")}
    )
    current = registry.async_get_device_by_identifier((DOMAIN, GARDEN), ENTRY_ID)
    controller = registry.async_get_device_by_identifier((DOMAIN, ENTRY_ID), ENTRY_ID)
    foreign = registry.async_get_or_create(
        config_entry_id=ENTRY_ID, identifiers={("other", "removed_subentry")}
    )
    assert current is not None
    assert controller is not None

    assert await async_remove_config_entry_device(hass, init_integration, stale)
    assert not await async_remove_config_entry_device(hass, init_integration, current)
    assert not await async_remove_config_entry_device(
        hass, init_integration, controller
    )
    assert not await async_remove_config_entry_device(hass, init_integration, foreign)


async def test_remove_stale_device_via_websocket(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """The UI can delete a stale light device but not a configured one."""
    del init_integration
    assert await async_setup_component(hass, "config", {})
    registry = dr.async_get(hass)
    stale = registry.async_get_or_create(
        config_entry_id=ENTRY_ID, identifiers={(DOMAIN, "removed_subentry")}
    )
    current = registry.async_get_device_by_identifier((DOMAIN, GARDEN), ENTRY_ID)
    assert current is not None

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "config/device_registry/remove", "device_id": stale.id}
    )
    response = await client.receive_json()
    assert response["success"]
    assert registry.async_get(stale.id) is None

    await client.send_json_auto_id(
        {"type": "config/device_registry/remove", "device_id": current.id}
    )
    response = await client.receive_json()
    assert not response["success"]
    assert registry.async_get(current.id) is not None
