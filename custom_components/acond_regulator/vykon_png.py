"""Ouško Výkon — malý graf vložený přes pravou spodní část schématu.

Leží přes topnou soustavu: zleva od svislé trubky z AKU, doprava ke konci
tlačítka FVE topení, na výšku jako AKU (horní a spodní okraj). Proto má vlastní
plátno v poměru té plochy (VLOZKA) a málo čísel — na mobilu je malý.

  • tepelný výkon (30028) světlou plochou, elektrický příkon (30027) tmavší
    plochou uvnitř ní — mezera mezi nimi je teplo vzaté ze vzduchu, takže je
    COP vidět i bez čísla,
  • COP (30029) tenkou čarou na pravé ose (jen když kompresor běží),
  • na konci čar hodnoty „teď“ (výkon, příkon v kW, COP) před osou COP,
    nahoře jen malé „kW“ a „COP“ nad osami, 12 h,
  • ohřev TUV a odmrazování šedým pruhem s popiskem nad ním.

Čistá kreslicí funkce bez Home Assistantu; data skládá `VykonImage`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw

from .teploty_png import GRID, INK, MUT, _fmt, _font, popis_pasu, useky_pasu

# Plocha vložky v souřadnicích schématu 1200 × 700 (dashboard ji tam umístí)
VLOZKA = (776, 319, 1180, 567)          # x0, y0, x1, y1
W = VLOZKA[2] - VLOZKA[0]               # 404
H = VLOZKA[3] - VLOZKA[1]               # 248
S = 3
VYSTUP = (W * 3, H * 3)

POZADI = (250, 250, 251)
RAM = (182, 188, 196)
TEPLO = (226, 75, 74)                   # tepelný výkon
ELEKTRO = (55, 110, 190)                # příkon
COP_BARVA = (29, 140, 90)

L, R = 44, W - 76                       # graf: vlevo osa kW, vpravo hodnoty a osa COP
OS_COP = W - 30                         # popisky osy COP
PAS = (0, 0, 0, 16)
ROZESTUP = 12
T, B = 46, H - 40


@dataclass
class VykonData:
    casy: list[datetime]
    prikon_kw: list[float | None]
    vykon_kw: list[float | None]
    cop: list[float | None]
    tuv: list[bool] = field(default_factory=list)
    odmraz: list[bool] = field(default_factory=list)


def vykresli(d: VykonData) -> bytes:
    n = len(d.casy)
    # Kreslí se na NEPRŮHLEDNÉ plátno (poloprůhledné plochy se tak míchají
    # s pozadím); průhledné jsou jen zaoblené rohy — maska na konci.
    img = Image.new("RGB", (W * S, H * S), POZADI)
    g = ImageDraw.Draw(img, "RGBA")

    def text(x, y, s, col=MUT, sz=11, b=False, anchor="la"):
        g.text((x * S, y * S), s, fill=col, font=_font(sz, b), anchor=anchor)


    hodnoty = [v for v in d.vykon_kw + d.prikon_kw if v is not None]
    vmax = max(hodnoty) if hodnoty else 1.0
    krok = next(k for k in (0.5, 1, 2, 5, 10, 20) if vmax / k <= 4)
    hi = max(krok, math.ceil(vmax / krok) * krok)

    cop_hodnoty = [v for v in d.cop if v is not None]
    cop_hi = max(4.0, math.ceil(max(cop_hodnoty) + 0.5)) if cop_hodnoty else 6.0

    X = lambda i: (L + (R - L) * i / max(n - 1, 1)) * S
    Y = lambda v: (B - (B - T) * v / hi) * S
    Yc = lambda v: (B - (B - T) * v / cop_hi) * S

    # nadpis
    text(12, 10, "Výkon · 12 h", INK, 13, True)

    # šedý pruh TUV / odmrazování (pod křivkami), popisek nahoře
    pas = [a or b for a, b in zip(d.tuv or [False] * n, d.odmraz or [False] * n)]
    konec_popisku = -1e9
    for od, do in useky_pasu(pas):
        x0, x1 = round(X(od)), round(X(min(do, n - 1)))
        g.rectangle([x0, T * S, max(x0 + S, x1 - 1), B * S], fill=PAS)
        # popisek nad pruh (uvnitř by ho zakryly plochy výkonu při ohřevu);
        # těsně za sebou jdoucí pruhy — jen první popisek, ať se nepřekrývají
        popis = popis_pasu(od, do, d.tuv, d.odmraz)
        pul = g.textlength(popis, font=_font(9)) / 2
        xm = (x0 + x1) / 2
        if xm - pul > konec_popisku + 4 * S:
            text(xm / S, T - 3, popis, MUT, 9, anchor="mb")
            konec_popisku = xm + pul

    # mřížka + osy
    v = 0.0
    while v <= hi + 1e-6:
        y = Y(v)
        g.line([(L * S, y), (R * S, y)], fill=GRID, width=S)
        text(L - 5, y / S, (_fmt(v).replace(",0", "")), MUT, 10, anchor="rm")
        v += krok
    text(L - 5, T - 12, "kW", MUT, 10, anchor="rm")
    text(OS_COP, T - 12, "COP", COP_BARVA, 10, anchor="lm")
    for c in range(0, int(cop_hi) + 1, 2 if cop_hi > 6 else 1):
        if c == 0:
            continue
        text(OS_COP, Yc(c) / S, str(c), COP_BARVA, 10, anchor="lm")

    def plocha(rada, barva, alfa):
        for i in range(n - 1):
            a = rada[i]
            if a is None or a <= 0:
                continue
            g.rectangle([round(X(i)), Y(a), round(X(i + 1)) - 1, Y(0)], fill=barva + (alfa,))

    def cara(rada, barva, w, y_fn):
        useky, cur = [], []
        for i in range(n):
            v = rada[i]
            if v is None:
                if cur:
                    useky.append(cur)
                cur = []
                continue
            if cur:
                cur.append((X(i), cur[-1][1]))
            cur.append((X(i), y_fn(v)))
        if cur:
            useky.append(cur)
        for u in useky:
            if len(u) > 1:
                g.line(u, fill=barva, width=int(w * S), joint="curve")

    plocha(d.vykon_kw, TEPLO, 70)
    plocha(d.prikon_kw, ELEKTRO, 110)
    cara(d.vykon_kw, TEPLO, 1.6, Y)
    cara(d.prikon_kw, ELEKTRO, 1.6, Y)
    cara(d.cop, COP_BARVA, 1.6, Yc)

    # hodnoty „teď“ na konci čar (jen poslední bod — COP z dřívějšího běhu
    # by u stojícího stroje lhal, proto tam „0“), rozestrčené, ať se nepřekrývají
    popisky = []
    for rada, barva, y_fn in ((d.vykon_kw, TEPLO, Y), (d.prikon_kw, ELEKTRO, Y),
                              (d.cop, COP_BARVA, Yc)):
        v = rada[-1] if rada else None
        if v is None and rada is d.cop and d.cop:
            # stroj stojí → COP se nepočítá; ukaž „0“ u spodku osy, ne prázdno
            p = d.prikon_kw[-1] if d.prikon_kw else None
            if p is None or p <= 0.1:
                v = 0.0
        if v is not None:
            popisky.append((y_fn(v) / S, _fmt(v), barva))
    popisky.sort(key=lambda p: p[0])
    ys: list[float] = []
    for y, _t, _c in popisky:
        ys.append(min(max(y, (ys[-1] + ROZESTUP) if ys else T), B))
    for i in range(len(ys) - 2, -1, -1):
        ys[i] = min(ys[i], ys[i + 1] - ROZESTUP)
    for (_y, t, c), y in zip(popisky, ys):
        text(R + 4, y, t, c, 10, True, anchor="lm")

    # časová osa po 3 h
    for i, c in enumerate(d.casy):
        if c.minute == 0 and c.hour % 3 == 0 and (i == 0 or d.casy[i - 1].hour != c.hour):
            text(X(i) / S, B + 5, f"{c.hour}:00", MUT, 10, anchor="ma")

    # legenda
    ly = H - 14
    for x, barva, s in ((L, TEPLO, "výkon"), (L + 95, ELEKTRO, "příkon"), (L + 195, COP_BARVA, "COP")):
        g.rectangle([x * S, (ly - 4) * S, (x + 14) * S, (ly + 4) * S], fill=barva + (150,))
        text(x + 19, ly, s, INK, 11, anchor="lm")

    # rámeček a zaoblené rohy (mimo ně průhledno, pod grafem nic neprosvítá)
    g.rounded_rectangle([S, S, (W - 1) * S, (H - 1) * S], radius=10 * S,
                        outline=RAM, width=2 * S)
    maska = Image.new("L", (W * S, H * S), 0)
    ImageDraw.Draw(maska).rounded_rectangle([0, 0, W * S - 1, H * S - 1], radius=11 * S, fill=255)
    img = img.convert("RGBA")
    img.putalpha(maska)
    out = img.resize(VYSTUP, Image.LANCZOS)
    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
