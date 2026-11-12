"""Base entity for Domestia lights."""

from typing import override

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MODEL, DOMAIN, MANUFACTURER
from .coordinator import DomestiaCoordinator, LightConfig


class DomestiaEntity(CoordinatorEntity[DomestiaCoordinator]):
    """An entity for one configured light, on its own device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DomestiaCoordinator, light: LightConfig) -> None:
        """Create the entity for a light."""
        super().__init__(coordinator)
        self.light = light
        # Keyed on the subentry, so moving a light to another relay keeps its
        # entity, device and history.
        self._attr_unique_id = light.subentry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, light.subentry_id)},
            name=light.name,
            manufacturer=MANUFACTURER,
            model=coordinator.config_entry.data.get(CONF_MODEL),
            via_device_id=coordinator.controller_device_id,
        )

    @property
    @override
    def available(self) -> bool:
        """Return whether the controller reported this light's relay."""
        return super().available and self.light.relay in self.coordinator.data
