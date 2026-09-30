"""Strategie Minimum – event-driven smyčka (ne coordinator).

Drží požadovanou zpátečku (40008) kousek POD skutečnou (30009), takže se TČ vidí
„nasycené" a stáhne se na minimální otáčky. Reaguje na každou změnu skutečné
zpátečky – proto vlastní smyčka na state-change, ne desetiminutový coordinator
(na tom by to „chcíplo").

Zápisy šetří pásmem: přepíše 40008 = skutečná + MIN_TARGET_DIFF jen když se rozdíl
(cíl − skutečná) vychýlí z pásma ⟨−1,7 ; −0,7⟩, a ne častěji než MIN_WRITE_BRAKE_S.
BEZ stropu na ekvitermě – uživatel chce topit trvale na minimum (třeba přebytky FVE),
níž si přidá Zebrou.

Původní registry (40008 i 40014) se ukládají do Store -> Ukonči je vrátí správně
i po restartu HA uprostřed režimu.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store

from .const import (
    ACOND_BIT_TUV,
    ACOND_CAPACITY,
    ACOND_COMP_BIT,
    ACOND_RETURN_ACT,
    ACOND_RETURN_READBACK,
    ACOND_SUMMER,
    ACOND_TARGET,
    DOMAIN,
    MIN_BAND,
    MIN_SAFETY_TICK_S,
    MIN_TARGET_DIFF,
    MIN_WRITE_BRAKE_S,
    MINIMUM_STORE_VERSION,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN = ("unknown", "unavailable", "", None)


class MinimumController:
    """Smyčka strategie Minimum. Aktivuje/deaktivuje ji select."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.seq = None                       # SequenceRunner – doplní __init__ (N18)
        self._store: Store = Store(hass, MINIMUM_STORE_VERSION, f"{DOMAIN}_minimum_{entry.entry_id}")
        self._orig: dict | None = None       # {"t40008":x, "t40014":y}
        self.mode: str | None = None          # "minimum" / "boost" / None
        self._unsub = None
        self._unsub_tick = None
        self._last_write_ts = 0.0
        self.active = False

    # ------------------------------------------------------------------ #
    @property
    def current_target(self) -> float | None:
        """Co Minimum drží na registru (pro okno). None dokud první zápis."""
        return self.coordinator.minimum_target

    def wanted_target(self) -> float | None:
        """Cíl, který Minimum PRÁVĚ chce na 40008 (skutečná − 1,2). None když neaktivní
        nebo chybí čtení. Používá kámen na konci: místo slepého vrácení původní
        zpátečky předá kormidlo rovnou Minimu, ať PZ nezůstane pod hysterezí a
        kompresor po náběhu nezhasne (přesně to, co Pavle chytil na železe)."""
        if not self.active or self.mode != "minimum":
            return None
        actual = self._num(ACOND_RETURN_ACT)
        if actual is None:
            return None
        return self._s_mezi(round(actual + MIN_TARGET_DIFF, 1))

    def _s_mezi(self, value: float) -> float:
        """Nikdy pod spodní mez registru zpátečky. Mez se čte z entity Acond
        (liší se podle stroje). Studená soustava po výpadku by jinak dala
        „skutečná − 1,2" pod mez a Acond by zápis odmítl s chybovou hláškou."""
        lo = self._attr(ACOND_TARGET, "min")
        return round(max(value, lo), 1) if lo is not None else value

    def note_external_write(self, value: float) -> None:
        """Kámen právě zapsal tuhle hodnotu za Minimum -> srovnej bookkeeping:
        posuň brzdu (ať Minimum hned nedublíruje zápis) a ukaž cíl v okně."""
        self._last_write_ts = time.monotonic()
        self.coordinator.minimum_target = value

    # ------------------------------------------------------------------ #
    # Aktivace / deaktivace (volá select)
    # ------------------------------------------------------------------ #
    async def async_activate_minimum(self) -> None:
        """Minimum: slider na MIN, event-driven smyčka drží zpátečku pod skutečnou."""
        if self.active:
            return
        await self._save_originals("minimum")
        smin = self._attr(ACOND_CAPACITY, "min")
        if smin is not None:
            await self._set_number(ACOND_CAPACITY, smin)
        self._arm()
        self.active = True
        self._last_write_ts = 0.0
        await self._tick(force=True)
        _LOGGER.debug("Minimum aktivováno, orig=%s, slider->%s", self._orig, smin)

    async def async_activate_boost(self) -> None:
        """BOOST: slider na MAX; zpátečku (ekviterma+10) píše coordinator. Bez smyčky."""
        if self.active:
            return
        await self._save_originals("boost")
        smax = self._attr(ACOND_CAPACITY, "max")
        if smax is not None:
            await self._set_number(ACOND_CAPACITY, smax)
        self.active = True
        self.coordinator.minimum_target = None
        _LOGGER.debug("BOOST aktivováno, orig=%s, slider->%s", self._orig, smax)

    async def _save_originals(self, mode: str) -> None:
        t8 = self._num(ACOND_RETURN_READBACK)     # původní 40008 (readback 30008)
        # BĚŽÍ-LI KÁMEN: readback je zamořený jeho boostem / staženou zpátečkou
        # (kameny čekají na TUV klidně desítky minut) – uložil by se nesmysl a
        # deaktivace by ho „vrátila". Originál se v tu chvíli bere z ekvitermního
        # cíle coordinatoru (vždy rozumná kotva). Ruční hodnotu z „Bez MaR" to
        # nepoškodí: bez běžícího kamene se dál bere readback doslova.
        if self.seq is not None and self.seq.busy:
            data = self.coordinator.data
            rf = getattr(data, "return_final", None) if data else None
            if rf is not None:
                t8 = rf
        t14 = self._num(ACOND_CAPACITY)           # původní 40014 (slider)
        self._orig = {"t40008": t8, "t40014": t14}
        self.mode = mode
        await self._store.async_save({"active": True, "mode": mode, "orig": self._orig})

    async def async_restore_originals(self) -> None:
        """Načti originály ze Store bez nastartování smyčky. Volá čistý stůl při
        startu mimo Standard – ten běží dřív, než by resume originály načetl, a
        deaktivace by jinak neměla co vracet (slider by zůstal viset na minimu).
        U běžícího/prázdného stavu no-op."""
        if self.active or self._orig is not None:
            return
        data = await self._store.async_load()
        if data and data.get("active"):
            self._orig = data.get("orig")
            self.mode = data.get("mode")
            self.active = True

    async def async_deactivate(self) -> None:
        if not self.active and self._orig is None:
            return
        self._disarm()
        self.active = False
        self.mode = None
        # vrať původní registry
        if self._orig is not None:
            if self._orig.get("t40008") is not None:
                await self._set_number(ACOND_TARGET, self._orig["t40008"])
            if self._orig.get("t40014") is not None:
                await self._set_number(ACOND_CAPACITY, self._orig["t40014"])
        self._orig = None
        self.coordinator.minimum_target = None
        await self._store.async_save({"active": False, "mode": None, "orig": None})
        _LOGGER.debug("Sliderová strategie deaktivována, registry vráceny")

    async def async_resume_if_needed(self) -> None:
        """Po startu HA: když Store hlásí aktivní režim, znovu naskoč (bez přepisu orig)."""
        data = await self._store.async_load()
        if not data or not data.get("active"):
            return
        self._orig = data.get("orig")
        self.mode = data.get("mode")
        self.active = True
        if self.mode == "minimum":
            self._arm()
            self._last_write_ts = 0.0
            await self._tick(force=True)
        # BOOST: slider je na max z HW, zpátečku píše coordinator -> nic víc netřeba
        _LOGGER.debug("Sliderová strategie obnovena po restartu, mode=%s, orig=%s", self.mode, self._orig)

    def shutdown(self) -> None:
        self._disarm()

    # ------------------------------------------------------------------ #
    # Smyčka
    # ------------------------------------------------------------------ #
    def _arm(self) -> None:
        if self._unsub is None:
            self._unsub = async_track_state_change_event(
                self.hass, [ACOND_RETURN_ACT], self._on_change
            )
        # pomalý pojistný tik – tracuje i když se skutečná zrovna nehýbe
        if self._unsub_tick is None:
            self._unsub_tick = async_track_time_interval(
                self.hass, self._on_interval, timedelta(seconds=MIN_SAFETY_TICK_S)
            )

    def _disarm(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None

    @callback
    def _on_change(self, event) -> None:  # noqa: ANN001
        self.hass.async_create_task(self._tick())

    @callback
    def _on_interval(self, now) -> None:  # noqa: ANN001
        self.hass.async_create_task(self._tick())

    async def _tick(self, force: bool = False) -> None:
        if not self.active or self.mode != "minimum":
            return
        # PŘEDNOST KAMENŮ: běží Kámen START/STOP a sám vlastní 40008 -> uhni mu.
        # Bez tohohle by smyčka Minima přepsala boost Zapnout jistě zpět na
        # „skutečná − 1,2\", kompresor by boost nedostal a kámen by nenaskočil.
        # Zrcadlí gate v coordinatoru (ten suspend_write respektuje taky).
        if self.coordinator.suspend_write:
            return
        # mimo Standard je 40008 do prázdna -> nezapisuj (MaR je hluchý)
        if not self.coordinator.is_standard():
            return
        # pauza: léto / TUV (během TUV si registr stejně přepisuje firmware)
        if self._is_on(ACOND_SUMMER) or self._is_on(ACOND_BIT_TUV):
            return
        # pauza: vypnutý zápis (switch „Zápis zpátečky" off) – Minimum drží, jen nezapisuje
        if not self.coordinator.write_enabled:
            return
        # PŘEDNOST NETOPIT: útlum drží zpátečku na 20 °C (zapisuje coordinator).
        # Minimum mlčí, dokud útlum nezruší Topit. Jinak by Netopit v režimu
        # Útlum pod Minimem nic neudělal (ověřeno na železe).
        if self.coordinator.utlum_pz is not None:
            return
        # Stojící kompresor: nezapisuj. Na registru zůstane poslední hodnota a ta
        # funguje jako práh – až zpátečka klesne pod ni, stroj se rozjede sám a
        # Minimum se znovu chytí. (Po TUV vrací registr firmware sám.)
        if not self._is_on(ACOND_COMP_BIT):
            return
        actual = self._num(ACOND_RETURN_ACT)
        if actual is None:
            return
        target = self._s_mezi(round(actual + MIN_TARGET_DIFF, 1))

        on_reg = self._num(ACOND_RETURN_READBACK)
        # rozhodni, jestli přepsat: rozdíl (co je na registru − skutečná) mimo pásmo,
        # nebo force (aktivace/obnova), nebo ještě nic nezapsáno
        need = force or on_reg is None
        if not need and on_reg is not None:
            diff = on_reg - actual
            if diff < (MIN_TARGET_DIFF - MIN_BAND) or diff > (MIN_TARGET_DIFF + MIN_BAND):
                need = True
        if not need:
            return

        # brzda proti cukání
        now = time.monotonic()
        if not force and (now - self._last_write_ts) < MIN_WRITE_BRAKE_S:
            return

        self._last_write_ts = now
        self.coordinator.minimum_target = target
        await self._set_number(ACOND_TARGET, target)

    # ------------------------------------------------------------------ #
    # Nástroje
    # ------------------------------------------------------------------ #
    async def _set_number(self, entity_id: str, value: float) -> None:
        await self.hass.services.async_call(
            "number", "set_value",
            {"entity_id": entity_id, "value": round(float(value), 1)},
            blocking=True,
        )

    def _is_on(self, entity_id: str) -> bool:
        st = self.hass.states.get(entity_id)
        return st is not None and st.state == "on"

    def _num(self, entity_id: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            return None

    def _attr(self, entity_id: str, attr: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None:
            return None
        val = st.attributes.get(attr)
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
