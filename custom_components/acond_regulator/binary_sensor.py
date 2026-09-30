"""Binary sensor MaR – validita oken Den/noc (start != stop).

Slouží k progresivnímu odkrývání oken v dashboardu: okno N+1 se ukáže, teprve
když je okno N platné (`binary_sensor.mar_dennoc_okno{N}` == on). Řešeno přes
binary_sensor schválně – conditional karta u Pavla umí spolehlivě jen `state`
match (card_mod šablony na kartách/tlačítkách nevyhodnotí), a porovnat dvě time
entity (start != stop) v jedné conditional podmínce nejde. Tenhle senzor ten
rozdíl slehne do jednoho on/off stavu.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DENNOC_MAX_WINDOWS,
    DENNOC_VALID,
    DOMAIN,
    FVE_AKTIVNI_KEY,
    MA_FVE_KEY,
    OKNA_MAX_WINDOWS,
    OKNA_VALID_KEY,
    PROFIL_KOLIZE_KEY,
    device_info,
    signal_dennoc_updated,
    signal_fve_updated,
    signal_topeni_updated,
    signal_okna_updated,
    signal_profily_updated,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    dennoc = data["dennoc"]
    okna = data["okna"]
    async_add_entities(
        [DenNocWindowValid(dennoc, entry, i) for i in range(DENNOC_MAX_WINDOWS)]
        + [OknaTeplotyWindowValid(okna, entry, i) for i in range(OKNA_MAX_WINDOWS)]
        + [FveAktivni(data["fve"], data["topeni"], entry)]
        + [MaFve(data["fve"], entry)]
        + [ProfilKolize(data["profily"], entry)]
    )


class DenNocWindowValid(BinarySensorEntity):
    """on == okno N je platné (obě meze nastavené a různé)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-check-outline"
    _attr_entity_registry_enabled_default = True

    def __init__(self, dennoc, entry: ConfigEntry, index: int) -> None:
        self._dennoc = dennoc
        self._index = index
        n = index + 1
        # -> binary_sensor.mar_dennoc_okno_{n}_nastaveno
        self._attr_name = f"Dennoc okno {n} nastaveno"
        self._attr_unique_id = f"{entry.entry_id}_{DENNOC_VALID.format(n=n)}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_dennoc_updated(self._dennoc.entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._dennoc.window_valid(self._index)


class OknaTeplotyWindowValid(BinarySensorEntity):
    """on == okno N režimu „Okna +/- teploty" je platné (obě meze různé).
    Řídí progresivní odkrývání oken v dashboardu.
    -> binary_sensor.mar_okna_teploty_{n}_nastaveno."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-check-outline"
    _attr_entity_registry_enabled_default = True

    def __init__(self, okna, entry: ConfigEntry, index: int) -> None:
        self._okna = okna
        self._index = index
        n = index + 1
        self._attr_name = f"Okna teploty {n} nastaveno"
        self._attr_unique_id = f"{entry.entry_id}_{OKNA_VALID_KEY.format(n=n)}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_okna_updated(self._okna.entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._okna.window_valid(self._index)


class FveAktivni(BinarySensorEntity):
    """on == běží nějaký FV program (OR všech). Teď jen FV-boost TUV (master zapnutý);
    při přidání dalšího FV programu ho sem MUSÍŠ zahrnout, jinak ho guard Dovolené
    přehlédne. Čte guard DovolenaController.can_enable().
    -> binary_sensor.mar_fve_aktivni."""

    _attr_has_entity_name = True
    _attr_name = "Fve aktivni"
    _attr_icon = "mdi:solar-power-variant"
    _attr_entity_registry_enabled_default = True

    def __init__(self, fve, topeni, entry: ConfigEntry) -> None:
        self._fve = fve
        self._topeni = topeni
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{FVE_AKTIVNI_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_fve_updated(self._entry.entry_id), self._refresh
            )
        )
        # OR-brána: topení podle přebytků je taky FV program -> překresli i na jeho signál
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_topeni_updated(self._entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        # OR všech FV programů: FV-boost TUV NEBO topení podle přebytků
        return self._fve.active or self._topeni.active


class ProfilKolize(BinarySensorEntity):
    """on == pod zadaným jménem už profil existuje.

    Blokující popup „opravdu přepsat?" z integrace vyrobit nejde. Náhradou je
    tenhle senzor: karta podle něj skryje „Uložit" a odkryje červené
    „Přepsat …". Nebezpečné tlačítko tedy existuje jen ve chvíli, kdy je
    nebezpečné, a jmenuje se podle toho, co udělá.

    -> binary_sensor.mar_profil_kolize"""

    _attr_has_entity_name = True
    _attr_name = "Profil kolize"
    _attr_icon = "mdi:file-alert-outline"
    _attr_should_poll = False

    def __init__(self, profily, entry: ConfigEntry) -> None:
        self._profily = profily
        self._attr_unique_id = f"{entry.entry_id}_{PROFIL_KOLIZE_KEY}"
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
    def is_on(self) -> bool:
        return self._profily.kolize


class MaFve(BinarySensorEntity):
    """on == uživatel má fotovoltaiku -> binary_sensor.mar_ma_fve.

    Neptá se uživatele, odvozuje: má-li vyplněný aspoň jeden zdroj FVE
    (přetok / výroba / baterie), fotovoltaiku má. Schéma podle toho vybere
    podklad — s panely, střídačem a baterií, nebo bez nich.

    Rozdíl proti `mar_fve_aktivni`: ten říká, jestli PRÁVĚ TEĎ běží nějaký
    FV program. Tenhle říká, jestli FVE VŮBEC EXISTUJE. Do schématu patří
    ten druhý — podklad se nesmí měnit podle toho, jestli zrovna svítí.
    """

    _attr_has_entity_name = True
    _attr_name = "Ma fve"
    _attr_icon = "mdi:solar-panel"

    def __init__(self, fve, entry: ConfigEntry) -> None:
        self._fve = fve
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{MA_FVE_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_fve_updated(self._entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return any((
            getattr(self._fve, "_src_pretok", ""),
            getattr(self._fve, "_src_vyroba", ""),
            getattr(self._fve, "_src_baterie", ""),
        ))
