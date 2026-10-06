# Acond Regulator (MaR)

MaR (Měření a Regulace) je nadstavba integrace [Acond Heat Pump](https://github.com/pavorlechre/homeassistant-acond) pro Home Assistant. Řídí tepelné čerpadlo Acond podle vlastní ekvitermní křivky a předpovědi počasí, umí topit a ohřívat vodu z přebytků fotovoltaiky, má časové programy a živé schéma celé soustavy.

**📖 [Příručka MaR](https://pavorlechre.github.io/acond_regulator/)** – co MaR umí, jak vypadá a jak se nastavuje. Stejnou příručku najdeš po instalaci i přímo v Home Assistantu.

> **Ve vývoji.** Integraci zatím testují dvě pilotní instalace.

## Instalace přes HACS

1. HACS → tři tečky vpravo nahoře → **Custom repositories** → `https://github.com/pavorlechre/acond_regulator`, typ **Integration**
2. Stáhni **Acond Regulator (MaR)** a udělej **plný restart** Home Assistantu
3. Nastavení → Zařízení a služby → Přidat integraci → **Acond Regulator**

Potřebuješ integraci Acond Heat Pump a kartu [`apexcharts-card`](https://github.com/RomRider/apexcharts-card).

## Licence

MIT
