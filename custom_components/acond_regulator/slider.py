"""Vracení půjčených sliderů Aconda (40014 strop výkonu, 40008 zpátečka).

Strategie a programy si slider půjčí, uloží si „originál“ a při odchodu ho vrátí.
Originál ale nemusí být platný: registr 40014, do kterého nikdo nikdy nezapsal,
čte Acond jako 0 (tester 27. 11. 2026: rozsah 2000–8000 W, slider 0). Zápis 0
Acond odmítne chybou a dřív to zastavilo celé přepnutí strategie – uživatel
uvízl v Minimu a nešlo odejít ani na Bez MaR.

Pravidla vrácení:
- 0 nebo méně u stropu výkonu = nezapsaný registr -> MAX (stroj bez limitu
  z Modbusu jede jako na max),
- jinak oříznout do rozsahu slideru (min/max atribut entity),
- selže-li zápis přesto, jen se zaloguje – odchod ze strategie se nikdy nezastaví.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _attr(hass: HomeAssistant, entity_id: str, attr: str) -> float | None:
    st = hass.states.get(entity_id)
    if st is None:
        return None
    try:
        return float(st.attributes.get(attr))
    except (ValueError, TypeError):
        return None


def platna_hodnota(
    hass: HomeAssistant, entity_id: str, value: float | None, nula_max: bool = False
) -> float | None:
    """Hodnota, kterou jde na slider opravdu zapsat. None = není co vracet."""
    if value is None:
        return None
    try:
        v = float(value)
    except (ValueError, TypeError):
        return None
    lo = _attr(hass, entity_id, "min")
    hi = _attr(hass, entity_id, "max")
    if nula_max and v <= 0:
        return hi                      # nezapsaný registr -> max (None, když max neznáme)
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


async def async_vrat(
    hass: HomeAssistant,
    entity_id: str,
    value: float | None,
    kdo: str,
    nula_max: bool = False,
    blocking: bool = True,
) -> None:
    """Vrať slider na uloženou hodnotu (opravenou podle pravidel nahoře).
    Nikdy nevyhodí výjimku – odchod ze strategie musí projít vždy."""
    v = platna_hodnota(hass, entity_id, value, nula_max)
    if v is None:
        _LOGGER.debug("%s: %s nevracím (uloženo %s, platná hodnota není)", kdo, entity_id, value)
        return
    if value is not None and abs(float(value) - v) > 1e-6:
        _LOGGER.info("%s: uložená hodnota %s mimo rozsah %s -> vracím %s", kdo, value, entity_id, v)
    try:
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": entity_id, "value": round(float(v), 1)},
            blocking=blocking,
        )
    except Exception as err:  # noqa: BLE001 – odchod ze strategie nesmí spadnout
        _LOGGER.warning("%s: vrácení %s na %s selhalo: %s", kdo, entity_id, v, err)
