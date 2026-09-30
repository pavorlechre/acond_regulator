"""Config flow MaR – fáze 1: jen poloha (mapový picker). Polohu lze měnit i v Options."""

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

from .const import CONF_LATITUDE, CONF_LOCATION, CONF_LONGITUDE, DOMAIN

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
    """Přenastavení polohy (lidé se stěhují)."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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
        return self.async_show_form(step_id="init", data_schema=_schema(cur))
