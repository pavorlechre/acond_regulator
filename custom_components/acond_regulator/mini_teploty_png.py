"""Malé teploty ve schématu — „okno ve zdi“ mezi tepelným čerpadlem a bojlerem.

Je vidět pořád (bez vypínače), proto je subtilní: žádné osy, mřížka ani
legenda, jen křivky a barevné plochy za posledních 6 h a u pravé hrany
(= teď) drobné hodnoty všech pěti čar. Podrobnosti ukáže klepnutí → ouško Teploty.

  • Nahoře MÍSTNOST: schody, cíl čárkovaně zeleně, odchylka vybarvená
    (červená nad cílem, modrá pod ním). Rozsah se přizpůsobí odchylce,
    aby byla vidět i desetina stupně.
  • Dole VODA: výstup (červeně), skutečná zpátečka (modře), požadovaná
    zpátečka (oranžově). Mezera výstup–zpátečka je jemně červená: „chod“ —
    když kompresor běží, čáry se rozestoupí, když stojí, sejdou se.
  • Ohřev TUV a odmrazování: šedý pruh, křivky vody v něm přerušené (výstup
    60 °C by rozbil měřítko). Pod 19,5 °C (TČ stojí, čidla venku vychladnou)
    tečkovaně po spodním okraji, stejně jako ve velkém grafu.

Plocha OKNO je v souřadnicích schématu 1200 × 700; zeď je v ní přerušená
(schema/gen_schema.py) a dashboard sem obrázek položí. Pozadí má barvu
podkladu schématu, takže obrázek nemá viditelný okraj.

Čistá kreslicí funkce bez Home Assistantu; data skládá `MiniTeplotyImage`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw

from .teploty_png import (
    ACT, AMB, BLU, DOLNI_MEZ, RED, ROOM, TGT, TUV_POZADOVANA, _fmt, _font, _rozsah,
)

# Plocha v souřadnicích schématu (x0, y0, x1, y1): mezi TČ (pravý okraj 304)
# a bojlerem (levý okraj 540) s podobnou mezerou z obou stran, mezi červenou
# (296) a modrou (505) trubkou.
OKNO = (322, 306, 520, 496)
W = OKNO[2] - OKNO[0]                   # 198
H = OKNO[3] - OKNO[1]                   # 190
S = 3
VYSTUP = (W * 2, H * 2)

POZADI = (251, 251, 252)                # = podklad schématu (#fbfbfc)
VODA = (224, 64, 44)                    # výstup — barva červené trubky
PAS = (0, 0, 0, 14)

L, R = 2, W - 34                        # graf; vpravo sloupek na hodnoty
P1 = (8, 62)                            # místnost: top, bottom
P2 = (76, 184)                          # voda
ROZESTUP = 10                           # svislá mezera mezi hodnotami vpravo


@dataclass
class MiniTeplotyData:
    """Řady na pravidelné mřížce (stejná délka). None = chybí."""
    casy: list[datetime]
    mistnost: list[float | None]
    cil: list[float | None]
    vystup: list[float | None]          # 30018 — topná voda z TČ
    pozadovana: list[float | None]      # 30008
    skutecna: list[float | None]        # 30009
    pas: list[bool]                     # TUV nebo odmrazování


def vykresli(d: MiniTeplotyData) -> bytes:
    n = len(d.casy)
    pas = [p or (v is not None and v >= TUV_POZADOVANA)
           for p, v in zip(d.pas, d.pozadovana)]
    img = Image.new("RGB", (W * S, H * S), POZADI)
    g = ImageDraw.Draw(img, "RGBA")

    def X(i):
        return (L + (R - L) * i / max(n - 1, 1)) * S

    def Yf(panel, lo, hi):
        t, b = panel
        return lambda v: (b - (b - t) * (min(hi, max(lo, v)) - lo) / (hi - lo)) * S

    def schody(rada, Y, preskoc=None):
        useky, cur = [], []
        for i in range(n):
            v = rada[i]
            if v is None or (preskoc is not None and preskoc(i, v)):
                if cur:
                    useky.append(cur)
                cur = []
                continue
            if cur:
                cur.append((X(i), cur[-1][1]))
            cur.append((X(i), Y(v)))
        if cur:
            useky.append(cur)
        return useky

    def cara(useky, col, w):
        for u in useky:
            if len(u) > 1:
                g.line(u, fill=col, width=int(w * S), joint="curve")

    def carkovane(useky, col, w, on=4, off=3):
        for u in useky:
            for (x0, y0), (x1, y1) in zip(u, u[1:]):
                delka = math.hypot(x1 - x0, y1 - y0)
                pos, kresli = 0.0, True
                while pos < delka:
                    kus = min((on if kresli else off) * S, delka - pos)
                    if kresli:
                        a, b = pos / delka, (pos + kus) / delka
                        g.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a),
                                (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)],
                               fill=col, width=int(w * S))
                    pos += kus
                    kresli = not kresli

    def teckovane_pod(rada, Y, lo, col):
        for i in range(n - 1):
            v = rada[i]
            if v is None or pas[i] or v >= lo or i % 3:
                continue
            x, y = X(i), Y(lo)
            g.ellipse([x - S, y - S, x + S, y + S], fill=col)

    def vypln(a_rada, b_rada, Y, barva_fn, alfa, preskoc=None):
        for i in range(n - 1):
            a, b = a_rada[i], b_rada[i]
            if a is None or b is None or a == b or (preskoc and preskoc(i)):
                continue
            y0, y1 = sorted((Y(a), Y(b)))
            if y1 - y0 < 1:
                continue
            g.rectangle([round(X(i)), y0, round(X(i + 1)) - 1, y1],
                        fill=barva_fn(a, b) + (alfa,))

    posledni = lambda r: next((v for v in reversed(r) if v is not None), None)
    horni: list[tuple[float, str, tuple]] = []        # (y, text, barva) — místnost
    dolni: list[tuple[float, str, tuple]] = []        # voda

    # ── místnost ──
    cil_ted = posledni(d.cil)
    if cil_ted is not None:
        odchylky = [abs(m - c) for m, c in zip(d.mistnost, d.cil)
                    if m is not None and c is not None]
        pul = max(0.4, (max(odchylky) if odchylky else 0) + 0.1)
        lo1, hi1 = cil_ted - pul, cil_ted + pul
        Y1 = Yf(P1, lo1, hi1)
        vypln(d.cil, d.mistnost, Y1, lambda c, m: RED if m > c else BLU, 60)
        carkovane(schody(d.cil, Y1), TGT + (200,), 1.1)
        cara(schody(d.mistnost, Y1), ROOM, 1.7)
        m = posledni(d.mistnost)
        if m is not None:
            horni.append((Y1(m) / S, _fmt(m), ROOM))
        horni.append((Y1(cil_ted) / S, _fmt(cil_ted), TGT))

    # ── voda ──
    for i in range(n):                  # šedý pruh TUV / odmraz přes oba panely
        if pas[i] and i < n - 1:
            g.rectangle([round(X(i)), P1[0] * S, round(X(i + 1)) - 1, P2[1] * S], fill=PAS)
    lo2, hi2 = _rozsah(d.vystup, d.pozadovana, d.skutecna, pas=pas,
                       minimum=3.0, krok=1.0, dolni=DOLNI_MEZ)
    Y2 = Yf(P2, lo2, hi2)
    mimo = lambda i, v: pas[i] or v < lo2
    # mezera výstup–zpátečka = chod (jen nad spodní mezí a mimo TUV)
    vypln(d.skutecna, d.vystup, Y2, lambda zp, vy: RED if vy > zp else BLU, 46,
          preskoc=lambda i: pas[i] or (d.vystup[i] or 0) < lo2)
    cara(schody(d.pozadovana, Y2, mimo), AMB, 1.5)
    cara(schody(d.vystup, Y2, mimo), VODA, 1.3)
    cara(schody(d.skutecna, Y2, mimo), ACT, 1.7)
    teckovane_pod(d.skutecna, Y2, lo2, ACT + (120,))
    teckovane_pod(d.vystup, Y2, lo2, VODA + (90,))

    for rada, col in ((d.vystup, VODA), (d.pozadovana, AMB), (d.skutecna, ACT)):
        v = posledni(rada)
        if v is not None:
            dolni.append((Y2(v) / S, _fmt(v), col))

    # hodnoty „teď“ u pravé hrany — v každém panelu rozestrčené, ať se nepřekrývají
    for popisky, (top, bot) in ((horni, (P1[0] - 4, P1[1] + 6)), (dolni, (P2[0] - 2, H - 5))):
        popisky.sort(key=lambda p: p[0])
        ys: list[float] = []
        for y, _t, _c in popisky:
            ys.append(min(max(y, (ys[-1] + ROZESTUP) if ys else top), bot))
        for i in range(len(ys) - 2, -1, -1):    # zpětně, když spodní narazil na dno
            ys[i] = min(ys[i], ys[i + 1] - ROZESTUP)
        for (_y, t, c), y in zip(popisky, ys):
            g.text(((R + 4) * S, y * S), t, fill=c, font=_font(9, True), anchor="lm")

    out = img.resize(VYSTUP, Image.LANCZOS)
    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
