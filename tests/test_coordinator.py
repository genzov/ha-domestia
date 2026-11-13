"""Tests for the coordinator: availability, policy and the repair issue."""

from datetime import timedelta
from unittest.mock import AsyncMock, call

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.domestia.api import DomestiaConnectionError
from custom_components.domestia.const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_CONSECUTIVE_FAILURES,
)
from custom_components.domestia.coordinator import DomestiaCoordinator

from .conftest import ENTRY_ID, HOST, STATE

ISSUE_ID = f"relay_not_reported_{ENTRY_ID}"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up entry with whatever mock_client is configured to return."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Advance time by one scan interval so the coordinator polls."""
    freezer.tick(timedelta(seconds=DEFAULT_SCAN_INTERVAL))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _coordinator(entry: MockConfigEntry) -> DomestiaCoordinator:
    coordinator: DomestiaCoordinator = entry.runtime_data
    return coordinator


async def test_initial_poll(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """The first poll asks up to the highest relay and needs no corrections."""
    coordinator = _coordinator(init_integration)

    mock_client.get_state.assert_awaited_once_with(3)
    mock_client.turn_on.assert_not_called()
    mock_client.set_max_brightness.assert_not_called()
    assert coordinator.data == STATE
    assert coordinator.consecutive_failures == 0
    assert hass.states.get("light.garden").state == STATE_ON
    assert hass.states.get("light.hall").state == STATE_OFF


async def test_three_strike_availability(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Lights stay available with old data until the third failure in a row."""
    coordinator = _coordinator(init_integration)
    mock_client.get_state.side_effect = DomestiaConnectionError("timed out")

    for attempt in range(1, MAX_CONSECUTIVE_FAILURES):
        await _poll(hass, freezer)
        assert coordinator.consecutive_failures == attempt
        assert coordinator.last_update_success
        assert coordinator.data == STATE
        assert hass.states.get("light.garden").state == STATE_ON

    await _poll(hass, freezer)
    assert coordinator.consecutive_failures == MAX_CONSECUTIVE_FAILURES
    assert not coordinator.last_update_success
    assert hass.states.get("light.garden").state == STATE_UNAVAILABLE

    # Further failures keep the lights unavailable.
    await _poll(hass, freezer)
    assert hass.states.get("light.garden").state == STATE_UNAVAILABLE

    mock_client.get_state.side_effect = None
    mock_client.get_state.return_value = {1: 64, 2: 48, 3: 0}
    await _poll(hass, freezer)
    assert coordinator.consecutive_failures == 0
    assert coordinator.last_update_success
    assert coordinator.data == {1: 64, 2: 48, 3: 0}
    assert hass.states.get("light.garden").state == STATE_ON


async def test_unavailable_logged_once(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Losing the controller is logged once, and so is the recovery."""
    del init_integration
    mock_client.get_state.side_effect = DomestiaConnectionError("timed out")
    for _ in range(MAX_CONSECUTIVE_FAILURES + 2):
        await _poll(hass, freezer)

    mock_client.get_state.side_effect = None
    for _ in range(2):
        await _poll(hass, freezer)

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "Cannot reach the Domestia controller: timed out" in errors[0].getMessage()
    assert sum("recovered" in r.getMessage() for r in caplog.records) == 1


async def test_failure_count_resets_on_success(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Only failures in a row count towards the threshold."""
    coordinator = _coordinator(init_integration)

    for _ in range(MAX_CONSECUTIVE_FAILURES):
        mock_client.get_state.side_effect = DomestiaConnectionError("timed out")
        await _poll(hass, freezer)
        mock_client.get_state.side_effect = None
        await _poll(hass, freezer)

    assert coordinator.consecutive_failures == 0
    assert hass.states.get("light.garden").state == STATE_ON


async def test_first_refresh_failure(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Without data from an earlier poll, a failure fails setup right away."""
    mock_client.get_state.side_effect = DomestiaConnectionError("refused")

    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_client.get_state.assert_awaited_once()


@pytest.mark.parametrize("raw", [0, 32])
async def test_always_on_enforced(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    raw: int,
) -> None:
    """An always-on relay below full brightness is turned back on at full."""
    mock_client.get_state.return_value = {1: raw, 2: 32, 3: 0}

    await _setup(hass, mock_config_entry)

    assert mock_client.mock_calls == [
        call.get_state(3),
        call.turn_on(1),
        call.set_max_brightness(1),
    ]
    assert _coordinator(mock_config_entry).data == {1: 64, 2: 32, 3: 0}


async def test_non_dimmable_dimmed_reset(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A dimmed non-dimmable relay is reset to full brightness."""
    mock_client.reset_mock()
    mock_client.get_state.return_value = {1: 64, 2: 32, 3: 20}

    await _poll(hass, freezer)

    assert mock_client.mock_calls == [call.get_state(3), call.set_max_brightness(3)]
    assert _coordinator(init_integration).data == {1: 64, 2: 32, 3: 64}
    assert hass.states.get("light.hall").state == STATE_ON


async def test_enforcement_errors_do_not_fail_update(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failed corrections are logged; the poll succeeds and both are attempted."""
    mock_client.get_state.return_value = {1: 0, 2: 32, 3: 20}
    mock_client.turn_on.side_effect = DomestiaConnectionError("turn on failed")
    mock_client.set_max_brightness.side_effect = DomestiaConnectionError("max failed")

    await _setup(hass, mock_config_entry)

    coordinator = _coordinator(mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert coordinator.last_update_success
    assert coordinator.data == {1: 64, 2: 32, 3: 64}
    assert mock_client.set_max_brightness.mock_calls == [call(1), call(3)]
    assert "Failed to turn on always-on light Wardrobe: turn on failed" in caplog.text
    assert "Failed to set always-on light Wardrobe to max brightness" in caplog.text
    assert "Failed to reset non-dimmable light Hall: max failed" in caplog.text


async def test_normalization(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A non-dimmable 1 means full brightness; a dimmable 1 is a dim level."""
    mock_client.get_state.return_value = {1: 1, 2: 1, 3: 1}

    await _setup(hass, mock_config_entry)

    assert _coordinator(mock_config_entry).data == {1: 64, 2: 1, 3: 64}
    mock_client.turn_on.assert_not_called()
    mock_client.set_max_brightness.assert_not_called()


async def test_unconfigured_relays_skipped(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Relays in the reply without a configured light are ignored."""
    mock_client.get_state.return_value = {1: 64, 2: 32, 3: 0, 4: 10, 5: 1}

    await _setup(hass, mock_config_entry)

    assert _coordinator(mock_config_entry).data == STATE


async def test_no_lights_polls_relay_one(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Without configured lights the poll still asks for one relay."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, unique_id=HOST)

    await _setup(hass, entry)

    mock_client.get_state.assert_awaited_once_with(1)
    assert _coordinator(entry).data == {}


async def test_state_change_logged(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Changes are logged once, described per light."""
    await _setup(hass, mock_config_entry)

    assert "Light state changed: Garden is on at 50% (brightness 32/64)" in caplog.text
    assert "Light state changed: Hall is off" in caplog.text
    assert "Light state changed: Wardrobe is on" in caplog.text

    caplog.clear()
    await _poll(hass, freezer)
    assert "Light state changed" not in caplog.text

    mock_client.get_state.return_value = {1: 64, 2: 48, 3: 1}
    await _poll(hass, freezer)
    assert "Light state changed: Garden is on at 75% (brightness 48/64)" in caplog.text
    assert "Light state changed: Hall is on" in caplog.text
    assert "Wardrobe" not in caplog.text


async def test_relay_not_reported_issue(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Missing relays raise a repair issue that clears once they are reported."""
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None

    mock_client.get_state.return_value = {1: 64}
    await _poll(hass, freezer)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.config_entry_id == ENTRY_ID
    assert not issue.is_fixable
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "relay_not_reported"
    assert issue.translation_placeholders == {"host": HOST, "relays": "2, 3"}
    assert _coordinator(init_integration).data == {1: 64}
    assert hass.states.get("light.garden").state == STATE_UNAVAILABLE
    assert hass.states.get("light.hall").state == STATE_UNAVAILABLE

    mock_client.get_state.return_value = dict(STATE)
    await _poll(hass, freezer)

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None
    assert hass.states.get("light.garden").state == STATE_ON


async def test_relay_not_reported_issue_at_setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The issue raised by the first poll survives setup, and goes with the entry."""
    mock_client.get_state.return_value = {1: 64}

    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_placeholders == {"host": HOST, "relays": "2, 3"}

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None
