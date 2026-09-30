"""Časový režim Den/noc (Patro 2, osa A – kameny). MÁ PŘEDNOST NAD ZEBROU.

Vnější časový rozvrh: v povolených oknech se smí topit (dle zvolené strategie),
mimo ně je TČ přepnuté na VYP (protizámraz běží dál). Přechody dělá **kameny**:
- vstup do okna  -> Kámen B (Zapnout jistě),
- odchod z okna  -> Kámen A (Vypnout šetrně).

Až 5 oken, stejný rozvrh každý den. Okno = dvojice časů (start, stop) v minutách
od půlnoci:
    start == stop  -> okno VYPNUTÉ (a řídí progresivní odkrývání v UI),
    start <  stop  -> okno v rámci dne (06:00–22:00),
    start >  stop  -> okno PŘES PŮLNOC (22:00–06:00).

**Bezstavový přes restart.** Nemá Store: „kde jsem" je čistá funkce hodin +
nastavených časů + master přepínače (ten se obnoví přes RestoreEntity switche).
Po startu jen zreconciluje železo (porovná „mám teď topit?" se skutečností) a
natáhne časovač na příští hranu. Idempotentní – kameny samy nic nedělají, když
už je železo ve správném stavu (Zapnout jistě: kompresor běží = netřeba;
Vypnout šetrně: TČ už stojí = netřeba).

**Vztah k Zebře.** Zebra smí cyklovat jen UVNITŘ okna. Přes hranice kameny NEpouští
Zebra, ale Den/noc:
- odchod z okna -> Den/noc pustí Vypnout šetrně a Zebru USPÍ (`async_window_closed`),
- vstup do okna -> Den/noc pustí Zapnout jistě a Zebru PROBUDÍ do čerstvého topení
  (`async_window_opened`).
Navíc Zebra konzultuje `may_heat_now()` v `_pause_ended` jako pojistku proti race
na hranici (kdyby jí doběhla pauza přesně v okamžiku zavření okna).

**Gate řízení (kdy Den/noc žene kameny):** master ON · aspoň jedno platné okno ·
Standard · strategie není BOOST/Bez MaR. `may_heat_now()` (háček pro Zebru) je
ale ČISTĚ o hodinách (Standard/strategii si Zebra hlídá sama) – vrací True i když
je master OFF nebo není žádné okno (pak Den/noc do ničeho nemluví).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import (
    ACOND_HP_ON,
    ACOND_SUMMER,
    DENNOC_MAX_WINDOWS,
    DENNOC_RETRY_S,
    OFFMODE_LETO,
    OFFMODE_PZ,
    STRATEGY_BEZ_MAR,
    STRATEGY_BOOST,
    signal_dennoc_updated,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN = ("unknown", "unavailable", "", None)


def _hhmm(minute: int | None) -> str | None:
    if minute is None:
        return None
    m = int(minute) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


class DenNocController:
    """Rozvrh oken den/noc. Přechody přes kameny, Zebru uspává/probouzí."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, seq, run) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.seq = seq
        self.run = run
        self._zebra = None  # doplní attach_zebra po konstrukci Zebry (kruh reference)

        self.enabled = False                                  # master přepínač
        # 5 oken: [start_min | None, stop_min | None]
        self.windows: list[list[int | None]] = [[None, None] for _ in range(DENNOC_MAX_WINDOWS)]
        self._unsub_timer = None

    def attach_zebra(self, zebra) -> None:
        """Zebra se konstruuje AŽ po Den/noc (drží ji kvůli háčku may_heat_now).
        Den/noc drží zpět Zebru, aby ji uměl uspat/probudit na hranicích okna."""
        self._zebra = zebra

    # ------------------------------------------------------------------ #
    # Nastavení z entit (switch + time)
    # ------------------------------------------------------------------ #
    async def async_set_enabled(self, value: bool) -> None:
        self.enabled = bool(value)
        await self.async_reconcile()
        self._notify()

    def set_window(self, index: int, which: str, minute: int | None) -> None:
        """Zapiš čas okna. `which` = 'start' | 'stop'. Nezápisný setter (volá se i
        při restore); reconcile spustí async_apply_config()."""
        if not (0 <= index < DENNOC_MAX_WINDOWS):
            return
        slot = 0 if which == "start" else 1
        self.windows[index][slot] = None if minute is None else int(minute) % 1440

    async def async_apply_config(self) -> None:
        """Zavolat po změně okna živě z UI: přepočti hranu, zreconciluj, překresli."""
        await self.async_reconcile()
        self._notify()

    # ------------------------------------------------------------------ #
    # Čtení stavu (okna, validita, „kde teď jsem")
    # ------------------------------------------------------------------ #
    def window_valid(self, index: int) -> bool:
        """Okno platné = obě meze nastavené a různé (start != stop)."""
        if not (0 <= index < DENNOC_MAX_WINDOWS):
            return False
        s, e = self.windows[index]
        return s is not None and e is not None and s != e

    def _valid_windows(self) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for i in range(DENNOC_MAX_WINDOWS):
            if self.window_valid(i):
                s, e = self.windows[i]
                out.append((int(s), int(e)))
        return out

    @staticmethod
    def _inside(win: tuple[int, int], m: float) -> bool:
        s, e = win
        if s < e:
            return s <= m < e
        # přes půlnoc (s > e); s == e vyloučeno validitou
        return m >= s or m < e

    def _inside_any(self, windows: list[tuple[int, int]], m: float) -> bool:
        return any(self._inside(w, m) for w in windows)

    @staticmethod
    def _now_min() -> float:
        now = dt_util.now()
        return now.hour * 60 + now.minute + now.second / 60.0

    def may_heat_now(self) -> bool:
        """Háček pro Zebru: SMÍ SE TEĎ topit dle hodin? Čistě rozvrh + hodiny.
        (Standard/strategii si Zebra hlídá zvlášť.) True když master OFF / žádné
        platné okno / jsme uvnitř nějakého okna."""
        if not self.enabled:
            return True
        windows = self._valid_windows()
        if not windows:
            return True
        return self._inside_any(windows, self._now_min())

    def inside_now(self) -> bool | None:
        """Pro okno: jsme teď v povoleném okně? None když Den/noc neaktivní."""
        if not self.enabled:
            return None
        windows = self._valid_windows()
        if not windows:
            return None
        return self._inside_any(windows, self._now_min())

    def as_attr(self) -> dict:
        """Kompaktní stav do atributu mar_stav."""
        windows = self._valid_windows()
        return {
            "aktivni": self.enabled,
            "uvnitr_okna": self.inside_now(),
            "pristi_zmena": self._next_edge_iso(windows),
            "oken_platnych": len(windows),
            "okna": [{"start": _hhmm(s), "stop": _hhmm(e)} for s, e in windows],
        }

    # ------------------------------------------------------------------ #
    # Reconcile + časovač hran
    # ------------------------------------------------------------------ #
    async def async_reconcile(self) -> None:
        """Srovnej železo s rozvrhem a natáhni časovač na příští hranu."""
        if not self.enabled:
            self._cancel_timer()
            return
        windows = self._valid_windows()
        if not windows:
            self._cancel_timer()
            return

        # časovač na příští hranu natahujeme VŽDY (i mimo Standard/BOOST), ať Den/noc
        # zase začne řídit, jakmile se gate otevře (návrat do Standardu / kompat. strategie)
        self._arm_next_edge(windows)

        # ...ale kameny žene jen když gate otevřen
        if not self.coordinator.is_standard():
            return
        if self.coordinator.strategy in (STRATEGY_BOOST, STRATEGY_BEZ_MAR):
            return

        desired = self._inside_any(windows, self._now_min())
        await self._apply(desired)

    async def _apply(self, desired: bool) -> None:
        """Sjednoť železo s rozvrhem. Kameny jsou idempotentní pro všechny režimy
        vypínání (VYP/Léto/PZ mají vlastní „netřeba"), takže stačí pustit fire dle
        desired. Kámen se ale PŘESKOČÍ, když je železo prokazatelně už v cílovém
        stavu (jinak každý reconcile uvnitř okna – změna strategie, offmodu, časů –
        pouští no-op kámen s 6 s suspend_write). POZOR na past Léto: kompresor může
        běžet kvůli TUV v letním režimu, a přeskočený kámen by pak nikdy nepřepnul
        na zimu -> start je no-op jen v zimě bez útlumu. Zebru synchronizuj vždy."""
        if self.seq.busy:
            # běží kámen (třeba dlouhý doběh / verify sezóny) – zkus později
            self._schedule_retry()
            return

        if desired:
            if not self._start_noop():
                self.seq.fire_start()         # kámen sám: netřeba, když už topí v zimě
            if self._zebra is not None:
                await self._zebra.async_window_opened()
        else:
            if not self._stop_noop():
                self.seq.fire_stop()          # kámen sám: netřeba, když už je v cílovém off-stavu
            if self._zebra is not None:
                await self._zebra.async_window_closed()

    def _start_noop(self) -> bool:
        """Zapnout jistě je jistý no-op: kompresor běží, NENÍ léto (jinak by kámen
        přepínal sezónu!) a neběží útlum PZ (jinak ho kámen ruší)."""
        return (
            self.run.kompresor_bezi is True
            and self._is_on(ACOND_SUMMER) is not True
            and self.coordinator.utlum_pz is None
        )

    def _stop_noop(self) -> bool:
        """Vypnout šetrně je jistý no-op podle zvoleného režimu vypínání:
        Léto -> už jsme v létě; Útlum PZ -> útlum už drží; VYP -> TČ už stojí."""
        om = self.seq.offmode
        if om == OFFMODE_LETO:
            return self._is_on(ACOND_SUMMER) is True
        if om == OFFMODE_PZ:
            return self.coordinator.utlum_pz is not None
        return self._is_on(ACOND_HP_ON) is False

    async def async_resume_if_needed(self) -> None:
        """Po startu: žádný uložený stav (bezstavový) – jen zreconciluj železo."""
        await self.async_reconcile()
        self._notify()

    def shutdown(self) -> None:
        self._cancel_timer()

    # ------------------------------------------------------------------ #
    # Časovač
    # ------------------------------------------------------------------ #
    def _edges(self, windows: list[tuple[int, int]]) -> list[int]:
        edges: set[int] = set()
        for s, e in windows:
            edges.add(s)
            edges.add(e)
        return sorted(edges)

    def _next_edge_dt(self, windows: list[tuple[int, int]]):
        edges = self._edges(windows)
        if not edges:
            return None
        now = dt_util.now()
        now_min = now.hour * 60 + now.minute + now.second / 60.0
        later = [e for e in edges if e > now_min]
        if later:
            target_min = later[0]
            add_days = 0
        else:
            target_min = edges[0]
            add_days = 1
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight + timedelta(days=add_days, minutes=target_min)

    def _next_edge_iso(self, windows: list[tuple[int, int]]) -> str | None:
        if not self.enabled or not windows:
            return None
        dt = self._next_edge_dt(windows)
        return dt.isoformat() if dt else None

    def _arm_next_edge(self, windows: list[tuple[int, int]]) -> None:
        when = self._next_edge_dt(windows)
        if when is None:
            self._cancel_timer()
            return
        self._arm_timer(when)

    def _schedule_retry(self) -> None:
        when = dt_util.utcnow() + timedelta(seconds=DENNOC_RETRY_S)
        self._arm_timer(when)

    def _arm_timer(self, when) -> None:
        self._cancel_timer()

        @callback
        def _fire(now) -> None:  # noqa: ANN001
            # MUSÍ být @callback: jinak HA spustí callback ve vlákně executoru,
            # kde async_create_task selže -> hrana se nezpracuje (tichý bug jako u Zebry).
            self._unsub_timer = None
            self.hass.async_create_task(self._on_edge())

        self._unsub_timer = async_track_point_in_time(self.hass, _fire, when)

    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    async def _on_edge(self) -> None:
        """Doběhl časovač hrany -> přepočti desired, aplikuj, natáhni další hranu."""
        await self.async_reconcile()
        self._notify()

    # ------------------------------------------------------------------ #
    # Nástroje
    # ------------------------------------------------------------------ #
    def _is_on(self, entity_id: str) -> bool | None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNKNOWN:
            return None
        return st.state == "on"

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_dennoc_updated(self.entry.entry_id))
