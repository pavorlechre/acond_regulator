"""Vypínač zápisu do TČ (zápis vypočtené zpátečky do registru 40008)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry

try:  # HA 2023.12+
    from homeassistant.exceptions import ServiceValidationError as _OdmitnutoError
except ImportError:  # pragma: no cover – starší HA
    from homeassistant.exceptions import HomeAssistantError as _OdmitnutoError
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    FVE_MASTER_KEY,
    PROFIL_NACIST_FVE_KEY,
    PROFIL_ULOZIT_FVE_KEY,
    TOPENI_MASTER_KEY,
    TOPENI_BATT_DISPOS_KEY,
    TOPENI_HLIDAC_KEY,
    STRATEGY_BEZ_MAR,
    STRATEGY_EKVITERM,
    VRSTVA_VYKON_KEY,
    device_info,
    signal_dennoc_updated,
    signal_dovolena_updated,
    signal_fve_updated,
    signal_topeni_updated,
    signal_okna_updated,
    signal_profily_updated,
    signal_vrstva_vykon,
    signal_zebra_updated,
)
from .coordinator import MarCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: MarCoordinator = data["coordinator"]
    async_add_entities(
        [
            WriteSwitch(coordinator, entry),
            ZebraSwitch(data["zebra"], entry),
            DenNocSwitch(data["dennoc"], entry),
            OknaTeplotySwitch(data["okna"], data["dovolena"], entry),
            DovolenaSwitch(data["dovolena"], entry),
            FveMasterSwitch(data["fve"], data["dovolena"], entry),
            TopeniMasterSwitch(data["topeni"], data["dovolena"], entry),
            TopeniBattSignSwitch(data["topeni"], entry),
            TopeniHlidacSwitch(data["topeni"], entry),
            # dva přepínače „včetně FVE" – VĚDOMĚ oddělené (viz docstring)
            ProfilFveSwitch(data["profily"], entry, PROFIL_ULOZIT_FVE_KEY,
                            "Profil ulozit fve", data["profily"].set_ulozit_fve),
            ProfilFveSwitch(data["profily"], entry, PROFIL_NACIST_FVE_KEY,
                            "Profil nacist fve", data["profily"].set_nacist_fve),
            VrstvaVykonSwitch(hass, entry),
        ]
    )


class WriteSwitch(RestoreEntity, SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Zápis zpátečky"
    _attr_icon = "mdi:pencil"

    def __init__(self, coordinator: MarCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_zapis"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self.coordinator.set_write_enabled(last.state == "on")
        # živé zrcadlo: když write_enabled změní select, switch to ukáže
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        return self.coordinator.write_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.set_write_enabled(True)
        # zapnout pauzu -> obnovit reálnou strategii (z Bez MaR zpět na Ekvitermu)
        if self.coordinator.strategy == STRATEGY_BEZ_MAR:
            self.coordinator.set_strategy(STRATEGY_EKVITERM)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.set_write_enabled(False)
        self.async_write_ha_state()


class ZebraSwitch(SwitchEntity):
    """Překryv Zebra (topení s pauzami). Stav i časovač vlastní ZebraController
    (Store), tenhle switch je jen jeho živé zrcadlo – proto ne RestoreEntity."""

    _attr_has_entity_name = True
    _attr_name = "Zebra"
    _attr_icon = "mdi:sine-wave"

    def __init__(self, zebra, entry: ConfigEntry) -> None:
        self._zebra = zebra
        self._attr_unique_id = f"{entry.entry_id}_zebra"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # controller po obnově (async_resume_if_needed) pošle signál -> překreslíme
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_zebra_updated(self._zebra.entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._zebra.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._zebra.async_enable()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._zebra.async_disable(resume_heating=True)
        self.async_write_ha_state()


class DenNocSwitch(RestoreEntity, SwitchEntity):
    """Master přepínač časového režimu Den/noc.

    Stav se PERSISTUJE tady (RestoreEntity) – controller je bezstavový, master
    on/off je jeho jediný trvalý vstup. Po obnově se protlačí do controlleru,
    který podle toho po startu (ne)zreconciluje železo. Časy oken drží time
    entity, platnost oken binary_sensory."""

    _attr_has_entity_name = True
    _attr_name = "Dennoc"           # -> switch.mar_dennoc (dashboard label „Den/noc")
    _attr_icon = "mdi:theme-light-dark"

    def __init__(self, dennoc, entry: ConfigEntry) -> None:
        self._dennoc = dennoc
        self._attr_unique_id = f"{entry.entry_id}_dennoc"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            # jen nastav interní příznak; reconcile udělá __init__ po složení všech mozků
            self._dennoc.enabled = last.state == "on"
        # živé překreslení při změně (uspání gate / hrana)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_dennoc_updated(self._dennoc.entry.entry_id),
                self._refresh,
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._dennoc.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._dennoc.async_set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._dennoc.async_set_enabled(False)
        self.async_write_ha_state()


class OknaTeplotySwitch(RestoreEntity, SwitchEntity):
    """Master přepínač časového režimu „Okna +/- teploty" (osa B).

    Stav se PERSISTUJE tady (RestoreEntity) – master on/off je jediný trvalý
    vstup, který se neodvodí z časů. Po obnově se protlačí do controlleru, který
    podle něj po startu (ne)zreconciluje výpůjčku 40001. Časy oken drží time
    entity, delty number entity, platnost oken binary_sensory.

    -> switch.mar_okna_teploty (dashboard label „Okna +/- teploty")."""

    _attr_has_entity_name = True
    _attr_name = "Okna teploty"
    _attr_icon = "mdi:swap-vertical-bold"

    def __init__(self, okna, dovolena, entry: ConfigEntry) -> None:
        self._okna = okna
        self._dovolena = dovolena
        self._attr_unique_id = f"{entry.entry_id}_okna_teploty"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            # jen nastav interní příznak; reconcile udělá __init__ po složení mozků
            self._okna.enabled = last.state == "on"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_okna_updated(self._okna.entry.entry_id),
                self._refresh,
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._okna.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        # reverzní guard: když běží Dovolená, Okna zapnout nelze (dva executory na 40001)
        if self._dovolena.enabled:
            _blok_dovolena("Okna +/− teploty")
            return
        await self._okna.async_set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._okna.async_set_enabled(False)
        self.async_write_ha_state()


class DovolenaSwitch(RestoreEntity, SwitchEntity):
    """Master přepínač režimu „Dovolená" (osa B).

    Zapnutí je HLÍDANÉ vstupním guardem (`can_enable`): jde jen když strategie ==
    Ekviterma, Okna +/- teploty jsou vyplá a interval je platný (od < do). Jinak se
    zapnutí zamítne + notifikace. Stav se persistuje (RestoreEntity); interval drží
    datetime entity, cíle number entity.

    -> switch.mar_dovolena (dashboard label „Dovolená")."""

    _attr_has_entity_name = True
    _attr_name = "Dovolena"
    _attr_icon = "mdi:beach"

    def __init__(self, dovolena, entry: ConfigEntry) -> None:
        self._dovolena = dovolena
        self._attr_unique_id = f"{entry.entry_id}_dovolena"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._dovolena.enabled = last.state == "on"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_dovolena_updated(self._dovolena.entry.entry_id),
                self._refresh,
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._dovolena.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        ok, reason = self._dovolena.can_enable()
        if not ok:
            _odmitni(f"Dovolenou teď nelze zapnout. {reason}")
        await self._dovolena.async_set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._dovolena.async_set_enabled(False)
        self.async_write_ha_state()


def _odmitni(zprava: str) -> None:
    """Odmítnutí, které je VIDĚT.

    Dřív šlo do `persistent_notification`, tedy do zvonečku, kterého si nikdo
    nevšimne – přepínač jen skočil zpátky bez vysvětlení. Chyba služby se v HA
    ukáže jako červená lišta PŘÍMO TAM, kde uživatel klikl, a chytí i zapnutí
    z dialogu entity, hlasem nebo automatizací.

    MaR přitom nikdy nic nezapíná ani nevypíná za uživatele – jen odmítne a řekne
    proč; po návratu z dovolené si program zapne sám."""
    raise _OdmitnutoError(zprava)


def _blok_dovolena(program: str) -> None:
    _odmitni(f"Běží Dovolená — {program} nelze zapnout. Nejdřív ukonči Dovolenou.")


class FveMasterSwitch(RestoreEntity, SwitchEntity):
    """Master „Ohřev TUV z přebytku FVE" (osa B, executor na 40005).

    Stav se PERSISTUJE tady (RestoreEntity) – master on/off je jediný trvalý vstup.
    Po obnově se protlačí do controlleru (jen příznak); reconcile udělá controller
    v async_resume_if_needed po složení všech mozků. Prahy drží number entity,
    zdroje text entity, hold vlastní switch.

    -> switch.mar_fve_tuv (dashboard label „Ohřev z přebytku FVE")."""

    _attr_has_entity_name = True
    _attr_name = "Fve tuv"
    _attr_icon = "mdi:water-boiler"

    def __init__(self, fve, dovolena, entry: ConfigEntry) -> None:
        self._fve = fve
        self._dovolena = dovolena      # zpětný zámek (dva executory nad 40005)
        self._attr_unique_id = f"{entry.entry_id}_{FVE_MASTER_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            # jen příznak; evaluate udělá controller po resume
            self._fve.enabled = last.state == "on"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_fve_updated(self._fve.entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._fve.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        # reverzní guard: když běží Dovolená, FVE TUV zapnout nelze (dva executory na 40005)
        if self._dovolena.enabled:
            _blok_dovolena("FVE TUV")
        await self._fve.async_set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._fve.async_set_enabled(False)
        self.async_write_ha_state()


class TopeniMasterSwitch(RestoreEntity, SwitchEntity):
    """Master „Topení podle přebytků" (osa A, executor 40014 + návnada 40008).

    Zapnutí je HLÍDANÉ vstupním guardem (`can_enable`): jde jen ve strategii
    Ekviterma + Typ regulace Standard (sahá na zpátečku). Jinak se zapnutí zamítne +
    notifikace. Stav se persistuje (RestoreEntity); parametry drží number entity,
    zdroj výkonu baterie text entity. Půjčku 40014 vrací controller.

    -> switch.mar_topeni (dashboard label „Topení podle přebytků")."""

    _attr_has_entity_name = True
    _attr_name = "Topeni"
    _attr_icon = "mdi:radiator"

    def __init__(self, topeni, dovolena, entry: ConfigEntry) -> None:
        self._topeni = topeni
        self._dovolena = dovolena      # zpětný zámek (dva executory nad 40001/40008)
        self._attr_unique_id = f"{entry.entry_id}_{TOPENI_MASTER_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._topeni.enabled = last.state == "on"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_topeni_updated(self._topeni.entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._topeni.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        # reverzní guard: když běží Dovolená, FVE Topení zapnout nelze
        if self._dovolena.enabled:
            _blok_dovolena("FVE Topení")
        ok, reason = self._topeni.can_enable()
        if not ok:
            _odmitni(f"FVE Topení teď nelze zapnout. {reason}")
        await self._topeni.async_set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._topeni.async_set_enabled(False)
        self.async_write_ha_state()


class TopeniBattSignSwitch(RestoreEntity, SwitchEntity):
    """Konvence znaménka zdroje výkonu baterie pro topení podle přebytků.

    ON  = zdroj hlásí VYBÍJENÍ jako kladné (GoodWe `sensor.battery_power`) – default.
    OFF = zdroj hlásí NABÍJENÍ jako kladné.
    Interně se vždy normalizuje na „nabíjení +", takže bilance = přetok + výkon
    baterie má správné znaménko (kladné = skutečný přebytek). Bez tohohle by se
    vybíjení počítalo jako přebytek (bilance kladná, i když baterka mizí).

    Stav se persistuje (RestoreEntity); default (bez uloženého stavu) = ON = GoodWe.

    -> switch.mar_topeni_baterie_vybijeni_kladne."""

    _attr_has_entity_name = True
    _attr_name = "Topeni baterie vybijeni kladne"
    _attr_icon = "mdi:plus-minus-variant"

    def __init__(self, topeni, entry: ConfigEntry) -> None:
        self._topeni = topeni
        self._attr_unique_id = f"{entry.entry_id}_{TOPENI_BATT_DISPOS_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        # default = ON (GoodWe: vybíjení kladné), když není uložený stav
        value = True if last is None else (last.state == "on")
        self._topeni.set_batt_dispos(value)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_topeni_updated(self._topeni.entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._topeni._batt_dispos

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._topeni.set_batt_dispos(True)
        self.async_write_ha_state()
        await self._topeni.async_apply_config()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._topeni.set_batt_dispos(False)
        self.async_write_ha_state()
        await self._topeni.async_apply_config()


class TopeniHlidacSwitch(RestoreEntity, SwitchEntity):
    """Vypínač hlídače restartu po předání ekvitermě (topení podle přebytků).

    ZAPNUTO (default) = po měkkém předání se nasadí hlídač: při proplachu okruhu
    (vypnutí primárního čerpadla) zkontroluje zpátečku proti ekvitermnímu cíli
    (křivka + korekce) a jednorázově nastartuje topení kamenem Topit.
    VYPNUTO = hlídač se nenasazuje (a případný nasazený se hned sundá); restart
    topení se nechá čistě na hysterezi stroje (naskočí sám ~2 °C pod ekvitermou –
    u slabu to trvá déle, ale někdo to tak chce, třeba přes noc bez Den/noc).

    Stav se persistuje (RestoreEntity); default bez uloženého stavu = ZAPNUTO.

    -> switch.mar_topeni_hlidac (dashboard label „Hlídač restartu").
    """

    _attr_has_entity_name = True
    _attr_name = "Topeni hlidac"
    _attr_icon = "mdi:restart-alert"

    def __init__(self, topeni, entry: ConfigEntry) -> None:
        self._topeni = topeni
        self._attr_unique_id = f"{entry.entry_id}_{TOPENI_HLIDAC_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        # default = ZAPNUTO (dosavadní chování), když není uložený stav
        value = True if last is None else (last.state == "on")
        self._topeni.set_hlidac(value)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_topeni_updated(self._topeni.entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._topeni.hlidac_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._topeni.set_hlidac(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._topeni.set_hlidac(False)
        self.async_write_ha_state()


class ProfilFveSwitch(RestoreEntity, SwitchEntity):
    """„Včetně FVE" – jeden přepínač u ukládání, druhý u načítání.

    Proč dva a ne jeden: jsou to různá rozhodnutí. Ukládat chceš typicky plnou
    sadu (záloha vlastního stroje), načítat naopak často jen ekvitermu — třeba
    když ti někdo pošle profil ze svého domu a jeho prahy přebytků nechceš.
    Jeden společný přepínač by tě nutil ho před každou akcí přehazovat.

    STAV DRŽÍ ENTITA, ne správce (0.12.1). Původně entita jen zrcadlila atribut
    správce přes `is_on` – jako to dělají mastery programů. U nich to funguje,
    tady se to na železe rozešlo: hodnota ve správci se nastavila (uložený
    soubor FVE opravdu obsahoval), ale zobrazený stav spadl zpátky na `off`.
    Tím se přepínač stal NEVYPNUTELNÝM: karta ukazovala `off`, takže další
    stisk poslal `turn_on` a sekce FVE se přidala vždycky.

    Náprava nespoléhá na to, že se najde přesný mechanismus rozejití: entita
    je JEDINÝ vlastník hodnoty (`_attr_is_on`) a do správce ji jen tlačí – tedy
    stejný vzor, jakým v MaR fungují všechny `number` entity (`FveNumber`,
    `ZebraIntervalNumber`). Zobrazení a zapsaná hodnota se pak nemají jak
    rozejít, protože je to jedno a totéž číslo. Navíc `should_poll = False`:
    hodnotu nemá odkud zjišťovat, mění ji jen uživatel.

    Sám nic neřídí: jen říká, jestli se sekce `fve` zapíše, resp. přečte.
    -> switch.mar_profil_ulozit_fve / switch.mar_profil_nacist_fve"""

    _attr_has_entity_name = True
    _attr_icon = "mdi:solar-power-variant-outline"
    _attr_should_poll = False

    def __init__(self, profily, entry: ConfigEntry, key: str, name: str,
                 setter) -> None:
        self._profily = profily
        self._setter = setter
        self._attr_name = name
        self._attr_is_on = False
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"
        # protlač obnovenou hodnotu do správce, ať se hned po startu shodují
        self._setter(self._attr_is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self._setter(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self._setter(False)
        self.async_write_ha_state()


class VrstvaVykonSwitch(RestoreEntity, SwitchEntity):
    """switch.mar_vrstva_vykon — ouško Výkon: vložený graf přes schéma.

    Čistě zobrazovací, do regulace nezasahuje. Zapíná a vypíná se klepnutím
    na ouško; na rozdíl od select.mar_vrstva se sám nevrací (graf je malý a
    schéma nezakrývá). Stav přežije restart. Stav se zrcadlí do hass.data,
    aby ho obrázek našel i když se přidá až po switchi.
    """

    _attr_has_entity_name = True
    _attr_name = "Vrstva výkon"
    _attr_icon = "mdi:chart-areaspline"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        self._is_on = False
        self._attr_unique_id = f"{entry.entry_id}_{VRSTVA_VYKON_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._is_on = last.state == "on"
        self._ohlas()

    @property
    def is_on(self) -> bool:
        return self._is_on

    @callback
    def _ohlas(self) -> None:
        self.hass.data.setdefault(DOMAIN, {}).setdefault(self._entry.entry_id, {})
        data = self.hass.data[DOMAIN][self._entry.entry_id]
        if isinstance(data, dict):
            data[VRSTVA_VYKON_KEY] = self._is_on
        async_dispatcher_send(self.hass, signal_vrstva_vykon(self._entry.entry_id), self._is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        self.async_write_ha_state()
        self._ohlas()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        self.async_write_ha_state()
        self._ohlas()
