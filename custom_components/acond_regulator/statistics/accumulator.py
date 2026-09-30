"""Statistický akumulátor MaR – per-režim energie, COP, motohodiny, teploty, starty.

Izolováno od regulační smyčky: vlastní state-change listenery + vlastní
persistence. Nebolt na MarCoordinator. Když se tady něco pokazí, regulace jede.

Princip
-------
* **Celoživotní kbelík + kruh 8 denních záznamů** ([0..6] = 7 uzavřených dnů,
  [-1] = dnešek). Odečty: `dnes` = poslední, `vcera` = předchozí, `7 dnů` =
  součet sedmi uzavřených. Dokud není záznamů 8, `7 dnů` hlásí None -> „–".
* **Klasifikace režimu** (exkluzivní, s prioritou):
  bit_8 -> odmraz | bit_3 -> TUV | bit_12 -> chlazení | náběh TUV (rozdíl
  setpointů) | bit_1 -> topení | jinak ostatní.
* **Energie = delta-crediting** z denních čítačů `eed`/`ted`: při každém
  přírůstku spočítej deltu vs. minulé čtení a připiš ji režimu. Nic se
  nedopočítává na konci dne.
* **Chlazení má vlastní čítače** `ced`/`eecd` (30073/30074) a připisuje se
  natvrdo do kbelíku `chlazeni` – ověřeno na železe 30.07.2026, že `eed`/`ted`
  při chlazení stojí. Do řádků „Celkem" chlazení NEPATŘÍ: ty zůstávají topná
  strana, aby se daly porovnávat s Acondacem.
* **Motohodiny** = čas, dokud je režim aktivní **a kompresor točí**
  (`RPM_RUNNING_MIN`). Bez toho spadne proplach okruhu po TUV do „Topení".
  Nedostupné otáčky gate NEZAVÍRAJÍ – „nevím" není „stojí".
* **Teploty = časově vážený průměr**: při změně se připíše `stará × doba`.
  Sbírají se dvě čísla, `°C·s` a `s`. Jmenovatel jsou SKUTEČNĚ MĚŘENÉ sekundy,
  ne hodiny dne -> výpadek jen zmenší jmenovatele a průměr zůstane pravdivý.
  (Dělit hodinami dne = tvrdit „chybějící data znamenají zima".)
* **Starty = náběžné hrany surových bitů**, neexklusivně. Start kompresoru je
  hrana OTÁČEK. Účel se klasifikuje hned, a když v tu chvíli po TUV nic
  neukazuje, drží se čekající záznam a `starty_tuv` se **doúčtuje zpětně**
  (`TUV_START_WINDOW_S`), když si voda vezme právě nastartovaný stroj.

Pasti (ošetřeno)
----------------
* **Baseline nese datum.** Bez toho se přes hranici dne rozbije: HA dole
  23:00->06:00 zahodí ranní kWh, 23:00->20:00 připíše `22,0−18,4 = 3,6 kWh`
  jednomu režimu a 18,4 kWh zmizí. Při `datum != dnes` se baseline zahodí a
  reseeduje z živého stavu. Uvnitř téhož dne se downtime delta připíše (záměr).
* **Půlnoc rozsekne otevřené segmenty**: nejdřív připsat naběhlý čas, pak
  přepnout den. Segmenty pak pokračují od půlnoci se stejnou hodnotou.
* **Půlnoční reset zdroje**: baseline si o půlnoci PODRŽÍ hodnotu a označí se
  „čekám reset" (per čítač, ne společně!). Když další odečet přijde nižší,
  připíše se CELÝ – je to energie od 00:00 – a baseline se posune. Kdyby se
  baseline o půlnoci jen zahodila, první odečet dne by se stal baseline a jeho
  energie by se každý den ztratila. Bonus: funguje bez ohledu na to, jestli
  firmware o půlnoci nuluje, nebo ne.
* **Nečekaný reset** (restart controlleru uprostřed dne) = jen rebaseline,
  nikdy záporný kredit.
* **`unavailable` u bitů nesmí vyrobit start**: reload integrace dělá
  `on -> unavailable -> on`. Za start se počítá jen `off -> on`, kde `off` bylo
  doopravdy `off`; neznámý stav se přeskočí a drží se poslední známý.
* **Sentinel odpojeného čidla −39 °C** vypadne pásmem platnosti (dolní hranice
  −35 °C). Kdyby prolezl, stáhne denní průměr o víc než 10 K.
* **Výpadek přes více dnů**: chybějící dny se doplní prázdné, aby `vcera` bylo
  skutečně včera. Otevřené segmenty se nefabrikují.
* **Motohodiny a teploty se přes restart nepřenášejí wall-clockem** – výpadek HA
  se do nich nezapočítá, po startu jedeme od „teď".
* **Čekající start se nepersistuje.** Restart HA v desetiminutovém okně mezi
  startem a náběhem TUV o doúčtování připraví. Vědomé zjednodušení: ukládat
  runtime okno do `.storage` by za jeden ztracený start nestálo.
* **Průměr teplot po restartu.** Jmenovatel jsou měřené sekundy, takže den, ve
  kterém se statistika teprve rozjela, hlásí průměr jen z té části dne. Chladná
  noc před nasazením v něm není -> číslo může být o pár stupňů výš než
  v Acondacu. Vědomě neopraveno (29.07.2026), viz DECISIONS §7.

Kbelík `ostatni` má DVOJÍ význam
--------------------------------
* **energie**: má být trvale ~0. Růst = naše čtení bitů se rozchází se strojem.
  Je to KONTROLKA, ne datový řádek.
* **motohodiny**: legitimní prostoj (stroj stojí). Velké číslo je správně.
  Od 0.9.2 sem padá i předběh a proplach okruhu – kompresor při nich netočí,
  takže to prostoj JE, i když bit_1 svítí.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import deque

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import (
    ACOND_BIT_COOL,
    ACOND_BIT_DEFROST,
    ACOND_HP_ON,
    ACOND_SUMMER,
    ACOND_BIT_HEAT,
    ACOND_BIT_TUV,
    ACOND_EL_CHLAD_DNES,
    ACOND_EL_DNES,
    ACOND_INDOOR,
    ACOND_OUTDOOR,
    ACOND_RETURN_READBACK,
    ACOND_RPM,
    ACOND_TARGET,
    ACOND_TEP_CHLAD_DNES,
    ACOND_TEP_DNES,
    BAND_INDOOR,
    BAND_OUTDOOR,
    CLOSED_DAYS,
    DOMAIN,
    MODE_COOL,
    MODE_DEFROST,
    MODE_HEAT,
    MODE_IDLE,
    MODE_TUV,
    MAR_FVE_TUV_REZIM,
    MAR_TOPENI_REZIM,
    MODES,
    OVERLAY_TOPENI,
    OVERLAY_TUV,
    OVERLAYS,
    PERIOD_DAY_PREFIX,
    PERIOD_TODAY,
    PERIOD_TOTAL,
    PERIOD_WEEK,
    PERIOD_YESTERDAY,
    REZIM_ON,
    RING_DAYS,
    RPM_RUNNING_MIN,
    START_COMP,
    START_DEFROST,
    START_KEYS,
    START_TUV,
    STATS_SAVE_DELAY,
    TUV_START_ABS,
    TUV_START_DELTA_K,
    TUV_START_WINDOW_S,
    STATS_TICK,
    STORAGE_VERSION,
    signal_stats_updated,
)

_LOGGER = logging.getLogger(__name__)

_KIND_EL = "el"
_KIND_TEP = "tep"
_KIND_EL_COOL = "el_chlad"
_KIND_TEP_COOL = "tep_chlad"
_KINDS = (_KIND_EL, _KIND_TEP, _KIND_EL_COOL, _KIND_TEP_COOL)

_SOURCE = {
    _KIND_EL: ACOND_EL_DNES,
    _KIND_TEP: ACOND_TEP_DNES,
    _KIND_EL_COOL: ACOND_EL_CHLAD_DNES,
    _KIND_TEP_COOL: ACOND_TEP_CHLAD_DNES,
}

# Do KTERÉHO kbelíku kind ukládá. Chlazení má vlastní čítače, ale ne vlastní
# kbelík – padá do `el`/`tep` pod klíčem režimu `chlazeni`, takže invariant
# Σ(režimy) == Σ(čítače) drží dál a nepřibývá třetí dimenze.
_KIND_BUCKET = {
    _KIND_EL: _KIND_EL,
    _KIND_TEP: _KIND_TEP,
    _KIND_EL_COOL: _KIND_EL,
    _KIND_TEP_COOL: _KIND_TEP,
}

# Chladicí čítače se připisují NATVRDO do `chlazeni`, ne podle `_mode`.
# Jinam tikat nemůžou – ověřeno na železe – takže klasifikaci na ně netřeba
# pouštět a nemůže se stát, že by je špatně přečtený bit poslal do topení.
_KIND_FORCE_MODE = {_KIND_EL_COOL: MODE_COOL, _KIND_TEP_COOL: MODE_COOL}

_OUT = "out"
_IN = "in"
_TEMP_SOURCE = {_OUT: ACOND_OUTDOOR, _IN: ACOND_INDOOR}
_TEMP_BAND = {_OUT: BAND_OUTDOOR, _IN: BAND_INDOOR}

# Bity, na kterých visí klasifikace režimu
# (odmraz > TUV > chlazení > náběh TUV > topení > ostatní).
_MODE_BITS = (ACOND_BIT_HEAT, ACOND_BIT_TUV, ACOND_BIT_DEFROST, ACOND_BIT_COOL,
              ACOND_HP_ON, ACOND_SUMMER)

# Odmrazení se počítá z hrany bit_8. Start kompresoru NE z bit_1 – ten naskočí
# 2–3 min dřív (předběh primárního čerpadla), viz const.RPM_RUNNING_MIN.
_BIT_START = {ACOND_BIT_DEFROST: START_DEFROST}

# Překryv -> entita, která ho hlásí, a režim, ve kterém smí připisovat.
_OVERLAY_SOURCE = {
    OVERLAY_TOPENI: (MAR_TOPENI_REZIM, MODE_HEAT),
    OVERLAY_TUV: (MAR_FVE_TUV_REZIM, MODE_TUV),
}

_SCHEMA = 3  # 1 = 0.8.0 (jen lifetime) · 2 = 0.9.0 (kruh dnů + překryvy) · 3 = 0.10.0 (chlazení)

# Schémata, která umíme načíst bez ztráty dat. Schéma 2 se liší JEN chybějícím
# kbelíkem `chlazeni` a chladicími baselinami – obojí se dopočte za běhu.
# POZOR: kdyby se tady testovalo `== _SCHEMA`, spadla by 0.9.x data do migrační
# větve pro 0.8.0, ta by v nich nenašla `el`/`tep` na první úrovni a MLČKY BY
# ZAHODILA VŠECHNO. Jediné místo v celém upgradu, kde se dá přijít o historii.
_LOADABLE = (2, _SCHEMA)


def _r2(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _fmt_hours(seconds: float | None) -> str | None:
    """Motohodiny ve tvaru Acondacu: „11h 14min"."""
    if seconds is None:
        return None
    total = int(seconds // 60)
    return f"{total // 60}h {total % 60}min"


class _Bucket:
    """Jeden záznam – denní (date) nebo celoživotní (date=None)."""

    __slots__ = ("date", "el", "tep", "hours", "starts", "t_sum", "t_sec", "over")

    def __init__(self, date: dt.date | None = None) -> None:
        self.date = date
        self.el: dict[str, float] = {m: 0.0 for m in MODES}
        self.tep: dict[str, float] = {m: 0.0 for m in MODES}
        self.hours: dict[str, float] = {m: 0.0 for m in MODES}
        self.starts: dict[str, int] = {k: 0 for k in START_KEYS}
        self.t_sum: dict[str, float] = {_OUT: 0.0, _IN: 0.0}
        self.t_sec: dict[str, float] = {_OUT: 0.0, _IN: 0.0}
        # překryv: TEPLO vyrobené, když přetokový program držel kormidlo
        self.over: dict[str, float] = {o: 0.0 for o in OVERLAYS}

    def bucket(self, kind: str) -> dict[str, float]:
        return self.el if _KIND_BUCKET[kind] == _KIND_EL else self.tep

    def as_dict(self) -> dict:
        return {
            "date": self.date.isoformat() if self.date else None,
            "el": self.el,
            "tep": self.tep,
            "hours": self.hours,
            "starts": self.starts,
            "t_sum": self.t_sum,
            "t_sec": self.t_sec,
            "over": self.over,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "_Bucket":
        raw = data.get("date")
        date = dt.date.fromisoformat(raw) if raw else None
        b = cls(date)
        for attr in ("el", "tep", "hours"):
            src = data.get(attr) or {}
            getattr(b, attr).update(
                {m: float(v) for m, v in src.items() if m in MODES}
            )
        src = data.get("starts") or {}
        b.starts.update({k: int(v) for k, v in src.items() if k in START_KEYS})
        for attr in ("t_sum", "t_sec"):
            src = data.get(attr) or {}
            getattr(b, attr).update(
                {w: float(v) for w, v in src.items() if w in (_OUT, _IN)}
            )
        src = data.get("over") or {}
        b.over.update({o: float(v) for o, v in src.items() if o in OVERLAYS})
        return b


class StatisticsAccumulator:
    """Stavová klasifikace + akumulace, kruh osmi dnů, vlastní persistence."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._signal = signal_stats_updated(entry.entry_id)
        self._store: Store = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_statistics"
        )

        self._life = _Bucket()
        self._ring: deque[_Bucket] = deque(maxlen=RING_DAYS)
        self._since: dt.datetime | None = None      # kdy statistika začala

        # baseline posledního čtení denních čítačů + jeho datum (oprava F1)
        self._base: dict[str, float | None] = {k: None for k in _KINDS}
        self._base_date: dt.date | None = None
        # „čekám půlnoční reset" – PER ČÍTAČ, ne společně
        self._rolled: dict[str, bool] = {k: False for k in _KINDS}

        # runtime stav (wall-clock se nepersistuje)
        self._mode: str = MODE_IDLE
        self._mode_since: dt.datetime | None = None
        self._bits: dict[str, bool | None] = {e: None for e in _MODE_BITS}
        self._rpm_running: bool | None = None
        self._wants_tuv: bool = False
        self._temp: dict[str, float | None] = {_OUT: None, _IN: None}
        self._temp_since: dict[str, dt.datetime | None] = {_OUT: None, _IN: None}
        self._over_on: dict[str, bool] = {o: False for o in OVERLAYS}

        # Čekající start kompresoru: drží REFERENCI na kbelík dne, ve kterém
        # start proběhl (ne index!), aby se doúčtování přes půlnoc trefilo do
        # správného dne. Runtime, nepersistuje se.
        self._pending_day: _Bucket | None = None
        self._pending_until: dt.datetime | None = None

        self._fingerprint: tuple | None = None
        self._unsubs: list = []

    # ------------------------------------------------------------------ #
    # Životní cyklus
    # ------------------------------------------------------------------ #
    async def async_load(self) -> None:
        """Obnov kbelíky z úložiště. Tolerantní: umí i schéma 0.8.0."""
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return

        since = data.get("since")
        if since:
            self._since = dt_util.parse_datetime(since)

        if data.get("schema") in _LOADABLE and isinstance(data.get("life"), dict):
            self._life = _Bucket.from_dict(data["life"])
            for raw in data.get("ring") or []:
                if isinstance(raw, dict) and raw.get("date"):
                    self._ring.append(_Bucket.from_dict(raw))
            base = data.get("base") or {}
            for kind in _KINDS:
                v = base.get(kind)
                self._base[kind] = float(v) if v is not None else None
            raw_date = data.get("base_date")
            self._base_date = dt.date.fromisoformat(raw_date) if raw_date else None
            rolled = data.get("rolled") or {}
            self._rolled = {k: bool(rolled.get(k)) for k in _KINDS}
            return

        # --- migrace 0.8.0 -> 0.9.0 --------------------------------------- #
        # Celoživotní kbelíky se ZACHOVAJÍ (rozhodnutí b), kruh dnů startuje
        # prázdný, baseline se zahodí (neznámé datum -> nesmíme riskovat lump).
        # Starty a teploty v lifetime začínají na nule – dřív se neměřily,
        # dopočítat je zpětně nelze a předstírat, že je známe, by byla lež.
        legacy = {
            "el": data.get("el"),
            "tep": data.get("tep"),
            "hours": data.get("hours"),
        }
        if any(legacy.values()):
            self._life = _Bucket.from_dict(legacy)
            _LOGGER.info(
                "Statistika: migrace 0.8.0 -> 0.9.0, celoživotní kbelíky "
                "zachovány, kruh dnů startuje prázdný"
            )

    @callback
    def async_start(self) -> None:
        """Doplň baseline z živých stavů, srovnej kruh a navěs listenery."""
        now = dt_util.now()
        today = now.date()

        if not self._ring:
            self._ring.append(_Bucket(today))
        else:
            # doplň dny, které proběhly, když HA neběžel (bez fabrikování segmentů)
            while self._ring[-1].date < today:
                self._ring.append(_Bucket(self._ring[-1].date + dt.timedelta(days=1)))

        if self._since is None:
            self._since = now

        if self._base_date != today:
            if self._base_date is not None:
                _LOGGER.debug(
                    "Statistika: baseline z %s != dnes %s -> zahozena, reseed "
                    "(zabraňuje falešnému kreditu přes hranici dne)",
                    self._base_date,
                    today,
                )
            for kind in _KINDS:
                self._base[kind] = self._live_float(_SOURCE[kind])
            self._base_date = today
            self._rolled = {k: False for k in _KINDS}
        else:
            # uvnitř téhož dne baseline držíme -> downtime delta se připíše (záměr)
            for kind in _KINDS:
                if self._base[kind] is None:
                    self._base[kind] = self._live_float(_SOURCE[kind])

        for entity_id in _MODE_BITS:
            self._bits[entity_id] = self._live_bit(entity_id)
        rpm = self._live_float(ACOND_RPM)
        self._rpm_running = None if rpm is None else rpm >= RPM_RUNNING_MIN
        self._wants_tuv = self._start_is_tuv()
        self._mode = self._classify()
        self._mode_since = now

        for which, entity_id in _TEMP_SOURCE.items():
            self._temp[which] = self._band(which, self._live_float(entity_id))
            self._temp_since[which] = now

        for overlay, (entity_id, _mode) in _OVERLAY_SOURCE.items():
            state = self.hass.states.get(entity_id)
            self._over_on[overlay] = state is not None and state.state == REZIM_ON

        self._unsubs.append(
            async_track_state_change_event(
                self.hass, list(_SOURCE.values()), self._handle_energy
            )
        )
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, list(_MODE_BITS), self._handle_bit
            )
        )
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, list(_TEMP_SOURCE.values()), self._handle_temp
            )
        )
        self._unsubs.append(
            async_track_state_change_event(self.hass, [ACOND_RPM], self._handle_rpm)
        )
        self._unsubs.append(
            async_track_state_change_event(
                self.hass,
                [ACOND_RETURN_READBACK, ACOND_TARGET],
                self._handle_setpoint,
            )
        )
        self._unsubs.append(
            async_track_state_change_event(
                self.hass,
                [e for e, _m in _OVERLAY_SOURCE.values()],
                self._handle_overlay,
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._handle_tick, dt.timedelta(seconds=STATS_TICK)
            )
        )

    async def async_stop(self) -> None:
        """Odpoj listenery, dolij otevřené segmenty a ulož napevno."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._ring:
            self._flush(dt_util.now())
        await self._store.async_save(self._data_to_save())

    # ------------------------------------------------------------------ #
    # Události
    # ------------------------------------------------------------------ #
    @callback
    def _handle_energy(self, event: Event) -> None:
        new: State | None = event.data.get("new_state")
        if new is None:
            return
        value = self._as_float(new)
        if value is None:
            return
        entity_id = new.entity_id
        kind = next((k for k, e in _SOURCE.items() if e == entity_id), None)
        if kind is None:
            return

        now = dt_util.now()
        self._sync(now)
        self._credit(kind, value)
        self._notify()

    @callback
    def _handle_bit(self, event: Event) -> None:
        new: State | None = event.data.get("new_state")
        if new is None:
            return
        entity_id = new.entity_id
        if entity_id not in self._bits:
            return

        now = dt_util.now()
        self._sync(now)          # naběhlý čas patří ještě STARÉMU režimu

        state = self._bit_from_state(new)
        if state is not None:
            old = self._bits[entity_id]
            key = _BIT_START.get(entity_id)
            if key is not None and old is False and state is True:
                self._ring[-1].starts[key] += 1
                self._life.starts[key] += 1
            self._bits[entity_id] = state
        # unavailable/unknown: držíme poslední známý stav, žádný start

        # bit_3 může dorazit až po startu kompresoru -> doúčtovat „kvůli TUV"
        self._resolve_pending(now)
        self._mode = self._classify()
        self._notify()

    @callback
    def _handle_temp(self, event: Event) -> None:
        new: State | None = event.data.get("new_state")
        if new is None:
            return
        which = next((w for w, e in _TEMP_SOURCE.items() if e == new.entity_id), None)
        if which is None:
            return

        now = dt_util.now()
        self._sync(now)          # integrál uzavřít STAROU hodnotou
        self._temp[which] = self._band(which, self._as_float(new))
        self._temp_since[which] = now

    @callback
    def _handle_rpm(self, event: Event) -> None:
        """Start kompresoru = hrana otáček, ne bit_1. A brána motohodin.

        Ověřeno na železe: bit_1 („TČ v provozu") naskočí 2–3 min před
        kompresorem kvůli předběhu primárního čerpadla, takže by počítal
        i starty, které se nekonaly.

        Účel startu se klasifikuje HNED: stroj si při ohřevu TUV nastaví 30008
        na 60 °C ~2 min PŘED startem, takže signál už tam v ten okamžik je.
        Porovnáváme rozdíl proti tomu, co píšeme na 40008; napevno zadaná
        šedesátka by u radiátorové instalace selhala.

        Když okamžitá klasifikace mlčí, start se **nezahazuje** – uloží se jako
        čekající a doúčtuje se, jakmile do `TUV_START_WINDOW_S` naskočí TUV.
        To je případ, kdy kompresor rozjel náš boost (kámen Topit) a voda si
        vzala až běžící stroj: v okamžiku hrany je 30008 ≈ 40008.

        POZOR na pořadí: `_sync()` musí proběhnout PŘED přepsáním
        `_rpm_running`, protože na něm od 0.9.2 visí brána motohodin. Jinak by
        se až celý tik stojícího stroje připsal běžícímu režimu (a naopak).
        """
        new: State | None = event.data.get("new_state")
        if new is None:
            return
        value = self._as_float(new)
        if value is None:
            return                      # unavailable -> držíme poslední známý

        running = value >= RPM_RUNNING_MIN
        was = self._rpm_running
        if was == running:
            return                      # kolísání nad/pod prahem, žádná hrana

        now = dt_util.now()
        self._sync(now)                 # naběhlý čas patří ještě STARÉMU stavu
        self._rpm_running = running

        if not running:
            # kompresor dojel -> okno se zavírá. Co přijde potom, už si vzal
            # jiný start, ne tenhle.
            self._pending_day = None
            self._pending_until = None
            self._notify()
            return

        if was is None:
            # první známá hodnota po startu integrace: stroj už mohl běžet,
            # tohle není hrana 0 -> otáčky. Start se nepočítá.
            self._notify()
            return

        day = self._ring[-1]
        day.starts[START_COMP] += 1
        self._life.starts[START_COMP] += 1
        if self._tuv_now():
            day.starts[START_TUV] += 1
            self._life.starts[START_TUV] += 1
            self._pending_day = None
            self._pending_until = None
        else:
            self._pending_day = day
            self._pending_until = now + dt.timedelta(seconds=TUV_START_WINDOW_S)
        self._schedule_save()
        self._notify()

    def _tuv_now(self) -> bool:
        """Ukazuje cokoli na TUV? Rozsvícený bit_3 NEBO rozdíl setpointů.

        Bit se ptá napřed schválně – je to tvrdý fakt ze stroje, kdežto rozdíl
        setpointů je heuristika pro náběhovou fázi, kdy bit ještě nesvítí.
        """
        if self._bits.get(ACOND_BIT_TUV):
            return True
        return self._start_is_tuv()

    def _resolve_pending(self, now: dt.datetime) -> None:
        """Vzala si voda právě nastartovaný kompresor? -> byl to start kvůli TUV.

        Připisuje se do kbelíku, ve kterém start proběhl (držíme referenci), ne
        do dneška – jinak by se start ve 23:58 s vodou v 00:02 zapsal špatnému
        dni. Jednou a dost: po připsání se okno ruší.
        """
        if self._pending_day is None:
            return
        if self._pending_until is not None and now > self._pending_until:
            self._pending_day = None
            self._pending_until = None
            return
        if not self._tuv_now():
            return
        self._pending_day.starts[START_TUV] += 1
        self._life.starts[START_TUV] += 1
        self._pending_day = None
        self._pending_until = None
        self._schedule_save()

    @callback
    def _handle_setpoint(self, event: Event) -> None:
        """Změna požadované zpátečky může překlopit režim (náběh TUV)."""
        if event.data.get("new_state") is None:
            return
        wants = self._start_is_tuv()
        if wants == self._wants_tuv:
            return
        now = dt_util.now()
        self._sync(now)                # naběhlý čas patří ještě starému režimu
        self._wants_tuv = wants
        # náběh TUV po startu z našeho boostu -> doúčtovat „kvůli TUV"
        self._resolve_pending(now)
        self._mode = self._classify()
        self._notify()

    def _start_is_tuv(self) -> bool:
        """Chce stroj výrazně víc, než mu píšeme? -> startoval kvůli vodě."""
        wants = self._live_float(ACOND_RETURN_READBACK)
        if wants is None:
            return False
        we_write = self._live_float(ACOND_TARGET)
        if we_write is None:
            return wants >= TUV_START_ABS
        return (wants - we_write) >= TUV_START_DELTA_K

    @callback
    def _handle_overlay(self, event: Event) -> None:
        """Přetokový program zabral/pustil. Jen přepne příznak – energie se
        připisuje až při dalším přírůstku čítače (delta-crediting)."""
        new: State | None = event.data.get("new_state")
        if new is None:
            return
        for overlay, (entity_id, _mode) in _OVERLAY_SOURCE.items():
            if entity_id == new.entity_id:
                self._sync(dt_util.now())
                self._over_on[overlay] = new.state == REZIM_ON
                return

    @callback
    def _handle_tick(self, _now: dt.datetime) -> None:
        """Seká nedokončené segmenty a osvěžuje displej (řeší F3)."""
        if not self._ring:
            return
        now = dt_util.now()
        self._sync(now)
        self._resolve_pending(now)     # doúčtování i vypršení okna
        self._notify()

    # ------------------------------------------------------------------ #
    # Jádro
    # ------------------------------------------------------------------ #
    def _sync(self, now: dt.datetime) -> None:
        """Překlop den (když je potřeba) a dolij otevřené segmenty do „teď"."""
        self._roll(now)
        self._flush(now)

    def _roll(self, now: dt.datetime) -> None:
        """Půlnoc: nejdřív připsat naběhlý čas, PAK přepnout den."""
        if not self._ring:
            return
        while self._ring[-1].date < now.date():
            next_day = self._ring[-1].date + dt.timedelta(days=1)
            midnight = dt_util.start_of_local_day(next_day)
            self._flush(midnight)
            self._ring.append(_Bucket(next_day))
            # segmenty pokračují přes půlnoc se stejnou hodnotou
            self._mode_since = midnight
            for which in (_OUT, _IN):
                if self._temp[which] is not None:
                    self._temp_since[which] = midnight
            # baseline hodnotu DRŽÍME, jen čekáme reset zdroje na nulu
            self._rolled = {k: True for k in _KINDS}
            self._base_date = next_day

    def _flush(self, ts: dt.datetime) -> None:
        """Připiš naběhlý čas a integrál teplot do dneška i do lifetime."""
        if not self._ring:
            return
        day = self._ring[-1]

        if self._mode_since is not None:
            sec = (ts - self._mode_since).total_seconds()
            if sec > 0:
                mode = self._hours_mode()
                day.hours[mode] += sec
                self._life.hours[mode] += sec
            self._mode_since = ts

        for which in (_OUT, _IN):
            value = self._temp[which]
            since = self._temp_since[which]
            if value is None or since is None:
                self._temp_since[which] = ts
                continue
            sec = (ts - since).total_seconds()
            if sec > 0:
                day.t_sum[which] += value * sec
                day.t_sec[which] += sec
                self._life.t_sum[which] += value * sec
                self._life.t_sec[which] += sec
            self._temp_since[which] = ts

    def _credit(self, kind: str, value: float) -> None:
        """Delta-crediting. Připisuje se do režimu podle cached `_mode`.

        Cached (ne live `_classify()`) je záměr: delta se nasčítala během
        intervalu, který právě skončil, tedy převážně pod STARÝM režimem.
        Zároveň to drží motohodiny a energii konzistentní (odpadá rozdíl F4).
        """
        prev = self._base[kind]
        if prev is None:
            self._base[kind] = value
            return

        delta = round(value - prev, 4)
        if delta < 0:
            if self._rolled[kind]:
                # čekaný půlnoční reset: nová hodnota JE energie od 00:00
                delta = value
                self._base[kind] = value
                self._rolled[kind] = False
                if delta <= 0:
                    return
            else:
                _LOGGER.debug(
                    "Statistika: nečekaný reset zdroje %s (%s -> %s), rebaseline",
                    kind,
                    prev,
                    value,
                )
                self._base[kind] = value
                self._rolled[kind] = False
                return
        else:
            self._rolled[kind] = False
            if delta == 0:
                return
            self._base[kind] = value

        mode = _KIND_FORCE_MODE.get(kind, self._mode)
        self._ring[-1].bucket(kind)[mode] += delta
        self._life.bucket(kind)[mode] += delta

        # Překryv: jen teplo a jen když sedí i režim -> skutečná podmnožina.
        # Bez kontroly režimu by přetokové topení běžící během ohřevu vody
        # nasálo cizí teplo do svého kbelíku.
        if kind == _KIND_TEP:
            for overlay, (_entity, need_mode) in _OVERLAY_SOURCE.items():
                if self._over_on[overlay] and self._mode == need_mode:
                    self._ring[-1].over[overlay] += delta
                    self._life.over[overlay] += delta

        self._schedule_save()

    def _classify(self) -> str:
        """Odmraz > TUV > chlazení > náběh TUV > topení > ostatní.

        TUV se pozná DVĚMA cestami: buď svítí bit_3, nebo stroj chce výrazně
        vyšší zpátečku, než mu píšeme (`_wants_tuv`). Ta druhá je potřeba kvůli
        náběhové fázi: stroj si nastaví 60 °C a rozjede kompresor, ale bit_3
        naskočí až o ~5 min později, protože trojcestný ventil nepustí vodu do
        bojleru, dokud výstup nemá teplotu.

        Bez toho by náběh spadl pod topení a v létě by v tabulce svítily
        kilowatthodiny a desítky minut „topení". Ověřeno proti Acondacu na
        druhé instalaci (Jan, 28.07.2026): TUV-only den má v appce Topení
        0,0 kWh a 0h 0min – náběh tam pod topení NEPATŘÍ.

        Chlazení sedí MEZI nimi (0.10.0). Nad topením proto, že bit_1 svítí
        i při chlazení. A nad náběhovou heuristikou proto, že v chladicí sezóně
        běží letní provoz, regulace na 40008 nezapisuje a visí tam stará
        hodnota – rozdíl proti 30008 by mohl ukousnout celé chlazení do TUV.

        Dvě pojistky. bit_0, protože bit_12 v čerpadle po chlazení ZŮSTÁVÁ
        svítit i po vypnutí TČ (ověřeno na železe 10.08.2026). A bit_10,
        protože topit jde jen v zimním režimu a chladit jen v letním, bez
        výjimky – mimo léto tedy o chlazení jít nemůže, ať bit_12 ukazuje co
        chce. Bez nich by zaseknutý bit ukousl topnou energii do chladicího
        kbelíku, jakmile se stroj zase rozjede.
        Energie na pořadí nezávisí: chladicí čítače jdou do svého kbelíku
        natvrdo. Rozhoduje jen o motohodinách a o účtování `eed`/`ted`, kdyby
        při chlazení přece jen tikly.
        """
        if self._bits.get(ACOND_BIT_DEFROST):
            return MODE_DEFROST
        if self._bits.get(ACOND_BIT_TUV):
            return MODE_TUV
        if (
            self._bits.get(ACOND_BIT_COOL)
            and self._bits.get(ACOND_HP_ON) is not False
            and self._bits.get(ACOND_SUMMER) is not False
        ):
            return MODE_COOL
        if self._wants_tuv:
            return MODE_TUV
        if self._bits.get(ACOND_BIT_HEAT):
            return MODE_HEAT
        return MODE_IDLE

    def _hours_mode(self) -> str:
        """Kam připsat naběhlý ČAS. Energie se řídí `_mode`, hodiny tímhle.

        Motohodina je čas kompresoru, ne čas rozsvíceného bitu. Bit_1 svítí
        i během ~15minutového proplachu okruhu po ohřevu vody – bez téhle brány
        se z toho v TUV-only dni stane „Topení 0h 33min" proti 0,00 kWh.
        Acondac na takovém dni ukazuje 0h 0min.

        Energie se ZÁMĚRNĚ neřídí tímhle: opožděná delta čítače dorazí až po
        zhasnutí kompresoru a spadla by do kontrolky `ostatni`, která má zůstat
        na nule. Atribuce energie je věc režimu, ne otáček.

        Nedostupné otáčky (`None`) bránu NEZAVÍRAJÍ – „nevím" není „stojí".
        """
        if self._rpm_running is False:
            return MODE_IDLE
        return self._mode

    @staticmethod
    def _band(which: str, value: float | None) -> float | None:
        """Pásmo platnosti čidla. Sentinel −39 °C vypadne dolní hranicí."""
        if value is None:
            return None
        lo, hi = _TEMP_BAND[which]
        return value if lo <= value <= hi else None

    # ------------------------------------------------------------------ #
    # Čtení
    # ------------------------------------------------------------------ #
    def _records(self, period: str) -> list[_Bucket] | None:
        """None = období není k dispozici -> tabulka vykreslí „–".

        Kromě pojmenovaných období rozumí i `den:N` (0 = dnešek, 1 = včerejšek,
        …, 7 = nejstarší v kruhu). To je okénko pro denní rozpad do exportu:
        v součtu za týden se ztratí, že jeden den bylo pět startů a druhý
        dvacet — a právě ten rozdíl je pro porovnání instalací zajímavý.
        """
        if not self._ring:
            return None
        if period == PERIOD_TOTAL:
            return [self._life]
        if period == PERIOD_TODAY:
            return [self._ring[-1]]
        if period == PERIOD_YESTERDAY:
            return [self._ring[-2]] if len(self._ring) >= 2 else None
        if period == PERIOD_WEEK:
            if len(self._ring) < RING_DAYS:
                return None
            return list(self._ring)[0:CLOSED_DAYS]
        if period.startswith(PERIOD_DAY_PREFIX):
            idx = self._day_index(period)
            if idx is None:
                return None
            return [self._ring[idx]]
        return None

    def _day_index(self, period: str) -> int | None:
        """`den:N` -> index do kruhu, nebo None, když ten den ještě neexistuje.

        Kruh se plní postupně, takže dokud neuběhne týden, část dnů chybí.
        Vrátit None je správně: sloupec se vykreslí s pomlčkami a je vidět,
        odkdy statistika běží.
        """
        try:
            back = int(period[len(PERIOD_DAY_PREFIX):])
        except ValueError:
            return None
        if back < 0 or back >= RING_DAYS:
            return None
        idx = len(self._ring) - 1 - back
        return idx if idx >= 0 else None

    def energy(
        self, kind: str, mode: str, period: str = PERIOD_TOTAL
    ) -> float | None:
        recs = self._records(period)
        if recs is None:
            return None
        return round(sum(r.bucket(kind)[mode] for r in recs), 3)

    def hours(self, mode: str, period: str = PERIOD_TOTAL) -> float | None:
        """Motohodiny v hodinách. `ostatni` = legitimní prostoj, ne závada."""
        recs = self._records(period)
        if recs is None:
            return None
        return round(sum(r.hours[mode] for r in recs) / 3600.0, 2)

    def starts(self, key: str, period: str = PERIOD_TODAY) -> int | None:
        recs = self._records(period)
        if recs is None:
            return None
        return sum(r.starts[key] for r in recs)

    def avg_temp(self, which: str, period: str = PERIOD_TODAY) -> float | None:
        """Časově vážený průměr. `0/0` -> None („–"), nikdy 0 °C."""
        recs = self._records(period)
        if recs is None:
            return None
        sec = sum(r.t_sec[which] for r in recs)
        if sec <= 0:
            return None
        return round(sum(r.t_sum[which] for r in recs) / sec, 1)

    def overlay(self, key: str, period: str = PERIOD_TOTAL) -> float | None:
        """Teplo vyrobené, když přetokový program držel kormidlo.

        POZOR na výklad: NENÍ to „teplo z přetoků". Když se slunce schová,
        stroj chvíli běží dál z baterie nebo ze sítě a to teplo se sem započítá
        taky. Záměrné zjednodušení – přesné rozdělení by znamenalo integrovat
        okamžitý výkon a ztratit přesnost delta-creditingu.
        """
        recs = self._records(period)
        if recs is None:
            return None
        return round(sum(r.over[key] for r in recs), 3)

    def cop(self, mode: str, period: str = PERIOD_TOTAL) -> float | None:
        """COP jednoho režimu. Odmraz NEEXISTUJE – není to proces s účinností.

        Pro `chlazeni` je to fyzikálně EER, ale řádek se jmenuje „COP chlazení"
        – Acondac ho na kartě ukazuje jako COP a názvy kopírujeme po něm, aby
        se čísla dala porovnávat očima.
        """
        if mode not in (MODE_HEAT, MODE_TUV, MODE_COOL):
            return None
        tep = self.energy(_KIND_TEP, mode, period)
        el = self.energy(_KIND_EL, mode, period)
        if tep is None or el is None or el <= 0:
            return None
        return round(tep / el, 2)

    def cop_total(self, period: str = PERIOD_TOTAL) -> float | None:
        """(tep_topeni + tep_tuv) / (el_topeni + el_tuv + el_odmraz).

        Vyjmenované kbelíky, ne Σvšechno: `ostatni` musí zůstat mimo (je to
        kontrolka) a odmraz patří jen do jmenovatele – jeho teplo není produkt.
        Proto COP celkem NENÍ průměr COP topení a TUV, ale vždy o kousek nižší.
        Ověřeno na Acondacu: 64,39 / 12,48 = 5,16.
        """
        recs = self._records(period)
        if recs is None:
            return None
        num = sum(r.tep[MODE_HEAT] + r.tep[MODE_TUV] for r in recs)
        den = sum(
            r.el[MODE_HEAT] + r.el[MODE_TUV] + r.el[MODE_DEFROST] for r in recs
        )
        if den <= 0:
            return None
        return round(num / den, 2)

    def current_mode(self) -> str:
        return self._mode

    def period_range(self, period: str) -> tuple[dt.datetime | None, dt.datetime | None]:
        """Rozsah dat pro hlavičku tabulky i snímku."""
        now = dt_util.now()
        if not self._ring:
            return (None, None)
        if period == PERIOD_TODAY:
            return (dt_util.start_of_local_day(self._ring[-1].date), now)
        if period == PERIOD_YESTERDAY:
            if len(self._ring) < 2:
                return (None, None)
            start = dt_util.start_of_local_day(self._ring[-2].date)
            return (start, start + dt.timedelta(days=1))
        if period == PERIOD_WEEK:
            if len(self._ring) < RING_DAYS:
                return (None, None)
            start = dt_util.start_of_local_day(self._ring[0].date)
            return (start, dt_util.start_of_local_day(self._ring[-1].date))
        if period.startswith(PERIOD_DAY_PREFIX):
            idx = self._day_index(period)
            if idx is None:
                return (None, None)
            start = dt_util.start_of_local_day(self._ring[idx].date)
            # dnešek ještě běží -> konec je „teď", ne půlnoc
            end = now if idx == len(self._ring) - 1 else start + dt.timedelta(days=1)
            return (start, end)
        return (self._since, now)

    def _is_today(self, period: str) -> bool:
        """Je to dnešek? Platí pro `dnes` i pro `den:0` — v obou případech je
        sloupec neúplný a musí dostat hvězdičku."""
        if period == PERIOD_TODAY:
            return True
        if period.startswith(PERIOD_DAY_PREFIX):
            return self._day_index(period) == len(self._ring) - 1
        return False

    def period_attributes(self, period: str) -> dict:
        """Všech 19 řádků tabulky + kontrolky. Chybějící hodnota = None -> „–"."""
        start, end = self.period_range(period)
        available = self._records(period) is not None

        def e(kind: str, mode: str) -> float | None:
            v = self.energy(kind, mode, period)
            return None if v is None else round(v, 2)

        def total(kind: str) -> float | None:
            parts = [e(kind, m) for m in (MODE_HEAT, MODE_TUV, MODE_DEFROST)]
            if any(p is None for p in parts):
                return None
            return round(sum(parts), 2)

        return {
            "obdobi": period,
            "dostupne": available,
            "uplne": not self._is_today(period),      # „dnes" je neúplný sloupec
            "rozsah_od": start.isoformat() if start else None,
            "rozsah_do": end.isoformat() if end else None,
            # vyrobená energie
            "tep_topeni": e(_KIND_TEP, MODE_HEAT),
            "tep_tuv": e(_KIND_TEP, MODE_TUV),
            "tep_odmraz": e(_KIND_TEP, MODE_DEFROST),
            "tep_celkem": total(_KIND_TEP),
            # spotřebovaná energie
            "el_topeni": e(_KIND_EL, MODE_HEAT),
            "el_tuv": e(_KIND_EL, MODE_TUV),
            "el_odmraz": e(_KIND_EL, MODE_DEFROST),
            "el_celkem": total(_KIND_EL),
            # COP
            "cop_topeni": self.cop(MODE_HEAT, period),
            "cop_tuv": self.cop(MODE_TUV, period),
            "cop_celkem": self.cop_total(period),
            # teploty
            "t_venkovni": self.avg_temp(_OUT, period),
            "t_vnitrni": self.avg_temp(_IN, period),
            # motohodiny (formátováno jako Acondac)
            "hodiny_topeni": _fmt_hours(self._sec(MODE_HEAT, period)),
            "hodiny_tuv": _fmt_hours(self._sec(MODE_TUV, period)),
            "hodiny_odmraz": _fmt_hours(self._sec(MODE_DEFROST, period)),
            # starty
            "starty_kompresor": self.starts(START_COMP, period),
            "starty_tuv": self.starts(START_TUV, period),
            "starty_odmraz": self.starts(START_DEFROST, period),
            # z přetoků (jen teplo, informativně)
            "fve_tep_topeni": _r2(self.overlay(OVERLAY_TOPENI, period)),
            "fve_tep_tuv": _r2(self.overlay(OVERLAY_TUV, period)),
            # chlazení – vlastní čítače 30073/30074, mimo řádky „Celkem"
            "tep_chlazeni": e(_KIND_TEP, MODE_COOL),
            "el_chlazeni": e(_KIND_EL, MODE_COOL),
            "cop_chlazeni": self.cop(MODE_COOL, period),
            "hodiny_chlazeni": _fmt_hours(self._sec(MODE_COOL, period)),
            # kontrolky (energie 'ostatni' MÁ být ~0; růst = rozchod s bity)
            "el_ostatni": e(_KIND_EL, MODE_IDLE),
            "tep_ostatni": e(_KIND_TEP, MODE_IDLE),
        }

    def _sec(self, mode: str, period: str) -> float | None:
        recs = self._records(period)
        if recs is None:
            return None
        return sum(r.hours[mode] for r in recs)

    def reconcile(self) -> dict[str, float | None]:
        """Kontrola pro debug: Σ(kbelíky) proti sobě.

        POZOR: NEsedne na registry 30037/30035 (celoživotní totály stroje) –
        zdroj jsou DENNÍ čítače 30040/30039, takže lifetime = součet dnů OD
        NASAZENÍ MaR, ne od narození jednotky.
        """
        return {
            "el_celkem_od_nasazeni": round(sum(self._life.el.values()), 3),
            "tep_celkem_od_nasazeni": round(sum(self._life.tep.values()), 3),
            "el_ostatni_kontrolka": round(self._life.el[MODE_IDLE], 3),
            "tep_ostatni_kontrolka": round(self._life.tep[MODE_IDLE], 3),
            "uzavrenych_dnu": max(len(self._ring) - 1, 0),
        }

    # ------------------------------------------------------------------ #
    # Persistence / notifikace
    # ------------------------------------------------------------------ #
    def _data_to_save(self) -> dict:
        return {
            "schema": _SCHEMA,
            "since": self._since.isoformat() if self._since else None,
            "life": self._life.as_dict(),
            "ring": [b.as_dict() for b in self._ring],
            "base": dict(self._base),
            "base_date": self._base_date.isoformat() if self._base_date else None,
            "rolled": dict(self._rolled),
        }

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, STATS_SAVE_DELAY)

    def _notify(self) -> None:
        """Signál sensorům – ale jen když se zobrazovaná čísla opravdu změnila.

        Bez téhle brzdy by 30s tik posílal signál pořád a recorder by dostával
        řádek každých 30 s o ničem. Motohodiny jsou v odpečátku zaokrouhlené na
        minuty, takže se fingerprint mění nejvýš raz za minutu.
        """
        fp = self._display_fingerprint()
        if fp == self._fingerprint:
            return
        self._fingerprint = fp
        async_dispatcher_send(self.hass, self._signal)

    def _display_fingerprint(self) -> tuple:
        day = self._ring[-1] if self._ring else _Bucket()
        return (
            tuple(round(day.el[m], 2) for m in MODES),
            tuple(round(day.tep[m], 2) for m in MODES),
            tuple(int(day.hours[m] // 60) for m in MODES),
            tuple(day.starts[k] for k in START_KEYS),
            self.avg_temp(_OUT, PERIOD_TODAY),
            self.avg_temp(_IN, PERIOD_TODAY),
            len(self._ring),
            self._mode,
        )

    # ------------------------------------------------------------------ #
    # Pomocníci
    # ------------------------------------------------------------------ #
    @staticmethod
    def _as_float(state: State | None) -> float | None:
        if state is None or state.state in ("unknown", "unavailable", "", None):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bit_from_state(state: State | None) -> bool | None:
        """None = neznámý stav -> držíme poslední známý, žádná hrana."""
        if state is None or state.state in ("unknown", "unavailable", "", None):
            return None
        return state.state == "on"

    def _live_float(self, entity_id: str) -> float | None:
        return self._as_float(self.hass.states.get(entity_id))

    def _live_bit(self, entity_id: str) -> bool | None:
        return self._bit_from_state(self.hass.states.get(entity_id))
