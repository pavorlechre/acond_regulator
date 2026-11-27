"""Uvolnění stropu 40014 při ohřevu TUV – jen Acond řady PRO.

PROČ
----
Řada PRO (40014 ve W) drží strop výkonu i při ohřevu TUV. S nízkým stropem
(Minimum, R-Ekviterma, uživatelův slider 5 kW) se bojler hřeje 1,5–2 h a dům
mezitím netopí (data Roman a Herodes, listopad 2026). Grandis/Economis strop při
TUV ignoruje sám, tam se nic nedělá. Odmraz strop ignorují obě řady.

JAK (Pavle 15. 11. 2026)
------------------------
- Začátek TUV (bit 3) pod MaR (zápis zapnutý, strategie ≠ Bez MaR, Standard):
  ulož aktuální 40014 do Store a zapiš maximum (atribut max entity 40014).
- Konec: jakmile neběží TUV ani odmraz (odmraz uvnitř TUV je pokračování),
  zapiš uložený strop zpátky HNED – Acond po TUV jinak dohání naplno.
  Vrací se jen tehdy, když na slideru pořád je naše maximum; když ho mezitím
  někdo změnil (uživatel, jiná strategie), necháme jeho hodnotu.
- Odchod z MaR (Bez MaR, čistý stůl): na PRO zapiš maximum – běžná hodnota
  u lidí bez Modbusu, ať TUV i topení jedou jako bez MaR.
- Restart HA během TUV: uložený strop přežije ve Store a vrátí se po konci TUV.
- Ostatní smyčky (R, Minimum, FVE Topení) během TUV/odmrazu na 40014 nesahají,
  takže se s nimi nepereme; po TUV pokračují ze stropu, který tu vracíme.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from . import slider
from .const import (
    ACOND_BIT_DEFROST,
    ACOND_BIT_TUV,
    ACOND_CAPACITY,
    DOMAIN,
    STRATEGY_BEZ_MAR,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN = ("unknown", "unavailable", "", None)
_STORE_VERSION = 1


class TuvStropController:
    """Na PRO uvolní strop 40014 po dobu TUV a při odchodu z MaR."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._store: Store = Store(hass, _STORE_VERSION, f"{DOMAIN}_tuv_strop_{entry.entry_id}")
        self._orig: float | None = None    # strop před TUV (None = nic nedržíme)
        self._max: float | None = None     # co jsme zapsali
        self._busy = False
        self._unsubs: list = []

    # ------------------------------------------------------------------ #
    # Veřejné
    # ------------------------------------------------------------------ #
    @property
    def uvolneno(self) -> bool:
        return self._orig is not None

    def je_pro(self) -> bool:
        """Řada PRO = slider 40014 ve W (integrace Acond nastaví jednotku podle série)."""
        st = self.hass.states.get(ACOND_CAPACITY)
        return st is not None and st.attributes.get("unit_of_measurement") == "W"

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data and data.get("orig") is not None:
            self._orig = float(data["orig"])
            self._max = data.get("max")

    async def async_start(self) -> None:
        self._unsubs.append(async_track_state_change_event(
            self.hass, [ACOND_BIT_TUV, ACOND_BIT_DEFROST], self._on_event,
        ))
        await self._krok()

    def shutdown(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()

    async def async_odchod(self) -> None:
        """Odchod z MaR (Bez MaR / čistý stůl): na PRO slider na maximum.

        Volá se AŽ PO vrácení půjček (R, Minimum, FVE Topení), ať je zápis poslední."""
        await self._zapomen()
        if not self.je_pro():
            return
        hi = self._attr("max")
        cur = self._num()
        if hi is None or (cur is not None and abs(cur - hi) < 1.0):
            return            # už je na maximu -> nepiš zbytečně (EEPROM)
        await slider.async_vrat(self.hass, ACOND_CAPACITY, hi, "TUV strop – odchod z MaR")
        await self.coordinator.async_request_refresh()
        _LOGGER.debug("TUV strop: odchod z MaR, slider na maximum %s", hi)

    # ------------------------------------------------------------------ #
    # Smyčka
    # ------------------------------------------------------------------ #
    @callback
    def _on_event(self, _event) -> None:  # noqa: ANN001
        self.hass.async_create_task(self._krok())

    def _pod_mar(self) -> bool:
        c = self.coordinator
        return (
            c.write_enabled
            and c.strategy != STRATEGY_BEZ_MAR
            and c.is_standard()
        )

    async def _krok(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            if self._neznamy(ACOND_BIT_TUV) or self._neznamy(ACOND_BIT_DEFROST):
                return            # bity ještě nenaběhly (start HA) -> nerozhoduj naslepo
            tuv = self._on(ACOND_BIT_TUV)
            odmraz = self._on(ACOND_BIT_DEFROST)

            if self._orig is None:
                # začátek TUV -> uvolni
                if tuv and self._pod_mar() and self.je_pro():
                    cur = self._num()
                    hi = self._attr("max")
                    if cur is None or hi is None:
                        return
                    self._orig, self._max = cur, hi
                    await self._save()
                    if cur < hi:
                        await self._set(hi)
                    _LOGGER.debug("TUV strop: TUV běží, strop %s -> %s", cur, hi)
                return

            # držíme uvolněno: konec až bez TUV i bez odmrazu
            if tuv or odmraz:
                return
            cur = self._num()
            if cur is None:
                return            # slider nedostupný (restart Acondu) -> počkej
            orig = self._orig
            nas = self._max is not None and abs(cur - float(self._max)) < 1.0
            await self._zapomen()
            if nas and self._pod_mar():
                await slider.async_vrat(self.hass, ACOND_CAPACITY, orig, "TUV strop", nula_max=True)
                await self.coordinator.async_request_refresh()
                _LOGGER.debug("TUV strop: konec TUV, strop zpět na %s", orig)
            else:
                _LOGGER.debug("TUV strop: konec TUV, slider změněn (%s) – nevracím", cur)
        finally:
            self._busy = False

    async def _zapomen(self) -> None:
        if self._orig is None:
            return
        self._orig = self._max = None
        await self._save()

    async def _save(self) -> None:
        await self._store.async_save({"orig": self._orig, "max": self._max})

    async def _set(self, value: float) -> None:
        await self.hass.services.async_call(
            "number", "set_value", {"entity_id": ACOND_CAPACITY, "value": value}, blocking=True,
        )
        await self.coordinator.async_request_refresh()

    def _on(self, entity_id: str) -> bool:
        st = self.hass.states.get(entity_id)
        return st is not None and st.state == "on"

    def _neznamy(self, entity_id: str) -> bool:
        st = self.hass.states.get(entity_id)
        return st is None or st.state in _UNKNOWN

    def _num(self) -> float | None:
        st = self.hass.states.get(ACOND_CAPACITY)
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            return None

    def _attr(self, attr: str) -> float | None:
        st = self.hass.states.get(ACOND_CAPACITY)
        if st is None:
            return None
        try:
            return float(st.attributes.get(attr))
        except (ValueError, TypeError):
            return None
