"""Render tabulky statistiky do PNG (styl Acondacu, tři sloupce naráz).

Proč vlastní render a ne screenshot dashboardu
----------------------------------------------
HA nativně „vyfoť tuhle kartu" neumí (umí jen export dat do CSV v panelu
Historie a v energetickém dashboardu). Server-side render je ale i lepší:
vypadá stejně na každém telefonu, nezávisí na šířce displeje, na světlém či
tmavém režimu ani na velikosti fontu, a nikdy se neusekne. Snímky poslané do
skupiny jsou tím porovnatelné.

Pillow je v jádře HA vždy. Přibalený DejaVuSans kvůli diakritice – stejná
mašinérie jako u grafu ekvitermní křivky (`image.py`).

Rozlišení: kreslíme přímo ve 2× (`S = 2`) a servírujeme bez zmenšení. FreeType
glyfy antialiasuje sám, takže na text není potřeba supersampling jako u křivky
(tam se S=4 zmenšuje LANCZOSem kvůli oblým hranám).

Bez HA závislostí -> jde zavolat i ze testu a vykreslit do souboru.
"""

from __future__ import annotations

import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# --- plátno (logické body; skutečné pixely = × S) -------------------------- #
S = 2
W = 560
PAD = 16
COL_LABEL = 194
COL_W = 110
COL_W_DAY = 88     # denní rozpad: osm sloupců, užší

ROW_H = 21
GROUP_H = 27
HEAD_H = 76
FOOT_H = 62

# --- barvy ----------------------------------------------------------------- #
C_BG = (255, 255, 255)
C_TITLE = (51, 51, 51)
C_TEXT = (34, 34, 34)
C_LABEL = (85, 85, 85)
C_GROUP = (63, 81, 181)
C_RULE = (224, 224, 224)
C_RULE_SOFT = (240, 240, 240)
C_HEAD_BG = (63, 81, 181)
C_HEAD_TXT = (255, 255, 255)
C_MUTED = (140, 140, 140)
C_ZEBRA = (250, 250, 252)
C_SUM_BG = (226, 229, 245)   # podklad souhrnných řádků (tón C_GROUP)

_FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts"
)


def _font(size: int):
    """Přibalený DejaVu se záložními systémovými cestami.

    Jen jeden řez. Tučný se nepřibaluje schválně – 708 kB kvůli třem souhrnným
    řádkům se nevyplatí a spoléhat na systémový font nejde (HA běží i v
    kontejnerech bez fontů). Souhrny se zvýrazňují podkladem a barvou textu.
    """
    name = "DejaVuSans.ttf"
    for path in (
        os.path.join(_FONTS_DIR, name),
        f"/usr/share/fonts/truetype/dejavu/{name}",
        f"/usr/share/fonts/dejavu/{name}",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# --- definice řádků -------------------------------------------------------- #
# (nadpis skupiny, [(popisek, klíč atributu, desetinná místa | None, souhrn)])
# „souhrn" = řádek Celkem -> tmavší podklad + tmavší popisek (místo tučného řezu).
# None = hodnota je už hotový string (motohodiny „11h 14min").
# Pořadí i názvy záměrně kopírují Acondac, aby se to dalo porovnávat očima.
GROUPS: list[tuple[str, list[tuple[str, str, int | None, bool]]]] = [
    (
        "Vyrobená energie [kWh]",
        [
            ("Celkem", "tep_celkem", 2, True),
            ("Topení", "tep_topeni", 2, False),
            ("TUV", "tep_tuv", 2, False),
            ("Odmrazování", "tep_odmraz", 2, False),
        ],
    ),
    (
        "Spotřebovaná energie [kWh]",
        [
            ("Celkem", "el_celkem", 2, True),
            ("Topení", "el_topeni", 2, False),
            ("TUV", "el_tuv", 2, False),
            ("Odmrazování", "el_odmraz", 2, False),
        ],
    ),
    (
        "COP",
        [
            ("Celkem", "cop_celkem", 2, True),
            ("Topení", "cop_topeni", 2, False),
            ("TUV", "cop_tuv", 2, False),
        ],
    ),
    (
        "Průměrná teplota [°C]",
        [
            ("Venkovní", "t_venkovni", 1, False),
            ("Vnitřní", "t_vnitrni", 1, False),
        ],
    ),
    (
        "Motohodiny",
        [
            ("Topení", "hodiny_topeni", None, False),
            ("TUV", "hodiny_tuv", None, False),
            ("Odmrazování", "hodiny_odmraz", None, False),
        ],
    ),
    (
        # NE „Počet startů" – odmrazení není start, je to cyklus. Pod takovou
        # hlavičkou by se to četlo špatně.
        "Počet",
        [
            ("Starty kompresoru", "starty_kompresor", 0, False),
            # odsazeno = podmnožina řádku nad sebou, ne třetí samostatné číslo
            ("     z toho kvůli TUV", "starty_tuv", 0, False),
            ("Odmrazování", "starty_odmraz", 0, False),
        ],
    ),
]

# Chlazení má vlastní čítače (30073/30074), takže do řádků „Celkem" nahoře
# nepatří – ty musí zůstat topná strana, jinak přestanou sedět na Acondac.
# Proto vlastní skupina a jednotky v popiscích řádků, ne v nadpisu.
# Zobrazuje se VŽDY, stejně jako skupina FVE: podmíněné bloky rozbíjejí
# markdown tabulku a 0,00 je stejně pravdivé jako skrytí.
GROUP_COOL: tuple[str, list[tuple[str, str, int | None, bool]]] = (
    "Chlazení",
    [
        ("Vyrobeno [kWh]", "tep_chlazeni", 2, False),
        ("Spotřebováno [kWh]", "el_chlazeni", 2, False),
        ("COP chlazení", "cop_chlazeni", 2, False),
        ("Motohodiny", "hodiny_chlazeni", None, False),
    ],
)

# Záměrně až dole a jako vlastní skupina, ne podřádky – aby tabulka držela
# podobu Acondacu i pro toho, kdo FV nemá. Skupina se vykresluje VŽDY: dřív
# byla podmíněná, ale ta podmínka rozbíjela markdown tabulku (prázdný řádek ji
# ukončil) a 0,00 je stejně pravdivé jako skrytí – kbelík existuje a je nulový.
GROUP_FVE: tuple[str, list[tuple[str, str, int | None, bool]]] = (
    "Vyrobeno programy FVE z přetoků [kWh]",
    [
        ("Topení", "fve_tep_topeni", 2, False),
        ("TUV", "fve_tep_tuv", 2, False),
    ],
)

DASH = "–"  # chybějící hodnota. NIKDY 0 – nula znamená „měřeno a neběželo".


def _fmt(value, decimals: int | None) -> str:
    """Čísla s desetinnou čárkou. None -> „–"."""
    if value is None:
        return DASH
    if decimals is None:
        return str(value)
    if decimals == 0:
        return str(int(value))
    return f"{float(value):.{decimals}f}".replace(".", ",")


def _groups() -> list[tuple[str, list[tuple[str, str, int | None, bool]]]]:
    return GROUPS + [GROUP_COOL, GROUP_FVE]


def canvas_height(groups=None) -> int:
    groups = _groups() if groups is None else groups
    rows = sum(len(items) for _, items in groups)
    return HEAD_H + len(groups) * GROUP_H + rows * ROW_H + FOOT_H


def render_table(columns: list[dict], meta: dict) -> bytes:
    """columns = 3× {label, sub, incomplete, data}; meta = {generated, starts_total}."""
    return _render(columns, meta, _groups(), W, COL_W)


def render_days(columns: list[dict], meta: dict) -> bytes:
    """Denní rozpad: dnešek + sedm zavřených dnů vedle sebe.

    Řádky jsou ZÁMĚRNĚ stejné jako ve sloupcové tabulce. Kdyby měla každá
    jiné, nedají se porovnat očima a člověk musí pokaždé hledat, kde co je.
    Liší se jen šířka sloupce a titulek.
    """
    width = 2 * PAD + COL_LABEL + COL_W_DAY * len(columns)
    return _render(columns, meta, _groups(), width, COL_W_DAY,
                   title="Statistika po dnech")


def _render(columns: list[dict], meta: dict, groups, W: int, COL_W: int,
            title: str = "Statistika tepelného čerpadla") -> bytes:
    H = canvas_height(groups)
    img = Image.new("RGB", (W * S, H * S), C_BG)
    d = ImageDraw.Draw(img)

    f_title = _font(15 * S)
    f_sub = _font(8 * S)
    f_head = _font(10 * S)
    f_group = _font(11 * S)
    f_row = _font(11 * S)
    f_foot = _font(8 * S)

    def x_col(i: int) -> int:
        # zarovnáno od PRAVÉ linky tabulky, ne od levého okraje – jinak poslední
        # sloupec přeteče linku a čísla nesedí s oddělovačem
        return (W - PAD) - COL_W * (len(columns) - 1 - i)

    # ---- hlavička --------------------------------------------------------- #
    d.rectangle([0, 0, W * S, 34 * S], fill=C_HEAD_BG)
    d.text((PAD * S, 10 * S), title, font=f_title, fill=C_HEAD_TXT)

    y = 42
    for i, col in enumerate(columns):
        cx = x_col(i) * S
        label = col.get("label", "")
        if col.get("incomplete"):
            label += " *"
        d.text((cx, y * S), label, font=f_head, fill=C_TITLE, anchor="rt")
        d.text((cx, (y + 14) * S), col.get("sub") or DASH,
               font=f_sub, fill=C_MUTED, anchor="rt")
    y = HEAD_H - 6
    d.line([(PAD * S, y * S), ((W - PAD) * S, y * S)], fill=C_RULE, width=S)

    # ---- tělo ------------------------------------------------------------- #
    y = HEAD_H
    shade = False
    for group_title, items in groups:
        d.text((PAD * S, (y + 8) * S), group_title, font=f_group, fill=C_GROUP)
        y += GROUP_H
        d.line([(PAD * S, (y - 4) * S), ((W - PAD) * S, (y - 4) * S)],
               fill=C_RULE_SOFT, width=S)
        for label, key, decimals, is_sum in items:
            if is_sum:
                d.rectangle(
                    [PAD * S, y * S, (W - PAD) * S, (y + ROW_H) * S], fill=C_SUM_BG
                )
            elif shade:
                d.rectangle(
                    [PAD * S, y * S, (W - PAD) * S, (y + ROW_H) * S], fill=C_ZEBRA
                )
            if not is_sum:
                shade = not shade
            font = f_row
            d.text((PAD * S + 8 * S, (y + 4) * S), label,
                   font=font, fill=C_TITLE if is_sum else C_LABEL)
            for i, col in enumerate(columns):
                data = col.get("data") or {}
                text = _fmt(data.get(key), decimals) if col.get("available", True) else DASH
                d.text(
                    (x_col(i) * S, (y + 4) * S),
                    text,
                    font=font,
                    fill=C_TEXT,
                    anchor="ra",
                )
            y += ROW_H

    # ---- patička ---------------------------------------------------------- #
    y += 6
    d.line([(PAD * S, y * S), ((W - PAD) * S, y * S)], fill=C_RULE, width=S)
    y += 6

    starts = meta.get("starts_total")
    d.text(
        (PAD * S, y * S),
        f"Starty kompresoru celkem od instalace: "
        f"{starts if starts is not None else DASH}",
        font=f_foot,
        fill=C_LABEL,
    )
    y += 11
    if any(col.get("incomplete") for col in columns):
        d.text((PAD * S, y * S), "* dnešní sloupec je neúplný den",
               font=f_foot, fill=C_MUTED)
        y += 11

    # Kontrolka: energie v kbelíku „ostatní" má být ~0. Když roste, rozchází se
    # naše čtení bitů se strojem. U motohodin je „ostatní" naopak prostoj.
    today = (columns[0].get("data") or {}) if columns else {}
    d.text(
        (PAD * S, y * S),
        f"kontrola (má být 0,00): el. ostatní {_fmt(today.get('el_ostatni'), 2)} · "
        f"tep. ostatní {_fmt(today.get('tep_ostatni'), 2)}",
        font=f_foot,
        fill=C_MUTED,
    )
    y += 11
    d.text(
        (PAD * S, y * S),
        f"Pořízeno: {meta.get('generated', '')}  ·  MaR · acond_regulator",
        font=f_foot,
        fill=C_MUTED,
    )

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
