"""Sdílený primitiv: běží / stojí kompresor (event-driven, debounced).

Jeden odvozený stav, který si sám vybere nejlepší dostupný signál. Vše ostatní
(mar_stav teď, kameny START/STOP později) ho jen ČTE – logika žije na jednom místě.

Proč ne na coordinatoru: coordinator tiká jednou za 600 s. Debounce v sekundách a
pozdější kameny s timeouty v minutách na desetiminutový tik nedosáhnou. Proto
samostatný objekt se state-change listenery na podkladové acond entity + vlastní
dispatcher signál (signal_run_updated), na který si mar_stav sedne.

Mini-safe: každé čtení defenzivní (chybí entita / unavailable -> None, nikdy pád).
Žebřík bere nejlepší dostupný signál: 30045 bit 0 -> RPM -> příkon. Na full i mini
je bit 0 přítomen, takže fallbacky (RPM/příkon) jsou jen pojistka pro cizí firmware.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    ACOND_COMP_BIT,
    ACOND_FAN_BIT,
    ACOND_POWER,
    ACOND_PUMP_BIT,
    ACOND_RPM,
    P_BASE,
    P_MARGIN,
    RPM_MIN,
    RUN_DEBOUNCE_S,
    signal_run_updated,
)

_LOGGER = logging.getLogger(__name__)

_UNKNOWN = ("unknown", "unavailable", "", None)


class RunState:
    """Debounced 'kompresor běží' + 'potvrzeně stojí'. Jen čte, nikdy nezapisuje."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._run: bool | None = None          # debounced, vystavené 'běží'
        self._pending: bool | None = None       # cíl naplánované změny
        self._unsub = None                       # odběr state-change
        self._debounce_cancel = None             # zrušení naplánovaného commitu

    # ------------------------------------------------------------------ #
    # Veřejné čtení
    # ------------------------------------------------------------------ #
    @property
    def kompresor_bezi(self) -> bool | None:
        """True/False dle debounced žebříku; None dokud nevíme (chybí signál)."""
        return self._run

    @property
    def potvrzene_stoji(self) -> bool | None:
        """Kompresor i větrák i primární čerpadlo prokazatelně off (doběh hotov).
        None dokud to nevíme. Používá se až u kamene STOP; mar_stav ho nečte."""
        c = self._bit(ACOND_COMP_BIT)
        f = self._bit(ACOND_FAN_BIT)
        p = self._bit(ACOND_PUMP_BIT)
        if None in (c, f, p):
            # Bez komponentních bitů se o „potvrzeně stojí" nedá tvrdit nic silného;
            # vrať aspoň to, co víme z debounced běhu (běží=False -> pravděpodobně stojí).
            return (self._run is False) if self._run is not None else None
        return (not c) and (not f) and (not p)

    # ------------------------------------------------------------------ #
    # Životní cyklus
    # ------------------------------------------------------------------ #
    def async_start(self) -> None:
        """Navěsí listenery a spočítá počáteční stav (bez debounce, ať okno má hned pravdu)."""
        self._unsub = async_track_state_change_event(
            self.hass, [ACOND_COMP_BIT, ACOND_RPM, ACOND_POWER], self._on_change
        )
        self._recompute(initial=True)

    def shutdown(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._debounce_cancel is not None:
            self._debounce_cancel()
            self._debounce_cancel = None

    # ------------------------------------------------------------------ #
    # Čtení podkladu (defenzivní)
    # ------------------------------------------------------------------ #
    def _bit(self, entity_id: str) -> bool | None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNKNOWN:
            return None
        return st.state == "on"

    def _num(self, entity_id: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            return None

    def _raw(self) -> bool | None:
        """Okamžitý žebřík: 30045 bit 0 -> RPM > RPM_MIN -> příkon > P_BASE+rezerva."""
        b = self._bit(ACOND_COMP_BIT)
        if b is not None:
            return b
        rpm = self._num(ACOND_RPM)
        if rpm is not None:
            return rpm > RPM_MIN
        p = self._num(ACOND_POWER)
        if p is not None:
            return p > (P_BASE + P_MARGIN)
        return None

    # ------------------------------------------------------------------ #
    # Debounce
    # ------------------------------------------------------------------ #
    @callback
    def _on_change(self, event) -> None:  # noqa: ANN001
        self._recompute()

    def _recompute(self, initial: bool = False) -> None:
        raw = self._raw()
        if raw is None:
            return  # neznámé -> nic nemazat, drž poslední známé

        # počáteční hodnota při startu: nastav hned, bez čekání na debounce
        if initial and self._run is None:
            self._run = raw
            self._dispatch()
            return

        # raw se vrátil na aktuální hodnotu -> zruš čekající změnu
        if raw == self._run:
            self._cancel_pending()
            return

        # raw != committed -> naplánuj commit po RUN_DEBOUNCE_S, pokud už neplánujeme totéž
        if self._pending == raw and self._debounce_cancel is not None:
            return
        self._cancel_pending()
        self._pending = raw
        self._debounce_cancel = async_call_later(self.hass, RUN_DEBOUNCE_S, self._commit)

    def _cancel_pending(self) -> None:
        if self._debounce_cancel is not None:
            self._debounce_cancel()
            self._debounce_cancel = None
        self._pending = None

    @callback
    def _commit(self, _now) -> None:  # noqa: ANN001
        self._debounce_cancel = None
        target = self._pending
        self._pending = None
        if target is None:
            return
        # potvrď, že raw je po celou dobu debounce pořád target (jinak byl přechod falešný)
        if self._raw() == target:
            self._run = target
            self._dispatch()

    def _dispatch(self) -> None:
        async_dispatcher_send(self.hass, signal_run_updated(self.entry.entry_id))
