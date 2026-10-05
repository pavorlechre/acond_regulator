"""Ouško Teploty — graf posledních 12 h, kreslený v Pillow přes celé schéma.

Čistá kreslicí funkce bez Home Assistantu: dostane hotové řady na pravidelné
časové mřížce a vrátí PNG. Data skládá `TeplotyImage` v image.py z recorderu.

Rozvržení (dohodnuté s Pavlem 29. 9. 2026):
  • Nahoře MÍSTNOST jako schody, cíl čárkovaně zeleně, odchylka vybarvená
    (červená nad cílem, modrá pod ním), pevný rozsah ±1 K kolem cíle.
  • Dole ZPÁTEČKA: požadovaná (oranžová, registr 30008) a skutečná (modrá),
    obě schody. Ekviterma bez přídavku čárkovaně fialově s vlastní pravou
    osou T ekv — převrácenou (zima nahoře), kreslenou stejně čárkovaně;
    čárkovaná čára do ní na konci vteče a ukazatel ukáže aktuální T ekv.
    Značky osy míří doleva do grafu, aby se nepletly s mínusy u čísel.
  • Přídavek na místnost jako jemná výplň mezi ekvitermou a výpočtem MaR:
    červená přidává, modrá ubírá.
  • TUV a odmrazování jako šedé pásy, křivky v nich přerušené, osy bez špiček.

Plátno má poměr stran schématu (1200 × 700), aby graf schéma přesně překryl.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 700          # souřadnice návrhu (= plátno schématu)
S = 3                     # kreslí se 3× větší, pak se zmenší (ostré hrany)
VYSTUP = (1800, 1050)     # výsledné PNG — ostré i na tabletu

_FONT = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")
_FONT_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONTB_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

INK = (43, 49, 56); MUT = (107, 112, 117); GRID = (0, 0, 0, 24)
RED = (226, 75, 74); BLU = (55, 138, 221); AMB = (196, 125, 20); ACT = (24, 95, 165)
PUR = (83, 74, 183); TGT = (29, 158, 117); ROOM = (216, 90, 48)
PAS = (0, 0, 0, 18)
POZADI = (250, 250, 251)

# rozvržení v souřadnicích návrhu
L, R, AX = 92, 1010, 1032          # levý okraj grafu, pravý okraj, pravá osa T ekv
P1 = (88, 258)                     # horní panel: top, bottom
P2 = (318, 600)                    # dolní panel
OS_CAS = 618                       # popisky času
LEGENDA = 664

# Spodní mez osy zpátečky. Co je níž (noc bez topení, voda z domu), se kreslí
# tečkovaně po spodním okraji — neroztahuje osu a nevypadá jako výpadek dat.
DOLNI_MEZ = 19.5
# Ohřev TUV: firmware zvedne požadovanou zpátečku vždy na 60 °C, a to dřív, než
# se rozsvítí bit TUV. Taková chvíle se bere jako TUV (šedý pás, mimo osu).
TUV_POZADOVANA = 59.5
MAX_POPISKU = 7


@dataclass
class TeplotyData:
    """Řady na pravidelné mřížce (stejná délka). None = chybí / přerušeno."""
    casy: list[datetime]
    mistnost: list[float | None]
    cil: list[float | None]
    t_ekv: list[float | None]
    ekv_zaklad: list[float | None]      # zpátečka z křivky bez přídavku
    vypocet: list[float | None]         # výpočet MaR (křivka + přídavek)
    pozadovana: list[float | None]      # 30008 — co TČ opravdu dostala
    skutecna: list[float | None]        # 30009
    pas: list[bool]                     # TUV nebo odmrazování
    tuv: list[bool] = field(default_factory=list)       # zvlášť kvůli popisku pásu
    odmraz: list[bool] = field(default_factory=list)
    krivka_x: list[float] = field(default_factory=list)
    krivka_y: list[float] = field(default_factory=list)


def _font(size: float, bold: bool = False) -> ImageFont.FreeTypeFont:
    cesty = ([_FONTB_FALLBACK, _FONT] if bold else [_FONT, _FONT_FALLBACK])
    for p in cesty:
        try:
            return ImageFont.truetype(p, int(size * S))
        except OSError:
            continue
    return ImageFont.load_default()


def _fmt(v: float) -> str:
    return f"{v:.1f}".replace(".", ",").replace("-", "−")


def krivka(x: float, xs: list[float], ys: list[float]) -> float | None:
    """Po částech lineární ekviterma (stejně jako koordinátor). Mimo rozsah ploše."""
    if not xs or len(xs) != len(ys):
        return None
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            f = (x - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + f * (ys[i + 1] - ys[i])
    return ys[-1]


def useky_pasu(pas: list[bool]) -> list[tuple[int, int]]:
    """Souvislé úseky pásu jako (od, do) — `do` je první index za úsekem."""
    out, i, n = [], 0, len(pas)
    while i < n:
        if pas[i]:
            j = i
            while j < n and pas[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def popis_pasu(od: int, do: int, tuv: list[bool], odmraz: list[bool]) -> str:
    """Co bylo v pásu: „TUV“, „odmraz.“, nebo obojí. Bez údaje (pás jen
    z požadované 60 °C) je to TUV."""
    t = any(tuv[od:do]) if tuv else False
    o = any(odmraz[od:do]) if odmraz else False
    if o and not t:
        return "odmraz."
    if o and t:
        return "TUV / odmraz."
    return "TUV"


def _rozsah(*rady, pas, minimum=2.0, krok=0.5, dolni=None):
    """Rozsah osy z hodnot mimo pásy. `dolni` = pevná spodní mez (níž se neroste)."""
    hodnoty = [v for r in rady for v, p in zip(r, pas) if v is not None and not p]
    if dolni is not None:
        nad = [v for v in hodnoty if v >= dolni]
        if not nad:
            return dolni, dolni + minimum
        lo = max(dolni, math.floor((min(nad) - 0.3) / krok) * krok)
        hi = math.ceil((max(nad) + 0.3) / krok) * krok
        return lo, max(hi, lo + minimum)
    if not hodnoty:
        return 20.0, 30.0
    lo, hi = min(hodnoty), max(hodnoty)
    stred = (lo + hi) / 2
    pul = max((hi - lo) / 2 + 0.3, minimum / 2)
    lo = math.floor((stred - pul) / krok) * krok
    hi = math.ceil((stred + pul) / krok) * krok
    return lo, hi


def _znacky(lo: float, hi: float) -> list[float]:
    """Nejvýš MAX_POPISKU popisků na „hezkém“ kroku."""
    for krok in (0.5, 1.0, 2.0, 5.0, 10.0):
        if (hi - lo) / krok + 1 <= MAX_POPISKU:
            break
    v = math.ceil(lo / krok - 1e-9) * krok
    out = []
    while v <= hi + 1e-6:
        out.append(round(v, 1))
        v += krok
    return out


def vykresli(d: TeplotyData) -> bytes:
    n = len(d.casy)
    # požadovaná 60 °C = začátek/průběh ohřevu TUV, i když bit ještě nesvítí
    d.pas = [p or (v is not None and v >= TUV_POZADOVANA)
             for p, v in zip(d.pas, d.pozadovana)]
    img = Image.new("RGB", (W * S, H * S), POZADI)
    g = ImageDraw.Draw(img, "RGBA")

    def text(x, y, s, col=MUT, sz=15, b=False, anchor="la"):
        g.text((x * S, y * S), s, fill=col, font=_font(sz, b), anchor=anchor)

    def X(i):
        return (L + (R - L) * i / max(n - 1, 1)) * S

    def Yf(panel, lo, hi):
        t, b = panel
        return lambda v: (b - (b - t) * (v - lo) / (hi - lo)) * S

    def mrizka(panel, Y, ticks):
        for v in ticks:
            y = Y(v)
            if panel[0] * S - 1 <= y <= panel[1] * S + 1:
                g.line([(L * S, y), (R * S, y)], fill=GRID, width=S)
                text(L - 10, y / S, _fmt(v) + " °C", anchor="rm")

    def pasy(panel, barva=PAS):
        i = 0
        while i < n:
            if d.pas[i]:
                j = i
                while j < n and d.pas[j]:
                    j += 1
                g.rectangle([X(i), panel[0] * S, X(min(j, n - 1)), panel[1] * S], fill=barva)
                i = j
            else:
                i += 1

    def schody(rada, Y, lo=None, hi=None, pres_pas=False):
        useky, cur = [], []
        for i in range(n):
            v = rada[i]
            if v is None or (d.pas[i] and not pres_pas):
                if cur:
                    useky.append(cur)
                cur = []
                continue
            if lo is not None:
                v = max(lo, min(hi, v))
            if cur:
                cur.append((X(i), cur[-1][1]))
            cur.append((X(i), Y(v)))
        if cur:
            useky.append(cur)
        return useky

    def schody_pod(rada, Y, lo):
        """Úseky, kde je hodnota pod spodní mezí — kreslí se po okraji."""
        useky, cur = [], []
        for i in range(n):
            v = rada[i]
            if v is None or d.pas[i] or v >= lo:
                if cur:
                    cur.append((X(i), cur[-1][1]))
                    useky.append(cur)
                cur = []
                continue
            if not cur:
                cur.append((X(i), Y(lo)))
            cur.append((X(i), Y(lo)))
        if cur:
            useky.append(cur)
        return useky

    def teckovane(useky, col, w):
        for u in useky:
            if len(u) < 2:
                continue
            x0, x1, y = u[0][0], u[-1][0], u[0][1]
            x = x0
            while x < x1:
                g.ellipse([x - w * S, y - w * S, x + w * S, y + w * S], fill=col)
                x += 7 * S

    def cara(useky, col, w):
        for u in useky:
            if len(u) > 1:
                g.line(u, fill=col, width=int(w * S), joint="curve")

    def carkovane(useky, col, w, on=7, off=5):
        for u in useky:
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
                               fill=col, width=int(w * S))
                    pos += kus
                    zbytek += kus
                    if zbytek >= (on if kresli else off) * S - 1e-6:
                        zbytek, kresli = 0.0, not kresli

    def vypln(dolni, horni, Y, barva_fn, alfa=64, lo=None, hi=None, pres_pas=False):
        for i in range(n - 1):
            a, b = dolni[i], horni[i]
            if (d.pas[i] and not pres_pas) or a is None or b is None or a == b:
                continue
            if lo is not None:
                a, b = max(lo, min(hi, a)), max(lo, min(hi, b))
            y0, y1 = sorted((Y(a), Y(b)))
            g.rectangle([round(X(i)), y0, round(X(i + 1)) - 1, y1],
                        fill=barva_fn(i) + (alfa,))

    posledni = lambda r: next((v for v in reversed(r) if v is not None), None)

    # ── nadpis ──
    text(L, 22, "Teploty · posledních 12 h", INK, 22, True)
    text(R, 26, "klepnutím zpět na schéma", MUT, 14, anchor="ra")

    # ── panel 1: místnost ──
    cil_ted = posledni(d.cil)
    text(L, P1[0] - 26, "Místnost", INK, 16, True)
    pasy(P1, (0, 0, 0, 8))      # ohřev TUV místnost neovlivní — jen slabé podbarvení
    if cil_ted is not None:
        lo1, hi1 = cil_ted - 1.0, cil_ted + 1.0
        Y1 = Yf(P1, lo1, hi1)
        mrizka(P1, Y1, [lo1, cil_ted, hi1])
        vypln(d.cil, d.mistnost, Y1,
              lambda i: RED if (d.mistnost[i] or 0) > (d.cil[i] or 0) else BLU, lo=lo1, hi=hi1,
              pres_pas=True)
        carkovane(schody(d.cil, Y1, lo1, hi1, pres_pas=True), TGT, 1.8)
        cara(schody(d.mistnost, Y1, lo1, hi1, pres_pas=True), ROOM, 2.6)
        text(R, P1[0] - 26, "- - cíl " + _fmt(cil_ted) + " °C", TGT, 14, anchor="ra")
        m = posledni(d.mistnost)
        if m is not None:
            ym = Y1(max(lo1, min(hi1, m))) / S
            text(R + 12, ym - 2, _fmt(m) + " °C", ROOM, 15, True)
            dv = round(m - cil_ted, 1)
            text(R + 12, ym + 16, "v cíli" if dv == 0 else
                 ("+" if dv > 0 else "−") + _fmt(abs(dv)) + " K",
                 TGT if dv == 0 else (RED if dv > 0 else BLU), 14)
    else:
        text((L + R) / 2, (P1[0] + P1[1]) / 2, "bez dat o místnosti", MUT, 16, anchor="mm")

    # ── panel 2: zpátečka ──
    text(L, P2[0] - 26, "Zpátečka", INK, 16, True)
    pasy(P2)
    lo2, hi2 = _rozsah(d.pozadovana, d.skutecna, d.ekv_zaklad, pas=d.pas, dolni=DOLNI_MEZ)
    Y2 = Yf(P2, lo2, hi2)
    mrizka(P2, Y2, _znacky(lo2, hi2))

    vypln(d.ekv_zaklad, d.vypocet, Y2,
          lambda i: RED if (d.vypocet[i] or 0) > (d.ekv_zaklad[i] or 0) else BLU,
          lo=lo2, hi=hi2)

    # pravá osa T ekv (převrácená — přepočet přes aktuální křivku)
    carkovane([[(AX * S, P2[0] * S), (AX * S, P2[1] * S)]], PUR, 1.8)
    text(AX, P2[0] - 26, "- - T ekv", PUR, 15, True, anchor="ma")
    t_ted = posledni(d.t_ekv)
    zakl_ted = posledni(d.ekv_zaklad)
    y_ukaz = Y2(max(lo2, min(hi2, zakl_ted))) if zakl_ted is not None else None
    if d.krivka_x:
        obsazeno = []
        for t in range(math.ceil(d.krivka_x[0] / 2) * 2, int(d.krivka_x[-1]) + 1, 2):
            z = krivka(t, d.krivka_x, d.krivka_y)
            if z is None:
                continue
            y = Y2(z)
            if not (P2[0] * S + 10 < y < P2[1] * S - 6):
                continue
            if y_ukaz is not None and abs(y - y_ukaz) < 22 * S:
                continue
            if any(abs(y - o) < 22 * S for o in obsazeno):
                continue
            obsazeno.append(y)
            g.line([((AX - 7) * S, y), (AX * S, y)], fill=PUR, width=S)   # značka doleva
            text(AX + 8, y / S, f"{t} °C".replace("-", "−"), PUR, 13, anchor="lm")

    zaklad_useky = schody(d.ekv_zaklad, Y2, lo2, hi2)
    if zaklad_useky and zaklad_useky[-1]:
        zaklad_useky[-1].append((AX * S, zaklad_useky[-1][-1][1]))   # vteče do osy
    carkovane(zaklad_useky, PUR, 2.0)
    pod = lambda r: [None if v is not None and v < lo2 else v for v in r]
    cara(schody(pod(d.pozadovana), Y2, lo2, hi2), AMB, 2.6)
    cara(schody(pod(d.skutecna), Y2, lo2, hi2), ACT, 2.6)
    # pod spodní mezí: tečkovaně, světleji, po spodním okraji
    teckovane(schody_pod(d.skutecna, Y2, lo2), ACT + (110,), 1.6)
    teckovane(schody_pod(d.pozadovana, Y2, lo2), AMB + (110,), 1.6)

    if y_ukaz is not None and t_ted is not None:
        popis = _fmt(t_ted) + " °C"
        tw = g.textlength(popis, font=_font(13, True)) / S
        bx = AX + 7
        g.polygon([(AX * S + S, y_ukaz), (bx * S, y_ukaz - 7 * S), (bx * S, y_ukaz + 7 * S)], fill=PUR)
        g.rounded_rectangle([bx * S, y_ukaz - 12 * S, (bx + tw + 14) * S, y_ukaz + 12 * S],
                            radius=5 * S, fill=PUR)
        text(bx + 7, y_ukaz / S, popis, (255, 255, 255), 13, True, anchor="lm")

    # pás TUV/odmraz — u každého pásu, kam se vejde, co v něm bylo
    for od, do in useky_pasu(d.pas):
        popis = popis_pasu(od, do, d.tuv, d.odmraz)
        sirka = (X(min(do, n - 1)) - X(od)) / S
        if sirka >= g.textlength(popis, font=_font(12)) / S + 4:
            xm = (X(od) + X(min(do, n - 1))) / 2 / S
            text(xm, P2[0] + 8, popis, MUT, 12, anchor="ma")

    # ── časová osa ──
    for i, c in enumerate(d.casy):
        if c.minute == 0 and c.hour % 2 == 0 and (i == 0 or d.casy[i - 1].hour != c.hour):
            text(X(i) / S, OS_CAS, f"{c.hour}:00", anchor="ma")

    # ── legenda ──
    def leg(x, druh, col, s):
        y = LEGENDA
        if druh == "cara":
            g.line([(x * S, y * S), ((x + 26) * S, y * S)], fill=col, width=int(2.6 * S))
        elif druh == "cark":
            carkovane([[(x * S, y * S), ((x + 26) * S, y * S)]], col, 2.0, 5, 4)
        else:
            g.rectangle([x * S, (y - 7) * S, (x + 12) * S, (y + 7) * S], fill=col[0] + (80,))
            g.rectangle([(x + 13) * S, (y - 7) * S, (x + 25) * S, (y + 7) * S], fill=col[1] + (80,))
        text(x + 34, y, s, INK, 14, anchor="lm")

    poz, sk = posledni(d.pozadovana), posledni(d.skutecna)
    leg(L, "cara", AMB, "požadovaná" + (f" {_fmt(poz)} °C" if poz is not None else ""))
    leg(L + 240, "cara", ACT, "skutečná" + (f" {_fmt(sk)} °C" if sk is not None else ""))
    leg(L + 460, "cark", PUR, "ekviterma bez přídavku")
    leg(L + 720, "vypln", (RED, BLU), "přídavek místnosti +/−")

    out = img.resize(VYSTUP, Image.LANCZOS)
    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
