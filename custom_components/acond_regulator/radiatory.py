"""R-Ekviterma – vrstva pod Ekvitermou pro radiátory se silným čerpadlem.

(Původně jen bez AKU; 15. 11. 2026 Pavle podmínku zrušil – malá taktovací nádrž
kolem 200–300 l chování nemění, u Romana s 200 l R běží.)

PROČ
----
Rychlá soustava (radiátory, málo vody) a silné čerpadlo: Acond po startu šlápne
naplno, zpátečka i místnost letí nahoru, kompresor vypne na +2 °C a cvaká
(simulace: 25–40 startů denně). Když je ztráta domu menší než minimální výkon,
musí se přebytek někam uložit – R ho ukládá do hmoty domu: dlouhé běhy, dlouhé
pauzy, pár startů denně.

JAK (zamčeno 9.–10. 11. 2026, viz const.py „R-Ekviterma")
----------------------------------------------------------
- Výkon řídí STROP 40014 (zápis platí do minuty). Start vždy z minima.
- Rampa: po R_PERIODA_S přidej krok, „když není brzda". Brzda = zpátečka dosáhla
  křivky (mimo dotápění) nebo místnost nad cílem (od cíl + 0,1) -> uber 2 kroky.
  Místnost na cíli v souvislém topení nebo rychlý růst místnosti -> strop drž (0.18.0b33;
  dřív brzda přímo na cíli srážela výkon na minimum a dům žil o 0,1 pod cílem). Na cíli
  se brzdí postaru v hysterezi (pauza/stop v posledních 3 h), když místnost roste
  (> 0,15 K/h) nebo svítí FVE.
- 40008: při rampě nahoru návnada (skutečná + 1 -> Acond tlačí do stropu), při
  brzdě křivka. Vždy honička: nikdy pod skutečná − 1,5, takže Acond na +2 sám
  nevypne.
- Pásmo: nad cíl + Pauza nad -> pauza KAMENEM NETOPIT (podle Režimu vypínání: VYP /
  Léto / Útlum – uživatel si tak volí i TUV a protáčení). Pod cíl − Start pod ->
  kámen Topit se stropem na minimu. Pauzu, kterou přebil jiný kámen (uživatel,
  Zebra, Den/noc), R nikdy sám neukončí.
- Po odmrazu dál stejně (3 min bez brzdy zpátečky – zbytkové teplo). Po TUV ze
  stropu před TUV, v dotápění dvojnásobným krokem. Brzda zpátečky má hysterezi
  a návnada se mění jen při kroku, ať požadovaná nekmitá s čidlem.
- Odchod z R (vypínač, jiná strategie, Bez MaR, mimo Standard) vrátí slider.
- Konec TUV (16. 11. 2026): zpátečkou teče zbytek z bojleru, žádaná = křivka by
  Acond ve Standardu vypnul. 3 min žádaná na maximum, pak honička dolů za vodou;
  stop kompresoru do 15 min po TUV je prodleva Acondu, ne nový start z minima.
- POSUN KŘIVKY (I složka; 16. 11. 2026, pravidlo C od 0.18.0b29): koeficient korekce
  (P) sám nechá místnost kousek pod cílem (u testera celé noci −0,3).
  NAHORU: každou celou hodinu okno posledních 3 h (minuty TUV se nepočítají) – nebyla
  pauza R ani stop kompresoru (mimo TUV a 15 min po ní), místnost ANI JEDNOU nedosáhla
  cíle a průměr je pod cílem − 0,1 -> posun += k × (cíl − průměr), pak čekat celé
  nové okno. Propad (větrání, TUV, výpadek) sníží průměr, ale ne maximum.
  DOLŮ z hystereze: pauza pásma po hodině, kdy R jela aspoň polovinu běhu nad minimem
  stropu -> posun −= k × 0,2 (křivka dala víc, než dům chtěl). Pauza po běhu na minimu
  = minimum je na dům moc, křivka za to nemůže. Ne, když vyráběla FVE.
  Z místnosti se dolů neučí: vysokou křivku zastaví brzda místnosti, pak hystereze,
  takže v místnosti se neukáže. 3 h po změně cíle se neučí. k = 1 + (střední voda −
  místnost) / (1,3 × (místnost − venku)) z měření, 1,1–2,0. Posun ±5 °C, přežije
  restart, vypnutí volby Radiátory ho vynuluje; b29 jednou vynuluje posun ze starého pravidla.
- TUV V PAUZE (0.18.0b29): kompresor už běží na bojler -> když je místnost do 0,1 °C
  nad startem, R ukončí pauzu kamenem Topit ještě při TUV (strop nemění) a po TUV se
  topí dál jako po TUV za běhu – bez stopu a nového startu (tester 21. 11. 10:24).

PŘEDNOSTI
---------
Kámen (suspend_write), útlum Netopit, FVE Topení (topeni_owns), léto, vypnutý
zápis -> R mlčí. Coordinator pod R nezapisuje 40008 (r_owns), výjimkou je útlum.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACOND_BIT_DEFROST,
    ACOND_BIT_TUV,
    ACOND_CAPACITY,
    ACOND_COMP_BIT,
    ACOND_HP_ON,
    ACOND_INDOOR,
    ACOND_OUTDOOR,
    ACOND_OUTLET,
    ACOND_RETURN_ACT,
    ACOND_RETURN_READBACK,
    ACOND_RPM,
    ACOND_SUMMER,
    ACOND_TARGET,
    BOOST,
    DOMAIN,
    MAX_RETURN,
    R_DOTAP,
    R_HONICKA_REZERVA,
    R_DRZ_RUST,
    R_DRZ_SOUVISLE_S,
    R_KROK_DOLU,
    R_KROK_NAHORU,
    R_MIN_BEH_S,
    R_MIST_NAD,
    R_MIN_PAUZA_S,
    R_NAVNADA,
    R_ODMRAZ_DOBEH_S,
    R_TUV_BOOST,
    R_TUV_NAVAZ,
    R_ZP_HYST,
    R_PAUZA_DEFAULT,
    R_PERIODA_S,
    R_PO_TUV_DRZ_S,
    R_PO_TUV_OKNO_S,
    R_POSUN_CIL_ZMRAZ_S,
    R_POSUN_DOLU,
    R_POSUN_EXP,
    R_POSUN_FVE_W,
    R_POSUN_K_DEFAULT,
    R_POSUN_K_DT_MIN,
    R_POSUN_K_MAX,
    R_POSUN_K_MIN,
    R_POSUN_MAX,
    R_POSUN_NAD_MIN,
    R_POSUN_OKNO_S,
    R_POSUN_PASMO,
    R_POSUN_PRED_PAUZOU_S,
    R_POSUN_VERZE,
    R_POSUN_VZOREK_S,
    R_POSUN_ZISK,
    R_RUST_BRZDA,
    R_RUST_MIN_S,
    R_RUST_OKNO_S,
    R_START_DEFAULT,
    R_STOP_DIFF,
    R_STORE_VERSION,
    R_TIK_S,
    STRATEGY_EKVITERM,
    signal_r_updated,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN = ("unknown", "unavailable", "", None)

STAV_VYP = "vypnuto"
STAV_JINA = "jiná strategie"
STAV_CEKA = "čeká"
STAV_BEH = "běh"
STAV_DOTAP = "dotápění"
STAV_PAUZA = "pauza"
STAV_START = "start"
STAV_TUV = "TUV/odmraz"
STAV_FVE = "vede FVE Topení"


class RadiatoryController:
    """Smyčka R-Ekvitermy. Zapíná ji switch.mar_ekviterma_radiatory."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, run, seq,
                 fve=None) -> None:
        self.hass = hass
        self.fve = fve                     # jen kvůli výrobě FVE (posun: slunce)
        self.entry = entry
        self.coordinator = coordinator
        self.run = run
        self.seq = seq
        self._store: Store = Store(hass, R_STORE_VERSION, f"{DOMAIN}_radiatory_{entry.entry_id}")

        self.enabled = False
        self.pauza_nad = R_PAUZA_DEFAULT
        self.start_pod = R_START_DEFAULT

        self._held = False                 # máme půjčený slider 40014
        self._orig_cap: float | None = None
        self._paused = False
        self._pause_since = 0.0
        self._run_since = 0.0
        self._last_step = 0.0
        self._was_running: bool | None = None
        self._frozen = False               # běží TUV/odmraz
        self._frozen_tuv = False           # …a byl v tom ohřev TUV
        self._tuv_boost = False            # po TUV v dotápění: dvojnásobný krok nahoru
        self._odmraz_konec = -1e9          # kdy skončil odmraz (zbytkové teplo)
        self._zp_brzda = False             # brzda „zpátečka na křivce" (s hysterezí)
        self._navnada: float | None = None # návnada platná do dalšího kroku
        self._honicka: float | None = None # honička platná do dalšího kroku
        self._room = deque()               # (monotonic, °C) za poslední hodinu
        self._tuv_konec = -1e9             # kdy skončil ohřev TUV

        # posun křivky (I složka, pravidlo C)
        self.posun = 0.0
        self.posun_k = R_POSUN_K_DEFAULT
        self.posun_info: str | None = None
        self._cil_zmena = -1e9             # kdy se naposled změnil cíl místnosti
        self._posun_cil: float | None = None
        self._p_key = None                 # celá hodina, na jejímž konci se vyhodnocuje
        # vzorky do okna: (monotonic, místnost, TUV, topí dům, nad minimem, FVE, (voda − m, m − venku))
        self._p_vzorky: deque = deque()
        self._p_mimo = time.monotonic()    # kdy naposled R nevedla (jiná strategie, mlčí, data…)
        self._p_stop = time.monotonic()    # kdy naposled pauza R / stál kompresor mimo TUV
        self._p_krok = -1e9                # kdy byl poslední krok nahoru
        self._busy = False
        self._unsubs: list = []

        # diagnostika (atributy vypínače, budoucí graf / CSV)
        self.stav = STAV_VYP
        self.duvod: str | None = None
        self.rust: float | None = None
        self.cil_krivka: float | None = None
        self.zadana: float | None = None
        self.acond_vypnul = 0

    # ------------------------------------------------------------------ #
    # Veřejné
    # ------------------------------------------------------------------ #
    def as_attr(self) -> dict:
        return {
            "aktivni": self.enabled,
            "stav": self.stav,
            "brzda": self.duvod,
            "strop": self._num(ACOND_CAPACITY) if self._held else None,
            "cil_z_krivky": self.cil_krivka,
            "zapsana_zadana": self.zadana,
            "rust_mistnosti": "—" if self.rust is None
            else f"{self.rust:+.2f} K/h".replace(".", ","),
            "pauza_nad": self.pauza_nad,
            "start_pod": self.start_pod,
            "acond_vypnul": self.acond_vypnul,
            "posun": round(self.posun, 1),
            "posun_k": round(self.posun_k, 2),
            "posun_info": self.posun_info,
        }

    def veta(self) -> str | None:
        """Přípona věty do Stavu regulace. None, když R neběží."""
        if not self.enabled or self.stav in (STAV_VYP, STAV_JINA):
            return None
        cil = self.coordinator.room_set()
        cz = lambda v: "—" if v is None else f"{float(v):.1f}".replace(".", ",")  # noqa: E731
        posun = ""
        if abs(self.posun) >= 0.05:
            posun = f" · křivka {self.posun:+.1f} °C (doučeno)".replace(".", ",")
        if self.stav == STAV_PAUZA and cil is not None:
            return f"R-Ekviterma · pauza, start pod {cz(cil - self.start_pod)} °C{posun}"
        if self.stav in (STAV_BEH, STAV_DOTAP) and cil is not None:
            s = f"R-Ekviterma · {self.stav}, pauza od {cz(cil + self.pauza_nad)} °C"
            return s + (f" (brzda: {self.duvod})" if self.duvod else "") + posun
        if self.stav == STAV_START and self.duvod:
            return f"R-Ekviterma · {self.duvod}{posun}"
        return f"R-Ekviterma · {self.stav}{posun}"

    async def async_set_enabled(self, value: bool) -> None:
        self.enabled = bool(value)
        if not self.enabled:
            # vypnutí volby = nový začátek: doučený posun zahodit
            if self.posun:
                self.posun = 0.0
                self.posun_info = "vynulováno (volba vypnuta)"
                await self._save()
            await self.async_release()
        await self._tick()

    async def async_apply_config(self) -> None:
        """Změna čísla v UI (number) -> přepočti hned."""
        await self._tick()

    def set_pauza(self, value: float) -> None:
        self.pauza_nad = float(value)

    def set_start(self, value: float) -> None:
        self.start_pod = float(value)

    async def async_load(self) -> None:
        """Načti půjčku ze Store (před čistým stolem, ať je co vracet)."""
        data = await self._store.async_load()
        if data and data.get("held"):
            self._held = True
            self._orig_cap = data.get("orig")
        if data:
            self.acond_vypnul = int(data.get("acond_vypnul") or 0)
            try:
                self.posun = float(data.get("posun") or 0.0)
                self.posun_k = float(data.get("posun_k") or R_POSUN_K_DEFAULT)
            except (TypeError, ValueError):
                self.posun, self.posun_k = 0.0, R_POSUN_K_DEFAULT
            if data.get("posun_v") != R_POSUN_VERZE:
                # posun naučený starým pravidlem (hodinovým) se v cyklování mýlil -> jednou zahodit
                if self.posun:
                    _LOGGER.info("R-Ekviterma: nové pravidlo posunu, posun %.2f vynulován", self.posun)
                    self.posun_info = "vynulováno (nové pravidlo učení)"
                self.posun = 0.0
                await self._save()

    async def async_start(self) -> None:
        """Po startu HA (po platformách a pokojovce): nasaď smyčku."""
        self._unsubs.append(async_track_time_interval(
            self.hass, self._on_time, timedelta(seconds=R_TIK_S)))
        self._unsubs.append(async_track_state_change_event(
            self.hass,
            [ACOND_COMP_BIT, ACOND_BIT_TUV, ACOND_BIT_DEFROST, ACOND_RETURN_ACT, ACOND_INDOOR],
            self._on_event,
        ))
        self._unsubs.append(self.coordinator.async_add_listener(self._on_coord))
        await self._tick()

    def shutdown(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()

    async def async_release(self) -> None:
        """Odchod z R: vrať uživatelův slider, uvolni 40008 ekvitermě."""
        self.coordinator.r_owns = False
        self.coordinator.r_target = None
        self._paused = False
        if self._held:
            self._held = False
            if self._orig_cap is not None:
                await self._set(ACOND_CAPACITY, self._orig_cap)
            _LOGGER.debug("R-Ekviterma: odchod, slider vrácen na %s", self._orig_cap)
            self._orig_cap = None
            await self._save()
            await self.coordinator.async_request_refresh()
        self.stav = STAV_VYP if not self.enabled else STAV_JINA
        self._notify()

    # ------------------------------------------------------------------ #
    # Smyčka
    # ------------------------------------------------------------------ #
    @callback
    def _on_time(self, _now) -> None:  # noqa: ANN001
        self.hass.async_create_task(self._tick())

    @callback
    def _on_event(self, _event) -> None:  # noqa: ANN001
        self.hass.async_create_task(self._tick())

    @callback
    def _on_coord(self) -> None:
        if not self._busy:
            self.hass.async_create_task(self._tick())

    def _drzi(self) -> bool:
        return (
            self.enabled
            and self.coordinator.strategy == STRATEGY_EKVITERM
            and self.coordinator.is_standard()
        )

    async def _tick(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            await self._krok()
        finally:
            self._busy = False
            self._notify()

    async def _krok(self) -> None:
        if not self._drzi():
            self._p_mimo = time.monotonic()
            if self._held or self.coordinator.r_owns:
                await self.async_release()
            else:
                self.stav = STAV_VYP if not self.enabled else STAV_JINA
            return

        c = self.coordinator
        if getattr(c, "topeni_owns", False):
            # FVE Topení drží 40008 i 40014 -> uhni; po předání pokračuj z jeho stropu
            c.r_owns = False
            self.stav = STAV_FVE
            self._p_mimo = time.monotonic()
            return

        if not self._held:
            self._orig_cap = self._num(ACOND_CAPACITY)
            self._held = True
            self._paused = False
            await self._save()
            # převzetí za běhu: strop rovnou na skutečné otáčky (žádný skok), jinak minimum
            lo, _hi = self._rozsah()
            akt = self._num(ACOND_RPM) if self.run.kompresor_bezi is True else None
            if lo is not None:
                await self._set_cap(max(lo, akt) if akt is not None else lo)
            self._last_step = time.monotonic()
            _LOGGER.debug("R-Ekviterma: převzato, slider uživatele %s", self._orig_cap)
        c.r_owns = True

        now = time.monotonic()
        room = self._num(ACOND_INDOOR)
        if room is not None:
            self._room.append((now, room))
            while self._room and now - self._room[0][0] > R_RUST_OKNO_S:
                self._room.popleft()
        self.rust = self._rust(now)

        cil = c.room_set()
        ret = self._num(ACOND_RETURN_ACT)
        data = c.data
        krivka = getattr(data, "return_final", None) if data else None
        lo, hi = self._rozsah()
        if None in (cil, ret, room, krivka, lo, hi):
            self._p_mimo = time.monotonic()
            if not self._paused:
                self.stav = STAV_CEKA
            return
        # posun: vzorek do okna, na přelomu hodiny vyhodnocení (pravidlo C)
        await self._posun_tik(now, room, cil, ret, lo)
        krivka = krivka + self.posun
        self.cil_krivka = round(krivka, 1)

        # --- vlastní pauza R (kámen Netopit podle Režimu vypínání) ---
        if self._paused:
            if self.seq.last_origin != "r":
                # pauzu přebil jiný kámen (uživatel, Zebra, Den/noc) -> už není naše;
                # R ji NIKDY sám neukončí, dál jedou běžné brány níž
                self._paused = False
            else:
                self._was_running = None
                self.duvod = None
                # TUV v pauze: kompresor už běží na bojler -> když je místnost do
                # R_TUV_NAVAZ nad startem, ukonči pauzu hned při TUV a po TUV se
                # plynule topí dál (jako TUV za běhu) místo stopu a nového startu
                tuv_v_pauze = self._on(ACOND_BIT_TUV)
                prah = cil - self.start_pod + (R_TUV_NAVAZ if tuv_v_pauze else 0.0)
                if (
                    not c.suspend_write and c.write_enabled
                    and room <= prah
                    and now - self._pause_since >= R_MIN_PAUZA_S
                ):
                    if not tuv_v_pauze:
                        await self._set_cap(lo)               # start z minima
                    # při TUV strop nesahat: řada PRO ho má na ohřev uvolněný a po TUV
                    # vrátí strop před TUV (minimum z pauzy) – topení naváže z minima
                    self._paused = False
                    self._last_step = now
                    self.seq.fire_start(origin="r")           # kámen Topit (při TUV jen zruší útlum)
                    self.stav = STAV_START
                    self.duvod = "TUV v pauze – navazuji topením" if tuv_v_pauze else None
                    return
                self.stav = STAV_PAUZA
                return

        # R mlčí (nezapisuje), ale drží půjčku
        if (
            not c.write_enabled
            or c.suspend_write
            or c.utlum_pz is not None
            or self._on(ACOND_SUMMER)
            or self._off(ACOND_HP_ON)          # TČ na VYP (Netopit, Den/noc, Zebra)
        ):
            self.stav = STAV_CEKA
            self._was_running = None
            self._p_mimo = time.monotonic()
            return

        krok_up = (hi - lo) * R_KROK_NAHORU
        krok_dn = (hi - lo) * R_KROK_DOLU
        dot = room < cil - R_DOTAP

        # --- TUV / odmraz: rampa zmrazená, nic nepíšeme ---
        # Strop R během nich nemění. Odmraz ho ignorují všechny řady; při TUV ho
        # Grandis ignoruje sám a řadě PRO ho na dobu TUV uvolní tuv_strop.py
        # (maximum, po TUV zpět na strop před TUV – odtud R pokračuje).
        tuv = self._on(ACOND_BIT_TUV)
        if tuv or self._on(ACOND_BIT_DEFROST):
            self._frozen = True
            self._frozen_tuv = self._frozen_tuv or tuv
            self.stav = STAV_TUV
            return
        if self._frozen:
            self._frozen = False
            if self._frozen_tuv:
                # KONEC TUV: pokračuj ze stropu před TUV (Acond nedožene víc než to);
                # po dlouhém koupání v dotápění přidávej dvojnásobným krokem
                self._tuv_boost = dot
                self._last_step = now
                self._tuv_konec = now
            else:
                # KONEC ODMRAZU (do 5 min): pokračuj normálně, jen R_ODMRAZ_DOBEH_S
                # neber vážně brzdu zpátečky – voda je ohřátá odmrazem, ne domem
                self._odmraz_konec = now
            self._frozen_tuv = False
            self._navnada = None
            self._honicka = None
            if self._tuv_konec == now:
                # zpátečkou teče zbytek z bojleru -> žádaná na maximum (Standard by
                # jinak vypnul na zpátečka ≥ žádaná + 2), pak honička dolů za vodou
                await self._write_target(self._clamp(MAX_RETURN))
            else:
                await self._write_target(self._clamp(max(krivka, ret + R_NAVNADA)) if dot
                                         else self._clamp(krivka))
        if not dot:
            self._tuv_boost = False

        bezi = self.run.kompresor_bezi is True
        po_tuv = now - self._tuv_konec < R_PO_TUV_OKNO_S
        if self._was_running is True and not bezi and not po_tuv:
            self.acond_vypnul += 1          # vypnul sám (ne pauza, ne kámen, ne TUV)
            await self._save()
        if bezi and self._was_running is not True:
            self._run_since = now
            self._last_step = now
        self._was_running = bezi

        # --- pásmo: nad cíl + Pauza nad -> kámen Netopit ---
        if room >= cil + self.pauza_nad:
            if bezi and now - self._run_since >= R_MIN_BEH_S and not self.seq.busy:
                await self._posun_pauza(now, cil)             # hystereze -> případně dolů
                self._paused = True
                self._pause_since = now
                self._was_running = None
                self.stav = STAV_PAUZA
                self.duvod = None
                await self._set_cap(lo)                       # připrav start z minima
                self.seq.fire_stop(origin="r")
                return
            if not bezi:
                # teplo a stojí (Acond sám, cizí kámen): nelákej start, jen drž křivku
                self.stav = STAV_CEKA
                self.duvod = "místnost nad pásmem"
                await self._write_target(self._clamp(krivka))
                return

        # --- kompresor stojí (po pauze, kámen, Acond sám): start z minima ---
        if not bezi:
            self.stav = STAV_START
            if po_tuv:
                # prodleva Acondu po TUV: strop před TUV nechat, jen lákat start
                self.duvod = "po TUV, čeká na Acond"
            else:
                self.duvod = None
                await self._set_cap(lo)
            await self._write_target(self._clamp(max(krivka, ret + BOOST)))
            return

        # --- 3 min po TUV: žádaná na maximum, zbytek z bojleru proteče ---
        if now - self._tuv_konec < R_PO_TUV_DRZ_S:
            self.stav = STAV_DOTAP if dot else STAV_BEH
            self.duvod = "po TUV"
            self._navnada = None
            self._honicka = None
            await self._write_target(self._clamp(MAX_RETURN))
            return

        # --- běh: rampa stropu ---
        # brzda zpátečky s hysterezí: sepne na křivce, pustí až R_ZP_HYST pod ní
        # (čidlo kmitá o 0,1 – bez hystereze skákala požadovaná o celý stupeň)
        po_odmrazu = now - self._odmraz_konec < R_ODMRAZ_DOBEH_S
        if dot or po_odmrazu:
            self._zp_brzda = False
        elif ret >= krivka:
            self._zp_brzda = True
        elif ret <= krivka - R_ZP_HYST:
            self._zp_brzda = False
        tvrda = []
        if self._zp_brzda:
            tvrda.append("zpátečka na křivce")
        if ret >= MAX_RETURN - 1:
            tvrda.append("strop zpátečky")
        # místnost: v souvislém topení na cíli strop DRŽ (brzda přímo na cíli dělala strop –
        # dům žil o 0,1 pod), tvrdě brzdi od cíl + R_MIST_NAD. Na cíli brzdi postaru v
        # hysterezi (pauza/stop v posledních 3 h – držení by zkrátilo běh do pauzy a učilo
        # posun falešně dolů), při úzkém pásmu, když místnost roste nebo svítí FVE.
        drz = False
        if room >= cil + R_MIST_NAD - 1e-6:
            tvrda.append("místnost nad cílem")
        elif room >= cil - 1e-6:
            if self.pauza_nad <= R_MIST_NAD + 1e-6 or now - self._p_stop < R_DRZ_SOUVISLE_S:
                tvrda.append("místnost v cíli")
            elif self.rust is not None and self.rust > R_DRZ_RUST:
                tvrda.append("místnost v cíli a roste")
            elif self._fve_sviti():
                tvrda.append("místnost v cíli, svítí FVE")
            else:
                drz = True
        mekka = drz or (self.rust is not None and self.rust > R_RUST_BRZDA)
        if tvrda:
            self.duvod = ", ".join(tvrda)
        elif drz:
            self.duvod = "místnost v cíli – držím"
        else:
            self.duvod = "růst místnosti" if mekka else None
        self.stav = STAV_DOTAP if dot else STAV_BEH

        cap = self._num(ACOND_CAPACITY)
        krok = now - self._last_step >= R_PERIODA_S
        if cap is not None and krok:
            self._last_step = now
            if tvrda:
                await self._set_cap(max(lo, cap - krok_dn))
            elif not mekka:
                nasobek = R_TUV_BOOST if self._tuv_boost else 1
                await self._set_cap(min(hi, cap + krok_up * nasobek))
        # Návnada / křivka se rozhoduje jen při kroku (ne každých 30 s), ať požadovaná
        # nekmitá s čidlem. Okamžitě jen tvrdé brzdy (místnost, strop zpátečky).
        if krok or self._navnada is None:
            self._navnada = None if (tvrda or mekka) else round(ret + R_NAVNADA, 1)
            self._honicka = None
        if tvrda:
            self._navnada = None
        zadana = max(krivka, self._navnada) if self._navnada is not None else krivka
        # honička: zasáhne, jen když zpátečce chybí do vypnutí méně než R_HONICKA_REZERVA,
        # a pak drží s rezervou do dalšího kroku (žádné kopírování čidla)
        if ret - zadana > R_STOP_DIFF - R_HONICKA_REZERVA:
            self._honicka = round(ret - R_STOP_DIFF + 2 * R_HONICKA_REZERVA, 1)
        if self._honicka is not None:
            zadana = max(zadana, self._honicka)
        await self._write_target(self._clamp(zadana))

    # ------------------------------------------------------------------ #
    # Nástroje
    # ------------------------------------------------------------------ #
    def _rust(self, now: float) -> float | None:
        if len(self._room) < 2:
            return None
        t0, r0 = self._room[0]
        if now - t0 < R_RUST_MIN_S:
            return None
        t1, r1 = self._room[-1]
        return round((r1 - r0) * 3600 / (t1 - t0), 2) if t1 > t0 else None

    def _fve_sviti(self) -> bool:
        """Vyrábí FVE víc než R_POSUN_FVE_W (slunce hřeje i dům). Bez FVE False."""
        if self.fve is None:
            return False
        try:
            pv = self.fve.pv_value()
        except Exception:  # noqa: BLE001
            return False
        return pv is not None and pv > R_POSUN_FVE_W

    def _rozsah(self) -> tuple[float | None, float | None]:
        return self._attr(ACOND_CAPACITY, "min"), self._attr(ACOND_CAPACITY, "max")

    def _clamp(self, v: float) -> float:
        lo = self._attr(ACOND_TARGET, "min")
        hi = self._attr(ACOND_TARGET, "max")
        v = min(v, MAX_RETURN)
        if hi is not None:
            v = min(v, hi)
        if lo is not None:
            v = max(v, lo)
        return round(v, 1)

    async def _set_cap(self, value: float) -> None:
        step = self._attr(ACOND_CAPACITY, "step") or 1.0
        v = round(round(value / step) * step, 1)
        cur = self._num(ACOND_CAPACITY)
        if cur is not None and abs(cur - v) < step / 2:
            return
        await self._set(ACOND_CAPACITY, v)

    async def _write_target(self, value: float) -> None:
        self.zadana = value
        self.coordinator.r_target = value
        readback = self._num(ACOND_RETURN_READBACK)
        if readback is not None and round(readback, 1) == round(value, 1):
            return
        if self.coordinator.suspend_write:       # kámen mohl naskočit mezitím
            return
        await self._set(ACOND_TARGET, value)

    async def _set(self, entity_id: str, value: float) -> None:
        await self.hass.services.async_call(
            "number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True,
        )

    async def _save(self) -> None:
        await self._store.async_save({
            "held": self._held, "orig": self._orig_cap, "acond_vypnul": self.acond_vypnul,
            "posun": round(self.posun, 2), "posun_k": round(self.posun_k, 3),
            "posun_v": R_POSUN_VERZE,
        })

    # ------------------------------------------------------------------ #
    # Posun křivky (I složka) – pravidlo C (0.18.0b29)
    # ------------------------------------------------------------------ #
    async def _posun_tik(self, now: float, room: float, cil: float, ret: float,
                         lo: float) -> None:
        """Vzorek do okna; na přelomu celé hodiny vyhodnoť okno (učení nahoru)."""
        key = dt_util.now().replace(minute=0, second=0, microsecond=0)
        if self._p_key is None:
            self._p_key = key
        elif key != self._p_key:
            self._p_key = key
            await self._posun_vyhodnot(now, cil)
        # změna cíle (Den/noc, Okna ±, ruční) -> 3 h neučit
        if self._posun_cil is not None and abs(cil - self._posun_cil) >= 0.05:
            self._cil_zmena = now
        self._posun_cil = cil
        tuv = self._on(ACOND_BIT_TUV)
        bezi = self.run.kompresor_bezi is True
        # pauza R nebo stojící kompresor mimo TUV a prodlevu po ní = okno se nahoru neučí
        if self._paused or (not bezi and not tuv and now - self._tuv_konec >= R_PO_TUV_OKNO_S):
            self._p_stop = now
        if self._p_vzorky and now - self._p_vzorky[-1][0] < R_POSUN_VZOREK_S:
            return
        topi = bezi and not tuv and not self._paused
        cap = self._num(ACOND_CAPACITY)
        nad_min = (cap is not None and cap > lo + 1) if topi else None
        k_vz = None
        if topi and not self._on(ACOND_BIT_DEFROST):
            sup = self._num(ACOND_OUTLET)
            te = self._num(ACOND_OUTDOOR)
            if sup is not None and te is not None:
                k_vz = ((sup + ret) / 2 - room, room - te)
        fve = self._fve_sviti()
        self._p_vzorky.append((now, room, tuv, topi, nad_min, fve, k_vz))
        while self._p_vzorky and now - self._p_vzorky[0][0] > R_POSUN_OKNO_S:
            self._p_vzorky.popleft()

    def _posun_k(self, vzorky) -> None:
        """k z měření v okně (radiátory ~1,6, podlaha ~1,2)."""
        kv = [v[6] for v in vzorky if v[6] is not None]
        if not kv:
            return
        voda = sum(a for a, _b in kv) / len(kv)
        dt = sum(b for _a, b in kv) / len(kv)
        if dt >= R_POSUN_K_DT_MIN and voda > 0:
            k = 1 + voda / (R_POSUN_EXP * dt)
            self.posun_k = min(R_POSUN_K_MAX, max(R_POSUN_K_MIN, k))

    async def _posun_vyhodnot(self, now: float, cil: float) -> None:
        """Konec celé hodiny: NAHORU, když místnost celé 3 h bez pauzy nedosáhla cíle."""
        hod = dt_util.now().strftime("%H:00")
        cz = lambda v: f"{round(v, 2) + 0.0:+.2f}".replace(".", ",")  # noqa: E731
        okno = R_POSUN_OKNO_S
        s = self._p_vzorky
        if (now - self._p_mimo < okno or not s or s[0][0] > now - okno + 300
                or len(s) < 0.8 * okno / 60):
            self.posun_info = f"{hod}: R nevedla celé 3 h"
            return
        if now - self._cil_zmena < R_POSUN_CIL_ZMRAZ_S:
            self.posun_info = f"{hod}: změna cíle – neučím"
            return
        if now - self._p_krok < okno - 60:
            self.posun_info = f"{hod}: po kroku čekám na celé nové okno"
            return
        mist = [v[1] for v in s if not v[2]]          # bez minut TUV
        if not mist:
            return
        prumer = sum(mist) / len(mist)
        mx = max(mist)
        odch = cil - prumer
        if odch <= R_POSUN_PASMO + 1e-6:
            self.posun_info = f"{hod}: místnost Ø {cz(-odch)} – v pořádku"
            return
        if mx >= cil - 1e-6:
            self.posun_info = f"{hod}: Ø {cz(-odch)}, ale místnost cíle dosáhla – neučím"
            return
        if now - self._p_stop < okno:
            self.posun_info = f"{hod}: Ø {cz(-odch)}, v okně byla pauza – neučím"
            return
        self._posun_k(s)
        krok = R_POSUN_ZISK * self.posun_k * odch
        novy = max(-R_POSUN_MAX, min(R_POSUN_MAX, round(self.posun + krok, 2)))
        _LOGGER.debug(
            "R-Ekviterma posun %s nahoru: 3 h Ø %.2f, max %.1f, k %.2f -> posun %.2f -> %.2f",
            hod, prumer, mx, self.posun_k, self.posun, novy,
        )
        self.posun_info = (f"{hod}: 3 h pod cílem (Ø {cz(-odch)}) → posun {cz(novy - self.posun)}")
        self.posun = novy
        self._p_krok = now
        await self._save()

    async def _posun_pauza(self, now: float, cil: float) -> None:
        """Pauza pásma (hystereze): když R hodinu před ní jela převážně nad minimem,
        křivka dala víc, než dům chtěl -> DOLŮ o k × R_POSUN_DOLU."""
        cz = lambda v: f"{round(v, 2) + 0.0:+.2f}".replace(".", ",")  # noqa: E731
        cas = dt_util.now().strftime("%H:%M")
        hod = [v for v in self._p_vzorky if now - v[0] <= R_POSUN_PRED_PAUZOU_S]
        beh = [v[4] for v in hod if v[4] is not None]     # jen minuty, kdy kompresor topil dům
        # (kámen Topit na začátku běhu R na chvíli „umlčí" – proto ne podmínka celé hodiny R)
        if len(beh) < R_MIN_BEH_S / 60 or now - self._cil_zmena < R_POSUN_CIL_ZMRAZ_S:
            return
        podil = sum(1 for b in beh if b) / len(beh)
        if podil < R_POSUN_NAD_MIN:
            self.posun_info = f"{cas}: pauza po běhu na minimu – křivka nevadí"
            return
        if any(v[5] for v in hod):
            self.posun_info = f"{cas}: pauza po běhu nad minimem, ale svítí FVE – neučím"
            return
        self._posun_k(self._p_vzorky)
        novy = max(-R_POSUN_MAX, min(R_POSUN_MAX,
                                     round(self.posun - self.posun_k * R_POSUN_DOLU, 2)))
        _LOGGER.debug("R-Ekviterma posun %s dolů: pauza po %.0f %% běhu nad minimem -> %.2f -> %.2f",
                      cas, podil * 100, self.posun, novy)
        self.posun_info = (f"{cas}: pauza po běhu nad minimem ({podil:.0%}) → posun "
                           f"{cz(novy - self.posun)}").replace("%", " %")
        self.posun = novy
        await self._save()

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_r_updated(self.entry.entry_id))

    def _off(self, entity_id: str) -> bool:
        st = self.hass.states.get(entity_id)
        return st is not None and st.state == "off"

    def _on(self, entity_id: str) -> bool:
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
        try:
            return float(st.attributes.get(attr))
        except (ValueError, TypeError):
            return None
