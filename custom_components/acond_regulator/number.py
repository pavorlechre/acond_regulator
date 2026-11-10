"""Number entity MaR.

Fáze 1: počet hodin (minulost/předpověď).
Fáze 2: 5 bodů ekvitermní křivky (zpátečka při dané venkovní) + koeficient korekce.
Vše živě editovatelné; tytéž hodnoty bude umět přepsat i CSV import na dálku.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    ACOND_ROOM_SET,
    BUFFER_HOURS,
    POZADOVANA_MISTNOST_KEY,
    POZADOVANA_MISTNOST_STEP,
    R_PASMO_MAX,
    R_PASMO_MIN,
    R_PASMO_STEP,
    R_PAUZA_DEFAULT,
    R_PAUZA_KEY,
    R_START_DEFAULT,
    R_START_KEY,
    ROOM_SET_MAX,
    ROOM_SET_MIN,
    CURVE_KEYS,
    CURVE_Y_DEFAULT,
    DEFAULT_COEF,
    DEFAULT_FUTURE_HOURS,
    DEFAULT_PAST_HOURS,
    DOMAIN,
    MAX_RETURN,
    MIN_RETURN,
    NUMBER_COEF,
    NUMBER_FUTURE_HOURS,
    NUMBER_PAST_HOURS,
    NUMBER_ZEBRA_HEAT,
    NUMBER_ZEBRA_PAUSE,
    OKNA_DELTA_DEFAULT,
    OKNA_DELTA_KEY,
    OKNA_DELTA_MAX,
    OKNA_DELTA_MIN,
    OKNA_DELTA_STEP,
    OKNA_MAX_WINDOWS,
    DOVOLENA_ROOM_DEFAULT,
    DOVOLENA_ROOM_KEY,
    DOVOLENA_ROOM_MAX,
    DOVOLENA_ROOM_MIN,
    DOVOLENA_ROOM_STEP,
    DOVOLENA_TUV_DEFAULT,
    DOVOLENA_TUV_KEY,
    DOVOLENA_TUV_MAX,
    DOVOLENA_TUV_MIN,
    DOVOLENA_TUV_STEP,
    FVE_BATT_POKLES_DEFAULT,
    FVE_BATT_POKLES_KEY,
    FVE_BATT_POKLES_MAX,
    FVE_BATT_POKLES_MIN,
    FVE_BATT_POKLES_STEP,
    FVE_BATT_PRAH_DEFAULT,
    FVE_BATT_PRAH_KEY,
    FVE_BATT_PRAH_MAX,
    FVE_BATT_PRAH_MIN,
    FVE_BATT_PRAH_STEP,
    FVE_START_DEFAULT,
    FVE_START_KEY,
    FVE_START_MAX,
    FVE_START_MIN,
    FVE_START_STEP,
    FVE_STOP_DEFAULT,
    FVE_STOP_KEY,
    FVE_STOP_MAX,
    FVE_STOP_MIN,
    FVE_STOP_STEP,
    TOPENI_HRANICE_KEY, TOPENI_HRANICE_DEFAULT, TOPENI_HRANICE_MIN, TOPENI_HRANICE_MAX, TOPENI_HRANICE_STEP,
    TOPENI_DOLNI_KEY, TOPENI_DOLNI_DEFAULT, TOPENI_DOLNI_MIN, TOPENI_DOLNI_MAX, TOPENI_DOLNI_STEP,
    TOPENI_TAH_KEY, TOPENI_TAH_DEFAULT, TOPENI_TAH_MIN, TOPENI_TAH_MAX, TOPENI_TAH_STEP,
    TOPENI_OKNO_KEY, TOPENI_OKNO_DEFAULT, TOPENI_OKNO_MIN, TOPENI_OKNO_MAX, TOPENI_OKNO_STEP,
    TOPENI_NAVNADA_KEY, TOPENI_NAVNADA_DEFAULT, TOPENI_NAVNADA_MIN, TOPENI_NAVNADA_MAX, TOPENI_NAVNADA_STEP,
    TOPENI_STROP_ZP_KEY, TOPENI_STROP_ZP_DEFAULT, TOPENI_STROP_ZP_MIN, TOPENI_STROP_ZP_MAX, TOPENI_STROP_ZP_STEP,
    TOPENI_NAVNADA_ROOM_KEY, TOPENI_NAVNADA_ROOM_DEFAULT, TOPENI_NAVNADA_ROOM_MIN,
    TOPENI_NAVNADA_ROOM_MAX, TOPENI_NAVNADA_ROOM_STEP,
    TOPENI_STOP_KEY, TOPENI_STOP_DEFAULT, TOPENI_STOP_MIN, TOPENI_STOP_MAX, TOPENI_STOP_STEP,
    TOPENI_START_KEY, TOPENI_START_DEFAULT, TOPENI_START_MIN, TOPENI_START_MAX, TOPENI_START_STEP,
    TOPENI_START_COLD_KEY, TOPENI_START_COLD_DEFAULT, TOPENI_START_COLD_MIN,
    TOPENI_START_COLD_MAX, TOPENI_START_COLD_STEP,
    TOPENI_PRODLEVA_KEY, TOPENI_PRODLEVA_DEFAULT, TOPENI_PRODLEVA_MIN,
    TOPENI_PRODLEVA_MAX, TOPENI_PRODLEVA_STEP,
    TOPENI_BLOK_KEY, TOPENI_BLOK_DEFAULT, TOPENI_BLOK_MIN, TOPENI_BLOK_MAX, TOPENI_BLOK_STEP,
    TOPENI_STROP_MIST_KEY, TOPENI_STROP_MIST_DEFAULT, TOPENI_STROP_MIST_MIN, TOPENI_STROP_MIST_MAX, TOPENI_STROP_MIST_STEP,
    TOPENI_CIDLO_KEY, TOPENI_CIDLO_DEFAULT, TOPENI_CIDLO_MIN, TOPENI_CIDLO_MAX, TOPENI_CIDLO_STEP,
    ZEBRA_DEFAULT_HEAT,
    ZEBRA_DEFAULT_PAUSE,
    ZEBRA_MAX,
    ZEBRA_MIN,
    ZEBRA_STEP,
    device_info,
)
from .coordinator import MarCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: MarCoordinator = data["coordinator"]
    zebra = data["zebra"]
    okna = data["okna"]
    dovolena = data["dovolena"]
    entities: list = [
        HoursNumber(coordinator, entry, NUMBER_PAST_HOURS, "Hodiny minulost",
                    DEFAULT_PAST_HOURS, min_value=1, max_value=BUFFER_HOURS),
        HoursNumber(coordinator, entry, NUMBER_FUTURE_HOURS, "Hodiny předpověď",
                    DEFAULT_FUTURE_HOURS, min_value=0, max_value=48),
        CoefNumber(coordinator, entry),
        ZebraIntervalNumber(zebra, entry, NUMBER_ZEBRA_HEAT, "Zebra topení",
                            ZEBRA_DEFAULT_HEAT, zebra.set_heat),
        ZebraIntervalNumber(zebra, entry, NUMBER_ZEBRA_PAUSE, "Zebra pauza",
                            ZEBRA_DEFAULT_PAUSE, zebra.set_pause),
    ]
    for i, key in enumerate(CURVE_KEYS):
        token = key.replace("bod_", "")  # m15, m5, 0, p5, p15
        entities.append(CurvePointNumber(coordinator, entry, i, key, f"Bod {token}", CURVE_Y_DEFAULT[i]))
    for i in range(OKNA_MAX_WINDOWS):
        n = i + 1
        # -> number.mar_okna_teploty_{n}_delta
        entities.append(
            OknaDeltaNumber(okna, entry, i, OKNA_DELTA_KEY.format(n=n),
                            f"Okna teploty {n} delta")
        )
    # -> number.mar_dovolena_teplota / number.mar_dovolena_tuv
    entities.append(
        DovolenaTargetNumber(
            dovolena, entry, DOVOLENA_ROOM_KEY, "Dovolena teplota",
            DOVOLENA_ROOM_MIN, DOVOLENA_ROOM_MAX, DOVOLENA_ROOM_STEP,
            DOVOLENA_ROOM_DEFAULT, "mdi:home-thermometer", dovolena.set_room_target,
        )
    )
    entities.append(
        DovolenaTargetNumber(
            dovolena, entry, DOVOLENA_TUV_KEY, "Dovolena tuv",
            DOVOLENA_TUV_MIN, DOVOLENA_TUV_MAX, DOVOLENA_TUV_STEP,
            DOVOLENA_TUV_DEFAULT, "mdi:water-thermometer", dovolena.set_tuv_target,
        )
    )
    # -> number.mar_fve_tuv_start / _stop / _baterie_prah
    fve = data["fve"]
    entities.append(
        FveNumber(fve, entry, FVE_START_KEY, "Fve tuv start",
                  FVE_START_MIN, FVE_START_MAX, FVE_START_STEP, FVE_START_DEFAULT,
                  "W", "mdi:play-circle-outline", fve.set_start)
    )
    entities.append(
        FveNumber(fve, entry, FVE_STOP_KEY, "Fve tuv stop",
                  FVE_STOP_MIN, FVE_STOP_MAX, FVE_STOP_STEP, FVE_STOP_DEFAULT,
                  "W", "mdi:stop-circle-outline", fve.set_stop)
    )
    entities.append(
        FveNumber(fve, entry, FVE_BATT_PRAH_KEY, "Fve tuv baterie prah",
                  FVE_BATT_PRAH_MIN, FVE_BATT_PRAH_MAX, FVE_BATT_PRAH_STEP,
                  FVE_BATT_PRAH_DEFAULT, "%", "mdi:battery-charging-high",
                  fve.set_batt_prah)
    )
    entities.append(
        FveNumber(fve, entry, FVE_BATT_POKLES_KEY, "Fve tuv baterie pokles",
                  FVE_BATT_POKLES_MIN, FVE_BATT_POKLES_MAX, FVE_BATT_POKLES_STEP,
                  FVE_BATT_POKLES_DEFAULT, "%", "mdi:battery-minus-variant",
                  fve.set_batt_pokles)
    )
    # -> number.mar_topeni_* (topení podle přebytků; reuse FveNumber – generický)
    topeni = data["topeni"]
    entities.append(FveNumber(topeni, entry, TOPENI_HRANICE_KEY, "Topeni soc hranice",
        TOPENI_HRANICE_MIN, TOPENI_HRANICE_MAX, TOPENI_HRANICE_STEP, TOPENI_HRANICE_DEFAULT,
        "%", "mdi:battery-heart-variant", topeni.set_hranice))
    entities.append(FveNumber(topeni, entry, TOPENI_DOLNI_KEY, "Topeni soc dolni mez",
        TOPENI_DOLNI_MIN, TOPENI_DOLNI_MAX, TOPENI_DOLNI_STEP, TOPENI_DOLNI_DEFAULT,
        "%", "mdi:battery-low", topeni.set_dolni))
    entities.append(FveNumber(topeni, entry, TOPENI_TAH_KEY, "Topeni tah",
        TOPENI_TAH_MIN, TOPENI_TAH_MAX, TOPENI_TAH_STEP, TOPENI_TAH_DEFAULT,
        "W", "mdi:arrow-collapse-down", topeni.set_tah))
    entities.append(FveNumber(topeni, entry, TOPENI_OKNO_KEY, "Topeni okno",
        TOPENI_OKNO_MIN, TOPENI_OKNO_MAX, TOPENI_OKNO_STEP, TOPENI_OKNO_DEFAULT,
        "min", "mdi:timer-sand", topeni.set_okno))
    entities.append(FveNumber(topeni, entry, TOPENI_NAVNADA_KEY, "Topeni navnada",
        TOPENI_NAVNADA_MIN, TOPENI_NAVNADA_MAX, TOPENI_NAVNADA_STEP, TOPENI_NAVNADA_DEFAULT,
        "\u00b0C", "mdi:fishing", topeni.set_navnada))
    # --- BEZBATERKOVÁ VĚTEV (Jan) – stejný slovník jako FVE TUV ---
    # V dashboardu se ukážou jen bez baterie (podmíněné karty na sensor.mar_fve_baterie).
    entities.append(FveNumber(topeni, entry, TOPENI_STOP_KEY, "Topeni stop",
        TOPENI_STOP_MIN, TOPENI_STOP_MAX, TOPENI_STOP_STEP, TOPENI_STOP_DEFAULT,
        "W", "mdi:stop-circle-outline", topeni.set_stop))
    entities.append(FveNumber(topeni, entry, TOPENI_START_KEY, "Topeni start",
        TOPENI_START_MIN, TOPENI_START_MAX, TOPENI_START_STEP, TOPENI_START_DEFAULT,
        "W", "mdi:play-circle-outline", topeni.set_start))
    entities.append(FveNumber(topeni, entry, TOPENI_START_COLD_KEY, "Topeni start studeny",
        TOPENI_START_COLD_MIN, TOPENI_START_COLD_MAX, TOPENI_START_COLD_STEP,
        TOPENI_START_COLD_DEFAULT,
        "W", "mdi:engine-outline", topeni.set_start_cold))
    entities.append(FveNumber(topeni, entry, TOPENI_PRODLEVA_KEY, "Topeni prodleva",
        TOPENI_PRODLEVA_MIN, TOPENI_PRODLEVA_MAX, TOPENI_PRODLEVA_STEP,
        TOPENI_PRODLEVA_DEFAULT,
        "min", "mdi:timer-sand", topeni.set_prodleva))
    entities.append(FveNumber(topeni, entry, TOPENI_BLOK_KEY, "Topeni blok",
        TOPENI_BLOK_MIN, TOPENI_BLOK_MAX, TOPENI_BLOK_STEP, TOPENI_BLOK_DEFAULT,
        "min", "mdi:content-save-cog-outline", topeni.set_blok))

    # Návnada B (pokojovka): druhý řidič FVE Topení. Zvedne POŽADOVANOU teplotu místnosti
    # přes správce pokojovky; účinek jde přes korekci do zpátečky a sám slábne, jak se
    # dům dotápí. 0 = vypnuto (jede jen návnada A). Reálná páka = delta × koef. korekce.
    entities.append(FveNumber(topeni, entry, TOPENI_NAVNADA_ROOM_KEY, "Topeni navnada pokojovka",
        TOPENI_NAVNADA_ROOM_MIN, TOPENI_NAVNADA_ROOM_MAX, TOPENI_NAVNADA_ROOM_STEP,
        TOPENI_NAVNADA_ROOM_DEFAULT,
        "\u00b0C", "mdi:home-thermometer-outline", topeni.set_navnada_room))
    # POZOR – jméno entity ZŮSTÁVÁ „Topeni strop zpatecka" (entity_id je kontrakt a
    # u Pavla i Jana musí být stejné). Změnila se jen ROLE: z pojistky, která program
    # vypínala, je měkký strop návnady = „Nejvyšší zpátečka – podlaha". Popisek v
    # dashboardu je přejmenovaný, entity_id ne.
    entities.append(FveNumber(topeni, entry, TOPENI_STROP_ZP_KEY, "Topeni strop zpatecka",
        TOPENI_STROP_ZP_MIN, TOPENI_STROP_ZP_MAX, TOPENI_STROP_ZP_STEP, TOPENI_STROP_ZP_DEFAULT,
        "\u00b0C", "mdi:pipe", topeni.set_max_zpatecka))
    entities.append(FveNumber(topeni, entry, TOPENI_STROP_MIST_KEY, "Topeni strop mistnost",
        TOPENI_STROP_MIST_MIN, TOPENI_STROP_MIST_MAX, TOPENI_STROP_MIST_STEP, TOPENI_STROP_MIST_DEFAULT,
        "\u00b0C", "mdi:home-thermometer", topeni.set_strop_mist))
    entities.append(FveNumber(topeni, entry, TOPENI_CIDLO_KEY, "Topeni cidlo timeout",
        TOPENI_CIDLO_MIN, TOPENI_CIDLO_MAX, TOPENI_CIDLO_STEP, TOPENI_CIDLO_DEFAULT,
        "min", "mdi:timer-alert-outline", topeni.set_cidlo_timeout))
    # -> number.mar_pozadovana_mistnost: SKUTEČNÁ požadovaná (40001 − posun +1 °C)
    entities.append(PozadovanaMistnostNumber(coordinator, data["pokojovka"], entry))
    # -> number.mar_r_pauza_nad / number.mar_r_start_pod (pásmo R-Ekvitermy, °C od cíle)
    radiatory = data["radiatory"]
    entities.append(FveNumber(radiatory, entry, R_PAUZA_KEY, "R pauza nad",
        R_PASMO_MIN, R_PASMO_MAX, R_PASMO_STEP, R_PAUZA_DEFAULT,
        "\u00b0C", "mdi:arrow-collapse-up", radiatory.set_pauza))
    entities.append(FveNumber(radiatory, entry, R_START_KEY, "R start pod",
        R_PASMO_MIN, R_PASMO_MAX, R_PASMO_STEP, R_START_DEFAULT,
        "\u00b0C", "mdi:arrow-collapse-down", radiatory.set_start))
    async_add_entities(entities)


class PozadovanaMistnostNumber(NumberEntity):
    """Požadovaná teplota místnosti tak, jak ji chce uživatel.

    Bez volby „Sekundární čerpadlo +1 °C“ je totožná s 40001. S volbou ukazuje
    40001 − 1 a zápis sem jde do 40001 + 1 (přes správce pokojovky, jediného
    vlastníka 40001). Všude v MaR (dlaždice, schéma, grafy, Plánovač) se ukazuje
    tahle entita, takže uživatel v MaR vidí svoje číslo. Nic se neukládá – hodnota
    se vždy počítá z registru."""

    _attr_has_entity_name = True
    _attr_name = "Pozadovana mistnost"
    _attr_icon = "mdi:home-thermometer"
    _attr_native_unit_of_measurement = "\u00b0C"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = ROOM_SET_MIN
    _attr_native_max_value = ROOM_SET_MAX

    def __init__(self, coordinator: MarCoordinator, pokojovka, entry) -> None:
        self.coordinator = coordinator
        self._pokojovka = pokojovka
        self._attr_unique_id = f"{entry.entry_id}_{POZADOVANA_MISTNOST_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(self.hass, [ACOND_ROOM_SET], self._zmena)
        )
        # posun se mění v coordinatoru (room_posun) -> překresli po každém cyklu
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @callback
    def _zmena(self, _event) -> None:  # noqa: ANN001
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.coordinator.room_set() is not None

    @property
    def native_value(self) -> float | None:
        return self.coordinator.room_set()

    @property
    def native_step(self) -> float:
        # krok převezmi od entity Acondu, ať se nedá zadat, co Acond neumí
        st = self.hass.states.get(ACOND_ROOM_SET) if self.hass else None
        try:
            return float(st.attributes.get("step")) if st is not None else POZADOVANA_MISTNOST_STEP
        except (TypeError, ValueError):
            return POZADOVANA_MISTNOST_STEP

    @property
    def extra_state_attributes(self) -> dict:
        return {"posun": self.coordinator.room_posun}

    async def async_set_native_value(self, value: float) -> None:
        await self._pokojovka.async_set_skutecna(value)


class _BaseNumber(RestoreNumber):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: MarCoordinator, entry, key: str, name: str, default: float) -> None:
        self.coordinator = coordinator
        self._key = key
        self._default = default
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_value = default
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._push()
        # po obnově uložené hodnoty si vynuť přepočet — jinak by return_base/model
        # po restartu visely na defaultech, než dorazí další plánovaný cyklus.
        # (víc entit -> debounce sloučí do jednoho refreshe)
        await self.coordinator.async_request_refresh()

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._push()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _push(self) -> None:  # přepsáno v potomcích
        raise NotImplementedError


class HoursNumber(_BaseNumber):
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "h"

    @property
    def native_value(self):
        # hodiny vždy celé – i kdyby se z minula obnovila desetinná hodnota
        v = self._attr_native_value
        return None if v is None else int(round(v))

    def __init__(self, coordinator, entry, key, name, default, min_value, max_value) -> None:
        super().__init__(coordinator, entry, key, name, default)
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value

    def _push(self) -> None:
        if self._key == NUMBER_PAST_HOURS:
            self.coordinator.set_hours(past=int(self._attr_native_value))
        else:
            self.coordinator.set_hours(future=int(self._attr_native_value))


class CoefNumber(_BaseNumber):
    # Strop 6 (bylo 3): radiátorová soustava potřebuje na stejný přírůstek výkonu
    # asi dvojnásobný zdvih vody než podlahovka (radiátor 45/20 má spád 25 K,
    # podlahovka 30/20 jen 10 K), a vyšší hlava zvládne i špatně nastavenou
    # křivku. Nula zůstává = korekce vypnutá (nutné při ladění offsetu/strmosti
    # a u instalace bez důvěryhodného čidla v místnosti).
    _attr_native_min_value = 0
    _attr_native_max_value = 6
    _attr_native_step = 0.1

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, NUMBER_COEF, "Koeficient korekce", DEFAULT_COEF)

    def _push(self) -> None:
        self.coordinator.set_coef(float(self._attr_native_value))


class CurvePointNumber(_BaseNumber):
    """Y (zpátečka) jednoho bodu křivky při pevné venkovní teplotě."""

    _attr_native_min_value = MIN_RETURN
    _attr_native_max_value = MAX_RETURN
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "°C"

    def __init__(self, coordinator, entry, index: int, key: str, name: str, default: float) -> None:
        super().__init__(coordinator, entry, key, name, default)
        self._index = index

    def _push(self) -> None:
        self.coordinator.set_curve_point(self._index, float(self._attr_native_value))


class ZebraIntervalNumber(RestoreNumber):
    """Interval Zebry (topení / pauza) v minutách. Pole 30–240, krok 5.

    Nezávislý na coordinatoru – hodnotu tlačí přímo do ZebraControlleru přes
    předaný setter. Po restartu obnoví uloženou hodnotu a hned ji do controlleru
    protlačí, aby ji měl k dispozici dřív, než controller obnoví běžící cyklus."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = ZEBRA_MIN
    _attr_native_max_value = ZEBRA_MAX
    _attr_native_step = ZEBRA_STEP
    _attr_native_unit_of_measurement = "min"

    def __init__(self, zebra, entry: ConfigEntry, key: str, name: str,
                 default: float, setter) -> None:
        self._zebra = zebra
        self._key = key
        self._setter = setter
        self._attr_name = name
        self._attr_native_value = default
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._setter(float(self._attr_native_value))

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._setter(float(value))
        self.async_write_ha_state()


class OknaDeltaNumber(RestoreNumber):
    """Delta jednoho okna „Okna +/- teploty" (°C). Pole ⟨−3 … +3⟩, krok 0,5.
    Záporná = pokles, kladná = přednatopení. Nezávislá na coordinatoru – hodnotu
    tlačí přímo do OknaTeplotyControlleru. Po restartu obnoví uloženou hodnotu a
    hned ji do controlleru protlačí, aby ji měl dřív, než po startu zreconciluje."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = OKNA_DELTA_MIN
    _attr_native_max_value = OKNA_DELTA_MAX
    _attr_native_step = OKNA_DELTA_STEP
    _attr_native_unit_of_measurement = "°C"
    _attr_icon = "mdi:thermometer-plus"

    def __init__(self, okna, entry: ConfigEntry, index: int, key: str, name: str) -> None:
        self._okna = okna
        self._index = index
        self._key = key
        self._attr_name = name
        self._attr_native_value = float(OKNA_DELTA_DEFAULT)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._okna.set_delta(self._index, float(self._attr_native_value))

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._okna.set_delta(self._index, float(value))
        self.async_write_ha_state()
        # živá změna delty se projeví hned (re-apply base+delta, jsme-li v okně)
        await self._okna.async_apply_config()


class DovolenaTargetNumber(RestoreNumber):
    """Absolutní cíl dovolené (teplota místnosti nebo TUV, °C). Nezávislá na
    coordinatoru – hodnotu tlačí přímo do DovolenaControlleru. Po restartu obnoví
    uloženou hodnotu a hned ji protlačí."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "°C"

    def __init__(self, dovolena, entry: ConfigEntry, key: str, name: str,
                 vmin: float, vmax: float, step: float, default: float,
                 icon: str, setter) -> None:
        self._dovolena = dovolena
        self._setter = setter
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_min_value = vmin
        self._attr_native_max_value = vmax
        self._attr_native_step = step
        self._attr_native_value = float(default)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._setter(float(self._attr_native_value))

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._setter(float(value))
        self.async_write_ha_state()
        await self._dovolena.async_apply_config()


class FveNumber(RestoreNumber):
    """Parametr FVE-boostu (prahy přetoku ve W, práh baterie v %). Nezávislý na
    coordinatoru – hodnotu tlačí přímo do FveControlleru. Po restartu obnoví
    uloženou hodnotu a hned ji protlačí; živá změna spustí přepočet."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(self, fve, entry: ConfigEntry, key: str, name: str,
                 vmin: float, vmax: float, step: float, default: float,
                 unit: str, icon: str, setter) -> None:
        self._fve = fve
        self._setter = setter
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_min_value = vmin
        self._attr_native_max_value = vmax
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_native_value = float(default)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._setter(float(self._attr_native_value))

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._setter(float(value))
        self.async_write_ha_state()
        await self._fve.async_apply_config()
