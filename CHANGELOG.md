# Changelog

Všechny podstatné změny integrace Acond Regulator (MaR).
Formát vychází z [Keep a Changelog](https://keepachangelog.com/cs/1.1.0/),
verze podle [sémantického verzování](https://semver.org/lang/cs/).

## [Nevydáno]

### Opraveno (0.18.0b32)
- **Profily při nové instalaci:** `text.mar_profil_nazev` se při prvním založení nepřidala (`AttributeError: 'ProfilNazevText' object has no attribute '_default'`), takže profily byly trvale mimo provoz. Do `ProfilNazevText` se v 0.18.0b14 omylem dostala větev pro předvyplnění zdrojů FVE; spadla jen bez uloženého stavu, proto se stávajících instalací netýkala. Větev odstraněna. Po aktualizaci a plném restartu se entita vytvoří.

### Přidáno (0.18.0b31)
- **Prohlížeč dat v repozitáři** (`prirucka/prohlizec.html`): stránka pro export dat po minutách — souhrn, průběhy se společnou časovou osou a tabulka po dnech. HA ji servíruje na `/mar-prirucka/prohlizec.html`, web příručky na `…/acond_regulator/prohlizec.html`. V okně exportu odkaz **Otevřít v prohlížeči dat** rovnou načte poslední export (`?csv=/local/mar_export/…`). V souhrnu nově **průměrný příkon a výkon** za období exportu.

### Změněno (0.18.0b31)
- **Výkon 24 h:** nula na pásku venkovní teploty tučně.
- **Statistika — texty:** okno Statistika i příručka ještě tvrdily, že součty jsou z denních čítačů jednotky. Opraveno: energie se od 0.18.0b28 počítá z okamžitého příkonu a výkonu; u řady PRO se proto liší od appky Acondac.

### Přidáno (0.18.0b30)
- **Ouško Výkon 24 h** — velký graf výkonu za 24 h přes celé schéma (`image.mar_vykon_24_h`, volba „Výkon 24 h“ v `select.mar_vrstva`), hned vedle ouška Výkon. Nahoře souhrn: teplo a elektřina za 24 h integrované z okamžitých hodnot (platí i pro řadu PRO; teplo z odmrazu a 3 min po něm se nepočítá), COP z nich, počet startů kompresoru, odmrazů a ohřevů TUV. Výkon, příkon a COP na společné mřížce, horní okraj = nejvyšší hodnota + 10 %. Pod grafem pásek venkovní teploty a T ekv. Svislé čáry po 3 h, půlnoc plnou čarou. Klepnutí vrátí schéma, po 5 min se vrátí samo. Ouško Stroj (prázdné) vypadlo, zůstává jedno připravené (Ekviterma).

### Změněno (0.18.0b30)
- **TUV žlutě, odmrazování šedě** ve všech grafech schématu (Teploty, okno ve zdi, Výkon, Výkon 24 h) — na první pohled se odliší.
- **Výkon (12 h) bez špiček v odmrazu:** výkon, příkon ani COP se během odmrazování a 5 min po něm nekreslí (Acond tam hlásí nesmyslný výkon až 12 kW a COP 12 — tok tepla se obrací), šedý pruh pokrývá i doběh.
- **Okno ve zdi:** ve středové dělicí čáře nápis „Teploty · 6 h“.

### Změněno (0.18.0b29)
- **R-Ekviterma: nové učení posunu křivky (pravidlo C).** Staré hodinové učení se v cyklování mýlilo jedním směrem (u testera +0,6 °C, vše omylem – z hodin po pauze, po výpadku, po vyvětrání). **Nahoru:** každou celou hodinu okno posledních 3 h (minuty TUV se nepočítají) – když v něm nebyla pauza ani stop kompresoru, místnost ani jednou nedosáhla cíle a průměr je pod cílem o víc než 0,1 °C, posun += k × (cíl − průměr); pak čeká celé nové okno. Krátký propad sníží průměr, ale ne maximum, takže se z něj neučí. **Dolů z hystereze:** vysoká křivka místnost nepřetopí (zastaví ji brzda na cíli, pak pauza), proto se dolů učí z pauzy pásma, před kterou R hodinu jela převážně nad minimem stropu (posun −= k × 0,2); pauza po běhu na minimu znamená silné čerpadlo, ne chybu křivky. Ne při výrobě FVE, 3 h po změně cíle se neučí. Zrušeno hodinové vyhodnocení, trend i „2 h souvislého běhu“. Ověřeno přehráním minutových dat testera 10.–21. 11. skutečným kódem (3 kroky ve studené noci 16. 11., žádný omyl, žádný krok dolů) a simulací uzavřené smyčky (cyklování, 3 týdny mrazu, mikroventilace, křivka vysoko, lehký i těžký dům). Posun naučený starým pravidlem se při aktualizaci jednou vynuluje.
- **R-Ekviterma: ohřev TUV během pauzy naváže topením.** Když Acond v pauze R ohřívá vodu a místnost je nejvýš 0,1 °C nad startem, R pauzu ukončí kamenem Topit už během ohřevu (strop nemění – u PRO je na TUV uvolněný a po TUV se vrátí na minimum). Po TUV se topí dál jako po TUV za běhu, bez zastavení a nového startu (tester 21. 11.: stop po TUV v 10:15 a nový start v 10:24).
- **Export dat:** nové sloupce `t_ekv` (ekvitermní/modelová teplota) a `fve_W` (výroba FVE; bez FVE prázdné), na konci řádku.

### Změněno (0.18.0b28)
- **Statistika počítá energii z okamžitých hodnot (Riemannův součet), ne z denních čítačů Acondu.** Okamžitý příkon (30027) a výkon (30028) × čas se připisují režimu, ve kterém stroj právě je – při každé změně hodnoty a každých 30 s, úsek delší než 5 min (výpadek) se nepočítá. Důvod: řada PRO má denní čítače `eed`/`ted` chybné (teplo až +65 %, COP 8,7), okamžité hodnoty dobré. Ověřeno na EVI 9.–17. 11. 2026 proti čítačům: teplo topení ±0,15 kWh, elektřina celkem ±0,1 kWh za den, motohodiny na minutu. Teplo během odmrazování = 0 (výkon tam hlásí kladné teplo, čítač ne). Chlazení beze změny (vlastní čítače). Čítače `eed`/`ted` zůstávají jako kontrolka – v patičce tabulky řádek „čítače Acondu včera". Dny spočítané dřív zůstávají; den instalace je kombinace.
- **R-Ekviterma: posun křivky se učí jen po 2 h souvislého běhu kompresoru.** Při cyklování se místnost houpe kvůli pauzám a chybu křivky z ní určit nelze (Roman 16. 11. 04:00 se naučil +0,3 °C z hodiny po pauze). Ověřeno přehráním Romanových dat: starý kód +0,31, nový 0; studená noc se souvislým chodem se dál doučí stejně (do cíle za ~10 h).

### Přidáno (R-Ekviterma, Roman 15.–16. 11.)
- **Doučení křivky (hodinový posun).** Korekce na místnost (koeficient) sama vždy nechá dům kousek pod cílem – u Romana celé noci na cíl − 0,3 °C, kde ho brzda „zpátečka na křivce" nepustila výš. R teď každou celou hodinu, kdy celou dobu vedla bez TUV a bez pauzy, porovná průměr místnosti s cílem: je-li odchylka větší než 0,1 °C a místnost se sama nevrací, posune křivku o k × (cíl − (průměr + trend za hodinu)). k = 1 + (střední voda − místnost) / (1,3 × (místnost − venku)) se počítá z měření (radiátory ~1,6, podlaha ~1,2; omezení 1,1–2,0), nic se nenastavuje. Dolů se neučí, když vyrábí FVE (přetopení je slunce); 3 h po změně cíle místnosti se neučí. Posun ±5 °C, přežije restart, vypnutí volby Radiátory ho vynuluje. Ve Stavu regulace „křivka +0,8 °C (doučeno)", atributy `posun`, `posun_k`, `posun_info`, sloupec `r_posun` v exportu dat.

### Opraveno (R-Ekviterma)
- **Konec TUV vypínal kompresor.** Po ohřevu teče zpátečkou zbytek z bojleru (~52 °C) a když byla voda v domě teplá, R zapsala požadovanou = křivka a Acond ve Standardu kompresor hned vypnul (zpátečka ≥ požadovaná + 2). R pak brala zastavení jako nový start a srazila strop na minimum – celou noc topila minimem (Roman 15. 11. 23:49). Teď na konci TUV 3 min požadovaná na maximu, pak honička dolů za vodou. Zastavení kompresoru do 15 min po TUV je prodleva: strop zůstane z doby před TUV a nepočítá se do „Acond vypnul sám".

### Změněno
- **Řada PRO – ohřev TUV na plný výkon.** Acond PRO drží strop 40014 i při ohřevu TUV, takže s nízkým stropem (Minimum, R-Ekviterma, stažený slider) se bojler hřál 1,5–2 h a dům mezitím netopil. MaR teď na PRO po dobu TUV strop uvolní na maximum a hned po konci TUV (odmraz uprostřed se počítá do TUV) vrátí strop před ohřevem; když slider během TUV změní někdo jiný, nechá ho. Odchod z MaR (Bez MaR, čistý stůl) na PRO nastaví slider na maximum. Grandis/Economis beze změny (strop při TUV ignoruje sám). Nový `tuv_strop.py`.
- **R-Ekviterma bez podmínky „bez akumulační nádrže“.** Malá taktovací nádrž (200–300 l) nevadí; volba se jmenuje jen „Radiátory, silné čerpadlo“, příručka zmiňuje, že u velké nádrže nic nepřinese.
- Volba **Sekundární čerpadlo +1 °C** je nově i v okně Ekviterma nad volbou Radiátory (v Nastavení zůstává).
- Odchod z Typu regulace Standard (čistý stůl) přepne strategii na **Bez MaR** (dřív zůstala viset Ekviterma se zápisem vypnutým).
- FVE topení běží i nad strategií **Stálý výkon** (dřív jen Ekviterma). Uživatelův slider je přitom podlaha: program přidává nad něj, pod něj neubere; když přebytek nestačí, po prodlevě předá a slider vrátí. Předání vrací řízení aktivní strategii, ne natvrdo ekvitermě; hlídač restartu porovnává s cílem strategie.
- Při jakékoli změně strategie FVE topení nejdřív vrátí slider a zpátečku (dřív jen při odchodu z Ekvitermy).
- FVE topení běží i nad strategií **Minimum**. Po dobu převzetí smyčka Minima mlčí, po předání se slider vrátí na minimum a Minimum se hned chytí. Hlídač restartu se pod Minimem nenasazuje.

### Opraveno
- Instalace přes HACS z commitu selhávala (prázdný soubor `statistics/__init__.py` HACS nestáhl). Soubor má teď jednořádkový popis.
- FVE topení bez baterie: pod ~1000 ot. se slider nikdy nezvedl (krok ±10 % byl menší než pásmo necitlivosti zápisu 100 ot.). Krok je teď aspoň 100 ot.
- Stav MaR: když FVE topení čeká kvůli blokaci (strategie, Den/noc, pauza Zebry, útlum, TUV, odmraz, letní režim, strop místnosti, chybějící čidlo), věta píše skutečný důvod („FVE Topení: čekám – …“). Dřív vždy tvrdila „čekám na přebytek“, i když přebytek byl.
- FVE topení bez baterie: bilance (palivo) se počítá ze samotného přetoku do sítě. Dřív vyžadovala i výkon baterie, takže bez baterie byla trvale N/A a topení se nikdy nerozjelo.

### Opraveno (R-Ekviterma, první den na železe)
- Požadovaná zpátečka už neskáče o celý stupeň s kmitáním čidla: brzda „zpátečka na křivce“ má hysterezi 0,3 °C, návnada se mění jen při kroku stropu (po 10 min) a honička zasáhne až těsně před vypnutím.
- Po **odmrazování** výkon nepadá na minimum: pokračuje se dál, jen 3 min po odmrazu se nebrzdí zpátečkou (voda ohřátá odmrazem).
- Po **ohřevu TUV** se pokračuje ze stropu před ohřevem (ne z minima); když místnost mezitím vychladla pod cíl − 0,3, přidává se dvojnásobným krokem.
- „Růst místnosti“ ukazuje „—“ místo „NeznámýK/h“.

### Změněno
- Tlačítko v okně Statistika se jmenuje **Export snímku a dat**.
- Export dat má navíc sloupce `sek_cerpadlo` a `prim_cerpadlo`.

### Přidáno
- **Export dat po minutách** (Statistika → Export statistiky): výběr délky 1 / 3 / 7 / 10 dní (`select.mar_export_delka`), tlačítko `button.mar_export`, stav a odkaz ke stažení v `sensor.mar_export`. CSV pro Excel (středník, desetinná čárka) s teplotami, zpátečkou, otáčkami a stropem, příkonem, výkonem, TUV, odmrazem, strategií a stavem R-Ekvitermy; ukládá se do `config/www/mar_export/` (posledních pět souborů).
- Volba **Sekundární čerpadlo +1 °C** (Nastavení, `switch.mar_sekundar_plus1`): MaR zapíše do Acondu jednou o 1 °C vyšší požadovanou místnost, ať termostat Acondu nevypíná oběhové čerpadlo; v MaR se všude počítá a ukazuje skutečná hodnota. Mimo Bez MaR a Standard se posun vrátí, restart ho nezdvojí. Nová entita `number.mar_pozadovana_mistnost` (skutečná požadovaná; zápis přidá posun) nahradila 40001 v dlaždici Nastavení, schématu, grafech, řádku Zebry a v kartě Plánovač MaR.
- **R-Ekviterma** – volba „Radiátory, silné čerpadlo — bez akumulační nádrže“ v okně Ekviterma (`switch.mar_ekviterma_radiatory`, `number.mar_r_pauza_nad`, `number.mar_r_start_pod`, výchozí pásmo ±0,3 °C). Pod Ekvitermou vede výkon stropem 40014: start z minima, +5 % rozsahu / 10 min, když nic nebrzdí; brzda zpátečka/místnost −10 %, rychlý růst místnosti strop drží; dotápění pod cílem − 0,3; pauza nad pásmem kamenem Netopit podle Režimu vypínání, start pod pásmem kamenem Topit z minima (pauzu přebitou jiným kamenem MaR neukončí); honička proti vypnutí na +2 °C; po TUV/odmrazu z minima. Odchod vrátí slider. R-diagnostika (atributy, graf) a příručka.
- Okno **Plánovač** (změny nastavení podle času): karta Plánovač MaR (strategie, Zebra a její délky, Den/noc, Okna ±, Dovolená, FVE TUV, FVE topení, kameny, okruh 1, TUV, otáčky) a karta Plánovač Acond (vše zapisovatelné v čerpadle kromě Potvrzení poruchy a Resetu PLC). Každá karta drží své plány pod vlastním štítkem (`mar`, `acond`). Vstup tlačítkem v řádku Režimů a dole v Nastavení, šipka zpět vrací odkud uživatel přišel. Vyžaduje nepovinný doplněk Scheduler z HACS; bez něj okno ukáže, co chybí.
- Okno Plánovač: třetí karta **Plánovač – vše ostatní** pro celý HA (všechny běžné domény včetně MaR a Acondu); plány z karet MaR a Acond v ní nejsou (`exclude_tags`).
- Příručka: kapitola Plánování časem (instalace Scheduleru v pěti krocích, dvě karty, jak založit plán, příklady, na co dát pozor). README: Scheduler jako nepovinný doplněk.
- Příručka: kapitola 0 · Začínáme (než začneš, instalace, pět kroků k prvnímu topení, jak poznat, že to funguje, jak z toho ven) s cestou ke každému nastavení.
- Výkon baterie a přepínač znaménka přesunuty do FVE TUV → Nastavení zdrojů k ostatním zdrojům (dřív schované ve FVE topení); ve FVE topení tlačítko Nastavení zdrojů.
- Čerstvá instalace začíná strategií Bez MaR (MaR jen sleduje a nezapisuje), dokud uživatel neklepne na Ekvitermu. Uložená strategie se obnovuje jako dosud.
- Příručka: opraven dvojitý řádek „Ouška“ v celkovém obsahu.
- Graf 24 h přesunut ze Schématu do samostatného okna „Průběh 24 h“ (šipka zpět na Schéma); pod schématem je jen tlačítko. Schéma se tak načítá bez čekání na graf. V Pohledu Aconda tlačítko není.
- Okno Schéma: pod schématem velký graf za 24 h (ApexCharts) — teploty (venku, uvnitř, výstup, zpátečka a požadovaná, TUV), výkony v kW (výkon a příkon TČ, FV, síť, baterie, dům), COP, baterie %, otáčky a proužky chodu kompresoru, TUV a odmrazování. Řady jdou skrýt ťuknutím v legendě, některé jsou na začátku skryté. Jen v samostatném okně, do Pohledu Aconda se nevkládá. Výkony mají v legendě jednotku kW. Po otevření jsou vidět teploty a stavy, výkony, COP, baterie a otáčky jsou skryté; chod kompresoru je červený proužek. Pokusně zoom obdélníkem (čas i hodnoty) s tlačítky přiblížit/oddálit/posun/zpět nahoře vpravo.
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
