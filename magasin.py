#!/usr/bin/env python3
"""
Henter fyllingsgrad for vannmagasiner fra NVE og sender til Slack.
Designet for å trigges av cron onsdager rett før kl 13:00 på Raspberry Pi.
"""

import os
import sys
import time
import logging
from pathlib import Path
import requests

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
NVE_URL = "https://biapi.nve.no/magasinstatistikk/api/Magasinstatistikk/HentOffentligDataSisteUke"
NVE_LENKE = "https://www.nve.no/energi/analyser-og-statistikk/magasinstatistikk/"
STATE_FILE = Path(__file__).parent / ".siste_nve_uke.txt"

MAKS_FORSOK = 90
SJEKK_INTERVALL_SEK = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nve-magasin")

PRISOMRADER = {
    "1": "Østlandet (NO1)",
    "2": "Sørlandet (NO2)",
    "3": "Midt-Norge (NO3)",
    "4": "Nord-Norge (NO4)",
    "5": "Vestlandet (NO5)"
}

def hent_data():
    resp = requests.get(NVE_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()

def formater_pil(endring):
    return "📈" if endring > 0 else "📉" if endring < 0 else "➡️"

def formater_komma(tall):
    """Konverterer desimalpunkt til norsk kommaformatering."""
    return f"{tall:.1f}".replace('.', ',')

def bygg_slack_melding(data):
    # Nasjonale tall (omrnr 0 eller omrType 'NO')
    nasjonal = next(
        (r for r in data if str(r.get("omrnr")) == "0" or r.get("omrType") == "NO"), 
        None
    )
    
    if not nasjonal:
        logger.warning("Fant ikke nasjonale tall i NVE-responsen.")
        return None, None

    aar, uke = nasjonal["iso_aar"], nasjonal["iso_uke"]
    ny_uke_id = f"{aar}-{uke}"
    forrige_uke = uke - 1 if uke > 1 else 52

    # Konverterer fra desimalbrøk (0.636) til prosent (63.6%)
    fylling_n = nasjonal["fyllingsgrad"] * 100
    endring_n = nasjonal["endring_fyllingsgrad"] * 100

    # Dynamisk retning for den redaksjonelle ingressen
    if endring_n > 0:
        retning_tekst = f"opp {formater_komma(abs(endring_n))} prosentpoeng"
    elif endring_n < 0:
        retning_tekst = f"ned {formater_komma(abs(endring_n))} prosentpoeng"
    else:
        retning_tekst = "urendret"

    # Redaksjonell ingress
    ingress = (
        f"Den samlede fyllingsgraden i norske vannmagasin var ved slutten av uke {uke} "
        f"på {formater_komma(fylling_n)} prosent. Fyllingsgraden er da {retning_tekst} "
        f"fra uke {forrige_uke}."
    )

    # Bygger Slack-meldingen - nå med ingressen i fet tekst!
    tekst = (
        f"*💧 Magasinstatistikk uke {uke}/{aar}*\n\n"
        f"*{ingress}*\n\n"
        f"*Norge totalt:* {formater_komma(fylling_n)}% ({formater_pil(endring_n)} {endring_n:+.1f} p.p.)\n"
        f"_Volum: {nasjonal['fylling_TWh']:.1f} av {nasjonal['kapasitet_TWh']:.1f} TWh_\n\n"
        f"*Regionale tall (prisområder):*\n"
    )

    # Filtrerer strengt på omrType == 'EL' for å unngå duplikater
    regioner = [
        r for r in data 
        if r.get("omrType") == "EL" and str(r.get("omrnr")) in PRISOMRADER
    ]
    regioner.sort(key=lambda x: int(x["omrnr"]))

    for r in regioner:
        navn = PRISOMRADER[str(r["omrnr"])]
        fylling = r["fyllingsgrad"] * 100
        endring = r["endring_fyllingsgrad"] * 100
        tekst += f"• *{navn}:* {formater_komma(fylling)}% ({formater_pil(endring)} {endring:+.1f} p.p.)\n"

    # Kilde/lenke nederst
    tekst += f"\n🔗 <{NVE_LENKE}|Se fullstendig magasinstatistikk hos NVE>"

    return ny_uke_id, tekst

def main():
    if not SLACK_WEBHOOK_URL:
        logger.error("SLACK_WEBHOOK_URL er ikke satt.")
        sys.exit(1)

    sist_sendt_uke = STATE_FILE.read_text().strip() if STATE_FILE.exists() else ""
    logger.info("Venter på nye tall fra NVE...")

    for forsok in range(1, MAKS_FORSOK + 1):
        try:
            data = hent_data()
            ny_uke_id, melding_tekst = bygg_slack_melding(data)

            if ny_uke_id and ny_uke_id != sist_sendt_uke:
                logger.info(f"Nye tall for {ny_uke_id} oppdaget! Sender til Slack...")
                resp = requests.post(SLACK_WEBHOOK_URL, json={"text": melding_tekst}, timeout=10)
                resp.raise_for_status()
                
                STATE_FILE.write_text(ny_uke_id)
                logger.info("Vellykket! Melding sendt og tilstand lagret.")
                sys.exit(0)
            else:
                logger.info(f"Forsøk {forsok}/{MAKS_FORSOK}: Siste tilgjengelige uke er {ny_uke_id} (allerede varslet). Venter...")

        except requests.RequestException as e:
            logger.warning(f"Nettverksfeil mot NVE: {e}")

        time.sleep(SJEKK_INTERVALL_SEK)

if __name__ == "__main__":
    main()
