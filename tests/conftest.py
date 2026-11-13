"""Shared fixtures.

Fixtures specific to one area belong in that area's test file.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigSubentryDataWithId
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.domestia.const import (
    CONF_ALWAYS_ON,
    CONF_DIMMABLE,
    CONF_MODEL,
    CONF_RELAY,
    DOMAIN,
    SUBENTRY_TYPE_LIGHT,
)

HOST = "192.168.1.2"
ENTRY_ID = "domestia_entry"

# Subentry ids of the lights in mock_config_entry.
WARDROBE = "wardrobe_subentry"  # relay 1, always on
GARDEN = "garden_subentry"  # relay 2, dimmable
HALL = "hall_subentry"  # relay 3, non-dimmable

# Raw controller state returned by mock_client: wardrobe full, garden half, hall off.
STATE = {1: 64, 2: 32, 3: 0}


def light_subentry(
    subentry_id: str,
    name: str,
    relay: int,
    *,
    dimmable: bool = False,
    always_on: bool = False,
) -> ConfigSubentryDataWithId:
    """Return subentry data for a light."""
    return ConfigSubentryDataWithId(
        subentry_id=subentry_id,
        subentry_type=SUBENTRY_TYPE_LIGHT,
        title=name,
        unique_id=str(relay),
        data={CONF_RELAY: relay, CONF_DIMMABLE: dimmable, CONF_ALWAYS_ON: always_on},
    )


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Patch the controller client in setup and the config flow."""
    with (
        patch("custom_components.domestia.DomestiaClient", autospec=True) as client_cls,
        patch("custom_components.domestia.config_flow.DomestiaClient", new=client_cls),
    ):
        client = client_cls.return_value
        client.host = HOST
        client.get_state.return_value = dict(STATE)
        yield client


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a controller entry with an always-on, a dimmable and a plain light."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=HOST,
        unique_id=HOST,
        entry_id=ENTRY_ID,
        data={CONF_HOST: HOST, CONF_MODEL: "DMC-012-003"},
        subentries_data=[
            light_subentry(WARDROBE, "Wardrobe", 1, always_on=True),
            light_subentry(GARDEN, "Garden", 2, dimmable=True),
            light_subentry(HALL, "Hall", 3),
        ],
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: AsyncMock
) -> MockConfigEntry:
    """Set up the integration with mock_config_entry and mock_client."""
    del mock_client
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
