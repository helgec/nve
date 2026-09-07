#!/usr/bin/env python3
"""
Henter fyllingsgrad for norske vannmagasiner fra NVE og sender til Slack.

Kjører en evig løkke som sjekker klokka hvert 5. sekund, og henter/sender
data kun i tidsvinduet 12:59-13:03 hver onsdag (NVE publiserer normalt
nye tall ca. kl. 13:00 på onsdager).

"""

import os
import sys
import time
import logging
from datetime import datetime

import requests

# --- Konfigurasjon --------------------------------------------------------

NVE_URL = "https://biapi.nve.no/magasinstatistikk/api/Magasinstatistikk/HentOffentligDataSisteUke"
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

SJEKK_INTERVALL_SEKUNDER = 5
VINDU_START = (12, 59)  # (time, minutt)
VINDU_SLUTT = (13, 3)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("nve-magasin")


def er_i_vindu(naa: datetime) -> bool:
    """Onsdag = weekday() == 2. Sjekker om klokka er innenfor vinduet."""
    if naa.weekday() != 2:
        return False
    start = naa.replace(hour=VINDU_START[0], minute=VINDU_START[1], second=0, microsecond=0)
    slutt = naa.replace(hour=VINDU_SLUTT[0], minute=VINDU_SLUTT[1], second=0, microsecond=0)
    return start <= naa <= slutt


def hent_fyllingsgrad_norge():
    """Henter siste ukes data og plukker ut raden for hele landet (EL/0)."""
    resp = requests.get(NVE_URL, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    for rad in data:
        if rad.get("omrType") == "EL" and rad.get("omrnr") == 0:
            return rad
    return None


def send_slack_melding(rad):
    if not SLACK_WEBHOOK_URL:
        logger.error("SLACK_WEBHOOK_URL er ikke satt - kan ikke sende melding")
        return

    endring = rad["endring_fyllingsgrad"]
    pil = "📈" if endring > 0 else "📉" if endring < 0 else "➡️"

    tekst = (
        f"*💧 Magasinstatistikk Norge – uke {rad['iso_uke']}/{rad['iso_aar']}*\n"
        f"Fyllingsgrad: *{rad['fyllingsgrad']:.1f}%* "
        f"({pil} {endring:+.1f} p.p. fra forrige uke)\n"
        f"Volum: {rad['fylling_TWh']:.1f} TWh av {rad['kapasitet_TWh']:.1f} TWh kapasitet"
    )

    resp = requests.post(SLACK_WEBHOOK_URL, json={"text": tekst}, timeout=10)
    resp.raise_for_status()
    logger.info("Sendt melding til Slack")


def main():
    if not SLACK_WEBHOOK_URL:
        logger.warning("SLACK_WEBHOOK_URL er ikke satt i miljøvariabler!")

    sist_sendt_uke = None  # (iso_aar, iso_uke) - hindrer dobbel-sending i samme vindu
    logger.info("Starter overvåking av NVE magasinstatistikk...")

    while True:
        naa = datetime.now()

        if er_i_vindu(naa):
            try:
                rad = hent_fyllingsgrad_norge()
                if rad:
                    denne_uken = (rad["iso_aar"], rad["iso_uke"])
                    if denne_uken != sist_sendt_uke:
                        logger.info(
                            "Nye data funnet: uke %s, fyllingsgrad %.1f%%",
                            rad["iso_uke"], rad["fyllingsgrad"],
                        )
                        send_slack_melding(rad)
                        sist_sendt_uke = denne_uken
                    else:
                        logger.debug("Data for denne uken er allerede sendt, venter...")
                else:
                    logger.warning("Fant ingen data for hele landet (EL/0) i responsen")
            except requests.RequestException as e:
                logger.error("Feil ved henting fra NVE: %s", e)

        time.sleep(SJEKK_INTERVALL_SEKUNDER)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Avslutter...")
        sys.exit(0)
