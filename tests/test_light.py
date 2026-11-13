"""Tests for the Domestia light platform."""

from typing import Any
from unittest.mock import AsyncMock, call

import pytest
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_SUPPORTED_COLOR_MODES,
    ColorMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import async_get_platforms
from pytest_homeassistant.common import MockConfigEntry

from custom_components.domestia.api import DomestiaConnectionError
from custom_components.domestia.const import CONF_ALWAYS_ON, DOMAIN

from .conftest import ENTRY_ID, GARDEN, HALL, HOST, WARDROBE, light_subentry

GARDEN_ID = "light.garden"
HALL_ID = "light.hall"


async def test_entities(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Each light but the always-on one gets an entity keyed on its subentry."""
    del init_integration
    registry = er.async_get(hass)
    assert registry.async_get_entity_id(LIGHT_DOMAIN, DOMAIN, GARDEN) == GARDEN_ID
    assert registry.async_get_entity_id(LIGHT_DOMAIN, DOMAIN, HALL) == HALL_ID
    assert registry.async_get_entity_id(LIGHT_DOMAIN, DOMAIN, WARDROBE) is None
    assert hass.states.get("light.wardrobe") is None

    garden = registry.async_get(GARDEN_ID)
    assert garden is not None
    assert garden.config_subentry_id == GARDEN


async def test_states(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """States and color modes follow the controller state and dimmability."""
    del init_integration
    garden = hass.states.get(GARDEN_ID)
    assert garden is not None
    assert garden.state == STATE_ON
    assert garden.attributes[ATTR_BRIGHTNESS] == 128  # 32/64
    assert garden.attributes[ATTR_COLOR_MODE] == ColorMode.BRIGHTNESS
    assert garden.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.BRIGHTNESS]

    hall = hass.states.get(HALL_ID)
    assert hall is not None
    assert hall.state == STATE_OFF
    assert hall.attributes.get(ATTR_BRIGHTNESS) is None
    assert hall.attributes.get(ATTR_COLOR_MODE) is None
    assert hall.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.ONOFF]


async def test_on_off_light_has_no_brightness(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A non-dimmable light that is on reports the on/off color mode only."""
    mock_client.get_state.return_value = {1: 64, 2: 32, 3: 64}
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    hall = hass.states.get(HALL_ID)
    assert hall is not None
    assert hall.state == STATE_ON
    assert hall.attributes[ATTR_COLOR_MODE] == ColorMode.ONOFF
    assert hall.attributes.get(ATTR_BRIGHTNESS) is None

    # Home Assistant skips brightness for on/off lights; the entity has none either.
    (platform,) = async_get_platforms(hass, DOMAIN)
    entity = platform.entities[HALL_ID]
    assert isinstance(entity, LightEntity)
    assert entity.brightness is None


async def test_device_info(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Each light is its own device, linked to the controller device."""
    del init_integration
    registry = dr.async_get(hass)
    controller = registry.async_get_device_by_identifier((DOMAIN, ENTRY_ID), ENTRY_ID)
    assert controller is not None
    assert controller.name == "Domestia controller"
    assert controller.manufacturer == "Domestia"
    assert controller.model == "DMC-012-003"

    device = registry.async_get_device_by_identifier((DOMAIN, GARDEN), ENTRY_ID)
    assert device is not None
    assert device.name == "Garden"
    assert device.manufacturer == "Domestia"
    assert device.model == "DMC-012-003"
    assert device.identifiers == {(DOMAIN, GARDEN)}
    assert device.via_device_id == controller.id
    assert device.config_entry_id == ENTRY_ID

    assert registry.async_get_device_by_identifier((DOMAIN, WARDROBE), ENTRY_ID) is None


async def test_device_info_without_model(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Without a configured model, the devices have none."""
    del mock_client
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=HOST,
        unique_id=HOST,
        data={CONF_HOST: HOST},
        subentries_data=[light_subentry(GARDEN, "Garden", 2, dimmable=True)],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = dr.async_get(hass)
    controller = registry.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    device = registry.async_get_device_by_identifier((DOMAIN, GARDEN), entry.entry_id)
    assert controller is not None
    assert device is not None
    assert controller.model is None
    assert device.model is None
    assert device.via_device_id == controller.id


@pytest.mark.parametrize(
    ("service", "data", "expected"),
    [
        pytest.param(
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: GARDEN_ID, ATTR_BRIGHTNESS: 1},
            [call.turn_on(2), call.set_brightness(2, 1)],
            id="dimmable_brightness_floored",
        ),
        pytest.param(
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: GARDEN_ID, ATTR_BRIGHTNESS: 255},
            [call.turn_on(2), call.set_brightness(2, 64)],
            id="dimmable_full_brightness",
        ),
        pytest.param(
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: GARDEN_ID, ATTR_BRIGHTNESS: 128},
            [call.turn_on(2), call.set_brightness(2, 32)],
            id="dimmable_half_brightness",
        ),
        pytest.param(
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: GARDEN_ID},
            [call.turn_on(2)],
            id="dimmable_without_brightness",
        ),
        pytest.param(
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: HALL_ID},
            [call.turn_on(3), call.set_max_brightness(3)],
            id="non_dimmable",
        ),
        pytest.param(
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: GARDEN_ID},
            [call.turn_off(2)],
            id="turn_off",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_commands(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    service: str,
    data: dict[str, Any],
    expected: list[Any],
) -> None:
    """Commands send the Go bridge's call sequence, then refresh the state."""
    mock_client.reset_mock()

    await hass.services.async_call(LIGHT_DOMAIN, service, data, blocking=True)

    commands = [c for c in mock_client.method_calls if c[0] != "get_state"]
    assert commands == expected
    # The refresh comes after the commands.
    assert mock_client.method_calls[-1][0] == "get_state"


@pytest.mark.parametrize(
    ("service", "method"),
    [(SERVICE_TURN_ON, "turn_on"), (SERVICE_TURN_OFF, "turn_off")],
)
async def test_command_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    service: str,
    method: str,
) -> None:
    """A failed command raises a translated error and still refreshes."""
    del init_integration
    mock_client.reset_mock()
    getattr(mock_client, method).side_effect = DomestiaConnectionError("refused")

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            LIGHT_DOMAIN, service, {ATTR_ENTITY_ID: HALL_ID}, blocking=True
        )

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "command_failed"
    assert exc_info.value.translation_placeholders == {
        "name": "Hall",
        "error": "refused",
    }
    assert str(exc_info.value) == "Failed to control Hall: refused"
    mock_client.get_state.assert_awaited_once()


async def test_failed_turn_on_still_sets_brightness(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A failed turn_on still sends the brightness, then raises its error."""
    del init_integration
    mock_client.reset_mock()
    mock_client.turn_on.side_effect = DomestiaConnectionError("refused")
    mock_client.set_brightness.side_effect = DomestiaConnectionError("timeout")

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: GARDEN_ID, ATTR_BRIGHTNESS: 255},
            blocking=True,
        )

    mock_client.set_brightness.assert_awaited_once_with(2, 64)
    assert exc_info.value.translation_placeholders == {
        "name": "Garden",
        "error": "refused",
    }
    mock_client.get_state.assert_awaited_once()


async def test_switched_to_always_on_removes_entity_and_device(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A light changed to always-on loses its entity and device on reload."""
    assert entity_registry.async_get_entity_id(LIGHT_DOMAIN, DOMAIN, HALL)
    subentry = init_integration.subentries[HALL]

    hass.config_entries.async_update_subentry(
        init_integration,
        subentry,
        data={**subentry.data, CONF_ALWAYS_ON: True},
    )
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id(LIGHT_DOMAIN, DOMAIN, HALL) is None
    assert hass.states.get(HALL_ID) is None
    assert (
        device_registry.async_get_device_by_identifier((DOMAIN, HALL), ENTRY_ID) is None
    )
