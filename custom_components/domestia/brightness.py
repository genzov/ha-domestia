"""Brightness conversion between the controller (0-64) and Home Assistant (0-255).

Conversions round half away from zero, like Go's math.Round. Python's round()
rounds half to even and Home Assistant's helpers scale differently; either
would break the round trip of every controller level.
"""

import math

from .api import MAX_BRIGHTNESS

MAX_HA_BRIGHTNESS = 255


def to_ha(raw: int) -> int:
    """Convert a controller brightness to Home Assistant's scale."""
    scaled = math.floor(raw * MAX_HA_BRIGHTNESS / MAX_BRIGHTNESS + 0.5)
    return min(scaled, MAX_HA_BRIGHTNESS)


def to_domestia(ha: int) -> int:
    """Convert a Home Assistant brightness to the controller's scale.

    Truncating would make low values collapse to 0, switching the light off
    instead of dimming it. The result is clamped to the controller's range.
    """
    scaled = math.floor(ha * MAX_BRIGHTNESS / MAX_HA_BRIGHTNESS + 0.5)
    return min(max(scaled, 0), MAX_BRIGHTNESS)


def describe_state(raw: int, *, dimmable: bool) -> str:
    """Describe a light's state for logging, e.g. "on at 75% (brightness 48/64)"."""
    if raw == 0:
        return "off"
    if not dimmable:
        return "on"
    percent = math.floor(raw / MAX_BRIGHTNESS * 100 + 0.5)
    return f"on at {percent}% (brightness {raw}/{MAX_BRIGHTNESS})"
