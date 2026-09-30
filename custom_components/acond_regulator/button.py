"""Tlačítka MaR – kameny START/STOP a snímek statistiky.

Tenká: stisk jen spustí sekvenci na pozadí. Potvrzení „Opravdu?" žije na
dashboardové kartě (tap_action.confirmation), ne tady – button entita nativně
confirmation neumí a nechceme cizí závislosti. Sekvence vždy doběhne celá,
druhý stisk během běhu se ignoruje (mode single v SequenceRunner).
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
    device_info,
    signal_stats_snapshot,
    signal_stats_snapshot_days,
)
from .image import async_save_snapshot
from .sequences import SequenceRunner
from .statistics.accumulator import StatisticsAccumulator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    seq: SequenceRunner = data["seq"]
    stats: StatisticsAccumulator = data["stats"]
    async_add_entities(
        [
            ZapnoutJisteButton(seq, entry),
            VypnoutSetrneButton(seq, entry),
            StatistikaObnovitButton(hass, entry, stats),
            StatistikaDnyButton(hass, entry, stats),
            # Profily: potvrzení „Opravdu?" žije na kartě (tap_action.confirmation),
            # stejně jako u kamenů. Uložit vs. Přepsat jsou DVĚ tlačítka schválně —
            # nebezpečné se na kartě odkryje jen při kolizi jména.
            ProfilButton(entry, "Profil ulozit", "mdi:content-save-outline",
                         data["profily"].async_ulozit),
            ProfilButton(entry, "Profil prepsat", "mdi:content-save-alert-outline",
                         lambda: data["profily"].async_ulozit(prepsat=True)),
            ProfilButton(entry, "Profil nacist", "mdi:tray-arrow-down",
                         data["profily"].async_nacist),
            ProfilButton(entry, "Profil smazat", "mdi:trash-can-outline",
                         data["profily"].async_smazat),
            # ruční obnova seznamu: kdo nakopíruje soubor přes SFTP, nemusí
            # čekat na pollování selectu (30 s) ani na restart HA
            ProfilButton(entry, "Profil obnovit seznam", "mdi:folder-refresh-outline",
                         lambda: data["profily"].async_refresh(force=True)),
        ]
    )


class _BaseButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, seq: SequenceRunner, entry: ConfigEntry, key: str, name: str) -> None:
        self._seq = seq
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)


class ZapnoutJisteButton(_BaseButton):
    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, seq, entry) -> None:
        super().__init__(seq, entry, "zapnout_jiste", "Zapnout jistě")

    async def async_press(self) -> None:
        self._seq.fire_start()


class VypnoutSetrneButton(_BaseButton):
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, seq, entry) -> None:
        super().__init__(seq, entry, "vypnout_setrne", "Vypnout šetrně")

    async def async_press(self) -> None:
        self._seq.fire_stop()


class StatistikaObnovitButton(ButtonEntity):
    """Vyrobí nový snímek tabulky statistiky – na obrazovku i na disk.

    Dvě věci naráz, protože jsou to dvě podoby jednoho snímku:

    1. Uloží PNG do `config/www/mar/` pod datovaným jménem. Odtud ho HA
       servíruje na `/local/mar/…` a teprve tam jde v prohlížeči normálně
       uložit. Nad image entitou to HA nepustí ani na PC, ani na tabletu.
    2. Pošle signál, kterým image entita posune `image_last_updated` a spolu
       s ním i adresu uloženého souboru (atribut `snimek_url` pro dashboard).

    Čísla se berou v okamžiku stisku, takže snímek nemůže být zastaralý.
    Kreslí se dvakrát (jednou do souboru, podruhé až si frontend vyžádá
    obrázek) – vědomě: sdílená cache mezi platformami by za pár desítek
    milisekund nestála.

    Uložení do galerie za uživatele HA udělat NEMŮŽE. Poslední krok je vždycky
    jeho: pravé tlačítko / podržení prstu nad otevřeným obrázkem.
    """

    _attr_has_entity_name = True
    _attr_name = "Statistika obnovit"
    _attr_icon = "mdi:camera-outline"

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, stats: StatisticsAccumulator
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._stats = stats
        self._attr_unique_id = f"{entry.entry_id}_statistika_obnovit"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_press(self) -> None:
        # None = zápis selhal (práva, plný disk). Entita si drží starou adresu
        # a snímek na obrazovce funguje dál – tlačítko nesmí spadnout kvůli
        # souboru, kterým se jen sdílí.
        url = await async_save_snapshot(self._hass, self._stats)
        async_dispatcher_send(
            self._hass, signal_stats_snapshot(self._entry.entry_id), url
        )


class StatistikaDnyButton(ButtonEntity):
    """Vyrobí snímek s denním rozpadem: dnešek + sedm zavřených dnů.

    Stejně jako u sloupcové tabulky dvě věci naráz: uloží soubor do
    `config/www/mar/` (vlastní archiv, aby se s druhým snímkem nepřepisovaly)
    a pošle signál, kterým se překreslí obrázek na dashboardu.
    """

    _attr_has_entity_name = True
    _attr_name = "Statistika po dnech"
    _attr_icon = "mdi:table-large"

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, stats: StatisticsAccumulator
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._stats = stats
        self._url: str | None = None
        self._attr_unique_id = f"{entry.entry_id}_statistika_dny"
        self._attr_device_info = device_info(entry.entry_id)

    @property
    def extra_state_attributes(self) -> dict:
        return {"snimek_url": self._url}

    async def async_press(self) -> None:
        url = await async_save_snapshot(self._hass, self._stats, days=True)
        if url:
            self._url = url
        self.async_write_ha_state()
        async_dispatcher_send(
            self._hass, signal_stats_snapshot_days(self._entry.entry_id), url
        )


class ProfilButton(ButtonEntity):
    """Jedna operace s profilem. Tenká: stisk jen zavolá správce, který si
    sám ohlásí výsledek do `sensor.mar_profil_stav`. Nikdy nevyhazuje —
    chyba se hlásí textem, ne červenou lištou, protože uživatel u profilů
    potřebuje vědět CO se nestalo, ne jen že se to nestalo."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, name: str, icon: str, akce) -> None:
        self._akce = akce
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{name.lower().replace(' ', '_')}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_press(self) -> None:
        await self._akce()
