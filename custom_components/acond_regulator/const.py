"""Konstanty pro acond_regulator (MaR) – fáze 1."""

from __future__ import annotations

DOMAIN = "acond_regulator"
STORAGE_VERSION = 1

# --- Konfigurace -----------------------------------------------------------
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_LOCATION = "location"          # mapový picker (rozbalí se na lat/lon)

# --- Pevné entity z integrace Acond (Regulace jede jen s Acondem) ----------
ACOND_OUTDOOR = "sensor.acond_30010_t_act_air"        # venkovní teplota
ACOND_INDOOR = "sensor.acond_30002_t_act_indoor1"     # skutečná teplota místnosti
ACOND_ROOM_SET = "number.acond_40001_t_set_indoor1"   # požadovaná teplota místnosti (uživatel)
ACOND_TUV_SET = "number.acond_40005_t_set_tuv"        # požadovaná teplota TUV (uživatel)
ACOND_SUMMER = "binary_sensor.acond_30007_tc_status_bit_10"  # letní režim -> nezapisovat
ACOND_TARGET = "number.acond_40008_t_set_water_back"  # cíl zápisu zpátečky (40008)
ACOND_RETURN_READBACK = "sensor.acond_30008_t_set_water_back"  # readback pro zápis-při-změně
ACOND_RETURN_ACT = "sensor.acond_30009_t_act_water_back"       # skutečná zpátečka (30009)
ACOND_HP_ON = "binary_sensor.acond_30007_tc_status_bit_0"      # TČ zapnuto (VYP = off)
ACOND_REG_TYPE = "sensor.acond_30015_regulation_type"         # Typ regulace (text)
# 40008 je „Požadovaná zpátečka – Standard\" -> náš zápis platí JEN v typu Standard.
# Whitelist jednoho: mimo Standard je MaR hluchý (nezapisuje, programy zhasnou).
# Nepotřebujeme znát ostatní typy jménem – okno ukáže živý řetězec, ať je tam cokoli.
REG_TYPE_STANDARD = "Standard"
# Pozn.: TUV (bit 3), odmraz (bit 8), léto (bit 10) mají konstanty níž ve statistice
# (ACOND_BIT_TUV/…DEFROST) a ACOND_SUMMER výš – mar_stav i gate zápisu je re-usují.

# --- Primitiv běhu kompresoru (event-driven RunState) ----------------------
# Klíčováno ČÍSLY registrů; jména na web-UI driftují (mini je píše jinak než full).
# Na mini i full jsou tyhle registry přítomné – žebřík běhu jede na obou plně.
ACOND_COMP_BIT = "binary_sensor.acond_30045_hp_comp_bit_0"     # Kompresor (primární signál)
ACOND_FAN_BIT = "binary_sensor.acond_30045_hp_comp_bit_1"      # Ventilátor
ACOND_PUMP_BIT = "binary_sensor.acond_30045_hp_comp_bit_2"     # Primární čerpadlo
ACOND_RPM = "sensor.acond_30024_comp_rpm_actual"               # otáčky (fallback 2)
ACOND_POWER = "sensor.acond_30027_aep"                         # el. příkon (fallback 3)

RPM_MIN = 300.0            # práh běhu z otáček (fallback, když chybí bit 0)
# P_BASE = klidová hladina příkonu (běží větrák/primár, kompresor stojí). Fallback 3
# (příkon) se použije JEN když chybí bit 0 i RPM – na známých jednotkách (full i mini)
# nikdy, protože 30045 bit 0 je přítomen. Kdyby to někdy zafungovalo, ZMĚŘ P_BASE na
# reálném železe, ať žebřík nechytne pomocné agregáty. 300 W je konzervativní placeholder.
P_BASE = 300.0
P_MARGIN = 100.0
RUN_DEBOUNCE_S = 8        # drž změnu ≥ tolik s (proti roztáčení / falešnému startu)

# --- Kameny START / STOP (Zapnout jistě / Vypnout šetrně) -------------------
# Přepsaná logika tvých skriptů do integrace; slepé delay -> čekání na primitiv.
ACOND_SW_TC = "switch.acond_40006_tc_set_bit_1"     # Pouze TČ (shodí VYP samo)
ACOND_SW_AUTO = "switch.acond_40006_tc_set_bit_0"   # Automatický
ACOND_SW_BIVAL = "switch.acond_40006_tc_set_bit_2"  # Bivalence
ACOND_SW_VYP = "switch.acond_40006_tc_set_bit_3"    # Vypnuto (antizámraz běží dál)

# Provozní režim TČ, který kámen Zapnout jistě zapíná (select.mar_rezim_tc).
# Default Pouze TČ = dosavadní chování; kdo jede Automat/Bivalenci, přepne si
# jednou v selectu a kámen mu režim nemění. (Vypnout šetrně se netýká – VYP je VYP.)
TCMODE_TC = "Pouze TČ"
TCMODE_AUTO = "Automatický"
TCMODE_BIVAL = "Bivalence"
TCMODE_OPTIONS = [TCMODE_TC, TCMODE_AUTO, TCMODE_BIVAL]
TCMODE_DEFAULT = TCMODE_TC
TCMODE_SWITCH = {
    TCMODE_TC: ACOND_SW_TC,
    TCMODE_AUTO: ACOND_SW_AUTO,
    TCMODE_BIVAL: ACOND_SW_BIVAL,
}

HP_LIMIT = 20.1           # zpátečka těsně nad limit TČ 20 °C -> kompresor stojí na hysterezi
BOOST = 10.0              # +°C na zpátečku při startu i BOOST režimu (starý script měl +7)
START_MAX_S = 8 * 60      # strop čekání na náběh kompresoru; při vypršení vrať původní a konec
DOBEH_MAX_S = 5 * 60      # strop čekání na doběh kompresor+větrák+primár (větrák někdy usekne -> 5)
DWELL_S = 5               # držení boostu po naskočení kompresoru (už jsme v hysterezi, nevypne)
TUV_WAIT_S = 90 * 60      # čekání na konec TUV/odmraz, pak abort (neodstavuj v zaseklém odmraz)
TUV_STABLE_S = 30         # TUV i odmraz musí být off aspoň tolik s (proti cukání bitu)

# --- Strategie Minimum (event-driven smyčka, ne coordinator) ----------------
ACOND_CAPACITY = "number.acond_40014_comp_capacity_max_set"  # slider Max otáček (40014)
MIN_TARGET_DIFF = -1.2    # cíl = skutečná zpátečka + tohle (kousek pod -> TČ jede na min)
MIN_BAND = 0.5            # přepiš, až rozdíl vyjede z pásma ⟨-1,7 ; -0,7⟩ = -1,2 ± 0,5
MIN_WRITE_BRAKE_S = 15.0  # min. odstup mezi zápisy (proti cukání čidla); mini brzdí 90s sám
MIN_SAFETY_TICK_S = 120   # pomalý pojistný tik Minima: tracuje, i když skutečná zrovna stojí
                          # (zabíjí „dead-man's-switch" – bez něj Minimum zamrzne, když
                          #  se 30009 přestane hýbat). Non-force -> píše jen když je třeba.
MINIMUM_STORE_VERSION = 1

# --- Patro 1: strategie dodávky (select.mar_strategie) ----------------------
# Hodnoty = přímo lidské popisky (žádná překladová vrstva). Coordinator je porovnává.
STRATEGY_EKVITERM = "Ekviterma"
STRATEGY_STALY = "Stálý výkon"
STRATEGY_MINIMUM = "Minimum"            # drží zpátečku těsně pod skutečnou -> TČ jede na min
STRATEGY_BOOST = "BOOST"                # slider na max + zpátečka ekviterma+10 = naplno
STRATEGY_BEZ_MAR = "Bez MaR"           # MaR nezapisuje; co se děje s topením je mimo MaR
STRATEGY_OPTIONS = [
    STRATEGY_EKVITERM,
    STRATEGY_STALY,
    STRATEGY_MINIMUM,
    STRATEGY_BOOST,
    STRATEGY_BEZ_MAR,
]
# Stálý výkon = ekviterma + posun. Kotví na křivce (tvá zodpovědně nastavená mez),
# ne na výkonu -> bezpečné na podlaze i radiátorech. Slider si řídí uživatel, MaR na
# 40014 nesahá; před/po režimu si slider nastaví sám (nápověda).
STALY_OFFSET = 10.0

# --- Patro 2: Zebra (časový překryv, ne strategie) -------------------------
# Cykluje topení/pauzu POUZE když je v pokoji natopeno (30002 > 40001, ostře).
# Pauzuje přes Kámen A (Vypnout šetrně), zpět přes Kámen B (Zapnout jistě).
# Ortogonální ke strategii: běží nad Ekvitermou / Stálým / Minimem. Pod BOOST
# a Bez MaR nedává smysl -> select ji tam sám zhasne. Mimo Standard je hluchá.
# (ACOND_ROOM_SET = požadovaná 40001 a ACOND_INDOOR = skutečná 30002 už jsou výš.)
ZEBRA_MIN = 30              # min. délka intervalu (min)
ZEBRA_MAX = 240             # max. délka intervalu (min)
ZEBRA_STEP = 5              # krok pole
ZEBRA_DEFAULT_HEAT = 60     # default interval topení (min)
ZEBRA_DEFAULT_PAUSE = 60    # default interval pauzy (min)
ZEBRA_MAX_STARTS_PER_DAY = 24   # strop startů (Kámen B) za kalendářní den
ZEBRA_STORE_VERSION = 1
SEQ_BUSY_RETRY_S = 60       # když na hranici běží kámen, odlož přechod o tolik s
NUMBER_ZEBRA_HEAT = "zebra_topeni"
NUMBER_ZEBRA_PAUSE = "zebra_pauza"

# --- Patro 2: Den/noc (časový režim č. 2, osa A – kameny) ------------------
# Vnější rozvrh oken „teď se smí topit". Přechody kameny (vstup = Zapnout jistě,
# odchod = Vypnout šetrně). MÁ PŘEDNOST NAD ZEBROU: mimo okno Zebra mlčí (Den/noc
# ji uspí), uvnitř cykluje. Bezstavový přes restart (čistá funkce hodin + časů).
# Okno = dvojice time entit start/stop; start==stop -> vypnuté (řídí i odkrývání
# dalšího okna v UI). start>stop -> přes půlnoc. Master = switch.mar_dennoc.
DENNOC_MAX_WINDOWS = 5       # strop počtu oken (progresivní odkrývání v UI)
DENNOC_RETRY_S = 60          # když na hraně běží kámen, zkus reconcile za tolik s
# Klíče entit (time start/stop pro každé okno + validita okna pro odkrývání)
DENNOC_TIME_START = "dennoc_okno{n}_start"
DENNOC_TIME_STOP = "dennoc_okno{n}_stop"
DENNOC_VALID = "dennoc_okno{n}"           # binary_sensor: okno je platné (start!=stop)
# Default 1. okna = „den" 06:00–22:00 (master je ale OFF, tak se nic neděje, dokud
# ho uživatel nezapne). Ostatní okna default vypnutá (00:00 == 00:00).
DENNOC_DEFAULT_START = "06:00:00"
DENNOC_DEFAULT_STOP = "22:00:00"

# --- Patro 2 / OSA B: Okna +/- teploty (časová korekce setpointu 40001) -----
# Časová okna, která na svou dobu posunou POŽADOVANOU teplotu místnosti (40001)
# o vlastní deltu (dolů = noční pokles, nahoru = např. přednatopení z FVE). Termální
# efekt jde přes korekci místnosti do zpátečky (40008) -> reálně účinné jen v
# ekvitermě; jinde je zápis inertní (jen displej). Běží ale bez ohledu na strategii
# (čistě oknem, jako Den/noc) kvůli čistotě a vysvětlitelnosti.
#
# KONTRAKT PŮJČ-A-VRAŤ (osa B smí na 40001, osa A nikdy): na náběžné hraně okna
# zachyť base = aktuální 40001, zapiš base+delta; na sestupné hraně vrať base.
# Base žije ve Store -> přežije restart (na rozdíl od Den/noc NENÍ bezstavový,
# protože si půjčuje uživatelův registr a musí ho vrátit). Promise model: na konci
# okna vracíme base DOSLOVA (uživatel ví, že běží pokles; když v okně sám sáhne,
# náš restore to přepíše – to je přesně ten slib, nehádáme se o „kdo psal naposled").
OKNA_MAX_WINDOWS = 3          # strop počtu oken (progresivní odkrývání v UI)
OKNA_RETRY_S = 60             # když 40001 nedostupný / kolize, zkus reconcile za tolik s
OKNA_STORE_VERSION = 1
# Klíče do unique_id (entity_id se odvozuje ze slugify NAME, ne odtud!). Ověřené
# id: switch.mar_okna_teploty · time.mar_okna_teploty_{n}_od/_do ·
# number.mar_okna_teploty_{n}_delta · binary_sensor.mar_okna_teploty_{n}_nastaveno.
OKNA_TIME_START = "okna_teploty_{n}_od"
OKNA_TIME_STOP = "okna_teploty_{n}_do"
OKNA_DELTA_KEY = "okna_teploty_{n}_delta"
OKNA_VALID_KEY = "okna_teploty_{n}_nastaveno"
OKNA_DELTA_MIN = -3.0         # skromný rozsah: reálná páka = delta × koeficient korekce
OKNA_DELTA_MAX = 3.0          # (Pavel ×2 -> −2 na 40001 udělá −4 na příspěvku ke zpátečce)
OKNA_DELTA_STEP = 0.5
OKNA_DELTA_DEFAULT = 0.0
# 1. okno default „noční pokles" 22:00–06:00 (přes půlnoc). Master je ale OFF,
# takže se nic neděje, dokud ho uživatel nezapne. Ostatní okna vypnutá (00:00==00:00).
OKNA_DEFAULT_START = "22:00:00"
OKNA_DEFAULT_STOP = "06:00:00"
# Clamp zápisu do 40001 (uživatelský setpoint místnosti). Ochrana base+delta proti
# extrémům; reálně base~21 ± 3. Fallback, když number entita nehlásí vlastní min/max.
ROOM_SET_MIN = 10.0
ROOM_SET_MAX = 30.0

# --- Správce pokojovky (jediný vlastník 40001) ----------------------------
# Půjč-a-vrať vytažený z Oken do samostatného dílu, protože 40001 chce víc
# programů naráz (Okna + FVE Topení) a dvojí půjčka rozbije originál: kdo vrátí
# první, přepíše druhého, a ten pak „vrátí" cizí hodnotu natrvalo.
# Správce NEMÁ vlastní vypínač – žije pořád, aby fungoval i s vyplými Okny.
POKOJOVKA_STORE_VERSION = 1
POKOJOVKA_RETRY_S = 60          # 40001 nedostupný (po startu) -> zkus znovu za tolik s
POKOJOVKA_CLAIM_OKNA = "okna"           # zákazník: Okna +/- teploty
POKOJOVKA_CLAIM_TOPENI = "fve_topeni"   # zákazník: FVE Topení (návnada pokojovkou)
# Ruční zásah uživatele do 40001: rozdíl proti očekávané hodnotě větší než tohle
# bereme jako ÚMYSL (posuň základ), menší jako zaokrouhlení desky (ignoruj).
POKOJOVKA_TOL = 0.3

# --- Patro 2 / OSA B: Dovolená (jeden dlouhý interval, executor na 40001 + 40005)
# Půjč-a-vrať jako Okna, ale: JEDEN interval (datetime od/do = datum+hodina, ne
# denní rozvrh), ABSOLUTNÍ cíle (drž X °C, ne delta), DVA registry (místnost 40001
# + TUV 40005). Termální efekt topení jen v ekvitermě (guard to zajistí); TUV jede
# vždy (nejde přes ekvitermu) -> na dovolené se voda nehřeje zbytečně.
#
# VSTUPNÍ GUARD (Pavleho návrh): zapnout Dovolenou jde jen když strategie ==
# Ekviterma A Okna +/- teploty vyplá. Jinak zamítni + hláška. A SYMETRICKY: když
# běží Dovolená, zamítni zapnutí Oken i přepnutí strategie pryč z Ekvitermy. Guard
# je jen na VSTUPU (okamžik kliknutí); co uživatel změní za běhu jinými cestami,
# je jeho odpovědnost (nechráníme všechno). Den/noc a Zebra se NEhlídají (můžou
# na dovolené prospět – protáčení/rozvrh běží dál).
DOVOLENA_STORE_VERSION = 1
DOVOLENA_RETRY_S = 60
DOVOLENA_ROOM_KEY = "dovolena_teplota"     # -> number.mar_dovolena_teplota (cíl 40001)
DOVOLENA_TUV_KEY = "dovolena_tuv"          # -> number.mar_dovolena_tuv (cíl 40005)
DOVOLENA_ROOM_MIN = 10.0
DOVOLENA_ROOM_MAX = 25.0
DOVOLENA_ROOM_STEP = 0.5
DOVOLENA_ROOM_DEFAULT = 16.0               # rozumný útlum místnosti při nepřítomnosti
DOVOLENA_TUV_MIN = 20.0
DOVOLENA_TUV_MAX = 55.0
DOVOLENA_TUV_STEP = 1.0
DOVOLENA_TUV_DEFAULT = 40.0                # nižší TUV – voda se nehřeje nadoraz
# default datetime od==do (2020) -> interval NEplatný, dokud uživatel nenastaví
DOVOLENA_DEFAULT_DT = "2020-01-01 00:00:00"

# --- Režim vypínání: VYP / Léto / Útlum PZ (co znamená „netopit") -----------
# Rozcestník pro kameny: „off" akce může být tvrdé VYP (bit_3), přepnutí do
# letního režimu (TUV jede dál) nebo útlum přes zpátečku (žádná změna módu, jen
# coordinator drží 40008 na PZ_TARGET). Default VYP = zpětná kompatibilita.
OFFMODE_VYP = "VYP"                 # tvrdé vypnutí (bit_3), antizámraz jede
OFFMODE_LETO = "Léto"              # letní režim: netop, ohřívej TUV (pulz léto/zima)
OFFMODE_PZ = "Útlum PZ"           # škrcení: normální mód, zpátečka držená na PZ_TARGET
OFFMODE_OPTIONS = [OFFMODE_VYP, OFFMODE_LETO, OFFMODE_PZ]
OFFMODE_DEFAULT = OFFMODE_VYP
PZ_TARGET = 20.0                   # útlumová zpátečka (těsně nad prahem topení u všech typů)

# --- FVE: přebytkový ohřev TUV (osa B, executor na 40005) -------------------
# Sleduje 15min klouzavý průměr BADGETU (= přetok + okamžitý příkon TČ) a při
# dostatku přebytku zvedne cíl TUV (40005) na horní mez, jinak ho vrátí (půjč-a-
# vrať). Badget „odmaskuje" spotřebu, kterou TČ zrovna žere (jinak by holý přetok
# spadl na ~0 a ohřev by se nespustil / předčasně vypnul). Přetok je konfigurovatelný
# přes text.mar_fve_zdroj_pretok; příkon TČ je nativní Acond entita (ACOND_POWER,
# 30027) napevno, s auto-detekcí jednotky W/kW a měkkou degradací na čistý přetok,
# když aep chybí. Bez baterky se START i STOP řídí badgetem; s baterkou START badget,
# STOP zůstává na SoC-poklesu (badget baterku neodmaskuje). Goodwe defaulty
# předvyplněné. Guard s Dovolenou jednostranný (běží-li FVE, Dovolenou nelze zapnout).
# Viz fve.py.
FVE_STORE_VERSION = 1
FVE_WINDOW_S = 15 * 60             # okno klouzavého průměru přetoku (s)
FVE_TICK_S = 30                    # periodický tik: prořez okna + kontrola stop
FVE_RETRY_S = 60                   # když registr TUV nedostupný při aktivaci
FVE_TUV_FALLBACK = 50.0           # fallback boost target, když max atribut 40005 chybí
# Trvalá ztráta zdroje baterie během aktivního boostu (překlep v entity_id,
# přejmenování po updatu, mrtvý měnič): PŘECHODNÝ výpadek se drží (nesahej),
# ale po tomhle timeoutu se boost ukončí, TUV vrátí a pošle se notifikace –
# jinak by bez SoC-stopky držel navěky. Zrcadlí cidlo_timeout topení.
FVE_BATT_LOST_S = 300.0

# Klíče (unique_id) + jména pro slug entity_id. POZOR: entity_id se tvoří ze slugu
# _attr_name (device „MaR" -> prefix mar_), ne z těchto klíčů. Jména volena tak, ať
# slug sedí přesně na dashboard (switch.mar_fve_tuv, number.mar_fve_tuv_start, …).
FVE_MASTER_KEY = "fve_tuv"                 # switch.mar_fve_tuv
FVE_START_KEY = "fve_tuv_start"            # number.mar_fve_tuv_start
FVE_STOP_KEY = "fve_tuv_stop"              # number.mar_fve_tuv_stop
FVE_BATT_PRAH_KEY = "fve_tuv_baterie_prah" # number.mar_fve_tuv_baterie_prah
FVE_SRC_PRETOK_KEY = "fve_zdroj_pretok"    # text.mar_fve_zdroj_pretok
FVE_SRC_VYROBA_KEY = "fve_zdroj_vyroba"    # text.mar_fve_zdroj_vyroba
FVE_SRC_BATERIE_KEY = "fve_zdroj_baterie"  # text.mar_fve_zdroj_baterie
FVE_AKTIVNI_KEY = "fve_aktivni"            # binary_sensor.mar_fve_aktivni
FVE_PRUMER_KEY = "fve_prumer_pretoku"      # sensor.mar_fve_prumer_pretoku (čistý přetok)
FVE_MIRROR_BATT_KEY = "fve_baterie"        # sensor.mar_fve_baterie
FVE_MIRROR_PV_KEY = "fve_pv"               # sensor.mar_fve_pv
# Badget = přetok + okamžitý příkon TČ (aep, 30027). „Odmaskuje" spotřebu, kterou
# TČ zrovna žere -> skutečný dostupný přebytek. Dvě formy: vyhlazený (řídí START)
# a syrový (do grafu + pro pozdější topení). Trvalý kontrakt (jako registry).
FVE_BADGET_KEY = "fve_badget"              # sensor.mar_fve_badget (průměr, START)
FVE_BADGET_SYROVY_KEY = "fve_badget_syrovy"  # sensor.mar_fve_badget_syrovy (okamžitý)

# Prahy (laditelné živě v UI)
FVE_START_DEFAULT = 800.0          # W – „hojnost": kolik přebytku, než začnu
FVE_START_MIN = 0.0
FVE_START_MAX = 5000.0
FVE_START_STEP = 50.0
FVE_STOP_DEFAULT = -200.0          # W – „vytrvalost": jak hluboko pod přebytek smím spadnout
FVE_STOP_MIN = -3000.0
# Strop posunut na +1000: na BADGETu (skutečný přebytek) má smysl i KLADNÝ stop
# („vypni, až reálný přebytek klesne pod X W" = nech si rezervu, nejdi do importu).
FVE_STOP_MAX = 1000.0
FVE_STOP_STEP = 50.0
FVE_BATT_PRAH_DEFAULT = 90.0       # % – spodní podmínka stopu (X): pod tímto SoC se smí vypnout
FVE_BATT_PRAH_MIN = 0.0
FVE_BATT_PRAH_MAX = 100.0
FVE_BATT_PRAH_STEP = 5.0
FVE_BATT_POKLES_KEY = "fve_tuv_baterie_pokles"  # number.mar_fve_tuv_baterie_pokles
FVE_BATT_POKLES_DEFAULT = 5.0      # % – povolený pokles SoC od startu (Y): kolik obětuju na mrak
FVE_BATT_POKLES_MIN = 1.0
FVE_BATT_POKLES_MAX = 20.0
FVE_BATT_POKLES_STEP = 1.0

# Goodwe defaulty zdrojových entit (uživatel přepíše vložením ze schránky)
FVE_SRC_PRETOK_DEFAULT = "sensor.active_power"           # znaménkový, kladné = export
FVE_SRC_VYROBA_DEFAULT = "sensor.pv_power"               # výkon panelů (jen displej)
FVE_SRC_BATERIE_DEFAULT = "sensor.battery_state_of_charge"  # SoC %

# Léto = TOGGLE (pulz), ne spínač. Čteme ACOND_SUMMER (bit_10), pulzneme bit_8.
# Čísla schválená údržbářem Acondu (poll tc_status 15 s, pulz ~4,5 s blocking):
ACOND_SEASON_BUTTON = "button.acond_40006_tc_set_bit_8"  # pulz přepne léto<->zima
SEASON_COOLDOWN_S = 25.0           # min. rozestup mezi pulzy (> poll Acondu 15 s)
SEASON_VERIFY_ATTEMPTS = 3         # kolik celých pokusů, než to vzdáme (~2 min nejhůř)
SEASON_VERIFY_POLL_S = 5.0         # jak často mezi pokusy číst bit_10
# okno >= 2×poll(15 s)+rezerva: přežije JEDEN spadlý poll (ECONNREFUSED ~30 s),
# jinak by druhý pokus pulzoval na STARÉM bit_10 a přehodil sezónu špatně
SEASON_VERIFY_WINDOW_S = 35.0

# --- Laditelné number entity (živě v UI) -----------------------------------
NUMBER_PAST_HOURS = "hodiny_minulost"
NUMBER_FUTURE_HOURS = "hodiny_predpoved"
NUMBER_COEF = "koeficient_korekce"

DEFAULT_PAST_HOURS = 24
DEFAULT_FUTURE_HOURS = 12
DEFAULT_UPDATE_INTERVAL = 600       # 10 min

# Mimořádné refreshe (request_refresh od FVE/topení/pokojovky…) spouštějí celý
# update cyklus. Aby nekřivily data a nemlátily do API:
# – předpověď se v rámci TTL bere z cache (open-meteo/met.no se volá jen na
#   pravidelném tiku; TTL < interval, ať řádný tik vždy stáhne čerstvou),
# – vzorek do bufferu venkovní teploty se přidá jen když je poslední starší
#   než BUFFER_SAMPLE_MIN_S (průměr je počtově vážený -> shluk mimořádných
#   vzorků by ho vychýlil k obdobím FV aktivity).
FORECAST_TTL_S = 540
BUFFER_SAMPLE_MIN_S = 300

# Vyhlazení vstupní teploty do ekvitermy (EMA). 1.0 = bez vyhlazení.
# 0.4 ~ časová konstanta 2 cykly (~20 min) při 10min intervalu – odfiltruje
# zbytkový sub-0,1°C zub modelové teploty, aniž přidá citelné zpoždění.
SMOOTH_ALPHA = 0.4

# Pevná hloubka rolling bufferu venkovní teploty (h). Buffer se ořezává JEN
# proti téhle hodnotě, NIKDY podle nastaveného okna „hodiny minulost" – díky
# tomu omyl typu 1 místo 10 nic nesmaže a oprava okna je okamžitá.
# 24 h = celý diurnální cyklus; čtecí okno z bufferu jen vybírá poslední past_hours.
BUFFER_HOURS = 24

# --- Ekvitermní křivka -----------------------------------------------------
# Pevné body na ose X (modelová teplota °C); uživatel/AI ladí jen Y (zpátečka).
CURVE_X = [-15, -5, 0, 5, 15]
CURVE_Y_DEFAULT = [34.0, 32.0, 30.0, 28.0, 25.0]
CURVE_KEYS = ["bod_m15", "bod_m5", "bod_0", "bod_p5", "bod_p15"]
CURVE_LABELS = ["−15 °C", "−5 °C", "0 °C", "+5 °C", "+15 °C"]

DEFAULT_COEF = 1.0                  # násobič korekce na teplotu místnosti (0–6, 0 = vypnuto)

MIN_RETURN = 20.0                   # bezpečnostní clamp zápisu
MAX_RETURN = 45.0

# --- Předpovědní zdroje (oba bez klíče, bez zásahu uživatele) --------------
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
METNO_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
# met.no ToS vyžaduje identifikační User-Agent (nastaveno v kódu, ne uživatelem)
USER_AGENT = "acond_regulator/0.8.0 (https://github.com/pavorlechre/acond_regulator)"

PLATFORMS = ["sensor", "binary_sensor", "number", "time", "datetime", "switch", "select", "image", "button", "text"]

# --- OSA A: TOPENÍ PODLE PŘEBYTKŮ (executor 40014 = páka, návnada 40008) -----
# Žrout přebytků: dům (betonový slab) je akumulátor. Při SoC baterie >= hranice
# převezme řízení od ekvitermy, drží 40008 = ekviterma+návnada (přišpendlí stroj
# na strop 40014) a MODULUJE 40014 tak, aby držel SoC u hranice (default 95 %).
# Řídicí zákon (ověřeno topeni_prebytky_simulace.py, 14/14 scénářů):
#   ΔP = B + k·(SoC − hranice)        [W],  B = časově vážený průměr (přetok + výkon baterie)
#   Δ40014 = ΔP / (aep/40014)         poměr čten jen v ustáleném stavu
#   strop stroje: hýbej 40014 jen v ±10 % kolem SKUTEČNÉ (normalizované); mimo -> čekej
#   převzetí na SoC >= hranice, měkké předání na SoC < dolní_mez (master zůstává)
#   tvrdý konec (master OFF, bez návratu): strop zpátečky / strop místnosti / ztráta čidla
#   TUV (bit_3) i odmraz (bit_8) -> zamrznout (drž), po doběhu pokračovat
# Přetok (net_grid) a SoC baterie SDÍLÍ s FVE (fve.pretok_value/battery_value);
# NOVÝ zdroj = znaménkový VÝKON baterie (nabíjení +). Zadrátováno do OR-brány
# binary_sensor.mar_fve_aktivni (jinak ho Dovolená guard mine). Gate: běží jen ve
# strategii Ekviterma + Standard (mimo -> vrať a zhasni). Viz topeni.py.
TOPENI_STORE_VERSION = 1
TOPENI_TICK_S = 30                 # vzorkování bilance + evaluate (rovnoměrná kadence)
TOPENI_STEP_SAFETY_S = 180         # pojistný tik KROKU 40014, i když SoC nemění celé %
TOPENI_RETRY_S = 60                # když je 40014/40008 při aktivaci nedostupný
TOPENI_BAND = 0.10                 # ±10 % kolem skutečné = strop stroje (Pavleho pravidlo)
# Fallback poměru r = aep/otáčky, než se změří první živý. Řádově: ~2000 W /
# ~3000 ot ≈ 0,7 W na otáčku. (Původních 45 byl pozůstatek procentové éry
# slideru – první krok by vyšel ~70× menší. Prakticky se fallback skoro
# nepoužije: první běžící krok ho přepíše měřeným poměrem.)
TOPENI_R_FALLBACK = 0.7
TOPENI_FULL_SOC = 98.0             # SoC >= tohle = baterka plná -> slider rovnou na max
                                   # (řeší mrtvé SoC-hodiny u plné baterky, nahrazuje slepý tik)
TOPENI_MIN_RPM = 200.0             # skutečné otáčky pod tímhle = stroj stojí -> nekrokuj, drž poměr

# Skutečná kapacita pro strop stroje (±10 %). ZMĚŘ NA ŽELEZE: podle druhu TČ se
# v instalačním flow porovnává stejná veličina (otáčky s otáčkami / výkon s výkonem).
# Default = otáčky 30024 normalizované 30020. Kdyby jednotka jela na výkon, přijde
# sem jiný pár registrů. Normalizace na podíl (x/x_max) je porovnání „stejné se
# stejnou" nezávisle na jednotce -> bezpečný default do doby doladění.
ACOND_RPM_MAX = "sensor.acond_30020_comp_rpm_max"

# --- entity klíče (slug _attr_name -> entity_id s prefixem mar_) ---
TOPENI_MASTER_KEY = "topeni"                          # switch.mar_topeni
TOPENI_SRC_BATT_VYKON_KEY = "topeni_zdroj_baterie_vykon"  # text.mar_topeni_zdroj_baterie_vykon
TOPENI_HRANICE_KEY = "topeni_soc_hranice"             # number.mar_topeni_soc_hranice
TOPENI_DOLNI_KEY = "topeni_soc_dolni_mez"             # number.mar_topeni_soc_dolni_mez
TOPENI_TAH_KEY = "topeni_tah"                         # number.mar_topeni_tah (k)
TOPENI_OKNO_KEY = "topeni_okno"                       # number.mar_topeni_okno (min)
TOPENI_NAVNADA_KEY = "topeni_navnada"                 # number.mar_topeni_navnada (+°C)
TOPENI_STROP_ZP_KEY = "topeni_strop_zpatecka"         # number.mar_topeni_strop_zpatecka
TOPENI_STROP_MIST_KEY = "topeni_strop_mistnost"       # number.mar_topeni_strop_mistnost
TOPENI_CIDLO_KEY = "topeni_cidlo_timeout"             # number.mar_topeni_cidlo_timeout

# --- defaulty (VŠE laditelné na železe – „praxe ukáže") ---
TOPENI_HRANICE_DEFAULT = 95.0; TOPENI_HRANICE_MIN = 50.0; TOPENI_HRANICE_MAX = 100.0; TOPENI_HRANICE_STEP = 1.0
TOPENI_DOLNI_DEFAULT = 90.0;  TOPENI_DOLNI_MIN = 50.0;  TOPENI_DOLNI_MAX = 99.0;  TOPENI_DOLNI_STEP = 1.0
TOPENI_TAH_DEFAULT = 100.0;   TOPENI_TAH_MIN = 0.0;     TOPENI_TAH_MAX = 500.0;   TOPENI_TAH_STEP = 10.0
TOPENI_OKNO_DEFAULT = 3.0;    TOPENI_OKNO_MIN = 1.0;    TOPENI_OKNO_MAX = 15.0;   TOPENI_OKNO_STEP = 0.5
TOPENI_NAVNADA_DEFAULT = 10.0; TOPENI_NAVNADA_MIN = 0.0; TOPENI_NAVNADA_MAX = 15.0; TOPENI_NAVNADA_STEP = 1.0
TOPENI_STROP_ZP_DEFAULT = 40.0; TOPENI_STROP_ZP_MIN = 25.0; TOPENI_STROP_ZP_MAX = 45.0; TOPENI_STROP_ZP_STEP = 1.0
TOPENI_STROP_MIST_DEFAULT = 24.0; TOPENI_STROP_MIST_MIN = 20.0; TOPENI_STROP_MIST_MAX = 30.0; TOPENI_STROP_MIST_STEP = 0.5
TOPENI_CIDLO_DEFAULT = 5.0;   TOPENI_CIDLO_MIN = 1.0;   TOPENI_CIDLO_MAX = 30.0;  TOPENI_CIDLO_STEP = 1.0

# GoodWe default nového zdroje (uživatel přepíše vložením ze schránky)
# --- Návnada pokojovkou (druhý řidič FVE Topení; půjč-a-vrať přes správce) ---
# Zvýšení POŽADOVANÉ teploty místnosti (40001). Účinek jde přes korekci do zpátečky
# a je SAMOOMEZUJÍCÍ: jak se dům dotápí, rozdíl (set − skutečná) klesá a tah slábne.
# Reálná páka = delta × koeficient korekce (Pavel ×2 -> +1 °C dá +2 °C na zpátečce).
TOPENI_NAVNADA_ROOM_KEY = "topeni_navnada_pokojovka"   # number.mar_topeni_navnada_pokojovka
TOPENI_NAVNADA_ROOM_DEFAULT = 0.0   # default 0 = chová se jako dosud (jen zpátečková návnada)
TOPENI_NAVNADA_ROOM_MIN = 0.0
TOPENI_NAVNADA_ROOM_MAX = 3.0
TOPENI_NAVNADA_ROOM_STEP = 0.5
TOPENI_ROOM_MARGIN = 0.5      # pokojovková návnada nesmí zvednout setpoint blíž než tohle
                              # ke stropu místnosti -> program dohasne sám, netrefí tvrdý stop

# --- Hlídač restartu po předání ekvitermě (bod 2) -------------------------
# Po velkých přetocích je zpátečka vysoko nad ekvitermou -> stroj vypne vlastní
# hysterezí (±2 °C) a sám by naskočil až 2 °C POD ekvitermou (u slabu hodiny).
# Hlídač probouzí VYPNUTÍ primárního čerpadla (30045 bit 2 – stroj jím po ~15 min
# proplachuje okruh, aby vůbec změřil pravdivou zpátečku) a při dosažení prahu
# pustí kámen Topit. Jednorázový: po výstřelu (nebo když se stroj rozjede sám) končí.
TOPENI_RESTART_OVER = 1.0     # zpátečka nejvýš tolik °C NAD ekvitermou -> startuj
TOPENI_RESTART_TOL = 0.5      # |readback 30008 − ekvitermní cíl| pod tohle = ekviterma už zapsala
                              # (pojistka proti startu s návnadou ještě na registru)

TOPENI_SRC_BATT_VYKON_DEFAULT = "sensor.battery_power"   # znaménkový, nabíjení +

# --- BEZBATERKOVÁ VĚTEV (Jan) --------------------------------------------
# Detekce: NENÍ nakonfigurovaný zdroj výkonu baterie -> bezbaterková větev.
# Podle KONFIGURACE, ne podle toho, jestli čidlo zrovna odpovídá (výpadek měniče
# nesmí uprostřed mraku přepnout regulaci do jiného režimu).
#
# Slovník je schválně STEJNÝ jako u FVE TUV, aby se uživatel neučil dvakrát:
#   STOP  „vytrvalost" – jak hluboko pod přebytek smím spadnout. SMÍ BÝT KLADNÝ
#         = nech si rezervu, nejdi do importu („radši ať trochu uteče").
#         Dělá dvojí práci: je to i CÍL, na který se moduluje.
#   START „hojnost"    – kolik přebytku stačí, aby si topení vzalo BĚŽÍCÍ stroj
#         (veze se, stojí jen pár set W navíc).
#   START STUDENÝ      – kolik musí být, aby mělo smysl ROZJET STOJÍCÍ stroj
#         (koupíš rovnou celé jeho minimum, proto výš).
# Měkké pojistky: studený >= start >= stop (jinak věčně běžící smyčka).
TOPENI_STOP_KEY = "topeni_stop"                  # number.mar_topeni_stop
TOPENI_STOP_DEFAULT = 300.0
TOPENI_STOP_MIN = -3000.0
TOPENI_STOP_MAX = 2000.0
TOPENI_STOP_STEP = 50.0

TOPENI_START_KEY = "topeni_start"                # number.mar_topeni_start
TOPENI_START_DEFAULT = 500.0
TOPENI_START_MIN = 0.0
TOPENI_START_MAX = 5000.0
TOPENI_START_STEP = 50.0

TOPENI_START_COLD_KEY = "topeni_start_studeny"   # number.mar_topeni_start_studeny
TOPENI_START_COLD_DEFAULT = 2000.0
TOPENI_START_COLD_MIN = 0.0
TOPENI_START_COLD_MAX = 8000.0
TOPENI_START_COLD_STEP = 100.0

TOPENI_PRODLEVA_KEY = "topeni_prodleva"          # number.mar_topeni_prodleva
TOPENI_PRODLEVA_DEFAULT = 10.0    # min na minimu pod STOP -> předání ekvitermě
TOPENI_PRODLEVA_MIN = 1.0
TOPENI_PRODLEVA_MAX = 60.0
TOPENI_PRODLEVA_STEP = 1.0

# --- Ochrana zápisů do 40014 (EEPROM v desce) ----------------------------
# JEN pro bezbaterkovou větev: ta tiká časem (30 s), takže by bez ochrany psala
# řádově tisíce zápisů denně. Bateriová větev krokuje na změnu celého % SoC, tedy
# desítky za den – tam se nesahá, ať se nezpomalí, co funguje.
TOPENI_BLOK_KEY = "topeni_blok"                  # number.mar_topeni_blok
TOPENI_BLOK_DEFAULT = 5.0         # min – blok zápisů SMĚREM NAHORU (přidání počká)
TOPENI_BLOK_MIN = 0.0
TOPENI_BLOK_MAX = 30.0
TOPENI_BLOK_STEP = 1.0
TOPENI_BLOK_DOWN_S = 60.0         # dolů krátce – peníze utíkají hned
TOPENI_DEADBAND_RPM = 100.0       # pásmo necitlivosti: menší změnu vůbec nezapisuj
TOPENI_PRURAZ_W = 1500.0          # nouzový průraz: hlubší schodek smí projít i v bloku

# --- Rozjezd stojícího stroje kamenem Topit (obě větve) ------------------
TOPENI_STONE_COOLDOWN_S = 900.0   # nepouštěj kámen dokola, když stroj nenaskočí
# Před kamenem počkej, až readback 30008 ukáže návnadu (kámen si z něj bere
# „původní zpátečku" a na konci ji vrací -> vrátí návnadu, ne holou ekvitermu).
# Čekání na readback místo slepé pauzy: jeden spadlý poll Acondu (~15 s) nesmí
# rozhodnout. Strop čekání kámen pustí stejně (horší varianta nesmí blokovat start).
TOPENI_STONE_WAIT_S = 40.0
TOPENI_STONE_POLL_S = 5.0
# Vypínač hlídače restartu (switch.mar_topeni_hlidac, default zapnuto).
# Vypnutý = po měkkém předání se restart nechá čistě na hysterezi stroje.
TOPENI_HLIDAC_KEY = "topeni_hlidac"
TOPENI_BILANCE_KEY = "topeni_bilance"                  # sensor.mar_topeni_bilance (průměr)
TOPENI_BILANCE_SYROVA_KEY = "topeni_bilance_syrova"    # sensor.mar_topeni_bilance_syrova (okamžitá)
TOPENI_BATT_DISPOS_KEY = "topeni_baterie_vybijeni_kladne"  # switch: konvence znaménka výkonu baterie

# ===========================================================================
# STATISTIKA (per-režim) – sdílené konstanty
# ===========================================================================
# Zdrojové entity Acondu pro delta-crediting + klasifikaci režimu.
# POZOR: bereme „DNES" čítače, ne „celkem". Firmware totál (ees/tes) přepisuje
# až o půlnoci (jednou denně), takže během provozního cyklu necvakne a
# event-driven crediting by nedostal nic. „Dnes" čítače tikají živě; půlnoční
# reset (5.0 -> 0.0) ošetří reset-logika akumulátoru (záporná delta = rebaseline).
ACOND_EL_DNES = "sensor.acond_30040_eed"   # elektrická energie dnes (kWh, živě, reset o půlnoci)
ACOND_TEP_DNES = "sensor.acond_30039_ted"  # tepelná energie dnes (kWh, živě, reset o půlnoci)

# Chlazení má VLASTNÍ čítače. Ověřeno na železe 30.07.2026 (půl hodiny chlazení):
# `eed`/`ted` stály na místě, zatímco `ced`/`eecd` naskočily na 5,70 / 1,30 kWh.
# Čistá separace -> chlazení se nemůže maskovat jako topení a `Celkem` v tabulce
# zůstává topná strana, jak to má Acondac.
ACOND_TEP_CHLAD_DNES = "sensor.acond_30073_ced"   # chladicí energie dnes (kWh)
ACOND_EL_CHLAD_DNES = "sensor.acond_30074_eecd"   # el. energie chlazení dnes (kWh)

ACOND_BIT_HEAT = "binary_sensor.acond_30007_tc_status_bit_1"    # TČ v provozu (= topení)
ACOND_BIT_TUV = "binary_sensor.acond_30007_tc_status_bit_3"     # ohřev TUV
ACOND_BIT_DEFROST = "binary_sensor.acond_30007_tc_status_bit_8" # odmrazování
ACOND_BIT_COOL = "binary_sensor.acond_30007_tc_status_bit_12"   # chlazení

# Klíče režimů (exkluzivní klasifikace s prioritou).
#
# POŘADÍ: odmraz > TUV (bit_3) > chlazení (bit_12) > náběh TUV (rozdíl
# setpointů) > topení > idle.
#
# Náběhová heuristika je ZÁMĚRNĚ pod chlazením. V chladicí sezóně je zapnutý
# letní provoz, takže regulace na 40008 nezapisuje a visí tam stará hodnota;
# rozdíl proti 30008 může snadno přeskočit TUV_START_DELTA_K a ukousnout celé
# chlazení do TUV. Bit_12 je tvrdý fakt ze stroje, heuristika jen berlička pro
# fázi, kdy bit_3 ještě nesvítí — tak ať je i v pořadí níž.
#
# Chlazení nad topením proto, že bit_1 („TČ v provozu") svítí i při chlazení.
#
# Pozn.: MODE_IDLE = „ostatní/standby" – kýbl pro režii mimo aktivní režimy.
# Existuje proto, aby Σ(režimy) == totál jednotky (přesná reconciliation).
MODE_HEAT = "topeni"
MODE_TUV = "tuv"
MODE_DEFROST = "odmraz"
MODE_COOL = "chlazeni"
MODE_IDLE = "ostatni"
MODES = [MODE_HEAT, MODE_TUV, MODE_DEFROST, MODE_COOL, MODE_IDLE]

# Lidské popisky pro entity / dashboard.
MODE_LABELS = {
    MODE_HEAT: "topení",
    MODE_TUV: "TUV",
    MODE_DEFROST: "odmrazování",
    MODE_COOL: "chlazení",
    MODE_IDLE: "ostatní",
}

# Debounce ukládání stavu statistiky (s). Při poruše ztratíme max tento interval
# energetických přírůstků; baseline se ukládá spolu s akumulátory -> totály se po
# restartu samy dorovnají přes „downtime" deltu (uvnitř téhož dne, viz níže).
STATS_SAVE_DELAY = 30

# --- Kruh denních záznamů -------------------------------------------------- #
# Celoživotní kbelík + 8 denních záznamů: [0..6] = 7 uzavřených dnů, [-1] = dnes.
# Dokud není záznamů 8, sloupec „7 dnů" hlásí None -> tabulka vykreslí „–"
# (falešně malé číslo v prvním týdnu po nasazení je horší než prázdno).
RING_DAYS = 8
CLOSED_DAYS = 7

PERIOD_TODAY = "dnes"
PERIOD_YESTERDAY = "vcera"
PERIOD_WEEK = "7dni"
PERIOD_TOTAL = "celkem"
PERIODS = [PERIOD_TODAY, PERIOD_YESTERDAY, PERIOD_WEEK, PERIOD_TOTAL]

# --- Starty (náběžné hrany surových bitů 30007) ---------------------------- #
# NESMÍ se počítat z přepnutí režimu: klasifikace je exkluzivní, takže TUV
# naskočené v běžícím topení by vypadalo jako nový start kompresoru. Kbelíky
# startů se navzájem NEvylučují (na rozdíl od energie a motohodin).
# Start kompresoru se NEPOČÍTÁ z bitu 1 („TČ v provozu")! Ověřeno na železe
# 28.07.2026: bit_1 naskočí ~2–3 min PŘED kompresorem (předběh primárního
# čerpadla). Firmwarový čítač 30075 při pokusu skočil 132->133->134 přesně na
# náběh otáček, ne na bit_1. Počítáme tedy hrany otáček 30024.
#
# Od 0.9.2 na stejném prahu visí i MOTOHODINY: připisují se jen, když kompresor
# doopravdy točí. Bez toho spadne do „Topení" proplach okruhu po ohřevu vody
# (bit_1 svítí, kompresor stojí) a TUV-only den má v tabulce 33 minut topení
# proti 0 kWh — Acondac má na takovém dni 0h 0min. Změřeno 30.07.2026.
# Když jsou otáčky NEDOSTUPNÉ, gate se neuplatní a připisuje se dál podle režimu:
# „nevím" není „stojí", jinak by výpadek Modbusu tiše ukrajoval motohodiny.
RPM_RUNNING_MIN = 100        # ot/min, nad tím považujeme kompresor za běžící

# Účel startu: v okamžiku startu porovnáme, co stroj CHCE (30008) s tím, co mu
# píšeme (40008). Při ohřevu TUV si stroj sám nastaví 60 °C, a to ~2 min PŘED
# startem kompresoru -> signál je k dispozici hned, žádné čekací okno.
# Rozdíl (ne absolutní práh) proto, že u radiátorové instalace je topná
# zpátečka výš než u podlahovky a napevno zadaná šedesátka by selhala.
TUV_START_DELTA_K = 5.0      # 30008 − 40008 nad tímto = start kvůli TUV
TUV_START_ABS = 50.0         # záloha, když 40008 není k dispozici

# Okno pro DOÚČTOVÁNÍ startu kvůli TUV. Okamžitá klasifikace výš platí, když si
# stroj TUV naplánuje sám. Když ale kompresor rozjede NÁŠ boost (kámen Topit,
# přetokové topení), je pořadí obrácené: v okamžiku hrany otáček je 30008 ≈ 40008
# (oba na návnadě), rozdíl je nula — a teprve za pár minut si voda vezme už
# běžící stroj. Ověřeno na železe 30.07.2026: 2 starty, oba skončily ohřevem
# vody, oba zaúčtované jako „ne TUV".
#
# Proto se po startu drží čekající záznam a `starty_tuv` se připíše ZPĚTNĚ do
# kbelíku toho dne, kdy start proběhl (drží se reference na kbelík, takže start
# ve 23:58 a voda v 00:02 sedí správně).
#
# Strop je nutný: v zimě stroj běží na topení hodiny a pak si vezme vodu — to
# start kvůli TUV NENÍ. Okno navíc zavírá zastavení kompresoru, ne jen čas.
TUV_START_WINDOW_S = 600     # 10 min od startu; pak už si vodu vzal běžící stroj

START_COMP = "kompresor"
START_TUV = "tuv"
START_DEFROST = "odmraz"
START_KEYS = [START_COMP, START_TUV, START_DEFROST]

# Životní počet startů kompresoru z firmwaru. Sekce registrů, kterou má jen
# novější Modbus -> u starší jednotky bude unavailable a tabulka ukáže „–".
# Používá se VÝHRADNĚ pro sloupec „celkem od instalace" (my počítáme od nasazení).
ACOND_STARTS_TOTAL = "sensor.acond_30075_compressor_starts"

# --- Teploty: pásma platnosti čidla --------------------------------------- #
# Sentinel odpojeného čidla je −39 °C (viz reference) -> musí vypadnout pásmem.
# Pozor: dolní hranice nesmí být pod −39, jinak sentinel proleze a stáhne
# denní průměr o celé stupně.
BAND_OUTDOOR = (-35.0, 55.0)
BAND_INDOOR = (5.0, 40.0)

# Tik statistiky (s). Seká nedokončené segmenty (motohodiny + integrál teplot),
# osvěžuje první sloupec tabulky a řeší mrznoucí motohodiny (F3). Stav entity
# se přepisuje jen při skutečné změně čísla, jinak by to zaplavilo recorder.
STATS_TICK = 30

# --- Režim přetokových programů ------------------------------------------- #
# Tříbarevný pruh do historie + zdroj pro překryvné kbelíky statistiky.
#
# PAST V POJMENOVÁNÍ: v kódu `.active` znamená MASTER ZAPNUTÝ (a
# binary_sensor.mar_fve_aktivni je OR-brána masterů). Tady „Aktivní" znamená, že
# program právě DRŽÍ KORMIDLO (`topeni.heating` / `fve.boosting`). Kompresor
# v tu chvíli běžet nemusí – proto ne „Topí" ani „Ohřívá“.
REZIM_OFF = "Vyp"
REZIM_WAIT = "Čeká"
REZIM_ON = "Aktivní"
REZIM_STATES = [REZIM_OFF, REZIM_WAIT, REZIM_ON]

TOPENI_REZIM_KEY = "topeni_rezim"        # sensor.mar_topeni_rezim
FVE_TUV_REZIM_KEY = "fve_tuv_rezim"      # sensor.mar_fve_tuv_rezim
MAR_TOPENI_REZIM = "sensor.mar_topeni_rezim"
MAR_FVE_TUV_REZIM = "sensor.mar_fve_tuv_rezim"

# --- Překryvné kbelíky („z přetoků") -------------------------------------- #
# NEJSOU to režimy: jsou to paralelní příčky vedle klasifikace, takže invariant
# Σ(režimy) == čítač zůstává netknutý. Připisuje se jen TEPLO (rozhodnutí), a
# jen když sedí i režim -> jsou to skutečné podmnožiny řádků Topení a TUV.
OVERLAY_TOPENI = "fve_topeni"
OVERLAY_TUV = "fve_tuv"
OVERLAYS = [OVERLAY_TOPENI, OVERLAY_TUV]

# --- Snímek statistiky na disk -------------------------------------------- #
# HA nepustí systémové „Uložit obrázek" nad image entitou NIKDE: na PC pravé
# tlačítko spolkne more-info dialog, na tabletu krátké i dlouhé podržení otevře
# entitu. Ověřeno 30.07.2026 na obou zařízeních. Není to nastavením, je to tak
# postavené — a z integrace se to opravit nedá.
#
# Cesta ven: zapsat PNG do `config/www/`, odkud ho HA servíruje na `/local/…`.
# To je normální statická adresa; otevřená v prohlížeči (nová záložka) platí
# běžná pravidla a obrázek jde uložit pravým tlačítkem / podržením prstu.
#
# Soubor se ukládá POD DATOVANÝM jménem a odkaz míří na něj. Fixní jméno by
# nešlo — `/local` servíruje HA s dlouhou cache (řádově týdny), takže by
# prohlížeč tvrdošíjně ukazoval první stažený snímek. Datované jméno je pokaždé
# jiná adresa, takže se cache nemá čeho chytit. Stabilní kopie `statistika.png`
# se píše taky, ale jen jako „poslední snímek" pro ruční sáhnutí přes sdílenou
# složku — na dashboard NEPATŘÍ, právě kvůli cache.
#
# POZOR: `/local` je bez přihlášení. Kdo je na síti a zná adresu, přečte si to.
# U statistiky TČ to neřešíme, ale je to vlastnost, ne přehlédnutí.
SNAPSHOT_SUBDIR = "mar"        # config/www/mar/ -> /local/mar/
SNAPSHOT_BASE = "statistika"       # statistika-20260730-174100.png
SNAPSHOT_BASE_DAYS = "statistika-dny"  # denní rozpad, vlastní archiv
SNAPSHOT_KEEP = 20             # kolik datovaných snímků nechat, zbytek se maže

# Adresa, na které integrace servíruje příručku. KONTRAKT: odkazy „Zpět do
# programu" uvnitř příručky se podle téhle cesty rozhodují, jestli se vůbec
# mají zobrazit (na GitHub Pages ta adresa neexistuje). Přejmenování = rozbíjející
# změna na obou stranách naráz.
MANUAL_URL = "/mar-prirucka"

# Odkazovat se MUSÍ na soubor, ne na složku: aiohttp v HA výpis adresářů
# nepovoluje, takže holé `/mar-prirucka/` vrátí 403 Forbidden (ověřeno na
# železe 10.08.2026). Registrace složky je správně kvůli budoucím obrázkům,
# jen se do ní nesmí mířit naslepo.
MANUAL_INDEX = f"{MANUAL_URL}/index.html"

# Adresa, na které integrace servíruje obrázky kompresoru pro kartu stavu.
# KONTRAKT stejného druhu jako MANUAL_URL: skládá se z ní jméno souboru přímo
# v šabloně markdown karty (`kompresor-{stupeň}{pásmo}.svg`), takže přejmenování
# je rozbíjející změna na obou stranách naráz.
KOMPRESOR_URL = "/mar-kompresor"

# KONTRAKT třetího druhu: složka s podklady a vrstvami vizuálního schématu.
# Jména souborů skládá dashboard (state_image), integrace jen zpřístupní složku.
SCHEMA_URL = "/mar-schema"

# ── vizuální schéma ──
KOMPRESOR_VIZUAL_KEY = "kompresor_vizual"     # sensor.mar_kompresor_vizual
MA_FVE_KEY = "ma_fve"                         # binary_sensor.mar_ma_fve
SCHEMA_SEKUNDAR_KEY = "schema_sekundar"       # select.mar_schema_sekundar
SCHEMA_SMER_KEY = "schema_smer"               # sensor.mar_schema_smer

# Jak schéma pozná, že sekundárním okruhem teče voda. Odvodit se to nedá:
# čerpadlo v zásuvce žádný registr nehlásí a instalace bez AKU nemá okruh 2.
SEKUNDAR_CERPADLO = "Podle čerpadla"    # standard – řídí TČ (bit_4)
SEKUNDAR_STALE = "Stále"                # čerpadlo trvale v zásuvce
SEKUNDAR_PRIMAR = "Podle primáru"       # bez AKU – okruh kopíruje primár
SEKUNDAR_OPTIONS = [SEKUNDAR_CERPADLO, SEKUNDAR_STALE, SEKUNDAR_PRIMAR]
SEKUNDAR_DEFAULT = SEKUNDAR_CERPADLO

# ── ouška pod schématem (select.mar_vrstva) ──
# Volby jsou jen ouška, která mají obsah. Další ouška (Energie, Ekviterma,
# Počasí, Stroj) jsou v obrázku ztlumená a přibudou sem, až dostanou obsah.
# Select je společný pro všechna zařízení, proto se po VRSTVA_NAVRAT_S sám
# vrátí na Schéma — tablet na zdi nezůstane viset v grafu.
VRSTVA_KEY = "vrstva"                         # select.mar_vrstva
VRSTVA_SCHEMA = "Schéma"
VRSTVA_TEPLOTY = "Teploty"
VRSTVA_POCASI = "Počasí"
VRSTVA_OPTIONS = [VRSTVA_SCHEMA, VRSTVA_TEPLOTY, VRSTVA_POCASI]
VRSTVA_NAVRAT_S = 300
TEPLOTY_KEY = "teploty_png"                   # image.mar_teploty
TEPLOTY_HODIN = 12                            # kolik hodin graf ukazuje
TEPLOTY_KROK_MIN = 5                          # mřížka grafu (min)
TEPLOTY_OBNOVA_S = 300                        # jak často se graf překreslí, když je vidět
POCASI_KEY = "pocasi_png"                     # image.mar_pocasi
# Ouško Výkon: vložený graf přes pravou spodní část schématu (topná soustava),
# zapíná a vypíná se klepnutím na ouško — nezávisle na Teplotách a Počasí.
VRSTVA_VYKON_KEY = "vrstva_vykon"             # switch.mar_vrstva_vykon
VYKON_KEY = "vykon_png"                       # image.mar_vykon
VYKON_HODIN = 12
ACOND_TEPELNY_VYKON = "sensor.acond_30028_ahp"   # tepelný výkon
ACOND_COP = "sensor.acond_30029_cop"
POCASI_HODIN = 24                             # ouško Počasí: hodin zpět i dopředu
# Malé teploty ve schématu („okno ve zdi“ mezi TČ a bojlerem) — vidět pořád.
MINI_TEPLOTY_KEY = "teploty_mini_png"         # image.mar_teploty_mini
MINI_TEPLOTY_HODIN = 6
ACOND_OUTLET = "sensor.acond_30018_t_act_water_outlet"   # výstup topné vody z TČ


def signal_vrstva_vykon(entry_id: str) -> str:
    """Dispatcher signál: switch.mar_vrstva_vykon se přepnul (nese bool)."""
    return f"{DOMAIN}_{entry_id}_vrstva_vykon"


def signal_vrstva(entry_id: str) -> str:
    """Dispatcher signál: select.mar_vrstva změnil volbu (nese novou volbu)."""
    return f"{DOMAIN}_{entry_id}_vrstva"

# Denní rozpad pro export: `den:0` = dnešek, `den:7` = nejstarší v kruhu.
# Není to entita ani view — jen klíč do akumulátoru pro druhou variantu snímku.
PERIOD_DAY_PREFIX = "den:"

# Klíče statistických period-sensorů (entity_id kontrakt).
STAT_TODAY_KEY = "stat_dnes"        # sensor.mar_stat_dnes
STAT_YESTERDAY_KEY = "stat_vcera"   # sensor.mar_stat_vcera
STAT_WEEK_KEY = "stat_7dni"         # sensor.mar_stat_7dni


# ===========================================================================
# PROFILY NASTAVENÍ (ulož / načti pojmenovanou sadu)
# ===========================================================================
# Profil = ekvitermní nastavení, volitelně plus LADĚNÍ FVE. Nic víc.
#
# Co v profilu ZÁMĚRNĚ NENÍ (a nikdy nesmí být):
#  – režimy (Den/noc, Zebra, Okna teploty, Dovolená) – mimo rozsah,
#  – ZAPOJENÍ (text.*_zdroj_*, znaménko baterie) – to je popis konkrétního
#    domu; cizí profil by příjemci nasypal názvy cizího měniče a jeho větev
#    by přestala vidět přebytek TIŠE, bez chyby,
#  – mastery a přepínače – profil je nastavení, ne povel k zapnutí,
#  – BEZPEČNOSTNÍ STROPY (topeni_strop_zpatecka, topeni_strop_mistnost,
#    topeni_cidlo_timeout) – jsou vázané na soustavu (podlaha × radiátor).
#    Cizí profil, který tiše zvedne nejvyšší zpátečku na 45 °C, neselže
#    hlasitě – jen měsíc přetápí. Proto se nepřenášejí.
#
# Důsledek: v profilu nezůstala jediná entita, která by uměla zápis odmítnout
# (mastery měly guardy, čísla ne). Načtení je čistá sada zápisů do `number`.
PROFIL_SCHEMA = 1                 # verze schématu: INFORMACE do logu, ne brána.
                                  # Zvedá se, až když se změní VÝZNAM klíče,
                                  # ne když klíč přibude.
PROFIL_DIR = "mar_profily"        # config/mar_profily/
PROFIL_VZOR_DIR = "vzory"         # ve složce integrace = jen ke čtení
PROFIL_EXT = ".csv"               # .csv, ať to otevře Sheets i poznámkový blok
PROFIL_SEP = ";"                  # STŘEDNÍK, ne čárka: Sheets v české lokalizaci
                                  # by na „30,6" rozstřelil řádek
PROFIL_HEADER_ROW = "sekce;klic;hodnota"
PROFIL_NONE = "—"                 # placeholder v rozbalovátku (options nesmí být prázdné)
PROFIL_VZOR_PREFIX = "[vzor] "    # značka předpřipravené sady (jen ke čtení)
PROFIL_NAME_MAX = 48

PROFIL_SECTION_EKV = "ekviterma"
PROFIL_SECTION_FVE = "fve"
PROFIL_SECTIONS = [PROFIL_SECTION_EKV, PROFIL_SECTION_FVE]

# --- klíče po sekcích ------------------------------------------------------
# Sekce ekviterma (8) – jádro profilu, aplikuje se VŽDY.
PROFIL_KEYS_EKV = [
    "bod_m15", "bod_m5", "bod_0", "bod_p5", "bod_p15",
    "koeficient_korekce", "hodiny_minulost", "hodiny_predpoved",
]

# Sekce fve (15) – jen když je zapnutý příslušný přepínač „včetně FVE".
# Rozdělená po VĚTVÍCH, protože s baterkou a bez baterky se ladí jinými prvky.
PROFIL_KEYS_FVE_SDILENE = [
    "fve_tuv_start", "fve_tuv_stop",
    "topeni_navnada", "topeni_navnada_pokojovka",
]
PROFIL_KEYS_FVE_BATT = [          # FVE topení, bateriová větev (SoC = palivoměr)
    "topeni_soc_hranice", "topeni_soc_dolni_mez", "topeni_tah", "topeni_okno",
]
PROFIL_KEYS_FVE_NOBATT = [        # FVE topení, bezbaterková větev (bilance = palivo)
    "topeni_stop", "topeni_start", "topeni_start_studeny",
    "topeni_prodleva", "topeni_blok",
]
PROFIL_KEYS_TUV_BATT = [          # FVE TUV, jen s baterkou (vlastní detekce větve!)
    "fve_tuv_baterie_prah", "fve_tuv_baterie_pokles",
]

# Mapa klíč -> entita. Je to zároveň WHITELIST: co tu není, se při načtení
# ignoruje. Cizí ani AI soubor tedy nemůže sáhnout nikam mimo těchto 23 čísel.
# Psáno naplno (ne f-stringem ve smyčce) schválně – přejmenování entity je
# pak vidět očima na jednom místě.
PROFIL_ENTITY = {
    "bod_m15": "number.mar_bod_m15",
    "bod_m5": "number.mar_bod_m5",
    "bod_0": "number.mar_bod_0",
    "bod_p5": "number.mar_bod_p5",
    "bod_p15": "number.mar_bod_p15",
    "koeficient_korekce": "number.mar_koeficient_korekce",
    "hodiny_minulost": "number.mar_hodiny_minulost",
    "hodiny_predpoved": "number.mar_hodiny_predpoved",
    "fve_tuv_start": "number.mar_fve_tuv_start",
    "fve_tuv_stop": "number.mar_fve_tuv_stop",
    "fve_tuv_baterie_prah": "number.mar_fve_tuv_baterie_prah",
    "fve_tuv_baterie_pokles": "number.mar_fve_tuv_baterie_pokles",
    "topeni_soc_hranice": "number.mar_topeni_soc_hranice",
    "topeni_soc_dolni_mez": "number.mar_topeni_soc_dolni_mez",
    "topeni_tah": "number.mar_topeni_tah",
    "topeni_okno": "number.mar_topeni_okno",
    "topeni_navnada": "number.mar_topeni_navnada",
    "topeni_navnada_pokojovka": "number.mar_topeni_navnada_pokojovka",
    "topeni_stop": "number.mar_topeni_stop",
    "topeni_start": "number.mar_topeni_start",
    "topeni_start_studeny": "number.mar_topeni_start_studeny",
    "topeni_prodleva": "number.mar_topeni_prodleva",
    "topeni_blok": "number.mar_topeni_blok",
}

# Zdroj baterie pro FVE TUV. Větev FVE TUV se řídí TÍMTO polem, ne zdrojem
# výkonu baterie u topení – jsou to dvě nezávislé detekce (ptáme se dvakrát).
PROFIL_TUV_BATT_SRC = "text.mar_fve_zdroj_baterie"

# Entity klíče (slug _attr_name -> entity_id s prefixem mar_)
PROFIL_SELECT_KEY = "profil"                  # select.mar_profil
PROFIL_NAZEV_KEY = "profil_nazev"             # text.mar_profil_nazev
PROFIL_ULOZIT_FVE_KEY = "profil_ulozit_fve"   # switch.mar_profil_ulozit_fve
PROFIL_NACIST_FVE_KEY = "profil_nacist_fve"   # switch.mar_profil_nacist_fve
PROFIL_KOLIZE_KEY = "profil_kolize"           # binary_sensor.mar_profil_kolize
# Stav přepínačů se čte ZE STAVOVÉHO STROJE (co uživatel vidí, to platí), takže
# jejich entity_id je kontrakt jako každá jiná adresa.
PROFIL_SW_ULOZIT = "switch.mar_profil_ulozit_fve"
PROFIL_SW_NACIST = "switch.mar_profil_nacist_fve"
PROFIL_STAV_KEY = "profil_stav"               # sensor.mar_profil_stav

# Kopie ke stažení. Stejný trik jako u snímků statistiky: HA servíruje
# `config/www/` na `/local/`, odkud jde soubor uložit normálně přes prohlížeč.
# DATOVANÉ jméno je nutné – `/local` má dlouhou cache, fixní jméno by nabízelo
# první stažený soubor navěky. POZOR: `/local` je bez přihlášení.
PROFIL_WWW_PREFIX = "profil"
PROFIL_WWW_KEEP = 20              # kolik kopií ke stažení nechat, zbytek smazat


def device_info(entry_id: str):
    """Jedno zařízení 'MaR' -> pevná, krátká entity_id (sensor/number.mar_*)."""
    from homeassistant.helpers.device_registry import DeviceInfo

    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name="MaR",
        manufacturer="Acond (open-source)",
        model="Ekvitermní regulace",
    )


def signal_stats_snapshot(entry_id: str) -> str:
    """Dispatcher signál: tlačítko „Obnovit snímek" ho pošle, image entita si
    posune image_last_updated a frontend si vyžádá nový render. Odděluje
    platformy button a image (nemusí na sebe držet referenci)."""
    return f"{DOMAIN}_stats_snapshot_{entry_id}"


def signal_stats_snapshot_days(entry_id: str) -> str:
    """Totéž pro denní rozpad. Vlastní signál, ne parametr: obě image entity
    se překreslují nezávisle a nemá smysl vyrábět jednu, když si člověk
    vyžádal druhou (kreslení je nejdražší část)."""
    return f"{DOMAIN}_stats_snapshot_days_{entry_id}"


def signal_stats_updated(entry_id: str) -> str:
    """Dispatcher signál: akumulátor ho pošle po každém přepočtu, statistické
    sensory si na něj sednou a překreslí stav (akumulátor není coordinator)."""
    return f"{DOMAIN}_stats_updated_{entry_id}"


def signal_run_updated(entry_id: str) -> str:
    """Dispatcher signál primitivu běhu kompresoru (RunState -> mar_stav).
    Pošle se při každé debounced změně 'kompresor běží / stojí', aby se okno
    překreslilo hned při rozběhu/zastavení, ne až za 10 min na coordinator tiku."""
    return f"{DOMAIN}_run_updated_{entry_id}"


def signal_seq_updated(entry_id: str) -> str:
    """Dispatcher signál sekvencí START/STOP (SequenceRunner -> mar_stav).
    Pošle se při každé změně fáze kamene, aby okno ukázalo, co kámen zrovna dělá."""
    return f"{DOMAIN}_seq_updated_{entry_id}"


def signal_zebra_updated(entry_id: str) -> str:
    """Dispatcher signál překryvu Zebra (ZebraController -> mar_stav).
    Pošle se při každé změně fáze (topení/pauza/idle), aby okno ukázalo, kde Zebra je."""
    return f"{DOMAIN}_zebra_updated_{entry_id}"


def signal_dennoc_updated(entry_id: str) -> str:
    """Dispatcher signál časového režimu Den/noc (DenNocController -> mar_stav +
    switch/binary_sensor). Pošle se při změně master přepínače, oken nebo hrany,
    aby se okno (uvnitř/mimo, příští změna) i odkrývání oken překreslily."""
    return f"{DOMAIN}_dennoc_updated_{entry_id}"


def signal_okna_updated(entry_id: str) -> str:
    """Dispatcher signál časového režimu Okna +/- teploty (OknaTeplotyController ->
    mar_stav + switch/binary_sensor). Pošle se při změně master přepínače, oken,
    delty nebo hrany výpůjčky, aby se stav (aktivní okno, delta, base, příští změna)
    i progresivní odkrývání oken překreslily."""
    return f"{DOMAIN}_okna_updated_{entry_id}"


def signal_dovolena_updated(entry_id: str) -> str:
    """Dispatcher signál režimu Dovolená (DovolenaController -> mar_stav + switch).
    Pošle se při změně master přepínače, intervalu, cílů nebo hrany výpůjčky."""
    return f"{DOMAIN}_dovolena_updated_{entry_id}"


def signal_fve_updated(entry_id: str) -> str:
    """Dispatcher signál FVE (FveController -> switch/number/text/sensor/binary).
    Pošle se při změně masteru, prahů, zdrojů, průměru přetoku nebo hrany výpůjčky,
    aby se mirror sensory, průměr, master i binary_sensor.mar_fve_aktivni překreslily."""
    return f"{DOMAIN}_fve_updated_{entry_id}"


def signal_topeni_updated(entry_id: str) -> str:
    """Dispatcher signál topení podle přebytků (TopeniController -> switch/number/
    text/binary_sensor/mar_stav). Pošle se při změně masteru, parametrů, zdroje,
    převzetí/předání nebo kroku 40014, aby se stav i OR-brána mar_fve_aktivni překreslily."""
    return f"{DOMAIN}_topeni_updated_{entry_id}"


def signal_profily_updated(entry_id: str) -> str:
    """Dispatcher signál profilů (ProfilyManager -> select/switch/binary_sensor/
    sensor). Pošle se po změně seznamu souborů, jména, přepínačů nebo po
    dokončení operace, aby se rozbalovátko, kolizní tlačítko i stavová řádka
    překreslily bez čekání na poll."""
    return f"{DOMAIN}_profily_updated_{entry_id}"
