"""Export dat po minutách do CSV (okno Statistika).

Na stisk tlačítka vezme z historie HA (recorder) hlavní veličiny čerpadla, MaR
a R-Ekvitermy za zvolenou délku, převzorkuje je po minutách (poslední známá
hodnota) a uloží jako CSV do `config/www/mar_export/`. Odkaz `/local/…` se ukáže
v okně Statistika – tester soubor stáhne v telefonu a pošle dál.

CSV je pro český Excel: oddělovač středník, desetinná čárka. Jméno souboru nese
náhodný kousek, protože `/local/` je v HA přístupné bez přihlášení.

Selhání (recorder, disk) NENÍ chyba integrace – jen se zapíše do stavu exportu.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import secrets

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    ACOND_BIT_DEFROST,
    ACOND_BIT_TUV,
    ACOND_CAPACITY,
    ACOND_COMP_BIT,
    ACOND_HP_ON,
    ACOND_INDOOR,
    ACOND_OUTLET,
    ACOND_OUTDOOR,
    ACOND_POWER,
    ACOND_PUMP_BIT,
    ACOND_RETURN_ACT,
    ACOND_RETURN_READBACK,
    ACOND_ROOM_SET,
    ACOND_RPM,
    ACOND_TEPELNY_VYKON,
    DOMAIN,
    EXPORT_DELKA_DEFAULT,
    EXPORT_DELKY,
    EXPORT_KEEP,
    EXPORT_SUBDIR,
    POZADOVANA_MISTNOST_KEY,
    R_SWITCH_KEY,
    signal_export_updated,
)

_LOGGER = logging.getLogger(__name__)

ACOND_SEK_CERPADLO = "binary_sensor.acond_30007_tc_status_bit_4"   # čerpadlo okruh 1 (sekundár)


# (sloupec, zdroj, převod) – zdroj = entity_id nebo ("mar", doména, klíč[, atribut])
_SLOUPCE = [
    ("venku", ACOND_OUTDOOR, "num"),
    ("mistnost", ACOND_INDOOR, "num"),
    ("cil_mistnosti", ("mar", "number", POZADOVANA_MISTNOST_KEY), "num"),
    ("cil_mistnosti_registr", ACOND_ROOM_SET, "num"),
    ("zpatecka", ACOND_RETURN_ACT, "num"),
    ("zadana_zpatecka", ACOND_RETURN_READBACK, "num"),
    ("zpatecka_z_ekvitermy", ("mar", "sensor", "vypoctena_zpatecka"), "num"),
    ("topna_voda", ACOND_OUTLET, "num"),
    ("otacky", ACOND_RPM, "num"),
    ("strop_40014", ACOND_CAPACITY, "num"),
    ("prikon_W", ACOND_POWER, "num"),
    ("vykon_W", ACOND_TEPELNY_VYKON, "num"),
    ("kompresor", ACOND_COMP_BIT, "bit"),
    ("tc_zapnuto", ACOND_HP_ON, "bit"),
    ("sek_cerpadlo", ACOND_SEK_CERPADLO, "bit"),
    ("prim_cerpadlo", ACOND_PUMP_BIT, "bit"),
    ("tuv", ACOND_BIT_TUV, "bit"),
    ("odmraz", ACOND_BIT_DEFROST, "bit"),
    ("strategie", ("mar", "select", "strategie"), "text"),
    ("r_zapnuto", ("mar", "switch", R_SWITCH_KEY), "bit"),
    ("r_stav", ("mar", "switch", R_SWITCH_KEY, "stav"), "text"),
    ("r_brzda", ("mar", "switch", R_SWITCH_KEY, "brzda"), "text"),
]


class ExportManager:
    """Drží zvolenou délku a výsledek posledního exportu."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.delka = EXPORT_DELKA_DEFAULT
        self.bezi = False
        self.vysledek: dict = {}

    def set_delka(self, value: str) -> None:
        if value in EXPORT_DELKY:
            self.delka = value
            self._notify()

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_export_updated(self.entry.entry_id))

    def _eid(self, zdroj) -> tuple[str | None, str | None]:
        if isinstance(zdroj, str):
            return zdroj, None
        _m, domena, klic, *attr = zdroj
        eid = er.async_get(self.hass).async_get_entity_id(
            domena, DOMAIN, f"{self.entry.entry_id}_{klic}")
        return eid, (attr[0] if attr else None)

    async def async_export(self) -> None:
        if self.bezi:
            return
        self.bezi = True
        self._notify()
        try:
            await self._export()
        except Exception as err:  # noqa: BLE001 — export je nadstavba
            _LOGGER.warning("Export dat selhal", exc_info=True)
            self.vysledek = {"chyba": str(err), "cas": dt_util.now().isoformat()}
        finally:
            self.bezi = False
            self._notify()

    async def _export(self) -> None:
        dni = EXPORT_DELKY.get(self.delka, 1)
        konec = dt_util.utcnow().replace(second=0, microsecond=0)
        start = konec - dt.timedelta(days=dni)

        zdroje = [(nazev, *self._eid(z), prevod) for nazev, z, prevod in _SLOUPCE]
        ids_bez = sorted({e for _n, e, a, _p in zdroje if e and a is None})
        ids_s = sorted({e for _n, e, a, _p in zdroje if e and a is not None})

        from homeassistant.components.recorder import get_instance, history

        def nacti(ids, attrs):
            if not ids:
                return {}
            return history.get_significant_states(
                self.hass, start - dt.timedelta(hours=1), konec, ids,
                include_start_time_state=True,
                significant_changes_only=False,
                minimal_response=False,
                no_attributes=not attrs,
            )

        rec = get_instance(self.hass)
        hist = await rec.async_add_executor_job(nacti, ids_bez, False)
        hist_a = await rec.async_add_executor_job(nacti, ids_s, True)

        directory = self.hass.config.path("www", EXPORT_SUBDIR)
        stamp = dt_util.now().strftime("%Y%m%d-%H%M")
        name = f"mar_{stamp}_{dni}d_{secrets.token_hex(3)}.csv"
        radku = await self.hass.async_add_executor_job(
            _zapis_csv, directory, name, start, konec, zdroje, hist, hist_a)
        self.vysledek = {
            "url": f"/local/{EXPORT_SUBDIR}/{name}",
            "soubor": name,
            "radku": radku,
            "od": dt_util.as_local(start).isoformat(timespec="minutes"),
            "do": dt_util.as_local(konec).isoformat(timespec="minutes"),
            "delka": self.delka,
            "cas": dt_util.now().isoformat(timespec="seconds"),
        }
        _LOGGER.debug("Export dat: %s (%s řádků)", name, radku)


# ---------------------------------------------------------------------- #
# Čisté funkce (executor) – bez HA, testovatelné
# ---------------------------------------------------------------------- #
def _hodnota(stav, attr, prevod):
    if stav is None:
        return None
    if attr is not None:
        v = (stav.attributes or {}).get(attr)
    else:
        v = stav.state
    if v in (None, "unknown", "unavailable", ""):
        return None
    if prevod == "bit":
        return 1 if v == "on" else 0 if v == "off" else None
    if prevod == "num":
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return str(v)


def _rada(stavy: list, casy: list, attr, prevod) -> list:
    """Hodnota platná v každé minutě (poslední známý stav ≤ čas)."""
    stavy = sorted(stavy, key=lambda s: s.last_updated)
    out, j, posledni = [], 0, None
    for t in casy:
        while j < len(stavy) and stavy[j].last_updated <= t:
            posledni = _hodnota(stavy[j], attr, prevod)
            j += 1
        out.append(posledni)
    return out


def _bunka(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return str(v).replace(";", ",")


def _zapis_csv(directory, name, start, konec, zdroje, hist, hist_a) -> int:
    kroku = int((konec - start).total_seconds() // 60)
    casy = [start + dt.timedelta(minutes=i) for i in range(kroku + 1)]
    sloupce = []
    for nazev, eid, attr, prevod in zdroje:
        zdroj = hist_a if attr is not None else hist
        sloupce.append(_rada(zdroj.get(eid, []) if eid else [], casy, attr, prevod))
    os.makedirs(directory, exist_ok=True)
    cesta = os.path.join(directory, name)
    with open(cesta, "w", encoding="utf-8-sig", newline="") as f:
        f.write(";".join(["cas"] + [z[0] for z in zdroje]) + "\r\n")
        for i, t in enumerate(casy):
            radek = [dt_util.as_local(t).strftime("%Y-%m-%d %H:%M")]
            radek += [_bunka(s[i]) for s in sloupce]
            f.write(";".join(radek) + "\r\n")
    # úklid: nech posledních EXPORT_KEEP souborů
    stare = sorted(
        (n for n in os.listdir(directory) if n.startswith("mar_") and n.endswith(".csv")),
        key=lambda n: os.path.getmtime(os.path.join(directory, n)),
    )
    for n in stare[:-EXPORT_KEEP]:
        try:
            os.remove(os.path.join(directory, n))
        except OSError:
            pass
    return len(casy)
