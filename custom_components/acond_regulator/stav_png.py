"""Obrázek „Stav regulace" pro schéma.

Proč obrázek: popisek `state-label` v Picture Elements má uvnitř pevně
„nezalamovat" a bez card_mod se to z YAML přebít nedá. Dlouhá věta se pak
usekne na okraji. Obrázek má skutečný rámeček, text se do něj zalomí po
slovech, a když je ho moc, písmo se zmenší, aby se vešel celý.

Modul je čistý (jen Pillow), aby šel vyzkoušet bez Home Assistantu:
`sestav_radky()` složí z atributů `sensor.mar_stav` odstavce, `vykresli()`
z nich udělá PNG. Obsah odpovídá kartě Stav regulace v okně Režimy.

Rozměr rámečku je v bodech plátna schématu (1200 × 700). Dashboard ho
musí umístit se stejným poměrem stran, jinak se text roztáhne.
"""

from __future__ import annotations

import datetime as dt
import os
from io import BytesIO
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

# rámeček v bodech plátna schématu; pozici a šířku drží dashboard
RAMEC_W, RAMEC_H = 390, 170       # x 795–1185, y 188–358 (vlevo AKU a trubka k soustavě)
MERITKO = 3                      # kreslí se 3× větší kvůli ostrosti

NADPIS_PT = 20
TEXT_MAX_PT = 16
TEXT_MIN_PT = 9

C_NADPIS = (27, 58, 99)          # MODRA_POPIS ze schématu
C_TEXT = (16, 20, 24)
C_TLUMENY = (59, 66, 74)

# druhy odstavců: (barva, tučně)
STYLY = {
    "strategie": (C_TEXT, True),
    "veta": (C_TEXT, False),
    "rezim": (C_TLUMENY, False),
}

_FONT = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        _FONT,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ── obsah ────────────────────────────────────────────────────────────────

def _cz(v: float | None, des: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{des}f}".replace(".", ",")


def sestav_radky(
    a: dict, na_mistni: Callable[[str | None], dt.datetime | None]
) -> list[tuple[str, str]]:
    """Z atributů `sensor.mar_stav` složí odstavce (druh, text).

    `na_mistni` převede ISO čas na místní datetime (v HA dt_util), ať je
    modul nezávislý na HA. Časy se píšou jako hodiny („v 14:35"), ne jako
    odpočet — obrázek se tak nemusí překreslovat každou minutu.
    """

    def hhmm(iso: str | None) -> str:
        t = na_mistni(iso)
        return t.strftime("%H:%M") if t else "—"

    def den(iso: str | None) -> str:
        t = na_mistni(iso)
        return f"{t.day}. {t.month}. {t:%H:%M}" if t else "—"

    r: list[tuple[str, str]] = []
    if a.get("strategie"):
        r.append(("strategie", str(a["strategie"])))
    if a.get("veta"):
        r.append(("veta", str(a["veta"])))

    reg = a.get("reguluje")
    if reg:
        r.append(("rezim", "Zápis zpátečky – zapisuje."))
    elif reg is None:
        r.append(("rezim", "Zápis zpátečky: stav ještě neznám (MaR se načítá)."))
    else:
        duvod = a.get("duvod") or "vede FVE topení, na registru je návnada"
        r.append(("rezim", f"Zápis zpátečky – nezapisuje: {duvod}."))

    if a.get("faze_aktivni"):
        co = "zapínání" if a["faze_aktivni"] == "start" else "vypínání"
        r.append(("rezim", f"Probíhá {co}: {a.get('faze') or '—'}."))
    elif a.get("stav_zeleza"):
        r.append(("rezim", f"{str(a['stav_zeleza']).capitalize()} — kompresor běží, netopí dům."))

    z = a.get("zebra") or {}
    if z.get("aktivni"):
        r.append(("rezim", f"Zebra: {z.get('faze') or '—'} · konec fáze v "
                           f"{hhmm(z.get('konec_faze'))} · startů dnes: {z.get('starty_dnes', 0)}"))

    d = a.get("dennoc") or {}
    if d.get("aktivni"):
        kde = ("v okně, topí se podle strategie" if d.get("uvnitr_okna")
               else "mimo okno, TČ vypnuté")
        r.append(("rezim", f"Den/noc: {kde} · příští změna v {hhmm(d.get('pristi_zmena'))}"))

    o = a.get("okna_teploty") or {}
    if o.get("aktivni"):
        if o.get("aktivni_okno"):
            delta = o.get("delta")
            posun = f"{delta:+.1f}".replace(".", ",") if delta is not None else "—"
            r.append(("rezim", f"Okna ±: okno {o['aktivni_okno']} běží, posun {posun} °C "
                               f"(z {_cz(o.get('base'))} °C) · vrátím v {hhmm(o.get('pristi_zmena'))}"))
        else:
            r.append(("rezim", f"Okna ±: mimo okno · příští v {hhmm(o.get('pristi_zmena'))}"))

    v = a.get("dovolena") or {}
    if v.get("aktivni"):
        if v.get("probiha"):
            r.append(("rezim", f"Dovolená běží: místnost {_cz(v.get('cil_mistnost'))} °C, "
                               f"TUV {_cz(v.get('cil_tuv'), 0)} °C · vrátím {den(v.get('do'))}"))
        else:
            r.append(("rezim", f"Dovolená naplánovaná: {den(v.get('od'))} → {den(v.get('do'))}"))
    return r


# ── kreslení ─────────────────────────────────────────────────────────────

def _zalom(d: ImageDraw.ImageDraw, text: str, font, sirka: float) -> list[str]:
    radky: list[str] = []
    akt = ""
    for slovo in text.split():
        zkus = f"{akt} {slovo}" if akt else slovo
        if d.textlength(zkus, font=font) <= sirka or not akt:
            akt = zkus
        else:
            radky.append(akt)
            akt = slovo
    if akt:
        radky.append(akt)
    return radky


def _rozvrh(d, odstavce, pt: float, sirka: float):
    """Vrátí (výška, řádky) pro danou velikost písma v bodech plátna."""
    S = MERITKO
    font = _font(round(pt * S))
    krok = pt * 1.3 * S
    mezera = pt * 0.35 * S
    vysl = []
    y = 0.0
    for i, (druh, text) in enumerate(odstavce):
        if i:
            y += mezera
        for radek in _zalom(d, text, font, sirka):
            vysl.append((y, radek, druh))
            y += krok
    return y, vysl, font


def vykresli(odstavce: list[tuple[str, str]]) -> bytes:
    S = MERITKO
    W, H = RAMEC_W * S, RAMEC_H * S
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    f_nadpis = _font(NADPIS_PT * S)
    d.text((0, 0), "Stav regulace", font=f_nadpis, fill=C_NADPIS,
           stroke_width=max(1, round(S * 0.6)), stroke_fill=C_NADPIS)
    y0 = NADPIS_PT * 1.5 * S
    volno = H - y0

    # největší písmo, se kterým se všechno vejde
    pt = TEXT_MAX_PT
    while True:
        vyska, rozvrh, font = _rozvrh(d, odstavce, pt, W)
        if vyska <= volno or pt <= TEXT_MIN_PT:
            break
        pt -= 0.5

    # ani nejmenší písmo nestačí: usekni poslední řádky a naznač to
    if vyska > volno:
        krok = pt * 1.3 * S
        rozvrh = [x for x in rozvrh if x[0] + krok <= volno]
        if rozvrh:
            y, t, druh = rozvrh[-1]
            rozvrh[-1] = (y, t.rstrip(" .,·") + " …", druh)

    for y, radek, druh in rozvrh:
        barva, tucne = STYLY.get(druh, STYLY["veta"])
        d.text((0, y0 + y), radek, font=font, fill=barva,
               stroke_width=max(1, round(S * pt / 26)) if tucne else 0,
               stroke_fill=barva)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
