# Schéma MaR — jak je udělané a jak ho měnit

Tenhle text je pro toho, kdo bude schéma upravovat. Popis pro uživatele,
co která kulička znamená, patří do příručky, ne sem.

---

## Jediné pravidlo

**SVG soubory se nikdy neupravují ručně.** Všechny kreslí `gen_schema.py`.
Ruční oprava vydrží do nejbližšího přegenerování a pak zmizí, aniž by kdokoli
věděl proč. Když se má něco posunout, změní se číslo v generátoru.

Stalo se to už jednou: šipka trojcestného byla otočená ručně a málem se
při další úpravě ztratila.

---

## Jak to funguje

Karta ve schématu je `picture-elements`. Její základní obrázek je průhledné
plátno (`v-prazdno.svg`) a **všechno ostatní jsou vrstvy naskládané na sebe**,
včetně podkladu. Proto jdou dělat varianty podkladu — základní obrázek karty
se přepnout nedá, ale prvek nad ním ano.

Každá vrstva má **celé plátno 1200 × 700** a kolem svého obsahu je průhledná.
Lícování je tím dané konstrukcí, ne pečlivostí: nikde se nic neposouvá,
protože všechno leží ve stejné soustavě souřadnic.

Naráz je viditelných nejvýš devět vrstev.

Rozdělení práce mezi obrázek a dashboard:

| Kreslí generátor | Dosazuje dashboard |
|---|---|
| tvary, potrubí, nádrže, zeď | naměřené hodnoty (teploty, výkony) |
| statické popisky („Boiler", „COP") | popisky s prefixem („Import 0,32 kWh") |
| pilulky tlačítek včetně nápisu | klikací plochy, potvrzení, podmínky |

Text, který se nemění, patří do obrázku. Text, který se mění, patří do YAML.

---

## Co kde změnit

Všechno podstatné je v `gen_schema.py` nahoře, v pojmenovaných konstantách.

**Ouška pod schématem** (`o-*.svg`) mají vlastní plátno 1200 × 64 a leží
v samostatné kartě těsně pod schématem. Díky tomu se kvůli nim nemusely
přepočítat souřadnice vrstev schématu. Graf ouška (např. Teploty) kreslí
integrace v Pillow (`teploty_png.py`) a dashboard ho zobrazí jako poslední
vrstvu schématu, takže schéma celé překryje. Hotová ouška: Teploty
(`teploty_png.py`) a Počasí (`pocasi_png.py`).

**Výkon** je výjimka: není to vrstva přes celé schéma, ale malý graf
(`vykon_png.py`) vložený do plochy `VYKON_VLOZKA` (od svislé trubky z AKU po
pravý okraj tlačítka FVE topení, výška AKU). Zapíná ho přepínač
`switch.mar_vrstva_vykon`, ne `select.mar_vrstva`. Když se změní rozmístění
schématu, posuň `VYKON_VLOZKA` (generátor) i `VLOZKA` (vykon_png.py) a
procenta umístění v dashboardu.

**Okno ve zdi** mezi TČ a bojlerem: zeď je přerušená (`ZED_OKNO`, mezi
červenou a modrou trubkou) a dashboard do otvoru položí malé teploty
`image.mar_teploty_mini` (`mini_teploty_png.py`, plocha `OKNO`, 6 h, bez os).
Je vidět pořád, nemá ouško ani vypínač; klepnutí otevře ouško Teploty. Při
posunu okna změň `ZED_OKNO` (generátor), `OKNO` (mini_teploty_png.py), procenta
v dashboardu a obrázek příručky (`gen_obrazek_prirucka.py`, ukázka
`mini-teploty-ukazka.png`). Nápis „Tepelné čerpadlo“ zmizel — pod strojem
jsou otáčky, zpátečka je pod modrou trubkou, výstup nad ohybem červené.

| Chci… | Změním |
|---|---|
| posunout nebo přejmenovat tlačítko | tabulku `TLACITKA` |
| tlačítko jen pro navigaci (bez zelené vrstvy) | množinu `BEZ_STAVU` |
| jinou velikost klikací plochy | `TVARY` |
| zkrátit nebo posunout zeď | `ZED_X, ZED_Y, ZED_W, ZED_H` |
| jinou trasu kuliček | konstanty `T_*` (sdílené s potrubím v podkladu) |
| jinou barvu | paletu nahoře (`CERVENA`, `MODRA`, `ORANZ`, …) |
| barvu kuliček při zpětném chodu | `KULICKA_TEPLA` / `KULICKA_STUDENA` (prohazují se ve `vrstvy()`) |
| rychlost kuliček | `RYCHLOST` (px/s) |
| přidat nebo přejmenovat ouško pod schématem | tabulku `OUSKA` |
| zapnout ouško, které dostalo obsah | odebrat ho z `PRIPRAVUJE_SE`, přidat volbu do `VRSTVA_OPTIONS` (const.py) a klikací plochu do dashboardu |

Trasy `T_*` používá **podklad i vrstva s kuličkami**. Změna trasy tedy
automaticky posune trubku i kuličky po ní. To je záměr; nerozpojuj to.

---

## Postup při změně

1. Změň číslo v `gen_schema.py`.
2. `python3 gen_schema.py` — přegeneruje všechno.
   Nebo jen část: `podklad`, `vrstvy`, `tlacitka`, `ouska`.
3. `python3 gen_schema.py --overit` — vypíše, co se liší proti disku.
   Před zápisem se hodí pustit nejdřív tohle: co se má lišit, se lišit má,
   a nic jiného.
4. Když se hýbalo tlačítko, vezmi nová procenta:
   `python3 gen_schema.py --souradnice` a přepiš je v dashboardu.
5. Když se změnilo rozmístění, přegeneruj i obrázek do příručky:
   `python3 gen_obrazek_prirucka.py` a výsledné SVG vlož do kapitoly Schéma
   (`prirucka/index.html`, sekce `#schema-popis`). Značky 1–14 se vážou na
   seznam pod obrázkem; když se něco posune, zkontroluj, že nic nezakrývají.
6. V dashboardu zvyš cache-buster `?v=XXXX` u všech odkazů na `/mar-schema/`.
7. Nasazení: smazat `__pycache__`, nahrát, **plný restart HA**, pak YAML
   a reload dashboardu.

---

## Pasti, na které se přišlo draze

**Celoplošná vrstva polyká dotyky.** Obrázek přes celé plátno je pro
prohlížeč obdélník, i když je průhledný, a bere kliknutí všude. Každá
tlačítková vrstva proto musí mít v dashboardu `pointer-events: none`,
jinak přestanou fungovat popisky pod ní.

**Cache.** `/mar-schema/` je v `__init__.py` registrované s vypnutými
cache hlavičkami (`StaticPathConfig(..., False)`). S nimi si tablet schová
obrázky na rok a nová verze se neobjeví ani po restartu. Nezapínej to zpátky.

**`state_image` potřebuje záložní `image`.** Bez něj se prvek nevykreslí vůbec.

**Písmo v obrázku se zmenšuje s obrázkem.** Na telefonu je plátno asi
třikrát menší, takže popisek 17 px vyjde na necelých 6. Co musí být čitelné
všude, patří do dashboardu jako `state-label` s pevnou velikostí v px.
`clamp` a `vw` vyzkoušené byly, nepomohly.

**Procenta v picture-elements udávají střed prvku**, ne levý horní roh.

**Stav regulace není ve schématu, ale v integraci.** Text s nadpisem kreslí
`stav_png.py` jako obrázek `image.mar_stav_regulace` (popisek `state-label`
neumí zalamovat). V podkladu proto musí zůstat volný rámeček x 795–1185,
y 188–358. Když se rámeček mění, změní se `RAMEC_W/RAMEC_H` v `stav_png.py`
i pozice a šířka prvku v dashboardu.

**Trojúhelník u trojcestného ukazuje, kam voda teče** — doprava do AKU,
dolů do boileru. Ne kam je zavřeno.

**Zaseknutý `bit_12`.** Chlazení se nepozná z `bit_12` samotného — po skončení
zůstane viset na `on`. Musí se číst spolu s `bit_10` (letní provoz).
Odmrazování je čistě `bit_8`. Tahle znalost je schovaná v
`sensor.mar_schema_smer`, aby nebyla rozkopírovaná po dashboardech;
nikdy ji nepiš znovu do YAML.

---

## Co se nesmí měnit

Tohle jsou kontrakty. Přejmenování je rozbíjející změna pro každého, kdo má
schéma v dashboardu:

- cesta `/mar-schema/`
- názvy souborů `podklad*.svg`, `v-*.svg`, `t-*.svg`
- entity `sensor.mar_kompresor_vizual`, `binary_sensor.mar_ma_fve`,
  `select.mar_schema_sekundar`

---

## Ověření, že generátor nelže

Když se generátor přepisuje, dá se dokázat, že vyrábí totéž co dřív:

1. `--overit` porovná text souborů znak po znaku.
2. Když se liší jen kosmeticky (desetinná místa, konec řádku), vykresli obě
   verze do PNG a porovnej po bodech. Nula rozdílných pixelů znamená,
   že se na obrazovce nic nezmění.

Takhle byl ověřený přechod ze tří skriptů na tenhle jeden: 39 souborů,
0 rozdílných pixelů.
