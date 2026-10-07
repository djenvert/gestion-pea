"""Cours de clôture quotidiens via l'API publique (non officielle) de Yahoo Finance."""

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

ENTETES = {"User-Agent": "Mozilla/5.0"}


class CoursIndisponible(Exception):
    pass


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=ENTETES)
    with urllib.request.urlopen(req, timeout=20) as rep:
        return json.load(rep)


SECTEURS = {
    "Basic Materials": "Matériaux de base",
    "Communication Services": "Communication",
    "Consumer Cyclical": "Consommation cyclique",
    "Consumer Defensive": "Consommation de base",
    "Energy": "Énergie",
    "Financial Services": "Finance",
    "Healthcare": "Santé",
    "Industrials": "Industrie",
    "Real Estate": "Immobilier",
    "Technology": "Technologie",
    "Utilities": "Services aux collectivités",
}


def trouver_symbole(isin: str) -> tuple[str, str, str | None]:
    """ISIN → (symbole, place, secteur) : première action trouvée, c'est la cotation principale."""
    url = ("https://query2.finance.yahoo.com/v1/finance/search?"
           + urllib.parse.urlencode({"q": isin, "quotesCount": 5, "newsCount": 0}))
    for q in _get(url).get("quotes", []):
        if q.get("quoteType") == "EQUITY":
            secteur = q.get("sectorDisp") or q.get("sector")
            return q["symbol"], q.get("exchDisp") or q.get("exchange"), SECTEURS.get(secteur, secteur)
    raise CoursIndisponible(f"aucun symbole Yahoo pour {isin}")


def clotures(symbole: str, depuis: date) -> tuple[str, list[tuple[str, float]]]:
    """Renvoie (devise, [(date ISO, clôture)]) depuis `depuis` inclus.

    La séance du jour n'est retenue qu'une fois le marché fermé.
    """
    debut = int(datetime.combine(depuis, datetime.min.time(), timezone.utc).timestamp())
    fin = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbole)}?"
           + urllib.parse.urlencode({"period1": debut, "period2": fin, "interval": "1d"}))
    resultat = (_get(url).get("chart", {}).get("result") or [None])[0]
    if not resultat:
        raise CoursIndisponible(f"pas de données pour {symbole}")
    meta = resultat["meta"]
    decalage = timedelta(seconds=meta.get("gmtoffset", 0))
    horodatages = resultat.get("timestamp") or []
    fermetures = resultat["indicators"]["quote"][0].get("close") or []

    # Séance en cours : la dernière barre n'est pas une clôture définitive
    seance = meta.get("currentTradingPeriod", {}).get("regular", {})
    maintenant = datetime.now(timezone.utc).timestamp()
    en_cours = seance.get("start", 0) <= maintenant < seance.get("end", 0)

    lignes = []
    for ts, cloture in zip(horodatages, fermetures):
        if cloture is None:
            continue
        if en_cours and ts >= seance["start"]:
            continue
        jour = (datetime.fromtimestamp(ts, timezone.utc) + decalage).date()
        lignes.append((jour.isoformat(), round(cloture, 4)))
    return meta.get("currency", "EUR"), lignes
