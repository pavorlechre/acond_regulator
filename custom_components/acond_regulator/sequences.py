"""Dva kameny: START (Zapnout jistě) a STOP (Vypnout šetrně) uvnitř integrace.

Přepis tvých HA skriptů do Pythonu – slepé `delay 3/4 min` nahrazeny čekáním na
primitiv `kompresor_běží` / `potvrzeně_stojí`. Sekvence běží jako úloha na pozadí;
VŽDY doběhne celá (žádné přerušení – půlnasazený boost/stažená zpátečka je horší
než pár minut navíc), re-entrance se ignoruje (mode single). Fáze se hlásí do
mar_stav přes dispatcher, ať v okně vidíš, co kámen zrovna dělá.

Zápis do 40008 během sekvence si coordinator NEROZBÍJÍ: sekvence si na dobu běhu
zvedne `coordinator.suspend_write`, takže ekvitermní zápis se do kamene neplete.
"""

from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    ACOND_BIT_DEFROST,
    ACOND_BIT_TUV,
    ACOND_HP_ON,
    ACOND_RETURN_READBACK,
    ACOND_SEASON_BUTTON,
    ACOND_SUMMER,
    ACOND_SW_TC,
    ACOND_SW_VYP,
    ACOND_TARGET,
    BOOST,
    DOBEH_MAX_S,
    DWELL_S,
    HP_LIMIT,
    MAX_RETURN,
    OFFMODE_DEFAULT,
    OFFMODE_LETO,
    OFFMODE_PZ,
    OFFMODE_VYP,
    PZ_TARGET,
    STRATEGY_BEZ_MAR,
    SEASON_COOLDOWN_S,
    SEASON_VERIFY_ATTEMPTS,
    SEASON_VERIFY_POLL_S,
    SEASON_VERIFY_WINDOW_S,
    START_MAX_S,
    TCMODE_DEFAULT,
    TCMODE_SWITCH,
    TUV_STABLE_S,
    TUV_WAIT_S,
    signal_seq_updated,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN = ("unknown", "unavailable", "", None)


class SequenceRunner:
    """Spouští kameny START/STOP. Drží aktuální fázi pro mar_stav."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, run,
                 minimum=None) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.run = run
        self._minimum = minimum          # pro předání kormidla na konci kamene
        self._busy = False
        # Režim vypínání (co znamená „netopit"): VYP / Léto / Útlum PZ. Plní ho
        # OffModeSelect; default VYP = dnešní chování, nic se nemění dokud se nezvolí.
        self.offmode: str = OFFMODE_DEFAULT
        # Provozní režim TČ, který Zapnout jistě zapíná (Pouze TČ / Automatický /
        # Bivalence). Plní ho TcModeSelect; default = dosavadní chování.
        self.tcmode: str = TCMODE_DEFAULT
        # Rollback: hodnota k vrácení na 40008, kdyby kámen spadl / byl přerušen
        # uprostřed (boost nasazený / zpátečka stažená). Po řádném vrácení None.
        self._rollback: float | None = None
        self._season_cooldown_until = 0.0   # monotonic; brání dvojímu pulzu léto/zima
        # veřejné čtení pro mar_stav
        self.active: str | None = None       # "start" / "stop" / None
        self.phase: str | None = None        # lidský text fáze

    @property
    def busy(self) -> bool:
        """Běží zrovna kámen? (Zebra podle toho odkládá přechod na hranici.)"""
        return self._busy

    # ------------------------------------------------------------------ #
    # Spouštěče (z tlačítek) – nikdy neblokují stisk, jedou na pozadí
    # ------------------------------------------------------------------ #
    def fire_start(self) -> None:
        if self._busy:
            _LOGGER.debug("Kámen už běží (%s), START ignoruji", self.active)
            return
        # SYNCHRONNĚ, ještě před vytvořením úlohy: zavře okno TOCTOU (dva spouštěče
        # ve stejné smyčce by jinak oba prošli kontrolou) i závod s frontou
        # ekvitermního zápisu (coordinator by mohl smazat boost).
        self._busy = True
        self.coordinator.suspend_write = True
        self.entry.async_create_background_task(
            self.hass, self._guard(self._seq_start()), "mar_seq_start"
        )

    def fire_stop(self) -> None:
        if self._busy:
            _LOGGER.debug("Kámen už běží (%s), STOP ignoruji", self.active)
            return
        self._busy = True
        self.coordinator.suspend_write = True
        self.entry.async_create_background_task(
            self.hass, self._guard(self._seq_stop()), "mar_seq_stop"
        )

    async def _guard(self, coro) -> None:
        """Obal: busy lock + suspend ekvitermního zápisu + úklid fáze.
        (busy/suspend už zvedl fire_* synchronně; tady idempotentně pro jistotu.)"""
        self._busy = True
        self.coordinator.suspend_write = True
        try:
            await coro
        except asyncio.CancelledError:
            # unload/restart uprostřed sekvence – vrať původní hodnoty best-effort
            self._set_phase(self.active, "přerušeno (restart)")
            await self._try_rollback()
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Sekvence kamene selhala")
            self._set_phase(None, "selhalo")
            await self._try_rollback()
        finally:
            self.coordinator.suspend_write = False
            self._busy = False
            self.active = None
            async_dispatcher_send(self.hass, signal_seq_updated(self.entry.entry_id))

    # ------------------------------------------------------------------ #
    # KÁMEN B – Zapnout jistě (START)
    # ------------------------------------------------------------------ #
    async def _seq_start(self) -> None:
        # Bez MaR: MaR neřídí, takže Topit nic nespouští – nevíme, co bude dál
        # (Acond podle svých programů, ruční zpátečka, nebo se topit nemá).
        # Případnou pauzu z Netopit ukončí uživatel sám.
        if self.coordinator.strategy == STRATEGY_BEZ_MAR:
            self._set_phase("start", "Bez MaR — TČ neřídím, Topit nic nespouští")
            await asyncio.sleep(6)
            return

        # útlum PZ zruš VŽDY jako první (i před early-returny) – ať se ekviterma
        # rozjede, i kdyby kompresor zrovna běžel kvůli TUV
        self.coordinator.set_utlum(None)

        # léto -> zima: „Topit" = chci topit = zima. Park do léta jsme mohli udělat
        # my (běžel časový režim). Verify smyčka; když přepnutí selže, PŘERUŠ –
        # nespouštět boost naslepo, když nevíme, jestli jsme fakt v zimě.
        if self._on(ACOND_SUMMER) is True:
            self._set_phase("start", "přepínám z léta do zimy")
            if not await self.async_ensure_season(want_summer=False):
                self._set_phase("start", "nepřepnuto na zimu — přerušeno")
                return

        if self.run.kompresor_bezi is True:
            self._set_phase("start", "kompresor už běží — netřeba")
            await asyncio.sleep(6)
            return

        # PODEZŘELÝ ORIGINÁL: po přerušeném STOPu může na registru viset zaparkovaná
        # hodnota (~HP_LIMIT). Vrátit ji na konci by kompresor hned zhasilo -> místo
        # ní se jako základ vezme ekvitermní cíl z coordinatoru.
        t_orig = self._sane_orig(self._num(ACOND_RETURN_READBACK))
        mode_switch = TCMODE_SWITCH.get(self.tcmode, ACOND_SW_TC)
        self._set_phase("start", f"zapínám {self.tcmode}, nasazuji boost")
        await self._switch_on(mode_switch)                 # shodí VYP samo
        if t_orig is not None:
            # clamp na max registru: boost přes horní mez by number.set_value odmítl
            # výjimkou a celý kámen by skončil „selhalo"
            await self._write(self._boost_value(t_orig))
            self._rollback = t_orig

        self._set_phase("start", "čekám, až naskočí kompresor")
        ok = await self._wait(lambda: self.run.kompresor_bezi is True, START_MAX_S)
        if not ok:
            # nenaskočil (porucha / léto / hned nasycený) – nedrž boost donekonečna
            self._set_phase("start", "kompresor nenaskočil — vracím původní zpátečku")
            if t_orig is not None:
                await self._write(t_orig)
            self._rollback = None
            return

        # boost přežije předání TUV: počkej, až NENÍ TUV, pak dwell
        if self._on(ACOND_BIT_TUV) is True:
            self._set_phase("start", "kompresor běží, čekám na konec TUV")
            await self._wait(lambda: self._on(ACOND_BIT_TUV) is not True, TUV_WAIT_S)
        self._set_phase("start", "držím boost (dwell)")
        await asyncio.sleep(DWELL_S)

        # KONEC KAMENE: vrať původní zpátečku – NEBO rovnou cíl Minima, je-li aktivní.
        # (Pavleho postup: kámen má přednost, ale na konci hned předá kormidlo Minimu,
        #  ať PZ nezůstane na původní hodnotě pod hysterezí a kompresor nezhasne.)
        final, from_min = self._handoff_target(t_orig)
        self._set_phase("start", "předávám Minimu" if from_min else "vracím původní zpátečku")
        if final is not None:
            await self._write(final)
        self._rollback = None
        self._set_phase("start", "hotovo")

    def _sane_orig(self, t_orig: float | None) -> float | None:
        """Původní zpátečka k vrácení. Chybí-li, nebo je podezřele nízko
        (<= HP_LIMIT = zaparkovaná po přerušeném STOPu), vezmi místo ní ekvitermní
        cíl z coordinatoru — ekviterma je vždy rozumná kotva."""
        if t_orig is not None and t_orig > HP_LIMIT:
            return t_orig
        data = self.coordinator.data
        rf = getattr(data, "return_final", None) if data else None
        return rf if rf is not None else t_orig

    def _boost_value(self, t_orig: float) -> float:
        """Boost ořezaný na horní mez registru 40008 (a bezpečnostní MAX_RETURN)."""
        target = t_orig + BOOST
        st = self.hass.states.get(ACOND_TARGET)
        if st is not None:
            try:
                target = min(target, float(st.attributes.get("max")))
            except (ValueError, TypeError):
                pass
        return min(target, MAX_RETURN)

    async def _try_rollback(self) -> None:
        """Best-effort vrácení 40008 po pádu/přerušení kamene (dřív jen slib
        v komentáři). Když selže i tohle (entita pryč), doléčí to ekviterma
        do jednoho tiku."""
        if self._rollback is None:
            return
        value, self._rollback = self._rollback, None
        try:
            await self._write(value)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Rollback zpátečky se nepovedl (entita nedostupná?)")

    def _handoff_target(self, t_orig: float | None) -> tuple[float | None, bool]:
        """Na konci kamene: cíl Minima (skutečná − 1,2), je-li aktivní, jinak původní."""
        if self._minimum is not None:
            wanted = self._minimum.wanted_target()
            if wanted is not None:
                self._minimum.note_external_write(wanted)   # srovnej brzdu + okno
                return wanted, True
        return t_orig, False

    # ------------------------------------------------------------------ #
    # KÁMEN A – Vypnout šetrně (STOP)
    # ------------------------------------------------------------------ #
    async def _seq_stop(self) -> None:
        # --- Útlum PZ: žádná změna módu. Řekni coordinatoru, ať drží zpátečku na
        #     PZ_TARGET; kompresor pro topení nenaskočí (zpátečka nad cílem), ale
        #     TČ zůstane v normálním módu -> TUV a oběh jedou dál. ---
        if self.offmode == OFFMODE_PZ:
            self._set_phase("stop", f"útlum: držím zpátečku {PZ_TARGET:.0f} °C")
            self.coordinator.set_utlum(PZ_TARGET)
            # zapiš hned, nečekej na 10min tik coordinatoru. Platí pod každou
            # strategií včetně Minima a Bez MaR (tam je to jediný zápis –
            # coordinator pod Bez MaR nezapisuje, pauzu ukončí uživatel sám).
            await self._write(PZ_TARGET)
            await asyncio.sleep(6)
            return

        # --- Léto: netop, ale ohřívej TUV. Přepni do léta (verify smyčka), žádné
        #     VYP ani stahování zpátečky. ---
        if self.offmode == OFFMODE_LETO:
            if self._on(ACOND_SUMMER) is True:
                self._set_phase("stop", "už v letním režimu — netřeba")
                await asyncio.sleep(6)
                return
            self._set_phase("stop", "přepínám do letního režimu")
            await self.async_ensure_season(want_summer=True)
            # i kdyby přepnutí selhalo, notifikace už letěla – nezůstat viset
            self._set_phase("stop", "hotovo")
            return

        # --- VYP (default): tvrdé vypnutí, antizámraz běží dál ---
        if self._on(ACOND_HP_ON) is not True:
            self._set_phase("stop", "TČ už stojí — netřeba")
            await asyncio.sleep(6)
            return

        # počkej na konec TUV/odmraz (oba off ≥ TUV_STABLE_S), jinak abort
        if self._on(ACOND_BIT_TUV) is True or self._on(ACOND_BIT_DEFROST) is True:
            self._set_phase("stop", "čekám na konec TUV/odmrazování")
            ok = await self._wait(
                lambda: self._on(ACOND_BIT_TUV) is not True
                and self._on(ACOND_BIT_DEFROST) is not True,
                TUV_WAIT_S,
                stable_s=TUV_STABLE_S,
            )
            if not ok:
                self._set_phase("stop", "přerušeno — TUV/odmraz neskončilo (90 min)")
                return

        t_orig = self._num(ACOND_RETURN_READBACK)
        lowered = False
        if t_orig is not None and t_orig > HP_LIMIT:
            self._set_phase("stop", "stahuji zpátečku, kompresor dojede")
            await self._write(HP_LIMIT)
            lowered = True
            self._rollback = t_orig

        self._set_phase("stop", "čekám na doběh kompresoru, větráku a čerpadla")
        stopped = await self._wait(lambda: self.run.potvrzene_stoji is True, DOBEH_MAX_S)
        if not stopped:
            # strop doběhu vypršel – přesto přepni VYP (jako slepý delay ve skriptu,
            # jen s podmínkou napřed); antizámraz běží dál pod VYP
            _LOGGER.debug("Doběh nepotvrzen do %ss, přepínám VYP přesto", DOBEH_MAX_S)

        self._set_phase("stop", "přepínám TČ na VYP")
        await self._switch_on(ACOND_SW_VYP)

        if lowered and t_orig is not None:
            self._set_phase("stop", "vracím původní zpátečku")
            await self._write(t_orig)
        self._rollback = None
        self._set_phase("stop", "hotovo")

    # ------------------------------------------------------------------ #
    # Nástroje
    # ------------------------------------------------------------------ #
    async def _wait(self, cond, timeout_s: float, stable_s: float = 0, poll_s: float = 2.0) -> bool:
        """Čekej, až cond() platí (volitelně stabilně stable_s). True=splněno, False=timeout."""
        start = time.monotonic()
        stable_since: float | None = None
        while True:
            if time.monotonic() - start > timeout_s:
                return False
            try:
                ok = bool(cond())
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                if stable_s <= 0:
                    return True
                now = time.monotonic()
                if stable_since is None:
                    stable_since = now
                elif now - stable_since >= stable_s:
                    return True
            else:
                stable_since = None
            await asyncio.sleep(poll_s)

    async def _write(self, value: float) -> None:
        await self.hass.services.async_call(
            "number", "set_value",
            {"entity_id": ACOND_TARGET, "value": round(float(value), 1)},
            blocking=True,
        )

    async def _switch_on(self, entity_id: str) -> None:
        await self.hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True
        )

    async def _notify(self, title: str, message: str) -> None:
        """Uživatelská hláška (např. léto u Zapnout jistě), ať to není ticho."""
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {"title": f"MaR – {title}", "message": message,
             "notification_id": f"mar_seq_{self.entry.entry_id}"},
            blocking=False,
        )

    def set_offmode(self, value: str) -> None:
        """Plní OffModeSelect: co znamená „netopit" (VYP / Léto / Útlum PZ)."""
        self.offmode = value

    def set_tcmode(self, value: str) -> None:
        """Plní TcModeSelect: který provozní režim TČ kámen Zapnout jistě zapíná
        (Pouze TČ / Automatický / Bivalence)."""
        self.tcmode = value

    # ------------------------------------------------------------------ #
    # Sezóna léto/zima (pro Léto větev kamenů) – toggle + verify smyčka
    # ------------------------------------------------------------------ #
    async def async_ensure_season(self, want_summer: bool) -> bool:
        """Dotáhni sezónu do cíle a OVĚŘ. Vrací True = potvrzeno, False = vzdáno.

        `_async_set_season` je atomický toggle s cooldownem (pulzne jen při reálné
        změně, v cooldownu se sám neprovede). Tahle smyčka ho volá opakovaně a
        mezitím ČTE bit_10, dokud stav nesedne (nebo nevyprší strop). Opakované
        volání je bezpečné (idempotence platí, dokud je read-back čerstvý – proto
        okno >= 2×poll). Kámen na False MUSÍ přerušit a NEpokračovat jako by byla zima."""
        for _ in range(SEASON_VERIFY_ATTEMPTS):
            await self._async_set_season(want_summer)      # pulzne jen když je bit_10 jiný
            waited = 0.0
            while waited < SEASON_VERIFY_WINDOW_S:
                await asyncio.sleep(SEASON_VERIFY_POLL_S)
                waited += SEASON_VERIFY_POLL_S
                st = self._on(ACOND_SUMMER)                # True/False/None(nečitelné)
                if st is None:
                    continue
                if st == want_summer:
                    return True                            # potvrzeno v cílové sezóně
            # okno vyprchalo, sezóna nesedí -> další pokus (cooldown už mezitím spadl)
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {
                "title": "MaR – přepnutí sezóny selhalo",
                "message": (
                    f"Nepodařilo se přepnout na {'léto' if want_summer else 'zimu'} "
                    f"do {SEASON_VERIFY_ATTEMPTS} pokusů. TČ zůstává v původní sezóně."
                ),
                "notification_id": "mar_season_switch_failed",   # stabilní -> nezaplaví lištu
            },
            blocking=False,
        )
        return False

    async def _async_set_season(self, want_summer: bool) -> None:
        """Atomický toggle léto/zima. Čte bit_10, pulz posílá jen při reálné změně.
        Cooldown brání dvojímu pulzu, než Acond stihne poll (15 s) a read-back se
        obnoví – jinak by druhý pulz přehodil sezónu zpět."""
        if time.monotonic() < self._season_cooldown_until:
            return
        is_summer = self._on(ACOND_SUMMER)
        if is_summer is None:
            return                                          # Acond nečitelný – neriskovat slepý toggle
        if is_summer == want_summer:
            return                                          # už ve správné sezóně
        await self.hass.services.async_call(
            "button", "press", {"entity_id": ACOND_SEASON_BUTTON}, blocking=True,
        )
        self._season_cooldown_until = time.monotonic() + SEASON_COOLDOWN_S

    def _on(self, entity_id: str) -> bool | None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNKNOWN:
            return None
        return st.state == "on"

    def _num(self, entity_id: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            return None

    def _set_phase(self, active: str | None, text: str | None) -> None:
        self.active = active
        self.phase = text
        _LOGGER.debug("Fáze kamene [%s]: %s", active, text)
        async_dispatcher_send(self.hass, signal_seq_updated(self.entry.entry_id))
