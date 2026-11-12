"""Constants for the Domestia integration."""

import logging
from typing import Final

DOMAIN: Final = "domestia"
LOGGER = logging.getLogger(__package__)

MANUFACTURER: Final = "Domestia"
# Supported controller models. The model is only shown on the devices; it does
# not change how the controller is driven.
MODELS: Final = ("DMC-012-003", "DMC-008-001")

CONF_MODEL: Final = "model"
CONF_LIGHTS: Final = "lights"
CONF_RELAY: Final = "relay"
CONF_DIMMABLE: Final = "dimmable"
CONF_ALWAYS_ON: Final = "always_on"

SUBENTRY_TYPE_LIGHT: Final = "light"

MIN_RELAY: Final = 1
MAX_RELAY: Final = 255

# Poll interval in seconds. Every poll opens a new TCP connection to the
# controller, so a tiny interval would hammer it.
DEFAULT_SCAN_INTERVAL: Final = 2.0
MIN_SCAN_INTERVAL: Final = 0.25

# Consecutive failed polls before the lights are marked unavailable.
MAX_CONSECUTIVE_FAILURES: Final = 3

# Refreshes requested after commands within this many seconds are coalesced.
REFRESH_COOLDOWN: Final = 0.3
