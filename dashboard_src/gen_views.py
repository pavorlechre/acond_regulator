#!/usr/bin/env python3
"""Generátor dashboardu MaR pro skladač integrace Acond.

JEDINÝ ZDROJ: dashboard_src/mar_dashboard.yaml (samostatný dashboard,
cesty bez předpony, odkazy /dashboard-pokus2/… – převzato z pokus2).

Výstupy (NEUPRAVOVAT RUČNĚ, vždy přegenerovat):
  custom_components/acond_regulator/dashboard/views.yaml
      okna MaR pro konec lišty Acondu; path s předponou `mar_`,
      odkazy přepsané na /acond-dashboard/mar_…
  custom_components/acond_regulator/dashboard/pohled_schema.yaml
      vsuvka do okna Pohled integrace Acond (slot `pohled_stav`):
      jedna sekce se stejnou kartou Schématu jako okno mar_schema.

Spuštění z kořene repa:  python3 dashboard_src/gen_views.py
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dashboard_src" / "mar_dashboard.yaml"
OUT_DIR = ROOT / "custom_components" / "acond_regulator" / "dashboard"
PREFIX = "mar_"
SRC_DASH = "/dashboard-pokus2/"
DST_DASH = "/acond-dashboard/"
SCHEMA_PATH = "schema"          # path okna Schématu ve zdroji
HEADER = (
    "# VYGENEROVÁNO z dashboard_src/mar_dashboard.yaml skriptem\n"
    "# dashboard_src/gen_views.py – NEUPRAVOVAT RUČNĚ, změnit zdroj a přegenerovat.\n"
)


def _rewrite_links(obj):
    """Přepiš /dashboard-pokus2/<path> na /acond-dashboard/mar_<path> (rekurzivně)."""
    if isinstance(obj, dict):
        return {k: _rewrite_links(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rewrite_links(v) for v in obj]
    if isinstance(obj, str) and SRC_DASH in obj:
        return re.sub(re.escape(SRC_DASH) + r"([A-Za-z0-9_-]+)",
                      lambda m: DST_DASH + PREFIX + m.group(1), obj)
    return obj


def _dump(path: Path, data) -> None:
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=4096)
    path.write_text(HEADER + text, encoding="utf-8")


def main() -> int:
    src = yaml.safe_load(SRC.read_text(encoding="utf-8"))
    views = src["views"]
    paths = [v["path"] for v in views]
    if len(paths) != len(set(paths)):
        print("CHYBA: duplicitní path ve zdroji", file=sys.stderr)
        return 1

    out_views = []
    for v in views:
        v = copy.deepcopy(v)
        v["path"] = PREFIX + v["path"]
        out_views.append(_rewrite_links(v))

    # Kontrola: každý odkaz na /acond-dashboard/mar_* míří na existující okno.
    known = {v["path"] for v in out_views}
    text = yaml.safe_dump(out_views, allow_unicode=True)
    missing = sorted({m for m in re.findall(DST_DASH + r"(mar_[A-Za-z0-9_-]+)", text)
                      if m not in known})
    if missing or SRC_DASH in text:
        print(f"CHYBA: neplatné odkazy {missing or SRC_DASH}", file=sys.stderr)
        return 1

    schema = next(v for v in out_views if v["path"] == PREFIX + SCHEMA_PATH)
    insert = [{
        "type": "grid",
        "column_span": 4,
        "cards": copy.deepcopy(schema["cards"]),
    }]
    # Oddíl přes více sloupců má uvnitř 12 × column_span dílků; karta bez
    # grid_options si vezme jen 12, tedy jeden sloupec. „full“ = celá šířka.
    for card in insert[0]["cards"]:
        card["grid_options"] = {"columns": "full"}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _dump(OUT_DIR / "views.yaml", out_views)
    _dump(OUT_DIR / "pohled_schema.yaml", insert)
    top = [v["title"] for v in out_views if not v.get("subview")]
    print(f"views.yaml: {len(out_views)} oken (v liště: {', '.join(top)})")
    print("pohled_schema.yaml: 1 sekce (Schéma)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
