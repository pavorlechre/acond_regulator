# MaR — živý stav vývoje

Čte se na začátku každého chatu, aktualizuje se na jeho konci. Nahrazuje dřívější
předávky (`*_predavka.md`). Repo je veřejné: **žádné IP adresy, ID Google tabulek
ani osobní údaje.**

Poslední aktualizace: 2026-09-30 · verze v `dev`: 0.17.5 (výchozí stav přenesený do repa)

---

## Jak pracujeme

- Pracuje se ve větvi **`dev`** (teď i výchozí větev repa). Do `main` Claude nikdy nepushuje.
- Claude navrhne změnu (co, které soubory, verze). **Zapisuje až po slovu „koduj“.**
  „koduj“ platí jen pro právě probraný návrh.
- Před každým pushem: kontrola syntaxe (Python, YAML, JSON), simulace, pokud se mění logika.
- Testovací verze v manifestu: `0.18.0b1`, `b2`… Release `0.18.0`, tag `v0.18.0`.
- **Merge `dev` → `main` a release dělá Pavle**, až verzi ověří na železe.
- Nasazení: HACS → MaR → Stáhnout znovu → **plný restart** HA.
- Honza aktualizuje jen na Pavlův pokyn.

## Stav

- 0.17.5 nasazená a ověřená na železe (kód beze změny přenesen ze zipu).
- **Schéma je první okno**, Pavle ho používá místo tabulek entit. Cache-buster `?v=0172`
  (při změně SVG ručně zvednout).
- Pracovní dashboard `dev_notes/pokus2.yaml` — **jen vzor**, k uživatelům se nedostane.
  Pavle i Honza ho mají zatím nasazený v HA.

## Zásady (neměnit bez výslovné domluvy)

- **Kontrakty jsou nedotknutelné:** čísla registrů, `entity_id`, cesty URL (`/mar-schema/`),
  názvy SVG (`podklad*.svg`, `v-*.svg`, `t-*.svg`). Přejmenování = rozbitý dashboard.
- **Dokud Honza jede na `pokus2`, nesmí se přejmenovat ani odebrat entita, kterou `pokus2` používá.**
- SVG se nikdy neupravují ručně — mění se generátor `schema/gen_schema.py` (viz `schema/CTIMNE.md`).
- Kameny rozhodují, JESTLI se topí; strategie JAK. Přepnutí strategie čerpadlo nespouští ani nezastavuje.
- Netopit je povel uživatele a platí vždy. Pod Bez MaR Topit nic nespouští.
- Minimum nezapisuje, když stojí kompresor, a nezapíše pod spodní mez registru 40008.
- Názvy souborů jen ASCII (diakritika v názvu rozbila `/api/states`).
- Když Pavle omezí rozsah („do odvolání jen příručka"), drží se to a ptá se před kódem.

## Rozpracováno / otevřené

1. **Přechod z `pokus2` na skládaný dashboard Acond** (cíl: Acond 3 okna + 3 okna MaR,
   Schéma nahoře v okně Přehled; `pokus2` se pak ruší).
   - `views.yaml` srovnat s `pokus2`; přepsat 23 odkazů `/dashboard-pokus2/…` na cesty dashboardu Acond.
   - Nový soubor kontraktu pro kartu Schématu v Přehledu (pracovní název `dashboard/prehled_top.yaml`).
   - Generátor v Acond repu — zpětně kompatibilní se starším MaR.
   - Honza přejde až s releasem obou integrací; do té doby `pokus2` nerušit.
   - Otázky pro Pavla: která 3 okna Acondu zůstanou, adresa dashboardu Acond, typ okna Přehled.
2. **Příručka:** zbývá 8× `XXX` k doplnění. FVE topení — grafy až v zimě (Honza dostal instrukce).
3. Chlazení: kuličky nemění barvu — neřešeno (chlazení se jen měří).
Body 4–6 jsou ze starších poznámek — ověřit, jestli už nejsou hotové:

4. Hodnoty v závorkách ve schématu na mobilu přetékají — odsazení v px, písmo 11 px.
5. FVE TUV práh baterie po 1 % místo 5 % (`FVE_BATT_PRAH_STEP` v `const.py`).
6. Badget popisek v dashboardu doplnit o „− vybíjení baterie".
7. Po prvním releasu: `hacs.json` → `hide_default_branch: true`, výchozí větev zpět na `main`.

## Acond repo (`homeassistant-acond`)

Připojené, zatím **jen ke čtení** — nic se nemění, dokud si postup neověříme na MaR.
Má releasy v0.1.0, v0.2.0 a `hide_default_branch: true`. Větev `dev` zatím nemá.
Skládání dashboardu je v `main`; ověřit, co z `main` ještě nevyšlo v releasu.
