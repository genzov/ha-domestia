"""Tests for the brightness conversions."""

import pytest

from custom_components.domestia.api import MAX_BRIGHTNESS
from custom_components.domestia.brightness import describe_state, to_domestia, to_ha


@pytest.mark.parametrize(
    ("ha", "expected"),
    [
        pytest.param(0, 0, id="off"),
        pytest.param(255, 64, id="max"),
        pytest.param(1, 0, id="lowest value still collapses to 0"),
        pytest.param(2, 1, id="smallest value that maps to a dim level"),
        pytest.param(4, 1, id="low value rounds to 1"),
        pytest.param(128, 32, id="midpoint rounds to nearest"),
        pytest.param(1000, 64, id="out-of-range high value is clamped"),
        pytest.param(-10, 0, id="negative value is clamped to 0"),
    ],
)
def test_to_domestia(ha: int, expected: int) -> None:
    """Home Assistant brightness is rounded and clamped to 0-64."""
    assert to_domestia(ha) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(0, 0, id="off"),
        pytest.param(64, 255, id="max"),
        pytest.param(1, 4, id="low value"),
        pytest.param(32, 128, id="midpoint rounds to nearest"),
        pytest.param(100, 255, id="out-of-range value is clamped"),
    ],
)
def test_to_ha(raw: int, expected: int) -> None:
    """Controller brightness is rounded and clamped to 0-255."""
    assert to_ha(raw) == expected


@pytest.mark.parametrize("raw", range(MAX_BRIGHTNESS + 1))
def test_round_trip(raw: int) -> None:
    """Every controller level survives a round trip through Home Assistant."""
    assert to_domestia(to_ha(raw)) == raw


@pytest.mark.parametrize(
    ("raw", "dimmable", "expected"),
    [
        (0, False, "off"),
        (0, True, "off"),
        (64, False, "on"),
        (64, True, "on at 100% (brightness 64/64)"),
        (48, True, "on at 75% (brightness 48/64)"),
        (32, True, "on at 50% (brightness 32/64)"),
        (1, True, "on at 2% (brightness 1/64)"),
        # 12.5% rounds half up like Go's math.Round, not to even.
        (8, True, "on at 13% (brightness 8/64)"),
    ],
)
def test_describe_state(raw: int, *, dimmable: bool, expected: str) -> None:
    """The logged state names the dim level of dimmable lights only."""
    assert describe_state(raw, dimmable=dimmable) == expected
