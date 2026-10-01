#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generátor vizuálního schématu MaR — jeden soubor pro všechna SVG.

    python3 gen_schema.py            přegeneruje všechno
    python3 gen_schema.py podklad    jen podklad.svg a podklad-bez-fve.svg
    python3 gen_schema.py vrstvy     jen pohyblivé vrstvy v-*.svg
    python3 gen_schema.py tlacitka   jen tlačítka t-*.svg
    python3 gen_schema.py ouska      jen ouška pod schématem o-*.svg
    python3 gen_schema.py --overit   nic nezapíše, jen porovná se stavem na disku
    python3 gen_schema.py --souradnice   vypíše procenta tlačítek do dashboardu

Podrobný návod, co kde měnit, je vedle v souboru CTIMNE.md.

ZÁSADA: SVG se nikdy neupravují ručně. Když se má něco posunout, změní se
číslo tady a pustí se generátor. Ruční oprava v SVG zmizí při nejbližším
přegenerování a nikdo neví proč.

Souřadnice jsou v plátně 1200 × 700, které je stejné pro všechny vrstvy.
Lícování je tím dané konstrukcí, ne pečlivostí.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

# ── plátno a paleta ──────────────────────────────────────────────────────
W, H = 1200, 700
OUT = Path(__file__).resolve().parent   # SVG leží vedle generátoru (složka schema/)
FONT = "Arial, Helvetica, sans-serif"

TMAVA = "#2b3138"        # obrysy
MODRA_POPIS = "#1b3a63"  # nadpisy prvků
SEDA_POPIS = "#6b7075"   # drobné popisky
CERVENA = "#e0402c"      # topná voda
MODRA = "#2f6fd0"        # zpátečka
ORANZ = "#f2a63b"        # elektřina
KULICKA_TEPLA = "#8f2216"
KULICKA_STUDENA = "#123f7d"
KULICKA_EL = "#a86a10"

RYCHLOST = 34.0          # px/s — jak rychle letí kulička

# ── společné kreslicí kousky ─────────────────────────────────────────────


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def platno(telo: list[str], xlink: bool = False) -> str:
    x = ' xmlns:xlink="http://www.w3.org/1999/xlink"' if xlink else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg"{x} '
            f'viewBox="0 0 {W} {H}" width="{W}" height="{H}">\n'
            + "".join(r + "\n" for r in telo) + "</svg>")


def txt(x, y, t, size, fill=MODRA_POPIS, anchor="middle") -> str:
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="{FONT}" '
            f'font-size="{size}" font-weight="700" fill="{fill}">{_esc(t)}</text>')


def trubka(d, barva, sirka=8) -> str:
    return (f'<path d="{d}" fill="none" stroke="{barva}" stroke-width="{sirka}" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="1.0"/>')


def hrdlo(x, y, w, h) -> str:
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" '
            f'fill="url(#kov)" stroke="{TMAVA}" stroke-width="1.5"/>')


def nadrz(cx, y0, y1, polosirka, poloosa, popisky) -> list[str]:
    """Válcová nádrž s víkem, dnem a přechodem teplá → studená."""
    x0, w = cx - polosirka, polosirka * 2
    r = []
    r.append(f'<ellipse cx="{cx}" cy="{y0}" rx="{polosirka}" ry="{poloosa}" fill="#3a4048"/>')
    r.append(f'<rect x="{x0}" y="{y0}" width="{w}" height="{y1 - y0}" '
             f'fill="url(#nadrz)" stroke="{TMAVA}" stroke-width="2"/>')
    r.append(f'<rect x="{x0}" y="{y0}" width="{w}" height="{y1 - y0}" fill="url(#plast)"/>')
    r.append(f'<ellipse cx="{cx}" cy="{y1}" rx="{polosirka}" ry="{poloosa}" '
             f'fill="#274a86" stroke="{TMAVA}" stroke-width="2"/>')
    return r


def cerpadlo(cx, cy, popis) -> list[str]:
    r = [f'<circle cx="{cx}" cy="{cy}" r="30" fill="{MODRA}" stroke="{TMAVA}" '
         f'stroke-width="2.5" filter="url(#stin)"/>',
         f'<circle cx="{cx}" cy="{cy}" r="21" fill="#e8ecf0" stroke="{TMAVA}" '
         f'stroke-width="1.5"/>']
    r.append(txt(cx, cy - 58, popis[0], 14))
    r.append(txt(cx, cy - 42, popis[1], 14))
    return r


# ══════════════════════════════════════════════════════════════════════════
#  ČÁST 1 — PODKLAD
# ══════════════════════════════════════════════════════════════════════════

DEFS = """<defs>
  <linearGradient id="kov" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0"   stop-color="#c9ced6"/>
    <stop offset="0.35" stop-color="#f6f8fa"/>
    <stop offset="1"   stop-color="#b8bfc9"/>
  </linearGradient>
  <linearGradient id="nadrz" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0"    stop-color="#e8422c"/>
    <stop offset="0.40" stop-color="#e2603a"/>
    <stop offset="0.58" stop-color="#5b7fc4"/>
    <stop offset="1"    stop-color="#1f4f9e"/>
  </linearGradient>
  <linearGradient id="plast" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#ffffff" stop-opacity="0.45"/>
    <stop offset="0.5" stop-color="#ffffff" stop-opacity="0"/>
    <stop offset="1" stop-color="#000000" stop-opacity="0.22"/>
  </linearGradient>
  <linearGradient id="panel" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#3d6fb5"/>
    <stop offset="1" stop-color="#1b3f7a"/>
  </linearGradient>
  <linearGradient id="zed" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#cfd3d8"/>
    <stop offset="1" stop-color="#aeb4bb"/>
  </linearGradient>
  <pattern id="srafy" width="14" height="14" patternUnits="userSpaceOnUse"
           patternTransform="rotate(45)">
    <line x1="0" y1="0" x2="0" y2="14" stroke="#9aa1a9" stroke-width="3"/>
  </pattern>
  <filter id="stin" x="-25%" y="-25%" width="150%" height="150%">
    <feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.28"/>
  </filter>
</defs>"""

# ── zeď (prostup venku / uvnitř) ─────────────────────────────────────────
# Pata zdi končí nad spodní lištou tlačítek; kdyby se lišta posunula,
# mění se tady jedno číslo.
ZED_X, ZED_Y, ZED_W, ZED_H = 428, 20, 44, 618

# ── trasy potrubí (sdílené s vrstvami kuliček – proto nahoře) ────────────
T_PRIMAR_TOPI = "M251 403 L251 296 L582 296 L702 296 L702 330"
T_PRIMAR_ZPET = "M702 556 L702 600 L486 600 L486 505 L304 505"
T_TUV_TOPI = "M251 403 L251 296 L582 296 L582 364"
T_TUV_ZPET = "M582 515 L582 600 L486 600 L486 505 L304 505"
T_SEK_RAD = "M744 364 L784 364 L784 520 L880 520"
T_SEK_ZPET = "M1100 520 L1130 520 L1130 600 L702 600 L702 556"
T_EL_SIT_DOVNITR = "M138 44 L660 44 L660 95"
T_EL_PANELY = "M376 127 L600 127"
T_EL_BAT_NABIJ = "M720 127 L840 127"
T_EL_DUM = "M660 160 L660 206"


def podklad(fve: bool = True) -> str:
    r: list[str] = [DEFS]
    r.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#fbfbfc"/>')

    # zeď
    r.append(f'<rect x="{ZED_X}" y="{ZED_Y}" width="{ZED_W}" height="{ZED_H}" '
             f'fill="url(#zed)" stroke="{TMAVA}" stroke-width="2"/>')
    r.append(f'<rect x="{ZED_X}" y="{ZED_Y}" width="{ZED_W}" height="{ZED_H}" '
             f'fill="url(#srafy)" opacity="0.5"/>')

    if fve:
        # stožár vysokého napětí
        r.append('<path d="M62 170 L100 20 L138 170 M70 138 L130 138 M78 100 L122 100" '
                 f'fill="none" stroke="{TMAVA}" stroke-width="2.5"/>')
        r.append(f'<path d="M58 34 L142 34 M64 50 L136 50" stroke="{TMAVA}" '
                 f'stroke-width="2.5"/>')
        r.append(txt(100, 132, "Síť", 15))

        # dva solární panely
        for px in (200, 292):
            r.append(f'<rect x="{px}" y="96" width="84" height="62" rx="3" '
                     f'fill="url(#panel)" stroke="{TMAVA}" stroke-width="2" '
                     f'filter="url(#stin)"/>')
            r.append(f'<line x1="{px + 28}" y1="96" x2="{px + 28}" y2="158" '
                     f'stroke="#8fb2e0" stroke-width="1.5"/>')
            r.append(f'<line x1="{px + 56}" y1="96" x2="{px + 56}" y2="158" '
                     f'stroke="#8fb2e0" stroke-width="1.5"/>')
            r.append(f'<line x1="{px}" y1="127" x2="{px + 84}" y2="127" '
                     f'stroke="#8fb2e0" stroke-width="1.5"/>')
        r.append(txt(284, 182, "Solární panely", 15))

    # tepelné čerpadlo
    r.append(f'<rect x="54" y="380" width="250" height="150" rx="10" fill="url(#kov)" '
             f'stroke="{TMAVA}" stroke-width="2.5" filter="url(#stin)"/>')
    r.append('<rect x="54" y="380" width="250" height="150" rx="10" fill="url(#plast)"/>')
    r.append(f'<circle cx="128" cy="452" r="46" fill="#3a4048" stroke="{TMAVA}" '
             f'stroke-width="2.5"/>')
    r.append('<circle cx="128" cy="452" r="40" fill="#dfe3e8"/>')
    r.append(txt(128, 514, "Větrák", 11, fill="#333a42"))
    r.append('<circle cx="251" cy="445" r="42" fill="#eef0f3" stroke="#8b9199" '
             'stroke-width="2"/>')
    r.append(txt(251, 524, "BIV", 11, fill="#4e555e"))
    r.append('<rect x="60" y="530" width="18" height="14" fill="#8b9199"/>')
    r.append('<rect x="280" y="530" width="18" height="14" fill="#8b9199"/>')
    r.append(txt(62, 522, "ACOND", 13, fill=SEDA_POPIS, anchor="start"))
    r.append(txt(179, 560, "Tepelné čerpadlo", 15))

    if fve:
        # vedení elektřiny
        r.append(trubka("M138 44 L660 44 L660 95", ORANZ, 5))
        r.append(trubka(T_EL_PANELY, ORANZ, 5))
        r.append(trubka(T_EL_BAT_NABIJ, ORANZ, 5))
        r.append(trubka(T_EL_DUM, ORANZ, 5))
        r.append(txt(660, 232, "dům", 13, fill=SEDA_POPIS))

    # potrubí
    r.append(trubka(T_PRIMAR_TOPI, CERVENA))
    r.append(trubka("M582 296 L582 364", CERVENA))
    r.append(trubka(T_SEK_RAD, CERVENA))
    r.append(trubka("M582 515 L582 600", MODRA))
    r.append(trubka("M1100 520 L1130 520 L1130 600", MODRA))
    r.append(trubka("M486 600 L1130 600", MODRA))
    r.append(trubka("M702 556 L702 600", MODRA))
    r.append(trubka("M486 600 L486 505 L304 505", MODRA))
    r.append(f'<circle cx="702" cy="600" r="6" fill="{MODRA}"/>')
    r.append(f'<circle cx="582" cy="600" r="6" fill="{MODRA}"/>')

    if fve:
        # střídač a baterie
        r.append(f'<rect x="600" y="95" width="120" height="65" rx="6" fill="url(#kov)" '
                 f'stroke="{TMAVA}" stroke-width="2" filter="url(#stin)"/>')
        r.append('<rect x="600" y="95" width="120" height="65" rx="6" fill="url(#plast)"/>')
        r.append('<circle cx="706" cy="149" r="4" fill="#4aa63c"/>')
        r.append(txt(660, 133, "Střídač", 14, fill="#4e555e"))
        r.append(f'<rect x="840" y="100" width="76" height="54" rx="5" fill="#3a4048" '
                 f'stroke="{TMAVA}" stroke-width="2" filter="url(#stin)"/>')
        r.append('<rect x="852" y="112" width="22" height="13" rx="2" fill="#e8ecf0"/>')
        r.append('<rect x="882" y="112" width="22" height="13" rx="2" fill="#e8ecf0"/>')
        r.append(txt(878, 90, "Baterie", 15))

    # trojcestný ventil
    r.append(f'<circle cx="582" cy="296" r="21" fill="#e8a83b" stroke="{TMAVA}" '
             f'stroke-width="2.5" filter="url(#stin)"/>')
    r.append(txt(614, 284, "3cestný", 13, anchor="start"))

    # boiler TUV
    r += nadrz(582, 364, 504, 42, 11, None)
    r.append(txt(582, 400, "Boiler", 15, fill="#ffffff"))
    r.append(txt(582, 418, "TUV", 15, fill="#ffffff"))

    # akumulační nádrž
    r += nadrz(702, 330, 556, 42, 11, None)
    r.append('<line x1="660" y1="426" x2="744" y2="426" stroke="#ffffff" '
             'stroke-width="2" opacity="0.55"/>')
    r.append(txt(702, 380, "AKU", 17, fill="#ffffff"))

    # topná soustava — jeden neutrální symbol
    r.append(txt(990, 476, "Topná soustava", 16))
    r.append(f'<rect x="880" y="488" width="220" height="64" rx="6" fill="url(#kov)" '
             f'stroke="{TMAVA}" stroke-width="2" filter="url(#stin)"/>')
    for lx in range(906, 1100, 26):
        r.append(f'<line x1="{lx}" y1="488" x2="{lx}" y2="552" stroke="#9aa1a9" '
                 f'stroke-width="2"/>')

    # Stav regulace kreslí integrace jako obrázek (stav_png.py) i s nadpisem;
    # rámeček x 795–1185, y 188–358 musí zůstat volný.

    # čerpadla
    r += cerpadlo(534, 600, ("Primární", "čerpadlo"))
    r += cerpadlo(816, 600, ("Sekundární", "čerpadlo"))

    # hrdla
    r.append(hrdlo(242, 396, 18, 14))
    r.append(hrdlo(693, 323, 18, 14))
    r.append(hrdlo(737, 355, 14, 18))
    r.append(hrdlo(693, 549, 18, 14))
    r.append(hrdlo(573, 357, 18, 14))
    r.append(hrdlo(573, 508, 18, 14))

    # popisky hodnot (samotné hodnoty dosazuje dashboard)
    r.append(txt(380, 236, "venku", 12, fill=SEDA_POPIS))
    r.append(txt(100, 200, "Příkon", 12, fill=SEDA_POPIS))
    r.append(txt(100, 250, "Výkon", 12, fill=SEDA_POPIS))
    r.append(txt(100, 300, "COP", 12, fill=SEDA_POPIS))
    if fve:
        r.append(txt(284, 206, "Dnes vyrobeno", 12, fill=SEDA_POPIS))
    r.append(txt(524, 236, "uvnitř", 12, fill=SEDA_POPIS))

    return platno(r)



# ══════════════════════════════════════════════════════════════════════════
#  ČÁST 2 — POHYBLIVÉ VRSTVY
# ══════════════════════════════════════════════════════════════════════════

def _lopatka(cx, cy, R, uhel) -> str:
    d = (f"M{cx} {cy} Q {cx + R * 0.30:.1f} {cy - R * 0.62:.1f}, "
         f"{cx + R * 0.089:.1f} {cy - R:.1f} "
         f"Q {cx - R * 0.28:.1f} {cy - R * 0.90:.1f}, {cx} {cy} Z")
    return (f'<path d="{d}" fill="#9aa1a9" stroke="#7d848c" stroke-width="1" '
            f'transform="rotate({uhel} {cx} {cy})"/>')


def vrtule(cx, cy, R, dur, mrizka: bool) -> tuple[list[str], list[str]]:
    """Vrátí (lopatky, statická výbava). Lopatky se buď točí, nebo stojí."""
    lopatky = [_lopatka(cx, cy, R, u) for u in (0, 72, 144, 216, 288)]
    statika: list[str] = []
    if mrizka:
        statika.append(f'<circle cx="{cx}" cy="{cy}" r="9" fill="#6f767e" '
                       f'stroke="#4e555e" stroke-width="1.5"/>')
        for k in (0.4125, 0.6375, 0.8625, 1.0625):
            statika.append(f'<circle cx="{cx}" cy="{cy}" r="{R * k}" fill="none" '
                           f'stroke="#3a4048" stroke-width="1.5" opacity="0.45"/>')
        for i in range(8):
            a = math.radians(i * 45)
            statika.append(
                f'<line x1="{cx}" y1="{cy}" x2="{cx + R * 1.2 * math.cos(a):.1f}" '
                f'y2="{cy + R * 1.2 * math.sin(a):.1f}" stroke="#3a4048" '
                f'stroke-width="1.3" opacity="0.4"/>')
    else:
        statika.append(f'<circle cx="{cx}" cy="{cy}" r="5" fill="#6f767e"/>')
    return lopatky, statika


def toci_se(lopatky, cx, cy, dur) -> str:
    hlava = (f'<g><animateTransform attributeName="transform" type="rotate" '
             f'from="0 {cx} {cy}" to="360 {cx} {cy}" dur="{dur}s" '
             f'repeatCount="indefinite"/>')
    return hlava + "\n".join(lopatky) + "</g>"


def _delka(d: str) -> float:
    body = []
    for kus in d.replace("M", " ").replace("L", " ").split():
        body.append(float(kus))
    dvojice = list(zip(body[0::2], body[1::2]))
    return sum(math.dist(dvojice[i], dvojice[i + 1]) for i in range(len(dvojice) - 1))


def kulicky(ident, d, pocet, barva, r=6) -> list[str]:
    """Kuličky letí po trase rychlostí RYCHLOST px/s, rozprostřené rovnoměrně."""
    dur = _delka(d) / RYCHLOST
    out = [f'<path id="{ident}" d="{d}" fill="none" stroke="none"/>']
    for i in range(pocet):
        out.append(
            f'<circle r="{r}" fill="{barva}" opacity="0.92">'
            f'<animateMotion dur="{dur:.2f}s" repeatCount="indefinite" '
            f'begin="-{i * dur / pocet:.2f}s"><mpath href="#{ident}"/>'
            f'</animateMotion></circle>')
    return out


def vrstvy() -> dict[str, str]:
    v: dict[str, str] = {}
    v["v-prazdno"] = platno([], xlink=True)

    # větrák tepelného čerpadla
    lop, sta = vrtule(128, 452, 36, "2.6", mrizka=True)
    v["v-vetrak-stoji"] = platno(lop + sta, xlink=True)
    v["v-vetrak-toci"] = platno([toci_se(lop, 128, 452, "2.6")] + sta, xlink=True)

    # vrtulky obou čerpadel
    for jm, cx, cy in (("cerp1", 534, 600), ("cerp2", 816, 600)):
        lop, sta = vrtule(cx, cy, 19, "1.8", mrizka=False)
        v[f"v-{jm}-stoji"] = platno(lop + sta, xlink=True)
        v[f"v-{jm}-toci"] = platno([toci_se(lop, cx, cy, "1.8")] + sta, xlink=True)

    # poloha trojcestného: šipka ukazuje, kam voda teče
    v["v-ventil-aku"] = platno(
        [f'<path d="M572 288 L590 296 L572 304 Z" fill="{TMAVA}"/>'], xlink=True)
    v["v-ventil-tuv"] = platno(
        [f'<path d="M574 288 L590 288 L582 306 Z" fill="{TMAVA}"/>'], xlink=True)

    # spirála bivalence
    biv = ("M216 508 L216 494 L224 494 L224 508 L232 508 L232 494 L240 494 "
           "L240 508 L248 508 L248 494 L256 494 L256 508 L264 508 L264 494 "
           "L272 494 L272 508 L280 508 L280 494 L288 494 L288 508")
    v["v-biv-klid"] = platno(
        [f'<path d="{biv}" fill="none" stroke="{TMAVA}" stroke-width="4" '
         f'stroke-linecap="round" stroke-linejoin="round"/>'], xlink=True)
    v["v-biv-topi"] = platno([
        f'<path d="{biv}" fill="none" stroke="{CERVENA}" stroke-width="12" '
        f'stroke-linecap="round" stroke-linejoin="round" opacity="0.25">'
        f'<animate attributeName="opacity" values="0.12;0.38;0.12" dur="2.2s" '
        f'repeatCount="indefinite"/></path>',
        f'<path d="{biv}" fill="none" stroke="{CERVENA}" stroke-width="4" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'], xlink=True)

    # kuličky vody.
    # Kulička nese teplo, ne vodu. Při odmrazování a při chlazení jde teplo
    # opačným směrem — ven z domu do stroje — takže se barvy prohodí:
    # výstupem odchází studená, zpátečkou se vrací teplejší.
    # Zpětné varianty mají vlastní id trasy, aby si dvě vrstvy v DOM
    # nepřebíraly `mpath`.
    for pozp, tepla, stud in (("", KULICKA_TEPLA, KULICKA_STUDENA),
                              ("-zpetne", KULICKA_STUDENA, KULICKA_TEPLA)):
        z = "z" if pozp else ""
        v[f"v-kul-primar{pozp}"] = platno(
            kulicky(f"t_ph{z}", T_PRIMAR_TOPI, 3, tepla)
            + kulicky(f"t_pc{z}", T_PRIMAR_ZPET, 3, stud), xlink=True)
        v[f"v-kul-tuv{pozp}"] = platno(
            kulicky(f"t_th{z}", T_TUV_TOPI, 2, tepla)
            + kulicky(f"t_tc{z}", T_TUV_ZPET, 3, stud), xlink=True)
        v[f"v-kul-sekundar{pozp}"] = platno(
            kulicky(f"t_sh{z}", T_SEK_RAD, 3, tepla)
            + kulicky(f"t_sc{z}", T_SEK_ZPET, 3, stud), xlink=True)

    # kuličky elektřiny
    v["v-el-sit-dovnitr"] = platno(
        kulicky("t_esd", T_EL_SIT_DOVNITR, 3, KULICKA_EL, r=5), xlink=True)
    v["v-el-sit-ven"] = platno(
        kulicky("t_esv", "M660 95 L660 44 L138 44", 3, KULICKA_EL, r=5), xlink=True)
    v["v-el-panely"] = platno(
        kulicky("t_ep", T_EL_PANELY, 2, KULICKA_EL, r=5), xlink=True)
    v["v-el-bat-nabij"] = platno(
        kulicky("t_ebn", T_EL_BAT_NABIJ, 1, KULICKA_EL, r=5), xlink=True)
    v["v-el-bat-vybij"] = platno(
        kulicky("t_ebv", "M840 127 L720 127", 1, KULICKA_EL, r=5), xlink=True)
    v["v-el-dum"] = platno(
        kulicky("t_ed", T_EL_DUM, 1, KULICKA_EL, r=5), xlink=True)
    return v


# ══════════════════════════════════════════════════════════════════════════
#  ČÁST 3 — TLAČÍTKA
# ══════════════════════════════════════════════════════════════════════════

SEDA_VYPLN, SEDA_RAM, SEDA_TEXT = "#e3e6ea", "#b6bcc4", "#3b424a"
ZELENA_VYPLN, ZELENA_RAM, ZELENA_TEXT = "#2e7d32", "#1b5e20", "#ffffff"

# id, nápis, x, y, šířka, výška, písmo, tvar klikací plochy
TLACITKA = [
    ("topit",     "Topit",     45, 586, 124, 38, 20, "kamen"),
    ("netopit",   "Netopit",  179, 586, 124, 38, 20, "kamen"),
    ("ekviterma", "Ekviterma",  20, 648, 88, 36, 17, "rezim"),
    ("staly",     "Stálý",     118, 648, 88, 36, 17, "rezim"),
    ("minimum",   "Minimum",   216, 648, 88, 36, 17, "rezim"),
    ("boost",     "BOOST",     314, 648, 88, 36, 17, "rezim"),
    ("bezmar",    "Bez MaR",   412, 648, 88, 36, 17, "rezim"),
    ("nastaveni", "Nastavení", 605, 648, 88, 36, 17, "rezim"),
    ("zebra",     "Zebra",     798, 648, 88, 36, 17, "rezim"),
    ("dennoc",    "Den/noc",   896, 648, 88, 36, 17, "rezim"),
    ("okna",      "Okna ±°C",  994, 648, 88, 36, 17, "rezim"),
    ("dovolena",  "Dovolená", 1092, 648, 88, 36, 17, "rezim"),
    ("fvetuv",    "FVE TUV",   950,  12, 110, 36, 17, "fve"),
    ("fvetopeni", "FVE topení", 1070, 12, 110, 36, 17, "fve"),
]

# Tlačítka, která jen navigují a nemají stav — bez zelené vrstvy.
BEZ_STAVU = {"nastaveni"}

TVARY = {"kamen": (124, 38), "rezim": (88, 36), "fve": (110, 36)}


def _pilulka(napis, x, y, w, h, fs, aktivni) -> str:
    vypln, ram, barva = ((ZELENA_VYPLN, ZELENA_RAM, ZELENA_TEXT) if aktivni
                         else (SEDA_VYPLN, SEDA_RAM, SEDA_TEXT))
    ty = y + h / 2 + fs * 0.36
    return (f'  <g>\n'
            f'    <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" ry="9"\n'
            f'          fill="{vypln}" stroke="{ram}" stroke-width="2"/>\n'
            f'    <text x="{x + w / 2:g}" y="{ty:g}" text-anchor="middle"\n'
            f'          font-family="{FONT}" font-size="{fs}" font-weight="700"\n'
            f'          fill="{barva}">{_esc(napis)}</text>\n'
            f'  </g>')


def tlacitka() -> dict[str, str]:
    t: dict[str, str] = {}
    t["t-podklad"] = platno([_pilulka(n, x, y, w, h, fs, False)
                             for _i, n, x, y, w, h, fs, _tv in TLACITKA])
    for _i, n, x, y, w, h, fs, _tv in TLACITKA:
        if _i in BEZ_STAVU:
            continue
        t[f"t-{_i}-on"] = platno([_pilulka(n, x, y, w, h, fs, True)])
    for tvar, (w, h) in TVARY.items():
        t[f"t-klik-{tvar}"] = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}">\n'
            f'  <rect width="{w}" height="{h}" rx="9" ry="9" fill="none" '
            f'pointer-events="all"/>\n</svg>')
    return t


# ══════════════════════════════════════════════════════════════════════════
#  ČÁST 4 — OUŠKA POD SCHÉMATEM
# ══════════════════════════════════════════════════════════════════════════
#
# Samostatná karta těsně pod schématem (vlastní plátno 1200 × OUSKO_H), aby
# se nemusely přepočítávat souřadnice vrstev schématu. Klepnutí přepíná
# select.mar_vrstva; aktivní ouško zvýrazní vrstva o-<id>-on.svg.
# Ouška, která ještě nemají obsah (PRIPRAVUJE_SE), jsou ztlumená a bez klikací
# plochy v dashboardu.

OUSKO_H = 64
OUSKO_W, OUSKO_MEZERA, OUSKO_X0, OUSKO_Y = 180, 12, 18, 4

# id, nápis, volba selectu, ikona (path v poli 24 × 24)
OUSKA = [
    ("schema",    "Schéma",    "Schéma",
     "M4 5h6v5H4zM14 14h6v5h-6zM7 10v6.5h7M17 10V5"),
    ("teploty",   "Teploty",   "Teploty",
     "M3 17l5-6 4 3 7-8M3 21h18"),
    ("energie",   "Energie",   "Energie",
     "M13 2 4 14h7l-1 8 9-12h-7z"),
    ("ekviterma", "Ekviterma", "Ekviterma",
     "M3 5c5 3 11 8 18 14M10 11a2 2 0 1 0 4 0a2 2 0 1 0-4 0"),
    ("pocasi",    "Počasí",    "Počasí",
     "M9 5.5a3.5 3.5 0 1 0 0 7M7 19h10a3.5 3.5 0 0 0 0-7 5 5 0 0 0-9.4 1.6A3 3 0 0 0 7 19z"),
    ("stroj",     "Stroj",     "Stroj",
     "M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"),
]
PRIPRAVUJE_SE = {"energie", "ekviterma", "pocasi", "stroj"}

OUSKO_VYPLN, OUSKO_RAM, OUSKO_TEXT = "#eef1f4", "#b6bcc4", "#3b424a"
OUSKO_AKT_VYPLN, OUSKO_AKT_RAM, OUSKO_AKT_TEXT = "#1b3a63", "#122a49", "#ffffff"


def _ousko_x(i: int) -> int:
    return OUSKO_X0 + i * (OUSKO_W + OUSKO_MEZERA)


def _ousko(i, napis, ikona, aktivni=False, ztlumene=False) -> str:
    x, y, w, h = _ousko_x(i), OUSKO_Y, OUSKO_W, OUSKO_H - OUSKO_Y - 6
    vypln, ram, barva = ((OUSKO_AKT_VYPLN, OUSKO_AKT_RAM, OUSKO_AKT_TEXT) if aktivni
                         else (OUSKO_VYPLN, OUSKO_RAM, OUSKO_TEXT))
    r = 12
    # ouško pořadače: rovná horní hrana (přiléhá ke schématu), zaoblený spodek
    d = (f"M{x} {y} H{x + w} V{y + h - r} Q{x + w} {y + h} {x + w - r} {y + h} "
         f"H{x + r} Q{x} {y + h} {x} {y + h - r} Z")
    op = ' opacity="0.45"' if ztlumene else ""
    ix, iy = x + 26, y + h / 2 - 12
    return (f'  <g{op}>\n'
            f'    <path d="{d}" fill="{vypln}" stroke="{ram}" stroke-width="2"/>\n'
            f'    <path d="{ikona}" transform="translate({ix:g} {iy:g})" fill="none" '
            f'stroke="{barva}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>\n'
            f'    <text x="{x + 58}" y="{y + h / 2 + 7:g}" font-family="{FONT}" '
            f'font-size="20" font-weight="700" fill="{barva}">{_esc(napis)}</text>\n'
            f'  </g>')


def _platno_ousek(telo: list[str]) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {OUSKO_H}" '
            f'width="{W}" height="{OUSKO_H}">\n' + "".join(r + "\n" for r in telo) + "</svg>")


def ouska() -> dict[str, str]:
    o: dict[str, str] = {}
    o["o-podklad"] = _platno_ousek([
        _ousko(i, n, ik, ztlumene=_i in PRIPRAVUJE_SE)
        for i, (_i, n, _v, ik) in enumerate(OUSKA)])
    for i, (_i, n, _v, ik) in enumerate(OUSKA):
        if _i in PRIPRAVUJE_SE:
            continue
        o[f"o-{_i}-on"] = _platno_ousek([_ousko(i, n, ik, aktivni=True)])
    o["o-klik"] = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {OUSKO_W} {OUSKO_H}" '
        f'width="{OUSKO_W}" height="{OUSKO_H}">\n'
        f'  <rect width="{OUSKO_W}" height="{OUSKO_H}" fill="none" '
        f'pointer-events="all"/>\n</svg>')
    return o


# ══════════════════════════════════════════════════════════════════════════

def vse(cast: str | None) -> dict[str, str]:
    s: dict[str, str] = {}
    if cast in (None, "podklad"):
        s["podklad"] = podklad(fve=True)
        s["podklad-bez-fve"] = podklad(fve=False)
    if cast in (None, "vrstvy"):
        s.update(vrstvy())
    if cast in (None, "tlacitka"):
        s.update(tlacitka())
    if cast in (None, "ouska"):
        s.update(ouska())
    return s


def souradnice() -> None:
    """Procenta pro picture-elements. Pozor: udávají STŘED prvku, ne roh."""
    print("# id, left %, top %, šířka % (klikací plocha)")
    for _i, _n, x, y, w, h, _fs, tvar in TLACITKA:
        print(f"{_i:10s} left: {(x + w / 2) / W * 100:6.3f}%  "
              f"top: {(y + h / 2) / H * 100:6.3f}%  "
              f"width: {TVARY[tvar][0] / W * 100:5.3f}%")
    print("\n# ouška (plátno 1200 × %d): id, left %%, šířka %%" % OUSKO_H)
    for i, (_i, _n, _v, _ik) in enumerate(OUSKA):
        print(f"{_i:10s} left: {(_ousko_x(i) + OUSKO_W / 2) / W * 100:6.3f}%  "
              f"width: {OUSKO_W / W * 100:5.3f}%")


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if "--souradnice" in args:
        souradnice()
        return 0
    overit = "--overit" in args
    args = [a for a in args if not a.startswith("--")]
    cast = args[0] if args else None
    if cast not in (None, "podklad", "vrstvy", "tlacitka", "ouska"):
        print(f"neznámá část: {cast}")
        return 2

    soubory = vse(cast)
    if overit:
        shoda = rozdil = chybi = 0
        for jm, obsah in sorted(soubory.items()):
            p = OUT / f"{jm}.svg"
            if not p.exists():
                print(f"CHYBÍ NA DISKU  {p.name}")
                chybi += 1
            elif p.read_text(encoding="utf-8") == obsah:
                shoda += 1
            else:
                print(f"LIŠÍ SE        {p.name}")
                rozdil += 1
        print(f"\nshoda {shoda} · liší se {rozdil} · chybí {chybi}")
        return 0 if rozdil == chybi == 0 else 1

    OUT.mkdir(parents=True, exist_ok=True)
    for jm, obsah in sorted(soubory.items()):
        (OUT / f"{jm}.svg").write_text(obsah, encoding="utf-8")
    print(f"zapsáno {len(soubory)} souborů do {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
