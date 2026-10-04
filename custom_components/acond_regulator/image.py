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
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    ACOND_BIT_DEFROST,
    ACOND_COP,
    ACOND_POWER,
    ACOND_TEPELNY_VYKON,
    ACOND_BIT_TUV,
    ACOND_INDOOR,
    ACOND_OUTDOOR,
    ACOND_RETURN_ACT,
    ACOND_RETURN_READBACK,
    ACOND_ROOM_SET,
    ACOND_STARTS_TOTAL,
    CURVE_X,
    DOMAIN,
    TEPLOTY_HODIN,
    TEPLOTY_KEY,
    TEPLOTY_KROK_MIN,
    TEPLOTY_OBNOVA_S,
    POCASI_HODIN,
    POCASI_KEY,
    VRSTVA_POCASI,
    VRSTVA_SCHEMA,
    VRSTVA_VYKON_KEY,
    VYKON_HODIN,
    VYKON_KEY,
    VRSTVA_TEPLOTY,
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
    signal_vrstva,
    signal_vrstva_vykon,
)
from .coordinator import MarCoordinator
from .stav_png import sestav_radky, vykresli
from .pocasi_png import PocasiData, vykresli as vykresli_pocasi, vyhled_tekv
from .teploty_png import TeplotyData, vykresli as vykresli_teploty
from .vykon_png import VykonData, vykresli as vykresli_vykon
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
            TeplotyImage(hass, coordinator, entry),
            PocasiImage(hass, coordinator, entry),
            VykonImage(hass, coordinator, entry),
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



# ═══════════════════════════════════════════════════════════════════════════
#  Ouško Teploty — graf 12 h přes schéma
# ═══════════════════════════════════════════════════════════════════════════

# MaR senzory hledané v registru podle unique_id (entity_id si uživatel může
# přejmenovat, unique_id ne).
_TEPLOTY_MAR = {
    "t_ekv": "ekvitermni_teplota",
    "ekv_zaklad": "zpatecka_z_ekvitermy",
    "vypocet": "vypoctena_zpatecka",
}


def _cislo(stav) -> float | None:
    try:
        return float(stav)
    except (TypeError, ValueError):
        return None


def _prevzorkuj(stavy: list, casy: list[dt.datetime], prevod):
    """Hodnota platná v každém čase mřížky (poslední známý stav ≤ čas)."""
    out, j, posledni = [], 0, None
    stavy = sorted(stavy, key=lambda st: st.last_changed)
    for t in casy:
        while j < len(stavy) and stavy[j].last_changed <= t:
            posledni = prevod(stavy[j].state)
            j += 1
        out.append(posledni)
    return out


async def _historie(hass: HomeAssistant, start: dt.datetime, ids: list[str]) -> dict:
    """Stavy entit od `start` z recorderu. Selhání = prázdno (graf je nadstavba)."""
    try:
        from homeassistant.components.recorder import get_instance, history

        return await get_instance(hass).async_add_executor_job(
            lambda: history.get_significant_states(
                hass, start, None, ids,
                include_start_time_state=True,
                significant_changes_only=False,
                minimal_response=False,
                no_attributes=True,
            ))
    except Exception:  # noqa: BLE001 — graf je nadstavba, nesmí nic shodit
        _LOGGER.warning("Ouško: historii z recorderu se nepodařilo načíst", exc_info=True)
        return {}


class _VrstvaImage(ImageEntity):
    """Společný základ obrázků oušek pod schématem.

    Kreslí se jen tehdy, když je otevřené NĚJAKÉ ouško s grafem (select.mar_vrstva
    ≠ Schéma) — pak se předkreslují všechny grafy, takže přepnutí mezi nimi je
    okamžité. Obnova každých TEPLOTY_OBNOVA_S sekund. Při Schématu nestojí nic.

    Bez probliknutí: graf se kreslí na pozadí a do aplikace se ohlásí (nové
    image_last_updated) až HOTOVÝ — do té doby aplikace drží předchozí obrázek.
    Jen úplně první obrázek po startu se kreslí na požádání; dashboard pod ním
    mezitím ukazuje „Kreslím graf…“.

    Když recorder chybí nebo dotaz selže, obrázek ukáže, co má (i prázdný
    graf) — regulace o tom neví a nic se nezastaví.
    """

    _attr_has_entity_name = True
    _attr_content_type = "image/png"
    VRSTVA = ""
    KLIC = ""

    def __init__(self, hass: HomeAssistant, coordinator: MarCoordinator, entry: ConfigEntry) -> None:
        ImageEntity.__init__(self, hass)
        self._coordinator = coordinator
        self._entry = entry
        self._png: bytes | None = None
        self._png_cas: dt.datetime | None = None
        self._kreslim = False
        self._aktivni = False
        self._zrus_obnovu = None
        self._attr_unique_id = f"{entry.entry_id}_{self.KLIC}"
        self._attr_device_info = device_info(entry.entry_id)
        self._attr_image_last_updated = dt_util.utcnow()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, signal_vrstva(self._entry.entry_id), self._vrstva))

    async def async_will_remove_from_hass(self) -> None:
        self._zastav_obnovu()

    @callback
    def _zastav_obnovu(self) -> None:
        if self._zrus_obnovu is not None:
            self._zrus_obnovu()
            self._zrus_obnovu = None

    @callback
    def _vrstva(self, volba: str) -> None:
        self._nastav_aktivni(volba != VRSTVA_SCHEMA)

    @callback
    def _nastav_aktivni(self, aktivni: bool) -> None:
        if aktivni == self._aktivni:
            return                       # přepnutí mezi grafy: obnova už běží
        self._aktivni = aktivni
        self._zastav_obnovu()
        if aktivni:
            self._zrus_obnovu = async_track_time_interval(
                self.hass, self._obnov, dt.timedelta(seconds=TEPLOTY_OBNOVA_S))
            cerstvy = (self._png_cas is not None and
                       (dt_util.utcnow() - self._png_cas).total_seconds() < TEPLOTY_OBNOVA_S)
            if not cerstvy:
                self._obnov()

    @callback
    def _obnov(self, _now=None) -> None:
        """Překresli na pozadí; aplikaci ohlas až hotový obrázek."""
        if self._kreslim:
            return
        self._kreslim = True
        self.hass.async_create_background_task(self._kresli(), f"mar_{self.KLIC}")

    async def _kresli(self) -> None:
        try:
            png = await self._vyrob()
        except Exception:  # noqa: BLE001 — graf je nadstavba, nesmí nic shodit
            _LOGGER.warning("Ouško: graf se nepodařilo nakreslit", exc_info=True)
            return
        finally:
            self._kreslim = False
        self._png = png
        self._png_cas = dt_util.utcnow()
        self._attr_image_last_updated = self._png_cas
        self.async_write_ha_state()

    def _mar_id(self, klic: str) -> str | None:
        return er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, f"{self._entry.entry_id}_{klic}")

    async def _vyrob(self) -> bytes:
        raise NotImplementedError

    async def async_image(self) -> bytes | None:
        if self._png is None:            # jen úplně první obrázek po startu
            self._png = await self._vyrob()
            self._png_cas = dt_util.utcnow()
        return self._png


class TeplotyImage(_VrstvaImage):
    """image.mar_teploty — graf ouška Teploty (12 h z recorderu)."""

    _attr_name = "Teploty"
    VRSTVA = VRSTVA_TEPLOTY
    KLIC = TEPLOTY_KEY

    async def _vyrob(self) -> bytes:
        data = await self._data()
        return await self.hass.async_add_executor_job(vykresli_teploty, data)

    async def _data(self) -> TeplotyData:
        konec = dt_util.utcnow().replace(second=0, microsecond=0)
        konec -= dt.timedelta(minutes=konec.minute % TEPLOTY_KROK_MIN)
        start = konec - dt.timedelta(hours=TEPLOTY_HODIN)
        kroku = TEPLOTY_HODIN * 60 // TEPLOTY_KROK_MIN
        casy = [start + dt.timedelta(minutes=TEPLOTY_KROK_MIN * i) for i in range(kroku + 1)]

        mar = {k: self._mar_id(v) for k, v in _TEPLOTY_MAR.items()}
        zdroje = {
            "mistnost": ACOND_INDOOR,
            "cil": ACOND_ROOM_SET,
            "pozadovana": ACOND_RETURN_READBACK,
            "skutecna": ACOND_RETURN_ACT,
            "tuv": ACOND_BIT_TUV,
            "odmraz": ACOND_BIT_DEFROST,
            **{k: v for k, v in mar.items() if v},
        }
        historie = await _historie(self.hass, start - dt.timedelta(hours=1), list(zdroje.values()))

        def rada(klic, prevod=_cislo):
            eid = zdroje.get(klic)
            return _prevzorkuj(historie.get(eid, []) if eid else [], casy, prevod)

        zap = lambda s: s == "on"
        tuv, odmraz = rada("tuv", zap), rada("odmraz", zap)
        mistni = [dt_util.as_local(c) for c in casy]
        return TeplotyData(
            casy=mistni,
            mistnost=rada("mistnost"),
            cil=rada("cil"),
            t_ekv=rada("t_ekv"),
            ekv_zaklad=rada("ekv_zaklad"),
            vypocet=rada("vypocet"),
            pozadovana=rada("pozadovana"),
            skutecna=rada("skutecna"),
            pas=[bool(a) or bool(b) for a, b in zip(tuv, odmraz)],
            krivka_x=list(CURVE_X),
            krivka_y=list(self._coordinator.curve_y),
        )


class PocasiImage(_VrstvaImage):
    """image.mar_pocasi — ouško Počasí: 24 h zpět (recorder) a 24 h dopředu.

    Předpověď i značky počasí bere z koordinátoru (žádný dotaz navíc),
    historii venkovní teploty a T ekv z recorderu.
    """

    _attr_name = "Počasí"          # entity_id: image.mar_pocasi (slugify bez diakritiky)
    VRSTVA = VRSTVA_POCASI
    KLIC = POCASI_KEY

    async def _vyrob(self) -> bytes:
        data = await self._data()
        return await self.hass.async_add_executor_job(vykresli_pocasi, data)

    async def _data(self) -> PocasiData:
        ted = dt_util.utcnow()
        start = ted - dt.timedelta(hours=POCASI_HODIN)
        konec = ted + dt.timedelta(hours=POCASI_HODIN)
        krok = dt.timedelta(minutes=TEPLOTY_KROK_MIN)
        prvni = start.replace(second=0, microsecond=0)
        prvni -= dt.timedelta(minutes=prvni.minute % TEPLOTY_KROK_MIN)
        casy, t = [], prvni
        while t <= ted:
            casy.append(t)
            t += krok

        model_id = self._mar_id("ekvitermni_teplota")
        ids = [ACOND_OUTDOOR] + ([model_id] if model_id else [])
        historie = await _historie(self.hass, start - dt.timedelta(hours=1), ids)
        venku = _prevzorkuj(historie.get(ACOND_OUTDOOR, []), casy, _cislo)
        model = _prevzorkuj(historie.get(model_id, []), casy, _cislo) if model_id else []

        def rada(series, do=konec) -> list:
            out = []
            for p in series or []:
                cas = dt_util.parse_datetime(str(p.get("datetime", "")))
                v = p.get("temperature")
                if cas is None or v is None or (do is not None and cas > do):
                    continue
                out.append((dt_util.as_local(cas), float(v)))
            return out

        data = self._coordinator.data
        symboly = []
        for p in getattr(self._coordinator, "pocasi_symboly", []) or []:
            cas = dt_util.parse_datetime(str(p.get("datetime", "")))
            if cas is not None and p.get("symbol"):
                symboly.append((dt_util.as_local(cas), str(p["symbol"]), p.get("srazky")))

        mistni = dt_util.as_local
        fc_prumer = rada(getattr(self._coordinator, "pocasi_avg", None) or (data.series_avg if data else []))
        hist_mistni = [mistni(c) for c in casy]
        tekv_ted = next((v for v in reversed(model) if v is not None), None)
        # výhled počítá i z předpovědi za okrajem grafu (24 h + fh), kreslí se do konce grafu
        fc_cela = rada(getattr(self._coordinator, "pocasi_avg", None) or (data.series_avg if data else []), do=None)
        vyhled = vyhled_tekv(
            mistni(ted), hist_mistni, venku, fc_cela,
            self._coordinator.past_hours, self._coordinator.future_hours, tekv_ted,
            do=mistni(konec))
        return PocasiData(
            ted=mistni(ted),
            start=mistni(start),
            konec=mistni(konec),
            hist_casy=hist_mistni,
            venku=venku,
            model=model,
            fc_prumer=fc_prumer,
            fc_open_meteo=rada(getattr(self._coordinator, "pocasi_s1", None) or (data.series1 if data else [])),
            fc_met_no=rada(getattr(self._coordinator, "pocasi_s2", None) or (data.series2 if data else [])),
            symboly=symboly,
            okno_od=mistni(ted - dt.timedelta(hours=max(1, int(self._coordinator.past_hours)))),
            okno_do=mistni(ted + dt.timedelta(hours=max(0, int(self._coordinator.future_hours)))),
            noci=self._noci(start, konec),
            vyhled_tekv=vyhled,
        )

    def _noci(self, start: dt.datetime, konec: dt.datetime) -> list:
        """Intervaly západ → východ slunce v okně grafu (místní čas)."""
        try:
            from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
            from homeassistant.helpers.sun import get_astral_event_date
        except Exception:  # noqa: BLE001 — bez slunce prostě bez nocí
            return []
        out = []
        den = dt_util.as_local(start).date() - dt.timedelta(days=1)
        posledni_den = dt_util.as_local(konec).date()
        while den <= posledni_den:
            try:
                zapad = get_astral_event_date(self.hass, SUN_EVENT_SUNSET, den)
                vychod = get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, den + dt.timedelta(days=1))
            except Exception:  # noqa: BLE001
                zapad = vychod = None
            if zapad and vychod and vychod > start and zapad < konec:
                out.append((dt_util.as_local(max(zapad, start)), dt_util.as_local(min(vychod, konec))))
            den += dt.timedelta(days=1)
        return out


class VykonImage(_VrstvaImage):
    """image.mar_vykon — ouško Výkon: malý graf vložený přes schéma (12 h).

    Na rozdíl od Teplot a Počasí ho nezapíná select.mar_vrstva, ale přepínač
    switch.mar_vrstva_vykon (zapni / vypni klepnutím na ouško). Kreslení na
    pozadí a obnova à 5 min jsou stejné jako u ostatních oušek.
    """

    _attr_name = "Výkon"          # entity_id: image.mar_vykon
    KLIC = VYKON_KEY

    async def async_added_to_hass(self) -> None:
        await ImageEntity.async_added_to_hass(self)
        self.async_on_remove(async_dispatcher_connect(
            self.hass, signal_vrstva_vykon(self._entry.entry_id), self._nastav_aktivni))
        data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        if isinstance(data, dict) and data.get(VRSTVA_VYKON_KEY):
            self._nastav_aktivni(True)

    async def _vyrob(self) -> bytes:
        data = await self._data()
        return await self.hass.async_add_executor_job(vykresli_vykon, data)

    def _na_kw(self, eid: str):
        """Převod stavu na kW podle jednotky entity (W i kW)."""
        st = self.hass.states.get(eid)
        jednotka = (st.attributes.get("unit_of_measurement") if st else None) or "W"
        nasobek = 1.0 if str(jednotka).lower() == "kw" else 0.001
        return lambda v: (None if _cislo(v) is None else _cislo(v) * nasobek)

    async def _data(self) -> VykonData:
        konec = dt_util.utcnow().replace(second=0, microsecond=0)
        konec -= dt.timedelta(minutes=konec.minute % TEPLOTY_KROK_MIN)
        start = konec - dt.timedelta(hours=VYKON_HODIN)
        kroku = VYKON_HODIN * 60 // TEPLOTY_KROK_MIN
        casy = [start + dt.timedelta(minutes=TEPLOTY_KROK_MIN * i) for i in range(kroku + 1)]
        ids = [ACOND_POWER, ACOND_TEPELNY_VYKON, ACOND_COP]
        historie = await _historie(self.hass, start - dt.timedelta(hours=1), ids)
        prikon = _prevzorkuj(historie.get(ACOND_POWER, []), casy, self._na_kw(ACOND_POWER))
        vykon = _prevzorkuj(historie.get(ACOND_TEPELNY_VYKON, []), casy, self._na_kw(ACOND_TEPELNY_VYKON))
        cop = _prevzorkuj(historie.get(ACOND_COP, []), casy, _cislo)
        # COP jen když kompresor opravdu běží (příkon nad 100 W) a dává smysl
        cop = [c if (c is not None and 0 < c < 15 and p is not None and p > 0.1) else None
               for c, p in zip(cop, prikon)]
        return VykonData(
            casy=[dt_util.as_local(c) for c in casy],
            prikon_kw=prikon, vykon_kw=vykon, cop=cop,
        )
