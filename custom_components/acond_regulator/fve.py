"""Režim „Ohřev TUV z přebytku FVE" (osa B, executor na 40005).

Sleduje 15min klouzavý průměr BADGETU (= přetok + okamžitý příkon TČ) a při dostatku
přebytku zvedne cíl TUV (40005) na MAXIMUM (horní mez 40005), jinak ho vrátí zpět.
Výkon kompresoru si TČ řídí samo (moduluje ~1:7) – MaR na výkon (40014) nesahá,
jen přepíná setpoint TUV normal <-> max.

BADGET (proč): holý přetok MASKUJE vlastní spotřeba. Když TČ zrovna topí z přebytku
(nebo běží jiný FV program), sežere ho, na elektroměru je ~0 a ohřev si myslí „není
přebytek". Přičtením okamžitého příkonu TČ (aep, registr 30027) tu spotřebu odmaskuješ:
badget = přetok + aep = skutečný dostupný přebytek, ať TČ topí, nebo stojí. Příkon TČ
je nativní Acond entita (ACOND_POWER) napevno, s auto-detekcí jednotky (W/kW) a měkkou
degradací na čistý přetok, když aep chybí.

VYBÍJENÍ BATERIE (0.17.5): badget má slepé místo – nepozná, že TČ topí Z BATERKY
(zimní podvečer: přetok 0, aep velký -> badget vypadá jako přebytek). Když je
vyplněný zdroj VÝKONU baterie (sdílený s topením podle přebytků, text
.mar_topeni_zdroj_baterie_vykon, normalizace „nabíjení +" vč. přepínače GoodWe),
přičte se k badgetu JEN VYBÍJENÍ: badget = přetok + aep + min(0, výkon baterie).
Nabíjení se NEpřičítá – jinak by ohřev ve dne přebíral baterce proud (START by
naskočil dřív, než baterka dobije). Zdroj výkonu chybí / je němý -> chování jako
dřív (pojistka navíc, ne tvrdá podmínka). Dvě formy:
  • avg_badget()   – 15min průměr (řídí START i grid-STOP); vzorkuje se na tiku (30 s,
                     rovnoměrná kadence -> přirozeně vyhlazený průměr).
  • badget_syrovy()– okamžitý přetok + okamžitý příkon (do grafu, bez zpoždění).
avg_export() zůstává (čistý přetok) pro sensor.mar_fve_prumer_pretoku a graf.

STAVOVÝ AUTOMAT (ověřeno simulacemi fve_sim.py + fve_sim_baterie.py)
--------------------------------------------------------
  START:  avg_badget > start  -> ZAPNI (půjč base 40005 + zachyť soc_start, zapiš max)
  Větev řídí PŘÍTOMNOST zdroje baterky:
   • BEZ baterky:  STOP když avg_badget < stop. Badget vidí pravdu i během boostu
       (přičte příkon TČ zpátky), takže se nevypne předčasně, když ohřev sám žere
       přebytek. Práh stop smí být i kladný (nech si rezervu, nejdi do importu).
   • S baterkou:   STOP když (SoC < práh X) A ZÁROVEŇ (soc_start − SoC ≥ pokles Y).
       Nad X se nevypíná (přemostí mrak shora z baterky); pod X s malým poklesem
       ještě ne. Badget baterku NEODMASKUJE (baterka pohltí přebytek dřív, než se
       stane přetokem; aep vrátí jen spotřebu TČ, ne nabíjení baterky) -> na STOP
       s baterkou zůstává SoC-pokles. (Výkon baterie od 0.17.5 jen odečte VYBÍJENÍ
       z badgetu – řeší falešný START z baterky, STOP dál řídí SoC.) Baterka unavailable uprostřed epizody ->
       drž a nesahej. START badgetem funguje i s baterkou (badget přirozeně zůstane
       nízký, dokud baterka přebytek hltá; vzroste, až přeteče nebo TČ maskuje).
  měkká pojistka: start < stop -> start := stop (žádná věčně běžící smyčka)

Dead-band + 15min průměr = žádný hunting. TUV-boost je jen změna setpointu
(kompresor spíná sám), takže explicitní min-runtime netřeba.

PŮJČ-A-VRAŤ: na ZAPNI zachyť base = aktuální 40005, na VYPNI vrať base DOSLOVA.
Base ve Store -> přežije restart (stejný kontrakt jako Dovolená / Okna +/- teploty).

GATE: řídí se jako ostatní programy – mimo Typ regulace Standard (_clean_slate)
se vypne a vrátí base. NEZÁVISLÉ na MaR-strategii (na rozdíl od Dovolené): je to
TUV (40005), ne zpátečka -> funguje pod jakoukoli strategií i v létě.

GUARD s Dovolenou je OBOUSTRANNÝ (switch.py): běží-li FVE (`active`), Dovolenou
nelze zapnout — a běží-li Dovolená, nelze zapnout FVE (dva executory nad 40005
se tak nikdy nepotkají). Guard je jen na VSTUPU (okamžik kliknutí).

ZDROJE jsou konfigurovatelné (uživatel vloží entity_id do text.mar_fve_zdroj_*):
přetok (znaménkový, kladné = export) -> průměr; výroba a baterie -> jen mirror/hold.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACOND_POWER,
    ACOND_TUV_SET,
    DOMAIN,
    FVE_BATT_LOST_S,
    FVE_RETRY_S,
    FVE_STORE_VERSION,
    FVE_TICK_S,
    FVE_TUV_FALLBACK,
    FVE_WINDOW_S,
    signal_fve_updated,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN = ("unknown", "unavailable", "", None)


class FveController:
    """FV-boost TUV: klouzavý průměr přetoku -> setpoint TUV (40005) na max/zpět."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._store: Store = Store(
            hass, FVE_STORE_VERSION, f"{DOMAIN}_fve_{entry.entry_id}"
        )

        # --- konfigurace (z entit) ---
        self.enabled = False                 # master switch.mar_fve_tuv
        self.start_thr = 0.0                 # W (kladné) – přepíše number
        self.stop_thr = 0.0                  # W (záporné) – přepíše number
        self.batt_prah = 0.0                 # % – spodní podmínka stopu (X)
        self.batt_pokles = 0.0               # % – povolený pokles SoC od startu (Y)
        self._src_pretok = ""                # entity_id zdroje přetoku
        self._src_vyroba = ""                # entity_id zdroje výroby (jen displej)
        self._src_baterie = ""               # entity_id zdroje baterie (%)

        # --- příkon TČ (aep) je NAPEVNO (nativní Acond entita), ne přes text ---
        self._src_aep = ACOND_POWER          # sensor.acond_30027_aep

        # --- výkon baterie půjčený z topení podle přebytků (nastaví __init__.py) ---
        self.topeni = None                   # TopeniController | None

        # --- běhový stav ---
        self._buffer: list[tuple[float, float]] = []   # (ts, W) čistý PŘETOK -> avg_export
        self._buffer_badget: list[tuple[float, float]] = []  # (ts, W) BADGET -> avg_badget
        self._active = False
        self._base_tuv: float | None = None
        self._soc_start: float | None = None   # SoC baterie zachycený při startu (Store)
        self._last_tuv: float | None = None
        self._started = False
        self._batt_lost_since: float | None = None   # od kdy je zdroj baterie němý (N5)

        # --- odběry ---
        self._unsub_pretok = None
        self._unsub_aep = None
        self._unsub_batt = None
        self._unsub_pv = None
        self._unsub_tick = None
        self._unsub_retry = None

    # ------------------------------------------------------------------ #
    # Nastavení z entit
    # ------------------------------------------------------------------ #
    def set_start(self, value: float) -> None:
        self.start_thr = float(value)

    def set_stop(self, value: float) -> None:
        self.stop_thr = float(value)

    def set_batt_pokles(self, value: float) -> None:
        self.batt_pokles = float(value)

    def set_batt_prah(self, value: float) -> None:
        self.batt_prah = float(value)

    def set_source_pretok(self, entity_id: str | None) -> None:
        eid = (entity_id or "").strip()
        if eid == self._src_pretok:
            return
        self._src_pretok = eid
        self._buffer.clear()          # zdroj se změnil -> staré vzorky neplatí
        self._buffer_badget.clear()   # badget stojí na přetoku -> taky zahoď
        self._sub_pretok()

    def set_source_vyroba(self, entity_id: str | None) -> None:
        eid = (entity_id or "").strip()
        if eid == self._src_vyroba:
            return
        self._src_vyroba = eid
        self._sub_pv()

    def set_source_baterie(self, entity_id: str | None) -> None:
        eid = (entity_id or "").strip()
        if eid == self._src_baterie:
            return
        self._src_baterie = eid
        self._sub_batt()

    # ------------------------------------------------------------------ #
    # Veřejné vlastnosti pro entity / guard
    # ------------------------------------------------------------------ #
    @property
    def active(self) -> bool:
        """Pro guard Dovolené a binary_sensor.mar_fve_aktivni = master zapnutý
        (ne nutně právě topí). „Vypni FV programy" = vypni master."""
        return self.enabled

    @property
    def boosting(self) -> bool:
        """Právě drží zvednutý TUV (pro banner / diagnostiku)."""
        return self._active

    def avg_export(self) -> float | None:
        """15min klouzavý průměr ČISTÉHO přetoku (W) nebo None, když nejsou vzorky.
        Zůstává kvůli sensor.mar_fve_prumer_pretoku a grafu (kontrola zaostávání)."""
        self._prune()
        if not self._buffer:
            return None
        return round(sum(v for _, v in self._buffer) / len(self._buffer), 0)

    def avg_badget(self) -> float | None:
        """15min klouzavý průměr BADGETU (přetok + příkon TČ) – řídí START i grid-STOP.
        Vzorkuje se na tiku (rovnoměrných 30 s) -> přirozeně vyhlazený průměr."""
        self._prune()
        if not self._buffer_badget:
            return None
        return round(sum(v for _, v in self._buffer_badget) / len(self._buffer_badget), 0)

    def badget_syrovy(self) -> float | None:
        """Okamžitý badget = přetok + příkon TČ + vybíjení baterie (W, záporné). Bez průměru.
        Přetok chybí -> None (nemá se o co opřít). Příkon chybí -> degraduj na přetok.
        Výkon baterie chybí -> bez korekce (jako do 0.17.4)."""
        exp = self._read_source_float(self._src_pretok)
        if exp is None:
            return None
        aep = self._read_aep()
        vyb = self.vybijeni_baterie()
        return round(exp + (aep or 0.0) + (vyb or 0.0), 0)

    def vybijeni_baterie(self) -> float | None:
        """Vybíjení baterie jako ZÁPORNÉ W (nabíjení -> 0). None = není baterka nebo
        zdroj výkonu baterie (pak se badget nekoriguje). Zdroj a znaménko sdílí
        topení podle přebytků (batt_vykon_value = „nabíjení +")."""
        if not self._src_baterie or self.topeni is None:
            return None
        try:
            bp = self.topeni.batt_vykon_value()
        except Exception:  # noqa: BLE001 — pojistka nesmí shodit ohřev
            return None
        if bp is None:
            return None
        return min(0.0, float(bp))

    def _read_aep(self) -> float | None:
        """Okamžitý příkon TČ (W). Auto-detekce jednotky: kW -> ×1000. None = nedostupné."""
        st = self.hass.states.get(self._src_aep)
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            val = float(st.state)
        except (ValueError, TypeError):
            return None
        unit = str(st.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit == "kw":              # příkon (výkon); W necháme, kW přepočteme
            val *= 1000.0
        return val

    def aep_value(self) -> float | None:
        """Pro mirror/graf/atributy: okamžitý příkon TČ ve W (po auto-jednotce)."""
        return self._read_aep()

    def soc_cil(self) -> float | None:
        """Odhad SoC, u kterého se s baterkou vypne: min(práh X, soc_start − pokles Y).
        None, když není baterka / soc_start. Jen pro popis ve stavové řádce."""
        if not self._src_baterie or self._soc_start is None:
            return None
        return round(min(self.batt_prah, self._soc_start - self.batt_pokles), 0)

    def pretok_value(self) -> float | None:
        """Okamžitý přetok (net_grid, W, + = export). Sdílí topení podle přebytků,
        ať se zdroj nezadává dvakrát."""
        return self._read_source_float(self._src_pretok)

    def battery_value(self) -> float | None:
        return self._read_source_float(self._src_baterie)

    def pv_value(self) -> float | None:
        return self._read_source_float(self._src_vyroba)

    # ------------------------------------------------------------------ #
    # Master + config
    # ------------------------------------------------------------------ #
    async def async_set_enabled(self, value: bool) -> None:
        self.enabled = bool(value)
        if self._started:
            await self.async_evaluate()
        self._notify()

    async def async_apply_config(self) -> None:
        """Volá se po živé změně parametru/zdroje z UI."""
        if self._started:
            await self.async_evaluate()
        self._notify()

    # ------------------------------------------------------------------ #
    # Start / obnova / vypnutí
    # ------------------------------------------------------------------ #
    async def async_resume_if_needed(self) -> None:
        """Po startu: obnov nevrácenou půjčku ze Store, nasaď odběry + tik, vyhodnoť."""
        data = await self._store.async_load()
        if data and data.get("active"):
            self._active = True
            self._base_tuv = data.get("base_tuv")
            self._soc_start = data.get("soc_start")
            # na registru je (z doby před restartem) boost target -> přednastav
            # _last_tuv, ať idempotentní zápis zbytečně nepřepisuje
            self._last_tuv = self._boost_target()
            _LOGGER.debug("FVE: obnovena půjčka base_tuv=%s", self._base_tuv)

        # odběry (idempotentní – text entity je možná už nasadily při setupu)
        self._sub_pretok()
        self._sub_aep()          # příkon TČ napevno (živý překres syrového badgetu)
        self._sub_pv()
        self._sub_batt()
        self._sample_badget()    # první vzorek badgetu hned, ať buffer nestartuje prázdný
        # periodický tik: vzorkuje badget, prořezává okno a hlídá stop, i když zdroj
        # zrovna nezmění stav
        self._unsub_tick = async_track_time_interval(
            self.hass, self._on_tick, timedelta(seconds=FVE_TICK_S)
        )
        self._started = True
        await self.async_evaluate()
        self._notify()

    def shutdown(self) -> None:
        for unsub in (self._unsub_pretok, self._unsub_aep, self._unsub_batt,
                      self._unsub_pv, self._unsub_tick, self._unsub_retry):
            if unsub is not None:
                unsub()
        self._unsub_pretok = self._unsub_aep = self._unsub_batt = None
        self._unsub_pv = None
        self._unsub_tick = self._unsub_retry = None

    # ------------------------------------------------------------------ #
    # Stavový automat
    # ------------------------------------------------------------------ #
    async def async_evaluate(self) -> None:
        # gate: master off nebo mimo Standard -> vrať a nesahej
        if not self.enabled or not self.coordinator.is_standard():
            await self._release()
            return

        avg = self.avg_badget()   # BADGET (přetok + příkon TČ), ne holý přetok
        if avg is None:
            return  # zatím nemáme vzorky -> drž aktuální stav (neškodné)

        # měkká pojistka: start nesmí být pod stop
        start = self.start_thr
        stop = self.stop_thr
        if start < stop:
            start = stop

        batt = self.battery_value()
        has_batt = bool(self._src_baterie)   # větev řídí PŘÍTOMNOST zdroje baterky

        if not self._active:
            if avg > start:
                await self._activate()       # zachytí soc_start (může být None)
            return

        # aktivní -> rozhodnutí o STOP
        if has_batt:
            # (b) baterka nedostupná uprostřed epizody: PŘECHODNÝ výpadek -> drž a
            # nesahej (grid je stejně maskovaný). Ale TRVALÁ ztráta (překlep v
            # entity_id, přejmenování po updatu, mrtvý měnič) nesmí držet boost
            # navěky – bez SoC tahle větev nemá žádnou stopku. Po timeoutu vrať
            # base, ukonči epizodu a řekni to nahlas (zrcadlí cidlo_timeout topení).
            if batt is None:
                now = dt_util.utcnow().timestamp()
                if self._batt_lost_since is None:
                    self._batt_lost_since = now
                elif (now - self._batt_lost_since) >= FVE_BATT_LOST_S:
                    self._batt_lost_since = None
                    await self._release()
                    await self.hass.services.async_call(
                        "persistent_notification", "create",
                        {
                            "title": "MaR – FVE TUV: ztráta zdroje baterie",
                            "message": (
                                "Zdroj SoC baterie je nedostupný déle než "
                                f"{int(FVE_BATT_LOST_S / 60)} min – boost TUV ukončen "
                                "a teplota vrácena. Zkontroluj entity_id zdroje "
                                "baterie (přejmenování po aktualizaci integrace?)."
                            ),
                            "notification_id": "mar_fve_batt_lost",
                        },
                        blocking=False,
                    )
                return
            self._batt_lost_since = None
            # pozdní zachycení, kdyby baterka byla při startu unavailable
            if self._soc_start is None:
                self._soc_start = batt
                await self._save()
            pokles = self._soc_start - batt
            # STOP: baterka pod prahem (X) A ZÁROVEŇ ukrojeno dost (pokles >= Y).
            # Nad X se nevypíná (přemostí mrak shora z baterky); pod X s malým poklesem
            # ještě ne. Grid se s baterkou neřeší (maskovaný).
            if batt < self.batt_prah and pokles >= self.batt_pokles:
                await self._release()
            return
        # bez baterky -> stop na BADGETU (přetok už nemaskuje vlastní ohřev).
        # Práh stop smí být i kladný (nech si rezervu, nejdi do importu).
        if avg < stop:
            await self._release()

    async def _activate(self) -> None:
        base = self._num(ACOND_TUV_SET)
        if base is None:
            self._schedule_retry()      # registr TUV zatím nedostupný -> zkus později
            return
        self._base_tuv = base
        self._soc_start = self.battery_value()   # SoC při startu (None bez baterky/unavailable)
        self._active = True
        await self._save()
        _LOGGER.debug("FVE: půjčka base_tuv=%s -> boost", base)
        await self._write(ACOND_TUV_SET, self._boost_target())
        await self.coordinator.async_request_refresh()

    async def _release(self) -> None:
        if self._active:
            if self._base_tuv is not None:
                await self._write(ACOND_TUV_SET, self._base_tuv)
            await self.coordinator.async_request_refresh()
            _LOGGER.debug("FVE: vráceno base_tuv=%s", self._base_tuv)
        self._active = False
        self._base_tuv = None
        self._soc_start = None
        self._last_tuv = None
        self._batt_lost_since = None
        await self._clear_store()

    def _boost_target(self) -> float:
        """Cíl boostu = horní mez 40005 (max atribut number.acond_40005_t_set_tuv).
        Standard 50 °C, servisem výš. Fallback na konstantu, když atribut chybí."""
        st = self.hass.states.get(ACOND_TUV_SET)
        if st is not None:
            mx = st.attributes.get("max")
            try:
                return float(mx)
            except (ValueError, TypeError):
                pass
        _LOGGER.warning("FVE: max atribut %s nedostupný -> fallback %s °C",
                        ACOND_TUV_SET, FVE_TUV_FALLBACK)
        return float(FVE_TUV_FALLBACK)

    # ------------------------------------------------------------------ #
    # Buffer průměru + odběry zdrojů
    # ------------------------------------------------------------------ #
    def _sample(self, value: float) -> None:
        self._buffer.append((dt_util.utcnow().timestamp(), float(value)))
        self._prune()

    def _prune(self) -> None:
        cutoff = dt_util.utcnow().timestamp() - FVE_WINDOW_S
        self._buffer = [(t, v) for (t, v) in self._buffer if t >= cutoff]
        self._buffer_badget = [(t, v) for (t, v) in self._buffer_badget if t >= cutoff]

    def _sample_badget(self) -> None:
        """Vzorek badgetu do bufferu (na tiku, rovnoměrná 30s kadence)."""
        b = self.badget_syrovy()
        if b is not None:
            self._buffer_badget.append((dt_util.utcnow().timestamp(), b))
            self._prune()

    def _sub_pretok(self) -> None:
        if self._unsub_pretok is not None:
            self._unsub_pretok()
            self._unsub_pretok = None
        if not self._src_pretok:
            return
        self._unsub_pretok = async_track_state_change_event(
            self.hass, [self._src_pretok], self._on_pretok_change
        )
        v = self._read_source_float(self._src_pretok)
        if v is not None:
            self._sample(v)

    def _sub_aep(self) -> None:
        # Příkon TČ je napevno; odběr slouží k živému překreslení syrového badgetu.
        # Rozhodnutí (avg_badget) běží na tiku, tady jen notify (a lehké evaluate).
        if self._unsub_aep is not None:
            self._unsub_aep()
            self._unsub_aep = None
        self._unsub_aep = async_track_state_change_event(
            self.hass, [self._src_aep], self._on_aep_change
        )

    def _sub_pv(self) -> None:
        if self._unsub_pv is not None:
            self._unsub_pv()
            self._unsub_pv = None
        if self._src_vyroba:
            self._unsub_pv = async_track_state_change_event(
                self.hass, [self._src_vyroba], self._on_mirror_change
            )

    def _sub_batt(self) -> None:
        if self._unsub_batt is not None:
            self._unsub_batt()
            self._unsub_batt = None
        if self._src_baterie:
            self._unsub_batt = async_track_state_change_event(
                self.hass, [self._src_baterie], self._on_mirror_change
            )

    @callback
    def _on_pretok_change(self, event) -> None:  # noqa: ANN001
        new = event.data.get("new_state")
        if new is None or new.state in _UNKNOWN:
            return
        try:
            self._sample(float(new.state))
        except (ValueError, TypeError):
            return
        if self._started:
            self.hass.async_create_task(self._evaluate_and_notify())

    @callback
    def _on_mirror_change(self, event) -> None:  # noqa: ANN001
        # výroba/baterie: jen překresli mirror sensory (a baterie může měnit hold)
        if self._started:
            self.hass.async_create_task(self._evaluate_and_notify())
        else:
            self._notify()

    @callback
    def _on_aep_change(self, event) -> None:  # noqa: ANN001
        # Příkon TČ se hýbe často (modulace kompresoru). Neblokujeme tím rozhodování
        # (to jede na tiku z avg_badget) – jen překreslíme syrový badget v UI/grafu.
        self._notify()

    @callback
    def _on_tick(self, now) -> None:  # noqa: ANN001
        self._sample_badget()    # rovnoměrný vzorek badgetu (30 s) -> vyhlazený průměr
        self.hass.async_create_task(self._evaluate_and_notify())

    async def _evaluate_and_notify(self) -> None:
        await self.async_evaluate()
        self._notify()

    # ------------------------------------------------------------------ #
    # Zápis / čtení
    # ------------------------------------------------------------------ #
    async def _write(self, entity_id: str, value: float) -> None:
        v = round(float(value), 1)
        if self._last_tuv is not None and round(self._last_tuv, 1) == v:
            return
        self._last_tuv = v
        await self.hass.services.async_call(
            "number", "set_value",
            {"entity_id": entity_id, "value": v},
            blocking=False,
        )

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

    def _read_source_float(self, entity_id: str) -> float | None:
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ #
    # Retry timer (registr TUV nedostupný při aktivaci)
    # ------------------------------------------------------------------ #
    def _schedule_retry(self) -> None:
        if self._unsub_retry is not None:
            return

        @callback
        def _fire(now) -> None:  # noqa: ANN001
            self._unsub_retry = None
            self.hass.async_create_task(self._evaluate_and_notify())

        self._unsub_retry = async_track_point_in_time(
            self.hass, _fire, dt_util.utcnow() + timedelta(seconds=FVE_RETRY_S)
        )

    # ------------------------------------------------------------------ #
    # Store + dispatch + atributy
    # ------------------------------------------------------------------ #
    async def _save(self) -> None:
        await self._store.async_save(
            {"active": self._active, "base_tuv": self._base_tuv, "soc_start": self._soc_start}
        )

    async def _clear_store(self) -> None:
        await self._store.async_save({})

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_fve_updated(self.entry.entry_id))

    def as_attr(self) -> dict:
        return {
            "nazev": "Ohřev TUV z přebytku",
            "aktivni": self.enabled,
            "topi": self._active,
            "prumer_pretoku": self.avg_export(),
            "badget": self.avg_badget(),            # průměr (řídí START)
            "badget_syrovy": self.badget_syrovy(),  # okamžitý (přetok + příkon TČ)
            "prikon_tc": self.aep_value(),          # okamžitý příkon TČ (W)
            "vybijeni_baterie": self.vybijeni_baterie(),  # W, záporné; None = bez korekce
            "start": self.start_thr,
            "stop": self.stop_thr,
            "ma_baterku": bool(self._src_baterie),
            "baterie": self.battery_value(),
            "baterie_prah": self.batt_prah,
            "baterie_pokles": self.batt_pokles,
            "soc_start": self._soc_start,
            "soc_cil": self.soc_cil(),              # kolem kolika % baterky vypnu
            "base_tuv": self._base_tuv,
        }
