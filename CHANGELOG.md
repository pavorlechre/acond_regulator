# Changelog

Všechny podstatné změny integrace Acond Regulator (MaR).
Formát vychází z [Keep a Changelog](https://keepachangelog.com/cs/1.1.0/),
verze podle [sémantického verzování](https://semver.org/lang/cs/).

## [Nevydáno]

### Přidáno
- Teploty (ouško): osa T ekv dál vpravo, mezi grafem a osou sloupec hodnot „teď“ (požadovaná, skutečná, výstup, ekviterma bez přídavku); výstup v rozsahu plnou čarou, mimo něj čárkovaně po okraji, hodnota vždy, bez šipky.
- Logo integrace (složka `brand/`, převzaté beze změny z integrace Acond).
- Upozornění v README a příručce: neoficiální doplněk (název a logo Acond jen k označení čerpadel) a bez záruky.
- Zdroje FVE se předvyplní hodnotami GoodWe jen tehdy, když ta entita v HA existuje; jinak zůstanou prázdné (dřív MaR u cizího měniče nebo bez FVE/baterie usoudil, že je má).
- Zrcadlové FVE senzory pro schéma: `sensor.mar_fve_pretok`, `…_baterie_vykon` (+ vybíjí), `…_dum` (dopočet výroba − přetok − nabíjení) a denní čítače `…_import_dnes`, `…_export_dnes`, `…_vyroba_dnes` (MaR sčítá výkon sám, o půlnoci nuluje, restart přežije; nepovinně vlastní denní senzory v `text.mar_fve_zdroj_*_dnes`, výchozí prázdné). Dashboard MaR už nejmenuje žádnou entitu střídače — schéma funguje s jakýmkoli měničem.
- FVE TUV: práh „Vypnout, až baterie klesne pod … %“ jde nastavit po 1 % (dřív po 5 %).
- Příručka po celkové kontrole: Topit (zruší útlum, přepne do zimy; běžící kompresor = typicky TUV), kde se volí provozní režim TČ, kdy svítí kameny, měkký × tvrdý strop u návnady zpátečky, počet obrázků schématu, úvod Režimů, jednotně pomlčka u startů.
- Teploty (ouško): výstup topné vody čárkovaně červeně, bez výplně; rozsah osy nezvětšuje, hodnota mimo graf se ukáže u kraje se šipkou.
- Příručka: přesnější popis Netopit ve VYP, snímky v kapitole Trendy (Souhrn, Energie celkem, grafy po dnech), odrážka o výstupu v oušku Teploty; „pravidlo palce“ → „orientačně platí“, zrušena poslední značka XXX.
- Příručka: oddíl „Ouška — grafy přes schéma“ v kapitole Schéma (Výkon, Teploty, Počasí) se snímky z provozu.
- Výkon: když kompresor stojí, ukáže se u COP hodnota „0,0“ (dřív tam nebylo nic).
- Ouška: funkční vlevo (Schéma, Teploty, Výkon, Počasí), dvě připravovaná vpravo jen jako prázdný ztlumený tvar bez nápisu.
- Okno ve zdi: hodnoty „teď“ u všech pěti čar (přibyl výstup a cíl místnosti); překreslí se do minuty po rozjezdu či zastavení kompresoru, začátku či konci TUV a odmrazování (jinak à 5 min), mřížka končí přesně v „teď“.
- Výkon: hodnoty na konci čar (výkon, příkon v kW, COP) před osou COP; nahoře jen malé „kW“ a „COP“ nad osami místo velkého čísla; šedý pruh ohřevu TUV a odmrazování s popiskem nad ním.
- Teploty: popisek pásu říká, co v něm bylo — „TUV“, „odmraz.“ (nebo obojí) — u každého pásu, kam se vejde.
- Okno ve zdi: malé teploty přímo ve schématu mezi tepelným čerpadlem a bojlerem (`image.mar_teploty_mini`), vidět pořád, bez vypínače. 6 h bez os: místnost proti cíli, výstup, skutečná a požadovaná zpátečka; růžová mezera výstup–zpátečka ukazuje chod stroje; TUV a odmrazování šedým pruhem; hodnoty „teď“ u pravé hrany. Klepnutí otevře ouško Teploty. Obnova po 5 minutách.
- Ouško Výkon (místo Energie): malý graf vložený přes topnou soustavu ve schématu (od trubky z AKU po tlačítko FVE topení, výška AKU) — tepelný výkon a příkon jako plochy, COP na pravé ose, 12 h, velké COP nahoře. Zapíná a vypíná se klepnutím na ouško (`switch.mar_vrstva_vykon`, pamatuje si stav), nezávisle na Teplotách a Počasí.
- Počasí: min a max i v historii venkovní teploty (jen skutečné vrcholy, ne useknutý kraj okna).
- Ouško Počasí: noci jako jemně šedé pozadí (západ → východ slunce z HA), T ekv tečkovaně dopředu stejným vzorcem jako MaR — končí, kam ještě sahá okno předpovědi (konec předpovědi minus hodiny předpovědi z nastavení), blok „venku teď / T ekv teď / T ekv za 12 h“, minimum a maximum předpovědi.
- Ouško Počasí (`image.mar_pocasi`): 24 h zpět a 24 h dopředu — venkovní teplota, T ekv, průměrná předpověď a oba zdroje, okno průměrování, ryska „teď“ a nahoře ikonky počasí po 3 h se srážkami (značky met.no, žádný dotaz navíc). Koordinátor si pro graf ponechá celou staženou předpověď; výpočet regulace beze změny.
- Ouška pod schématem: Schéma, Teploty, Energie, Ekviterma, Počasí, Stroj. Funkční je zatím Teploty, ostatní jsou ztlumená. Volba `select.mar_vrstva` se po 5 minutách sama vrátí na Schéma (je společná pro všechna zařízení).
- Graf Teploty (`image.mar_teploty`) přes celé schéma: místnost proti cíli, požadovaná a skutečná zpátečka, ekviterma bez přídavku s pravou osou T ekv, přídavek na místnost, pásy TUV a odmrazování. 12 h z recorderu, kreslí se jen při otevřeném oušku, obnova po 5 minutách. Klepnutím na graf zpět na schéma.
- Dashboard pro skládání s integrací Acond (od Acond 0.3.0): okna MaR odpovídají pracovnímu dashboardu `pokus2` (Režimy, FVE TUV, Statistika a jejich podokna včetně Profilů a Nastavení), adresy `/acond-dashboard/mar_…`.
- Vsuvka `dashboard/pohled_schema.yaml`: Schéma soustavy nahoře v okně Pohled integrace Acond (tlačítka režimů Acondu zůstávají). Schéma je dál i jako samostatné podokno `/acond-dashboard/mar_schema`.
- Oba soubory generuje `dashboard_src/gen_views.py` z jediného zdroje `dashboard_src/mar_dashboard.yaml`.

### Opraveno
- Krátké odmrazování a ohřev TUV v grafech nepropadnou: krok mřížky (5 min) se označí, když děj proběhl kdykoli během něj, ne jen v jeho okamžiku (Teploty, okno ve zdi, Výkon).
- Schéma na tabletu ve velkém zobrazení blikalo a spodek (tlačítka, ouška) se rozpadal do vodorovných pruhů: pohyblivé vrstvy (kuličky, vrtulky, větrák, ventil, bivalence) byly obrázky přes celé schéma a s každým pohybem se překreslovalo všechno. Teď má každá jen svůj výřez; vzhled beze změny (ověřeno porovnáním pixelů).

### Změněno
- Schéma: zeď je mezi trubkami přerušená (okno s malými teplotami); místo nápisu „Tepelné čerpadlo“ jsou pod strojem otáčky (v závorce strop) na jednom řádku, zpátečka se přesunula pod modrou trubku, výstupní teplota nad ohyb červené. Příručka: obrázek schématu a popis (nová položka 15).
- Manifest: `http` uveden v závislostech (používá se pro `/mar-schema/`). Chování beze změny.
- Počasí: výhled T ekv tence čárkovaně až na konec grafu (+24 h) — koordinátor drží 24 h + hodiny předpovědi z nastavení; venkovní teplota tyrkysově (modrá splývala s fialovou T ekv); rezerva nad a pod křivkami, aby se popisky min/max vešly.
- Ouška bez probliknutí: graf se kreslí na pozadí a do aplikace jde až hotový; s otevřeným ouškem se předkreslují všechny grafy (přepnutí mezi nimi je okamžité); úplně první kreslení po startu kryje plocha „Kreslím graf…“ místo prosvítajícího schématu.
- Graf Teploty: čára a výplň místnosti pokračují pásem TUV (ohřev vody místnost neovlivní), pás je v panelu místnosti jen slabě podbarvený.
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
