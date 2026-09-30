"""Time entity MaR – časy oken režimu Den/noc (start/stop pro každé z 5 oken).

Každé okno = dvojice time entit. start==stop -> okno vypnuté (viz DenNocController).
Hodnotu tlačí přímo do DenNocControlleru přes setter; po restartu obnoví uloženou
hodnotu (RestoreEntity, parse "HH:MM:SS") a protlačí ji do controlleru, aby ji měl
k dispozici dřív, než po startu zreconciluje železo.

Výchozí: 1. okno 06:00–22:00 („den"), ostatní 00:00 == 00:00 (vypnutá). Master
přepínač je ale default OFF, takže se nic neděje, dokud ho uživatel nezapne.
"""

from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DENNOC_DEFAULT_START,
    DENNOC_DEFAULT_STOP,
    DENNOC_MAX_WINDOWS,
    DENNOC_TIME_START,
    DENNOC_TIME_STOP,
    DOMAIN,
    OKNA_DEFAULT_START,
    OKNA_DEFAULT_STOP,
    OKNA_MAX_WINDOWS,
    OKNA_TIME_START,
    OKNA_TIME_STOP,
    device_info,
)


def _parse(s: str) -> dt_time | None:
    try:
        hh, mm, *rest = s.split(":")
        return dt_time(int(hh), int(mm), int(rest[0]) if rest else 0)
    except (ValueError, AttributeError, IndexError):
        return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    dennoc = data["dennoc"]
    okna = data["okna"]
    entities: list = []
    for i in range(DENNOC_MAX_WINDOWS):
        n = i + 1
        # 1. okno má smysluplný default „den", zbytek vypnutá (00:00 == 00:00)
        d_start = DENNOC_DEFAULT_START if i == 0 else "00:00:00"
        d_stop = DENNOC_DEFAULT_STOP if i == 0 else "00:00:00"
        # názvy bez „/" a pomlček -> čistá id time.mar_dennoc_okno_{n}_od / _do
        entities.append(
            DenNocTime(dennoc, entry, i, "start",
                       DENNOC_TIME_START.format(n=n), f"Dennoc okno {n} od", d_start)
        )
        entities.append(
            DenNocTime(dennoc, entry, i, "stop",
                       DENNOC_TIME_STOP.format(n=n), f"Dennoc okno {n} do", d_stop)
        )
    for i in range(OKNA_MAX_WINDOWS):
        n = i + 1
        # 1. okno default „noční pokles" 22:00–06:00, zbytek vypnutá (00:00==00:00)
        d_start = OKNA_DEFAULT_START if i == 0 else "00:00:00"
        d_stop = OKNA_DEFAULT_STOP if i == 0 else "00:00:00"
        # -> time.mar_okna_teploty_{n}_od / _do
        entities.append(
            OknaTeplotyTime(okna, entry, i, "start",
                            OKNA_TIME_START.format(n=n), f"Okna teploty {n} od", d_start)
        )
        entities.append(
            OknaTeplotyTime(okna, entry, i, "stop",
                            OKNA_TIME_STOP.format(n=n), f"Okna teploty {n} do", d_stop)
        )
    async_add_entities(entities)


class DenNocTime(RestoreEntity, TimeEntity):
    """Jeden čas okna Den/noc (start nebo stop). Tlačí minuty do controlleru."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-time-four-outline"

    def __init__(self, dennoc, entry: ConfigEntry, index: int, which: str,
                 key: str, name: str, default: str) -> None:
        self._dennoc = dennoc
        self._index = index
        self._which = which
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)
        self._attr_native_value = _parse(default)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            parsed = _parse(last.state)
            if parsed is not None:
                self._attr_native_value = parsed
        self._push()

    def _minutes(self) -> int | None:
        v = self._attr_native_value
        return None if v is None else v.hour * 60 + v.minute

    def _push(self) -> None:
        self._dennoc.set_window(self._index, self._which, self._minutes())

    async def async_set_value(self, value: dt_time) -> None:
        self._attr_native_value = value
        self._push()
        self.async_write_ha_state()
        await self._dennoc.async_apply_config()


class OknaTeplotyTime(RestoreEntity, TimeEntity):
    """Jeden čas okna „Okna +/- teploty" (od / do). Živá změna se VALIDUJE proti
    překryvu s ostatními platnými okny; při kolizi se ZAMÍTNE (hodnota skočí zpět
    a přijde notifikace). Restore a defaulty se tlačí bez validace (jsou to už
    dřív ověřené hodnoty)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-time-four-outline"

    def __init__(self, okna, entry: ConfigEntry, index: int, which: str,
                 key: str, name: str, default: str) -> None:
        self._okna = okna
        self._index = index
        self._which = which
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)
        self._attr_native_value = _parse(default)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            parsed = _parse(last.state)
            if parsed is not None:
                self._attr_native_value = parsed
        # restore bez validace (dřív uložené, ověřené hodnoty)
        self._okna.set_window(self._index, self._which, self._minutes())

    def _minutes(self) -> int | None:
        v = self._attr_native_value
        return None if v is None else v.hour * 60 + v.minute

    async def async_set_value(self, value: dt_time) -> None:
        minute = value.hour * 60 + value.minute
        # VALIDACE: zkus zapsat přes controller; False = překryv -> zamítni
        if not self._okna.try_set_window(self._index, self._which, minute):
            from homeassistant.components import persistent_notification

            persistent_notification.async_create(
                self.hass,
                f"Čas okna {self._index + 1} by se překrýval s jiným oknem "
                f"„Okna +/- teploty“. Změna byla zamítnuta – okna se nesmí "
                f"překrývat (delty by se sčítaly).",
                title="MaR – překryv oken",
                notification_id=f"mar_okna_overlap_{self._index}",
            )
            # skoč zpět na starou hodnotu (nech UI konzistentní s controllerem)
            self.async_write_ha_state()
            return
        self._attr_native_value = value
        self.async_write_ha_state()
        await self._okna.async_apply_config()
