"""Obrázek schématu pro příručku (kapitola Schéma, #schema-popis).

Skládá vrstvy, které nakreslil gen_schema.py, přidá ukázkové hodnoty na
stejná místa jako dashboard a očíslované značky 1–14. Seznam k značkám je
v příručce. Po změně schématu: nejdřív gen_schema.py, pak tenhle skript,
a výsledné SVG vložit do prirucka/index.html jako data:image/svg+xml;base64.

    python3 gen_obrazek_prirucka.py   →  schema-prirucka.svg vedle skriptu
"""
import re
from pathlib import Path
B = Path(__file__).resolve().parent.parent
SCH, KOMP = B/'schema', B/'kompresor'

def vnitrek(p):
    s = p.read_text(encoding='utf-8')
    s = re.sub(r'<\?xml[^>]*>', '', s)
    s = re.sub(r'^\s*<svg[^>]*>', '', s.strip(), count=1)
    return re.sub(r'</svg>\s*$', '', s)

vrstvy = ['podklad', 'v-vetrak-toci', 'v-cerp1-toci', 'v-cerp2-toci', 'v-ventil-aku',
          'v-biv-klid', 'v-kul-primar', 'v-kul-sekundar', 'v-el-panely', 'v-el-dum',
          'v-el-bat-nabij', 't-podklad', 't-ekviterma-on', 't-zebra-on', 't-fvetuv-on']
telo = [f'<g>{vnitrek(SCH / (v + ".svg"))}</g>' for v in vrstvy]

# emblém kompresoru: střed 20,9 % / 63,6 %, šířka 7,7 % plátna
w = 0.077 * 1200
cx, cy = 0.209 * 1200, 0.636 * 700
telo.append(f'<svg x="{cx - w/2:.1f}" y="{cy - w/2:.1f}" width="{w:.1f}" height="{w:.1f}" '
            f'viewBox="0 0 128 128">{vnitrek(KOMP / "topeni-2b.svg")}</svg>')

F = 'font-family="Arial, Helvetica, sans-serif"'
HALO = 'paint-order="stroke" stroke="#ffffff" stroke-width="4" stroke-linejoin="round"'
def hod(lx, ty, t, maly=False, tmavy=False):
    x, y = lx * 12, ty * 7 + (26 if maly else 0)
    size, weight = (17, 600) if maly else (21, 700)
    if tmavy:
        fill = '#dfe8f5' if maly else '#ffffff'
        halo = 'paint-order="stroke" stroke="#16324f" stroke-width="4" stroke-linejoin="round"'
    else:
        fill, halo = ('#5a6068' if maly else '#101418'), HALO
    # obrys zvlášť pod textem – spolehlivější než paint-order
    zakl = (f'x="{x:.1f}" y="{y + size*0.36:.1f}" text-anchor="middle" {F} '
            f'font-size="{size}" font-weight="{weight}"')
    obrys = re.search(r'stroke="([^"]+)"', halo).group(1)
    return (f'<text {zakl} fill="{obrys}" stroke="{obrys}" stroke-width="4" '
            f'stroke-linejoin="round">{t}</text><text {zakl} fill="{fill}">{t}</text>')

hodnoty = [
    (31.7, 36.6, '12,4 °C'), (43.7, 36.6, '22,6 °C'), (43.7, 36.6, '(22,5 °C)', True),
    (8.3, 31.7, '640 W'), (8.3, 38.9, '3 120 W'), (8.3, 46.0, '4,9'), (8.3, 50.0, 'Dnes 3,4 kWh'),
    (23.3, 9.1, 'Import 1,8 kWh'), (23.3, 11.7, 'Export 4,2 kWh'),
    (24.6, 51.4, '32,5 °C'), (31.0, 57.5, '2 450 rpm'), (31.0, 57.5, '(3 000 rpm)', True),
    (31.0, 64.9, '28,1 °C'), (31.0, 64.9, '(28,4 °C)', True),
    (48.5, 65.1, '47,5 °C', False, True), (48.5, 65.1, '(48,0 °C)', True, True),
    (23.7, 32.0, '18,6 kWh'), (55.0, 10.6, '3 850 W'), (44.5, 18.1, '5 900 W'),
    (65.0, 18.1, '−1 210 W'), (55.0, 27.1, '840 W'), (79.8, 18.1, '82 %'),
]
for h in hodnoty:
    telo.append(hod(*h))

# stav regulace (v HA ho kreslí image.mar_stav_regulace; tady vektorově)
stav = [(0, 20, 700, '#1b3a63', 'Stav regulace'),
        (30, 16, 700, '#101418', 'Ekviterma'),
        (53, 15, 400, '#101418', 'Topí se, MaR reguluje. Cíl zpátečky 28,4 °C,'),
        (72, 15, 400, '#101418', 'na registru 28,4 °C. · FVE TUV: čekám'),
        (91, 15, 400, '#101418', 'na přebytek'),
        (114, 15, 400, '#3b424a', 'Zápis zpátečky – zapisuje.'),
        (136, 15, 400, '#3b424a', 'Zebra: topení · konec fáze v 14:35')]
for dy, fs, fw, c, t in stav:
    telo.append(f'<text x="795" y="{188 + dy + fs:.0f}" {F} font-size="{fs}" '
                f'font-weight="{fw}" fill="{c}">{t}</text>')

# značky: (číslo, x, y)
ZNACKY = [
    (1, 470, 70), (2, 192, 400), (3, 455, 225), (4, 340, 532), (5, 612, 330),
    (6, 530, 430), (7, 1000, 440), (8, 660, 600), (9, 770, 200),
    (10, 22, 605), (11, 518, 666), (12, 940, 628), (13, 930, 30), (14, 712, 666),
]
for n, x, y in ZNACKY:
    telo.append(f'<g><circle cx="{x}" cy="{y}" r="16" fill="#d62728" stroke="#ffffff" '
                f'stroke-width="3"/><text x="{x}" y="{y + 6.5}" text-anchor="middle" {F} '
                f'font-size="18" font-weight="700" fill="#ffffff">{n}</text></g>')

svg = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
       'viewBox="0 0 1200 700" width="1200" height="700" role="img" '
       'aria-label="Schéma MaR s očíslovanými částmi">\n' + '\n'.join(telo) + '\n</svg>\n')
(Path(__file__).resolve().parent / 'schema-prirucka.svg').write_text(svg, encoding='utf-8')
print(len(svg)//1024, 'kB')
