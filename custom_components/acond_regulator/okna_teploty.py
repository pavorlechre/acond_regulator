"""Časový režim „Okna +/- teploty" (Patro 2, OSA B – executor na 40001).

Časová okna, která na svou dobu posunou POŽADOVANOU teplotu místnosti
(`number.acond_40001_t_set_indoor1`) o vlastní deltu:
- záporná delta = noční pokles (klasika, i když úsporu obvykle nepřinese),
- kladná delta = přednatopení (např. přes den z FVE).

Termální efekt jde přes korekci místnosti do zpátečky (40008), takže je reálně
účinný jen v ekvitermě (nižší setpoint -> menší korekce -> nižší zpátečka). Jinde
je zápis do 40001 inertní (posune se displej, topení se nehne). Výpůjčka přesto
běží **bez ohledu na strategii** (čistě oknem, jako Den/noc) – kvůli čistotě a
vysvětlitelnosti. Jediná vyšší brána je Typ regulace Standard (mimo něj je celý
MaR hluchý -> půjčku vrátíme a nesaháme).

KONTRAKT PŮJČ-A-VRAŤ (od 0.6.0 přes SPRÁVCE POKOJOVKY)
------------------------------------------------------
Osa A (ekviterma/zpátečka) na 40001 NIKDY nesahá. A nesahá na něj ani tenhle
režim: od zavedení souběhu s FVE Topením je jediným vlastníkem 40001 `pokojovka.py`.
Okna jsou jen jeho ZÁKAZNÍK:
- **náběžná hrana** okna -> `pokojovka.async_claim("okna", delta)`;
- **sestupná hrana** (nebo vypnutí masteru / odchod ze Standardu) -> `async_release`.

Originál (`base`) drží správce, jednou pro všechny zákazníky, a vrací ho až po
odhlášení posledního – proto se dvě současné půjčky (Okno + FVE Topení) nemůžou
navzájem přepsat. Store originálu se přestěhoval do správce (ten si starý klíč
při prvním startu jednou převezme, aby se nevrácená půjčka neztratila).

PROMISE MODEL
-------------
Slibujeme: „po uplynutí okna vrátíme hodnotu ze začátku okna" – a uděláme to
DOSLOVA. Žádná detekce „uživatel sáhl": když v okně sám změní 40001, náš restore
na konci okna to přepíše. To je přesně ten slib (uživatel ví, že běží pokles).

PŘEKRYV OKEN
------------
Zakázán už při ukládání (validace v `try_set_window`): v každém okamžiku platí
nejvýš jedno okno, takže se delty nikdy nesčítají. Test je wrap-aware (okno přes
půlnoc se rozbalí na dva intervaly) a intervaly jsou půlotevřené `[od, do)`, aby
dotyk (okno končí 06:00, další začíná 06:00) prošel jako sousedství, ne překryv.
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
    OKNA_DELTA_DEFAULT,
    OKNA_MAX_WINDOWS,
    POKOJOVKA_CLAIM_OKNA,
    signal_okna_updated,
)

_LOGGER = logging.getLogger(__name__)
_DAY = 1440


def _hhmm(minute: int | None) -> str | None:
    if minute is None:
        return None
    m = int(minute) % _DAY
    return f"{m // 60:02d}:{m % 60:02d}"


class OknaTeplotyController:
    """Rozvrh oken +/- teploty. Půjčuje/vrací setpoint místnosti (40001)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator,
                 pokojovka) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.pokojovka = pokojovka        # jediný vlastník 40001; my jsme zákazník

        self.enabled = False                                  # master přepínač
        # okna: [start_min | None, stop_min | None]
        self.windows: list[list[int | None]] = [[None, None] for _ in range(OKNA_MAX_WINDOWS)]
        self.deltas: list[float] = [float(OKNA_DELTA_DEFAULT) for _ in range(OKNA_MAX_WINDOWS)]
        self._unsub_timer = None

        # --- stav (originál 40001 drží správce, ne my) ---
        self._active_index: int | None = None      # index okna, jehož přání je přihlášené

    # ------------------------------------------------------------------ #
    # Nastavení z entit (switch + time + number)
    # ------------------------------------------------------------------ #
    async def async_set_enabled(self, value: bool) -> None:
        self.enabled = bool(value)
        await self.async_reconcile()
        self._notify()

    def set_window(self, index: int, which: str, minute: int | None) -> None:
        """Zapiš čas okna BEZ validace (restore/default push). Živá editace z UI
        jde přes `try_set_window` (validuje překryv)."""
        if not (0 <= index < OKNA_MAX_WINDOWS):
            return
        slot = 0 if which == "start" else 1
        self.windows[index][slot] = None if minute is None else int(minute) % _DAY

    def try_set_window(self, index: int, which: str, minute: int | None) -> bool:
        """Živá změna času okna z UI. Vrátí False, když by výsledné (platné) okno
        překrylo jiné platné okno -> volající time entita změnu ZAMÍTNE. True =
        uloženo do controlleru."""
        if not (0 <= index < OKNA_MAX_WINDOWS):
            return False
        slot = 0 if which == "start" else 1
        cand = list(self.windows[index])
        cand[slot] = None if minute is None else int(minute) % _DAY
        s, e = cand
        if self._would_overlap(index, s, e):
            return False
        self.windows[index] = cand
        return True

    def set_delta(self, index: int, value: float) -> None:
        if 0 <= index < OKNA_MAX_WINDOWS:
            self.deltas[index] = float(value)

    async def async_apply_config(self) -> None:
        """Zavolat po živé změně okna/delty z UI: přepočti hranu, zreconciluj,
        překresli. Změna delty za běhu okna se projeví hned (re-apply base+delta)."""
        await self.async_reconcile()
        self._notify()

    # ------------------------------------------------------------------ #
    # Validita oken a překryv
    # ------------------------------------------------------------------ #
    def window_valid(self, index: int) -> bool:
        """Okno platné = obě meze nastavené a různé (start != stop)."""
        if not (0 <= index < OKNA_MAX_WINDOWS):
            return False
        s, e = self.windows[index]
        return s is not None and e is not None and s != e

    def _valid_windows(self) -> list[tuple[int, int, int]]:
        out: list[tuple[int, int, int]] = []
        for i in range(OKNA_MAX_WINDOWS):
            if self.window_valid(i):
                s, e = self.windows[i]
                out.append((i, int(s), int(e)))
        return out

    @staticmethod
    def _expand(s: int, e: int) -> list[tuple[int, int]]:
        """Rozbal okno na půlotevřené intervaly [start, stop) v [0, 1440).
        Okno přes půlnoc (s > e) -> dva intervaly."""
        if s < e:
            return [(s, e)]
        return [(s, _DAY), (0, e)]  # s == e vyloučeno validitou

    @staticmethod
    def _iv_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
        """Průnik dvou půlotevřených intervalů [s,e). Dotyk (e1==s2) NENÍ překryv."""
        return a[0] < b[1] and b[0] < a[1]

    def _would_overlap(self, cand_index: int, s: int | None, e: int | None) -> bool:
        """Překrylo by kandidátní okno (s,e) jiné PLATNÉ okno? Neplatné okno
        (chybí mez / start==stop) se s ničím nepřekrývá."""
        if s is None or e is None or s == e:
            return False
        cand = self._expand(int(s), int(e))
        for i in range(OKNA_MAX_WINDOWS):
            if i == cand_index:
                continue
            os_, oe = self.windows[i]
            if os_ is None or oe is None or os_ == oe:
                continue
            other = self._expand(int(os_), int(oe))
            for ca in cand:
                for ob in other:
                    if self._iv_overlap(ca, ob):
                        return True
        return False

    # ------------------------------------------------------------------ #
    # „Kde teď jsem" (které okno platí)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _inside(win: tuple[int, int], m: float) -> bool:
        s, e = win
        if s < e:
            return s <= m < e
        return m >= s or m < e  # přes půlnoc

    @staticmethod
    def _now_min() -> float:
        now = dt_util.now()
        return now.hour * 60 + now.minute + now.second / 60.0

    def _active_window(self, windows: list[tuple[int, int, int]]) -> tuple[int, int] | None:
        """Vrať (index, delta) okna, uvnitř kterého teď jsme. Díky zákazu překryvu
        je nejvýš jedno; kdyby jich (starým uložením) bylo víc, vyhrává vyšší index."""
        m = self._now_min()
        best: int | None = None
        for (i, s, e) in windows:
            if self._inside((s, e), m) and (best is None or i > best):
                best = i
        if best is None:
            return None
        return (best, self._delta(best))

    def _delta(self, index: int | None) -> float:
        if index is None or not (0 <= index < OKNA_MAX_WINDOWS):
            return 0.0
        return float(self.deltas[index])

    def as_attr(self) -> dict:
        """Kompaktní stav do atributu mar_stav."""
        windows = self._valid_windows()
        ai = self._active_index if self.pokojovka.claim_of(POKOJOVKA_CLAIM_OKNA) is not None else None
        return {
            "nazev": "Okna +/- teploty",
            "aktivni": self.enabled,
            "aktivni_okno": (ai + 1) if ai is not None else None,
            "delta": self._delta(ai) if ai is not None else None,
            "base": self.pokojovka.base,
            "pristi_zmena": self._next_edge_iso(windows),
            "oken_platnych": len(windows),
            "okna": [
                {"od": _hhmm(s), "do": _hhmm(e), "delta": self._delta(i)}
                for (i, s, e) in windows
            ],
        }

    # ------------------------------------------------------------------ #
    # Reconcile + půjč-a-vrať
    # ------------------------------------------------------------------ #
    async def async_reconcile(self) -> None:
        """Srovnej výpůjčku s rozvrhem a natáhni časovač na příští hranu."""
        if not self.enabled:
            await self._release()
            self._cancel_timer()
            return
        windows = self._valid_windows()
        if not windows:
            await self._release()
            self._cancel_timer()
            return

        # časovač natahujeme VŽDY (i mimo Standard), ať režim zase začne řídit,
        # jakmile se brána otevře (návrat do Standardu)
        self._arm_next_edge(windows)

        # gate: mimo Standard je celý MaR hluchý -> vrať půjčku a nesahej
        if not self.coordinator.is_standard():
            await self._release()
            return

        await self._apply(self._active_window(windows))

    async def _apply(self, active: tuple[int, int] | None) -> None:
        """Sjednoť přání u správce pokojovky s rozvrhem."""
        if active is None:
            await self._release()
            return

        index, delta = active
        self._active_index = index
        # Originál si zachytí a hlídá správce – my jen řekneme, co chceme.
        await self.pokojovka.async_claim(POKOJOVKA_CLAIM_OKNA, delta)

    async def _release(self) -> None:
        """Odhlas přání. Originál vrátí správce, až se odhlásí i ostatní."""
        self._active_index = None
        await self.pokojovka.async_release(POKOJOVKA_CLAIM_OKNA)

    async def async_resume_if_needed(self) -> None:
        """Po startu jen zreconciluj: originál drží (a obnovuje) správce pokojovky.
        Platformy (switch/time/number) už obnovily master/časy/delty do controlleru."""
        await self.async_reconcile()
        self._notify()

    def shutdown(self) -> None:
        self._cancel_timer()

    # ------------------------------------------------------------------ #
    # Časovač hran
    # ------------------------------------------------------------------ #
    def _edges(self, windows: list[tuple[int, int, int]]) -> list[int]:
        edges: set[int] = set()
        for _, s, e in windows:
            edges.add(s)
            edges.add(e)
        return sorted(edges)

    def _next_edge_dt(self, windows: list[tuple[int, int, int]]):
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

    def _next_edge_iso(self, windows: list[tuple[int, int, int]]) -> str | None:
        if not self.enabled or not windows:
            return None
        dt = self._next_edge_dt(windows)
        return dt.isoformat() if dt else None

    def _arm_next_edge(self, windows: list[tuple[int, int, int]]) -> None:
        when = self._next_edge_dt(windows)
        if when is None:
            self._cancel_timer()
            return
        self._arm_timer(when)

    def _arm_timer(self, when) -> None:
        self._cancel_timer()

        @callback
        def _fire(now) -> None:  # noqa: ANN001
            # MUSÍ být @callback: jinak HA spustí callback ve vlákně executoru,
            # kde async_create_task tiše selže -> hrana se nezpracuje (bug jako u Zebry).
            self._unsub_timer = None
            self.hass.async_create_task(self._on_edge())

        self._unsub_timer = async_track_point_in_time(self.hass, _fire, when)

    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    async def _on_edge(self) -> None:
        """Doběhl časovač hrany -> přepočti výpůjčku, aplikuj, natáhni další hranu."""
        await self.async_reconcile()
        self._notify()

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_okna_updated(self.entry.entry_id))
