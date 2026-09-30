"""select.mar_strategie – Patro 1: způsob dodávky tepla (exkluzivní).

Ekviterma · Stálý výkon · Minimum · BOOST · Bez MaR.

Vazba na zápis (přátelské chování, jak si Pavle přál):
- výběr „Bez MaR" -> vypne zápis MaR (co se děje s topením je mimo MaR),
- výběr jakékoli regulační strategie -> zapne zápis.
- výběr BOOST / Bez MaR -> zhasne časové plány (Zebra ap.); potvrzení na kartě.
Switch „Zápis zpátečky" zůstává živým zrcadlem write_enabled a lze jím dál rychle
pauznout bez ztráty vybrané strategie. Vybraná strategie přežije restart (RestoreEntity);
při obnově se nastaví jen strategie, ne write_enabled (ať zůstane bezpečný default
„po startu nezapisovat", který drží switch).
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    DOMAIN,
    OFFMODE_DEFAULT,
    OFFMODE_OPTIONS,
    OFFMODE_PZ,
    STRATEGY_BEZ_MAR,
    STRATEGY_BOOST,
    STRATEGY_EKVITERM,
    STRATEGY_MINIMUM,
    STRATEGY_OPTIONS,
    PROFIL_NONE,
    PROFIL_SELECT_KEY,
    SCHEMA_SEKUNDAR_KEY,
    SEKUNDAR_DEFAULT,
    SEKUNDAR_OPTIONS,
    TCMODE_DEFAULT,
    TCMODE_OPTIONS,
    device_info,
    signal_profily_updated,
)
from .coordinator import MarCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            StrategySelect(data["coordinator"], data["min"], data["zebra"], data["dennoc"], data["dovolena"], data["topeni"], entry),
            OffModeSelect(data["coordinator"], data["seq"], data["dennoc"], entry),
            TcModeSelect(data["seq"], entry),
            ProfilSelect(data["profily"], entry),
            SchemaSekundarSelect(entry),
        ]
    )


class StrategySelect(RestoreEntity, SelectEntity):
    _attr_has_entity_name = True
    _attr_name = "Strategie"
    _attr_icon = "mdi:tune-variant"
    _attr_options = STRATEGY_OPTIONS

    def __init__(self, coordinator: MarCoordinator, minimum, zebra, dennoc, dovolena,
                 topeni, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self.minimum = minimum
        self.zebra = zebra
        self.dennoc = dennoc
        self.dovolena = dovolena
        self.topeni = topeni
        self._attr_unique_id = f"{entry.entry_id}_strategie"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in STRATEGY_OPTIONS:
            # obnov jen strategii; write_enabled řídí switch (bezpečný default po startu).
            # Smyčku Minima po restartu naskočí __init__ přes async_resume_if_needed.
            self.coordinator.set_strategy(last.state)
        # překresli, když se strategie změní odjinud (např. budoucí překryvy)
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def current_option(self) -> str:
        return self.coordinator.strategy

    async def async_select_option(self, option: str) -> None:
        prev = self.coordinator.strategy
        # reverzní guard: běží-li Dovolená, nelze pryč z Ekvitermy (dovolená drží 40001)
        if self.dovolena.enabled and option != STRATEGY_EKVITERM:
            from homeassistant.components import persistent_notification

            persistent_notification.async_create(
                self.hass,
                "Běží Dovolená — strategii nelze měnit. Nejdřív ukonči Dovolenou.",
                title="MaR – Dovolená běží",
                notification_id="mar_strategie_blocked_dovolena",
            )
            self.async_write_ha_state()  # UI se vrátí na Ekviterma (může krátce probliknout)
            return
        # Opouštíme Ekvitermu -> topení podle přebytků MUSÍ vrátit 40014 a uvolnit
        # blokaci 40008 DŘÍV, než sliderová strategie sáhne na registry (jinak by
        # Minimum uložilo naši návnadu/slider jako „orig"). Master topení zůstane
        # zapnutý (dřímá), po návratu do Ekvitermy zase převezme na hranici SoC.
        if option != STRATEGY_EKVITERM and prev == STRATEGY_EKVITERM:
            await self.topeni.async_yield_control()

        slider_strats = (STRATEGY_MINIMUM, STRATEGY_BOOST)
        # odchod ze sliderové strategie (Minimum/BOOST) -> vrať původní registry
        if prev in slider_strats and option != prev:
            await self.minimum.async_deactivate()

        # BOOST/Bez MaR nedávají s časovými plány smysl -> zhasni je.
        # Lidské potvrzení „Vypínám časové plány, pokračovat?" žije na kartě;
        # tady je pojistka v kódu, ať se plány zhasnou vždycky (i mimo kartu).
        # resume_heating=True: kdyby Zebra měla TČ v pauze, nenech uživatele studeného.
        if option in (STRATEGY_BOOST, STRATEGY_BEZ_MAR):
            await self.zebra.async_disable(resume_heating=True)

        self.coordinator.set_strategy(option)
        # Bez MaR = ruce pryč (vypni zápis); jiná strategie = zapni zápis
        self.coordinator.set_write_enabled(option != STRATEGY_BEZ_MAR)

        # vstup do sliderové strategie -> nastartuj (uloží orig, nastaví slider)
        if option == STRATEGY_MINIMUM and prev != STRATEGY_MINIMUM:
            await self.minimum.async_activate_minimum()
        elif option == STRATEGY_BOOST and prev != STRATEGY_BOOST:
            await self.minimum.async_activate_boost()

        # Den/noc NEvypínáme (rozvrh je trvalý). Jen ho nech přehodnotit gate:
        # pod BOOST/Bez MaR zdřímne (neřídí, jen drží časovač), po návratu ke
        # kompatibilní strategii zase aplikuje rozvrh (může srovnat železo kamenem).
        await self.dennoc.async_reconcile()

        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class OffModeSelect(RestoreEntity, SelectEntity):
    """select.mar_rezim_vypnuti – co znamená „netopit": VYP / Léto / Útlum PZ.

    Řídí, jak se zachová kámen „Netopit" (a Den/noc/Zebra přes něj):
    - VYP  = tvrdé vypnutí (bit_3), antizámraz jede (default, zpětná kompatibilita),
    - Léto = přepnout do letního režimu (TUV jede dál, dům se netopí),
    - Útlum PZ = žádná změna módu, jen držet zpátečku na pevné hodnotě.
    Volba přežije restart (RestoreEntity) a protlačí se do runneru. „Topit" cestu
    zpět uhodne ze železa, takže selector nemusí řešit.
    """

    _attr_has_entity_name = True
    _attr_name = "Režim vypínání"
    _attr_icon = "mdi:power-settings"
    _attr_options = OFFMODE_OPTIONS

    def __init__(self, coordinator, seq, dennoc, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self.seq = seq
        self.dennoc = dennoc
        self._value = OFFMODE_DEFAULT
        self._attr_unique_id = f"{entry.entry_id}_rezim_vypnuti"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in OFFMODE_OPTIONS:
            self._value = last.state
        self.seq.set_offmode(self._value)

    @property
    def current_option(self) -> str:
        return self._value

    async def async_select_option(self, option: str) -> None:
        if option not in OFFMODE_OPTIONS:
            return
        self._value = option
        self.seq.set_offmode(option)
        # opouštím Útlum PZ -> zruš držení zpátečky, ať nezůstane viset na 20
        if option != OFFMODE_PZ:
            self.coordinator.set_utlum(None)
        # sjednoť železo s novým režimem hned (jsme-li zrovna v off-okně Den/noc);
        # varování „nepřepínat za běhu" žije na kartě, tohle je self-heal pojistka
        await self.dennoc.async_reconcile()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class TcModeSelect(RestoreEntity, SelectEntity):
    """select.mar_rezim_tc – provozní režim TČ, který zapíná kámen Zapnout jistě.

    Pouze TČ / Automatický / Bivalence. Default Pouze TČ = dosavadní chování;
    kdo jede Automat nebo Bivalenci, nastaví si to tady jednou a kámen mu při
    startu topení nepřepne režim na cizí (dřív zapínal Pouze TČ natvrdo).
    Vypnout šetrně se netýká – VYP je VYP bez ohledu na provozní režim.
    Volba přežije restart (RestoreEntity) a protlačí se do runneru kamenů.
    """

    _attr_has_entity_name = True
    _attr_name = "Rezim tc"          # -> select.mar_rezim_tc (dashboard label „Provozní režim TČ")
    _attr_icon = "mdi:heat-pump-outline"
    _attr_options = TCMODE_OPTIONS

    def __init__(self, seq, entry: ConfigEntry) -> None:
        self.seq = seq
        self._value = TCMODE_DEFAULT
        self._attr_unique_id = f"{entry.entry_id}_rezim_tc"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in TCMODE_OPTIONS:
            self._value = last.state
        self.seq.set_tcmode(self._value)

    @property
    def current_option(self) -> str:
        return self._value

    async def async_select_option(self, option: str) -> None:
        if option not in TCMODE_OPTIONS:
            return
        self._value = option
        self.seq.set_tcmode(option)
        self.async_write_ha_state()


class ProfilSelect(RestoreEntity, SelectEntity):
    """select.mar_profil – které nastavení načíst / smazat.

    Seznam se plní ZA BĚHU z adresáře (přidáš soubor, po refreshi je
    v rozbalovátku). Vzory mají značku `[vzor] ` a jsou jen ke čtení.
    Placeholder „—" je v options vždycky: prázdný seznam options se v HA chová
    nepříjemně a taky je slušné začínat na „nic nevybráno", aby jedno omylné
    kliknutí na Načíst nepřepsalo nastavení.
    """

    _attr_has_entity_name = True
    _attr_name = "Profil"
    _attr_icon = "mdi:content-save-cog-outline"
    # JEDINÁ pollovaná entita MaR (30 s): jinak by se soubor nakopírovaný
    # ručně přes SFTP objevil v rozbalovátku až po restartu HA. Cena je jeden
    # `listdir` za půl minuty; `async_refresh` navíc dispatchuje jen při
    # SKUTEČNÉ změně seznamu, takže se stav entit nepřepisuje naprázdno.
    _attr_should_poll = True

    def __init__(self, profily, entry: ConfigEntry) -> None:
        self._profily = profily
        self._attr_unique_id = f"{entry.entry_id}_{PROFIL_SELECT_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        # obnovuje se jen jméno; jestli soubor pořád existuje, řeší refresh
        if last is not None and last.state not in ("unknown", "unavailable", ""):
            self._profily.vybrany = last.state
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
    def options(self) -> list[str]:
        return self._profily.options

    @property
    def current_option(self) -> str:
        # smazaný / zmizelý profil nesmí zůstat vybraný (HA by hlásil neplatný stav)
        return self._profily.vybrany if self._profily.vybrany in self.options else PROFIL_NONE

    async def async_update(self) -> None:
        await self._profily.async_refresh()

    async def async_select_option(self, option: str) -> None:
        self._profily.set_vybrany(option)
        self.async_write_ha_state()


class SchemaSekundarSelect(RestoreEntity, SelectEntity):
    """select.mar_schema_sekundar – kdy schéma rozjede sekundární okruh.

    Čistě zobrazovací volba, do regulace nezasahuje. Existuje proto, že chod
    sekundáru se z Modbusu odvodit nedá:
      • Podle čerpadla – standard, řídí TČ (bit_4)
      • Stále – čerpadlo trvale v zásuvce, TČ o něm neví
      • Podle primáru – instalace bez AKU, okruh kopíruje primární čerpadlo

    Default je standard, takže kdo nic nenastaví, vidí správné chování.
    Volba přežije restart (RestoreEntity).
    """

    _attr_has_entity_name = True
    _attr_name = "Schema sekundar"
    _attr_icon = "mdi:pump"
    _attr_options = SEKUNDAR_OPTIONS
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry) -> None:
        self._value = SEKUNDAR_DEFAULT
        self._attr_unique_id = f"{entry.entry_id}_{SCHEMA_SEKUNDAR_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in SEKUNDAR_OPTIONS:
            self._value = last.state

    @property
    def current_option(self) -> str:
        return self._value

    async def async_select_option(self, option: str) -> None:
        if option not in SEKUNDAR_OPTIONS:
            return
        self._value = option
        self.async_write_ha_state()
