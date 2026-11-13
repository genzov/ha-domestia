"""The Domestia integration."""

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry

from .api import DomestiaClient
from .const import CONF_MODEL, DOMAIN, MANUFACTURER
from .coordinator import DomestiaConfigEntry, DomestiaCoordinator

PLATFORMS = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: DomestiaConfigEntry) -> bool:
    """Set up a Domestia controller."""
    # The controller device, which each light device links to via via_device_id.
    controller = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=entry.data.get(CONF_MODEL),
        name="Domestia controller",
    )

    coordinator = DomestiaCoordinator(
        hass, entry, DomestiaClient(entry.data[CONF_HOST]), controller.id
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    # The only reload mechanism: options, reconfigure and light subentry
    # changes all fire this listener. Flows must not reload themselves.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: DomestiaConfigEntry
) -> None:
    """Reload the entry after its options, data or subentries changed."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: DomestiaConfigEntry) -> bool:
    """Unload a Domestia controller."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,  # noqa: ARG001
    entry: DomestiaConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Allow removing a light device whose subentry no longer exists.

    The controller device and devices of configured lights are kept: they are
    recreated on the next setup anyway.
    """
    return any(
        domain == DOMAIN
        and identifier != entry.entry_id
        and identifier not in entry.subentries
        for domain, identifier in device_entry.identifiers
    )
