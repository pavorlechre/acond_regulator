"""Acond Regulator / MaR – ekvitermní regulace + statistika.

hass.data[DOMAIN][entry_id] = {"coordinator": MarCoordinator, "stats": StatisticsAccumulator}
Dva oddělené „mozky": regulační smyčka (coordinator) a statistika (event-driven
akumulátor s vlastní persistencí). Statistika se navěšuje až po setupu platforem,
kdy už existují entity Acondu (baseline z živých stavů).
"""

from __future__ import annotations

import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    ACOND_REG_TYPE,
    DOMAIN,
    KOMPRESOR_URL,
    SCHEMA_URL,
    MANUAL_URL,
    PLATFORMS,
    REG_TYPE_STANDARD,
    STRATEGY_BOOST,
    STRATEGY_EKVITERM,
    STRATEGY_MINIMUM,
)
from .coordinator import MarCoordinator
from .dennoc import DenNocController
from .dovolena import DovolenaController
from .fve import FveController
from .minimum import MinimumController
from .okna_teploty import OknaTeplotyController
from .pokojovka import PokojovkaOwner
from .profily import ProfilyManager
from .runstate import RunState
from .sequences import SequenceRunner
from .statistics.accumulator import StatisticsAccumulator
from .topeni import TopeniController
from .zebra import ZebraController

_LOGGER = logging.getLogger(__name__)


async def _async_serve_manual(hass: HomeAssistant) -> None:
    """Naservíruj příručku na `/mar-prirucka/` (adresa je KONTRAKT).

    Soubor leží ve složce integrace, takže uživatel dostane příručku k té verzi,
    kterou má nainstalovanou — ne k nějaké novější, která popisuje tlačítka, co
    ještě nemá.

    FAIL-SAFE: když registrace z jakéhokoli důvodu selže, integrace běží dál a
    jen se zaloguje. Nápověda je nadstavba; nesmí shodit regulaci.
    """
    # Cesta se registruje jednou za běh HA. Při reloadu integrace by druhá
    # registrace téže cesty skončila výjimkou a zbytečným warningem v logu.
    if hass.data.get(DOMAIN, {}).get("_manual_served"):
        return
    try:
        from homeassistant.components.http import StaticPathConfig

        directory = os.path.join(os.path.dirname(__file__), "prirucka")
        if not os.path.isdir(directory):
            _LOGGER.warning("Složka s příručkou chybí (%s) — nápověda nepojede",
                            directory)
            return
        index = os.path.join(directory, "index.html")
        await hass.http.async_register_static_paths(
            [
                # Holá složka by vrátila 403 (výpis adresářů je zakázaný),
                # proto se navíc mapuje přímo soubor na adresu bez lomítka.
                # Bez dlouhé mezipaměti: po aktualizaci MaR musí aplikace
                # hned ukázat příručku k nainstalované verzi (jako u schématu).
                StaticPathConfig(MANUAL_URL, index, False),
                StaticPathConfig(f"{MANUAL_URL}/index.html", index, False),
            ]
        )
        hass.data.setdefault(DOMAIN, {})["_manual_served"] = True
    except Exception:  # noqa: BLE001 — nadstavba nesmí shodit setup
        _LOGGER.warning("Příručku se nepodařilo naservírovat", exc_info=True)


async def _async_serve_kompresor(hass: HomeAssistant) -> None:
    """Naservíruj obrázky kompresoru na `/mar-kompresor/` (adresa je KONTRAKT).

    Dvacet malých SVG (4 stupně otáček × 5 teplotních pásem). Který se ukáže,
    rozhoduje šablona v markdown kartě — integrace jen zpřístupní složku.

    Na rozdíl od příručky se registruje CELÁ SLOŽKA: jména souborů skládá
    šablona za běhu, takže je dopředu neznáme. Holá cesta bez souboru vrátí
    403 (výpis adresářů je zakázaný), to je v pořádku — nikdo na ni nemíří.

    FAIL-SAFE: selže-li registrace, integrace běží dál. Obrázek je ozdoba.
    """
    if hass.data.get(DOMAIN, {}).get("_kompresor_served"):
        return
    try:
        from homeassistant.components.http import StaticPathConfig

        directory = os.path.join(os.path.dirname(__file__), "kompresor")
        if not os.path.isdir(directory):
            _LOGGER.warning("Složka s obrázky kompresoru chybí (%s)", directory)
            return
        await hass.http.async_register_static_paths(
            [StaticPathConfig(KOMPRESOR_URL, directory, True)]
        )
        hass.data.setdefault(DOMAIN, {})["_kompresor_served"] = True
    except Exception:  # noqa: BLE001 — nadstavba nesmí shodit setup
        _LOGGER.warning("Obrázky kompresoru se nepodařilo naservírovat", exc_info=True)


async def _async_serve_schema(hass: HomeAssistant) -> None:
    """Naservíruj podklady a vrstvy schématu na `/mar-schema/` (adresa je KONTRAKT).

    Dvaadvacet SVG: dva podklady (s FVE / bez) a pohyblivé vrstvy — kuličky,
    vrtulky, poloha trojcestného, bivalence. Který se ukáže, rozhoduje
    `state_image` v dashboardu; integrace jen zpřístupní složku.

    Stejný tvar jako `_async_serve_kompresor` — registruje se CELÁ SLOŽKA,
    protože jména souborů skládá dashboard za běhu.

    FAIL-SAFE: selže-li registrace, integrace běží dál. Schéma je ozdoba.
    """
    if hass.data.get(DOMAIN, {}).get("_schema_served"):
        return
    try:
        from homeassistant.components.http import StaticPathConfig

        directory = os.path.join(os.path.dirname(__file__), "schema")
        if not os.path.isdir(directory):
            _LOGGER.warning("Složka se schématem chybí (%s)", directory)
            return
        await hass.http.async_register_static_paths(
            # Poslední argument je cache_headers. U schématu MUSÍ být False:
            # s True posílá HA prohlížeči max-age na rok, takže po aktualizaci
            # integrace zůstane v tabletu viset stará kresba a nepomůže ani
            # restart HA — jen ruční smazání cache. Kompresor a příručka to
            # mají zapnuté, ty se prakticky nemění; schéma se mění pořád.
            [StaticPathConfig(SCHEMA_URL, directory, False)]
        )
        hass.data.setdefault(DOMAIN, {})["_schema_served"] = True
    except Exception:  # noqa: BLE001 — nadstavba nesmí shodit setup
        _LOGGER.warning("Schéma se nepodařilo naservírovat", exc_info=True)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _async_serve_manual(hass)
    await _async_serve_kompresor(hass)
    await _async_serve_schema(hass)

    coordinator = MarCoordinator(hass, entry)
    await coordinator.async_load_buffer()
    await coordinator.async_config_entry_first_refresh()

    stats = StatisticsAccumulator(hass, entry)
    await stats.async_load()

    run = RunState(hass, entry)
    minimum = MinimumController(hass, entry, coordinator)
    seq = SequenceRunner(hass, entry, coordinator, run, minimum)
    # Den/noc (osa A) se konstruuje PŘED Zebrou: Zebra ho drží kvůli háčku
    # may_heat_now(); Den/noc pak drží zpět Zebru (attach), aby ji uměl uspat/
    # probudit na hranicích okna. Kruh reference rozvázán dvoufázově.
    dennoc = DenNocController(hass, entry, coordinator, seq, run)
    zebra = ZebraController(hass, entry, coordinator, seq, dennoc)
    dennoc.attach_zebra(zebra)
    # Okna +/- teploty (osa B, executor na 40001) je nezávislý mozek: nepouští
    # kameny, nesahá na 40008, nekoliduje s osou A (Den/noc/Zebra) o registr.
    # Drží jen coordinator kvůli is_standard() bráně a request_refresh() po zápisu.
    # Správce pokojovky: JEDINÝ vlastník 40001. Nemá master – žije pořád, aby
    # pokojovková návnada FVE Topení fungovala i s vyplými Okny. Zákazníci (Okna,
    # FVE Topení) si u něj přihlašují přání; originál drží jednou pro všechny a vrací
    # ho až po odhlášení posledního -> souběh si nemůže rozbít uživatelovu hodnotu.
    pokojovka = PokojovkaOwner(hass, entry, coordinator)
    okna = OknaTeplotyController(hass, entry, coordinator, pokojovka)
    # FVE (osa B, executor na 40005): přebytkový ohřev TUV. Nezávislý mozek – drží
    # jen coordinator (is_standard gate + refresh). Konstruuje se PŘED Dovolenou,
    # protože Dovolená ho drží kvůli vstupnímu guardu („vypni FV programy").
    fve = FveController(hass, entry, coordinator)
    # Topení podle přebytků (osa A, executor 40014 + návnada 40008): SoC-řízený
    # žrout přebytků. Drží coordinator (uhne přes topeni_owns, dá ekvitermní base +
    # is_standard gate) a fve (sdílí přetok + SoC baterie, ať se nezadává dvakrát).
    topeni = TopeniController(hass, entry, coordinator, fve, pokojovka)
    fve.topeni = topeni          # FVE TUV odečte z badgetu vybíjení baterie (sdílený zdroj výkonu)
    topeni.seq = seq             # kámen Topit pro hlídač restartu po předání ekvitermě
    topeni.dennoc = dennoc       # háček „smí se teď topit?" – Den/noc má přednost
    topeni.zebra = zebra         # …a pauza Zebry taky (časové programy > přebytky)
    minimum.seq = seq            # originál 40008 se při běžícím kameni bere z ekvitermy
    # Dovolená (osa B, executor na 40001 + 40005) drží coordinator (is_standard,
    # refresh, strategie pro guard), okna (guard „Okna musí být vyplá") a fve
    # (guard „FV programy musí být vyplé"). Vstupní výlučnost zajišťuje, že se
    # executory nepotkají nad 40001/40005.
    dovolena = DovolenaController(hass, entry, coordinator, okna, fve)
    dovolena.topeni = topeni     # OR-brána guardu „vypni FV programy" i pro topení
    # Profily nastavení: NENÍ regulační mozek – nemá tik, nesleduje zdroje, nedrží
    # žádnou půjčku registru. Jen na stisk tlačítka přečte/zapíše 23 čísel, a to
    # PŘES ENTITY (number.set_value), ne do controllerů. Drží coordinator kvůli
    # jednomu refreshi po dávce a topeni kvůli detekci větve (s/bez baterky).
    profily = ProfilyManager(hass, entry, coordinator, topeni)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "stats": stats,
        "run": run,
        "seq": seq,
        "min": minimum,
        "zebra": zebra,
        "dennoc": dennoc,
        "okna": okna,
        "pokojovka": pokojovka,
        "dovolena": dovolena,
        "fve": fve,
        "topeni": topeni,
        "profily": profily,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # seznam profilů: až po platformách, ať select dostane první dispatch
    await profily.async_start()

    # navěs statistiku až teď: entity Acondu (ees/tes/bity) už jsou k dispozici,
    # takže doplníme baseline z živých stavů a nepřipíšeme po startu obří deltu
    stats.async_start()

    # primitiv běhu kompresoru: taky až teď, ať mar_stav (jeho čtenář) už existuje
    # a dostane první dispatch; sám sleduje podkladové acond entity, ne náš coordinator
    run.async_start()

    async def _clean_slate() -> None:
        """Čistý stůl: MaR přestane zapisovat a všechny naše programy/režimy
        zhasnou. Volá se při odchodu z Typu regulace = Standard (a při startu,
        když Standard není). Návrat do Standardu NIC nenaskočí sám – uživatel
        si vše nastaví znovu, smysluplně se stavem domu."""
        coordinator.set_write_enabled(False)
        # NEJDŘÍV obnov případné nevrácené půjčky ze Store (restart mimo Standard:
        # clean slate běží dřív než resume, takže by jinak nebylo co vracet a
        # Store by se smazal/osiřel – slider 40014 by zůstal viset). U běžícího
        # systému jsou obě volání no-op.
        await topeni.async_restore_borrow()
        await minimum.async_restore_originals()
        await minimum.async_deactivate()              # vrátí registry (no-op když nic nedrží)
        await zebra.async_disable(resume_heating=False)  # mimo Standard kámen nepouštět
        await dennoc.async_set_enabled(False)         # rozvrh zhasni (časy oken zůstanou)
        await okna.async_set_enabled(False)           # vrať půjčku 40001, master zhasni (časy zůstanou)
        await dovolena.async_set_enabled(False)       # vrať půjčku 40001+40005, master zhasni (interval zůstane)
        await fve.async_set_enabled(False)            # vrať půjčku 40005, master zhasni (prahy/zdroje zůstanou)
        await topeni.async_set_enabled(False)         # vrať půjčku 40014, uvolni 40008, master zhasni (parametry zůstanou)
        coordinator.set_strategy(STRATEGY_EKVITERM)   # neutrální základ
        await coordinator.async_request_refresh()

    # Správce pokojovky: JEN načti originál ze Store (+ jednorázová migrace ze Store
    # Oken). Nerozhoduje se teď – čeká na settle, až budou přání zákazníků kompletní,
    # jinak by originál vrátil dřív, než se stihnou přihlásit.
    await pokojovka.async_restore()

    # start: buď obnov (ve Standardu), nebo rovnou čistý stůl (mimo Standard)
    if coordinator.is_standard():
        if coordinator.strategy in (STRATEGY_MINIMUM, STRATEGY_BOOST):
            await minimum.async_resume_if_needed()
        await zebra.async_resume_if_needed()
        # Den/noc AŽ po Zebře: bezstavový, jen zreconciluje železo dle rozvrhu
        # (master i časy oken už obnovily switch/time entity při setupu platforem)
        await dennoc.async_resume_if_needed()
        # Okna +/- teploty: obnov případnou nevrácenou půjčku 40001 ze Store a
        # zreconciluj (master/časy/delty už obnovily switch/time/number entity)
        await okna.async_resume_if_needed()
        # Dovolená: obnov případnou nevrácenou půjčku 40001+40005 ze Store a zreconciluj
        await dovolena.async_resume_if_needed()
    else:
        await _clean_slate()

    # FVE se rozbíhá VŽDY (i mimo Standard): nasadí odběry zdrojů + tik, ať mirror
    # sensory a klouzavý průměr žijí. Vlastní přepočet (boost) si Standard hlídá
    # uvnitř async_evaluate; mimo Standard nebo s vyplým masterem jen vrátí/nesahá.
    await fve.async_resume_if_needed()

    # Topení podle přebytků taky VŽDY: nasadí odběr zdroje + tik, ať bilance žije.
    # Vlastní převzetí si Standard+Ekviterma hlídá uvnitř async_evaluate; mimo ně
    # nebo s vyplým masterem jen vrátí půjčku / nesahá.
    await topeni.async_resume_if_needed()

    # Teď jsou přání zákazníků kompletní -> správce pokojovky se smí rozhodnout,
    # jestli půjčku držet dál, nebo vrátit uživatelův originál.
    await pokojovka.async_settle()

    # POJISTKA: 40008 platí jen v Typu regulace Standard. Jakmile Acond přepne
    # jinam, uděláme čistý stůl. (Vstup zpět do Standardu neděláme nic – čistý stůl.)
    async def _on_regtype_change(event) -> None:  # noqa: ANN001
        new = event.data.get("new_state")
        if new is None:
            return
        # `unavailable`/`unknown` NENÍ změna typu regulace – jen výpadek čtení
        # (reload Acondu, restart TČ, Modbus). Čistý stůl by smazal všechny mastery
        # kvůli přechodnému výpadku; dělá se jen při ČITELNÉ hodnotě != Standard.
        # (Zrcadlí benevolenci is_standard(): None -> True.)
        if new.state in ("unknown", "unavailable", "", None):
            return
        if new.state != REG_TYPE_STANDARD:
            await _clean_slate()

    entry.async_on_unload(
        async_track_state_change_event(hass, [ACOND_REG_TYPE], _on_regtype_change)
    )

    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data:
            if data.get("fve") is not None:
                data["fve"].shutdown()
            if data.get("topeni") is not None:
                data["topeni"].shutdown()
            if data.get("dovolena") is not None:
                data["dovolena"].shutdown()
            if data.get("okna") is not None:
                data["okna"].shutdown()
            if data.get("profily") is not None:
                data["profily"].shutdown()
            if data.get("pokojovka") is not None:
                data["pokojovka"].shutdown()
            if data.get("dennoc") is not None:
                data["dennoc"].shutdown()
            if data.get("zebra") is not None:
                data["zebra"].shutdown()
            if data.get("min") is not None:
                data["min"].shutdown()
            if data.get("run") is not None:
                data["run"].shutdown()
            if data.get("stats") is not None:
                await data["stats"].async_stop()
    return ok


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
