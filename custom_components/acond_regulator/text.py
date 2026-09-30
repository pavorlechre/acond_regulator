"""Text entity MaR – zdrojové entity FVE (přetok / výroba / baterie).

Uživatel sem vloží `entity_id` svého střídače (nejlíp ze schránky). Předvyplněno
pro Goodwe; jiný střídač přepíše. Hodnota se protlačí do FveControlleru, který se
podle ní (od)hlásí ze sledování zdroje. Persistováno přes RestoreEntity – po
restartu se obnoví poslední vložené entity_id (ne default).

-> text.mar_fve_zdroj_pretok / _vyroba / _baterie
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    PROFIL_NAME_MAX,
    PROFIL_NAZEV_KEY,
    FVE_SRC_BATERIE_DEFAULT,
    FVE_SRC_BATERIE_KEY,
    FVE_SRC_PRETOK_DEFAULT,
    FVE_SRC_PRETOK_KEY,
    FVE_SRC_VYROBA_DEFAULT,
    FVE_SRC_VYROBA_KEY,
    TOPENI_SRC_BATT_VYKON_KEY,
    TOPENI_SRC_BATT_VYKON_DEFAULT,
    device_info,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    fve = data["fve"]
    topeni = data["topeni"]
    async_add_entities(
        [
            FveSourceText(fve, entry, FVE_SRC_PRETOK_KEY, "Fve zdroj pretok",
                          FVE_SRC_PRETOK_DEFAULT, fve.set_source_pretok,
                          "mdi:transmission-tower-export"),
            FveSourceText(fve, entry, FVE_SRC_VYROBA_KEY, "Fve zdroj vyroba",
                          FVE_SRC_VYROBA_DEFAULT, fve.set_source_vyroba,
                          "mdi:solar-power"),
            FveSourceText(fve, entry, FVE_SRC_BATERIE_KEY, "Fve zdroj baterie",
                          FVE_SRC_BATERIE_DEFAULT, fve.set_source_baterie,
                          "mdi:battery-70"),
            # -> text.mar_topeni_zdroj_baterie_vykon (znaménkový výkon baterie, +nabíjí)
            FveSourceText(topeni, entry, TOPENI_SRC_BATT_VYKON_KEY,
                          "Topeni zdroj baterie vykon", TOPENI_SRC_BATT_VYKON_DEFAULT,
                          topeni.set_source_batt_vykon, "mdi:battery-charging"),
            # -> text.mar_profil_nazev
            ProfilNazevText(data["profily"], entry),
        ]
    )


class FveSourceText(RestoreEntity, TextEntity):
    """Jedno zdrojové entity_id pro FVE. Default = Goodwe, přepíše uživatel."""

    _attr_has_entity_name = True
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 255

    def __init__(self, fve, entry: ConfigEntry, key: str, name: str,
                 default: str, setter, icon: str) -> None:
        self._fve = fve
        self._key = key
        self._default = default
        self._setter = setter
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_value = default
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unknown", "unavailable", None):
            self._attr_native_value = last.state
        # protlač do controlleru (nasadí odběr zdroje); evaluate běží až po resume
        self._setter(self._attr_native_value)

    async def async_set_value(self, value: str) -> None:
        self._attr_native_value = value
        self._setter(value)
        self.async_write_ha_state()
        # živá změna zdroje -> re-subscribe + přepočet
        await self._fve.async_apply_config()


class ProfilNazevText(RestoreEntity, TextEntity):
    """text.mar_profil_nazev – jméno pro „Uložit jako".

    Rozbalovátko samo pojmenovat neumí, proto textové pole. Jméno se zároveň
    porovnává s existujícími soubory (binary_sensor.mar_profil_kolize), takže
    každý úhoz překresluje kolizní tlačítko."""

    _attr_has_entity_name = True
    _attr_name = "Profil nazev"
    _attr_icon = "mdi:tag-outline"
    _attr_should_poll = False
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = PROFIL_NAME_MAX

    def __init__(self, profily, entry: ConfigEntry) -> None:
        self._profily = profily
        self._attr_native_value = ""
        self._attr_unique_id = f"{entry.entry_id}_{PROFIL_NAZEV_KEY}"
        self._attr_device_info = device_info(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unknown", "unavailable", None):
            self._attr_native_value = last.state
        self._profily.set_nazev(self._attr_native_value)

    async def async_set_value(self, value: str) -> None:
        self._attr_native_value = value
        self._profily.set_nazev(value)
        self.async_write_ha_state()
