"""Načtení nastavení ze souboru (Konfigurovat → Načíst nastavení ze souboru).

Soubor připraví Pavle / Claude z rozboru dat testera a tester ho jen nahraje.
Odpadá ruční opisování čísel (překlepy, zapomenuté přepínače).

FORMÁT – CSV se středníkem (otevře ho Excel/Sheets i poznámkový blok), UTF-8:

    # řádek s mřížkou = komentář
    entity_id;hodnota;popisek (nepovinný, jen pro lidi)
    number.mar_bod_0;31,5;Bod křivky při 0 °C
    switch.mar_zebra;zapnuto;Zebra
    select.mar_strategie;Ekviterma;Strategie

Pravidla (Pavle 28. 11. 2026):
- Nastaví se VŠE, co v souboru je – ale jen entity MaR (`*.mar_*`) a Aconda
  (`*.acond_*`), domény number / switch / select / time / datetime / text.
  Tlačítka se ze souboru nikdy nemačkají (Reset PLC, Potvrzení poruchy, …).
- Neznámý řádek se přeskočí a nahlásí, zbytek se nastaví. Nikdy nespadnout.
- PEVNÉ POŘADÍ bez ohledu na pořadí řádků v souboru – MaR má vazby:
    1. čísla, časy, texty (vč. slideru 40014),
    2. přepínače a volby (R, +1 °C, Režim vypínání, Acond typ regulace…),
    3. strategie (Minimum si při zapnutí ukládá slider, R ho vrací…),
    4. programy a zápis (Zebra, Den/noc, Okna, Dovolená, FVE) – až po strategii,
       protože BOOST / Bez MaR Zebru vypíná a strategie přepíná zápis zpátečky.
- Hodnota, která už platí, se nezapisuje (šetří EEPROM Aconda).
- Před zápisem se současný stav všech dotčených položek uloží jako záloha ve
  STEJNÉM formátu (`config/mar_nastaveni/zaloha_*.csv`) – „Vrátit stav před
  posledním načtením“ ji načte stejnou cestou.
- Po zápisu se stav zkontroluje: co entita nepřevzala (třeba strategie během
  Dovolené), je ve zprávě jako „nepřevzato“.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

NACTENI_DIR = "mar_nastaveni"          # config/mar_nastaveni/
NACTENI_ZALOHA_POSLEDNI = "zaloha_posledni.csv"
NACTENI_KEEP = 20                      # kolik záloh a načtených souborů nechat
NACTENI_SEP = ";"

_DOMENY = ("number", "switch", "select", "time", "datetime", "text")

_STRATEGIE = "select.mar_strategie"
# 4. skupina: programy a zápis – až po strategii
_PROGRAMY = (
    "switch.mar_zapis_zpatecky",
    "switch.mar_dovolena",
    "switch.mar_okna_teploty",
    "switch.mar_dennoc",
    "switch.mar_zebra",
    "switch.mar_fve_tuv",
    "switch.mar_topeni",
)

_ANO = {"on", "zapnuto", "zap", "ano", "1", "true", "yes"}
_NE = {"off", "vypnuto", "vyp", "ne", "0", "false", "no"}
_UNKNOWN = ("unknown", "unavailable", "", None)


@dataclass
class Vysledek:
    nastaveno: list[str] = field(default_factory=list)
    beze_zmeny: list[str] = field(default_factory=list)
    preskoceno: list[str] = field(default_factory=list)   # neznámé / nepovolené řádky
    odmitnuto: list[str] = field(default_factory=list)    # zápis selhal
    neprevzato: list[str] = field(default_factory=list)   # po zápisu jiný stav
    zaloha: str | None = None
    soubor: str | None = None

    def souhrn(self) -> str:
        return (
            f"Nastaveno {len(self.nastaveno)} · beze změny {len(self.beze_zmeny)} · "
            f"přeskočeno {len(self.preskoceno)} · odmítnuto {len(self.odmitnuto)} · "
            f"nepřevzato {len(self.neprevzato)}"
        )

    def podrobne(self, limit: int = 25) -> str:
        casti = [self.souhrn()]
        for nadpis, seznam in (
            ("Nastaveno", self.nastaveno),
            ("Přeskočeno", self.preskoceno),
            ("Odmítnuto", self.odmitnuto),
            ("Nepřevzato – zkontroluj", self.neprevzato),
        ):
            if seznam:
                radky = seznam[:limit]
                if len(seznam) > limit:
                    radky.append(f"… a dalších {len(seznam) - limit}")
                casti.append(f"**{nadpis}:**\n" + "\n".join(f"- {r}" for r in radky))
        return "\n\n".join(casti)


# --------------------------------------------------------------------------- #
# Rozbor souboru
# --------------------------------------------------------------------------- #
def rozeber(text: str) -> tuple[list[tuple[int, str, str, str]], list[str]]:
    """-> ([(číslo řádku, entity_id, hodnota, popisek)], [vadné řádky])."""
    polozky: list[tuple[int, str, str, str]] = []
    vadne: list[str] = []
    for n, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        casti = [c.strip().strip('"') for c in line.split(NACTENI_SEP)]
        if len(casti) < 2:
            vadne.append(f"ř. {n}: chybí středník a hodnota ({line[:40]})")
            continue
        eid, hodnota = casti[0].lower(), casti[1]
        popisek = casti[2] if len(casti) > 2 else ""
        if eid in ("entity_id", "entita"):          # hlavička
            continue
        polozky.append((n, eid, hodnota, popisek))
    return polozky, vadne


def _povoleno(eid: str) -> str | None:
    """None = smí se; jinak důvod, proč ne."""
    if "." not in eid:
        return "není entity_id"
    domena, objekt = eid.split(".", 1)
    if domena == "button":
        return "tlačítka se ze souboru nemačkají"
    if domena not in _DOMENY:
        return f"doména {domena} se nenastavuje"
    if not (objekt.startswith("mar_") or objekt.startswith("acond_")):
        return "jen položky MaR a Aconda"
    return None


def _skupina(eid: str) -> int:
    domena = eid.split(".", 1)[0]
    if eid in _PROGRAMY:
        return 4
    if eid == _STRATEGIE:
        return 3
    if domena in ("switch", "select"):
        return 2
    return 1


def _cislo(text: str) -> float | None:
    try:
        return float(str(text).strip().replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _cas(text: str) -> str | None:
    """'6:00' / '06:00' / '06:00:00' -> '06:00:00'."""
    try:
        casti = [int(x) for x in str(text).strip().split(":")]
    except ValueError:
        return None
    if len(casti) == 2:
        casti.append(0)
    if len(casti) != 3 or not (0 <= casti[0] < 24 and 0 <= casti[1] < 60 and 0 <= casti[2] < 60):
        return None
    return f"{casti[0]:02d}:{casti[1]:02d}:{casti[2]:02d}"


# --------------------------------------------------------------------------- #
# Převod hodnoty na volání služby
# --------------------------------------------------------------------------- #
def _priprav(hass: HomeAssistant, eid: str, hodnota: str):
    """-> (služba, data, cílová hodnota pro porovnání, chyba). Chyba = str."""
    st = hass.states.get(eid)
    domena = eid.split(".", 1)[0]
    if domena == "number":
        v = _cislo(hodnota)
        if v is None:
            return None, None, None, f"„{hodnota}“ není číslo"
        lo = _cislo(st.attributes.get("min")) if st else None
        hi = _cislo(st.attributes.get("max")) if st else None
        if (lo is not None and v < lo - 1e-9) or (hi is not None and v > hi + 1e-9):
            rozsah = f"{lo:g}–{hi:g}" if lo is not None and hi is not None else "entity"
            return None, None, None, f"{hodnota} mimo rozsah {rozsah}"
        return ("number", "set_value"), {"value": v}, v, None
    if domena == "switch":
        h = str(hodnota).strip().lower()
        if h in _ANO:
            return ("switch", "turn_on"), {}, "on", None
        if h in _NE:
            return ("switch", "turn_off"), {}, "off", None
        return None, None, None, f"„{hodnota}“ – čekám zapnuto/vypnuto"
    if domena == "select":
        moznosti = list(st.attributes.get("options") or []) if st else []
        h = str(hodnota).strip()
        shoda = next((o for o in moznosti if o.lower() == h.lower()), None)
        if shoda is None:
            # tolerance: „Útlum“ ~ „Útlum PZ“ (začátek jména, jen když je jednoznačný)
            kandidati = [o for o in moznosti if o.lower().startswith(h.lower())]
            shoda = kandidati[0] if len(kandidati) == 1 else None
        if shoda is None:
            return None, None, None, f"„{h}“ není v nabídce ({', '.join(moznosti)})"
        return ("select", "select_option"), {"option": shoda}, shoda, None
    if domena == "time":
        c = _cas(hodnota)
        if c is None:
            return None, None, None, f"„{hodnota}“ není čas (hh:mm)"
        return ("time", "set_value"), {"time": c}, c, None
    if domena == "datetime":
        h = str(hodnota).strip()
        return ("datetime", "set_value"), {"datetime": h}, h, None
    if domena == "text":
        h = str(hodnota)
        return ("text", "set_value"), {"value": h}, h, None
    return None, None, None, "nepodporovaná doména"


def _shoduje(eid: str, stav: str | None, cil) -> bool:
    if stav in _UNKNOWN:
        return False
    domena = eid.split(".", 1)[0]
    if domena == "number":
        v = _cislo(stav)
        return v is not None and abs(v - float(cil)) < 1e-6
    if domena == "time":
        return _cas(stav) == cil
    return str(stav) == str(cil)


def _hodnota_do_souboru(eid: str, stav: str) -> str:
    domena = eid.split(".", 1)[0]
    if domena == "switch":
        return "zapnuto" if stav == "on" else "vypnuto"
    if domena == "number":
        v = _cislo(stav)
        return f"{v:g}".replace(".", ",") if v is not None else stav
    return stav


def _najdi(hass: HomeAssistant, eid: str) -> str | None:
    """entity_id, jak je v HA. Přejmenovaná entita MaR se dohledá podle klíče
    (unique_id = <entry_id>_<klíč>, klíč = objekt bez předpony `mar_`)."""
    if hass.states.get(eid) is not None:
        return eid
    domena, objekt = eid.split(".", 1)
    if not objekt.startswith("mar_"):
        return None
    klic = objekt[4:]
    reg = er.async_get(hass)
    for ent in reg.entities.values():
        if (
            ent.platform == DOMAIN
            and ent.domain == domena
            and ent.unique_id.endswith(f"_{klic}")
            and hass.states.get(ent.entity_id) is not None
        ):
            return ent.entity_id
    return None


# --------------------------------------------------------------------------- #
# Načtení
# --------------------------------------------------------------------------- #
async def async_nacti(hass: HomeAssistant, text: str, zdroj: str = "soubor") -> Vysledek:
    """Rozeber, zazálohuj, nastav v pevném pořadí, zkontroluj. Nikdy nevyhodí."""
    v = Vysledek()
    polozky, vadne = rozeber(text)
    v.preskoceno.extend(vadne)

    plan: list[tuple[int, int, str, str, tuple, dict, object]] = []
    videno: set[str] = set()
    for n, eid, hodnota, popisek in polozky:
        jmeno = popisek or eid
        duvod = _povoleno(eid)
        if duvod:
            v.preskoceno.append(f"ř. {n} {jmeno}: {duvod}")
            continue
        skutecne = _najdi(hass, eid)
        if skutecne is None:
            v.preskoceno.append(f"ř. {n} {jmeno}: v HA neexistuje")
            continue
        if skutecne in videno:
            v.preskoceno.append(f"ř. {n} {jmeno}: v souboru dvakrát, platí první")
            continue
        sluzba, data, cil, chyba = _priprav(hass, skutecne, hodnota)
        if chyba:
            v.odmitnuto.append(f"{jmeno}: {chyba}")
            continue
        videno.add(skutecne)
        plan.append((_skupina(skutecne), n, skutecne, jmeno, sluzba, data, cil))

    plan.sort(key=lambda p: (p[0], p[1]))

    # záloha současného stavu všeho, na co se bude sahat (i co se nezmění)
    if plan:
        try:
            v.zaloha = await _async_zaloha(hass, plan, zdroj)
        except Exception:  # noqa: BLE001 – bez zálohy raději nepsat nic
            _LOGGER.exception("Načtení nastavení: zálohu nešlo uložit – nic nenastavuji")
            v.odmitnuto.append("záloha současného stavu se nepovedla – nic nenastaveno")
            return v

    zapsane: list[tuple[str, str, object]] = []
    for _sk, _n, eid, jmeno, (dom, srv), data, cil in plan:
        st = hass.states.get(eid)
        if st is not None and _shoduje(eid, st.state, cil):
            v.beze_zmeny.append(jmeno)
            continue
        try:
            await hass.services.async_call(dom, srv, {"entity_id": eid, **data}, blocking=True)
        except Exception as err:  # noqa: BLE001 – jeden řádek nesmí zastavit zbytek
            v.odmitnuto.append(f"{jmeno}: {err}")
            continue
        zapsane.append((eid, jmeno, cil))
        # mezi skupinami nech MaR dodýchat (strategie spouští půjčky a kameny)
        if _sk >= 3:
            await asyncio.sleep(0.5)

    # kontrola: převzala entita hodnotu? (Acond potvrzuje zápis čtením – chvíli počkej)
    if zapsane:
        await asyncio.sleep(3)
        for eid, jmeno, cil in zapsane:
            st = hass.states.get(eid)
            if st is not None and _shoduje(eid, st.state, cil):
                v.nastaveno.append(f"{jmeno}: {_hodnota_do_souboru(eid, st.state)}")
            else:
                v.neprevzato.append(
                    f"{jmeno}: chtěl jsem {cil}, je {st.state if st else 'nic'}"
                )

    _LOGGER.info("Načtení nastavení (%s): %s", zdroj, v.souhrn())
    return v


async def _async_zaloha(hass: HomeAssistant, plan, zdroj: str) -> str:
    radky = [
        f"# MaR – záloha před načtením nastavení ({zdroj}), {time.strftime('%d. %m. %Y %H:%M')}",
        "# Načti přes Konfigurovat → Vrátit stav před posledním načtením.",
        "entity_id;hodnota;popisek",
    ]
    for _sk, _n, eid, jmeno, _s, _d, _c in plan:
        st = hass.states.get(eid)
        if st is None or st.state in _UNKNOWN:
            continue
        radky.append(f"{eid};{_hodnota_do_souboru(eid, st.state)};{jmeno}")
    text = "\n".join(radky) + "\n"
    adresar = hass.config.path(NACTENI_DIR)
    jmeno_souboru = f"zaloha_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    await hass.async_add_executor_job(_zapis_zalohu, adresar, jmeno_souboru, text)
    return os.path.join(NACTENI_DIR, jmeno_souboru)


def _zapis_zalohu(adresar: str, jmeno: str, text: str) -> None:
    os.makedirs(adresar, exist_ok=True)
    for cil in (jmeno, NACTENI_ZALOHA_POSLEDNI):
        with open(os.path.join(adresar, cil), "w", encoding="utf-8") as f:
            f.write(text)
    _prores(adresar, "zaloha_2")


def ulozit_nacteny(hass: HomeAssistant, text: str) -> str:
    """Kopie nahraného souboru do config/mar_nastaveni/ (co se kdy načítalo). Executor."""
    adresar = hass.config.path(NACTENI_DIR)
    os.makedirs(adresar, exist_ok=True)
    jmeno = f"nacteno_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    with open(os.path.join(adresar, jmeno), "w", encoding="utf-8") as f:
        f.write(text)
    _prores(adresar, "nacteno_")
    return os.path.join(NACTENI_DIR, jmeno)


def precti_posledni_zalohu(hass: HomeAssistant) -> str | None:
    """Text poslední zálohy nebo None. Executor."""
    cesta = hass.config.path(NACTENI_DIR, NACTENI_ZALOHA_POSLEDNI)
    if not os.path.isfile(cesta):
        return None
    with open(cesta, "r", encoding="utf-8-sig") as f:
        return f.read()


def precti_soubor(cesta: str) -> str:
    """Nahraný soubor: UTF-8 (s BOM i bez), jinak Windows-1250 (český Excel). Executor."""
    with open(cesta, "rb") as f:
        data = f.read()
    for kod in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(kod)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _prores(adresar: str, predpona: str) -> None:
    try:
        soubory = sorted(f for f in os.listdir(adresar) if f.startswith(predpona))
    except OSError:
        return
    for f in soubory[:-NACTENI_KEEP]:
        try:
            os.remove(os.path.join(adresar, f))
        except OSError:
            pass


def oznam(hass: HomeAssistant, v: Vysledek, nadpis: str) -> None:
    """Trvalé oznámení v HA – výsledek zůstane vidět i po zavření dialogu."""
    text = v.podrobne()
    if v.zaloha:
        text += f"\n\nZáloha předchozího stavu: `{v.zaloha}`."
    text += (
        "\n\n**Kontrola:** Okno Diagnostika (Acond) → Export AI kontextu → *Jen acond entity* "
        "a soubor pošli ke kontrole."
    )
    hass.async_create_task(
        hass.services.async_call(
            "persistent_notification", "create",
            {"title": nadpis, "message": text, "notification_id": "mar_nacteni_nastaveni"},
        )
    )
