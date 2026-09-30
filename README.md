# Acond Regulator (MaR)

MaR (Měření a Regulace) – nadstavba integrace Acond pro Home Assistant: ekvitermní regulace, řízení podle FVE přebytků, časové programy a schéma soustavy pro tepelná čerpadla Acond.

> **Ve vývoji.** Integraci zatím testují dvě pilotní instalace. Nastavení, entity i vzhled se mohou měnit.

## Co to umí

- **Ekviterma** – vlastní ekvitermní křivka s průměrováním venkovní teploty (minulost + předpověď) a korekcí na místnost; zapisuje požadovanou zpátečku do čerpadla.
- **Strategie topení** – Ekviterma, Minimum, Stálá teplota, Boost, Bez MaR.
- **Časové programy** – Zebra (topení s pauzami), Den/noc, Okna ± teploty, Dovolená.
- **FVE** – ohřev TUV a topení z přebytků fotovoltaiky, s baterií i bez ní.
- **Schéma soustavy** – živý obrázek čerpadla, akumulace, TUV a domu jako první okno dashboardu.
- **Statistika a trendy** – COP, energie, motohodiny, starty kompresoru.
- **Příručka** v češtině přímo v integraci.

## Požadavky

- Home Assistant 2024.1 nebo novější
- Integrace **[Acond Heat Pump](https://github.com/pavorlechre/homeassistant-acond)** (Modbus TCP) – MaR čte a zapisuje přes její entity
- HACS karta [`apexcharts-card`](https://github.com/RomRider/apexcharts-card) pro grafy

## Instalace přes HACS

1. HACS → tři tečky vpravo nahoře → **Custom repositories**
2. URL: `https://github.com/pavorlechre/acond_regulator`, typ **Integration**
3. Najdi **Acond Regulator (MaR)** a stáhni
4. **Plný restart** Home Assistantu
5. Nastavení → Zařízení a služby → Přidat integraci → **Acond Regulator**

Dashboard se napojí sám do dashboardu integrace Acond (okna na konci).

## Licence

MIT
