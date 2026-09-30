"""Profily nastavení MaR – ulož / načti pojmenovanou sadu.

Profil = ekvitermní nastavení (8 čísel), volitelně plus ladění FVE (15 čísel).
Rozsah a co v profilu záměrně NENÍ je popsané u konstant v `const.py`.

Tři věci, na kterých celý díl stojí:

1. **Načítá se PŘES ENTITU, ne do controlleru.** Stav nikde centrálně neleží –
   každý parametr je `Restore*` entita, která si hodnotu drží sama a setterem ji
   tlačí do controlleru. Zápis přímo do controlleru by nechal entitu na staré
   hodnotě a po restartu by ji `async_added_to_hass` protlačila zpátky: profil
   by se tiše rozpadl a nikdo by nevěděl proč. Proto `number.set_value`.

2. **Chybějící klíč = nesahat.** Ne default, ne nula. Díky tomu je tentýž
   formát použitelný pro budoucí denní zásah AI (pošle jen sekci `ekviterma`
   a FVE se ani nedotkne) a pro výměnu mezi instalacemi s různou větví.

3. **Validace po ŘÁDCÍCH, ne po souboru.** Špatný řádek se zahodí a zaloguje,
   zbytek se aplikuje. Rozbitý soubor nesmí zrušit celé načtení, natož zastavit
   topení. Nikdy nespadnout.

Větve (s baterkou / bez baterky) se neřeší novým mechanismem – vyřeší je
pravidlo z bodu 2: uložení zapíše jen klíče SVÉ větve, načtení aplikuje, co
v souboru je. Honzův soubor prostě SoC řádky neobsahuje, takže Pavlovi je
nepřepíše, a naopak.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import slugify

from .const import (
    DOMAIN,
    PROFIL_SW_NACIST,
    PROFIL_SW_ULOZIT,
    PROFIL_DIR,
    PROFIL_ENTITY,
    PROFIL_EXT,
    PROFIL_HEADER_ROW,
    PROFIL_KEYS_EKV,
    PROFIL_KEYS_FVE_BATT,
    PROFIL_KEYS_FVE_NOBATT,
    PROFIL_KEYS_FVE_SDILENE,
    PROFIL_KEYS_TUV_BATT,
    PROFIL_NAME_MAX,
    PROFIL_NONE,
    PROFIL_SCHEMA,
    PROFIL_SECTION_EKV,
    PROFIL_SECTION_FVE,
    PROFIL_SECTIONS,
    PROFIL_SEP,
    PROFIL_TUV_BATT_SRC,
    PROFIL_VZOR_DIR,
    PROFIL_VZOR_PREFIX,
    PROFIL_WWW_KEEP,
    PROFIL_WWW_PREFIX,
    SNAPSHOT_SUBDIR,
    signal_profily_updated,
)

_LOGGER = logging.getLogger(__name__)


def _fmt(value: float) -> str:
    """Číslo do souboru: bez zbytečných nul, vždy s tečkou (čte se i čárka)."""
    return f"{value:g}"


def _cislo(text: str) -> float | None:
    """Hodnota ze souboru. Desetinná čárka i tečka – uživatel i Sheets."""
    try:
        return float(text.strip().replace(",", "."))
    except (ValueError, AttributeError):
        return None


def _ciste_utf8(text: str) -> bool:
    """Je jméno bezpečné poslat do stavu entity?

    `os.listdir()` dekóduje jména souborů s `surrogateescape`: bajty, které
    nejsou platné UTF-8 (soubor přenesený z Windows, jiné kódování, poškozený
    přenos), se objeví jako náhradní znaky typu „udcXX". Takový řetězec se NEDÁ
    serializovat do JSON – a protože Home Assistant serializuje seznam stavů
    jako CELEK, jediné takové jméno v atributu `options` shodí `/api/states`
    pro CELÝ Home Assistant. Frontend pak nedostane žádné stavy: entity se
    tváří jako neexistující, přepínače se „vracejí" a stránka s logy se točí.

    Přesně tohle se stalo ve 0.12.2 kvůli vzorům s diakritikou v názvu (ZIP je
    uložil bez příznaku UTF-8). Od 0.12.3 mají vzory jména bez diakritiky A
    navíc tudy neprojde nic, co by to mohlo zopakovat.
    """
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _bezpecne_jmeno(name: str) -> str:
    """Jméno profilu -> jméno souboru. Ubrat cokoli, čím by se dalo vyjet
    z adresáře (lomítka, dvojtečky, uvozovky) i řídicí znaky. Diakritiku
    ponechat – Honza si soubor má poznat."""
    zakazane = set('/\\:*?"<>|\r\n\t')
    ocisteno = "".join(
        ch for ch in (name or "")
        if ch not in zakazane and ch >= " " and _ciste_utf8(ch)
    )
    ocisteno = ocisteno.strip().strip(".")
    return ocisteno[:PROFIL_NAME_MAX]


class ProfilyManager:
    """Ulož / načti / smaž profil. Žádný vlastní tik, žádné pollování –
    všechno se děje jen na stisk tlačítka."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator,
                 topeni) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.topeni = topeni
        # přepínače „včetně FVE“ – VĚDOMĚ dva, každý u své akce (uložení může
        # chtít plnou sadu, načtení jen ekvitermu; typicky při cizím profilu)
        # Hodnoty přepínačů se NEDRŽÍ tady (0.12.2). Čtou se v okamžiku operace
        # přímo ze stavu přepínače – viz `_prepinac()`. Zrcadlení do atributu se
        # na železe rozešlo: atribut byl `True`, ale zobrazený přepínač `off`,
        # takže se z něj nedalo přepnout zpátky (karta ukazovala off -> stisk
        # poslal turn_on) a soubor vznikal vždycky s FVE. Když je jediným
        # zdrojem pravdy to, co uživatel VIDÍ, nemá se to jak rozejít.
        self.ulozit_fve = False      # jen pro obnovu po restartu (viz set_*)
        self.nacist_fve = False
        self.vybrany = PROFIL_NONE       # co je v rozbalovátku
        self.nazev = ""                  # jméno pro „Uložit jako“
        self.stav = "—"                  # výsledek poslední operace (sensor)
        self.stav_atributy: dict[str, Any] = {}
        self._seznam: list[str] = []      # uživatelské profily (bez přípony)
        self._vzory: list[str] = []       # předpřipravené (bez přípony)
        self._adresar_posledni = ""       # kde jsme naposled hledali (do diagnostiky)
        self._chyba_cesty: str | None = None

    # --- životní cyklus ----------------------------------------------------

    async def async_start(self) -> None:
        """Založ výchozí adresář a načti seznam souborů.

        Adresář se zakládá prázdný schválně: uživatel, kterému někdo pošle
        profil mailem, musí mít kam ho nakopírovat, a hledat neexistující
        složku je zbytečná otrava. Fail-safe: nejde-li to, seznam zůstane
        prázdný a integrace jede dál (profily jsou nadstavba, ne regulace)."""
        try:
            # POZOR: os.makedirs bere druhý POZIČNÍ argument jako `mode`, ne
            # `exist_ok` – proto vlastní obal. (Přímé `makedirs(cesta, True)`
            # založilo složku s právy 0o001 a při reloadu spadlo na FileExists.)
            await self.hass.async_add_executor_job(
                self._zaloz, self.hass.config.path(PROFIL_DIR)
            )
        except OSError:
            _LOGGER.warning("Adresář profilů nešlo založit", exc_info=True)
        await self.async_refresh()

    # --- adresáře ----------------------------------------------------------

    def _vzory_dir(self) -> str:
        return os.path.join(os.path.dirname(__file__), PROFIL_VZOR_DIR)

    def _user_dir(self) -> str:
        """Jediný adresář profilů: `config/mar_profily/`.

        Volitelný „jiný adresář" byl v 0.12.2 ZRUŠEN. Jeho textová entita si
        při přidání volala `async_refresh()`, takže cokoli, co v refreshi
        selhalo, shodilo tu entitu (HA hlásil „Entita nebyla nalezena") A
        NAVÍC nechalo seznam profilů prázdný – rozbalovátko mělo jen „—".
        Nepovinné pohodlí nesmí mít moc rozbít hlavní funkci; adresář je
        proto pevný a refresh se volá jen odtud, kde nemá co zabít."""
        return self.hass.config.path(PROFIL_DIR)

    # --- seznam ------------------------------------------------------------

    def _precti_seznam(self, adresar: str) -> list[str]:
        try:
            if not os.path.isdir(adresar):
                return []
            soubory = os.listdir(adresar)
        except OSError:
            _LOGGER.warning("Adresář profilů %s nejde přečíst", adresar, exc_info=True)
            return []
        jmena = []
        for f in soubory:
            if not f.endswith(PROFIL_EXT):
                continue
            if not os.path.isfile(os.path.join(adresar, f)):
                continue
            if not _ciste_utf8(f):
                # NIKDY to nepustit do stavu entity – shodilo by to celý HA
                _LOGGER.warning(
                    "Profil %r v %s má jméno v cizím kódování a je přeskočen. "
                    "Přejmenuj ho bez diakritiky (ideálně jen a-z, 0-9, mezera).",
                    f.encode("utf-8", "backslashreplace").decode("ascii"), adresar,
                )
                continue
            jmena.append(f[: -len(PROFIL_EXT)])
        return sorted(jmena, key=lambda s: s.lower())

    async def async_refresh(self, force: bool = False) -> None:
        """Přečti adresáře. Volá se po operaci i z POLLOVÁNÍ selectu (30 s),
        aby se objevil i soubor nakopírovaný ručně přes SFTP — na to se dřív
        čekalo do restartu HA, což byla chyba návrhu.

        Dispatch jen při SKUTEČNÉ změně: pollování nesmí každých 30 s
        překreslovat entity a zaplavovat recorder."""
        # CELÉ v try: refresh se volá i z pollování a z přidávání entit, takže
        # jediná výjimka tady umí zabít entitu i nechat seznam navždy prázdný.
        # Radši prázdný seznam a hlášku v logu než mrtvé okno.
        try:
            adresar = self._user_dir()
            vzory = await self.hass.async_add_executor_job(
                self._precti_seznam, self._vzory_dir()
            )
            seznam = await self.hass.async_add_executor_job(
                self._precti_seznam, adresar
            )
        except Exception as err:  # noqa: BLE001
            self._chyba_cesty = f"Seznam profilů nešlo přečíst: {err}"
            _LOGGER.warning("Profily: čtení adresářů selhalo", exc_info=True)
            self._dispatch()
            return
        zmena = (vzory != self._vzory or seznam != self._seznam
                 or adresar != self._adresar_posledni or self._chyba_cesty)
        self._vzory = vzory
        self._seznam = seznam
        self._adresar_posledni = adresar
        self._chyba_cesty = None
        # smazaný / zmizelý profil nesmí zůstat vybraný
        if self.vybrany != PROFIL_NONE and self.vybrany not in self.options:
            self.vybrany = PROFIL_NONE
            zmena = True
        if zmena:
            _LOGGER.info("Profily: %s -> %d profilů, %d vzorů",
                         adresar, len(seznam), len(vzory))
        if zmena or force:
            self._dispatch()

    @property
    def options(self) -> list[str]:
        """Rozbalovátko: placeholder + vzory + uživatelské. Placeholder tu je
        vždycky, protože prázdný seznam options se v HA chová nepříjemně."""
        return (
            [PROFIL_NONE]
            + [PROFIL_VZOR_PREFIX + v for v in self._vzory]
            + self._seznam
        )

    @property
    def kolize(self) -> bool:
        """True == pod zadaným jménem už soubor existuje. Řídí odkrytí
        červeného „Přepsat“ místo „Uložit“ (nebezpečné tlačítko existuje jen
        ve chvíli, kdy je nebezpečné)."""
        jmeno = _bezpecne_jmeno(self.nazev)
        return bool(jmeno) and jmeno in self._seznam

    # --- settery z entit ---------------------------------------------------

    def set_ulozit_fve(self, value: bool) -> None:
        self.ulozit_fve = bool(value)

    def set_nacist_fve(self, value: bool) -> None:
        self.nacist_fve = bool(value)

    def _prepinac(self, entity_id: str, zaloha: bool) -> bool:
        """Co uživatel VIDÍ, to platí. Stav přepínače se čte ze stavového
        stroje v okamžiku stisku tlačítka; `zaloha` (poslední protlačená
        hodnota) se použije jen když entita ještě neexistuje."""
        st = self.hass.states.get(entity_id)
        if st is None or st.state not in ("on", "off"):
            return zaloha
        return st.state == "on"

    @property
    def s_fve_ulozit(self) -> bool:
        return self._prepinac(PROFIL_SW_ULOZIT, self.ulozit_fve)

    @property
    def s_fve_nacist(self) -> bool:
        return self._prepinac(PROFIL_SW_NACIST, self.nacist_fve)

    def set_vybrany(self, value: str) -> None:
        self.vybrany = value
        self._dispatch()

    def set_nazev(self, value: str) -> None:
        self.nazev = value or ""
        self._dispatch()          # překreslí kolizní tlačítko

    # --- větve -------------------------------------------------------------

    def _ma_baterku_topeni(self) -> bool:
        """Větev FVE topení podle KONFIGURACE zdroje (vlastnost controlleru)."""
        try:
            return bool(self.topeni.ma_baterku)
        except AttributeError:      # pragma: no cover – obrana proti refaktoru
            return False

    def _ma_baterku_tuv(self) -> bool:
        """Větev FVE TUV má VLASTNÍ detekci – zdroj SoC v procentech, ne zdroj
        výkonu baterie u topení. Ptáme se dvakrát, každého bloku zvlášť."""
        st = self.hass.states.get(PROFIL_TUV_BATT_SRC)
        if st is None:
            return False
        return st.state not in ("unknown", "unavailable", "", None)

    def _klice_k_ulozeni(self, s_fve: bool) -> list[str]:
        klice = list(PROFIL_KEYS_EKV)
        if not s_fve:
            return klice
        klice += PROFIL_KEYS_FVE_SDILENE
        klice += (
            PROFIL_KEYS_FVE_BATT if self._ma_baterku_topeni() else PROFIL_KEYS_FVE_NOBATT
        )
        if self._ma_baterku_tuv():
            klice += PROFIL_KEYS_TUV_BATT
        return klice

    def _vetev_popis(self) -> str:
        return "s baterkou" if self._ma_baterku_topeni() else "bez baterky"

    # --- serializace -------------------------------------------------------

    def _sestav(self, s_fve: bool) -> tuple[str, int, list[str]]:
        """Text souboru, počet zapsaných hodnot, varování."""
        varovani: list[str] = []
        radky: list[str] = []
        for klic in self._klice_k_ulozeni(s_fve):
            entita = PROFIL_ENTITY[klic]
            st = self.hass.states.get(entita)
            if st is None or st.state in ("unknown", "unavailable", "", None):
                varovani.append(f"{klic}: entita {entita} nedostupná, vynechána")
                continue
            hodnota = _cislo(st.state)
            if hodnota is None:
                varovani.append(f"{klic}: stav „{st.state}“ není číslo, vynechán")
                continue
            sekce = (
                PROFIL_SECTION_EKV if klic in PROFIL_KEYS_EKV else PROFIL_SECTION_FVE
            )
            radky.append(PROFIL_SEP.join([sekce, klic, _fmt(hodnota)]))

        stamp = time.strftime("%d.%m.%Y %H:%M")
        hlavicka = [
            f"# MaR profil · schema {PROFIL_SCHEMA} · uloženo {stamp}"
            + (f" · {self._vetev_popis()}" if s_fve else ""),
            PROFIL_HEADER_ROW,
        ]
        return "\n".join(hlavicka + radky) + "\n", len(radky), varovani

    def _rozeber(self, text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
        """Řádky souboru -> [(sekce, klíč, hodnota)] + varování. Nic nezapisuje."""
        polozky: list[tuple[str, str, str]] = []
        varovani: list[str] = []
        for cislo_radku, radek in enumerate(text.splitlines(), start=1):
            cisty = radek.strip()
            if not cisty or cisty.startswith("#"):
                continue
            if cisty.replace(" ", "").lower() == PROFIL_HEADER_ROW.replace(" ", ""):
                continue
            casti = [c.strip() for c in cisty.split(PROFIL_SEP)]
            if len(casti) != 3:
                varovani.append(f"řádek {cislo_radku}: nemá tři pole, zahozen")
                continue
            polozky.append((casti[0].lower(), casti[1], casti[2]))
        return polozky, varovani

    # --- aplikace ----------------------------------------------------------

    async def _aplikuj(self, polozky, s_fve: bool) -> tuple[int, int, list[str]]:
        """Zapiš hodnoty PŘES ENTITU (number.set_value). Vrátí (zapsáno,
        přeskočeno, varování). Každý řádek v vlastním try – jeden rozbitý
        nesmí zastavit dávku."""
        zapsano = 0
        preskoceno = 0
        varovani: list[str] = []

        for sekce, klic, surova in polozky:
            if sekce not in PROFIL_SECTIONS:
                varovani.append(f"{klic}: neznámá sekce „{sekce}“, přeskočeno")
                preskoceno += 1
                continue
            if sekce == PROFIL_SECTION_FVE and not s_fve:
                preskoceno += 1          # vypnutý přepínač – ticho, je to volba
                continue
            entita = PROFIL_ENTITY.get(klic)
            if entita is None:
                # profil z novější verze MaR: načti co jde, zbytek zaloguj
                varovani.append(f"{klic}: neznámý klíč, přeskočeno")
                preskoceno += 1
                continue
            hodnota = _cislo(surova)
            if hodnota is None:
                varovani.append(f"{klic}: „{surova}“ není číslo, zahozeno")
                preskoceno += 1
                continue
            st = self.hass.states.get(entita)
            if st is None:
                varovani.append(f"{klic}: entita {entita} neexistuje, zahozeno")
                preskoceno += 1
                continue
            # mimo rozsah se ZAHAZUJE, neclampuje: profil, který si vymyslel
            # nesmysl, nemá právo posunout regulaci na krajní mez potichu
            dolni = st.attributes.get("min")
            horni = st.attributes.get("max")
            if dolni is not None and hodnota < float(dolni) - 1e-9:
                varovani.append(f"{klic}: {surova} je pod minimem {dolni}, zahozeno")
                preskoceno += 1
                continue
            if horni is not None and hodnota > float(horni) + 1e-9:
                varovani.append(f"{klic}: {surova} je nad maximem {horni}, zahozeno")
                preskoceno += 1
                continue
            try:
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": entita, "value": hodnota},
                    blocking=True,
                )
                zapsano += 1
            except Exception as err:  # noqa: BLE001 – jeden řádek nesmí shodit dávku
                varovani.append(f"{klic}: zápis selhal ({err})")
                preskoceno += 1

        return zapsano, preskoceno, varovani

    # --- operace -----------------------------------------------------------

    async def async_ulozit(self, prepsat: bool = False) -> None:
        jmeno = _bezpecne_jmeno(self.nazev)
        if not jmeno:
            self._hlas("Zadej jméno profilu.")
            return
        if jmeno.startswith(PROFIL_VZOR_PREFIX.strip()):
            self._hlas("Jméno nesmí začínat značkou vzoru.")
            return
        adresar = self._user_dir()
        if not prepsat and jmeno in self._seznam:
            self._hlas(f"Profil „{jmeno}“ už existuje — použij Přepsat.")
            return

        s_fve = self.s_fve_ulozit
        text, pocet, varovani = self._sestav(s_fve)
        if not pocet:
            self._hlas("Není co uložit — hodnoty nejsou dostupné.")
            return
        cil = os.path.join(adresar, jmeno + PROFIL_EXT)
        try:
            await self.hass.async_add_executor_job(self._zapis, adresar, cil, text)
        except OSError as err:
            _LOGGER.warning("Profil %s se nepodařilo uložit", cil, exc_info=True)
            self._hlas(f"Uložení selhalo: {err}")
            return

        odkaz = await self._async_kopie_ke_stazeni(jmeno, text)
        await self.async_refresh(force=True)
        self.vybrany = jmeno
        rozsah = "s FVE" if s_fve else "jen ekviterma"
        self._hlas(
            f"Uloženo „{jmeno}“ ({pocet} hodnot, {rozsah}).",
            {"soubor": cil, "odkaz": odkaz, "pocet": pocet, "varovani": varovani},
        )
        for v in varovani:
            _LOGGER.warning("Profil %s – %s", jmeno, v)

    async def async_nacist(self) -> None:
        if self.vybrany == PROFIL_NONE:
            self._hlas("Vyber profil.")
            return
        je_vzor = self.vybrany.startswith(PROFIL_VZOR_PREFIX)
        jmeno = self.vybrany[len(PROFIL_VZOR_PREFIX):] if je_vzor else self.vybrany
        adresar = self._vzory_dir() if je_vzor else self._user_dir()
        zdroj = os.path.join(adresar, jmeno + PROFIL_EXT)
        try:
            text = await self.hass.async_add_executor_job(self._precti, zdroj)
        except OSError as err:
            _LOGGER.warning("Profil %s nejde přečíst", zdroj, exc_info=True)
            self._hlas(f"Načtení selhalo: {err}")
            return

        s_fve = self.s_fve_nacist
        polozky, varovani = self._rozeber(text)
        zapsano, preskoceno, varovani2 = await self._aplikuj(polozky, s_fve)
        varovani += varovani2
        # jeden refresh po celé dávce (ne po každém čísle)
        await self.coordinator.async_request_refresh()

        ma_fve = any(s == PROFIL_SECTION_FVE for s, _k, _v in polozky)
        pozn = ""
        if s_fve and not ma_fve:
            pozn = " Soubor sekci FVE neobsahuje."
        elif not s_fve and ma_fve:
            pozn = " Sekce FVE přeskočena (přepínač vypnutý)."
        self._hlas(
            f"Načteno „{jmeno}“: {zapsano} hodnot"
            + (f", {preskoceno} přeskočeno" if preskoceno else "")
            + "." + pozn,
            {"zapsano": zapsano, "preskoceno": preskoceno, "varovani": varovani},
        )
        for v in varovani:
            _LOGGER.warning("Profil %s – %s", jmeno, v)

    async def async_smazat(self) -> None:
        if self.vybrany == PROFIL_NONE:
            self._hlas("Vyber profil.")
            return
        if self.vybrany.startswith(PROFIL_VZOR_PREFIX):
            self._hlas("Vzorové profily se mazat nedají (jsou jen ke čtení).")
            return
        adresar = self._user_dir()
        jmeno = self.vybrany
        cil = os.path.join(adresar, jmeno + PROFIL_EXT)
        try:
            await self.hass.async_add_executor_job(os.remove, cil)
        except OSError as err:
            _LOGGER.warning("Profil %s se nepodařilo smazat", cil, exc_info=True)
            self._hlas(f"Smazání selhalo: {err}")
            return
        self.vybrany = PROFIL_NONE
        await self.async_refresh(force=True)
        self._hlas(f"Smazáno „{jmeno}“.")

    # --- souborové operace (běží v executoru) ------------------------------

    @staticmethod
    def _zaloz(adresar: str) -> None:
        os.makedirs(adresar, exist_ok=True)

    @staticmethod
    def _zapis(adresar: str, cil: str, text: str) -> None:
        os.makedirs(adresar, exist_ok=True)
        with open(cil, "w", encoding="utf-8") as f:
            f.write(text)

    @staticmethod
    def _precti(zdroj: str) -> str:
        with open(zdroj, "r", encoding="utf-8-sig") as f:
            return f.read()

    async def _async_kopie_ke_stazeni(self, jmeno: str, text: str) -> str | None:
        """Kopie do `config/www/mar/` -> jde stáhnout přes prohlížeč (odkaz
        `/local/...`). DATOVANÉ jméno kvůli dlouhé cache `/local`.
        Ozdoba: selže-li, uložení profilu tím není dotčené."""
        try:
            adresar = self.hass.config.path("www", SNAPSHOT_SUBDIR)
            soubor = (
                f"{PROFIL_WWW_PREFIX}-{slugify(jmeno)}-"
                f"{time.strftime('%Y%m%d-%H%M%S')}{PROFIL_EXT}"
            )
            cil = os.path.join(adresar, soubor)
            await self.hass.async_add_executor_job(self._zapis, adresar, cil, text)
            await self.hass.async_add_executor_job(self._prores, adresar)
            return f"/local/{SNAPSHOT_SUBDIR}/{soubor}"
        except Exception:  # noqa: BLE001 – kopie ke stažení je nadstavba
            _LOGGER.warning("Kopii profilu ke stažení nešlo vyrobit", exc_info=True)
            return None

    @staticmethod
    def _prores(adresar: str) -> None:
        """Nech posledních N datovaných kopií, zbytek smaž (jinak by www rostlo)."""
        try:
            soubory = sorted(
                f for f in os.listdir(adresar)
                if f.startswith(PROFIL_WWW_PREFIX + "-") and f.endswith(PROFIL_EXT)
            )
        except OSError:
            return
        if len(soubory) <= PROFIL_WWW_KEEP:
            return
        for f in soubory[:-PROFIL_WWW_KEEP]:
            try:
                os.remove(os.path.join(adresar, f))
            except OSError:
                pass

    # --- pomocné -----------------------------------------------------------

    @property
    def diagnostika(self) -> dict[str, Any]:
        """Kde se hledá a co je vidět. Bez tohohle se hledá naslepo: když
        se nakopírovaný soubor neobjeví, první otázka je „v jakém adresáři
        se vlastně kouká a kolik souborů tam vidí"."""
        return {
            "adresar": self._adresar_posledni or self._user_dir(),
            "ulozit_s_fve": self.s_fve_ulozit,
            "nacist_s_fve": self.s_fve_nacist,
            "pripona": PROFIL_EXT,
            "profilu": len(self._seznam),
            "vzoru": len(self._vzory),
            "chyba_cesty": self._chyba_cesty,
        }

    def _hlas(self, zprava: str, atributy: dict[str, Any] | None = None) -> None:
        """Výsledek operace do stavové řádky. Bez zpětné vazby by se tichá
        degradace (zahozený řádek) nikdy neprojevila."""
        self.stav = zprava[:255]
        self.stav_atributy = atributy or {}
        _LOGGER.info("Profily: %s", zprava)
        self._dispatch()

    def _dispatch(self) -> None:
        async_dispatcher_send(self.hass, signal_profily_updated(self.entry.entry_id))

    def shutdown(self) -> None:
        """Nic k uklízení – žádný tik, žádný odběr, žádná půjčka registru."""
        return
