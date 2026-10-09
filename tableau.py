"""Génère tableau.html : tableau de bord autonome (données intégrées, aucun appel réseau)."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import db

RACINE = Path(__file__).resolve().parent
MODELE = RACINE / "tableau_modele.html"
SORTIE = RACINE / "tableau.html"

PAYS = {"FR": "France", "DE": "Allemagne", "IT": "Italie", "ES": "Espagne",
        "NL": "Pays-Bas", "BE": "Belgique", "LU": "Luxembourg", "IE": "Irlande",
        "PT": "Portugal", "FI": "Finlande", "AT": "Autriche"}


def situations(con: sqlite3.Connection) -> dict:
    """Pour chaque transaction (par référence) : la position juste avant et juste après."""
    etat, res = {}, {}
    for t in con.execute("SELECT * FROM transactions ORDER BY date_execution, id"):
        avant = dict(etat.get(t["isin"], {"qte": 0, "pv": 0}))
        e = db.appliquer(etat, t)
        res[t["reference"]] = {
            "qte_avant": avant["qte"],
            "qte_apres": e["qte"],
            "pru_apres": float(round(e["cout"] / e["qte"], 4)) if e["qte"] else None,
            "pv_operation": float(round(e["pv"] - avant["pv"], 2)),
        }
    return res


def donnees(con: sqlite3.Connection) -> dict:
    lignes = lambda sql: [dict(r) for r in con.execute(sql)]
    positions = lignes(
        "SELECT p.*, k.symbole, k.secteur, (SELECT count(*) FROM transactions t "
        "WHERE t.isin = p.isin AND t.provisoire = 1) AS provisoires FROM v_positions p "
        "LEFT JOIN tickers k ON k.isin = p.isin ORDER BY p.valorisation DESC")
    for p in positions:
        p["pays"] = PAYS.get(p["isin"][:2], p["isin"][:2])
    transactions = lignes(
        "SELECT date_execution, reference, sens, type_transaction, libelle, isin, quantite, "
        "cours, montant_brut, courtages + commission + frais AS couts, montant_net, provisoire "
        "FROM transactions ORDER BY date_execution DESC")
    apres = situations(con)
    for t in transactions:
        t.update(apres[t["reference"]])
    return {
        "genere_le": datetime.now().strftime("%d/%m/%Y à %H:%M"),
        "historique": lignes("SELECT * FROM historique ORDER BY date"),
        "positions": positions,
        "transactions": transactions,
        "etf": dict(db.ETF, taux=float(db.ETF_TAUX)),
        "frais": con.execute(
            "SELECT coalesce(sum(courtages + commission + frais), 0) FROM transactions").fetchone()[0],
    }


def ecrire(d: dict, sortie: Path) -> Path:
    # `</` échappé pour que les données ne puissent pas fermer la balise <script>
    charge = json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
    sortie.write_text(MODELE.read_text(encoding="utf-8").replace("/*__DONNEES__*/null", charge),
                      encoding="utf-8")
    return sortie


def generer(base: Path) -> Path:
    con = sqlite3.connect(base)
    con.row_factory = sqlite3.Row
    return ecrire(donnees(con), SORTIE)


if __name__ == "__main__":
    print(generer(RACINE / "pea.db"))
