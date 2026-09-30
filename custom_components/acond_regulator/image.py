"""PNG image entity MaR: graf ekvitermní křivky a tabulka statistiky.

Apexcharts má jen časovou osu; křivku zpátečka-vs-venkovní kreslíme sami.
PNG (Pillow je v jádře HA vždy) — image/svg+xml HA frontend do <img> nervuje.
Ukazuje 5 bodů, ploché prodloužení za kraje a aktuální provozní bod.

Druhá entita je snímek tabulky statistiky pro sdílení do skupiny. HA nativně
„vyfoť tuhle kartu" neumí (jen export dat do CSV v panelu Historie), takže se
kreslí serverem — a to je i lepší: vypadá stejně na každém telefonu, nezávisí
na šířce displeje ani na světlém/tmavém režimu a nikdy se neusekne.

Snímek se navíc UKLÁDÁ na disk do `config/www/mar/`. Důvod je prozaický: nad
image entitou nepustí HA systémové „Uložit obrázek" ani na PC (pravé tlačítko
spolkne more-info dialog), ani na tabletu (krátké i dlouhé podržení otevře
entitu). Soubor ve `www` je dostupný na `/local/mar/…`, což je normální statická
adresa — otevřená v nové záložce se chová jako každý jiný obrázek na webu.
Jméno souboru nese časové razítko, protože `/local` servíruje HA s dlouhou
cache a na fixní jméno by prohlížeč pořád ukazoval první stažený snímek.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    ACOND_STARTS_TOTAL,
    CURVE_X,
    DOMAIN,
    PERIOD_DAY_PREFIX,
    RING_DAYS,
    PERIOD_TODAY,
    PERIOD_WEEK,
    PERIOD_YESTERDAY,
    SNAPSHOT_BASE,
    SNAPSHOT_BASE_DAYS,
    SNAPSHOT_KEEP,
    SNAPSHOT_SUBDIR,
    device_info,
    signal_stats_snapshot,
    signal_stats_snapshot_days,
)
from .coordinator import MarCoordinator
from .stav_png import sestav_radky, vykresli
from .statistics.accumulator import StatisticsAccumulator
from .statistics.table_png import render_days, render_table

# plátno
W, H = 480, 300
L, R, T, B = 52, 462, 22, 250          # plot area

XMIN, XMAX = -16.0, 16.0

# barvy
C_BG = (255, 255, 255)
C_AXIS = (204, 204, 204)
C_GRID = (238, 238, 238)
C_GRIDH = (243, 243, 243)
C_TITLE = (51, 51, 51)
C_TICK = (102, 102, 102)
C_CURVE = (31, 119, 180)
C_NOW = (214, 39, 40)


_FONT_BUNDLED = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Přibalený DejaVu (plná diakritika + →) má přednost; load_default je až nouze.
    for path in (
        _FONT_BUNDLED,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _vdash(d: ImageDraw.ImageDraw, x: float, y0: float, y1: float,
           fill, dash: int = 4, gap: int = 3, width: int = 1) -> None:
    y = y0
    while y < y1:
        d.line([(x, y), (x, min(y + dash, y1))], fill=fill, width=width)
        y += dash + gap


_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# Snímek tabulky: sestavení dat + zápis na disk
# ---------------------------------------------------------------------- #
def _starts_total(hass: HomeAssistant) -> int | None:
    """Životní počet startů z firmwaru (30075). Má ho jen novější Modbus –
    u starší jednotky vyjde None a tabulka vykreslí „–". Žádná podmíněná
    karta, layout se nehne."""
    state = hass.states.get(ACOND_STARTS_TOTAL)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    try:
        return int(float(state.state))
    except (TypeError, ValueError):
        return None


def _period_label(stats: StatisticsAccumulator, period: str) -> str | None:
    """Rozsah dat pod hlavičkou sloupce. Bez něj je poslaný snímek
    nedatovaný a za týden bezcenný."""
    start, end = stats.period_range(period)
    if start is None or end is None:
        return None
    if period == PERIOD_WEEK:
        last = end - dt.timedelta(days=1)
        return f"{start:%d.%m.}\u2013{last:%d.%m.%Y}"
    return f"{start:%d.%m.%Y}"


async def async_build_table_png(
    hass: HomeAssistant, stats: StatisticsAccumulator
) -> bytes:
    """Sestav data v event loopu, kresli v executoru (Pillow je synchronní).

    Sdílí ji image entita i tlačítko, aby snímek na obrazovce a snímek
    v souboru vznikaly z jednoho místa.
    """
    columns = []
    for period, label in (
        (PERIOD_TODAY, "DNES"),
        (PERIOD_YESTERDAY, "VČERA"),
        (PERIOD_WEEK, "7 DNŮ"),
    ):
        attrs = stats.period_attributes(period)
        columns.append(
            {
                "label": label,
                "sub": _period_label(stats, period),
                "incomplete": period == PERIOD_TODAY,
                "available": bool(attrs.get("dostupne")),
                "data": attrs,
            }
        )

    meta = {
        "generated": dt_util.now().strftime("%d.%m.%Y %H:%M"),
        "starts_total": _starts_total(hass),
    }
    return await hass.async_add_executor_job(render_table, columns, meta)


_DNY = ("Ne", "Po", "Út", "St", "Čt", "Pá", "So")


async def async_build_days_png(
    hass: HomeAssistant, stats: StatisticsAccumulator
) -> bytes:
    """Denní rozpad: dnešek + sedm zavřených dnů vedle sebe.

    V součtu za týden se ztratí, že jeden den bylo pět startů a druhý dvacet —
    a právě ten rozdíl je při porovnávání instalací zajímavější než průměr.

    Pořadí sloupců je stejné jako ve sloupcové tabulce: DNES vlevo, pak VČERA
    a směrem doprava historie. Dvě tabulky s opačným směrem času vedle sebe
    jsou matoucí.

    Dny, které v kruhu ještě nejsou (statistika běží kratší dobu), se vykreslí
    s pomlčkami. Schovat je by bylo horší: takhle je vidět, odkdy se měří.
    """
    columns = []
    for back in range(RING_DAYS):
        period = f"{PERIOD_DAY_PREFIX}{back}"
        attrs = stats.period_attributes(period)
        start, _end = stats.period_range(period)
        if back == 0:
            label = "DNES"
        elif back == 1:
            label = "VČERA"
        else:
            label = _DNY[int(start.strftime("%w"))] if start else "—"
        columns.append(
            {
                "label": label,
                "sub": f"{start:%d.%m.}" if start else None,
                "incomplete": back == 0,
                "available": bool(attrs.get("dostupne")),
                "data": attrs,
            }
        )

    meta = {
        "generated": dt_util.now().strftime("%d.%m.%Y %H:%M"),
        "starts_total": _starts_total(hass),
    }
    return await hass.async_add_executor_job(render_days, columns, meta)


def _write_snapshot(directory: str, png: bytes, stamp: str,
                    base: str = SNAPSHOT_BASE) -> str:
    """Zapiš datovaný snímek + stabilní kopii a prořež archiv. Běží v executoru.

    Stabilní `statistika.png` je jen pro ruční sáhnutí (sdílená složka, záloha);
    na dashboardu se odkazuje datované jméno, jinak by cache `/local` držela
    první stažený snímek na věky.
    """
    os.makedirs(directory, exist_ok=True)
    dated = f"{base}-{stamp}.png"
    with open(os.path.join(directory, dated), "wb") as handle:
        handle.write(png)
    with open(os.path.join(directory, f"{base}.png"), "wb") as handle:
        handle.write(png)

    # razítko je v názvu, takže abecední řazení == chronologické
    archive = sorted(
        name
        for name in os.listdir(directory)
        if name.startswith(f"{base}-") and name.endswith(".png")
    )
    for name in archive[:-SNAPSHOT_KEEP]:
        try:
            os.remove(os.path.join(directory, name))
        except OSError:  # noqa: PERF203
            _LOGGER.debug("Starý snímek %s se nepodařilo smazat", name)
    return dated


async def async_save_snapshot(
    hass: HomeAssistant, stats: StatisticsAccumulator, days: bool = False
) -> str | None:
    """Vyrob snímek a ulož ho do `config/www/mar/`. Vrací adresu `/local/…`.

    `days=False` je sloupcová tabulka (dnes / včera / 7 dnů),
    `days=True` denní rozpad. Oba mají vlastní základ jména, takže se
    v archivu nepřepisují a dají se stáhnout zvlášť.

    Selhání zápisu NENÍ chyba integrace – snímek na obrazovce funguje dál,
    jen se nedá otevřít v prohlížeči. Proto warning a None, ne výjimka.
    """
    if days:
        png = await async_build_days_png(hass, stats)
        base = SNAPSHOT_BASE_DAYS
    else:
        png = await async_build_table_png(hass, stats)
        base = SNAPSHOT_BASE
    stamp = dt_util.now().strftime("%Y%m%d-%H%M%S")
    directory = hass.config.path("www", SNAPSHOT_SUBDIR)
    try:
        name = await hass.async_add_executor_job(
            _write_snapshot, directory, png, stamp, base
        )
    except OSError as err:
        _LOGGER.warning("Snímek statistiky se nepodařilo uložit do %s: %s",
                        directory, err)
        return None
    return f"/local/{SNAPSHOT_SUBDIR}/{name}"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: MarCoordinator = data["coordinator"]
    async_add_entities(
        [
            CurveImage(hass, coordinator, entry),
            StatisticsImage(hass, data["stats"], entry),
            StatisticsDaysImage(hass, data["stats"], entry),
            StavImage(hass, entry),
        ]
    )


class CurveImage(CoordinatorEntity[MarCoordinator], ImageEntity):
    _attr_has_entity_name = True
    _attr_name = "Ekviterm křivka"
    _attr_content_type = "image/png"

    def __init__(self, hass: HomeAssistant, coordinator: MarCoordinator, entry: ConfigEntry) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{entry.entry_id}_krivka_png"
        self._attr_device_info = device_info(entry.entry_id)
        self._attr_image_last_updated = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        ys = list(self.coordinator.curve_y)
        data = self.coordinator.data
        model = getattr(data, "model_temp", None)
        base = getattr(data, "return_base", None)
        final = getattr(data, "return_final", None)
        return await self.hass.async_add_executor_job(self._render, ys, model, base, final)

    # ------------------------------------------------------------------ #
    def _render(self, ys: list[float], model: float | None,
                base: float | None, final: float | None) -> bytes:
        if not ys:
            ys = [30.0] * len(CURVE_X)

        ymin = min(ys) - 2
        ymax = max(ys) + 2
        for v in (base, final):
            if v is not None:
                ymin = min(ymin, v - 2)
                ymax = max(ymax, v + 2)
        if ymax - ymin < 1:
            ymax = ymin + 1

        # Supersampling: ImageDraw hrany nevyhlazuje, proto kreslíme S× zvětšeně
        # a pak zmenšíme LANCZOSem -> hladká křivka, kolečka i text.
        # Servírujeme ve 2× rozlišení (OUT), aby to bylo ostré i na displeji.
        S = 4          # render scale (W*S × H*S = 1920×1200)
        OUT = 2        # served scale (W*OUT × H*OUT = 960×600); efektivní AA = S/OUT = 2×

        def mx(t: float) -> float:
            return (L + (t - XMIN) / (XMAX - XMIN) * (R - L)) * S

        def my(v: float) -> float:
            return (B - (v - ymin) / (ymax - ymin) * (B - T)) * S

        img = Image.new("RGB", (W * S, H * S), C_BG)
        d = ImageDraw.Draw(img)
        f_title = _font(13 * S)
        f_tick = _font(11 * S)
        f_now = _font(12 * S)

        d.text((L * S, 4 * S), "Ekvitermní křivka — zpátečka vs. venkovní",
                font=f_title, fill=C_TITLE)

        # vodorovná mřížka + Y popisky
        steps = 4
        for i in range(steps + 1):
            v = ymin + (ymax - ymin) * i / steps
            y = my(v)
            d.line([(L * S, y), (R * S, y)], fill=C_GRIDH, width=S)
            d.text((L * S - 6 * S, y), f"{v:.0f}", font=f_tick, fill=C_TICK, anchor="rm")

        # svislá mřížka + X popisky
        for t in CURVE_X:
            x = mx(t)
            d.line([(x, T * S), (x, B * S)], fill=C_GRID, width=S)
            lbl = f'{"+" if t > 0 else ""}{t}°'
            d.text((x, B * S + 4 * S), lbl, font=f_tick, fill=C_TICK, anchor="ma")

        # osy
        d.line([(L * S, T * S), (L * S, B * S)], fill=C_AXIS, width=S)
        d.line([(L * S, B * S), (R * S, B * S)], fill=C_AXIS, width=S)

        # křivka: ploché prodloužení za kraje + body
        pts = [(XMIN, ys[0])] + list(zip(CURVE_X, ys)) + [(XMAX, ys[-1])]
        line = [(mx(x), my(y)) for x, y in pts]
        d.line(line, fill=C_CURVE, width=3 * S, joint="curve")
        for x, y in zip(CURVE_X, ys):
            cx, cy = mx(x), my(y)
            r = 3.5 * S
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=C_CURVE)

        # aktuální provozní bod: plný bod = hodnota z křivky (base), dutý kroužek
        # = finální zpátečka (base + korekce), kroužek vždy navrch. Když je korekce
        # ≈ 0, leží soustředně na sobě → "topím přesně podle křivky".
        if model is not None and base is not None:
            cxm = max(XMIN, min(XMAX, model))
            cx = mx(cxm)
            y_base = my(base)
            y_final = my(final) if final is not None else y_base
            _vdash(d, cx, T * S, B * S, C_NOW, dash=4 * S, gap=3 * S, width=S)

            r_dot = 4 * S          # plný bod na ekvitermní křivce (base)
            r_ring = 6.5 * S       # dutý kroužek = finální zpátečka (s korekcí)
            d.line([(cx, y_base), (cx, y_final)], fill=C_NOW, width=S)   # spojnice = korekce
            d.ellipse([cx - r_dot, y_base - r_dot, cx + r_dot, y_base + r_dot], fill=C_NOW)
            d.ellipse([cx - r_ring, y_final - r_ring, cx + r_ring, y_final + r_ring],
                      outline=C_NOW, width=2 * S)                        # navrch

            # popisek: model° → base [± korekce = final] °C nad horním z markerů.
            # Korekci bereme jako final - base (sedí i při clampu). Čárka = oddělovač.
            corr = round(final - base, 1) if final is not None else 0.0
            t_final = final if final is not None else base
            if abs(corr) < 0.05:
                label = f"{model:.1f}° → {base:.1f} °C"
            else:
                sign = "+" if corr >= 0 else "−"
                label = f"{model:.1f}° → {base:.1f} {sign} {abs(corr):.1f} = {t_final:.1f} °C"
            label = label.replace(".", ",")
            # popisek do horního pruhu (nad křivkou — tam je vždy místo), přilepený
            # k svislé čárce; vpravo od ní, doleva jen kdyby přetekl z plátna.
            lw = d.textlength(label, font=f_now)
            y_lbl = T * S + 3 * S
            if cx + 4 * S + lw <= W * S - 2 * S:
                d.text((cx + 4 * S, y_lbl), label, font=f_now, fill=C_NOW, anchor="lt")
            else:
                d.text((cx - 4 * S, y_lbl), label, font=f_now, fill=C_NOW, anchor="rt")

        img = img.resize((W * OUT, H * OUT), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


class StatisticsImage(ImageEntity):
    """Snímek tabulky statistiky (tři sloupce: dnes / včera / 7 dnů).

    Na hlavních view NENÍ – od čísel je živá markdown tabulka. Tahle entita
    existuje jen kvůli sdílení a bydlí ve vnořeném view `statistika_export`.

    Render je LÍNÝ: HA zavolá `async_image()` teprve když si snímek někdo
    vyžádá, takže se nikdy nekreslí do šuplíku. Data se čtou až v tu chvíli,
    proto snímek nemůže mít stará čísla. Tlačítko „Obnovit" jen posune
    `image_last_updated`, čímž obejde cache prohlížeče.

    Kreslení běží v executoru – Pillow je synchronní a event loop se blokovat
    nesmí.

    Atribut `snimek_url` nese adresu naposledy uloženého souboru. Dashboard z něj
    skládá odkaz „Otevřít snímek" – jen tak se člověk dostane k systémovému
    „Uložit obrázek", které HA nad entitou nepustí.
    """

    _attr_has_entity_name = True
    _attr_name = "Statistika"
    _attr_content_type = "image/png"

    def __init__(
        self, hass: HomeAssistant, stats: StatisticsAccumulator, entry: ConfigEntry
    ) -> None:
        ImageEntity.__init__(self, hass)
        self._stats = stats
        self._entry = entry
        self._url: str | None = None
        self._attr_unique_id = f"{entry.entry_id}_statistika_png"
        self._attr_device_info = device_info(entry.entry_id)
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def extra_state_attributes(self) -> dict:
        return {"snimek_url": self._url}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_stats_snapshot(self._entry.entry_id),
                self._handle_snapshot,
            )
        )

    @callback
    def _handle_snapshot(self, url: str | None = None) -> None:
        """Tlačítko vyrobilo snímek. `url` je adresa uloženého souboru.

        Když zápis selhal (None), adresu si DRŽÍME – starý odkaz je pořád platný
        a je lepší než prázdné místo na dashboardu.
        """
        if url:
            self._url = url
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        return await async_build_table_png(self.hass, self._stats)


class StatisticsDaysImage(ImageEntity):
    """Denní rozpad jako obrázek přímo v HA — dnešek a sedm zavřených dnů.

    Osm sloupců se na displej mobilu nevejde, ale na tabletu a na PC se to
    přečíst dá a je zbytečné kvůli tomu chodit pro soubor. Karta v dashboardu
    navíc ukáže, jestli se rozpad vůbec vyrobil.

    Kreslí se až na vyžádání a čísla se berou v ten okamžik, takže obrázek
    nemůže být zastaralý.
    """

    _attr_has_entity_name = True
    _attr_name = "Statistika po dnech"
    _attr_content_type = "image/png"

    def __init__(
        self, hass: HomeAssistant, stats: StatisticsAccumulator, entry: ConfigEntry
    ) -> None:
        ImageEntity.__init__(self, hass)
        self._stats = stats
        self._entry = entry
        self._url: str | None = None
        self._attr_unique_id = f"{entry.entry_id}_statistika_dny_png"
        self._attr_device_info = device_info(entry.entry_id)
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def extra_state_attributes(self) -> dict:
        return {"snimek_url": self._url}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_stats_snapshot_days(self._entry.entry_id),
                self._handle_snapshot,
            )
        )

    @callback
    def _handle_snapshot(self, url: str | None = None) -> None:
        # Zápis na disk mohl selhat (None) — starou adresu si držíme, je pořád
        # platná a lepší než prázdné místo na dashboardu.
        if url:
            self._url = url
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        return await async_build_days_png(self.hass, self._stats)


class StavImage(ImageEntity):
    """„Stav regulace" pro schéma jako obrázek se zalomeným textem.

    Čte atributy `sensor.mar_stav` (ne vnitřek koordinátoru), takže ukazuje
    přesně totéž co karta v Režimech. Obrázek se překreslí jen tehdy, když se
    změní text — ne při každém tiknutí senzoru. Časy jsou psané hodinami,
    takže se kvůli plynoucímu času překreslovat nemusí.

    Entity_id senzoru se hledá v registru podle unique_id. Při prvním startu
    senzor ještě nemusí být zapsaný, proto se hledání chvíli opakuje.
    """

    _attr_has_entity_name = True
    _attr_name = "Stav regulace"
    _attr_content_type = "image/png"

    _POKUSU = 30
    _ODSTUP = 10  # s

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        ImageEntity.__init__(self, hass)
        self._entry = entry
        self._radky: list[tuple[str, str]] = []
        self._png: bytes | None = None
        self._pokus = 0
        self._attr_unique_id = f"{entry.entry_id}_stav_png"
        self._attr_device_info = device_info(entry.entry_id)
        self._attr_image_last_updated = dt_util.utcnow()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._napoj()

    @callback
    def _napoj(self, _now=None) -> None:
        eid = er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, f"{self._entry.entry_id}_stav")
        if eid is None:
            self._pokus += 1
            if self._pokus <= self._POKUSU:
                self.async_on_remove(async_call_later(self.hass, self._ODSTUP, self._napoj))
            else:
                _LOGGER.warning("Stav regulace: senzor stavu nenalezen, obrázek zůstane prázdný")
            return
        self.async_on_remove(async_track_state_change_event(self.hass, [eid], self._zmena))
        self._prepocitej(self.hass.states.get(eid))

    @callback
    def _zmena(self, event) -> None:
        self._prepocitej(event.data.get("new_state"))

    @staticmethod
    def _na_mistni(iso: str | None):
        if not iso:
            return None
        t = dt_util.parse_datetime(str(iso))
        return dt_util.as_local(t) if t else None

    @callback
    def _prepocitej(self, state) -> None:
        if state is None:
            return
        radky = sestav_radky(dict(state.attributes), self._na_mistni)
        if radky == self._radky:
            return
        self._radky = radky
        self._png = None
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        if self._png is None:
            self._png = await self.hass.async_add_executor_job(vykresli, list(self._radky))
        return self._png

