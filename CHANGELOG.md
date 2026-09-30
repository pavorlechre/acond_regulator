# Changelog

Všechny podstatné změny integrace Acond Regulator (MaR).
Formát vychází z [Keep a Changelog](https://keepachangelog.com/cs/1.1.0/),
verze podle [sémantického verzování](https://semver.org/lang/cs/).

## [Nevydáno]

### Přidáno
- Dashboard pro skládání s integrací Acond (od Acond 0.3.0): okna MaR odpovídají pracovnímu dashboardu `pokus2` (Režimy, FVE TUV, Statistika a jejich podokna včetně Profilů a Nastavení), adresy `/acond-dashboard/mar_…`.
- Vsuvka `dashboard/pohled_schema.yaml`: Schéma soustavy nahoře v okně Pohled integrace Acond (tlačítka režimů Acondu zůstávají). Schéma je dál i jako samostatné podokno `/acond-dashboard/mar_schema`.
- Oba soubory generuje `dashboard_src/gen_views.py` z jediného zdroje `dashboard_src/mar_dashboard.yaml`.

### Změněno
- Manifest: `http` uveden v závislostech (používá se pro `/mar-schema/`). Chování beze změny.
- Nastavení: šipka zpět vrací tam, odkud jsi přišel (Schéma v okně Pohled nebo podokno Schéma), místo pevně na podokno Schéma.
- Příručka: nové oddíly ve Statistice (tabulka dnes/včera/7 dnů, co znamenají řádky, statistika po dnech), FVE TUV „Jak číst grafy“, další obrázky.

## [0.17.5] - 2026-09-30

První verze v tomto repozitáři. Kód odpovídá instalaci ověřené na železe.

### Obsah
- Ekviterma s modelovou teplotou (minulost + předpověď) a korekcí na místnost.
- Strategie Ekviterma, Minimum, Stálá teplota, Boost, Bez MaR; kameny Topit / Netopit.
- Programy Zebra, Den/noc, Okna ± teploty, Dovolená; režim vypínání Útlum.
- FVE TUV a FVE topení (s baterií i bez ní), Badget.
- Schéma soustavy jako první okno, stav regulace jako obrázek, okno Nastavení.
- Profily nastavení, statistika, trendy, příručka.
