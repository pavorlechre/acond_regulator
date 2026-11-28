"""Config flow MaR – poloha (mapový picker); Options = nabídka:
poloha, načtení nastavení ze souboru (nacteni.py), návrat zálohy před načtením."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import nacteni
from .const import CONF_LATITUDE, CONF_LOCATION, CONF_LONGITUDE, DOMAIN

_AI_KONTEXT = "button.acond_ai_context_acond"   # Acond: Export AI kontextu – jen acond (+ MaR)
_SOUBOR = "soubor"
_AI = "ai_kontext"

_LOCATION = selector.LocationSelector(selector.LocationSelectorConfig(radius=False))


def _schema(default_loc: dict[str, float]) -> vol.Schema:
    return vol.Schema({vol.Required(CONF_LOCATION, default=default_loc): _LOCATION})


def _flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    loc = user_input.pop(CONF_LOCATION, None) or {}
    out = dict(user_input)
    out[CONF_LATITUDE] = loc.get("latitude")
    out[CONF_LONGITUDE] = loc.get("longitude")
    return out


class MarConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Regulace (MaR)", data=_flatten(user_input))
        default_loc = {
            "latitude": self.hass.config.latitude,
            "longitude": self.hass.config.longitude,
        }
        return self.async_show_form(step_id="user", data_schema=_schema(default_loc))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return MarOptionsFlow(entry)


class MarOptionsFlow(OptionsFlow):
    """Nabídka: načíst nastavení ze souboru · vrátit zálohu · poloha."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._vysledek: nacteni.Vysledek | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["nacist", "vratit", "poloha"])

    # --- poloha (dřívější jediný krok) -------------------------------------
    async def async_step_poloha(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            flat = _flatten(user_input)
            # zápis nové polohy do data entry; options necháme prázdné
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, **flat}
            )
            return self.async_create_entry(title="", data={})
        cur = {
            "latitude": self.entry.data.get(CONF_LATITUDE, self.hass.config.latitude),
            "longitude": self.entry.data.get(CONF_LONGITUDE, self.hass.config.longitude),
        }
        return self.async_show_form(step_id="poloha", data_schema=_schema(cur))

    # --- načtení nastavení ze souboru ----------------------------------------
    async def async_step_nacist(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                text = await self.hass.async_add_executor_job(
                    _precti_nahrany, self.hass, user_input[_SOUBOR]
                )
            except Exception:  # noqa: BLE001
                errors["base"] = "soubor_necitelny"
            else:
                try:
                    await self.hass.async_add_executor_job(nacteni.ulozit_nacteny, self.hass, text)
                except OSError:
                    pass                                  # kopie je jen pro historii
                self._vysledek = await nacteni.async_nacti(self.hass, text, "soubor")
                nacteni.oznam(self.hass, self._vysledek, "MaR – načtení nastavení")
                return await self.async_step_hotovo()
        schema = vol.Schema({
            vol.Required(_SOUBOR): selector.FileSelector(
                selector.FileSelectorConfig(accept=".csv,.txt,text/csv,text/plain")
            )
        })
        return self.async_show_form(step_id="nacist", data_schema=schema, errors=errors)

    # --- návrat zálohy -------------------------------------------------------
    async def async_step_vratit(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        text = await self.hass.async_add_executor_job(nacteni.precti_posledni_zalohu, self.hass)
        if text is None:
            return self.async_abort(reason="zadna_zaloha")
        if user_input is not None:
            self._vysledek = await nacteni.async_nacti(self.hass, text, "záloha")
            nacteni.oznam(self.hass, self._vysledek, "MaR – návrat stavu před načtením")
            return await self.async_step_hotovo()
        radky = sum(
            1 for r in text.splitlines()
            if r.strip() and not r.startswith("#") and not r.startswith("entity_id")
        )
        return self.async_show_form(
            step_id="vratit", data_schema=vol.Schema({}),
            description_placeholders={"pocet": str(radky)},
        )

    # --- výsledek + AI kontext ke kontrole -----------------------------------
    async def async_step_hotovo(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input.get(_AI) and self.hass.states.get(_AI_KONTEXT) is not None:
                await self.hass.services.async_call(
                    "button", "press", {"entity_id": _AI_KONTEXT}, blocking=False
                )
            return self.async_create_entry(title="", data=dict(self.entry.options))
        v = self._vysledek or nacteni.Vysledek()
        ma_ai = self.hass.states.get(_AI_KONTEXT) is not None
        schema = vol.Schema({vol.Optional(_AI, default=ma_ai): bool}) if ma_ai else vol.Schema({})
        return self.async_show_form(
            step_id="hotovo", data_schema=schema,
            description_placeholders={"vysledek": v.podrobne(limit=15)},
        )


def _precti_nahrany(hass, file_id: str) -> str:
    """Nahraný soubor z dialogu HA (file_upload). Běží v executoru."""
    from homeassistant.components.file_upload import process_uploaded_file

    with process_uploaded_file(hass, file_id) as cesta:
        return nacteni.precti_soubor(str(cesta))
