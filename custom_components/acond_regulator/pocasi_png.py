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

MODRA = (0, 151, 167)        # venku — tyrkysová (dřív modrá splývala s fialovou T ekv)
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
    noci: list[tuple[datetime, datetime]] = field(default_factory=list)   # západ → východ
    vyhled_tekv: list[tuple[datetime, float]] = field(default_factory=list)  # T ekv dopředu


# ── výhled T ekv ─────────────────────────────────────────────────────────

def vyhled_tekv(ted: datetime, hist_casy: list[datetime], venku: list[float | None],
                predpoved: list[tuple[datetime, float]], hodin_minulost: int,
                hodin_predpoved: int, tekv_ted: float | None,
                do: datetime | None = None) -> list[tuple[datetime, float]]:
    """T ekv dopředu stejným vzorcem jako MaR, po hodinách.

    model(t) = (bh · průměr[t − bh, t] + fh · průměr předpovědi[t, t + fh]) / (bh + fh)
    Do minulého okna padá změřená venkovní teplota (do „teď“) a za „teď“
    předpověď. Počítá se jen tam, kde má okno předpovědi celou délku — tedy
    nejdéle do konce předpovědi minus fh (a ne dál než `do`, konec grafu). Výsledek se posune tak, aby navázal
    na skutečnou T ekv v „teď“ (MaR navíc lehce vyhlazuje a vzorkuje buffer).
    """
    bh, fh = max(1, int(hodin_minulost)), max(0, int(hodin_predpoved))
    fc = sorted((t, v) for t, v in predpoved if v is not None)
    if not fc:
        return []
    konec_fc = fc[-1][0]

    def fc_v(t: datetime) -> float | None:
        """Předpověď v čase t (lineárně mezi hodinami)."""
        if t <= fc[0][0]:
            return fc[0][1]
        for (ta, va), (tb, vb) in zip(fc, fc[1:]):
            if ta <= t <= tb:
                f = (t - ta).total_seconds() / max((tb - ta).total_seconds(), 1)
                return va + f * (vb - va)
        return None

    mereno = [(t, v) for t, v in zip(hist_casy, venku) if v is not None and t <= ted]

    def minuly_prumer(t: datetime) -> float | None:
        od = t - timedelta(hours=bh)
        vals = [v for tt, v in mereno if od <= tt <= t]
        k = max(od, ted) + timedelta(minutes=5)
        while k <= t:
            v = fc_v(k)
            if v is not None:
                vals.append(v)
            k += timedelta(minutes=5)
        return sum(vals) / len(vals) if vals else None

    def model(t: datetime) -> float | None:
        mp = minuly_prumer(t)
        if mp is None:
            return None
        if fh == 0:
            return mp
        okno = [v for tt, v in fc if t <= tt < t + timedelta(hours=fh)]
        if not okno:
            return None
        return (bh * mp + fh * (sum(okno) / len(okno))) / (bh + fh)

    posledni = konec_fc - timedelta(hours=fh)
    if do is not None:
        posledni = min(posledni, do)        # dál než konec grafu se nekreslí
    if posledni <= ted:
        return []
    zaklad = model(ted)
    posun = (tekv_ted - zaklad) if (tekv_ted is not None and zaklad is not None) else 0.0
    out: list[tuple[datetime, float]] = []
    t = ted
    while t <= posledni:
        m = model(t)
        if m is not None:
            out.append((t, round(m + posun, 1)))
        t += timedelta(hours=1)
    return out


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
        svetlo(g, cx, cy, r * (0.5 if not noc else 0.7))
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
    hodnoty += [v for _, v in d.fc_prumer + d.fc_open_meteo + d.fc_met_no + d.vyhled_tekv]
    if hodnoty:
        # rezerva nahoře i dole, aby se popisky max/min vešly nad/pod vrchol
        rozpeti = max(hodnoty) - min(hodnoty)
        rez = max(1.0, rozpeti * 0.10)
        lo = math.floor(min(hodnoty) - rez)
        hi = math.ceil(max(hodnoty) + rez)
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

    # noci (od západu do východu slunce) — jemně šedé pozadí grafu
    for od, do in d.noci:
        xa, xb = max(X(od), L * S), min(X(do), R * S)
        if xb > xa:
            g.rectangle([xa, P[0] * S, xb, P[1] * S], fill=(40, 50, 80, 14))

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

    # T ekv dopředu — tence čárkovaně; končí tam, kam ještě sahá okno předpovědi
    if len(d.vyhled_tekv) > 1:
        body = [(X(t), Y(v)) for t, v in d.vyhled_tekv]
        zbytek, kresli = 0.0, True
        on, off = 10 * S, 6 * S
        for (x0, y0), (x1, y1) in zip(body, body[1:]):
            delka = math.hypot(x1 - x0, y1 - y0)
            pos = 0.0
            while pos < delka:
                kus = min((on if kresli else off) - zbytek, delka - pos)
                if kresli:
                    a, b = pos / delka, (pos + kus) / delka
                    g.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a),
                            (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)], fill=PUR, width=int(2.0 * S))
                pos += kus
                zbytek += kus
                if zbytek >= (on if kresli else off) - 1e-6:
                    zbytek, kresli = 0.0, not kresli

    def vrchol(t, v, nahoru, popis, barva):
        """Bod a popisek vrcholu: nad/pod bodem, a když tam není místo, vedle něj."""
        x, y = X(t), Y(v)
        g.ellipse([x - 4 * S, y - 4 * S, x + 4 * S, y + 4 * S], fill=barva)
        s_ = f"{popis} {_fmt(v)} °C"
        ty = y / S - 16 if nahoru else y / S + 16
        if P[0] + 8 <= ty <= P[1] - 14:
            text(x / S, ty, s_, barva, 14, True, anchor="mm")
            return
        sirka = g.textlength(s_, font=_font(14, True)) / S
        if x / S + 12 + sirka <= R:
            text(x / S + 12, y / S, s_, barva, 14, True, anchor="lm")
        else:
            text(x / S - 12, y / S, s_, barva, 14, True, anchor="rm")

    # minimum a maximum v historii (venku) — jen skutečné vrcholy, ne useknutý kraj okna
    hist = [(t, v) for t, v in zip(d.hist_casy, d.venku) if v is not None]
    if len(hist) >= 12:
        kraj = timedelta(minutes=45)
        for (t, v), nahoru, popis in ((max(hist, key=lambda p: p[1]), True, "max"),
                                      (min(hist, key=lambda p: p[1]), False, "min")):
            if t - hist[0][0] < kraj or hist[-1][0] - t < kraj:
                continue                  # vrchol na kraji okna = useknutý, nepopisovat
            vrchol(t, v, nahoru, popis, MODRA)

    # minimum a maximum předpovědi
    budouci = [(t, v) for t, v in d.fc_prumer if t > d.ted + timedelta(hours=1)]
    if len(budouci) >= 3:
        for (t, v), nahoru, popis in ((max(budouci, key=lambda p: p[1]), True, "max"),
                                      (min(budouci, key=lambda p: p[1]), False, "min")):
            vrchol(t, v, nahoru, popis, CERVENA)

    # ryska teď
    xt = X(d.ted)
    y = P[0] * S
    while y < P[1] * S:
        g.line([(xt, y), (xt, min(y + 6 * S, P[1] * S))], fill=(120, 120, 120), width=S)
        y += 11 * S
    g.rounded_rectangle([xt - 22 * S, (P[0] - 26) * S, xt + 22 * S, (P[0] - 4) * S],
                        radius=6 * S, fill=(128, 128, 128))
    text(xt / S, P[0] - 15, "teď", (255, 255, 255), 14, True, anchor="mm")

    # blok aktuálních hodnot vlevo nahoře (nad historií, kde nejsou ikonky)
    posledni = lambda r: next((v for v in reversed(r) if v is not None), None)
    venku_ted, tekv_ted = posledni(d.venku), posledni(d.model)

    def dlazdice(x, popis, hodnota, barva):
        text(x, IKONY_Y - 22, popis, MUT, 14)
        text(x, IKONY_Y + 8, hodnota, barva, 24, True)

    x = L
    if venku_ted is not None:
        dlazdice(x, "venku teď", _fmt(venku_ted) + " °C", MODRA)
        x += 170
    if tekv_ted is not None:
        dlazdice(x, "T ekv teď", _fmt(tekv_ted) + " °C", PUR)
        x += 170
    if d.vyhled_tekv and tekv_ted is not None:
        cil = d.ted + timedelta(hours=12)
        t_v, v_v = min(d.vyhled_tekv, key=lambda p: abs((p[0] - cil).total_seconds()))
        hod = round((t_v - d.ted).total_seconds() / 3600)
        if hod >= 1:
            rozdil = v_v - tekv_ted
            sipka = "↑" if rozdil > 0.05 else ("↓" if rozdil < -0.05 else "→")
            dlazdice(x, f"T ekv za {hod} h", f"{sipka} {_fmt(v_v)} °C", PUR)

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
        elif druh == "carky":
            for k in range(3):
                g.line([((x + k * 9) * S, y * S), ((x + k * 9 + 6) * S, y * S)], fill=col, width=int(2.0 * S))
        elif druh == "tenka":
            g.line([(x * S, y * S), ((x + 24) * S, y * S)], fill=col, width=int(1.6 * S))
        else:
            g.rectangle([x * S, (y - 4) * S, (x + 24) * S, (y + 3) * S], fill=col)
        text(x + 32, y, s, INK, 14, anchor="lm")

    leg(L, "cara", MODRA, "venku")
    leg(L + 125, "cara", PUR, "T ekv")
    leg(L + 230, "carky", PUR, "T ekv výhled")
    leg(L + 395, "cara", CERVENA, "předpověď")
    leg(L + 540, "tenka", ORANZ, "open-meteo")
    leg(L + 690, "tenka", ZELENA, "met.no")
    leg(L + 815, "pruh", ZLUTA, "okno průměru")

    out = img.resize(VYSTUP, Image.LANCZOS)
    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
