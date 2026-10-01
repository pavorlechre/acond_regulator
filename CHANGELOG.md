# Changelog

Všechny podstatné změny integrace Acond Regulator (MaR).
Formát vychází z [Keep a Changelog](https://keepachangelog.com/cs/1.1.0/),
verze podle [sémantického verzování](https://semver.org/lang/cs/).

## [Nevydáno]

### Přidáno
- Ouško Počasí (`image.mar_pocasi`): 24 h zpět a 24 h dopředu — venkovní teplota, T ekv, průměrná předpověď a oba zdroje, okno průměrování, ryska „teď“ a nahoře ikonky počasí po 3 h se srážkami (značky met.no, žádný dotaz navíc). Koordinátor si pro graf ponechá celou staženou předpověď; výpočet regulace beze změny.
- Ouška pod schématem: Schéma, Teploty, Energie, Ekviterma, Počasí, Stroj. Funkční je zatím Teploty, ostatní jsou ztlumená. Volba `select.mar_vrstva` se po 5 minutách sama vrátí na Schéma (je společná pro všechna zařízení).
- Graf Teploty (`image.mar_teploty`) přes celé schéma: místnost proti cíli, požadovaná a skutečná zpátečka, ekviterma bez přídavku s pravou osou T ekv, přídavek na místnost, pásy TUV a odmrazování. 12 h z recorderu, kreslí se jen při otevřeném oušku, obnova po 5 minutách. Klepnutím na graf zpět na schéma.
- Dashboard pro skládání s integrací Acond (od Acond 0.3.0): okna MaR odpovídají pracovnímu dashboardu `pokus2` (Režimy, FVE TUV, Statistika a jejich podokna včetně Profilů a Nastavení), adresy `/acond-dashboard/mar_…`.
- Vsuvka `dashboard/pohled_schema.yaml`: Schéma soustavy nahoře v okně Pohled integrace Acond (tlačítka režimů Acondu zůstávají). Schéma je dál i jako samostatné podokno `/acond-dashboard/mar_schema`.
- Oba soubory generuje `dashboard_src/gen_views.py` z jediného zdroje `dashboard_src/mar_dashboard.yaml`.

### Změněno
- Manifest: `http` uveden v závislostech (používá se pro `/mar-schema/`). Chování beze změny.
- Graf Teploty: osa zpátečky začíná na 19,5 °C, nižší hodnoty se kreslí tečkovaně po spodním okraji; požadovaná 60 °C se bere jako ohřev TUV (šedý pás, mimo osu); nejvýš 7 popisků; cíl místnosti v záhlaví (nepřekrývá se s hodnotou).
- Schéma v okně Pohled integrace Acond přes celou šířku (`grid_options: columns: full`).
- Příručka se posílá bez dlouhé mezipaměti, aplikace po aktualizaci hned ukáže novou.
- Generátor schématu zapisuje SVG vedle sebe (dřív do neexistující podsložky).
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
