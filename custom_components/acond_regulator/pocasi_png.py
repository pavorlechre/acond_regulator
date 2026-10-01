"""Ouško Počasí — 24 h zpět a 24 h dopředu, kreslené v Pillow přes celé schéma.

Obsah převzatý z Pavlova grafu počasí (ApexCharts), jen nakreslený tak, aby
šel položit přes schéma a nesl ikonky počasí:
  • modře venkovní teplota z čidla TČ (historie),
  • fialově T ekv — teplota, podle které jede ekviterma (historie modelu),
  • červeně průměrná předpověď, tence oba zdroje (open-meteo, met.no),
  • žlutý pruh = okno průměrování modelu (minulost + předpověď z nastavení),
  • ryska „teď“,
  • nahoře ikonky počasí po 3 h nad předpovědí (značky met.no) a srážky.

Čistá kreslicí funkce bez Home Assistantu; data skládá `PocasiImage`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import BytesIO

from PIL import Image, ImageDraw

from .teploty_png import (
    GRID, INK, MUT, POZADI, PUR, S, VYSTUP, H, W, _fmt, _font, _znacky,
)

MODRA = (31, 119, 180)
CERVENA = (214, 39, 40)
ORANZ = (255, 127, 14)
ZELENA = (44, 160, 44)
ZLUTA = (240, 196, 25)
SLUNCE = (247, 181, 0)
MRAK = (176, 186, 197)
MRAK_TMAVY = (128, 138, 150)
KAPKA = (47, 111, 208)

L, R = 92, 1110
P = (178, 586)              # graf: top, bottom
IKONY_Y = 118               # střed řady ikonek
OS_CAS = 606
LEGENDA = 664


@dataclass
class PocasiData:
    ted: datetime
    start: datetime
    konec: datetime
    hist_casy: list[datetime] = field(default_factory=list)
    venku: list[float | None] = field(default_factory=list)
    model: list[float | None] = field(default_factory=list)
    fc_prumer: list[tuple[datetime, float]] = field(default_factory=list)
    fc_open_meteo: list[tuple[datetime, float]] = field(default_factory=list)
    fc_met_no: list[tuple[datetime, float]] = field(default_factory=list)
    symboly: list[tuple[datetime, str, float | None]] = field(default_factory=list)
    okno_od: datetime | None = None
    okno_do: datetime | None = None


# ── ikonky ───────────────────────────────────────────────────────────────

def _slunce(g, cx, cy, r):
    for k in range(8):
        a = k * math.pi / 4
        g.line([(cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35),
                (cx + math.cos(a) * r * 1.85, cy + math.sin(a) * r * 1.85)],
               fill=SLUNCE, width=max(2, int(r * 0.28)))
    g.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SLUNCE)


def _mesic(g, cx, cy, r):
    g.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(150, 160, 190))
    g.ellipse([cx - r * 0.35, cy - r * 1.15, cx + r * 1.45, cy + r * 0.65], fill=POZADI)


def _mrak(g, cx, cy, r, barva=MRAK):
    g.ellipse([cx - r * 1.6, cy - r * 0.2, cx - r * 0.2, cy + r * 1.0], fill=barva)
    g.ellipse([cx - r * 0.9, cy - r * 0.9, cx + r * 0.7, cy + r * 0.7], fill=barva)
    g.ellipse([cx + r * 0.0, cy - r * 0.45, cx + r * 1.6, cy + r * 1.0], fill=barva)
    g.rounded_rectangle([cx - r * 1.5, cy + r * 0.2, cx + r * 1.5, cy + r * 1.0],
                        radius=r * 0.4, fill=barva)


def _kapky(g, cx, cy, r, pocet):
    for k in range(pocet):
        x = cx + (k - (pocet - 1) / 2) * r * 0.9
        g.line([(x, cy), (x - r * 0.25, cy + r * 0.6)], fill=KAPKA, width=max(2, int(r * 0.22)))


def _vlocky(g, cx, cy, r, pocet):
    for k in range(pocet):
        x = cx + (k - (pocet - 1) / 2) * r * 0.9
        for a in (0, math.pi / 3, 2 * math.pi / 3):
            dx, dy = math.cos(a) * r * 0.28, math.sin(a) * r * 0.28
            g.line([(x - dx, cy + r * 0.3 - dy), (x + dx, cy + r * 0.3 + dy)],
                   fill=(110, 150, 200), width=max(1, int(r * 0.12)))


def _blesk(g, cx, cy, r):
    g.polygon([(cx + r * 0.1, cy), (cx - r * 0.35, cy + r * 0.55), (cx, cy + r * 0.55),
               (cx - r * 0.2, cy + r * 1.05), (cx + r * 0.45, cy + r * 0.4),
               (cx + r * 0.1, cy + r * 0.4)], fill=SLUNCE)


def ikona(g, cx, cy, r, znacka: str) -> None:
    """Ikonka podle značky met.no (clearsky_day, lightrainshowers_night, …)."""
    noc = znacka.endswith("_night")
    zaklad = znacka.split("_")[0]
    svetlo = _mesic if noc else _slunce
    if zaklad == "clearsky":
        svetlo(g, cx, cy, r * 0.75)
        return
    if zaklad in ("fair", "partlycloudy"):
        svetlo(g, cx - r * 0.45, cy - r * 0.45, r * 0.55)
        _mrak(g, cx + r * 0.2, cy + r * 0.05, r * (0.5 if zaklad == "fair" else 0.68))
        return
    if zaklad == "fog":
        _mrak(g, cx, cy - r * 0.35, r * 0.6)
        for k in range(3):
            y = cy + r * 0.45 + k * r * 0.25
            g.line([(cx - r, y), (cx + r, y)], fill=MRAK_TMAVY, width=max(2, int(r * 0.12)))
        return
    prehanky = "showers" in zaklad
    if prehanky:
        svetlo(g, cx - r * 0.5, cy - r * 0.6, r * 0.45)
    tmavy = "heavy" in zaklad or "thunder" in zaklad
    _mrak(g, cx + (r * 0.1 if prehanky else 0), cy - r * 0.35, r * 0.65,
          MRAK_TMAVY if tmavy else MRAK)
    if zaklad == "cloudy":
        return
    pocet = 3 if "heavy" in zaklad else (1 if "light" in zaklad else 2)
    if "thunder" in zaklad:
        _blesk(g, cx - r * 0.1, cy + r * 0.25, r * 0.7)
        if "rain" in zaklad or "sleet" in zaklad or "snow" in zaklad:
            _kapky(g, cx + r * 0.55, cy + r * 0.4, r * 0.7, 1)
    elif "snow" in zaklad:
        _vlocky(g, cx, cy + r * 0.35, r * 0.75, pocet)
    elif "sleet" in zaklad:
        _kapky(g, cx - r * 0.3, cy + r * 0.4, r * 0.7, 1)
        _vlocky(g, cx + r * 0.35, cy + r * 0.35, r * 0.75, 1)
    elif "rain" in zaklad:
        _kapky(g, cx, cy + r * 0.4, r * 0.75, pocet)


# ── graf ─────────────────────────────────────────────────────────────────

def vykresli(d: PocasiData) -> bytes:
    img = Image.new("RGB", (W * S, H * S), POZADI)
    g = ImageDraw.Draw(img, "RGBA")
    t0, t1 = d.start.timestamp(), d.konec.timestamp()

    def text(x, y, s, col=MUT, sz=15, b=False, anchor="la"):
        g.text((x * S, y * S), s, fill=col, font=_font(sz, b), anchor=anchor)

    def X(t: datetime) -> float:
        return (L + (R - L) * (t.timestamp() - t0) / (t1 - t0)) * S

    hodnoty = [v for v in d.venku + d.model if v is not None]
    hodnoty += [v for _, v in d.fc_prumer + d.fc_open_meteo + d.fc_met_no]
    if hodnoty:
        lo = math.floor(min(hodnoty) - 0.5)
        hi = math.ceil(max(hodnoty) + 0.5)
        if hi - lo < 4:
            stred = (hi + lo) / 2
            lo, hi = math.floor(stred - 2), math.ceil(stred + 2)
    else:
        lo, hi = 0, 10

    def Y(v: float) -> float:
        return (P[1] - (P[1] - P[0]) * (v - lo) / (hi - lo)) * S

    # nadpis
    text(L, 22, "Počasí · 24 h zpět a 24 h dopředu", INK, 22, True)
    text(R, 26, "klepnutím zpět na schéma", MUT, 14, anchor="ra")

    # mřížka a osa °C
    for v in _znacky(lo, hi):
        y = Y(v)
        if P[0] * S - 1 <= y <= P[1] * S + 1:
            g.line([(L * S, y), (R * S, y)], fill=GRID, width=S)
            text(L - 10, y / S, _fmt(v).replace(",0", "") + " °C", anchor="rm")

    # okno průměrování (žlutý pruh na spodní hraně)
    if d.okno_od and d.okno_do:
        xa = max(X(d.okno_od), L * S)
        xb = min(X(d.okno_do), R * S)
        if xb > xa:
            g.rectangle([xa, (P[1] - 7) * S, xb, P[1] * S], fill=ZLUTA)

    # časová osa
    h = d.start.replace(minute=0, second=0, microsecond=0)
    while h <= d.konec:
        if h >= d.start and h.hour % 4 == 0:
            text(X(h) / S, OS_CAS, f"{h.hour}:00", anchor="ma")
        h += timedelta(hours=1)

    def cara(body, col, w):
        useky, cur = [], []
        for t, v in body:
            if v is None:
                if cur:
                    useky.append(cur)
                cur = []
                continue
            cur.append((X(t), Y(v)))
        if cur:
            useky.append(cur)
        for u in useky:
            if len(u) > 1:
                g.line(u, fill=col, width=int(w * S), joint="curve")

    # historie
    cara(list(zip(d.hist_casy, d.venku)), MODRA, 2.6)
    cara(list(zip(d.hist_casy, d.model)), PUR, 3.6)

    # předpověď — začíná na „teď“ navázáním na poslední známou venkovní teplotu
    cara(d.fc_open_meteo, ORANZ, 1.6)
    cara(d.fc_met_no, ZELENA, 1.6)
    cara(d.fc_prumer, CERVENA, 3.6)

    # ryska teď
    xt = X(d.ted)
    y = P[0] * S
    while y < P[1] * S:
        g.line([(xt, y), (xt, min(y + 6 * S, P[1] * S))], fill=(120, 120, 120), width=S)
        y += 11 * S
    g.rounded_rectangle([xt - 22 * S, (P[0] - 26) * S, xt + 22 * S, (P[0] - 4) * S],
                        radius=6 * S, fill=(128, 128, 128))
    text(xt / S, P[0] - 15, "teď", (255, 255, 255), 14, True, anchor="mm")

    # ikonky po 3 h nad předpovědí
    for t, znacka, mm in d.symboly:
        if t <= d.ted or t > d.konec or t.hour % 3 != 0:
            continue
        x = X(t) / S
        if x < L + 20 or x > R - 10:
            continue
        ikona(g, x * S, IKONY_Y * S, 22 * S, znacka)
        if mm is not None and mm >= 0.1:
            text(x, IKONY_Y + 30, _fmt(mm).replace(",0", "") + " mm", KAPKA, 12, anchor="ma")

    # tenká nápověda dole (bez hodnot)
    def leg(x, druh, col, s):
        y = LEGENDA
        if druh == "cara":
            g.line([(x * S, y * S), ((x + 24) * S, y * S)], fill=col, width=int(2.6 * S))
        elif druh == "tenka":
            g.line([(x * S, y * S), ((x + 24) * S, y * S)], fill=col, width=int(1.6 * S))
        else:
            g.rectangle([x * S, (y - 4) * S, (x + 24) * S, (y + 3) * S], fill=col)
        text(x + 32, y, s, INK, 14, anchor="lm")

    leg(L, "cara", MODRA, "venku (čidlo TČ)")
    leg(L + 200, "cara", PUR, "T ekv")
    leg(L + 310, "cara", CERVENA, "předpověď")
    leg(L + 455, "tenka", ORANZ, "open-meteo")
    leg(L + 610, "tenka", ZELENA, "met.no")
    leg(L + 745, "pruh", ZLUTA, "okno průměrování")

    out = img.resize(VYSTUP, Image.LANCZOS)
    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
