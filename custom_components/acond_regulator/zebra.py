"""Překryv Zebra – topení s pauzami (Patro 2, časový režim č. 1).

Pro období, kdy i minimální výkon přetápí. Když je v pokoji NATOPENO
(skutečná 30002 > požadovaná 40001, ostře), po intervalu topení pustí
**Vypnout šetrně** (Kámen A) na dobu intervalu pauzy, pak **Zapnout jistě**
(Kámen B) a jede zas. Dokud natopeno není (40001 ≥ 30002), topný interval se
jen zopakuje – nepauzuje se, dům se dohání.

Zebra NENÍ strategie: je ortogonální ke `select.mar_strategie`. Běží nad
Ekvitermou / Stálým / Minimem (ideál je Minimum). Pod BOOST a Bez MaR nedává
smysl – tam ji select sám zhasne. Mimo Standard je hluchá jako celý MaR.

Mechanika stojí celá na hotových kamenech (`SequenceRunner`), takže dědí jejich
bezpečnosti zadarmo: čekání na konec TUV/odmrazu, léto = no-op, doběh kompresoru.
Bug „přednost kamenů“ (Minimum přepisoval boost) je opravený v minimum.py –
bez něj by Zapnout jistě pod Minimem nenaskočil.

Stav (enabled, fáze, konec fáze, počet startů dne) žije ve Store -> přežije
restart HA uprostřed cyklu (`async_resume_if_needed`).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACOND_INDOOR,
    ACOND_ROOM_SET,
    DOMAIN,
    SEQ_BUSY_RETRY_S,
    STRATEGY_EKVITERM,
    STRATEGY_MINIMUM,
    STRATEGY_STALY,
    ZEBRA_DEFAULT_HEAT,
    ZEBRA_DEFAULT_PAUSE,
    ZEBRA_MAX_STARTS_PER_DAY,
    ZEBRA_STORE_VERSION,
    signal_zebra_updated,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN = ("unknown", "unavailable", "", None)

# strategie, nad kterými má Zebra smysl (jinak jen „čeká“, nepauzuje)
_COMPATIBLE = (STRATEGY_EKVITERM, STRATEGY_STALY, STRATEGY_MINIMUM)

STATE_IDLE = "idle"
STATE_HEAT = "heat"
STATE_PAUSE = "pause"


class ZebraController:
    """Automat topení↔pauza. Pouští kameny na hranicích intervalů."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, seq,
                 dennoc=None) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.seq = seq
        self._dennoc = dennoc               # Den/noc (osa A) – háček „smí se teď topit?"
        self._store: Store = Store(hass, ZEBRA_STORE_VERSION, f"{DOMAIN}_zebra_{entry.entry_id}")

        self.enabled = False
        self.state = STATE_IDLE
        self.heat_min = float(ZEBRA_DEFAULT_HEAT)
        self.pause_min = float(ZEBRA_DEFAULT_PAUSE)
        self._phase_ends = None            # datetime konce aktuální fáze
        self._unsub_timer = None
        self._starts_today = 0
        self._starts_day: date | None = None
        # Den/noc zavřel okno -> Zebra uspána (kameny na hranicích okna pouští
        # Den/noc, ne Zebra). Runtime příznak; po restartu ho srovná Den/noc reconcile.
        self._suspended_by_window = False

    # ------------------------------------------------------------------ #
    # Čtení pro okno (mar_stav)
    # ------------------------------------------------------------------ #
    @property
    def phase_label(self) -> str | None:
        if not self.enabled:
            return None
        if self.state == STATE_HEAT:
            return "topení"
        if self.state == STATE_PAUSE:
            return "pauza"
        return None

    @property
    def phase_ends_iso(self) -> str | None:
        return self._phase_ends.isoformat() if self._phase_ends else None

    @property
    def starts_today(self) -> int:
        self._maybe_reset_day()
        return self._starts_today

    @property
    def pausing(self) -> bool:
        """Zebra právě drží PAUZU (pro háček „smí se teď topit?" v topení podle
        přebytků). Uspaná oknem Den/noc se nepočítá – tam říká „ne" už Den/noc."""
        return self.enabled and self.state == STATE_PAUSE and not self._suspended_by_window

    def as_attr(self) -> dict:
        """Kompaktní stav do atributu okna (schéma 'prekryvy')."""
        return {
            "nazev": "Zebra",
            "aktivni": self.enabled,
            "faze": self.phase_label,
            "konec_faze": self.phase_ends_iso,
            "starty_dnes": self.starts_today,
        }

    # ------------------------------------------------------------------ #
    # Ladění intervalů (volají number entity)
    # ------------------------------------------------------------------ #
    def set_heat(self, minutes: float) -> None:
        self.heat_min = float(minutes)

    def set_pause(self, minutes: float) -> None:
        self.pause_min = float(minutes)

    # ------------------------------------------------------------------ #
    # Zapnutí / vypnutí (volá switch + select při zhasnutí překryvů)
    # ------------------------------------------------------------------ #
    async def async_enable(self) -> None:
        """Zapni překryv. Vstup do HEAT, spusť timer topení. Kámen NENUTÍM –
        buď TČ už jede, nebo si ho uživatel pustil sám, až měl vše nastaveno.
        Zapnutí MIMO okno Den/noc -> naskoč rovnou uspaná (žádné noční retry
        smyčky a matoucí „topení" v okně; probudí ji až otevření okna)."""
        if self.enabled:
            return
        self.enabled = True
        if not self._may_heat_now():
            self._suspended_by_window = True
            self.state = STATE_IDLE
            self._phase_ends = None
            await self._save()
            self._notify()
            _LOGGER.debug("Zebra zapnuta mimo okno Den/noc -> uspána do otevření")
            return
        self._suspended_by_window = False
        self._enter_heat()
        await self._save()
        _LOGGER.debug("Zebra zapnuta -> HEAT %s min", self.heat_min)

    async def async_disable(self, resume_heating: bool = True) -> None:
        """Vypni překryv. Když jsme byli v pauze (TČ zhasnuté Zebrou) a
        resume_heating, pusť Zapnout jistě, ať uživatele nenecháme studeného."""
        was_pause = self.state == STATE_PAUSE
        self._cancel_timer()
        self.enabled = False
        self.state = STATE_IDLE
        self._phase_ends = None
        self._suspended_by_window = False
        await self._save()
        if resume_heating and was_pause and self.coordinator.is_standard():
            self.seq.fire_start()
        self._notify()
        _LOGGER.debug("Zebra vypnuta (byla pauza=%s, resume=%s)", was_pause, resume_heating)

    # ------------------------------------------------------------------ #
    # Den/noc (osa A) uspává/probouzí Zebru na hranicích okna
    # ------------------------------------------------------------------ #
    async def async_window_closed(self) -> None:
        """Den/noc zavřel okno: Zebra přestane cyklovat. Kámen (Vypnout šetrně)
        pouští Den/noc SÁM – tady jen zruš časovač a zapamatuj uspání. Bez uspání
        by Zebře doběhla fáze v noci a pustila by Zapnout jistě mimo okno."""
        if not self.enabled or self._suspended_by_window:
            return
        self._cancel_timer()
        self._suspended_by_window = True
        self._phase_ends = None
        await self._save()
        self._notify()
        _LOGGER.debug("Zebra uspána (Den/noc zavřel okno)")

    async def async_window_opened(self) -> None:
        """Den/noc otevřel okno: byla-li Zebra uspána oknem, naskoč čerstvě do
        TOPENÍ (ne doprostřed staré pauzy). Kámen (Zapnout jistě) pustil Den/noc."""
        if not self.enabled or not self._suspended_by_window:
            return
        self._suspended_by_window = False
        self._enter_heat()
        await self._save()
        _LOGGER.debug("Zebra probuzena (Den/noc otevřel okno) -> HEAT")

    # ------------------------------------------------------------------ #
    # Obnova po restartu
    # ------------------------------------------------------------------ #
    async def async_resume_if_needed(self) -> None:
        data = await self._store.async_load()
        if not data or not data.get("enabled"):
            return
        self.enabled = True
        self.state = data.get("state", STATE_HEAT)
        self._starts_today = int(data.get("starts_today", 0))
        sd = data.get("starts_day")
        self._starts_day = date.fromisoformat(sd) if sd else None
        ends = data.get("phase_ends")
        self._phase_ends = dt_util.parse_datetime(ends) if ends else None
        # heat_min/pause_min doplnily number entity při restore (jsou už nastavené)

        if self.state == STATE_IDLE or self._phase_ends is None:
            # nekonzistentní uložení -> naskoč čistě do HEAT
            self._enter_heat()
            await self._save()
            return

        now = dt_util.utcnow()
        if self._phase_ends <= now:
            # hranice proběhla během výpadku -> vyhodnoť ji hned
            self._notify()
            await self._on_boundary()
        else:
            self._arm_timer(self._phase_ends)
            self._notify()
        _LOGGER.debug("Zebra obnovena: state=%s, konec=%s", self.state, self._phase_ends)

    def shutdown(self) -> None:
        self._cancel_timer()

    # ------------------------------------------------------------------ #
    # Fáze
    # ------------------------------------------------------------------ #
    def _enter_heat(self) -> None:
        self.state = STATE_HEAT
        self._schedule(self.heat_min)
        self._notify()

    def _enter_pause(self) -> None:
        self.state = STATE_PAUSE
        self._schedule(self.pause_min)
        self._notify()

    async def _on_boundary(self) -> None:
        """Doběhl timer aktuální fáze -> rozhodni další krok."""
        if not self.enabled:
            return
        # běží-li zrovna kámen (třeba dlouhý doběh Vypnout šetrně), nepřekřič ho –
        # odlož přechod, ať se fire_start/fire_stop nezahodí (kámen je mode single)
        if self.seq.busy:
            self._schedule_retry()
            return

        if self.state == STATE_HEAT:
            await self._heat_ended()
        elif self.state == STATE_PAUSE:
            await self._pause_ended()

    async def _heat_ended(self) -> None:
        self._maybe_reset_day()
        if not self._may_heat_now():
            # hrana topení padla přesně mimo povolené okno Den/noc – nepauzuj ani
            # neopakuj naslepo, jen krátce počkej; Den/noc nás beztak uspí.
            self._schedule_retry()
            return
        room_set = self._num(ACOND_ROOM_SET)   # 40001 (jen ČTU)
        room_act = self._num(ACOND_INDOOR)      # 30002

        # „natopeno“ = skutečná ostře větší než požadovaná (30002 > 40001).
        heated = (
            room_set is not None
            and room_act is not None
            and room_act > room_set
        )
        can_pause = (
            heated
            and self._compatible()
            and self._starts_today < ZEBRA_MAX_STARTS_PER_DAY
        )

        if can_pause:
            self.seq.fire_stop()          # Kámen A – Vypnout šetrně
            self._enter_pause()
            _LOGGER.debug("Zebra: natopeno -> pauza %s min", self.pause_min)
        else:
            # dům není natopen (nebo strop startů / nekompatibilní) -> zopakuj topení
            self._enter_heat()
            _LOGGER.debug(
                "Zebra: nepauzuji (natopeno=%s, kompat=%s, starty=%s) -> další topení",
                heated, self._compatible(), self._starts_today,
            )
        await self._save()

    async def _pause_ended(self) -> None:
        self._maybe_reset_day()
        if not self._may_heat_now():
            # konec pauzy padl MIMO povolené okno Den/noc – NEPOUŠTĚJ Zapnout jistě
            # (nesmíme topit v „noci"). Zůstaň v pauze a zkus později; Den/noc nás
            # brzy uspí a probudí až s otevřením okna do čerstvého topení.
            self._schedule_retry()
            return
        self.seq.fire_start()             # Kámen B – Zapnout jistě
        self._starts_today += 1
        self._enter_heat()
        await self._save()
        _LOGGER.debug("Zebra: konec pauzy -> Zapnout jistě, start #%s", self._starts_today)

    # ------------------------------------------------------------------ #
    # Timer
    # ------------------------------------------------------------------ #
    def _schedule(self, minutes: float) -> None:
        when = dt_util.utcnow() + timedelta(minutes=max(1.0, float(minutes)))
        self._phase_ends = when
        self._arm_timer(when)

    def _schedule_retry(self) -> None:
        when = dt_util.utcnow() + timedelta(seconds=SEQ_BUSY_RETRY_S)
        self._phase_ends = when
        self._arm_timer(when)

    def _arm_timer(self, when) -> None:
        self._cancel_timer()

        @callback
        def _fire(now) -> None:  # noqa: ANN001
            # MUSÍ být @callback: jinak HA spustí callback ve vlákně executoru,
            # kde async_create_task selže -> hranice se nezpracuje, odpočet jede
            # do minusu a Zebra nepauzuje. (Přesně ten bug, co Pavle chytil.)
            self._unsub_timer = None
            self.hass.async_create_task(self._on_boundary())

        self._unsub_timer = async_track_point_in_time(self.hass, _fire, when)

    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    # ------------------------------------------------------------------ #
    # Nástroje
    # ------------------------------------------------------------------ #
    def _compatible(self) -> bool:
        return self.coordinator.strategy in _COMPATIBLE and self.coordinator.is_standard()

    def _may_heat_now(self) -> bool:
        """Háček Den/noc: smí se teď topit dle hodin? Dokud Den/noc není/aktivní,
        vždy True (Zebra cykluje bez omezení)."""
        if self._dennoc is None:
            return True
        return self._dennoc.may_heat_now()

    def _maybe_reset_day(self) -> None:
        today = dt_util.now().date()
        if self._starts_day != today:
            self._starts_day = today
            self._starts_today = 0

    def _num(self, entity_id: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            # 40001 může nést teplotu v atributu (číselník ji dává ve state, ale pro jistotu)
            try:
                return float(st.attributes.get("temperature"))
            except (ValueError, TypeError):
                return None

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_zebra_updated(self.entry.entry_id))

    async def _save(self) -> None:
        await self._store.async_save(
            {
                "enabled": self.enabled,
                "state": self.state,
                "phase_ends": self.phase_ends_iso,
                "starts_today": self._starts_today,
                "starts_day": self._starts_day.isoformat() if self._starts_day else None,
                "heat_min": self.heat_min,
                "pause_min": self.pause_min,
            }
        )
