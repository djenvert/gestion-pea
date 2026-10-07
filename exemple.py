#!/usr/bin/env python3
"""Génère tableau_exemple.html et bilan_exemple.png à partir de données fictives.

Les sociétés, leurs ISIN, symboles et secteurs sont réels (informations publiques).
Les opérations, quantités et cours sont inventés : marche aléatoire à graine fixe,
pour que le fichier soit identique d'une génération à l'autre. Les données passent
par la même chaîne que le vrai tableau (base SQLite en mémoire, recalcul des
positions et de l'historique, même modèle HTML).

Usage : python3 exemple.py
"""

import random
import sqlite3
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import bilan
import db
import tableau

RACINE = Path(__file__).resolve().parent
SORTIE = RACINE / "tableau_exemple.html"
SORTIE_BILAN = RACINE / "bilan_exemple.png"
DEBUT, FIN = date(2025, 10, 1), date(2026, 9, 30)

# ISIN, libellé, symbole Yahoo, secteur, lieu d'exécution (None = Paris), cours de départ
VALEURS = [
    ("FR0000120271", "TOTALENERGIES SE", "TTE.PA", "Énergie", None, 58),
    ("FR0000121014", "LVMH MOET HENNESSY VUITTON", "MC.PA", "Consommation cyclique", None, 610),
    ("FR0000120578", "SANOFI", "SAN.PA", "Santé", None, 92),
    ("FR0000121972", "SCHNEIDER ELECTRIC SE", "SU.PA", "Industrie", None, 225),
    ("FR0000120073", "AIR LIQUIDE", "AI.PA", "Matériaux de base", None, 170),
    ("FR0000131104", "BNP PARIBAS ACTIONS A", "BNP.PA", "Finance", None, 66),
    ("NL0010273215", "ASML HOLDING", "ASML.AS", "Technologie", "EURONEXT AMSTERDAM", 690),
    ("DE0007164600", "SAP SE", "SAP.DE", "Technologie", "XETRA", 235),
    ("DE0008404005", "ALLIANZ SE-VNA", "ALV.DE", "Finance", "XETRA", 335),
    ("IT0003128367", "ENEL", "ENEL.MI", "Services aux collectivités", "EURONEXT MILAN", 7.4),
    ("ES0144580Y14", "IBERDROLA", "IBE.MC", "Services aux collectivités", "CRIEE MARCHE ELECTRONIQUE", 14.5),
]

# (date, ISIN, sens, montant visé en euros)
OPERATIONS = [
    ("2025-10-02", "FR0000120271", "ACHAT", 1500), ("2025-10-02", "FR0000121014", "ACHAT", 1800),
    ("2025-10-09", "NL0010273215", "ACHAT", 2100), ("2025-10-16", "FR0000120578", "ACHAT", 1400),
    ("2025-11-04", "DE0007164600", "ACHAT", 1650), ("2025-11-18", "FR0000121972", "ACHAT", 1350),
    ("2025-12-02", "IT0003128367", "ACHAT", 1100), ("2025-12-15", "FR0000120073", "ACHAT", 1700),
    ("2026-01-08", "FR0000131104", "ACHAT", 1300), ("2026-01-22", "DE0008404005", "ACHAT", 1650),
    ("2026-02-05", "FR0000120271", "ACHAT", 1000), ("2026-02-19", "ES0144580Y14", "ACHAT", 1200),
    ("2026-03-10", "NL0010273215", "ACHAT", 1400), ("2026-04-07", "FR0000121014", "VENTE", 1200),
    ("2026-04-21", "FR0000120578", "ACHAT", 950), ("2026-05-12", "DE0007164600", "ACHAT", 1200),
    ("2026-06-03", "FR0000121972", "ACHAT", 900), ("2026-06-24", "IT0003128367", "ACHAT", 800),
    ("2026-07-15", "FR0000131104", "ACHAT", 1000), ("2026-09-08", "FR0000120073", "ACHAT", 1200),
    ("2026-09-29", "DE0008404005", "ACHAT", 1000),
]

D = Decimal


def centimes(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def jours_ouvres():
    d = DEBUT
    while d <= FIN:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def cours_fictifs(alea: random.Random) -> dict[str, dict[str, float]]:
    """Marche aléatoire géométrique, tendance et volatilité propres à chaque valeur."""
    cours = {}
    for isin, *_, depart in VALEURS:
        tendance, vol = alea.uniform(-0.0004, 0.0010), alea.uniform(0.010, 0.020)
        c, serie = float(depart), {}
        for d in jours_ouvres():
            c *= 1 + alea.gauss(tendance, vol)
            serie[d.isoformat()] = round(c, 3 if c < 20 else 2)
        cours[isin] = serie
    return cours


def avis(n: int, jour: str, isin: str, sens: str, montant: int, cours: dict) -> dict:
    """Avis d'opéré fictif, avec des frais calqués sur ceux d'un vrai compte."""
    _, libelle, _, _, lieu, _ = next(v for v in VALEURS if v[0] == isin)
    prix = D(str(cours[isin][jour]))
    quantite = max(1, int(montant / prix))
    brut = centimes(quantite * prix)
    commission = centimes(brut * D("0.005"))
    paris = lieu is None
    # Paris : taxe sur les transactions financières à l'achat ; ailleurs : courtages du marché
    frais = centimes(brut * D("0.004")) if paris and sens == "ACHAT" else D(0)
    courtages = D(0) if paris else max(D("0.10"), centimes(brut * D("0.0001")))
    couts = commission + frais + courtages
    j = datetime.fromisoformat(jour)
    return {
        "reference": f"{70000000 + n * 137}" if paris else f"G{j:%y%m%d}U{1000 + n:04d}",
        "date_execution": j.replace(hour=10 + n % 6, minute=(n * 7) % 60, second=(n * 13) % 60),
        "sens": sens,
        "type_transaction": f"{sens.capitalize()} {'au comptant' if paris else 'en bourse étrangère'}",
        "type_ordre": ("au marché" if n % 3 else "à cours limité") if paris else None,
        "libelle": libelle,
        "isin": isin,
        "quantite": quantite,
        "cours": prix,
        "devise": "EUR",
        "lieu_execution": lieu,
        "montant_brut": brut,
        "courtages": courtages,
        "commission": commission,
        "frais": frais,
        "montant_net": brut + couts if sens == "ACHAT" else brut - couts,
        "solde_titres": None,
        # La dernière opération illustre une saisie provisoire, en attente de l'avis
        "provisoire": n == len(OPERATIONS) - 1,
    }


def base() -> sqlite3.Connection:
    """Base SQLite en mémoire remplie du portefeuille fictif (aussi utilisée par les tests)."""
    alea = random.Random(2026)
    cours = cours_fictifs(alea)
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    for isin, _, symbole, secteur, lieu, _ in VALEURS:
        con.execute("INSERT INTO tickers (isin, symbole, place, secteur) VALUES (?, ?, ?, ?)",
                    (isin, symbole, lieu or "EURONEXT PARIS", secteur))
        con.executemany("INSERT INTO cours (isin, date, cloture) VALUES (?, ?, ?)",
                        [(isin, d, c) for d, c in cours[isin].items()])
    for n, (jour, isin, sens, montant) in enumerate(OPERATIONS):
        a = avis(n, jour, isin, sens, montant, cours)
        if a["provisoire"]:  # commission encore inconnue
            a["montant_net"] -= a["commission"]
            a["commission"] = D(0)
        db.inserer(con, a, "exemple")
    db.recalculer_valeurs(con)
    db.recalculer_historique(con)
    return con


def generer() -> Path:
    con = base()
    d = tableau.donnees(con)
    d["exemple"] = True
    d["genere_le"] = f"{FIN:%d/%m/%Y} à 18:30"  # fixe : le fichier ne change pas à chaque génération
    tableau.ecrire(d, SORTIE)
    b = bilan.donnees(con, date(2026, 9, 25))  # dernière semaine complète du jeu fictif
    b["exemple"] = True
    bilan.rendre(b, SORTIE_BILAN)
    return SORTIE


if __name__ == "__main__":
    print(generer())
    print(SORTIE_BILAN)
