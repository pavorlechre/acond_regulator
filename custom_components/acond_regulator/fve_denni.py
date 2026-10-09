"""Denní čítače energie pro schéma: import, export a výroba „dnes“ (kWh).

Dashboard MaR nesmí jmenovat entity konkrétního střídače, proto má MaR vlastní
`sensor.mar_fve_import_dnes`, `…_export_dnes` a `…_vyroba_dnes`.

Varianta C (Pavle 9. 10. 2026):
  • výchozí: MaR sám sčítá okamžitý výkon ze zdrojů, které už má vyplněné
    (přetok: + = export, − = import; výroba panelů), o půlnoci nuluje,
    restart přežije (datum v atributu `den`);
  • nepovinně: uživatel vyplní vlastní denní senzor (text.mar_fve_zdroj_*_dnes),
    pak se zobrazuje ten. Výchozí hodnota pole je prázdná.

Sčítá se při každém překreslení FVE (změna přetoku, tik 30 s). Díra delší než
DENNI_MEZERA_S se nesčítá — výpadek zdroje nebo HA se neodhaduje. Stav se
zapisuje nejvýš jednou za DENNI_ZAPIS_S, ať se nezahlcuje databáze.
"""

from __future__ import annotations

import time
from datetime import date

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import DENNI_MEZERA_S, DENNI_ZAPIS_S, device_info, signal_fve_updated

_UNKNOWN = ("unknown", "unavailable", "", None)

IMPORT, EXPORT, VYROBA = "import", "export", "vyroba"


class FveDenniCitac(RestoreSensor):
    """Jeden denní čítač (kWh). `druh` = import / export / vyroba."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "kWh"
    _attr_suggested_display_precision = 2

    def __init__(self, fve, entry: ConfigEntry, key: str, name: str,
                 druh: str, icon: str) -> None:
        self._fve = fve
        self._entry = entry
        self._druh = druh
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)
        self._kwh = 0.0
        self._den: date = dt_util.now().date()
        self._last_w: float | None = None
        self._last_ts: float | None = None
        self._zapsano_ts = 0.0
        self._zapsano_val: float | None = None

    # ------------------------------------------------------------------ #
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        dnes = dt_util.now().date().isoformat()
        # obnova jen téhož dne (přes půlnoc vypnutý HA -> začni od nuly);
        # vlastní součet je v atributu, i když se zobrazoval cizí senzor
        if last is not None and last.attributes.get("den") == dnes:
            try:
                self._kwh = float(last.attributes.get("spocteno_kwh") or 0.0)
            except (TypeError, ValueError):
                self._kwh = 0.0
        self.async_on_remove(async_dispatcher_connect(
            self.hass, signal_fve_updated(self._entry.entry_id), self._on_update))
        self.async_on_remove(async_track_time_change(
            self.hass, self._o_pulnoci, hour=0, minute=0, second=0))
        self._sectene()

    # ------------------------------------------------------------------ #
    def _zdroj_vykonu(self) -> str:
        return self._fve.src_vyroba if self._druh == VYROBA else self._fve.src_pretok

    def _vlastni(self) -> str:
        return {IMPORT: self._fve.src_import_dnes, EXPORT: self._fve.src_export_dnes,
                VYROBA: self._fve.src_vyroba_dnes}[self._druh]

    def _cti_w(self) -> float | None:
        """Okamžitý výkon zdroje ve W (kW se přepočte)."""
        eid = self._zdroj_vykonu()
        st = self.hass.states.get(eid) if eid else None
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            v = float(st.state)
        except (TypeError, ValueError):
            return None
        if str(st.attributes.get("unit_of_measurement") or "").strip().lower() == "kw":
            v *= 1000.0
        return v

    def _cti_vlastni(self) -> float | None:
        eid = self._vlastni()
        st = self.hass.states.get(eid) if eid else None
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            v = float(st.state)
        except (TypeError, ValueError):
            return None
        if str(st.attributes.get("unit_of_measurement") or "").strip() == "Wh":
            v /= 1000.0
        return v

    def _podil(self, w: float) -> float:
        if self._druh == IMPORT:
            return max(0.0, -w)
        return max(0.0, w)

    def _sectene(self) -> None:
        """Přičti energii od minulého vzorku a vezmi nový vzorek."""
        dnes = dt_util.now().date()
        if dnes != self._den:                      # půlnoc mohla proklouznout
            self._den, self._kwh = dnes, 0.0
        ted = time.monotonic()
        if self._last_w is not None and self._last_ts is not None:
            dt = ted - self._last_ts
            if 0 < dt <= DENNI_MEZERA_S:
                self._kwh += self._podil(self._last_w) * dt / 3_600_000.0
        self._last_w = self._cti_w()
        self._last_ts = ted

    # ------------------------------------------------------------------ #
    @callback
    def _on_update(self) -> None:
        self._sectene()
        hodnota = self.native_value
        ted = time.monotonic()
        if hodnota != self._zapsano_val and ted - self._zapsano_ts >= DENNI_ZAPIS_S:
            self._zapis(hodnota, ted)

    @callback
    def _o_pulnoci(self, now) -> None:  # noqa: ANN001
        self._sectene()
        self._den, self._kwh = dt_util.now().date(), 0.0
        self._zapis(self.native_value, time.monotonic())

    def _zapis(self, hodnota, ted: float) -> None:
        self._zapsano_val, self._zapsano_ts = hodnota, ted
        self.async_write_ha_state()

    # ------------------------------------------------------------------ #
    @property
    def native_value(self) -> float | None:
        vlastni = self._cti_vlastni()
        if vlastni is not None:
            return round(vlastni, 2)
        return round(self._kwh, 2)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "den": self._den.isoformat(),
            "zdroj": self._vlastni() if self._cti_vlastni() is not None else "výpočet",
            "spocteno_kwh": round(self._kwh, 3),
        }
