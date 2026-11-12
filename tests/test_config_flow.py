"""Tests for the Domestia config flow."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.domestia.const import CONF_MODEL, DOMAIN

from .conftest import HOST


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Keep flow tests from setting up the created entry."""
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
    mock_client.get_state.assert_awaited_once_with(1)
    mock_setup_entry.assert_called_once()
