"""Platforma datetime – interval režimu Dovolená (od / do = datum + hodina).

-> datetime.mar_dovolena_od / datetime.mar_dovolena_do
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, DOVOLENA_DEFAULT_DT, device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    dovolena = hass.data[DOMAIN][entry.entry_id]["dovolena"]
    async_add_entities(
        [
            DovolenaDateTime(dovolena, entry, "from", "Dovolena od"),
            DovolenaDateTime(dovolena, entry, "to", "Dovolena do"),
        ]
    )


def _parse_default() -> datetime:
    naive = datetime.strptime(DOVOLENA_DEFAULT_DT, "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)


class DovolenaDateTime(RestoreEntity, DateTimeEntity):
    """Jeden okraj intervalu dovolené. Hodnotu tlačí do DovolenaControlleru; ten si
    z (od < do) sám určí platnost. Přežije restart (RestoreEntity)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, dovolena, entry: ConfigEntry, which: str, name: str) -> None:
        self._dovolena = dovolena
        self._which = which  # "from" | "to"
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_dovolena_{which}"
        self._attr_device_info = device_info(entry.entry_id)
        self._attr_native_value = _parse_default()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unknown", "unavailable", ""):
            parsed = dt_util.parse_datetime(last.state)
            if parsed is not None:
                self._attr_native_value = dt_util.as_local(parsed)
        self._push()

    def _push(self) -> None:
        v = self._attr_native_value
        if self._which == "from":
            self._dovolena.set_from(v)
        else:
            self._dovolena.set_to(v)

    @property
    def native_value(self) -> datetime | None:
        return self._attr_native_value

    async def async_set_value(self, value: datetime) -> None:
        # HA předává hodnotu v UTC (aware) -> ulož v lokále pro čitelnost/porovnání
        self._attr_native_value = dt_util.as_local(value)
        self._push()
        self.async_write_ha_state()
        await self._dovolena.async_apply_config()
