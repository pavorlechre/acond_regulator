"""Režim „Topení podle přebytků" (osa A, executor 40014 = páka, návnada 40008).

ŽROUT PŘEBYTKŮ, ne termostat. Dům (betonový slab u Pavla) je tepelný akumulátor,
teplota v domě je vedlejší produkt. Cíl: sníst co nejvíc FV přebytku do topení a
přitom držet SoC baterie kolem hranice (default 95 %), aby do sítě neteklo skoro nic.

ŘÍDICÍ ZÁKON (ověřeno topeni_prebytky_simulace.py, 14/14 scénářů)
----------------------------------------------------------------
  Bilance:  B = časově vážený průměr (přetok + výkon baterie)   [W, + = přebytek]
  Zásah:    ΔP = B + k·(SoC − hranice)                          [W]
              - na hranici (SoC=95): ΔP = B  (naber přebytek, co tě vynesl na 95)
              - nad hranicí: přidej víc (přebytek + tah dolů), pod hranicí: uber
  Převod:   Δ40014 = ΔP / r,  r = aep / SKUTEČNÉ otáčky (30027 / 30024). Obě
              veličiny jsou měřené, poměr je poctivý kdykoli stroj reálně běží
              (otáčky >= TOPENI_MIN_RPM) – kotva ke skutečnosti vyřešila starou
              past 13min náběhu (ta platila pro éru r = aep/SETPOINT). Drobné
              odchylky (režie čerpadel ap.) si regulátor iterativně dorovná
              v pásmu ±10 %. r se veze s COP přes den.
  Návnada A (zpátečka): 40008 = ekviterma + návnada, ořezaná „Nejvyšší zpátečkou"
              (ochrana podlahy) a nikdy pod ekvitermní základ. Přišpendlí stroj na strop
              40014. NENÍ to páka – jen návnada. Do Store se NEUKLÁDÁ: při předání jen
              uvolníme blokaci a ekviterma si sama zapíše správnou hodnotu podle teplot.
              MĚKKÝ STROP: nad návnadou stroj sám brzdí (reguluje na setpoint), takže
              hodnota návnady JE hranice, přes kterou se skoro nedostane. Tvrdý stop na
              zpátečku proto neexistuje – uživatel jen nikdy nevědomky nepřekročí trubky.
  Návnada B (pokojovka): zvýšení POŽADOVANÉ teploty místnosti (40001) přes správce
              pokojovky (`pokojovka.py`, půjč-a-vrať). Účinek jde přes korekci do zpátečky
              a je SAMOOMEZUJÍCÍ: jak se dům dotápí, rozdíl (set − skutečná) klesá a tah
              slábne -> v cíli se vypne sám. Ořez: zvednutý setpoint nikdy nedosáhne na
              strop místnosti, aby si program sám neshodil master. Obě návnady jdou
              kombinovat i používat samostatně (kterákoli smí být 0).
  Strop stroje: 40014 smí být jen ±10 % kolem SKUTEČNÝCH otáček -> nahoru po špičkách,
              na stropu se cmd zasekne ~10 % nad skutečnem (signál, ne útěk na 5000).
  Plná baterka: SoC >= 98 % -> slider rovnou na MAX (baterka nemá kam brát, stroj ať
              žere co umí); pod prahem převezme normální modulace. Nahrazuje slepý tik. Nahoře
              = víc nejde (topí naplno), dole = na minimu (níž nejede). Symetricky.
  Mez slideru z flow: 40014 se ořezává na comp_capacity_min/max (min/max atribut
              number.acond_40014) – tvrdý uživatelský strop výkonu, ne konec epizody.

STAVOVÝ AUTOMAT
---------------
  WAITING (master on, čekáme): SoC < hranice nebo bez přebytku -> topí ekviterma, díváme se.
  PŘEVZETÍ (SoC >= hranice A přebytek): zablokuj ekvitermu (coordinator.topeni_owns),
     ulož 40014 do Store, nasaď návnadu (drží stroj na aktuálním capu), 40014 NECHÁME
     kde je, moduluj odsud (nikdy neshodíme běžící stroj dolů). První krok dle vzorce.
  ACTIVE: takt = změna SoC o 1 % NEBO pojistný tik (~3 min). Každý takt: spočti B, ΔP,
     Δ40014, aplikuj přes strop stroje. Návnadu 40008 drž (idempotentně).
  MĚKKÉ PŘEDÁNÍ (SoC < dolní_mez): vrať 40014 ze Store, uvolni ekvitermu, ale MASTER
     ZŮSTANE ON -> čeká na návrat (SoC >= hranice). Jediná měkká hrana.
  TVRDÝ KONEC (master OFF, bez návratu bez uživatele): strop MÍSTNOSTI (30002) /
     ztráta čidla SoC > timeout / uživatel vypnul. Vždy stejná úklidová cesta: vrať
     40014, uvolni ekvitermu, odhlas pokojovkovou návnadu. Strop místnosti navíc rozsvítí
     vlajku `strop_padl` -> dashboard odemkne kartu nastavení i s vyplým masterem (jinak
     by se okno s nastavením otevřelo a hned zavřelo, protože je podmíněné masterem).
     Zpátečka tvrdý stop NEMÁ (viz Návnada A) – nemá co vypínat.
  DŘÍMÁNÍ: TUV (bit_3) / odmraz (bit_8) / léto / suspend_write (běží kámen) -> nesahej
     na 40014 ani 40008, drž poslední. BEZ časového doběhu: doběh dával smysl jen
     kvůli pojistce na zpátečku (voda u čidla chladne dlouho), a ta je pryč.
     Bilance je elektrická veličina a po skončení TUV je čistá okamžitě. Po konci
     dřímání se invaliduje cache _last_40008 (kámen/firmware mohl registr přepsat
     a idempotence by jinak návnadu tiše nedržela).
  ČASOVÉ PROGRAMY MAJÍ PŘEDNOST: háček „smí se teď topit?" = okno Den/noc otevřené
     A Zebra není v pauze A neběží útlum PZ. Mimo něj žádné převzetí, žádný výstřel
     kamene; aktivní epizoda -> měkké předání BEZ nasazení hlídače. Po otevření
     okna / konci pauzy se převezme normálně znovu.

HLÍDAČ RESTARTU PO PŘEDÁNÍ (Pavleho postup ze železa)
-----------------------------------------------------
Po velkých přetocích je zpátečka i 6 °C nad tím, co zapíše ekviterma. Stroj má kolem
setpointu pásmo ±2 °C: okamžitě vypne a sám by naskočil až 2 °C POD ekvitermou – u slabu
hodiny bez topení. Když kompresor stojí, voda u čidla stojí taky a ukazuje nesmysl;
stroj proto ~každých 15 min cvakne primární čerpadlo a okruh prožene. Konec toho
proplachu je JEDINÝ okamžik, kdy je zpátečka pravdivá -> je to spouštěč (událost, ne
časovač). Při měkkém předání se hlídač nasadí a při zpátečce nejvýš TOPENI_RESTART_OVER
nad ekvitermou pustí kámen Topit. Jednorázový; sundá se i sám, jakmile se kompresor
rozjede na topení. Past: kámen si bere „původní zpátečku" z registru, takže se nesmí
spustit dřív, než tam ekviterma po předání zapíše svoje číslo (jinak by si jako základ
vzal návnadu) – jistí porovnání readbacku 30008 s ekvitermním cílem.

ZDROJE: přetok (net_grid) a SoC baterie SDÍLÍ s FVE (fve.pretok_value/battery_value –
  už nakonfigurováno, nezadává se dvakrát). NOVÝ = znaménkový VÝKON baterie (nabíjení +)
  přes text.mar_topeni_zdroj_baterie_vykon. aep = nativní 30027 napevno (auto W/kW).

GATE: běží jen ve strategii Ekviterma + Typ regulace Standard (mimo -> vrať a zhasni).
  Zadrátováno do OR-brány binary_sensor.mar_fve_aktivni (přes .active) – jinak ho
  Dovolená guard mine.
"""

from __future__ import annotations

import asyncio
import logging
import math
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
    ACOND_BIT_DEFROST,
    ACOND_BIT_TUV,
    ACOND_CAPACITY,
    ACOND_INDOOR,
    ACOND_POWER,
    ACOND_PUMP_BIT,
    ACOND_ROOM_SET,
    ACOND_RETURN_ACT,
    ACOND_RETURN_READBACK,
    ACOND_RPM,
    ACOND_SUMMER,
    ACOND_TARGET,
    DOMAIN,
    MAX_RETURN,
    MIN_RETURN,
    POKOJOVKA_CLAIM_TOPENI,
    STRATEGY_EKVITERM,
    TOPENI_BAND,
    TOPENI_FULL_SOC,
    TOPENI_MIN_RPM,
    TOPENI_BLOK_DOWN_S,
    TOPENI_DEADBAND_RPM,
    TOPENI_PRURAZ_W,
    TOPENI_R_FALLBACK,
    TOPENI_RESTART_OVER,
    TOPENI_RESTART_TOL,
    TOPENI_RETRY_S,
    TOPENI_ROOM_MARGIN,
    TOPENI_STONE_COOLDOWN_S,
    TOPENI_STONE_POLL_S,
    TOPENI_STONE_WAIT_S,
    TOPENI_STEP_SAFETY_S,
    TOPENI_STORE_VERSION,
    TOPENI_TICK_S,
    signal_topeni_updated,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN = ("unknown", "unavailable", "", None)


class TopeniController:
    """Topení podle přebytků – SoC-řízený alokátor na 40014 s návnadou 40008."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, fve,
                 pokojovka=None) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.fve = fve                 # sdílené zdroje: přetok + SoC baterie
        self.pokojovka = pokojovka     # správce 40001 (návnada B); my jsme zákazník
        self.seq = None                # SequenceRunner – doplní __init__ (kámen Topit)
        self.dennoc = None             # háček „smí se teď topit?" (Den/noc má přednost)
        self.zebra = None              # …a pauza Zebry taky
        self._store: Store = Store(
            hass, TOPENI_STORE_VERSION, f"{DOMAIN}_topeni_{entry.entry_id}"
        )

        # --- konfigurace (z entit) ---
        self.enabled = False           # master switch.mar_topeni
        self.hranice = 95.0            # cílový SoC
        self.dolni = 90.0              # měkké předání
        self.tah = 100.0               # k [W na % odchylky]
        self.okno_s = 180.0            # okno časově váženého průměru (s) = number×60
        self.navnada = 10.0            # návnada A: +°C na 40008 nad ekvitermu
        self.navnada_room = 0.0        # návnada B: +°C na 40001 (přes správce pokojovky)
        self.max_zp = 40.0             # MĚKKÝ strop návnady A – ochrana podlahy (ne pojistka)
        self.strop_mist = 24.0         # tvrdý strop místnosti (30002) – jediný tvrdý stop
        self.cidlo_timeout_s = 300.0   # ztráta čidla SoC -> tvrdý konec
        self._src_batt_vykon = ""      # entity_id znaménkového výkonu baterie
        #   BEZBATERKOVÁ VĚTEV (Jan) – stejný slovník jako FVE TUV:
        self.stop = 300.0              # „vytrvalost" W: cíl modulace I hranice odchodu
        self.start = 500.0             # „hojnost" W: převzetí BĚŽÍCÍHO stroje
        self.start_cold = 2000.0       # W: rozjezd STOJÍCÍHO stroje (koupíš celé minimum)
        self.prodleva_min = 10.0       # min na minimu pod STOP -> předání
        self.blok_min = 5.0            # min: blok zápisů 40014 SMĚREM NAHORU
        self._batt_dispos = True       # True = zdroj hlásí VYBÍJENÍ jako kladné (GoodWe);
                                       # normalizujeme interně na „nabíjení +"

        # --- běhový stav ---
        self._active = False
        self._store_40014: float | None = None   # uživatelův slider (půjč-a-vrať)
        self._buf: list[tuple[float, float]] = []  # (ts, B) pro časově vážený průměr
        self._r_last = TOPENI_R_FALLBACK          # poslední platný poměr aep/40014
        self._last_act_soc: int | None = None
        self._last_act_ts = 0.0
        self._last_40008: float | None = None
        self._sensor_lost_since: float | None = None
        self._started = False
        self.strop_padl: str | None = None   # None / "mistnost" / "cidlo" – vlajka pro dashboard
        self._wd_armed = False               # hlídač restartu po předání
        self.hlidac_enabled = True           # switch.mar_topeni_hlidac (default zapnuto)
        self._was_frozen = False             # sledování konce dřímání -> invalidace _last_40008
        self._deficit_since: float | None = None   # bez baterky: od kdy jsme na minimu pod STOP
        self._last_handback_ts = 0.0         # brzda proti drahým studeným rozjezdům po sobě
        self._last_stone: float | None = None      # kdy naposled letěl kámen Topit
        self._last_write_ts = 0.0            # ochrana zápisů 40014 (jen bezbaterková větev)

        # --- odběry ---
        self._unsub_batt_vykon = None
        self._unsub_tick = None
        self._unsub_retry = None
        self._unsub_pump = None

    # ------------------------------------------------------------------ #
    # Nastavení z entit
    # ------------------------------------------------------------------ #
    def set_hranice(self, v: float) -> None:
        self.hranice = float(v)

    def set_dolni(self, v: float) -> None:
        self.dolni = float(v)

    def set_tah(self, v: float) -> None:
        self.tah = float(v)

    def set_okno(self, v_min: float) -> None:
        self.okno_s = max(1.0, float(v_min)) * 60.0

    def set_navnada(self, v: float) -> None:
        self.navnada = float(v)

    def set_max_zpatecka(self, v: float) -> None:
        """Nejvyšší zpátečka – podlaha. MĚKKÝ strop: ořezává návnadu A, nevypíná."""
        self.max_zp = float(v)

    def set_navnada_room(self, v: float) -> None:
        self.navnada_room = max(0.0, float(v))

    # --- bezbaterková větev ---
    def set_stop(self, v: float) -> None:
        self.stop = float(v)

    def set_start(self, v: float) -> None:
        self.start = float(v)

    def set_start_cold(self, v: float) -> None:
        self.start_cold = float(v)

    def set_prodleva(self, v: float) -> None:
        self.prodleva_min = max(1.0, float(v))

    def set_blok(self, v: float) -> None:
        self.blok_min = max(0.0, float(v))

    # --- prahy s měkkými pojistkami (studený >= start >= stop) ---
    @property
    def ma_baterku(self) -> bool:
        """Větev podle KONFIGURACE zdroje, ne podle toho, jestli čidlo odpovídá –
        výpadek měniče nesmí uprostřed mraku přepnout regulaci do jiného režimu."""
        return bool(self._src_batt_vykon)

    @property
    def start_thr(self) -> float:
        return max(self.start, self.stop)

    @property
    def start_cold_thr(self) -> float:
        return max(self.start_cold, self.start_thr)

    def set_strop_mist(self, v: float) -> None:
        self.strop_mist = float(v)

    def set_cidlo_timeout(self, v_min: float) -> None:
        self.cidlo_timeout_s = max(1.0, float(v_min)) * 60.0

    def set_batt_dispos(self, value: bool) -> None:
        """Konvence znaménka zdroje výkonu baterie. True = zdroj má vybíjení kladné
        (GoodWe) -> interně otočíme na „nabíjení +". Změna zneplatní staré vzorky."""
        value = bool(value)
        if value == self._batt_dispos:
            return
        self._batt_dispos = value
        self._buf.clear()

    def set_source_batt_vykon(self, entity_id: str | None) -> None:
        eid = (entity_id or "").strip()
        if eid == self._src_batt_vykon:
            return
        self._src_batt_vykon = eid
        self._buf.clear()          # zdroj se změnil -> staré vzorky bilance neplatí
        self._sub_batt_vykon()

    # ------------------------------------------------------------------ #
    # Veřejné vlastnosti pro entity / guard
    # ------------------------------------------------------------------ #
    @property
    def active(self) -> bool:
        """Pro OR-bránu binary_sensor.mar_fve_aktivni a guard Dovolené = master zapnutý
        (ne nutně právě topí). „Vypni FV programy" = vypni master."""
        return self.enabled

    @property
    def heating(self) -> bool:
        """Právě drží řízení (převzato od ekvitermy) – pro banner / mar_stav."""
        return self._active

    def bilance(self) -> float | None:
        """Časově vážený průměr B = přetok + výkon baterie (W). None bez vzorků."""
        self._prune()
        if not self._buf:
            return None
        now = dt_util.utcnow().timestamp()
        if len(self._buf) == 1:
            return round(self._buf[0][1], 0)
        area = 0.0
        span = 0.0
        for i in range(len(self._buf) - 1):
            (t0, v0), (t1, _) = self._buf[i], self._buf[i + 1]
            area += v0 * (t1 - t0)
            span += (t1 - t0)
        area += self._buf[-1][1] * (now - self._buf[-1][0])   # poslední úsek do teď
        span += (now - self._buf[-1][0])
        return round(area / span, 0) if span > 0 else round(self._buf[-1][1], 0)

    def bilance_syrova(self) -> float | None:
        """Okamžitá bilance přetok + výkon baterie (do grafu/diagnostiky)."""
        pretok = self.fve.pretok_value() if self.fve else None
        bp = self.batt_vykon_value()   # normalizováno na „nabíjení +"
        # výkon baterie je TVRDÁ podmínka: bez něj je bilance maskovaná (viz destilát)
        if pretok is None or bp is None:
            return None
        return round(pretok + bp, 0)

    def soc_value(self) -> float | None:
        return self.fve.battery_value() if self.fve else None

    def batt_vykon_value(self) -> float | None:
        """Výkon baterie normalizovaný na „NABÍJENÍ kladné" (interní konvence).
        Zdroj s vybíjením-kladným (GoodWe) se otočí přepínačem."""
        raw = self._read_source_float(self._src_batt_vykon)
        if raw is None:
            return None
        return -raw if self._batt_dispos else raw

    def target_40014(self) -> float | None:
        """Co držíme na slideru 40014 (pro okno). None dokud neaktivní."""
        return self._num(ACOND_CAPACITY) if self._active else None

    def as_attr(self) -> dict:
        return {
            "nazev": "Topení podle přebytků",
            "aktivni": self.enabled,
            "topi": self._active,
            "soc": self.soc_value(),
            "hranice": self.hranice,
            "dolni_mez": self.dolni,
            "bilance": self.bilance(),
            "bilance_syrova": self.bilance_syrova(),
            "vykon_baterie": self.batt_vykon_value(),
            "ma_zdroj_vykonu": bool(self._src_batt_vykon),
            "baterie_vybijeni_kladne": self._batt_dispos,
            "tah_k": self.tah,
            "okno_min": round(self.okno_s / 60.0, 1),
            "navnada": self.navnada,
            "navnada_pokojovka": self.navnada_room,
            "slider_40014": self.target_40014(),
            "slider_base": self._store_40014,
            "pomer_r": round(self._r_last, 1),
            "max_zpatecka": self.max_zp,
            "strop_mistnost": self.strop_mist,
            "strop_padl": self.strop_padl,
            "hlidac_restartu": self._wd_armed,
            "hlidac_zapnut": self.hlidac_enabled,
            "ma_baterku": self.ma_baterku,
            "stop": self.stop,
            "start": self.start_thr,
            "start_studeny": self.start_cold_thr,
            "prodleva_min": self.prodleva_min,
            "blok_min": self.blok_min,
            "odchazi": self._deficit_since is not None,
        }

    # ------------------------------------------------------------------ #
    # Master + config
    # ------------------------------------------------------------------ #
    async def async_set_enabled(self, value: bool) -> None:
        value = bool(value)
        if self.enabled and not value:
            # uživatel vypnul master -> tvrdý úklid (vrať slider, uvolni ekvitermu)
            await self._cleanup()
        if value and not self.enabled:
            # vědomé zapnutí uživatelem = potvrzení, že strop viděl -> zhasni vlajku
            self.strop_padl = None
        self.enabled = value
        if self._started:
            await self.async_evaluate()
        self._notify()

    async def async_apply_config(self) -> None:
        if self._started:
            await self.async_evaluate()
        self._notify()

    def can_enable(self) -> tuple[bool, str]:
        """Smí se topení podle přebytků teď zapnout? Jen ve strategii Ekviterma
        + Typ regulace Standard (sahá na zpátečku). -> (ok, důvod pro hlášku)."""
        if self.coordinator.strategy != STRATEGY_EKVITERM:
            return (False, "Nejdřív přepni strategii na Ekviterma.")
        if not self.coordinator.is_standard():
            return (False, "Acond musí být v Typu regulace Standard.")
        return (True, "")

    async def async_yield_control(self) -> None:
        """Vynucené měkké předání ekvitermě (opouští se Ekviterma / kolize os A).
        Vrátí 40014, uvolní blokaci; MASTER ZŮSTANE zapnutý (dřímá, dokud se
        nevrátíme do Ekvitermy). Volá select při odchodu ze strategie."""
        self._disarm_watchdog()
        if self._active:
            await self._cleanup()
        self._notify()

    # ------------------------------------------------------------------ #
    # Start / obnova / vypnutí
    # ------------------------------------------------------------------ #
    async def async_restore_borrow(self) -> None:
        """Načti případnou nevrácenou půjčku 40014 ze Store (bez nasazení odběrů).
        Volá ji resume i čistý stůl při startu mimo Standard – ten běží DŘÍV než
        resume a bez obnovy by neměl co vracet (Store by se smazal, slider ztratil).
        U běžícího systému no-op."""
        if self._started or self._active:
            return
        data = await self._store.async_load()
        if data and data.get("active"):
            self._active = True
            self._store_40014 = data.get("store_40014")
            self.coordinator.topeni_owns = True    # zase zablokuj ekvitermní zápis
            _LOGGER.debug("TOPENI: obnovena půjčka store_40014=%s", self._store_40014)

    async def async_resume_if_needed(self) -> None:
        """Po startu HA: obnov nevrácenou půjčku 40014 ze Store, nasaď odběry + tik."""
        await self.async_restore_borrow()

        self._sub_batt_vykon()
        self._unsub_tick = async_track_time_interval(
            self.hass, self._on_tick, timedelta(seconds=TOPENI_TICK_S)
        )
        self._started = True
        await self.async_evaluate()
        self._notify()

    def shutdown(self) -> None:
        for unsub in (self._unsub_batt_vykon, self._unsub_tick, self._unsub_retry,
                      self._unsub_pump):
            if unsub is not None:
                unsub()
        self._unsub_batt_vykon = self._unsub_tick = self._unsub_retry = None
        self._unsub_pump = None
        self._wd_armed = False

    # ------------------------------------------------------------------ #
    # Stavový automat
    # ------------------------------------------------------------------ #
    def _frozen(self) -> bool:
        """Dřímání: kámen / TUV / odmraz -> na registry se nesahá."""
        return (
            self.coordinator.suspend_write
            or self._is_on(ACOND_BIT_TUV)
            or self._is_on(ACOND_BIT_DEFROST)
        )

    def _smi_topit(self) -> bool:
        """ČASOVÉ PROGRAMY MAJÍ PŘEDNOST: okno Den/noc otevřené, Zebra není
        v pauze, neběží útlum PZ. Mimo to žádné převzetí ani výstřel kamene."""
        if self.coordinator.utlum_pz is not None:
            return False
        if self.dennoc is not None and not self.dennoc.may_heat_now():
            return False
        if self.zebra is not None and self.zebra.pausing:
            return False
        return True

    async def async_evaluate(self) -> None:
        # INVALIDACE CACHE po konci dřímání: kámen nebo firmware (TUV) mohl 40008
        # přepsat; _last_40008 by pak idempotenci „splnil" a návnada by se tiše
        # nedržela (stroj by dojel holou ekvitermu a zhasl). Konec dřímání ->
        # návnadu ověř znovu proti readbacku.
        frozen = self._frozen()
        if self._was_frozen and not frozen:
            self._last_40008 = None
        self._was_frozen = frozen

        # GATE: master off / mimo Standard / mimo Ekvitermu / léto -> vrať a zhasni.
        # (Topení sahá na zpátečku -> dává smysl jen v ekvitermě + Standardu, ne v létě.)
        if (
            not self.enabled
            or not self.coordinator.is_standard()
            or self.coordinator.strategy != STRATEGY_EKVITERM
            or self._is_on(ACOND_SUMMER)
        ):
            self._disarm_watchdog()      # mimo naše podmínky nestartuj
            await self._handback(soft=True, arm=False)
            return

        # ČASOVÝ GATE (Den/noc zavřeno / pauza Zebry / útlum PZ): měkké předání
        # BEZ hlídače — restart obstará kámen Den/noc (ráno) nebo Zebra (konec
        # pauzy). Master zůstává; po otevření se převezme normálně znovu.
        if not self._smi_topit():
            self._disarm_watchdog()
            await self._handback(soft=True, arm=False)
            return

        # hlídač restartu se sundá sám, jakmile stroj zase topí (TUV/odmraz se nepočítá –
        # tam kompresor běží na jiný děj a topení z toho nemá nic)
        now = dt_util.utcnow().timestamp()

        if self._wd_armed:
            rpm = self._num(ACOND_RPM)
            if (
                rpm is not None and rpm >= TOPENI_MIN_RPM
                and not self._is_on(ACOND_BIT_TUV)
                and not self._is_on(ACOND_BIT_DEFROST)
            ):
                self._disarm_watchdog()

        # VĚTEV: s baterkou (SoC je palivoměr) / bez baterky (bilance je palivo).
        if not self.ma_baterku:
            await self._evaluate_nobatt(now)
            return

        soc = self.soc_value()
        bp = self.batt_vykon_value()   # výkon baterie = TVRDÁ podmínka (odmaskovaná brzda)

        # ztráta klíčového signálu (SoC NEBO výkon baterie): drž, po timeoutu TVRDÝ konec.
        # Bez výkonu baterie je bilance maskovaná -> nesmíme topit ani převzít
        # (topili bychom naslepo).
        if soc is None or bp is None:
            if self._sensor_lost_since is None:
                self._sensor_lost_since = now
            if self._active and (now - self._sensor_lost_since) >= self.cidlo_timeout_s:
                await self._hard_off("ztráta čidla (SoC / výkon baterie)", flag="cidlo")
            return
        self._sensor_lost_since = None

        # ---- WAITING -> převzetí ----
        if not self._active:
            # nepřebírat naslepo během TUV/odmrazu/kamene (firmware si 40008 zrovna
            # přepisuje sám) – přebytek nikam neuteče, počká se na další tik
            if frozen:
                return
            # přetopená místnost -> nepřebírat (žádný tvrdý stop, jen se čeká,
            # až zchladne; master zůstává)
            t_room = self._num(ACOND_INDOOR)
            if t_room is not None and t_room >= self.strop_mist:
                return
            b = self.bilance()
            if soc >= self.hranice and b is not None and b > 0:
                await self._takeover()
            return

        # ---- ACTIVE ----
        # BEZPEČNOST každý cyklus (ne až na 1 % SoC): JEDINÝ tvrdý strop = místnost.
        # Zpátečku hlídá měkký strop uvnitř návnady (viz _write_navnada) – nevypíná.
        # Čidlo místnosti (30002) TUV ani odmraz nešpiní, takže se čte i za dřímání.
        t_room = self._num(ACOND_INDOOR)
        if t_room is not None and t_room >= self.strop_mist:
            await self._hard_off("strop místnosti")
            return

        # měkké předání pod dolní mez -> zpět do WAITING (master zůstává).
        # TADY nasadit hlídač restartu: zpátečka je po přebytcích vysoko a stroj by
        # jinak čekal, až spadne 2 °C pod ekvitermu (u slabu hodiny bez topení).
        if soc < self.dolni:
            await self._handback(soft=True, arm=True)
            return

        # ZMRAZENÍ: běží kámen / TUV / odmraz -> nesahej, nečti poměr, drž poslední
        if (
            self.coordinator.suspend_write
            or self._is_on(ACOND_BIT_TUV)
            or self._is_on(ACOND_BIT_DEFROST)
        ):
            return

        # návnada A na 40008 (drží stroj na stropu 40014) – idempotentně
        await self._write_navnada()
        # návnada B na 40001 přes správce pokojovky – idempotentně
        await self._claim_room()

        # BATERKA PLNÁ: SoC >= práh -> nech jemnou modulaci a dej slider rovnou na MAX.
        # Baterka nemá kam brát, tak ať stroj žere, co umí (stroj si vlastní strop pohlídá
        # sám otáčkami). Řeší mrtvé SoC-hodiny u plné baterky JEDNÍM zápisem místo slepého
        # tiku. Jakmile SoC klesne pod práh (stroj vytvoří deficit), převezme normální
        # modulace. Idempotentní – když je slider už na max, nezapisuje.
        if soc >= TOPENI_FULL_SOC:
            cap_max = self._attr(ACOND_CAPACITY, "max")
            cur = self._num(ACOND_CAPACITY)
            if cap_max is not None and (cur is None or round(cur, 1) != round(cap_max, 1)):
                await self._set_number(ACOND_CAPACITY, cap_max)
            self._last_act_soc = math.floor(soc)
            return

        # TAKT: krok jen při změně celého % SoC (žádný slepý časovač – plnou baterku
        # řeší práh výše, a mezi hranicí a plnem se SoC hýbe sám, takže hodiny tikají).
        soc_int = math.floor(soc)
        if soc_int == self._last_act_soc:
            return
        self._last_act_soc = soc_int
        self._last_act_ts = now

        await self._step_40014(soc)

    # ------------------------------------------------------------------ #
    # BEZBATERKOVÁ VĚTEV (Jan)
    # ------------------------------------------------------------------ #
    async def _evaluate_nobatt(self, now: float) -> None:
        """Bez baterky odpadá palivoměr i nárazník, ale taky maskování: bilance sítě
        je poctivý signál sama o sobě. Místo „bilance + tah k hranici SoC" se reguluje
        na **bilance − STOP**, tedy ustálený stav = do sítě trvale teče zhruba STOP.
        („Radši ať trochu uteče, než abych dokupoval.")"""
        b = self.bilance()
        if b is None:
            if self._sensor_lost_since is None:
                self._sensor_lost_since = now
            if self._active and (now - self._sensor_lost_since) >= self.cidlo_timeout_s:
                await self._hard_off("ztráta čidla (bilance sítě)", flag="cidlo")
            return
        self._sensor_lost_since = None

        rpm = self._num(ACOND_RPM)
        bezi = rpm is not None and rpm >= TOPENI_MIN_RPM

        # ---- WAITING -> převzetí ----
        if not self._active:
            # nepřebírat naslepo během TUV/odmrazu/kamene (viz baterková větev)
            if self._frozen():
                return
            # přetopená místnost -> nepřebírat, jen čekat (bez tvrdého stopu)
            t_room_w = self._num(ACOND_INDOOR)
            if t_room_w is not None and t_room_w >= self.strop_mist:
                return
            # DVA PRAHY: běžícího stroje se svezeme za pár set W navíc; stojící stroj
            # znamená koupit rovnou celé jeho minimum -> proto výš. Mezi prahy se čeká
            # a jakmile stroj rozjede ekviterma sama, topení ho hned převezme zadarmo.
            if bezi:
                if b > self.start_thr:
                    await self._takeover()
                return
            # brzda proti drahým studeným rozjezdům za sebou (mraky po předání)
            if (now - self._last_handback_ts) < self.prodleva_min * 60.0:
                return
            if b > self.start_cold_thr:
                await self._takeover()
            return

        # ---- ACTIVE ----
        t_room = self._num(ACOND_INDOOR)
        if t_room is not None and t_room >= self.strop_mist:
            await self._hard_off("strop místnosti")
            return

        # DŘÍMÁNÍ: kámen / TUV / odmraz. Odpočet odchodu se přitom NESMÍ počítat –
        # bojler sežere přebytek naráz (bez baterky chybí tlumič) a topení by z toho
        # vypadlo, ačkoli se nic nezkazilo. Jediná vazba mezi programy, co je potřeba.
        if (
            self.coordinator.suspend_write
            or self._is_on(ACOND_BIT_TUV)
            or self._is_on(ACOND_BIT_DEFROST)
        ):
            self._deficit_since = None
            return

        await self._write_navnada()
        await self._claim_room()

        # ODCHOD: ubírání není vypínání. Dokud je kam ubírat, ubíráme; teprve když je
        # stroj na svém minimu a PŘESTO se je pod STOP, znamená to, že minimum stroje
        # je větší než dostupný přebytek -> po prodlevě předej ekvitermě.
        if b < self.stop and self._at_minimum():
            if self._deficit_since is None:
                self._deficit_since = now
            elif (now - self._deficit_since) >= self.prodleva_min * 60.0:
                await self._handback(soft=True, arm=True)
                return
        else:
            self._deficit_since = None

        # TAKT: časový – bez baterky nemá co tikat na % SoC
        if (now - self._last_act_ts) < TOPENI_STEP_SAFETY_S:
            return
        self._last_act_ts = now
        # STOP dělá dvojí práci: je to i CÍL, na který se moduluje
        await self._step_common(b - self.stop, "bez baterky", nobatt=True)

    def _at_minimum(self) -> bool:
        """Stroj je na svém dně = povolujeme mu jen jeho minimum."""
        cmd = self._num(ACOND_CAPACITY)
        cap_min = self._attr(ACOND_CAPACITY, "min")
        if cmd is None or cap_min is None:
            return False
        return cmd <= cap_min + TOPENI_DEADBAND_RPM

    # ------------------------------------------------------------------ #
    # Krok páky 40014
    # ------------------------------------------------------------------ #
    async def _step_40014(self, soc: float) -> None:
        # KOTVA KE SKUTEČNOSTI (oprava z železa): počítáme ze SKUTEČNÝCH otáček 30024,
        # ne ze setpointu 40014. Setpoint je jen "co si přeju" – stroj má vlastní strop
        # (slider 5000, ale TČ jelo 3000). Poměr i krok proto ze skutečna:
        #   r = aep / n_skut               [W na otáčku] – vždy poctivé (obojí měřené)
        #   n_cíl = n_skut + ΔP/r          kotva k realitě -> žádné plazení nad stroj
        n_skut = self._num(ACOND_RPM)                 # 30024 comp_rpm_actual
        aep = self._read_aep()                        # 30027
        cmd = self._num(ACOND_CAPACITY)               # 40014 (jen pro zápis-při-změně)
        if n_skut is None or aep is None:
            return
        if n_skut < TOPENI_MIN_RPM:
            return        # stroj stojí (čeká/mrzne) -> nekrokuj, drž poslední poměr

        b = self.bilance()
        if b is None:
            return
        await self._step_common(b + self.tah * (soc - self.hranice), "SoC=%.1f" % soc)

    async def _step_common(self, dP: float, ctx: str, nobatt: bool = False) -> None:
        """Společný executor obou větví. Liší se jen tím, co je dP („kolik wattů
        navíc smím sežrat"); kotva, pásmo i meze slideru jsou stejné."""
        n_skut = self._num(ACOND_RPM)
        aep = self._read_aep()
        cmd = self._num(ACOND_CAPACITY)
        if n_skut is None or aep is None:
            return
        if n_skut < TOPENI_MIN_RPM:
            return        # stroj stojí -> nekrokuj, drž poslední poměr

        r = aep / n_skut
        if r > 0:
            self._r_last = r
        r = self._r_last

        delta = dP / r                                # otáčky
        new_cmd = n_skut + delta                      # KOTVA ke skutečnosti

        # STROP STROJE = ±BAND kolem SKUTEČNÝCH otáček (stejné jednotky, přímé porovnání).
        # Nahoru "po špičkách" (max +BAND nad skutečné -> stroj dojede, pak zas +BAND);
        # když stroj nereaguje (na stropu), cmd se zasekne ~BAND nad skutečnem = signál
        # stropu, ne útěk na 5000. Dolů symetricky (na minimu níž nejede).
        new_cmd = min(new_cmd, n_skut * (1 + TOPENI_BAND))
        new_cmd = max(new_cmd, n_skut * (1 - TOPENI_BAND))

        # mez slideru z flow (comp_capacity_min/max = min/max atribut 40014)
        cap_min = self._attr(ACOND_CAPACITY, "min")
        cap_max = self._attr(ACOND_CAPACITY, "max")
        if cap_min is not None:
            new_cmd = max(new_cmd, cap_min)
        if cap_max is not None:
            new_cmd = min(new_cmd, cap_max)
        new_cmd = round(new_cmd, 1)
        if cmd is not None and new_cmd == round(cmd, 1):
            return

        # OCHRANA ZÁPISŮ (EEPROM v desce) – JEN bezbaterková větev: ta tiká časem,
        # takže by bez ní psala řádově tisíce zápisů denně. Bateriová větev krokuje
        # na změnu celého % SoC (desítky za den) a zůstává nedotčená.
        if nobatt and cmd is not None:
            zmena = new_cmd - cmd
            if abs(zmena) < TOPENI_DEADBAND_RPM:
                return                       # pásmo necitlivosti – dorovná si to stroj sám
            pruraz = abs(dP) >= TOPENI_PRURAZ_W   # hluboký schodek smí projít i v bloku
            if not pruraz:
                blok = self.blok_min * 60.0 if zmena > 0 else TOPENI_BLOK_DOWN_S
                if (dt_util.utcnow().timestamp() - self._last_write_ts) < blok:
                    return
            self._last_write_ts = dt_util.utcnow().timestamp()

        await self._set_number(ACOND_CAPACITY, new_cmd)
        _LOGGER.debug(
            "TOPENI[%s]: dP=%.0f r=%.2f n_skut=%.0f 40014->%.0f",
            ctx, dP, r, n_skut, new_cmd,
        )

    # ------------------------------------------------------------------ #
    # Převzetí / předání / tvrdý konec
    # ------------------------------------------------------------------ #
    async def _takeover(self) -> None:
        cmd = self._num(ACOND_CAPACITY)
        if cmd is None:
            self._schedule_retry()      # slider zatím nedostupný -> zkus později
            return
        self._store_40014 = cmd          # půjč-a-vrať: uživatelův slider
        self.coordinator.topeni_owns = True   # coordinator uhne (nezapisuje ekvitermu)
        self._active = True
        self._last_act_soc = None        # ať první krok proběhne hned
        self._last_act_ts = 0.0
        await self._save()
        # 40014 NESAHÁME: necháme stroj tam, kde právě je. Návnada 40008=ekviterma+10
        # ho na aktuálním capu přišpendlí a modulujeme ODSUD. Stáhnout na dolní mez by
        # shodilo běžící stroj (např. 3000 -> min) přesně ve chvíli, kdy SoC nad hranicí
        # a je přebytek -> chceme topit VÍC, ne míň. Stroj byl už pod capem (ekviterma
        # jede taky pod 40014), takže návnada ho nikdy neshodí dolů, jen podrží/zvedne.
        await self._write_navnada()
        await self._claim_room()
        await self._maybe_stone_start()
        self._disarm_watchdog()      # zase topíme -> hlídač z minulého předání je zbytečný
        await self.coordinator.async_request_refresh()
        _LOGGER.debug("TOPENI: PŘEVZETÍ, uloženo 40014=%s", self._store_40014)
        # první krok hned
        soc = self.soc_value()
        if soc is not None:
            self._last_act_soc = math.floor(soc)
            self._last_act_ts = dt_util.utcnow().timestamp()
            await self._step_40014(soc)

    async def _handback(self, soft: bool, arm: bool = False) -> None:
        """Měkké předání ekvitermě: vrať 40014, uvolni blokaci. MASTER ZŮSTANE.
        `arm=True` nasadí hlídač restartu (jen když jsme opravdu topili)."""
        if self._active:
            await self._cleanup()
            self._last_handback_ts = dt_util.utcnow().timestamp()
            self._deficit_since = None
            _LOGGER.debug("TOPENI: měkké předání ekvitermě")
            if arm:
                self._arm_watchdog()

    async def _hard_off(self, why: str, flag: str = "mistnost") -> None:
        """Tvrdý konec: úklid + vypni master. Bez zásahu uživatele se nevrátí.
        Rozsvítí vlajku pro dashboard (SKUTEČNÝM důvodem: "mistnost" / "cidlo"),
        ať jde nastavení otevřít i s vyplým masterem a hláška nelže."""
        await self._cleanup()
        self.enabled = False
        self.strop_padl = flag
        self._disarm_watchdog()     # tvrdý konec -> stroj startovat nechceme
        _LOGGER.warning("TOPENI: TVRDÝ KONEC (%s) -> ekviterma, master OFF", why)
        self._notify()

    async def _cleanup(self) -> None:
        """Společná úklidová cesta: vrať slider 40014, uvolni ekvitermu (40008 se
        NEVRACÍ – jen zvedneme blokaci a ekviterma si zapíše správnou hodnotu)."""
        if self._store_40014 is not None:
            await self._set_number(ACOND_CAPACITY, self._store_40014)
        # návnada B: odhlas přání u správce (originál 40001 vrátí on, až se odhlásí
        # i Okna – proto se souběh nemůže navzájem přepsat)
        if self.pokojovka is not None:
            await self.pokojovka.async_release(POKOJOVKA_CLAIM_TOPENI)
        self.coordinator.topeni_owns = False
        self.coordinator.topeni_target = None
        self._active = False
        self._store_40014 = None
        self._last_40008 = None
        self._last_act_soc = None
        await self._clear_store()
        await self.coordinator.async_request_refresh()   # ekviterma hned přepočítá 40008

    # ------------------------------------------------------------------ #
    # Návnada B: pokojovka přes správce 40001
    # ------------------------------------------------------------------ #
    async def _claim_room(self) -> None:
        """Přihlas u správce přání „o kolik tepleji". Ořez tak, aby zvednutý setpoint
        nikdy nedosáhl na strop místnosti – jinak by si program vlastní návnadou shodil
        master tvrdým stopem. Takhle místo toho dohasne sám (korekce s dotápěním klesá).
        Návnada 0 (nebo ořez na nulu) = přání se odhlásí, jede jen návnada A."""
        if self.pokojovka is None:
            return
        want = float(self.navnada_room)
        if want > 0:
            # originál drží správce; než ho má, vezmi aktuální 40001 jako odhad
            base = self.pokojovka.base
            if base is None:
                base = self._num(ACOND_ROOM_SET)
            if base is not None:
                want = min(want, self.strop_mist - TOPENI_ROOM_MARGIN - base)
        if want <= 0:
            await self.pokojovka.async_release(POKOJOVKA_CLAIM_TOPENI)
            return
        await self.pokojovka.async_claim(POKOJOVKA_CLAIM_TOPENI, round(want, 1))

    # ------------------------------------------------------------------ #
    # Hlídač restartu po předání ekvitermě
    # ------------------------------------------------------------------ #
    async def _maybe_stone_start(self) -> None:
        """Stroj při převzetí stojí -> rozjeď ho kamenem Topit.

        Návnada sama jen zvedne setpoint a doufá; kámen navíc srovná sezónu, přepne
        do Pouze TČ a POČKÁ na skutečné otáčky. A hlavně: alokátor bez běžícího stroje
        nekrokuje (nemá z čeho počítat poměr aep/otáčky), takže bez tohohle by program
        uvázl ve stavu „aktivní, ale nic se neděje" – program čeká na stroj, stroj na povel.
        Pojistka: nepouštět dokola, když stroj nenaskočí."""
        rpm = self._num(ACOND_RPM)
        if rpm is not None and rpm >= TOPENI_MIN_RPM:
            return
        if not self._smi_topit():
            return                      # mimo okno / v pauze Zebry stroj nestartovat
        now = dt_util.utcnow().timestamp()
        if self._last_stone is not None and (now - self._last_stone) < TOPENI_STONE_COOLDOWN_S:
            return
        self._last_stone = now
        _LOGGER.debug("TOPENI: převzetí se stojícím strojem -> kámen Topit (po readbacku)")
        # NEJDŘÍV počkat, až readback 30008 ukáže návnadu: kámen si z registru bere
        # „původní zpátečku" a na konci ji VRACÍ. Kdyby vystřelil hned, přečetl by
        # ještě starou ekvitermu a na konci by návnadu smazal -> stroj by dojel
        # ekvitermu a zhasl („aktivní, ale nic se neděje"). Čeká se na readback,
        # ne slepou pauzu (jeden spadlý poll Acondu nesmí rozhodnout); po stropu
        # se kámen pustí stejně – horší varianta nesmí blokovat start.
        self.entry.async_create_background_task(
            self.hass, self._stone_after_navnada(), "mar_topeni_stone"
        )

    async def _stone_after_navnada(self) -> None:
        """Počkej na readback návnady (max TOPENI_STONE_WAIT_S), pak pusť kámen."""
        target = self.coordinator.topeni_target
        waited = 0.0
        while waited < TOPENI_STONE_WAIT_S:
            if not self._active or not self.enabled:
                return                  # epizoda mezitím skončila -> nestartovat
            rb = self._num(ACOND_RETURN_READBACK)
            if target is None or (
                rb is not None and abs(rb - target) <= TOPENI_RESTART_TOL
            ):
                break
            await asyncio.sleep(TOPENI_STONE_POLL_S)
            waited += TOPENI_STONE_POLL_S
            target = self.coordinator.topeni_target
        if not self._smi_topit():
            return
        if self.seq is not None:
            self.seq.fire_start()

    def set_hlidac(self, value: bool) -> None:
        """Vypínač hlídače (switch.mar_topeni_hlidac). Vypnutí sundá i už nasazený
        hlídač; restart po předání se pak nechá čistě na hysterezi stroje."""
        self.hlidac_enabled = bool(value)
        if not self.hlidac_enabled:
            self._disarm_watchdog()
        self._notify()

    def _arm_watchdog(self) -> None:
        """Nasaď hlídač. Spouštěč je UDÁLOST (vypnutí primárního čerpadla), ne časovač:
        stroj jím po ~15 min proplachuje okruh a jedině tehdy je zpátečka pravdivá."""
        if not self.hlidac_enabled:
            return                      # hlídač vypnutý uživatelem -> nenasazovat
        if self._wd_armed:
            return
        self._wd_armed = True
        self._unsub_pump = async_track_state_change_event(
            self.hass, [ACOND_PUMP_BIT], self._on_pump
        )
        _LOGGER.debug("TOPENI: hlídač restartu nasazen (čekám na proplach okruhu)")

    def _disarm_watchdog(self) -> None:
        if self._unsub_pump is not None:
            self._unsub_pump()
            self._unsub_pump = None
        self._wd_armed = False

    @callback
    def _on_pump(self, event) -> None:  # noqa: ANN001
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        if old is None or new is None:
            return
        if old.state == "on" and new.state == "off":
            self.hass.async_create_task(self._wd_check())

    async def _wd_check(self) -> None:
        """Doběhl proplach okruhu -> teď je zpátečka pravdivá. Vyhodnoť a případně
        pusť kámen Topit. JEDNORÁZOVĚ: po výstřelu se hlídač sundá."""
        if not self._wd_armed:
            return
        if (
            not self.enabled
            or not self.coordinator.is_standard()
            or self.coordinator.strategy != STRATEGY_EKVITERM
            or self._is_on(ACOND_SUMMER)
        ):
            self._disarm_watchdog()
            return
        # ČASOVÉ PROGRAMY MAJÍ PŘEDNOST: mimo okno Den/noc / v pauze Zebry / za
        # útlumu PZ stroj nestartovat (gate v evaluate hlídač beztak brzy sundá)
        if not self._smi_topit():
            return
        # TUV/odmraz: 30009 je zašpiněná cizím dějem -> tenhle proplach neplatí,
        # počkáme na další (hlídač zůstává nasazený)
        if self._is_on(ACOND_BIT_TUV) or self._is_on(ACOND_BIT_DEFROST):
            return
        if self.coordinator.suspend_write:
            return                      # běží kámen -> nepouštěj druhý
        # ŽIVOST ČIDEL MÍSTNOSTI: když teď nežije 30002 nebo 40001, korekce v
        # return_final je falešná NULA a práh by byl „čistá křivka bez korekce"
        # (přesně chyba ze železa). Tenhle proplach přeskoč, počkej na další.
        if self._num(ACOND_INDOOR) is None or self._num(ACOND_ROOM_SET) is None:
            return

        data = self.coordinator.data
        cil = getattr(data, "return_final", None) if data else None
        t_zp = self._num(ACOND_RETURN_ACT)
        readback = self._num(ACOND_RETURN_READBACK)
        if cil is None or t_zp is None or readback is None:
            return
        # PAST: kámen si bere „původní zpátečku" z registru. Dokud tam po předání visí
        # ještě návnada, vzal by si ji jako základ a na konci by ji tam vrátil.
        if abs(readback - cil) > TOPENI_RESTART_TOL:
            return
        if t_zp > cil + TOPENI_RESTART_OVER:
            return                      # ještě moc horko -> počkej na další proplach

        self._disarm_watchdog()
        _LOGGER.debug(
            "TOPENI: hlídač restartu -> kámen Topit (zpátečka %.1f, ekviterma %.1f)",
            t_zp, cil,
        )
        if self.seq is not None:
            self.seq.fire_start()

    # ------------------------------------------------------------------ #
    # Návnada 40008 = ekviterma + návnada (idempotentně)
    # ------------------------------------------------------------------ #
    async def _write_navnada(self) -> None:
        data = self.coordinator.data
        base = getattr(data, "return_final", None) if data else None
        if base is None:
            return                       # ekviterma zatím nemá cíl -> drž (neškodné)
        # MĚKKÝ STROP: návnada nikdy nad „Nejvyšší zpátečku" (ochrana podlahy) a
        # nikdy POD ekvitermní základ (jinak bychom chladili pod normální topení –
        # v nejhorším návnada zmizí a topí se prostě ekvitermně).
        target = min(base + self.navnada, self.max_zp)
        target = max(target, base)
        target = round(min(target, MAX_RETURN), 1)
        target = max(target, MIN_RETURN)
        self.coordinator.topeni_target = target
        # zápis-při-změně proti readbacku 30008 (jako coordinator/minimum)
        readback = self._num(ACOND_RETURN_READBACK)
        if readback is not None and round(readback, 1) == target:
            self._last_40008 = target
            return
        if self._last_40008 is not None and round(self._last_40008, 1) == target:
            return
        self._last_40008 = target
        await self._set_number(ACOND_TARGET, target)

    # ------------------------------------------------------------------ #
    # Buffer bilance + odběry
    # ------------------------------------------------------------------ #
    def _sample(self) -> None:
        b = self.bilance_syrova()
        if b is not None:
            self._buf.append((dt_util.utcnow().timestamp(), b))
            self._prune()

    def _prune(self) -> None:
        cutoff = dt_util.utcnow().timestamp() - self.okno_s
        # nech aspoň jeden vzorek starší než okno, ať plocha pokrývá celé okno
        keep: list[tuple[float, float]] = []
        for i, (t, v) in enumerate(self._buf):
            if t >= cutoff:
                keep.append((t, v))
            elif i + 1 < len(self._buf) and self._buf[i + 1][0] >= cutoff:
                keep.append((t, v))   # poslední pod-okenní vzorek drž (okraj plochy)
        self._buf = keep or self._buf[-1:]

    def _sub_batt_vykon(self) -> None:
        if self._unsub_batt_vykon is not None:
            self._unsub_batt_vykon()
            self._unsub_batt_vykon = None
        if not self._src_batt_vykon:
            return
        self._unsub_batt_vykon = async_track_state_change_event(
            self.hass, [self._src_batt_vykon], self._on_source_change
        )

    @callback
    def _on_source_change(self, event) -> None:  # noqa: ANN001
        self._sample()
        if self._started:
            self.hass.async_create_task(self._evaluate_and_notify())

    @callback
    def _on_tick(self, now) -> None:  # noqa: ANN001
        self._sample()               # rovnoměrný vzorek bilance (30 s)
        self.hass.async_create_task(self._evaluate_and_notify())

    async def _evaluate_and_notify(self) -> None:
        await self.async_evaluate()
        self._notify()

    # ------------------------------------------------------------------ #
    # Retry (registr nedostupný při převzetí)
    # ------------------------------------------------------------------ #
    def _schedule_retry(self) -> None:
        if self._unsub_retry is not None:
            return

        @callback
        def _fire(now) -> None:  # noqa: ANN001
            self._unsub_retry = None
            self.hass.async_create_task(self._evaluate_and_notify())

        self._unsub_retry = async_track_point_in_time(
            self.hass, _fire, dt_util.utcnow() + timedelta(seconds=TOPENI_RETRY_S)
        )

    # ------------------------------------------------------------------ #
    # Store + dispatch + čtení
    # ------------------------------------------------------------------ #
    async def _save(self) -> None:
        await self._store.async_save(
            {"active": self._active, "store_40014": self._store_40014}
        )

    async def _clear_store(self) -> None:
        await self._store.async_save({})

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_topeni_updated(self.entry.entry_id))

    async def _set_number(self, entity_id: str, value: float) -> None:
        await self.hass.services.async_call(
            "number", "set_value",
            {"entity_id": entity_id, "value": round(float(value), 1)},
            blocking=False,
        )

    def _read_aep(self) -> float | None:
        """Okamžitý příkon TČ (W). Auto-detekce jednotky: kW -> ×1000."""
        st = self.hass.states.get(ACOND_POWER)
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            val = float(st.state)
        except (ValueError, TypeError):
            return None
        unit = str(st.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit == "kw":
            val *= 1000.0
        return val

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

    def _attr(self, entity_id: str, attr: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None:
            return None
        try:
            return float(st.attributes.get(attr))
        except (ValueError, TypeError):
            return None
