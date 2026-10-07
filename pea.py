#!/usr/bin/env python3
"""Import des avis d'opéré déposés dans inbox/ vers la base pea.db.

Usage :
  pea.py [traiter]   traite les PDF de inbox/, archive dans outbox/AAAA-MM/
  pea.py recalculer  reconstruit la table `valeurs` depuis `transactions`
  pea.py cours       récupère les clôtures manquantes et met à jour `historique`
  pea.py tableau     régénère le tableau de bord tableau.html
"""

import fcntl
import logging
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import cours_yahoo
import db
import parser_avis
import tableau as tableau_html

RACINE = Path(__file__).resolve().parent
INBOX = RACINE / "inbox"
OUTBOX = RACINE / "outbox"
ERREURS = RACINE / "erreurs"
BASE = RACINE / "pea.db"
LOGS = RACINE / "logs"

log = logging.getLogger("pea")


def configurer_logs() -> None:
    LOGS.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.FileHandler(LOGS / "pea.log", encoding="utf-8"),
                    logging.StreamHandler(sys.stdout)):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def deplacer(fichier: Path, dossier: Path) -> Path:
    """Déplace sans écraser : ajoute _1, _2… si le nom existe déjà."""
    dossier.mkdir(parents=True, exist_ok=True)
    cible = dossier / fichier.name
    n = 1
    while cible.exists():
        cible = dossier / f"{fichier.stem}_{n}{fichier.suffix}"
        n += 1
    shutil.move(fichier, cible)
    return cible


def fichiers_stables(dossier: Path) -> list[Path]:
    """Avis PDF et ordres .txt de l'inbox dont la taille ne bouge plus (copie terminée)."""
    pdfs = sorted(p for p in dossier.iterdir() if p.is_file() and p.suffix.lower() in (".pdf", ".txt"))
    if not pdfs:
        return []
    tailles = {p: p.stat().st_size for p in pdfs}
    time.sleep(2)
    return [p for p in pdfs if p.exists() and p.stat().st_size == tailles[p]]


def traiter() -> int:
    pdfs = fichiers_stables(INBOX)
    if not pdfs:
        return 0
    con = db.connecter(BASE)
    importes = doublons = erreurs = 0
    for pdf in pdfs:
        try:
            avis = parser_avis.lire(pdf)
        except Exception as exc:
            erreurs += 1
            cible = deplacer(pdf, ERREURS)
            log.error("%s : %s → %s", pdf.name, exc, cible.relative_to(RACINE))
            continue
        with con:
            statut = db.inserer(con, avis, pdf.name)
        cible = deplacer(pdf, OUTBOX / avis["date_execution"].strftime("%Y-%m"))
        if statut != "doublon":
            importes += 1
            precision = {"remplacé": " (remplace la saisie provisoire)",
                         "importé": " (PROVISOIRE : commission inconnue, avis attendu)"
                                    if avis["provisoire"] else ""}[statut]
            log.info("%s : %s %d × %s (%s) à %s, net %s €%s → %s", pdf.name, avis["sens"].lower(),
                     avis["quantite"], avis["libelle"], avis["isin"], avis["cours"],
                     avis["montant_net"], precision, cible.relative_to(RACINE))
        else:
            doublons += 1
            log.info("%s : référence %s déjà en base, ignoré → %s",
                     pdf.name, avis["reference"], cible.relative_to(RACINE))
    recalculer(con)
    log.info("Bilan : %d importé(s), %d doublon(s), %d erreur(s)", importes, doublons, erreurs)
    if importes:  # une nouvelle valeur doit être valorisée sans attendre le passage du soir
        cours()
    return 1 if erreurs else 0


def recalculer(con=None) -> int:
    con = con or db.connecter(BASE)
    with con:
        ecarts = db.recalculer_valeurs(con)
        db.recalculer_historique(con)
    for ecart in ecarts:
        log.warning("Rapprochement : %s", ecart)
    for t in con.execute("SELECT reference, libelle, substr(date_execution, 1, 10) AS jour "
                         "FROM transactions WHERE provisoire = 1 ORDER BY date_execution"):
        log.info("En attente d'avis : %s du %s (ordre %s)", t["libelle"], t["jour"], t["reference"])
    return tableau()


def cours() -> int:
    """Complète la table `cours` pour chaque valeur détenue, puis reconstruit `historique`."""
    con = db.connecter(BASE)
    echecs = 0
    lignes = con.execute(
        "SELECT t.isin, t.libelle, min(substr(t.date_execution, 1, 10)) AS premier_achat, "
        "k.symbole, k.secteur, (SELECT max(date) FROM cours c WHERE c.isin = t.isin) AS dernier_cours "
        "FROM transactions t LEFT JOIN tickers k ON k.isin = t.isin "
        "GROUP BY t.isin ORDER BY t.libelle"
    ).fetchall()
    for v in lignes:
        try:
            symbole = v["symbole"]
            if not symbole or not v["secteur"]:
                trouve, place, secteur = cours_yahoo.trouver_symbole(v["isin"])
                symbole = symbole or trouve  # un symbole corrigé à la main est conservé
                with con:
                    con.execute(
                        "INSERT INTO tickers (isin, symbole, place, secteur) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT (isin) DO UPDATE SET secteur = excluded.secteur",
                        (v["isin"], symbole, place, secteur))
                if not v["symbole"]:
                    log.info("%s : symbole Yahoo %s (%s)", v["libelle"], symbole, place)
            # Recharge quelques jours en arrière pour absorber d'éventuelles corrections
            depuis = date.fromisoformat(v["dernier_cours"] or v["premier_achat"])
            depuis -= timedelta(days=5 if v["dernier_cours"] else 0)
            devise, clotures = cours_yahoo.clotures(symbole, depuis)
            with con:
                con.executemany(
                    "INSERT OR REPLACE INTO cours (isin, date, cloture, devise) VALUES (?, ?, ?, ?)",
                    [(v["isin"], d, c, devise) for d, c in clotures],
                )
            if devise != "EUR":
                log.warning("%s : cours en %s, valorisation non convertie", v["libelle"], devise)
        except Exception as exc:
            echecs += 1
            log.error("%s (%s) : cours non récupérés : %s", v["libelle"], v["isin"], exc)
    with con:
        nb = db.recalculer_historique(con)
    dernier = con.execute("SELECT * FROM historique ORDER BY date DESC LIMIT 1").fetchone()
    if dernier:
        log.info("Historique : %d jour(s). Au %s : valorisation %.2f €, investi %.2f €, "
                 "plus-value latente %+.2f € (%+.2f %%)", nb, dernier["date"],
                 dernier["valorisation"], dernier["montant_investi"], dernier["plus_value_latente"],
                 100 * dernier["plus_value_latente"] / dernier["montant_investi"])
    tableau()
    return 1 if echecs else 0


def tableau() -> int:
    try:
        tableau_html.generer(BASE)
    except Exception as exc:  # le tableau de bord ne doit jamais bloquer l'import
        log.error("Tableau de bord non généré : %s", exc)
        return 1
    return 0


def main() -> int:
    configurer_logs()
    commande = sys.argv[1] if len(sys.argv) > 1 else "traiter"
    commandes = {"traiter": traiter, "recalculer": recalculer, "cours": cours,
                 "tableau": tableau}
    if commande not in commandes:
        print(__doc__)
        return 2
    # Un seul traitement à la fois (launchd peut relancer pendant une exécution)
    with open(RACINE / ".pea.lock", "w") as verrou:
        fcntl.flock(verrou, fcntl.LOCK_EX)
        return commandes[commande]()


if __name__ == "__main__":
    sys.exit(main())
