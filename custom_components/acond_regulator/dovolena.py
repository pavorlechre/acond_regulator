"""Režim „Dovolená" (Patro 2, OSA B – executor na 40001 + 40005).

Jeden dlouhý interval (datum + hodina od/do), po který MaR drží ABSOLUTNÍ cíle:
- teplotu místnosti (`number.acond_40001_t_set_indoor1`) na útlumu (např. 16 °C),
- teplotu TUV (`number.acond_40005_t_set_tuv`) níž (např. 40 °C).
Po skončení intervalu obě hodnoty vrátí na původní (půjč-a-vrať, base ve Store,
přežije restart – stejný kontrakt jako Okna +/- teploty).

Termální efekt na TOPENÍ jde přes ekvitermní korekci do zpátečky, takže platí jen
v Ekvitermě – což vstupní guard zajišťuje (dovolenou nepustí mimo Ekvitermu).
TUV část (40005) NEjde přes ekvitermu, funguje tedy vždy: na dovolené se voda
nehřeje zbytečně nadoraz.

VSTUPNÍ GUARD (rozhodnutí s Pavlem)
-----------------------------------
Zapnout Dovolenou lze jen když strategie == Ekviterma A Okna +/- teploty vyplá
A interval je platný (od < do). Jinak se zapnutí ZAMÍTNE + hláška. Symetricky
(řešeno v OknaTeplotySwitch a StrategySelect): když běží Dovolená, zamítne se
zapnutí Oken i přepnutí strategie pryč z Ekvitermy. Guard je jen na VSTUPU
(okamžik kliknutí); co uživatel změní za běhu jinými cestami, je jeho odpovědnost.
Den/noc a Zebra se NEhlídají (na dovolené můžou prospět).

Protože je Dovolená vstupně výlučná vůči Oknům, oba režimy se nikdy nepotkají nad
40001 – kontrakt půjč-a-vrať tak zůstává jednovýpůjčitelový a čistý.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACOND_ROOM_SET,
    ACOND_TUV_SET,
    DOMAIN,
    DOVOLENA_RETRY_S,
    DOVOLENA_ROOM_DEFAULT,
    DOVOLENA_ROOM_MAX,
    DOVOLENA_ROOM_MIN,
    DOVOLENA_STORE_VERSION,
    DOVOLENA_TUV_DEFAULT,
    DOVOLENA_TUV_MAX,
    DOVOLENA_TUV_MIN,
    STRATEGY_EKVITERM,
    signal_dovolena_updated,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN = ("unknown", "unavailable", "", None)


class DovolenaController:
    """Jeden interval dovolené. Půjčuje/vrací setpoint místnosti (40001) i TUV (40005)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, okna, fve=None) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.okna = okna  # kvůli vstupnímu guardu (Okna musí být vyplá)
        self.fve = fve    # kvůli vstupnímu guardu (FV programy musí být vyplé)
        self._store: Store = Store(
            hass, DOVOLENA_STORE_VERSION, f"{DOMAIN}_dovolena_{entry.entry_id}"
        )

        self.enabled = False
        self.topeni = None          # nastaví __init__ po konstrukci topeni (OR-brána guardu)
        self._from: datetime | None = None
        self._to: datetime | None = None
        self.room_target = float(DOVOLENA_ROOM_DEFAULT)
        self.tuv_target = float(DOVOLENA_TUV_DEFAULT)
        self._unsub_timer = None

        # --- stav výpůjčky (dva registry) ---
        self._base_room: float | None = None
        self._base_tuv: float | None = None
        self._active = False
        self._last_room: float | None = None
        self._last_tuv: float | None = None

    # ------------------------------------------------------------------ #
    # Nastavení z entit
    # ------------------------------------------------------------------ #
    def set_from(self, value: datetime | None) -> None:
        self._from = value

    def set_to(self, value: datetime | None) -> None:
        self._to = value

    def set_room_target(self, value: float) -> None:
        self.room_target = float(value)

    def set_tuv_target(self, value: float) -> None:
        self.tuv_target = float(value)

    def interval_valid(self) -> bool:
        """Interval platný = obě meze nastavené a od < do."""
        return self._from is not None and self._to is not None and self._from < self._to

    # ------------------------------------------------------------------ #
    # Vstupní guard
    # ------------------------------------------------------------------ #
    def can_enable(self) -> tuple[bool, str]:
        """Smí se Dovolená právě teď zapnout? -> (ok, důvod pro hlášku)."""
        if self.coordinator.strategy != STRATEGY_EKVITERM:
            return (False, "Nejdřív přepni strategii na Ekviterma.")
        if self.okna.enabled:
            return (False, "Nejdřív vypni Okna +/− teploty.")
        if (self.fve is not None and self.fve.active) or (
            self.topeni is not None and self.topeni.active
        ):
            return (False, "Nejdřív vypni FV programy.")
        if not self.interval_valid():
            return (False, "Nastav platný interval (Od musí být dřív než Do).")
        return (True, "")

    async def async_set_enabled(self, value: bool) -> None:
        """Zapnout/vypnout master. Zapnutí je hlídané vstupním guardem – volající
        switch si `can_enable()` ověří ještě před tímto voláním a případně zamítne."""
        self.enabled = bool(value)
        await self.async_reconcile()
        self._notify()

    async def async_apply_config(self) -> None:
        await self.async_reconcile()
        self._notify()

    # ------------------------------------------------------------------ #
    # Reconcile + půjč-a-vrať
    # ------------------------------------------------------------------ #
    async def async_reconcile(self) -> None:
        if not self.enabled or not self.interval_valid():
            await self._release()
            self._cancel_timer()
            return

        now = dt_util.now()
        # natáhni časovač na příští hranu (start nebo konec), i mimo Standard
        if now < self._from:
            self._arm_timer(self._from)
        elif self._from <= now < self._to:
            self._arm_timer(self._to)
        else:
            # interval už celý proběhl -> vrať a ukliď
            await self._release()
            self._cancel_timer()
            return

        # gate: mimo Standard je celý MaR hluchý -> vrať a nesahej
        if not self.coordinator.is_standard():
            await self._release()
            return

        if self._from <= now < self._to:
            await self._apply()
        else:
            await self._release()  # ještě nezačalo

    async def _apply(self) -> None:
        """Uvnitř intervalu: zachyť base obou registrů (poprvé) a drž cíle."""
        if not self._active:
            base_r = self._num(ACOND_ROOM_SET)
            base_t = self._num(ACOND_TUV_SET)
            if base_r is None or base_t is None:
                # registry zatím nedostupné (po startu) -> zkus později
                self._schedule_retry()
                return
            self._base_room = base_r
            self._base_tuv = base_t
            self._active = True
            await self._save()
            _LOGGER.debug(
                "Dovolená: půjčka base_room=%s base_tuv=%s", base_r, base_t
            )

        await self._write(ACOND_ROOM_SET, self._clamp_room(self.room_target), "room")
        await self._write(ACOND_TUV_SET, self._clamp_tuv(self.tuv_target), "tuv")
        await self.coordinator.async_request_refresh()

    async def _release(self) -> None:
        """Vrať oba uživatelovy originály (držíme-li půjčku) a ukliď."""
        if self._active:
            if self._base_room is not None:
                await self._write(ACOND_ROOM_SET, self._base_room, "room")
            if self._base_tuv is not None:
                await self._write(ACOND_TUV_SET, self._base_tuv, "tuv")
            await self.coordinator.async_request_refresh()
            _LOGGER.debug(
                "Dovolená: vráceno room=%s tuv=%s", self._base_room, self._base_tuv
            )
        self._active = False
        self._base_room = None
        self._base_tuv = None
        self._last_room = None
        self._last_tuv = None
        await self._clear_store()

    async def async_resume_if_needed(self) -> None:
        """Po startu obnov nevrácenou půjčku ze Store a zreconciluj."""
        data = await self._store.async_load()
        if data and data.get("active"):
            self._active = True
            self._base_room = data.get("base_room")
            self._base_tuv = data.get("base_tuv")
            # na registrech je (z doby před restartem) cíl dovolené -> přednastav
            # _last_*, ať idempotentní reconcile uvnitř intervalu nepřepisuje
            self._last_room = self._clamp_room(self.room_target)
            self._last_tuv = self._clamp_tuv(self.tuv_target)
            _LOGGER.debug(
                "Dovolená: obnovena půjčka base_room=%s base_tuv=%s",
                self._base_room, self._base_tuv,
            )
        await self.async_reconcile()
        self._notify()

    def shutdown(self) -> None:
        self._cancel_timer()

    def as_attr(self) -> dict:
        return {
            "nazev": "Dovolená",
            "aktivni": self.enabled,
            "probiha": self._active,
            "od": self._from.isoformat() if self._from else None,
            "do": self._to.isoformat() if self._to else None,
            "cil_mistnost": self.room_target,
            "cil_tuv": self.tuv_target,
            "base_mistnost": self._base_room,
            "base_tuv": self._base_tuv,
            "interval_platny": self.interval_valid(),
        }

    # ------------------------------------------------------------------ #
    # Zápis
    # ------------------------------------------------------------------ #
    async def _write(self, entity_id: str, value: float, which: str) -> None:
        """Zápis do registru přes number.set_value, jen při reálné změně."""
        v = round(float(value), 1)
        last = self._last_room if which == "room" else self._last_tuv
        if last is not None and round(last, 1) == v:
            return
        if which == "room":
            self._last_room = v
        else:
            self._last_tuv = v
        await self.hass.services.async_call(
            "number", "set_value",
            {"entity_id": entity_id, "value": v},
            blocking=False,
        )

    @staticmethod
    def _clamp_room(value: float) -> float:
        return round(max(DOVOLENA_ROOM_MIN, min(DOVOLENA_ROOM_MAX, value)), 1)

    @staticmethod
    def _clamp_tuv(value: float) -> float:
        return round(max(DOVOLENA_TUV_MIN, min(DOVOLENA_TUV_MAX, value)), 1)

    def _num(self, entity_id: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            try:
                return float(st.attributes.get("temperature"))
            except (ValueError, TypeError):
                return None

    # ------------------------------------------------------------------ #
    # Časovač hran
    # ------------------------------------------------------------------ #
    def _schedule_retry(self) -> None:
        self._arm_timer(dt_util.utcnow() + timedelta(seconds=DOVOLENA_RETRY_S))

    def _arm_timer(self, when) -> None:
        self._cancel_timer()

        @callback
        def _fire(now) -> None:  # noqa: ANN001
            # @callback nutně: jinak async_create_task v executor vlákně tiše selže
            self._unsub_timer = None
            self.hass.async_create_task(self._on_edge())

        self._unsub_timer = async_track_point_in_time(self.hass, _fire, when)

    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    async def _on_edge(self) -> None:
        await self.async_reconcile()
        self._notify()

    # ------------------------------------------------------------------ #
    # Store + dispatch
    # ------------------------------------------------------------------ #
    async def _save(self) -> None:
        await self._store.async_save(
            {"active": self._active, "base_room": self._base_room, "base_tuv": self._base_tuv}
        )

    async def _clear_store(self) -> None:
        await self._store.async_save({})

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_dovolena_updated(self.entry.entry_id))
