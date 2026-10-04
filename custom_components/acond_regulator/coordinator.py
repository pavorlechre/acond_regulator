"""Fáze 1: vytvoření teploty do ekvitermy.

Cyklus:
  1. open-meteo: hodinová předpověď -> průměr příštích N h + pole [čas, t]
  2. met.no:     hodinová předpověď -> průměr příštích N h + pole [čas, t]
  3. průměr obou předpovědí (po hodinách i jako skalár)
  4. minulý průměr venkovní teploty z vlastního bufferu (acond_30010, M h)
  5. ekvitermní (modelová) teplota = (minulý průměr + budoucí průměr) / 2

Zápis do TČ ani křivka tady ještě nejsou – to je fáze 2+.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    POCASI_HODIN,
    ACOND_BIT_TUV,
    ACOND_INDOOR,
    ACOND_OUTDOOR,
    ACOND_REG_TYPE,
    ACOND_RETURN_READBACK,
    ACOND_ROOM_SET,
    ACOND_SUMMER,
    ACOND_TARGET,
    REG_TYPE_STANDARD,
    BUFFER_HOURS,
    BUFFER_SAMPLE_MIN_S,
    FORECAST_TTL_S,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CURVE_X,
    CURVE_Y_DEFAULT,
    DEFAULT_COEF,
    DEFAULT_FUTURE_HOURS,
    DEFAULT_PAST_HOURS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_RETURN,
    METNO_URL,
    MIN_RETURN,
    OPEN_METEO_URL,
    SMOOTH_ALPHA,
    STALY_OFFSET,
    STORAGE_VERSION,
    STRATEGY_BEZ_MAR,
    STRATEGY_BOOST,
    STRATEGY_EKVITERM,
    STRATEGY_MINIMUM,
    STRATEGY_STALY,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class RegulatorResult:
    """Výstup cyklu (čteno senzory)."""

    avg1: float | None = None              # open-meteo průměr N h
    avg2: float | None = None              # met.no průměr N h
    avg_forecast: float | None = None      # průměr obou (skalár)
    past_avg: float | None = None          # minulý průměr venkovní
    model_temp: float | None = None        # ekvitermní teplota
    series1: list = field(default_factory=list)        # [{datetime, temperature}]
    series2: list = field(default_factory=list)
    series_avg: list = field(default_factory=list)
    # --- fáze 2 ---
    return_base: float | None = None       # z křivky (bez korekce)
    korekce: float | None = None           # koef * (set - indoor)
    return_final: float | None = None      # ekvitermní cíl (z křivky + korekce, po clampu)
    target: float | None = None            # co se reálně zapisuje = return_final + posun strategie
    strategy: str = "Ekviterma"            # aktivní strategie Patra 1
    summer: bool = False                   # letní režim (nezapisuje se)
    tuv: bool = False                      # ohřev TUV (nezapisuje se – gate „b")
    standard: bool = True                  # Acond Typ regulace == Standard (jinak hluchý)
    reg_type: str | None = None            # živý řetězec typu regulace (pro okno)
    written: float | None = None           # co se zapsalo do 40008


class MarCoordinator(DataUpdateCoordinator[RegulatorResult]):
    """Sběr předpovědí + výpočet ekvitermní teploty."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
        )
        # laditelné hodiny (plní number entity, default zde)
        self.past_hours: int = DEFAULT_PAST_HOURS
        self.future_hours: int = DEFAULT_FUTURE_HOURS

        # --- fáze 2: ekvitermní křivka + zápis ---
        # kanonické parametry (plní number entity; později i CSV import na dálku)
        self.curve_y: list[float] = list(CURVE_Y_DEFAULT)   # Y při CURVE_X
        self.coef: float = DEFAULT_COEF                     # násobič korekce
        self.write_enabled: bool = False                    # vypínač zápisu (default vyp.)
        self.suspend_write: bool = False                    # kámen START/STOP běží -> nezasahovat do 40008
        self.utlum_pz: float | None = None                  # útlum PZ: drž 40008 na této hodnotě (None = ekviterma)
        self.strategy: str = STRATEGY_EKVITERM              # aktivní strategie Patra 1
        self.standard_ok: bool = True                       # cache typu regulace (pro okno)
        self.minimum_target: float | None = None           # cíl držený smyčkou Minima (pro okno)
        self.topeni_owns: bool = False                      # topení podle přebytků vlastní 40008/40014 -> coordinator uhne
        self.topeni_target: float | None = None            # návnada 40008, kterou topení drží (pro okno)
        self._model_smooth: float | None = None             # stav EMA vyhlazení model_temp

        # buffer minulé venkovní teploty (version-proof, přežije restart)
        self._buffer: list[list] = []
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_history")

        # TTL cache předpovědí: mimořádné refreshe (request_refresh od programů)
        # nesmí pokaždé mlátit do open-meteo/met.no – v rámci TTL se vrací poslední
        # stažená předpověď. TTL < update_interval, takže řádný 10min tik vždy
        # stáhne čerstvou. Cache se přeskočí, když by nestačila delšímu oknu.
        self._fcst_ts: float = 0.0
        self._fcst_n: int = 0
        self._fcst_s1: list = []
        self._fcst_s2: list = []
        self.pocasi_symboly: list = []                    # ouško Počasí: značky met.no po hodinách
        self.pocasi_s1: list = []                         # ouško Počasí: celé stažené předpovědi
        self.pocasi_s2: list = []
        self.pocasi_avg: list = []

    async def async_load_buffer(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, list):
            self._buffer = data

    # ------------------------------------------------------------------ #
    async def _async_update_data(self) -> RegulatorResult:
        res = RegulatorResult()
        bh = max(1, int(self.past_hours))          # váha minulosti (h); min 1
        fh = max(0, int(self.future_hours))        # váha předpovědi (h); 0 = bez předpovědi
        n_sensor = max(1, fh)                      # informativní senzory předpovědi: vždy aspoň 1 h
        n_display = max(12, n_sensor)              # do grafu vždy aspoň 12 h
        # Ouško Počasí: 24 h předpovědi do grafu + fh h navíc, aby výhled T ekv
        # došel poctivě (s celým oknem předpovědi) až na konec grafu.
        n_fetch = max(POCASI_HODIN + fh + 1, n_display)

        now_ts = dt_util.utcnow().timestamp()
        if not ((now_ts - self._fcst_ts) < FORECAST_TTL_S and self._fcst_n >= n_fetch):
            self._fcst_s1 = await self._open_meteo(n_fetch)
            self._fcst_s2 = await self._met_no(n_fetch)
            self._fcst_n = n_fetch
            self._fcst_ts = now_ts
        # (jinak čerstvá cache stačí -> žádný dotaz na API při mimořádném refreshi)
        # Ouško Počasí dostane celou staženou řadu; senzory, jejich atributy
        # (ApexCharts) i model dál jen prvních n_display hodin jako dřív.
        self.pocasi_s1 = list(self._fcst_s1)
        self.pocasi_s2 = list(self._fcst_s2)
        self.pocasi_avg = self._merge(self.pocasi_s1, self.pocasi_s2)
        res.series1 = list(self._fcst_s1[:n_display])
        res.series2 = list(self._fcst_s2[:n_display])
        res.series_avg = self._merge(res.series1, res.series2)

        # informativní průměry předpovědi (senzory) – z prvních n_sensor hodin
        res.avg1 = self._mean(res.series1[:n_sensor])
        res.avg2 = self._mean(res.series2[:n_sensor])
        res.avg_forecast = self._mean(res.series_avg[:n_sensor])

        res.past_avg = self._past_average()

        if res.past_avg is None:
            raise UpdateFailed("Chybí minulý průměr (buffer zatím prázdný).")

        # --- modelová teplota: vážení PO HODINÁCH ---
        # model = (bh·minulost + fh·předpověď) / (bh + fh);  fh=0 -> jen minulost
        fcst_model = self._mean(res.series_avg[:fh]) if fh > 0 else None
        if fcst_model is None:
            # fh=0 nebo předpověď nedostupná -> jeď podle minulosti (neztrácej regulaci)
            raw_model = res.past_avg
        else:
            raw_model = (bh * res.past_avg + fh * fcst_model) / (bh + fh)

        # vyhlazení vstupu do ekvitermy: lehká EMA + zaokrouhlení na 0,1.
        # model_temp je už 24/+12h průměr, ale zbytkový sub-0,1°C zub se přes křivku
        # propisoval do drobného cukání „zpátečky z ekvitermy" (a zbytečných zápisů).
        # EMA zub odfiltruje, aniž přidá citelné zpoždění pomalému signálu.
        if self._model_smooth is None:
            self._model_smooth = raw_model
        else:
            self._model_smooth = SMOOTH_ALPHA * raw_model + (1 - SMOOTH_ALPHA) * self._model_smooth
        res.model_temp = round(self._model_smooth, 1)

        # --- fáze 2: křivka -> zpátečka ---
        res.return_base = self._curve(res.model_temp)
        res.korekce = self._correction()
        res.return_final = self._clamp(res.return_base + res.korekce)
        res.summer = self._is_summer()
        res.tuv = self._is_tuv()
        res.reg_type = self.reg_type
        res.standard = self.is_standard()
        self.standard_ok = res.standard
        res.strategy = self.strategy

        # --- Patro 1: strategie posune CÍL zápisu, ne výpočet ---
        # Stálý výkon = ekviterma + posun (kotví na křivce -> bezpečné). BOOST zatím
        # placeholder = jako ekviterma. Bez MaR = nezapisovat. Minimum = vlastní ho
        # event-driven smyčka (minimum.py), coordinator do 40008 nesahá, jen ukáže cíl.
        offset = STALY_OFFSET if self.strategy in (STRATEGY_STALY, STRATEGY_BOOST) else 0.0
        hands_off = self.strategy == STRATEGY_BEZ_MAR
        minimum = self.strategy == STRATEGY_MINIMUM
        if minimum:
            res.target = self.minimum_target
        else:
            res.target = self._clamp(res.return_final + offset)

        # Útlum PZ: drž zpátečku na pevné hodnotě (netop, ale TČ zůstává v normálním
        # módu -> TUV a oběh jedou). Přepíše cíl KAŽDÉ strategie včetně Minima –
        # Netopit je povel uživatele a platí vždy. Smyčka Minima po dobu útlumu mlčí.
        utlum = self.utlum_pz is not None
        if utlum:
            res.target = self.utlum_pz

        # Gate „b": během ohřevu TUV nezapisovat. Kompresor jede na vodu, zpátečka
        # vyletí vysoko a firmware si 40008 stejně dočasně přepisuje sám – náš zápis
        # by byl neúčinný a jen by se s firmwarem přetahoval o registr. Počítáme dál,
        # jen nezapisujeme; mar_stav ukáže cíl i důvod pauzy.
        # suspend_write: běží kámen START/STOP a sám manipuluje 40008 -> nešlapat mu do toho.
        # hands_off: strategie Bez MaR. minimum: 40008 vlastní smyčka Minima.
        # standard: mimo Typ regulace = Standard je 40008 do prázdna -> nezapisuj.
        if (
            self.write_enabled
            and not hands_off
            and (not minimum or utlum)
            and not self.topeni_owns
            and not res.summer
            and not res.tuv
            and not self.suspend_write
            and res.standard
        ):
            res.written = await self._write_setpoint(res.target)

        await self._store.async_save(self._buffer)
        return res

    # ------------------------------------------------------------------ #
    # Předpovědi
    # ------------------------------------------------------------------ #
    async def _open_meteo(self, n: int) -> list:
        session = async_get_clientsession(self.hass)
        params = {
            "latitude": self.entry.data[CONF_LATITUDE],
            "longitude": self.entry.data[CONF_LONGITUDE],
            "hourly": "temperature_2m",
            "timezone": "UTC",
            "forecast_days": 3,          # ať je vždy aspoň 24 h dopředu i večer
        }
        try:
            resp = await session.get(OPEN_METEO_URL, params=params, timeout=30)
            data = await resp.json()
            times = data["hourly"]["time"]
            temps = data["hourly"]["temperature_2m"]
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("open-meteo selhalo: %s", err)
            return []

        now = dt_util.utcnow()
        series: list = []
        for t_str, temp in zip(times, temps):
            ts = dt_util.parse_datetime(t_str)
            if ts is None or temp is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt_util.UTC)
            if ts >= now - timedelta(minutes=30):
                series.append({"datetime": ts.isoformat(), "temperature": round(float(temp), 1)})
            if len(series) >= n:
                break
        return series

    async def _met_no(self, n: int) -> list:
        session = async_get_clientsession(self.hass)
        params = {
            "lat": self.entry.data[CONF_LATITUDE],
            "lon": self.entry.data[CONF_LONGITUDE],
        }
        try:
            resp = await session.get(
                METNO_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=30
            )
            data = await resp.json()
            ts_list = data["properties"]["timeseries"]
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("met.no selhalo: %s", err)
            return []

        now = dt_util.utcnow()
        series: list = []
        symboly: list = []
        for item in ts_list:
            ts = dt_util.parse_datetime(item.get("time", ""))
            if ts is None:
                continue
            try:
                temp = item["data"]["instant"]["details"]["air_temperature"]
            except (KeyError, TypeError):
                continue
            if ts >= now - timedelta(minutes=30):
                if len(series) < n:
                    series.append({"datetime": ts.isoformat(), "temperature": round(float(temp), 1)})
                # Pro ouško Počasí: značka počasí a srážky na příští hodinu.
                # Jen zobrazení — do regulace nevstupuje.
                h1 = (item.get("data") or {}).get("next_1_hours") or {}
                znacka = (h1.get("summary") or {}).get("symbol_code")
                if znacka and len(symboly) < POCASI_HODIN + 3:
                    symboly.append({
                        "datetime": ts.isoformat(),
                        "symbol": znacka,
                        "srazky": (h1.get("details") or {}).get("precipitation_amount"),
                    })
            if len(series) >= n and len(symboly) >= POCASI_HODIN + 3:
                break
        self.pocasi_symboly = symboly
        return series

    def _merge(self, s1: list, s2: list) -> list:
        """Průměr obou předpovědí po hodinách (podle času z open-meteo)."""
        if not s1 and not s2:
            return []
        by_time = {p["datetime"]: p["temperature"] for p in s2}
        merged: list = []
        base = s1 if s1 else s2
        for p in base:
            t = p["temperature"]
            other = by_time.get(p["datetime"])
            val = round((t + other) / 2, 1) if other is not None else t
            merged.append({"datetime": p["datetime"], "temperature": val})
        return merged

    @staticmethod
    def _mean(series: list) -> float | None:
        vals = [p["temperature"] for p in series]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 1)

    # ------------------------------------------------------------------ #
    # Minulost
    # ------------------------------------------------------------------ #
    def _past_average(self) -> float | None:
        now = dt_util.utcnow()
        st = self.hass.states.get(ACOND_OUTDOOR)
        if st is not None:
            try:
                val = float(st.state)
            except (ValueError, TypeError):
                val = None
            if val is not None:
                # vzorek jen když je poslední starší než BUFFER_SAMPLE_MIN_S:
                # průměr je počtově vážený a shluk mimořádných refreshů (FVE,
                # topení, pokojovka) by ho vychýlil k obdobím FV aktivity
                last_ts = None
                if self._buffer:
                    last_ts = dt_util.parse_datetime(self._buffer[-1][0])
                if last_ts is None or (now - last_ts).total_seconds() >= BUFFER_SAMPLE_MIN_S:
                    self._buffer.append([now.isoformat(), val])

        # rozparsuj časy jednou
        parsed: list[tuple[datetime, float]] = []
        for s in self._buffer:
            ts = dt_util.parse_datetime(s[0])
            if ts is not None:
                parsed.append((ts, s[1]))

        # PERZISTENTNÍ buffer: ořez jen proti pevné hloubce BUFFER_HOURS,
        # NIKDY podle nastaveného okna -> změna „hodiny minulost" nemaže historii.
        keep_horizon = now - timedelta(hours=BUFFER_HOURS)
        parsed = [(ts, v) for ts, v in parsed if ts >= keep_horizon]
        self._buffer = [[ts.isoformat(), v] for ts, v in parsed]

        if not parsed:
            return None

        # ČTECÍ okno: průměr jen posledních past_hours hodin z bufferu (read-time slice)
        win_horizon = now - timedelta(hours=max(1, int(self.past_hours)))
        vals = [v for ts, v in parsed if ts >= win_horizon]
        if not vals:                      # okno zatím nemá data (čerstvě po startu) -> vezmi, co je
            vals = [v for _, v in parsed]
        return round(sum(vals) / len(vals), 1)

    # ------------------------------------------------------------------ #
    def set_hours(self, past: int | None = None, future: int | None = None) -> None:
        if past is not None:
            self.past_hours = int(past)
        if future is not None:
            self.future_hours = int(future)

    # ------------------------------------------------------------------ #
    # Fáze 2: křivka, korekce, zápis
    # ------------------------------------------------------------------ #
    def _curve(self, x: float) -> float:
        """Po částech lineární interpolace zpátečky z 5 bodů. Mimo rozsah ploše."""
        xs, ys = CURVE_X, self.curve_y
        if x <= xs[0]:
            return round(ys[0], 1)
        if x >= xs[-1]:
            return round(ys[-1], 1)
        for i in range(len(xs) - 1):
            if xs[i] <= x <= xs[i + 1]:
                f = (x - xs[i]) / (xs[i + 1] - xs[i])
                return round(ys[i] + f * (ys[i + 1] - ys[i]), 1)
        return round(ys[-1], 1)

    def _correction(self) -> float:
        """koeficient * (požadovaná - skutečná teplota místnosti)."""
        room_set = self._state_float(ACOND_ROOM_SET)
        room_act = self._state_float(ACOND_INDOOR)
        if room_set is None or room_act is None:
            return 0.0
        return round(self.coef * (room_set - room_act), 2)

    def _clamp(self, value: float) -> float:
        return round(max(MIN_RETURN, min(MAX_RETURN, value)), 1)

    def _is_summer(self) -> bool:
        st = self.hass.states.get(ACOND_SUMMER)
        return st is not None and st.state == "on"

    def _is_tuv(self) -> bool:
        st = self.hass.states.get(ACOND_BIT_TUV)
        return st is not None and st.state == "on"

    @property
    def reg_type(self) -> str | None:
        """Živý řetězec Typu regulace Acondu (30015). None dokud entita nenaběhla."""
        st = self.hass.states.get(ACOND_REG_TYPE)
        if st is None or st.state in ("unknown", "unavailable", "", None):
            return None
        return st.state

    def is_standard(self) -> bool:
        """True == Acond je v Typu regulace Standard (jediný, kde 40008 platí).

        Neznámý stav (entita ještě nenaběhla) bereme benevolentně jako True –
        jinak by MaR po startu zbytečně mlčel, než 30015 dorazí. Jakmile dorazí
        jiná hodnota, pojistka sepne (watcher v __init__ udělá čistý stůl)."""
        rt = self.reg_type
        return rt is None or rt == REG_TYPE_STANDARD

    async def _write_setpoint(self, value: float) -> float | None:
        """Zápis do 40008 přes number.set_value; jen když se zaokrouhlená hodnota
        liší od readbacku 30008 (zápis-při-změně)."""
        # POJISTKA PROTI ZÁVODU S KAMENEM: gate se kontroloval na začátku cyklu,
        # ale kámen mohl odstartovat mezitím (fire_* teď zvedá suspend_write
        # synchronně). Zafrontovaný ekvitermní zápis by mu smazal boost -> re-check
        # těsně před službou a zápis blocking, ať je pořadí deterministické.
        if self.suspend_write:
            return None
        readback = self._state_float(ACOND_RETURN_READBACK)
        if readback is not None and round(readback, 1) == value:
            return None  # beze změny nezapisujeme
        await self.hass.services.async_call(
            "number", "set_value",
            {"entity_id": ACOND_TARGET, "value": value},
            blocking=True,
        )
        return value

    def _state_float(self, entity_id: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None:
            return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            temp = st.attributes.get("temperature")
            try:
                return float(temp)
            except (ValueError, TypeError):
                return None

    def set_curve_point(self, index: int, value: float) -> None:
        if 0 <= index < len(self.curve_y):
            self.curve_y[index] = float(value)

    def set_coef(self, value: float) -> None:
        self.coef = float(value)

    def set_write_enabled(self, value: bool) -> None:
        self.write_enabled = bool(value)

    def set_utlum(self, value: float | None) -> None:
        """Zapni/vypni útlum PZ. value = držená zpátečka, None = zpět na ekvitermu."""
        self.utlum_pz = None if value is None else float(value)

    def set_strategy(self, value: str) -> None:
        self.strategy = value
