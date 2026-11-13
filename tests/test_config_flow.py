"""Tests for the Domestia config flow."""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant.common import MockConfigEntry

from custom_components.domestia.api import DomestiaConnectionError
from custom_components.domestia.const import (
    CONF_ALWAYS_ON,
    CONF_DIMMABLE,
    CONF_MODEL,
    CONF_RELAY,
    DOMAIN,
    SUBENTRY_TYPE_LIGHT,
)

from .conftest import GARDEN, HALL, HOST

OTHER_HOST = "192.168.1.3"


@pytest.fixture(autouse=True)
def mock_setup_entry(request: pytest.FixtureRequest) -> Generator[AsyncMock | None]:
    """Keep flow tests from setting up the created entry.

    Tests that use init_integration need a really loaded entry, so they are
    left unpatched.
    """
    if "init_integration" in request.fixturenames:
        yield None
        return
    with patch(
        "custom_components.domestia.async_setup_entry", return_value=True
    ) as setup_entry:
        yield setup_entry


async def test_user_creates_entry(
    hass: HomeAssistant, mock_client: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """The user step tests the connection and creates an entry for the host."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_MODEL: "DMC-012-003"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOST
    assert result["data"] == {CONF_HOST: HOST, CONF_MODEL: "DMC-012-003"}
    assert result["result"].unique_id == HOST
    assert result["result"].subentries == {}
    mock_client.get_state.assert_awaited_once_with(1)
    mock_setup_entry.assert_called_once()


@pytest.mark.parametrize(
    ("user_input", "expected"),
    [
        ({CONF_HOST: HOST, CONF_MODEL: "DMC-008-001"}, {"model": "DMC-008-001"}),
        ({CONF_HOST: HOST}, {}),
    ],
    ids=["model", "no model"],
)
async def test_user_model(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    user_input: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """A chosen model is stored; an omitted model leaves the key out."""
    del mock_client
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST, **expected}
    assert result["result"].data.get(CONF_MODEL) == expected.get(CONF_MODEL)


async def test_user_rejects_unknown_model(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """The selector schema rejects a model that is not supported."""
    del mock_client
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_MODEL: "DMC-999"}
        )


async def test_user_cannot_connect(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """A connection failure shows an error, and the user can retry."""
    mock_client.get_state.side_effect = DomestiaConnectionError("refused")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}

    mock_client.get_state.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_already_configured(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A controller can only be added once."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_client.get_state.assert_not_awaited()


async def test_reconfigure(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Reconfigure updates the host, model, unique id and title."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert dict(mock_config_entry.data) == {CONF_HOST: OTHER_HOST}
    assert mock_config_entry.unique_id == OTHER_HOST
    assert mock_config_entry.title == OTHER_HOST
    assert len(mock_config_entry.subentries) == 3
    mock_client.get_state.assert_awaited_once_with(1)


async def test_reconfigure_loaded_entry_uses_its_client(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A loaded entry's own client probes the controller, serializing requests."""
    mock_client.reset_mock()
    with patch("custom_components.domestia.config_flow.DomestiaClient") as new_client:
        result = await init_integration.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    new_client.assert_not_called()
    mock_client.get_state.assert_any_await(1)


async def test_reconfigure_same_host(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Keeping the host and changing only the model is not a conflict."""
    del mock_client
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_MODEL: "DMC-008-001"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert dict(mock_config_entry.data) == {CONF_HOST: HOST, CONF_MODEL: "DMC-008-001"}


async def test_reconfigure_cannot_connect(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A connection failure shows an error, and the user can retry."""
    mock_config_entry.add_to_hass(hass)
    mock_client.get_state.side_effect = DomestiaConnectionError("refused")
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert mock_config_entry.unique_id == HOST

    mock_client.get_state.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.unique_id == OTHER_HOST


async def test_reconfigure_host_of_other_entry(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Reconfigure aborts when another entry already has the new host."""
    mock_config_entry.add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN, title=OTHER_HOST, unique_id=OTHER_HOST, data={}
    ).add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.unique_id == HOST
    mock_client.get_state.assert_not_awaited()


async def _start_light_flow(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Start the add-light flow and return its flow id."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LIGHT), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_subentry_user(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Adding a light creates a subentry and the entry reloads with its entity."""
    flow_id = await _start_light_flow(hass, init_integration)
    result = await hass.config_entries.subentries.async_configure(
        flow_id, {CONF_NAME: "Porch", CONF_RELAY: 4.0, CONF_DIMMABLE: True}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Porch"
    assert result["data"] == {
        CONF_RELAY: 4,
        CONF_DIMMABLE: True,
        CONF_ALWAYS_ON: False,
    }
    assert isinstance(result["data"][CONF_RELAY], int)
    assert result["unique_id"] == "4"
    assert "4" in {s.unique_id for s in init_integration.subentries.values()}
    assert hass.states.get("light.porch") is not None


async def test_subentry_user_always_on_without_name(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """An always-on light needs no name and is titled after its relay."""
    flow_id = await _start_light_flow(hass, init_integration)
    result = await hass.config_entries.subentries.async_configure(
        flow_id, {CONF_RELAY: 4, CONF_ALWAYS_ON: True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Relay 4"
    assert result["data"][CONF_ALWAYS_ON] is True


@pytest.mark.parametrize(
    ("user_input", "error"),
    [
        ({CONF_NAME: " ", CONF_RELAY: 4}, "name_required"),
        ({CONF_RELAY: 4}, "name_required"),
        ({CONF_NAME: "Porch", CONF_RELAY: 4.5}, "invalid_relay"),
    ],
    ids=["blank name", "no name", "fractional relay"],
)
async def test_subentry_user_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    user_input: dict[str, Any],
    error: str,
) -> None:
    """Invalid input shows an error, and the user can correct it."""
    flow_id = await _start_light_flow(hass, init_integration)
    result = await hass.config_entries.subentries.async_configure(flow_id, user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    result = await hass.config_entries.subentries.async_configure(
        flow_id, {CONF_NAME: "Porch", CONF_RELAY: 4}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_subentry_user_duplicate_relay(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A relay that already has a light aborts."""
    flow_id = await _start_light_flow(hass, init_integration)
    result = await hass.config_entries.subentries.async_configure(
        flow_id, {CONF_NAME: "Porch", CONF_RELAY: 2.0}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(init_integration.subentries) == 3


async def test_subentry_reconfigure(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Reconfiguring a light updates its subentry, keeping its entity."""
    result = await init_integration.start_subentry_reconfigure_flow(hass, GARDEN)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    schema = result["data_schema"]
    assert schema is not None
    suggested = {
        str(key): key.description["suggested_value"]
        for key in schema.schema
        if key.description
    }
    assert suggested == {
        CONF_NAME: "Garden",
        CONF_RELAY: 2,
        CONF_DIMMABLE: True,
        CONF_ALWAYS_ON: False,
    }

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Garden", CONF_RELAY: 5.0, CONF_DIMMABLE: False},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    subentry = init_integration.subentries[GARDEN]
    assert subentry.title == "Garden"
    assert subentry.unique_id == "5"
    assert dict(subentry.data) == {
        CONF_RELAY: 5,
        CONF_DIMMABLE: False,
        CONF_ALWAYS_ON: False,
    }
    assert init_integration.runtime_data.lights[5].subentry_id == GARDEN


async def test_subentry_reconfigure_keeps_relay(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A light keeping its own relay is not a collision."""
    result = await init_integration.start_subentry_reconfigure_flow(hass, HALL)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_NAME: "Hallway", CONF_RELAY: 3}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert init_integration.subentries[HALL].title == "Hallway"


async def test_subentry_reconfigure_name_required(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Clearing a light's name shows an error, and the user can correct it."""
    result = await init_integration.start_subentry_reconfigure_flow(hass, HALL)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_NAME: "", CONF_RELAY: 3}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "name_required"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_NAME: "Hall", CONF_RELAY: 3}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_subentry_reconfigure_relay_collision(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Moving a light to a relay another light uses aborts."""
    result = await init_integration.start_subentry_reconfigure_flow(hass, GARDEN)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_NAME: "Garden", CONF_RELAY: 3}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert init_integration.subentries[GARDEN].unique_id == "2"
