#!/usr/bin/env python3
"""
Henter fyllingsgrad for vannmagasiner fra NVE og sender til Slack.
Designet for å trigges av cron onsdager rett før kl 13:00.
Skriptet sjekker hyppig i opptil 15 minutter for å varsle sekundet tallene slippes.
"""

import os
import sys
import time
import logging
from pathlib import Path
import requests

# --- Konfigurasjon --------------------------------------------------------
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
NVE_URL = "https://biapi.nve.no/magasinstatistikk/api/Magasinstatistikk/HentOffentligDataSisteUke"

# Fil for å huske hvilken uke vi sist varslet om (for å unngå duplikater)
STATE_FILE = Path(__file__).parent / ".siste_nve_uke.txt"

# Maks antall forsøk før skriptet gir opp (90 forsøk * 10 sek = 15 minutter)
MAKS_FORSOK = 90
SJEKK_INTERVALL_SEK = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nve-magasin")

# Kartlegging av NVEs prisområder (omrnr)
PRISOMRADER = {
    1: "Østlandet (NO1)",
    2: "Sørlandet (NO2)",
    3: "Midt-Norge (NO3)",
    4: "Nord-Norge (NO4)",
    5: "Vestlandet (NO5)"
}

def hent_data():
    resp = requests.get(NVE_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()

def formater_pil(endring):
    return "📈" if endring > 0 else "📉" if endring < 0 else "➡️"

def bygg_slack_melding(data):
    """Finner nasjonale og regionale tall og bygger en Slack-vennlig tekst."""
    nasjonal = next((r for r in data if r.get("omrType") == "EL" and r.get("omrnr") == 0), None)
    
    if not nasjonal:
        return None, None

    aar, uke = nasjonal["iso_aar"], nasjonal["iso_uke"]
    ny_uke_id = f"{aar}-{uke}"

    # 1. Nasjonale tall
    endring_n = nasjonal["endring_fyllingsgrad"]
    tekst = (
        f"*💧 Magasinstatistikk uke {uke}/{aar}*\n\n"
        f"*Norge totalt:* {nasjonal['fyllingsgrad']:.1f}% "
        f"({formater_pil(endring_n)} {endring_n:+.1f} p.p.)\n"
        f"_Volum: {nasjonal['fylling_TWh']:.1f} av {nasjonal['kapasitet_TWh']:.1f} TWh_\n\n"
    )

    # 2. Regionale tall (Prisområdene)
    tekst += "*Regionale tall:*\n"
    regioner = [r for r in data if r.get("omrType") == "NO" and r.get("omrnr") in PRISOMRADER.keys()]
    regioner.sort(key=lambda x: x["omrnr"])

    for r in regioner:
        navn = PRISOMRADER[r["omrnr"]]
        endring = r["endring_fyllingsgrad"]
        tekst += f"• *{navn}:* {r['fyllingsgrad']:.1f}% ({formater_pil(endring)} {endring:+.1f} p.p.)\n"

    return ny_uke_id, tekst

def main():
    if not SLACK_WEBHOOK_URL:
        logger.error("SLACK_WEBHOOK_URL er ikke satt. Avbryter.")
        sys.exit(1)

    sist_sendt_uke = STATE_FILE.read_text().strip() if STATE_FILE.exists() else ""
    logger.info("Venter på nye tall fra NVE...")

    for forsok in range(MAKS_FORSOK):
        try:
            data = hent_data()
            ny_uke_id, melding_tekst = bygg_slack_melding(data)

            if ny_uke_id and ny_uke_id != sist_sendt_uke:
                logger.info(f"Nye tall for {ny_uke_id} oppdaget! Sender til Slack.")
                
                resp = requests.post(SLACK_WEBHOOK_URL, json={"text": melding_tekst}, timeout=10)
                resp.raise_for_status()
                
                # Oppdater state-filen så vi ikke sender igjen
                STATE_FILE.write_text(ny_uke_id)
                logger.info("Melding sendt, avslutter.")
                sys.exit(0)
            
            else:
                logger.debug(f"Forsøk {forsok+1}/{MAKS_FORSOK}: Fortsatt tall for uke {ny_uke_id}. Venter {SJEKK_INTERVALL_SEK} sek...")

        except requests.RequestException as e:
            logger.warning(f"Nettverksfeil mot NVE: {e}")

        time.sleep(SJEKK_INTERVALL_SEK)

    logger.info("Tidsavbrudd: Fant ingen nye tall i løpet av vinduet på 15 minutter.")

if __name__ == "__main__":
    main()
