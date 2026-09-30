"""Statistické sensor entity MaR.

Tenké entity nad StatisticsAccumulator. Akumulátor není coordinator – entity se
po jeho přepočtu překreslí přes dispatcher signál (signal_stats_updated).

Vyrobené entity (device „MaR" -> entity_id sensor.mar_*)
-------------------------------------------------------
**Celoživotní** (od nasazení MaR, ne od narození jednotky):
  Elektrická energie:  mar_el_topeni / _tuv / _odmraz / _ostatni   (kWh, total_increasing)
  Tepelná energie:     mar_tep_topeni / _tuv / _odmraz / _ostatni  (kWh, total_increasing)
  COP:                 mar_cop_topeni / _tuv / _celkem             (poměr, measurement)
  Motohodiny:          mar_hodiny_topeni / _tuv / _odmraz          (h, total_increasing)

Energie i motohodiny jsou `total_increasing` -> spadnou do LTS „zadarmo navždy"
a visí na nich grafy ve view `statistika` / `statistika2`. **Nesahat**, jsou
kontrakt (rozhodnutí b).

**Periodové** – tři entity pro tabulku ve stylu Acondacu (rozhodnutí a, e):
  mar_stat_dnes · mar_stat_vcera · mar_stat_7dni

Proč atributy a ne ~60 samostatných entit: tabulka má 19 řádků × 3 sloupce.
Jako entity by to znamenalo 57 nových kontraktů v `entity_id`, 57 řádků
v recorderu a nesnesitelnou údržbu. Stav entity nese jen lidský popis období,
data jsou v atributech a markdown tabulka je čte přes `state_attr()`.

Stav je záměrně **bez času** (jen datum) – jinak by se měnil každou minutu a
zaplavil recorder. Přesný rozsah včetně času je v atributech `rozsah_od`
a `rozsah_do`, a je i v patičce exportovaného snímku.
"""

from __future__ import annotations

import datetime as dt

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from ..const import (
    MODE_DEFROST,
    MODE_HEAT,
    MODE_IDLE,
    MODE_TUV,
    PERIOD_TODAY,
    PERIOD_TOTAL,
    PERIOD_WEEK,
    PERIOD_YESTERDAY,
    STAT_TODAY_KEY,
    STAT_WEEK_KEY,
    STAT_YESTERDAY_KEY,
    device_info,
    signal_stats_updated,
)
from .accumulator import StatisticsAccumulator

_KIND_EL = "el"
_KIND_TEP = "tep"


def build_statistics_sensors(
    acc: StatisticsAccumulator, entry: ConfigEntry
) -> list[SensorEntity]:
    """Sestaví seznam statistických entit (montuje se v sensor.py)."""
    ents: list[SensorEntity] = []

    # --- Celoživotní (kontrakt 0.8.0, nesahat) ------------------------- #
    # Popisky volené tak, aby entity_id vyšlo přesně mar_<kind>_<mode>.
    mode_labels = [
        (MODE_HEAT, "topení"),
        (MODE_TUV, "TUV"),
        (MODE_DEFROST, "odmraz"),
        (MODE_IDLE, "ostatní"),
    ]
    for kind, kpre in ((_KIND_EL, "El."), (_KIND_TEP, "Tep.")):
        for mode, mlabel in mode_labels:
            ents.append(EnergySensor(acc, entry, kind, mode, f"{kpre} – {mlabel}"))

    # COP – topení, TUV, celkem. Odmraz NENÍ proces s účinností (nulový nebo
    # záporný čitatel), proto se nepočítá vůbec – ani jako nula.
    ents.append(CopSensor(acc, entry, MODE_HEAT, "COP – topení"))
    ents.append(CopSensor(acc, entry, MODE_TUV, "COP – TUV"))
    ents.append(CopSensor(acc, entry, None, "COP – celkem"))

    # Motohodiny – topení, TUV, odmraz. `ostatni` (prostoj) se neukazuje.
    for mode, mlabel in (
        (MODE_HEAT, "topení"),
        (MODE_TUV, "TUV"),
        (MODE_DEFROST, "odmraz"),
    ):
        ents.append(HoursSensor(acc, entry, mode, f"Hodiny – {mlabel}"))

    # --- Periodové (tabulka Acondacu) ---------------------------------- #
    ents.append(PeriodSensor(acc, entry, PERIOD_TODAY, STAT_TODAY_KEY, "Stat dnes"))
    ents.append(
        PeriodSensor(acc, entry, PERIOD_YESTERDAY, STAT_YESTERDAY_KEY, "Stat vcera")
    )
    ents.append(PeriodSensor(acc, entry, PERIOD_WEEK, STAT_WEEK_KEY, "Stat 7dni"))

    return ents


class _StatBase(SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        acc: StatisticsAccumulator,
        entry: ConfigEntry,
        key: str,
        name: str,
    ) -> None:
        self._acc = acc
        self._entry = entry
        self._attr_name = name
        # prefix _stat_ -> žádná kolize unique_id s entitami fáze 1
        self._attr_unique_id = f"{entry.entry_id}_stat_{key}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_stats_updated(self._entry.entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class EnergySensor(_StatBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, acc, entry, kind: str, mode: str, name: str) -> None:
        super().__init__(acc, entry, f"{kind}_{mode}", name)
        self._kind = kind
        self._mode = mode

    @property
    def native_value(self):
        return self._acc.energy(self._kind, self._mode, PERIOD_TOTAL)


class CopSensor(_StatBase):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:heat-pump-outline"
    _attr_suggested_display_precision = 2

    def __init__(self, acc, entry, mode: str | None, name: str) -> None:
        # mode None -> celkový COP
        super().__init__(acc, entry, f"cop_{mode or 'celkem'}", name)
        self._mode = mode

    @property
    def native_value(self):
        if self._mode is None:
            return self._acc.cop_total(PERIOD_TOTAL)
        return self._acc.cop(self._mode, PERIOD_TOTAL)


class HoursSensor(_StatBase):
    """Motohodiny per režim.

    POZOR na dvojí význam kbelíku `ostatni`: u energie má být trvale ~0 (růst =
    rozchod čtení bitů se strojem), u motohodin je to legitimní prostoj. Proto
    se `ostatni` jako motohodina vůbec nevystavuje – aby si to nikdo nepřečetl
    jako závadu.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 1

    def __init__(self, acc, entry, mode: str, name: str) -> None:
        super().__init__(acc, entry, f"hodiny_{mode}", name)
        self._mode = mode

    @property
    def native_value(self):
        return self._acc.hours(self._mode, PERIOD_TOTAL)


class PeriodSensor(_StatBase):
    """Jedno období tabulky. Stav = popis rozsahu, data = atributy.

    Bez `state_class` i `device_class` schválně: není to měřená veličina, je to
    nosič dat pro tabulku. Kdyby to mělo `total_increasing`, HA by z denně se
    nulujícího čísla dělal nesmysly v LTS.
    """

    _attr_icon = "mdi:table"

    def __init__(
        self, acc, entry, period: str, key: str, name: str
    ) -> None:
        super().__init__(acc, entry, key, name)
        self._period = period

    @property
    def native_value(self) -> str | None:
        start, end = self._acc.period_range(self._period)
        if start is None:
            return None
        if self._period == PERIOD_WEEK:
            if end is None:
                return None
            last = (end - dt.timedelta(days=1)).date()
            return f"{start:%d.%m.}\u2013{last:%d.%m.%Y}"
        if self._period == PERIOD_TOTAL:
            return f"od {start:%d.%m.%Y}"
        return f"{start:%d.%m.%Y}"

    @property
    def extra_state_attributes(self) -> dict:
        return self._acc.period_attributes(self._period)
