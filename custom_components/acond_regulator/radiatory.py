"""R-Ekviterma – vrstva pod Ekvitermou pro radiátory se silným čerpadlem BEZ AKU.

PROČ
----
Rychlá soustava (radiátory, málo vody) a silné čerpadlo: Acond po startu šlápne
naplno, zpátečka i místnost letí nahoru, kompresor vypne na +2 °C a cvaká
(simulace: 25–40 startů denně). Když je ztráta domu menší než minimální výkon,
musí se přebytek někam uložit – R ho ukládá do hmoty domu: dlouhé běhy, dlouhé
pauzy, pár startů denně.

JAK (zamčeno 9.–10. 11. 2026, viz const.py „R-Ekviterma")
----------------------------------------------------------
- Výkon řídí STROP 40014 (zápis platí do minuty). Start vždy z minima.
- Rampa: po R_PERIODA_S přidej krok, „když není brzda". Brzda = zpátečka dosáhla
  křivky (mimo dotápění) nebo místnost dosáhla cíle -> uber 2 kroky. Jen rychlý
  růst místnosti -> strop drž.
- 40008: při rampě nahoru návnada (skutečná + 1 -> Acond tlačí do stropu), při
  brzdě křivka. Vždy honička: nikdy pod skutečná − 1,5, takže Acond na +2 sám
  nevypne.
- Pásmo: nad cíl + Pauza nad -> pauza KAMENEM NETOPIT (podle Režimu vypínání: VYP /
  Léto / Útlum – uživatel si tak volí i TUV a protáčení). Pod cíl − Start pod ->
  kámen Topit se stropem na minimu. Pauzu, kterou přebil jiný kámen (uživatel,
  Zebra, Den/noc), R nikdy sám neukončí.
- Po TUV/odmrazu: z minima, v dotápění z posledního stropu − krok. Hned.
- Odchod z R (vypínač, jiná strategie, Bez MaR, mimo Standard) vrátí slider.

PŘEDNOSTI
---------
Kámen (suspend_write), útlum Netopit, FVE Topení (topeni_owns), léto, vypnutý
zápis -> R mlčí. Coordinator pod R nezapisuje 40008 (r_owns), výjimkou je útlum.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store

from .const import (
    ACOND_BIT_DEFROST,
    ACOND_BIT_TUV,
    ACOND_CAPACITY,
    ACOND_COMP_BIT,
    ACOND_HP_ON,
    ACOND_INDOOR,
    ACOND_RETURN_ACT,
    ACOND_RETURN_READBACK,
    ACOND_RPM,
    ACOND_SUMMER,
    ACOND_TARGET,
    BOOST,
    DOMAIN,
    MAX_RETURN,
    R_DOTAP,
    R_HONICKA_REZERVA,
    R_KROK_DOLU,
    R_KROK_NAHORU,
    R_MIN_BEH_S,
    R_MIN_PAUZA_S,
    R_NAVNADA,
    R_PAUZA_DEFAULT,
    R_PERIODA_S,
    R_RUST_BRZDA,
    R_RUST_MIN_S,
    R_RUST_OKNO_S,
    R_START_DEFAULT,
    R_STOP_DIFF,
    R_STORE_VERSION,
    R_TIK_S,
    STRATEGY_EKVITERM,
    signal_r_updated,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN = ("unknown", "unavailable", "", None)

STAV_VYP = "vypnuto"
STAV_JINA = "jiná strategie"
STAV_CEKA = "čeká"
STAV_BEH = "běh"
STAV_DOTAP = "dotápění"
STAV_PAUZA = "pauza"
STAV_START = "start"
STAV_TUV = "TUV/odmraz"
STAV_FVE = "vede FVE Topení"


class RadiatoryController:
    """Smyčka R-Ekvitermy. Zapíná ji switch.mar_ekviterma_radiatory."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, run, seq) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.run = run
        self.seq = seq
        self._store: Store = Store(hass, R_STORE_VERSION, f"{DOMAIN}_radiatory_{entry.entry_id}")

        self.enabled = False
        self.pauza_nad = R_PAUZA_DEFAULT
        self.start_pod = R_START_DEFAULT

        self._held = False                 # máme půjčený slider 40014
        self._orig_cap: float | None = None
        self._paused = False
        self._pause_since = 0.0
        self._run_since = 0.0
        self._last_step = 0.0
        self._was_running: bool | None = None
        self._frozen = False               # běží TUV/odmraz
        self._last_run_cap: float | None = None
        self._room = deque()               # (monotonic, °C) za poslední hodinu
        self._busy = False
        self._unsubs: list = []

        # diagnostika (atributy vypínače, budoucí graf / CSV)
        self.stav = STAV_VYP
        self.duvod: str | None = None
        self.rust: float | None = None
        self.cil_krivka: float | None = None
        self.zadana: float | None = None
        self.acond_vypnul = 0

    # ------------------------------------------------------------------ #
    # Veřejné
    # ------------------------------------------------------------------ #
    def as_attr(self) -> dict:
        return {
            "aktivni": self.enabled,
            "stav": self.stav,
            "brzda": self.duvod,
            "strop": self._num(ACOND_CAPACITY) if self._held else None,
            "cil_z_krivky": self.cil_krivka,
            "zapsana_zadana": self.zadana,
            "rust_mistnosti": self.rust,
            "pauza_nad": self.pauza_nad,
            "start_pod": self.start_pod,
            "acond_vypnul": self.acond_vypnul,
        }

    def veta(self) -> str | None:
        """Přípona věty do Stavu regulace. None, když R neběží."""
        if not self.enabled or self.stav in (STAV_VYP, STAV_JINA):
            return None
        cil = self.coordinator.room_set()
        cz = lambda v: "—" if v is None else f"{float(v):.1f}".replace(".", ",")  # noqa: E731
        if self.stav == STAV_PAUZA and cil is not None:
            return f"R-Ekviterma · pauza, start pod {cz(cil - self.start_pod)} °C"
        if self.stav in (STAV_BEH, STAV_DOTAP) and cil is not None:
            s = f"R-Ekviterma · {self.stav}, pauza od {cz(cil + self.pauza_nad)} °C"
            return s + (f" (brzda: {self.duvod})" if self.duvod else "")
        return f"R-Ekviterma · {self.stav}"

    async def async_set_enabled(self, value: bool) -> None:
        self.enabled = bool(value)
        if not self.enabled:
            await self.async_release()
        await self._tick()

    async def async_apply_config(self) -> None:
        """Změna čísla v UI (number) -> přepočti hned."""
        await self._tick()

    def set_pauza(self, value: float) -> None:
        self.pauza_nad = float(value)

    def set_start(self, value: float) -> None:
        self.start_pod = float(value)

    async def async_load(self) -> None:
        """Načti půjčku ze Store (před čistým stolem, ať je co vracet)."""
        data = await self._store.async_load()
        if data and data.get("held"):
            self._held = True
            self._orig_cap = data.get("orig")
        if data:
            self.acond_vypnul = int(data.get("acond_vypnul") or 0)

    async def async_start(self) -> None:
        """Po startu HA (po platformách a pokojovce): nasaď smyčku."""
        self._unsubs.append(async_track_time_interval(
            self.hass, self._on_time, timedelta(seconds=R_TIK_S)))
        self._unsubs.append(async_track_state_change_event(
            self.hass,
            [ACOND_COMP_BIT, ACOND_BIT_TUV, ACOND_BIT_DEFROST, ACOND_RETURN_ACT, ACOND_INDOOR],
            self._on_event,
        ))
        self._unsubs.append(self.coordinator.async_add_listener(self._on_coord))
        await self._tick()

    def shutdown(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()

    async def async_release(self) -> None:
        """Odchod z R: vrať uživatelův slider, uvolni 40008 ekvitermě."""
        self.coordinator.r_owns = False
        self.coordinator.r_target = None
        self._paused = False
        if self._held:
            self._held = False
            if self._orig_cap is not None:
                await self._set(ACOND_CAPACITY, self._orig_cap)
            _LOGGER.debug("R-Ekviterma: odchod, slider vrácen na %s", self._orig_cap)
            self._orig_cap = None
            await self._save()
            await self.coordinator.async_request_refresh()
        self.stav = STAV_VYP if not self.enabled else STAV_JINA
        self._notify()

    # ------------------------------------------------------------------ #
    # Smyčka
    # ------------------------------------------------------------------ #
    @callback
    def _on_time(self, _now) -> None:  # noqa: ANN001
        self.hass.async_create_task(self._tick())

    @callback
    def _on_event(self, _event) -> None:  # noqa: ANN001
        self.hass.async_create_task(self._tick())

    @callback
    def _on_coord(self) -> None:
        if not self._busy:
            self.hass.async_create_task(self._tick())

    def _drzi(self) -> bool:
        return (
            self.enabled
            and self.coordinator.strategy == STRATEGY_EKVITERM
            and self.coordinator.is_standard()
        )

    async def _tick(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            await self._krok()
        finally:
            self._busy = False
            self._notify()

    async def _krok(self) -> None:
        if not self._drzi():
            if self._held or self.coordinator.r_owns:
                await self.async_release()
            else:
                self.stav = STAV_VYP if not self.enabled else STAV_JINA
            return

        c = self.coordinator
        if getattr(c, "topeni_owns", False):
            # FVE Topení drží 40008 i 40014 -> uhni; po předání pokračuj z jeho stropu
            c.r_owns = False
            self.stav = STAV_FVE
            return

        if not self._held:
            self._orig_cap = self._num(ACOND_CAPACITY)
            self._held = True
            self._paused = False
            await self._save()
            # převzetí za běhu: strop rovnou na skutečné otáčky (žádný skok), jinak minimum
            lo, _hi = self._rozsah()
            akt = self._num(ACOND_RPM) if self.run.kompresor_bezi is True else None
            if lo is not None:
                await self._set_cap(max(lo, akt) if akt is not None else lo)
            self._last_step = time.monotonic()
            _LOGGER.debug("R-Ekviterma: převzato, slider uživatele %s", self._orig_cap)
        c.r_owns = True

        now = time.monotonic()
        room = self._num(ACOND_INDOOR)
        if room is not None:
            self._room.append((now, room))
            while self._room and now - self._room[0][0] > R_RUST_OKNO_S:
                self._room.popleft()
        self.rust = self._rust(now)

        cil = c.room_set()
        ret = self._num(ACOND_RETURN_ACT)
        data = c.data
        krivka = getattr(data, "return_final", None) if data else None
        self.cil_krivka = krivka
        lo, hi = self._rozsah()
        if None in (cil, ret, room, krivka, lo, hi):
            if not self._paused:
                self.stav = STAV_CEKA
            return

        # --- vlastní pauza R (kámen Netopit podle Režimu vypínání) ---
        if self._paused:
            if self.seq.last_origin != "r":
                # pauzu přebil jiný kámen (uživatel, Zebra, Den/noc) -> už není naše;
                # R ji NIKDY sám neukončí, dál jedou běžné brány níž
                self._paused = False
            else:
                self._was_running = None
                self.duvod = None
                if (
                    not c.suspend_write and c.write_enabled
                    and room <= cil - self.start_pod
                    and now - self._pause_since >= R_MIN_PAUZA_S
                ):
                    await self._set_cap(lo)                   # start z minima
                    self._paused = False
                    self._last_step = now
                    self.seq.fire_start(origin="r")           # kámen Topit
                    self.stav = STAV_START
                    return
                self.stav = STAV_PAUZA
                return

        # R mlčí (nezapisuje), ale drží půjčku
        if (
            not c.write_enabled
            or c.suspend_write
            or c.utlum_pz is not None
            or self._on(ACOND_SUMMER)
            or self._off(ACOND_HP_ON)          # TČ na VYP (Netopit, Den/noc, Zebra)
        ):
            self.stav = STAV_CEKA
            self._was_running = None
            return

        krok_up = (hi - lo) * R_KROK_NAHORU
        krok_dn = (hi - lo) * R_KROK_DOLU
        dot = room < cil - R_DOTAP

        # --- TUV / odmraz: rampa zmrazená, nic nepíšeme ---
        if self._on(ACOND_BIT_TUV) or self._on(ACOND_BIT_DEFROST):
            self._frozen = True
            self.stav = STAV_TUV
            return
        if self._frozen:
            # KONEC TUV/odmrazu: hned z minima, v dotápění z posledního stropu − krok
            self._frozen = False
            if dot and self._last_run_cap is not None:
                await self._set_cap(max(lo, self._last_run_cap - krok_up))
            else:
                await self._set_cap(lo)
            self._last_step = now

        bezi = self.run.kompresor_bezi is True
        if self._was_running is True and not bezi:
            self.acond_vypnul += 1          # vypnul sám (ne pauza, ne kámen, ne TUV)
            await self._save()
        if bezi and self._was_running is not True:
            self._run_since = now
            self._last_step = now
        self._was_running = bezi

        # --- pásmo: nad cíl + Pauza nad -> kámen Netopit ---
        if room >= cil + self.pauza_nad:
            if bezi and now - self._run_since >= R_MIN_BEH_S and not self.seq.busy:
                self._paused = True
                self._pause_since = now
                self._was_running = None
                self.stav = STAV_PAUZA
                self.duvod = None
                await self._set_cap(lo)                       # připrav start z minima
                self.seq.fire_stop(origin="r")
                return
            if not bezi:
                # teplo a stojí (Acond sám, cizí kámen): nelákej start, jen drž křivku
                self.stav = STAV_CEKA
                self.duvod = "místnost nad pásmem"
                await self._write_target(self._clamp(krivka))
                return

        # --- kompresor stojí (po pauze, kámen, Acond sám): start z minima ---
        if not bezi:
            self.stav = STAV_START
            self.duvod = None
            await self._set_cap(lo)
            await self._write_target(self._clamp(max(krivka, ret + BOOST)))
            return

        # --- běh: rampa stropu ---
        tvrda = []
        if ret >= krivka and not dot:
            tvrda.append("zpátečka na křivce")
        if ret >= MAX_RETURN - 1:
            tvrda.append("strop zpátečky")
        if room >= cil:
            tvrda.append("místnost v cíli")
        mekka = self.rust is not None and self.rust > R_RUST_BRZDA
        self.duvod = ", ".join(tvrda) if tvrda else ("růst místnosti" if mekka else None)
        self.stav = STAV_DOTAP if dot else STAV_BEH

        cap = self._num(ACOND_CAPACITY)
        if cap is not None and now - self._last_step >= R_PERIODA_S:
            self._last_step = now
            if tvrda:
                await self._set_cap(max(lo, cap - krok_dn))
            elif not mekka:
                await self._set_cap(min(hi, cap + krok_up))
        cap = self._num(ACOND_CAPACITY)
        if cap is not None:
            self._last_run_cap = cap

        zadana = krivka if (tvrda or mekka) else max(krivka, ret + R_NAVNADA)
        zadana = max(zadana, ret - R_STOP_DIFF + R_HONICKA_REZERVA)    # honička
        await self._write_target(self._clamp(zadana))

    # ------------------------------------------------------------------ #
    # Nástroje
    # ------------------------------------------------------------------ #
    def _rust(self, now: float) -> float | None:
        if len(self._room) < 2:
            return None
        t0, r0 = self._room[0]
        if now - t0 < R_RUST_MIN_S:
            return None
        t1, r1 = self._room[-1]
        return round((r1 - r0) * 3600 / (t1 - t0), 2) if t1 > t0 else None

    def _rozsah(self) -> tuple[float | None, float | None]:
        return self._attr(ACOND_CAPACITY, "min"), self._attr(ACOND_CAPACITY, "max")

    def _clamp(self, v: float) -> float:
        lo = self._attr(ACOND_TARGET, "min")
        hi = self._attr(ACOND_TARGET, "max")
        v = min(v, MAX_RETURN)
        if hi is not None:
            v = min(v, hi)
        if lo is not None:
            v = max(v, lo)
        return round(v, 1)

    async def _set_cap(self, value: float) -> None:
        step = self._attr(ACOND_CAPACITY, "step") or 1.0
        v = round(round(value / step) * step, 1)
        cur = self._num(ACOND_CAPACITY)
        if cur is not None and abs(cur - v) < step / 2:
            return
        await self._set(ACOND_CAPACITY, v)

    async def _write_target(self, value: float) -> None:
        self.zadana = value
        self.coordinator.r_target = value
        readback = self._num(ACOND_RETURN_READBACK)
        if readback is not None and round(readback, 1) == round(value, 1):
            return
        if self.coordinator.suspend_write:       # kámen mohl naskočit mezitím
            return
        await self._set(ACOND_TARGET, value)

    async def _set(self, entity_id: str, value: float) -> None:
        await self.hass.services.async_call(
            "number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True,
        )

    async def _save(self) -> None:
        await self._store.async_save({
            "held": self._held, "orig": self._orig_cap, "acond_vypnul": self.acond_vypnul,
        })

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_r_updated(self.entry.entry_id))

    def _off(self, entity_id: str) -> bool:
        st = self.hass.states.get(entity_id)
        return st is not None and st.state == "off"

    def _on(self, entity_id: str) -> bool:
        st = self.hass.states.get(entity_id)
        return st is not None and st.state == "on"

    def _num(self, entity_id: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            return None

    def _attr(self, entity_id: str, attr: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None:
            return None
        try:
            return float(st.attributes.get(attr))
        except (ValueError, TypeError):
            return None
