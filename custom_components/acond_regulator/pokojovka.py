"""Správce pokojovky – JEDINÝ vlastník `number.acond_40001_t_set_indoor1` (40001).

PROČ EXISTUJE
-------------
Půjč-a-vrať byl původně uvnitř Oken +/- teploty. Jenže o 40001 stojí víc programů
naráz (Okna + FVE Topení – souběh je ŽÁDOUCÍ a bude běžný) a dvojí půjčka rozbije
originál:

    uživatel 21  ->  Okno si půjčí (base=21), zapíše 23
                 ->  FVE Topení si „půjčí" (base=23!), zapíše 25
                 ->  Okno skončí, vrátí 21   (smaže FVE)
                 ->  FVE skončí, vrátí 23    (hodnota, která nikdy nebyla uživatelova)

Uživateli by v registru zůstalo cizí číslo napořád. Správce tomu brání
konstrukcí: originál se bere JEDNOU a vrací se AŽ po odhlášení posledního zájemce.

Nesmí viset na žádném masteru: kdyby žil pod Okny, stačilo by mít Okna vyplá a
pokojovková návnada FVE Topení by tiše nedělala nic.

KONTRAKT
--------
- **první přihlášení** (`async_claim`) -> zachyť `base` = aktuální 40001 (uživatelův
  originál) a ulož do Store;
- **za běhu** -> zapisuj `clamp(base + skládání(přání))`;
- **poslední odhlášení** (`async_release`) -> vrať `base` DOSLOVA a zapomeň.

SKLÁDÁNÍ PŘÁNÍ = **VEZMI VĚTŠÍ** (Pavleho volba)
------------------------------------------------
Okno chce +2 a FVE Topení +2 -> zapíše se +2, NE +4. Obě přání jsou „chci tepleji";
větší z nich uspokojí obě naráz a nedá se tím přetopit. Záporná delta (noční pokles)
prohraje s kladnou – přebytek pokles vyruší, ale výš než na originál nepůjde.

Dovolená ZÁKAZNÍKEM NENÍ: je s Okny i FV programy vzájemně vyloučená (oboustranný
zámek ve switch.py), takže se s nimi nad 40001 nikdy nepotká a půjčuje si sama.

BRÁNA
-----
Jediná nadřazená brána je Typ regulace = Standard (mimo něj je celý MaR hluchý ->
vrať a nesahej). Žádná jiná – správce musí přežít vypnutá Okna i vypnuté FVE.

SLIB: „VRÁTÍME, CO JSI NAPOSLED CHTĚL" (změna proti původnímu slibu Oken)
-------------------------------------------------------------------------
Původně platilo „vrátíme hodnotu z okamžiku první půjčky, doslova, bez detekce
zásahu". Jenže to tiše maže uživatelovu vůli: základ 24, návnada +1, na registru
25; uživatel přetočí na 24,5 a na konci epizody dostane zpátky 24, ne 23,5.

Nově správce POSLOUCHÁ 40001 a porovnává se svým očekáváním (base + přání):
- **sedí** -> byla to naše vlastní ozvěna, nedělej nic;
- **nesedí** -> sáhl uživatel: posuň o ten rozdíl base, ulož ho, a **na registr
  NESAHEJ** (správná hodnota tam už je -> žádné přetahování, žádné blikání).

Vlastní zápisy se odfiltrují samy, protože `_last_written` se nastavuje PŘED
voláním služby, takže očekávání vždycky splní. **Pořadí zachovat!**
Práh `POKOJOVKA_TOL` odděluje úmysl od zaokrouhlení desky.

Naslouchátko dělá ještě druhou práci: vyžádá si přepočet ekvitermy. Coordinator
totiž tiká jednou za 10 minut a na 40001 se sám nedívá – ruční změna požadované
teploty se proto dřív projevila v korekci až s několikaminutovým zpožděním.

MIGRACE
-------
Originál dřív bydlel ve Store Oken. Kdyby uživatel aktualizoval UPROSTŘED běžícího
okna, osiřel by mu tam a v registru by natrvalo zůstala zvýšená teplota. Správce
proto při prvním startu starý klíč jednou přečte, převezme z něj `base` a smaže ho.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACOND_ROOM_SET,
    DOMAIN,
    OKNA_STORE_VERSION,
    POKOJOVKA_RETRY_S,
    POKOJOVKA_STORE_VERSION,
    POKOJOVKA_TOL,
    ROOM_SET_MAX,
    ROOM_SET_MIN,
)

_LOGGER = logging.getLogger(__name__)
_UNKNOWN = ("unknown", "unavailable", "", None)


class PokojovkaOwner:
    """Jediný vlastník 40001. Zákazníci si přihlašují a odhlašují přání (delta)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._store: Store = Store(
            hass, POKOJOVKA_STORE_VERSION, f"{DOMAIN}_pokojovka_{entry.entry_id}"
        )
        # starý domov originálu (migrace jednou při prvním startu)
        self._legacy_store: Store = Store(
            hass, OKNA_STORE_VERSION, f"{DOMAIN}_okna_teploty_{entry.entry_id}"
        )

        self._base: float | None = None          # uživatelův originál (None = nepůjčeno)
        self._claims: dict[str, float] = {}       # zákazník -> přání (°C)
        self._last_written: float | None = None   # co jsme naposled sami zapsali
        self._unsub_retry = None
        self._unsub_room = None                   # naslouchátko na 40001
        self._settled = False                     # po startu: čekáme, až se ozvou zákazníci

    # ------------------------------------------------------------------ #
    # Veřejné čtení
    # ------------------------------------------------------------------ #
    @property
    def base(self) -> float | None:
        """Uživatelův originál, dokud držíme půjčku. None = nepůjčeno."""
        return self._base

    def claim_of(self, key: str) -> float | None:
        return self._claims.get(key)

    def effective_delta(self) -> float | None:
        """Skládání přání = VEZMI VĚTŠÍ. None, když nikdo nic nechce."""
        if not self._claims:
            return None
        return max(self._claims.values())

    def as_attr(self) -> dict:
        return {
            "base": self._base,
            "delta": self.effective_delta(),
            "zakaznici": dict(sorted(self._claims.items())),
        }

    # ------------------------------------------------------------------ #
    # Zákaznické API
    # ------------------------------------------------------------------ #
    async def async_claim(self, key: str, delta: float) -> None:
        """Přihlas/uprav přání zákazníka. Idempotentní."""
        d = round(float(delta), 1)
        if self._claims.get(key) == d:
            return
        self._claims[key] = d
        await self.async_reconcile()

    async def async_release(self, key: str) -> None:
        """Odhlas přání zákazníka. Když byl poslední, vrátí se originál."""
        if key not in self._claims:
            return
        self._claims.pop(key, None)
        await self.async_reconcile()

    # ------------------------------------------------------------------ #
    # Srovnání registru se stavem přání
    # ------------------------------------------------------------------ #
    async def async_reconcile(self) -> None:
        if not self._settled:
            return          # po startu držíme, dokud se zákazníci nepřihlásí (viz settle)

        # brána: mimo Standard je celý MaR hluchý -> vrať a nesahej
        if not self.coordinator.is_standard():
            await self._give_back()
            return

        delta = self.effective_delta()
        if delta is None:
            await self._give_back()
            return

        if self._base is None:
            # čerstvá půjčka: zachyť originál PŘED jakýmkoli zápisem (synchronně)
            base = self._num(ACOND_ROOM_SET)
            if base is None:
                self._schedule_retry()     # 40001 po startu ještě nežije -> zkus později
                return
            self._base = base
            await self._save()
            _LOGGER.debug("Pokojovka: půjčka base=%s (zákazníci %s)", base, self._claims)

        await self._write(self._clamp(self._base + delta))

    async def _give_back(self) -> None:
        """Vrať originál (držíme-li půjčku) a zapomeň. Přání zůstávají – zákazníci
        si je odhlásí sami; po návratu do Standardu se půjčka obnoví."""
        if self._base is None:
            return
        await self._write(self._clamp(self._base))
        _LOGGER.debug("Pokojovka: vráceno base=%s", self._base)
        self._base = None
        self._last_written = None
        await self._clear_store()

    # ------------------------------------------------------------------ #
    # Start / obnova
    # ------------------------------------------------------------------ #
    async def async_restore(self) -> None:
        """Načti originál ze Store (+ jednorázová migrace ze Store Oken).
        NEREKONCILUJE – to udělá až `async_settle()`, aby se nevrátil originál
        dřív, než se stihnou přihlásit zákazníci (jinak zbytečný přepis tam a zpět)."""
        data = await self._store.async_load()
        if data and data.get("base") is not None:
            self._base = float(data["base"])
            _LOGGER.debug("Pokojovka: obnoven base=%s", self._base)
            return

        # migrace: originál mohl zůstat po starší verzi ve Store Oken
        legacy = await self._legacy_store.async_load()
        if legacy and legacy.get("base") is not None:
            self._base = float(legacy["base"])
            await self._save()
            await self._legacy_store.async_save({})
            _LOGGER.warning(
                "Pokojovka: převzat originál base=%s ze starého úložiště Oken (migrace)",
                self._base,
            )

    async def async_settle(self) -> None:
        """Volá __init__ AŽ po obnově všech zákazníků: teď je stav přání úplný,
        takže se smí rozhodnout, jestli držet, nebo vrátit."""
        self._settled = True
        if self._unsub_room is None:
            self._unsub_room = async_track_state_change_event(
                self.hass, [ACOND_ROOM_SET], self._on_room_changed
            )
        if self._base is not None:
            delta = self.effective_delta()
            if delta is not None:
                # na registru je (z doby před restartem) base+delta -> přednastav
                # _last_written, ať se zbytečně nepřepisuje stejná hodnota
                self._last_written = self._clamp(self._base + delta)
        await self.async_reconcile()

    def shutdown(self) -> None:
        self._cancel_retry()
        if self._unsub_room is not None:
            self._unsub_room()
            self._unsub_room = None

    # ------------------------------------------------------------------ #
    # Naslouchátko na 40001 (ruční zásah + okamžitý přepočet ekvitermy)
    # ------------------------------------------------------------------ #
    @callback
    def _on_room_changed(self, event) -> None:  # noqa: ANN001
        new = event.data.get("new_state")
        if new is None or new.state in _UNKNOWN:
            return
        try:
            val = float(new.state)
        except (ValueError, TypeError):
            return
        self.hass.async_create_task(self._handle_room(val))

    async def _handle_room(self, val: float) -> None:
        """Dvě práce naráz:
        1) ruční zásah uživatele -> posuň základ (slib „vrátíme, cos naposled chtěl");
        2) vždy si vyžádej přepočet ekvitermy (coordinator tiká 10 min a na 40001
           se sám nedívá -> jinak se změna projeví v korekci až za tu dobu)."""
        if self._base is not None:
            delta = self.effective_delta()
            if delta is not None:
                ocekavane = self._clamp(self._base + delta)
                rozdil = val - ocekavane
                if abs(rozdil) > POKOJOVKA_TOL:
                    # sáhl uživatel: posuň základ, na registr NESAHEJ (už je správně)
                    self._base = round(self._base + rozdil, 1)
                    self._last_written = val
                    await self._save()
                    _LOGGER.debug(
                        "Pokojovka: ruční zásah %s -> nový základ %s",
                        round(rozdil, 1), self._base,
                    )
        await self.coordinator.async_request_refresh()

    # ------------------------------------------------------------------ #
    # Zápis + pomocné
    # ------------------------------------------------------------------ #
    async def _write(self, value: float) -> None:
        """Zápis do 40001 jen při reálné změně. Po zápisu si vyžádá přepočet
        ekvitermy, ať se posun propíše do zpátečky hned, ne až za coordinator tik."""
        v = round(float(value), 1)
        if self._last_written is not None and round(self._last_written, 1) == v:
            return
        self._last_written = v
        await self.hass.services.async_call(
            "number", "set_value",
            {"entity_id": ACOND_ROOM_SET, "value": v},
            blocking=False,
        )
        await self.coordinator.async_request_refresh()

    @staticmethod
    def _clamp(value: float) -> float:
        return round(max(ROOM_SET_MIN, min(ROOM_SET_MAX, value)), 1)

    def _num(self, entity_id: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNKNOWN:
            return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            try:
                return float(st.attributes.get("temperature"))
            except (ValueError, TypeError):
                return None

    def _schedule_retry(self) -> None:
        if self._unsub_retry is not None:
            return

        @callback
        def _fire(now) -> None:  # noqa: ANN001
            # MUSÍ být @callback (jinak běží v executoru a async_create_task tiše selže)
            self._unsub_retry = None
            self.hass.async_create_task(self.async_reconcile())

        self._unsub_retry = async_track_point_in_time(
            self.hass, _fire, dt_util.utcnow() + timedelta(seconds=POKOJOVKA_RETRY_S)
        )

    def _cancel_retry(self) -> None:
        if self._unsub_retry is not None:
            self._unsub_retry()
            self._unsub_retry = None

    async def _save(self) -> None:
        await self._store.async_save({"base": self._base})

    async def _clear_store(self) -> None:
        await self._store.async_save({})
