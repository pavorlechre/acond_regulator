"""Senzory MaR – fáze 1: dvě předpovědi, jejich průměr, ekvitermní teplota."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    KOMPRESOR_VIZUAL_KEY,
    SCHEMA_SMER_KEY,
    ACOND_BIT_COOL,
    ACOND_BIT_DEFROST,
    ACOND_BIT_TUV,
    ACOND_HP_ON,
    ACOND_REG_TYPE,
    ACOND_RETURN_ACT,
    ACOND_RETURN_READBACK,
    ACOND_SUMMER,
    DOMAIN,
    FVE_BADGET_KEY,
    FVE_BADGET_SYROVY_KEY,
    FVE_MIRROR_BATT_KEY,
    FVE_MIRROR_PV_KEY,
    FVE_MIRROR_PRETOK_KEY,
    FVE_MIRROR_BATT_VYKON_KEY,
    FVE_DUM_KEY,
    FVE_IMPORT_DNES_KEY,
    FVE_EXPORT_DNES_KEY,
    FVE_VYROBA_DNES_KEY,
    FVE_PRUMER_KEY,
    FVE_TUV_REZIM_KEY,
    PROFIL_STAV_KEY,
    REZIM_OFF,
    REZIM_ON,
    REZIM_STATES,
    REZIM_WAIT,
    STRATEGY_BEZ_MAR,
    STRATEGY_MINIMUM,
    TOPENI_BILANCE_KEY,
    TOPENI_BILANCE_SYROVA_KEY,
    TOPENI_REZIM_KEY,
    device_info,
    signal_dennoc_updated,
    signal_dovolena_updated,
    signal_fve_updated,
    signal_okna_updated,
    signal_profily_updated,
    signal_run_updated,
    signal_seq_updated,
    signal_topeni_updated,
    signal_zebra_updated,
)
from .coordinator import MarCoordinator
from .fve_denni import EXPORT, IMPORT, VYROBA, FveDenniCitac
from .runstate import RunState
from .sequences import SequenceRunner
from .statistics.sensors import build_statistics_sensors


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: MarCoordinator = data["coordinator"]
    stats = data["stats"]
    run: RunState = data["run"]
    seq: SequenceRunner = data["seq"]
    zebra = data["zebra"]
    dennoc = data["dennoc"]
    okna = data["okna"]
    dovolena = data["dovolena"]
    async_add_entities(
        [
            ForecastSensor(coordinator, entry, "predpoved_1", "Předpověď 1",
                           lambda r: r.avg1, lambda r: r.series1),
            ForecastSensor(coordinator, entry, "predpoved_2", "Předpověď 2",
                           lambda r: r.avg2, lambda r: r.series2),
            ForecastSensor(coordinator, entry, "predpoved_prumer", "Předpověď průměr",
                           lambda r: r.avg_forecast, lambda r: r.series_avg),
            ModelSensor(coordinator, entry),
            BaseReturnSensor(coordinator, entry),
            ReturnSensor(coordinator, entry),
            StavSensor(hass, coordinator, run, seq, zebra, dennoc, okna, dovolena,
                       data["fve"], data["topeni"], entry),
            KompresorVizualSensor(entry),
            SchemaSmerSensor(entry),
            RezimSensor(data["topeni"], entry, TOPENI_REZIM_KEY, "Topeni rezim",
                        signal_topeni_updated, lambda c: c.heating),
            RezimSensor(data["fve"], entry, FVE_TUV_REZIM_KEY, "Fve tuv rezim",
                        signal_fve_updated, lambda c: c.boosting),
            FveReadSensor(data["fve"], entry, FVE_PRUMER_KEY, "Fve prumer pretoku",
                          data["fve"].avg_export, "W", SensorDeviceClass.POWER,
                          "mdi:transmission-tower-export"),
            FveReadSensor(data["fve"], entry, FVE_BADGET_KEY, "Fve badget",
                          data["fve"].avg_badget, "W", SensorDeviceClass.POWER,
                          "mdi:scale-balance"),
            FveReadSensor(data["fve"], entry, FVE_BADGET_SYROVY_KEY, "Fve badget syrovy",
                          data["fve"].badget_syrovy, "W", SensorDeviceClass.POWER,
                          "mdi:scale-balance"),
            FveReadSensor(data["fve"], entry, FVE_MIRROR_BATT_KEY, "Fve baterie",
                          data["fve"].battery_value, "%", SensorDeviceClass.BATTERY,
                          "mdi:battery-70"),
            TopeniReadSensor(data["topeni"], entry, TOPENI_BILANCE_KEY, "Topeni bilance",
                          data["topeni"].bilance, "W", SensorDeviceClass.POWER,
                          "mdi:scale-balance"),
            TopeniReadSensor(data["topeni"], entry, TOPENI_BILANCE_SYROVA_KEY, "Topeni bilance syrova",
                          data["topeni"].bilance_syrova, "W", SensorDeviceClass.POWER,
                          "mdi:scale-balance"),
            FveReadSensor(data["fve"], entry, FVE_MIRROR_PV_KEY, "Fve pv",
                          data["fve"].pv_value, "W", SensorDeviceClass.POWER,
                          "mdi:solar-power"),
            # zrcadla pro schéma (dashboard nejmenuje entity střídače)
            FveReadSensor(data["fve"], entry, FVE_MIRROR_PRETOK_KEY, "Fve pretok",
                          data["fve"].pretok_value, "W", SensorDeviceClass.POWER,
                          "mdi:transmission-tower"),
            FveReadSensor(data["fve"], entry, FVE_MIRROR_BATT_VYKON_KEY, "Fve baterie vykon",
                          data["fve"].batt_vykon_schema, "W", SensorDeviceClass.POWER,
                          "mdi:battery-charging"),
            FveReadSensor(data["fve"], entry, FVE_DUM_KEY, "Fve dum",
                          data["fve"].dum_value, "W", SensorDeviceClass.POWER,
                          "mdi:home-lightning-bolt"),
            FveDenniCitac(data["fve"], entry, FVE_IMPORT_DNES_KEY, "Fve import dnes",
                          IMPORT, "mdi:transmission-tower-import"),
            FveDenniCitac(data["fve"], entry, FVE_EXPORT_DNES_KEY, "Fve export dnes",
                          EXPORT, "mdi:transmission-tower-export"),
            FveDenniCitac(data["fve"], entry, FVE_VYROBA_DNES_KEY, "Fve vyroba dnes",
                          VYROBA, "mdi:solar-power-variant"),
            ProfilStavSensor(data["profily"], entry),
        ]
        + build_statistics_sensors(stats, entry)
    )


class _Base(CoordinatorEntity[MarCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "°C"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)


class ForecastSensor(_Base):
    """Skalární stav = průměr příštích N h; atribut 'forecast' = pole [čas, t] pro Apex."""

    def __init__(self, coordinator, entry, key, name, value_fn, series_fn) -> None:
        super().__init__(coordinator, entry, key, name)
        self._value_fn = value_fn
        self._series_fn = series_fn

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self._value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        if self.coordinator.data is None:
            return {}
        return {"forecast": self._series_fn(self.coordinator.data)}


class ModelSensor(_Base):
    """Vypočtená teplota pro ekvitermu (= (minulý průměr + budoucí průměr)/2)."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "ekvitermni_teplota", "Ekvitermní teplota")

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.model_temp

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data
        if d is None:
            return {}
        return {
            "minuly_prumer": d.past_avg,
            "budouci_prumer": d.avg_forecast,
            "hodiny_minulost": self.coordinator.past_hours,
            "hodiny_predpoved": self.coordinator.future_hours,
        }


class BaseReturnSensor(_Base):
    """Zpátečka z ekvitermní křivky (bez korekce) – samostatná entita pro graf."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "zpatecka_z_ekvitermy", "Zpátečka z ekvitermy")

    @property
    def native_value(self):
        d = self.coordinator.data
        return None if d is None else d.return_base


class ReturnSensor(_Base):
    """Vypočtená zpátečka pro zápis (po křivce, korekci a clampu)."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "vypoctena_zpatecka", "Vypočtená zpátečka")

    @property
    def native_value(self):
        d = self.coordinator.data
        return None if d is None else d.return_final

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data
        if d is None:
            return {}
        return {
            "z_krivky": d.return_base,
            "korekce": d.korekce,
            "letni_rezim": d.summer,
            "zapsano": d.written,
        }


# ===========================================================================
# sensor.mar_stav – popisovač (živá pravda). JEN ČTE, nerozhoduje, nezapisuje.
# ===========================================================================
_UNKNOWN = ("unknown", "unavailable", "", None)


class StavSensor(SensorEntity):
    """Sesbírá vstupy (železo + MaR výpočet), složí vyřešenou pravdu, vystaví ji.

    state  = krátký stabilní token (barevný pás do historie).
    atributy = strukturovaná pravda: dva klíčové booly (topí/reguluje), rozpad
    zpátečky, důvod pauzy, banner „co drží kormidlo", hotová česká věta.

    Není CoordinatorEntity schválně: i když výpočet ekvitermy zrovna není hotový
    (coordinator.data == None po startu), okno má pořád ukázat pravdu o železe
    (topí/netopí). Proto se coordinatoru jen poslouchá, ale nepodmiňuje se jím
    dostupnost. Schéma atributů je kompletní od začátku; pole pro strategie/
    překryvy/profil jsou zatím None/[] a naplní se, až přistanou další kroky.
    """

    _attr_has_entity_name = True
    _attr_name = "Stav"
    _attr_icon = "mdi:heat-pump-outline"

    def __init__(self, hass: HomeAssistant, coordinator: MarCoordinator,
                 run: RunState, seq: SequenceRunner, zebra, dennoc, okna, dovolena,
                 fve, topeni, entry: ConfigEntry) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.run = run
        self.seq = seq
        self.zebra = zebra
        self.dennoc = dennoc
        self.okna = okna
        self.dovolena = dovolena
        self.fve = fve
        self.topeni = topeni
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_stav"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # 1) nový výpočet ekvitermy (coordinator tik / request_refresh)
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
        # 2) rozběh/zastavení kompresoru (debounced primitiv) – okamžitě
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_run_updated(self._entry.entry_id), self.async_write_ha_state
            )
        )
        # 3) fáze kamenů START/STOP – ať okno ukáže, co kámen zrovna dělá
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_seq_updated(self._entry.entry_id), self.async_write_ha_state
            )
        )
        # 3b) fáze překryvu Zebra (topení/pauza/idle)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_zebra_updated(self._entry.entry_id), self.async_write_ha_state
            )
        )
        # 3c) změna Den/noc (master / okna / hrana)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_dennoc_updated(self._entry.entry_id), self.async_write_ha_state
            )
        )
        # 3d) změna Okna +/- teploty (master / okna / delta / hrana výpůjčky)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_okna_updated(self._entry.entry_id), self.async_write_ha_state
            )
        )
        # 3e) změna Dovolená (master / interval / cíle / hrana výpůjčky)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_dovolena_updated(self._entry.entry_id), self.async_write_ha_state
            )
        )
        # 3f) změna FVE (master / boost / badget / baterie) – ať se věta a atributy hnou
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_fve_updated(self._entry.entry_id), self.async_write_ha_state
            )
        )
        # 3g) změna topení podle přebytků (master / převzetí / krok 40014)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_topeni_updated(self._entry.entry_id), self.async_write_ha_state
            )
        )
        # 4) živé změny gate bitů (VYP/TUV/odmraz/léto/typ regulace) – okamžité
        #    překreslení, ať okno nečeká na desetiminutový coordinator tik
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [ACOND_HP_ON, ACOND_BIT_TUV, ACOND_BIT_DEFROST, ACOND_BIT_COOL, ACOND_SUMMER,
                 ACOND_RETURN_ACT, ACOND_REG_TYPE],
                self._on_gate_change,
            )
        )

    @callback
    def _on_gate_change(self, event) -> None:  # noqa: ANN001
        self.async_write_ha_state()

    # ------------------------------------------------------------------ #
    # Čtení podkladu (defenzivní – mini-safe)
    # ------------------------------------------------------------------ #
    def _is_on(self, entity_id: str) -> bool | None:
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

    # ------------------------------------------------------------------ #
    # Skládání pravdy
    # ------------------------------------------------------------------ #
    def _resolve(self) -> dict:
        d = self.coordinator.data
        topi = self.run.kompresor_bezi           # True / False / None
        hp_on = self._is_on(ACOND_HP_ON)
        tuv = self._is_on(ACOND_BIT_TUV)
        odmraz = self._is_on(ACOND_BIT_DEFROST)
        chladi = self._is_on(ACOND_BIT_COOL)
        leto = self._is_on(ACOND_SUMMER)
        write_enabled = bool(self.coordinator.write_enabled)
        strategie = self.coordinator.strategy
        hands_off = strategie == STRATEGY_BEZ_MAR
        seq_active = self.seq.active             # "start" / "stop" / None
        seq_phase = self.seq.phase
        standard_ok = self.coordinator.is_standard()
        reg_type = self.coordinator.reg_type

        # Drží FVE Topení kormidlo? Pak ekviterma NEVELÍ zpátečce – řídí se výkon
        # stroje (40014) a na 40008 visí jen návnada. Ekviterma přitom dál počítá
        # a její hodnota je základ návnady („připravuje teplotu pro FVE Topení").
        topeni_vede = bool(getattr(self.coordinator, "topeni_owns", False))

        # MaR velí zpátečce EKVITERMOU? = Standard A zápis zapnutý A ne Bez MaR A
        # ne léto A ne TUV A neběží kámen A nevede FVE Topení.
        reguluje = (
            standard_ok
            and write_enabled
            and not hands_off
            and (leto is not True)
            and (tuv is not True)
            and (seq_active is None)
            and not topeni_vede
        )

        # --- token (priorita shora, první platný vyhrává) ---
        # Běžící kámen je nejsalientnější (to jsi zrovna zmáčkl) -> nahoře.
        # Pak explicitní firmwarové fáze (odmraz/TUV), pak běh, pak klidový kontext.
        if seq_active == "stop":
            token = "vypinam"
        elif seq_active == "start":
            token = "startuju"
        elif odmraz is True:
            token = "odmraz"
        elif tuv is True:
            token = "tuv"
        elif chladi is True and hp_on is not False and leto is not False:
            # Nad "topi" schválně: bit_1 („TČ v provozu") svítí i při chlazení,
            # takže bez tohohle by se chlazení hlásilo jako topení.
            #
            # Podmínka `hp_on is not False` je nutná: bit_12 v čerpadle po
            # chlazení ZŮSTÁVÁ svítit i po vypnutí TČ (ověřeno na železe
            # 10.08.2026 ve 21:17 — stroj vypnutý, stav hlásil chlazení).
            # Bez ní by rozsvícený bit přebil odpověď „vypnuto", protože se
            # ptá dřív. Neptáme se na běžící kompresor, ale na zapnutý stroj:
            # při chlazení kompresor cykluje a mezi cykly to pořád je chlazení.
            #
            # A druhá pojistka, bit_10: topit jde JEN v zimním režimu a chladit
            # JEN v letním, bez výjimky (Pavel 10.08.2026). Mimo léto tedy
            # nemůže jít o chlazení, ať bit_12 ukazuje cokoli. Sezónu přepíná
            # sám stroj podle průměrné venkovní teploty; hranice („konec topné
            # sezóny" / „start sezony chlazení") se nastavuje jen v aplikaci
            # Acond a v Modbusu není. Nevadí — nepotřebujeme znát práh, jen
            # výsledek, a ten čteme přímo.
            token = "chlazeni"
        elif topi is True:
            token = "topi"
        elif self.coordinator.utlum_pz is not None:
            token = "utlum"
        elif hp_on is False:
            token = "vyp"
        elif leto is True:
            token = "leto"
        else:
            token = "netopi"

        # --- důvod, proč MaR nevelí (pořadí gateů zápisu) ---
        if seq_active is not None:
            duvod = "běží kámen"
        elif not standard_ok:
            duvod = f"Acond není ve Standard ({reg_type or '—'})"
        elif hands_off:
            duvod = "bez MaR"
        elif not write_enabled:
            duvod = "zápis vypnutý"
        elif chladi is True and hp_on is not False and leto is not False:
            # Při chlazení je letní provoz zapnutý, takže "léto" je sice pravda,
            # ale neužitečná: neřekne, že stroj právě pracuje. Chlazení je
            # konkrétnější odpověď na otázku "proč MaR nevelí".
            # Stejná pojistka jako u tokenu — bit_12 po vypnutí TČ zůstává.
            duvod = "chlazení"
        elif leto is True:
            duvod = "léto"
        elif tuv is True:
            duvod = "TUV"
        else:
            duvod = None

        # --- banner „co drží kormidlo" (železo) ---
        # OPRAVA: banner jen když kompresor OPRAVDU běží. Léto/TUV s vypnutým
        # kompresorem není „pozor něco jede" – to patří do věty, ne do výstrahy.
        if topi is True and odmraz is True:
            stav_zeleza = "odmrazování"
        elif topi is True and tuv is True:
            stav_zeleza = "ohřev TUV"
        elif topi is True and chladi is True:
            stav_zeleza = "chlazení"
        else:
            stav_zeleza = None

        if self.coordinator.utlum_pz is not None:
            cil = self.coordinator.utlum_pz          # útlum přebíjí každou strategii
        elif strategie == STRATEGY_MINIMUM:
            cil = self.coordinator.minimum_target   # živý cíl smyčky Minima
        else:
            cil = d.target if d else None
        z_krivky = d.return_base if d else None
        korekce = d.korekce if d else None
        model = d.model_temp if d else None
        zapsano = d.written if d else None
        na_registru = self._num(ACOND_RETURN_READBACK)   # 30008
        skutecna = self._num(ACOND_RETURN_ACT)           # 30009

        return {
            "token": token,
            "topi": topi,
            "reguluje": reguluje,
            "topeni_vede": topeni_vede,
            "duvod": duvod,
            "standard": standard_ok,
            "typ_regulace": reg_type,
            "zebra": self.zebra.as_attr(),
            "dennoc": self.dennoc.as_attr(),
            "okna_teploty": self.okna.as_attr(),
            "dovolena": self.dovolena.as_attr(),
            "fve": self.fve.as_attr(),
            "topeni": self.topeni.as_attr(),
            "stav_zeleza": stav_zeleza,
            "faze_aktivni": seq_active,
            "faze": seq_phase,
            "strategie": strategie,
            "zpatecka_cil": cil,
            "zpatecka_z_krivky": z_krivky,
            "korekce": korekce,
            "model_teplota": model,
            "zpatecka_na_registru": na_registru,
            "zpatecka_skutecna": skutecna,
            "zapsano": zapsano,
            "write_enabled": write_enabled,
        }

    @staticmethod
    def _cz(v) -> str:
        """Číslo do české věty: 30,2 · None -> pomlčka."""
        if v is None:
            return "—"
        try:
            return f"{float(v):.1f}".replace(".", ",")
        except (ValueError, TypeError):
            return str(v)

    def _veta(self, t: dict) -> str:
        cil = self._cz(t["zpatecka_cil"])
        reg = self._cz(t["zpatecka_na_registru"])
        token = t["token"]
        we = t["write_enabled"]
        faze = t["faze"]
        strat = t["strategie"]

        if token == "startuju":
            return f"Zapínám TČ jistě — {faze or 'pracuji'}."
        if token == "vypinam":
            return f"Vypínám TČ šetrně — {faze or 'pracuji'}."
        if not t["standard"]:
            typ = t["typ_regulace"] or "?"
            return (f"MaR nezapisuje: Acond je v Typu regulace «{typ}», ne Standard "
                    f"— nastav Standard a znovu si zvol režim.")
        if strat == STRATEGY_BEZ_MAR:
            return "Bez MaR — MaR do zpátečky nezapisuje."
        if token == "odmraz":
            return f"Probíhá odmrazování. Kompresor běží, MaR nezasahuje. Počítá cíl {cil} °C."
        if token == "tuv":
            return (f"Kompresor běží a ohřívá teplou vodu — MaR zápis pozastavil, "
                    f"počká na konec. Zatím počítá cíl {cil} °C.")
        if token == "topi":
            # Když vede FVE Topení, ekvitermní ocásek („MaR reguluje, cíl X, na registru Y")
            # by lhal a mátl: X je ekvitermní cíl, Y návnada – dvě různá čísla bez
            # vysvětlení. Detail i s čísly vysvětlí přípona FVE Topení, tady jen holý fakt.
            if t.get("topeni_vede"):
                return "Topí se, řídí FVE Topení."
            rezim = "" if strat == "Ekviterma" else f" (režim {strat})"
            if we:
                return f"Topí se, MaR reguluje{rezim}. Cíl zpátečky {cil} °C, na registru {reg} °C."
            return f"Topí se. Zápis vypnutý — MaR jen počítá (cíl {cil} °C)."
        if token == "chlazeni":
            return ("Kompresor běží a chladí — MaR nezasahuje. "
                    "Chlazení má vlastní měření, do statistiky topení se nepočítá.")
        if token == "vyp":
            return "TČ je vypnuté. Běží jen protizámraz."
        if token == "utlum":
            return (f"Útlum — netopím, držím zpátečku na {cil} °C. "
                    f"TČ zůstává v provozu, TUV a oběh jedou.")
        if token == "leto":
            return f"Letní režim — TČ netopí, MaR nezapisuje. Počítá cíl {cil} °C pro případ návratu."
        # netopi
        if we:
            return f"TČ je zapnuté, kompresor stojí (čeká na hysterezi). Zápis zapnutý, cíl {cil} °C."
        return f"TČ je zapnuté, kompresor stojí. Zápis vypnutý — MaR jen počítá (cíl {cil} °C)."

    def _fve_clause(self, fve: dict) -> str | None:
        """Přípona věty za FVE TUV (badget). None, když je master vyplý.

        NÁZVOSLOVÍ: „FVE TUV", ne holé „FVE" – jinak je to proti „FVE Topení" matoucí.
        - boostuje s baterkou: „vypnu kolem CÍL % baterky" (cíl = min(X, soc_start−Y))
        - boostuje bez baterky: „vypnu, až přebytek klesne pod STOP W"
        - master on, zatím netopí: „čekám na přebytek".
        """
        if not fve or not fve.get("aktivni"):
            return None
        if fve.get("topi"):
            if fve.get("ma_baterku") and fve.get("soc_cil") is not None:
                return f"FVE TUV: ohřívám vodu, vypnu kolem {self._cz(fve['soc_cil'])} % baterky"
            return (f"FVE TUV: ohřívám vodu, vypnu, až přebytek klesne pod "
                    f"{self._cz(fve.get('stop'))} W")
        return "FVE TUV: čekám na přebytek"

    def _topeni_clause(self, tp: dict, cil: str, reg: str) -> str | None:
        """Přípona věty za FVE Topení. None, když je master vyplý.

        ČEKÁ: topí normálně ekviterma, nic mimořádného se neděje -> jen odznak.
        AKTIVNÍ: ekviterma nepřestala počítat – její hodnota JE základ návnady, takže
        věta „připravuje teplotu pro FVE Topení" je doslova pravdivá. Zároveň vysvětlí
        dvě různá čísla (cíl vs. registr), která by jinak vypadala jako chyba regulace.
        """
        if not tp or not tp.get("aktivni"):
            return None
        if tp.get("topi"):
            # ekviterma nepřestala počítat – její hodnota JE základ návnady, takže
            # věta je doslova pravdivá a zároveň vysvětlí dvě různá čísla
            zaklad = (f"Ekviterma připravuje teplotu pro FVE Topení: cíl {cil} °C, "
                      f"na registru {reg} °C jako návnada. Beru přebytek, řídím výkon "
                      f"stroje")
            if tp.get("ma_baterku"):
                return f"{zaklad}, držím SoC kolem {self._cz(tp.get('hranice'))} %"
            konec = ""
            if tp.get("odchazi"):
                konec = (f", jsem na minimu a nestačí to — za {self._cz(tp.get('prodleva_min'))}"
                         f" min předám ekvitermě")
            return f"{zaklad}, držím přebytek nad {self._cz(tp.get('stop'))} W{konec}"

        if tp.get("ma_baterku"):
            return f"FVE Topení: čekám, až baterka dojede na {self._cz(tp.get('hranice'))} %"
        return (f"FVE Topení: čekám na přebytek nad {self._cz(tp.get('start'))} W "
                f"(rozjezd stojícího stroje až nad {self._cz(tp.get('start_studeny'))} W)")

    # ------------------------------------------------------------------ #
    @property
    def native_value(self) -> str:
        return self._resolve()["token"]

    @property
    def extra_state_attributes(self) -> dict:
        t = self._resolve()
        fve_clause = self._fve_clause(t["fve"])
        topeni_clause = self._topeni_clause(
            t["topeni"], self._cz(t["zpatecka_cil"]), self._cz(t["zpatecka_na_registru"])
        )
        base_veta = self._veta(t)
        veta = base_veta + (f" · {fve_clause}" if fve_clause else "")
        veta = veta + (f" · {topeni_clause}" if topeni_clause else "")
        return {
            # dva klíčové booly
            "topi": t["topi"],
            "reguluje": t["reguluje"],
            # rozpad zpátečky
            "zpatecka_cil": t["zpatecka_cil"],
            "zpatecka_z_krivky": t["zpatecka_z_krivky"],
            "korekce": t["korekce"],
            "model_teplota": t["model_teplota"],
            "zpatecka_na_registru": t["zpatecka_na_registru"],
            "zpatecka_skutecna": t["zpatecka_skutecna"],
            "zapsano": t["zapsano"],
            # kontext / gate
            "duvod": t["duvod"],
            "standard": t["standard"],
            "typ_regulace": t["typ_regulace"],
            "stav_zeleza": t["stav_zeleza"],
            "faze_aktivni": t["faze_aktivni"],
            "faze": t["faze"],
            "zdroj_rizeni": (
                "MaR – FVE Topení" if t["topeni_vede"]
                else ("MaR ekviterma" if t["write_enabled"] else "uživatel")
            ),
            "rezim": t["strategie"],
            "strategie": t["strategie"],
            # překryvy (Patro 2): zatím jen Zebra; přibudou další časové režimy
            "prekryvy": [t["zebra"]],
            "zebra": t["zebra"],
            # Den/noc (osa A – brána zap/vyp, ne překryv): vlastní klíč
            "dennoc": t["dennoc"],
            # Okna +/- teploty (osa B – časová korekce setpointu 40001): vlastní klíč
            "okna_teploty": t["okna_teploty"],
            # Dovolená (osa B – dlouhý interval, 40001 + 40005): vlastní klíč
            "dovolena": t["dovolena"],
            # FVE – přebytkový ohřev TUV (osa B, badget): vlastní klíč + věta
            "fve": t["fve"],
            "fve_veta": fve_clause,
            # Topení podle přebytků (osa A – SoC-řízený žrout přebytků): vlastní klíč + věta
            "topeni": t["topeni"],
            "topeni_veta": topeni_clause,
            # vlajka tvrdého stopu: drží se, dokud uživatel master zase nezapne.
            # Dashboard na ni věší podmínku „ukaž nastavení", jinak se karta se stropy
            # otevře a hned zavře (je podmíněná masterem, který si strop sám shodil).
            "strop_padl": (t["topeni"] or {}).get("strop_padl"),
            "profil": None,
            "poznamka": None,
            # hotová věta pro tiskárnu (+ přípona FVE, když master jede)
            "veta": veta,
        }

class FveReadSensor(SensorEntity):
    """Čtecí senzor FVE (průměr přetoku / mirror baterie / mirror PV). Čte hodnotu
    z FveControlleru přes value_fn; překresluje se na signal_fve_updated.
    Není CoordinatorEntity – hodnoty jdou z controlleru, ne z ekvitermního tiku."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, fve, entry: ConfigEntry, key: str, name: str,
                 value_fn, unit: str, device_class, icon: str | None = None) -> None:
        self._fve = fve
        self._entry = entry
        self._value_fn = value_fn
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        if icon is not None:
            self._attr_icon = icon
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_fve_updated(self._entry.entry_id), self.async_write_ha_state
            )
        )

    @property
    def native_value(self):
        return self._value_fn()


class TopeniReadSensor(SensorEntity):
    """Čtecí senzor topení podle přebytků (bilance průměr / syrová). Čte hodnotu
    z TopeniControlleru přes value_fn; překresluje se na signal_topeni_updated."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, topeni, entry: ConfigEntry, key: str, name: str,
                 value_fn, unit: str, device_class, icon: str | None = None) -> None:
        self._topeni = topeni
        self._entry = entry
        self._value_fn = value_fn
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        if icon is not None:
            self._attr_icon = icon
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_topeni_updated(self._entry.entry_id), self.async_write_ha_state
            )
        )

    @property
    def native_value(self):
        return self._value_fn()


class RezimSensor(SensorEntity):
    """Tříbarevný pruh do historie: Vyp / Čeká / Aktivní.

    Vzniklo proto, že `history-graph` nad `switch.mar_topeni` ukazuje jen
    MASTER – žlutá tam svítí celý den bez ohledu na to, jestli se něco dělo.
    Tenhle sensor přidává to, co v grafu chybělo: kdy byly podmínky splněné.

    POZOR NA POJMENOVÁNÍ: v kódu `.active` = master zapnutý. Tady „Aktivní"
    znamená, že program DRŽÍ KORMIDLO (`heating` / `boosting`). Kompresor při
    tom běžet nemusí – proto ne „Topí" ani „Ohřívá".

    Slouží dvěma věcem naráz: pruhu v grafu a překryvným kbelíkům statistiky
    (řádky „z přetoků" v tabulce). Proto jedna entita na program, ne dvě.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:chart-timeline-variant"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = REZIM_STATES

    def __init__(self, ctrl, entry: ConfigEntry, key: str, name: str,
                 signal_fn, running_fn) -> None:
        self._ctrl = ctrl
        self._entry = entry
        self._signal_fn = signal_fn
        self._running_fn = running_fn
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._signal_fn(self._entry.entry_id),
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self) -> str:
        if not self._ctrl.enabled:
            return REZIM_OFF
        return REZIM_ON if self._running_fn(self._ctrl) else REZIM_WAIT


class ProfilStavSensor(SensorEntity):
    """sensor.mar_profil_stav – výsledek poslední operace s profilem.

    Není to ozdoba. Načtení je tolerantní (špatný řádek se zahodí a jede se
    dál), takže bez viditelné zpětné vazby by tichá degradace nikdy nevyplavala
    — uživatel by věřil, že načetl osm hodnot, a přitom mu prolezlo pět.
    Podrobnosti (které řádky spadly, odkaz ke stažení) jsou v atributech."""

    _attr_has_entity_name = True
    _attr_name = "Profil stav"
    _attr_icon = "mdi:information-outline"
    _attr_should_poll = False

    def __init__(self, profily, entry: ConfigEntry) -> None:
        self._profily = profily
        self._attr_unique_id = f"{entry.entry_id}_{PROFIL_STAV_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_profily_updated(self._profily.entry.entry_id),
                self._refresh,
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        return self._profily.stav

    @property
    def extra_state_attributes(self) -> dict:
        # diagnostika je tu VŽDY, ne jen po operaci: když se nakopírovaný soubor
        # neobjeví, první otázka je „v jakém adresáři se kouká a co tam vidí"
        return {**self._profily.diagnostika, **self._profily.stav_atributy}


class SchemaSmerSensor(SensorEntity):
    """sensor.mar_schema_smer – kterým směrem ve schématu putuje teplo.

    Kulička ve schématu nese teplo, ne vodu. Při běžném topení jde teplo
    ze stroje do domu, takže výstup je červený a zpátečka modrá. Při
    odmrazování a při chlazení jde teplo opačně a barvy se prohodí.

    Vrací jednu ze tří hodnot:
      • `normal`    – topení, TUV, klid
      • `odmraz`    – odmrazování, obrací se PRIMÁR (sekundár běží dál normálně,
                      z akumulačky pořád odchází teplá voda do domu)
      • `chlazeni`  – chlazení, obrací se primár I sekundár

    PAST, kvůli které to není jednořádková šablona: `bit_12` (chlazení)
    zůstává po skončení chlazení zaseknutý na `on` a sám o sobě nic neznamená.
    Musí se číst spolu s `bit_10` (letní provoz). Tahle znalost má být na
    jednom místě v integraci, ne rozkopírovaná po dashboardech.

    Pořadí je záměrné: odmrazování má přednost, protože může nastat i
    v létě a je krátké a výrazné.
    """

    _attr_has_entity_name = True
    _attr_name = "Schema smer"
    _attr_icon = "mdi:swap-horizontal"
    _attr_should_poll = False

    _ZDROJE = (
        "binary_sensor.acond_30007_tc_status_bit_8",
        "binary_sensor.acond_30007_tc_status_bit_10",
        "binary_sensor.acond_30007_tc_status_bit_12",
    )

    def __init__(self, entry) -> None:  # noqa: ANN001
        self._attr_unique_id = f"{entry.entry_id}_{SCHEMA_SMER_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        from homeassistant.helpers.event import async_track_state_change_event

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, list(self._ZDROJE), self._on_zdroj_change
            )
        )

    @callback
    def _on_zdroj_change(self, event) -> None:  # noqa: ANN001
        self.async_write_ha_state()

    def _zap(self, eid: str) -> bool:
        st = self.hass.states.get(eid)
        return st is not None and st.state == "on"

    @property
    def native_value(self) -> str:
        if self._zap("binary_sensor.acond_30007_tc_status_bit_8"):
            return "odmraz"
        if (self._zap("binary_sensor.acond_30007_tc_status_bit_12")
                and self._zap("binary_sensor.acond_30007_tc_status_bit_10")):
            return "chlazeni"
        return "normal"


class KompresorVizualSensor(SensorEntity):
    """sensor.mar_kompresor_vizual – jméno souboru s emblémem kompresoru.

    Skládá stav × stupeň otáček × teplotní pásmo do jednoho klíče
    (např. `topeni-2c`), který schéma přeloží na `/mar-kompresor/topeni-2c.svg`.

    Logika je PŘENESENÁ ze šablony v okně Režimy, aby obě místa ukazovala
    totéž. Kdyby se měnila, musí se změnit na obou. Konkrétně:
      • stupeň 1–3 z otáček vůči rozsahu KONKRÉTNÍHO stroje (min/max slideru),
        ne vůči pevným prahům — každý Acond má jiný rozsah
      • pásmo a–e z výstupní teploty vůči krajním bodům ekvitermní křivky
        (`number.mar_bod_p15` / `mar_bod_m15`), tedy vůči měřítku TOHOTO domu
      • pořadí stavů stejné jako ve statistice, aby obrázek nelhal proti číslům

    Čte cizí entity ze stavového stroje stejně jako ta šablona — proto ty
    natvrdo psané entity_id. Když chybí, vrátí klidový emblém a nespadne.
    """

    _attr_has_entity_name = True
    _attr_name = "Kompresor vizual"
    _attr_icon = "mdi:hvac"
    _attr_entity_registry_enabled_default = True
    # Bez pollingu – entita se překresluje UDÁLOSTMI ze zdrojů (viz níže).
    # Dokud tu polling byl, maskoval chybu v trackingu: stav se opravil až
    # při třicetisekundovém dotazu, tedy v průměru o 15 s později než šablona.
    _attr_should_poll = False

    _ZDROJE = (
        "sensor.mar_stav",
        "binary_sensor.acond_30045_hp_comp_bit_0",
        "binary_sensor.acond_30007_tc_status_bit_2",
        "binary_sensor.acond_30007_tc_status_bit_10",
        "sensor.acond_30024_comp_rpm_actual",
        "sensor.acond_30018_t_act_water_outlet",
        "number.acond_40014_comp_capacity_max_set",
        "number.mar_bod_p15",
        "number.mar_bod_m15",
        "switch.acond_40006_tc_set_bit_1",
        "switch.acond_40006_tc_set_bit_2",
        "switch.acond_40006_tc_set_bit_3",
        "switch.acond_40006_tc_set_bit_4",
    )

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{entry.entry_id}_{KOMPRESOR_VIZUAL_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        from homeassistant.helpers.event import async_track_state_change_event

        # POZOR: obsluha MUSÍ být @callback metoda, ne holá lambda.
        # Neoznačenou funkci pustí HA jako úlohu do vlákna executoru a
        # `async_write_ha_state()` z jiného vlákna je porušení thread-safety –
        # překreslení se zahodí. Stejný tvar jako `_on_gate_change` u StavSensoru.
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, list(self._ZDROJE), self._on_zdroj_change
            )
        )

    @callback
    def _on_zdroj_change(self, event) -> None:  # noqa: ANN001
        self.async_write_ha_state()

    # ── čtečky, které nespadnou na chybějící entitě ──
    def _zap(self, eid: str) -> bool:
        st = self.hass.states.get(eid)
        return st is not None and st.state == "on"

    def _cislo(self, eid: str, vychozi: float) -> float:
        st = self.hass.states.get(eid)
        try:
            return float(st.state)
        except (AttributeError, TypeError, ValueError):
            return vychozi

    def _atribut(self, eid: str, jmeno: str, vychozi: float) -> float:
        st = self.hass.states.get(eid)
        try:
            return float(st.attributes.get(jmeno))
        except (AttributeError, TypeError, ValueError):
            return vychozi

    @property
    def native_value(self) -> str:
        stav_ent = self.hass.states.get("sensor.mar_stav")
        stav = stav_ent.attributes.get("stav_zeleza") if stav_ent else None
        bezi = self._zap("binary_sensor.acond_30045_hp_comp_bit_0")

        # stupeň otáček vůči rozsahu tohoto stroje
        rpm = self._cislo("sensor.acond_30024_comp_rpm_actual", 0.0)
        lo = self._atribut("number.acond_40014_comp_capacity_max_set", "min", 1500.0)
        hi = self._atribut("number.acond_40014_comp_capacity_max_set", "max", 6000.0)
        t3 = (hi - lo) / 3 if hi > lo else 1.0
        stupen = 1 if rpm < lo + t3 else (2 if rpm < lo + 2 * t3 else 3)

        # teplotní pásmo vůči krajním bodům ekvitermní křivky tohoto domu
        tv = self._cislo("sensor.acond_30018_t_act_water_outlet", 0.0)
        dolni = self._cislo("number.mar_bod_p15", 25.0)
        horni = self._cislo("number.mar_bod_m15", 45.0)
        krok = (horni - dolni) / 3 if horni > dolni else 1.0
        if tv < dolni:
            pasmo = "a"
        elif tv < dolni + krok:
            pasmo = "b"
        elif tv < dolni + 2 * krok:
            pasmo = "c"
        elif tv < horni:
            pasmo = "d"
        else:
            pasmo = "e"

        # klidový režim podle zapnutého bitu, barva podle sezóny
        if self._zap("switch.acond_40006_tc_set_bit_3"):
            rez = "vyp"
        elif self._zap("switch.acond_40006_tc_set_bit_4"):
            rez = "chl"
        elif self._zap("switch.acond_40006_tc_set_bit_2"):
            rez = "biv"
        elif self._zap("switch.acond_40006_tc_set_bit_1"):
            rez = "tc"
        else:
            rez = "aut"
        leto = self._zap("binary_sensor.acond_30007_tc_status_bit_10")

        if stav == "odmrazování":
            return "odmrazovani"
        if stav == "chlazení":
            return "chlazeni"
        if stav == "ohřev TUV":
            return f"tuv-{stupen}{pasmo}"
        if bezi:
            return f"topeni-{stupen}{pasmo}"
        if self._zap("binary_sensor.acond_30007_tc_status_bit_2"):
            return "klid-porucha"
        if rez == "chl":
            return "klid-chl-z"
        return f"klid-{rez}-{'l' if leto else 'z'}"
