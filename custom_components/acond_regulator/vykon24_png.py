"""Ouško Výkon 24 h — velký graf výkonu za 24 h, kreslený v Pillow přes celé schéma.

Velký bratříček vloženého grafu Výkon (12 h). Dohodnuto s Pavlem 23. 11. 2026
podle náhledu:

  • Nahoře souhrn za 24 h: teplo a elektřina (integrované z okamžitých hodnot,
    takže platí i pro řadu PRO s chybnými čítači), COP z nich, počet startů
    kompresoru, odmrazů a ohřevů TUV.
  • Hlavní panel: tepelný výkon (červená plocha), příkon (modrá plocha uvnitř),
    COP (zelená čára). kW a COP mají společnou mřížku — číslo vlevo i vpravo
    leží na stejné čáře. Horní okraj = nejvyšší hodnota + 10 %, nahoru na celé.
  • TUV žlutým pásem, odmrazování šedým. V odmrazu (a krok po něm) se výkon,
    příkon ani COP nekreslí: Acond tam hlásí nesmyslné špičky (tok tepla se
    obrací), proto mezera.
  • Pod ním pásek venkovní teploty (fialově) a T ekv (šedě čárkovaně) —
    je vidět, proč stroj přidává nebo ubírá. Rozsah z dat ±1 °C.
  • Svislé čáry jen po 3 h, slabé a tečkované; půlnoc plnou čarou.

Plátno má poměr stran schématu (1200 × 700), aby graf schéma přesně překryl.
Čistá kreslicí funkce bez Home Assistantu; data skládá `Vykon24Image`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw

from .teploty_png import (
    GRID, INK, MUT, PAS_TUV, POZADI, _fmt, _font, bez_odmrazu, useky_barev,
)

W, H = 1200, 700
S = 3
VYSTUP = (1800, 1050)

TEPLO = (226, 75, 74)                   # tepelný výkon
ELEKTRO = (55, 110, 190)                # příkon
COP_BARVA = (29, 140, 90)
VENKU = (127, 119, 221)
TEKV = (136, 135, 128)
SVISLA = (0, 0, 0, 40)
PULNOC = (0, 0, 0, 95)

L, R = 92, 1080                         # graf; vpravo osa COP a hodnoty „teď“
P1 = (122, 470)                         # hlavní panel: top, bottom
P2 = (500, 600)                         # pásek venku
OS_CAS = 618
LEGENDA = 664
ROZESTUP = 17


@dataclass
class Vykon24Data:
    """Řady na pravidelné mřížce (stejná délka). None = chybí."""
    casy: list[datetime]
    prikon_kw: list[float | None]
    vykon_kw: list[float | None]
    cop: list[float | None]
    tuv: list[bool]
    odmraz: list[bool]
    venku: list[float | None] = field(default_factory=list)
    t_ekv: list[float | None] = field(default_factory=list)
    # souhrn (None = neznámé, neukáže se)
    teplo_kwh: float | None = None
    elektro_kwh: float | None = None
    starty: int | None = None
    odmrazu: int | None = None
    tuv_pocet: int | None = None


def _sklon(n: int, jeden: str, dva: str, pet: str) -> str:
    if n == 1:
        return jeden
    if 2 <= n <= 4:
        return dva
    return pet


def _kwh(v: float) -> str:
    return (f"{v:.0f}" if v >= 20 else f"{v:.1f}").replace(".", ",")


def souhrn(d: Vykon24Data) -> str:
    casti = []
    if d.teplo_kwh is not None:
        casti.append(f"{_kwh(d.teplo_kwh)} kWh tepla")
    if d.elektro_kwh is not None:
        casti.append(f"{_kwh(d.elektro_kwh)} kWh el.")
    if d.teplo_kwh is not None and d.elektro_kwh and d.elektro_kwh > 0.05:
        casti.append(f"COP {_fmt(d.teplo_kwh / d.elektro_kwh)}")
    if d.starty is not None:
        casti.append(f"{d.starty} {_sklon(d.starty, 'start', 'starty', 'startů')}")
    if d.odmrazu is not None:
        casti.append(f"{d.odmrazu} {_sklon(d.odmrazu, 'odmraz', 'odmrazy', 'odmrazů')}")
    if d.tuv_pocet is not None:
        casti.append(f"TUV {d.tuv_pocet}×")
    return "  ·  ".join(casti)


def vykresli(d: Vykon24Data) -> bytes:
    n = len(d.casy)
    odmraz = list(d.odmraz) + [False] * (n - len(d.odmraz))
    tuv = list(d.tuv) + [False] * (n - len(d.tuv))
    vykon = bez_odmrazu(d.vykon_kw, odmraz)
    prikon = bez_odmrazu(d.prikon_kw, odmraz)
    cop = bez_odmrazu(d.cop, odmraz)
    venku = d.venku if len(d.venku) == n else [None] * n
    t_ekv = d.t_ekv if len(d.t_ekv) == n else [None] * n

    img = Image.new("RGB", (W * S, H * S), POZADI)
    g = ImageDraw.Draw(img, "RGBA")

    def text(x, y, s, col=MUT, sz=15, b=False, anchor="la"):
        g.text((x * S, y * S), s, fill=col, font=_font(sz, b), anchor=anchor)

    def X(i):
        return (L + (R - L) * i / max(n - 1, 1)) * S

    posledni = lambda r: next((v for v in reversed(r) if v is not None), None)

    # ── nadpis a souhrn ──
    text(L, 22, "Výkon · posledních 24 h", INK, 22, True)
    text(R, 26, "klepnutím zpět na schéma", MUT, 14, anchor="ra")
    s = souhrn(d)
    if s:
        text(L, 62, s, INK, 16)

    # ── rozsahy ──
    hodnoty = [v for r in (vykon, prikon, cop) for v in r if v is not None]
    y_max = max(1, math.ceil(max(hodnoty) * 1.1)) if hodnoty else 6
    krok = 1 if y_max <= 10 else 2 if y_max <= 20 else 5
    y_max = math.ceil(y_max / krok) * krok
    Y1 = lambda v: (P1[1] - (P1[1] - P1[0]) * max(0.0, min(v, y_max)) / y_max) * S

    teploty = [v for r in (venku, t_ekv) for v in r if v is not None]
    if teploty:
        lo2, hi2 = math.floor(min(teploty) - 1), math.ceil(max(teploty) + 1)
    else:
        lo2, hi2 = -5, 5
    if hi2 - lo2 < 4:
        hi2 = lo2 + 4
    krok2 = 2 if hi2 - lo2 <= 12 else 5
    Y2 = lambda v: (P2[1] - (P2[1] - P2[0]) * (v - lo2) / (hi2 - lo2)) * S

    # ── vodorovná mřížka ──
    v = 0
    while v <= y_max:
        y = Y1(v)
        if v > 0:
            g.line([(L * S, y), (R * S, y)], fill=GRID, width=S)
        text(L - 10, y / S, str(v), MUT, 14, anchor="rm")
        text(R + 10, y / S, str(v), COP_BARVA, 14, anchor="lm")
        v += krok
    text(L - 10, P1[0] - 20, "kW", MUT, 14, anchor="rm")
    text(R + 10, P1[0] - 20, "COP", COP_BARVA, 14, anchor="lm")

    t = math.ceil(lo2 / krok2) * krok2
    while t <= hi2:
        y = Y2(t)
        nula = t == 0                    # nula tučně (Pavle 23. 11.): mráz na první pohled
        g.line([(L * S, y), (R * S, y)], fill=(0, 0, 0, 120) if nula else GRID,
               width=2 * S if nula else S)
        text(L - 10, y / S, f"{t}".replace("-", "−"), INK if nula else MUT, 13, nula, anchor="rm")
        t += krok2
    text(L - 10, P2[0] - 14, "°C", MUT, 13, anchor="rm")

    # ── pásy TUV (žlutě) a odmrazu (šedě) přes oba panely ──
    # šedý pás i přes doběh po odmrazu — mezera v čarách pak leží celá v pásu
    odmraz_pas = [v is None for v in bez_odmrazu([0] * n, odmraz)]
    pas = [a or b for a, b in zip(tuv, odmraz_pas)]
    for od, do, je_odmraz in useky_barev(pas, odmraz_pas):
        x0, x1 = round(X(od)), round(X(min(do, n - 1)))
        x1 = max(x0 + 2 * S, x1)
        barva = (0, 0, 0, 30) if je_odmraz else PAS_TUV
        for top, bot in (P1, P2):
            g.rectangle([x0, top * S, x1, bot * S], fill=barva)

    # ── svislé čáry po 3 h (tečkovaně), půlnoc plnou čarou; popisky času ──
    for i, c in enumerate(d.casy):
        if c.minute >= 5 or c.hour % 3 or (i and d.casy[i - 1].hour == c.hour):
            continue
        x = X(i)
        if c.hour == 0:
            g.line([(x, P1[0] * S), (x, P2[1] * S)], fill=PULNOC, width=S)
        else:
            y = P1[0] * S
            while y < P2[1] * S:
                g.line([(x, y), (x, min(y + 2 * S, P2[1] * S))], fill=SVISLA, width=S)
                y += 6 * S
        text(x / S, OS_CAS, f"{c.hour:02d}:00", INK if c.hour == 0 else MUT, 14,
             c.hour == 0, anchor="ma")

    # ── výkon, příkon, COP ──
    def plocha(rada, barva, alfa):
        for i in range(n - 1):
            a = rada[i]
            if a is None or a <= 0:
                continue
            g.rectangle([round(X(i)), Y1(a), round(X(i + 1)) - 1, Y1(0)], fill=barva + (alfa,))

    def useky(rada, y_fn):
        out, cur = [], []
        for i in range(n):
            v = rada[i]
            if v is None:
                if cur:
                    out.append(cur)
                cur = []
                continue
            if cur:
                cur.append((X(i), cur[-1][1]))
            cur.append((X(i), y_fn(v)))
        if cur:
            out.append(cur)
        return out

    def cara(rada, barva, w, y_fn):
        for u in useky(rada, y_fn):
            if len(u) > 1:
                g.line(u, fill=barva, width=int(w * S), joint="curve")

    def carkovane(rada, barva, w, y_fn, on=7, off=5):
        # fáze čárkování běží přes celý úsek (schody mají krátké kousky)
        for u in useky(rada, y_fn):
            zbytek, kresli = 0.0, True
            for (x0, y0), (x1, y1) in zip(u, u[1:]):
                delka = math.hypot(x1 - x0, y1 - y0)
                pos = 0.0
                while pos < delka:
                    kus = min((on if kresli else off) * S - zbytek, delka - pos)
                    if kresli:
                        a, b = pos / delka, (pos + kus) / delka
                        g.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a),
                                (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)],
                               fill=barva, width=int(w * S))
                    pos += kus
                    zbytek += kus
                    if zbytek >= (on if kresli else off) * S - 1e-6:
                        zbytek, kresli = 0.0, not kresli

    plocha(vykon, TEPLO, 55)
    plocha(prikon, ELEKTRO, 95)
    cara(vykon, TEPLO, 2.2, Y1)
    cara(prikon, ELEKTRO, 2.2, Y1)
    cara(cop, COP_BARVA, 2.2, Y1)
    g.line([(L * S, Y1(0)), (R * S, Y1(0))], fill=(0, 0, 0, 70), width=S)

    # ── venku a T ekv ──
    g.rectangle([L * S, P2[0] * S, R * S, P2[1] * S], outline=(0, 0, 0, 40), width=S)
    Y2c = lambda v: Y2(max(lo2, min(hi2, v)))
    carkovane(t_ekv, TEKV, 1.8, Y2c)
    cara(venku, VENKU, 2.2, Y2c)

    # ── legenda s hodnotami „teď“ ──
    p_ted, v_ted = prikon[-1] if prikon else None, vykon[-1] if vykon else None
    c_ted = cop[-1] if cop else None
    if c_ted is None and (p_ted is None or p_ted <= 0.1) and prikon and prikon[-1] is not None:
        c_ted = 0.0                      # stroj stojí → COP 0, ne číslo z dřívějšího běhu
    ven_ted, tekv_ted = posledni(venku), posledni(t_ekv)

    def leg(x, druh, col, s):
        y = LEGENDA
        if druh == "box":
            g.rectangle([x * S, (y - 7) * S, (x + 14) * S, (y + 7) * S], fill=col)
        elif druh == "cara":
            g.line([(x * S, y * S), ((x + 24) * S, y * S)], fill=col, width=int(2.6 * S))
        else:
            for k in range(0, 24, 8):
                g.line([((x + k) * S, y * S), ((x + k + 5) * S, y * S)], fill=col, width=int(2.2 * S))
        text(x + (20 if druh == "box" else 30), y, s, INK, 14, anchor="lm")
        return x + (20 if druh == "box" else 30) + g.textlength(s, font=_font(14)) / S + 24

    kw = lambda v: f" {_fmt(v)} kW" if v is not None else ""
    x = L
    x = leg(x, "box", TEPLO + (170,), "výkon" + kw(v_ted))
    x = leg(x, "box", ELEKTRO + (190,), "příkon" + kw(p_ted))
    x = leg(x, "box", COP_BARVA + (190,), "COP" + (f" {_fmt(c_ted)}" if c_ted is not None else ""))
    x = leg(x, "box", PAS_TUV[:3] + (150,), "TUV")
    x = leg(x, "box", (0, 0, 0, 70), "odmraz")
    x = leg(x, "cara", VENKU, "venku" + (f" {_fmt(ven_ted)} °C" if ven_ted is not None else ""))
    leg(x, "cark", TEKV, "T ekv" + (f" {_fmt(tekv_ted)} °C" if tekv_ted is not None else ""))

    out = img.resize(VYSTUP, Image.LANCZOS)
    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
