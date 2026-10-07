"""Base SQLite du PEA : transactions et agrégat par valeur (PRU frais inclus)."""

import sqlite3
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY,
  reference TEXT NOT NULL UNIQUE,
  date_execution TEXT NOT NULL,
  sens TEXT NOT NULL CHECK (sens IN ('ACHAT','VENTE')),
  type_transaction TEXT NOT NULL,
  type_ordre TEXT,
  libelle TEXT NOT NULL,
  isin TEXT NOT NULL,
  quantite INTEGER NOT NULL,
  cours REAL NOT NULL,
  devise TEXT NOT NULL DEFAULT 'EUR',
  lieu_execution TEXT,
  montant_brut REAL NOT NULL,
  courtages REAL NOT NULL DEFAULT 0,
  commission REAL NOT NULL,
  frais REAL NOT NULL DEFAULT 0,
  montant_net REAL NOT NULL,
  solde_titres INTEGER,
  provisoire INTEGER NOT NULL DEFAULT 0,  -- saisi depuis le suivi des ordres, avis attendu
  fichier_source TEXT NOT NULL,
  importe_le TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_transactions_isin_date ON transactions (isin, date_execution);

CREATE TABLE IF NOT EXISTS valeurs (
  isin TEXT PRIMARY KEY,
  libelle TEXT NOT NULL,
  quantite INTEGER NOT NULL,
  pru REAL,
  montant_investi REAL NOT NULL,
  plus_value_realisee REAL NOT NULL DEFAULT 0,
  maj_le TEXT NOT NULL
);

-- Symbole Yahoo Finance de chaque valeur (résolu automatiquement, modifiable)
CREATE TABLE IF NOT EXISTS tickers (
  isin TEXT PRIMARY KEY,
  symbole TEXT NOT NULL,
  place TEXT,
  secteur TEXT
);

-- Cours de clôture quotidiens
CREATE TABLE IF NOT EXISTS cours (
  isin TEXT NOT NULL,
  date TEXT NOT NULL,                  -- 'AAAA-MM-JJ'
  cloture REAL NOT NULL,
  devise TEXT NOT NULL DEFAULT 'EUR',
  PRIMARY KEY (isin, date)
);

-- Valorisation quotidienne du portefeuille (reconstruite à chaque mise à jour)
CREATE TABLE IF NOT EXISTS historique (
  date TEXT PRIMARY KEY,
  montant_investi REAL NOT NULL,       -- coût de revient des titres détenus
  valorisation REAL NOT NULL,          -- Σ quantité × dernière clôture connue
  plus_value_latente REAL NOT NULL,
  plus_value_realisee REAL NOT NULL    -- cumulée depuis le début
);

-- Positions actuelles valorisées au dernier cours connu
DROP VIEW IF EXISTS v_positions;
CREATE VIEW v_positions AS
SELECT v.isin, v.libelle, v.quantite, v.pru, v.montant_investi,
       c.date AS date_cours, c.cloture AS cours,
       round(v.quantite * c.cloture, 2) AS valorisation,
       round(v.quantite * c.cloture - v.montant_investi, 2) AS plus_value_latente,
       round(100.0 * (c.cloture / v.pru - 1), 2) AS plus_value_pct
FROM valeurs v
LEFT JOIN cours c ON c.isin = v.isin
  AND c.date = (SELECT max(date) FROM cours WHERE isin = v.isin)
WHERE v.quantite > 0;
"""

CHAMPS = (
    "reference", "date_execution", "sens", "type_transaction", "type_ordre", "libelle",
    "isin", "quantite", "cours", "devise", "lieu_execution", "montant_brut", "courtages",
    "commission", "frais", "montant_net", "solde_titres", "provisoire", "fichier_source",
)


def connecter(chemin: Path) -> sqlite3.Connection:
    con = sqlite3.connect(chemin)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    colonnes = {r["name"] for r in con.execute("PRAGMA table_info(tickers)")}
    if "secteur" not in colonnes:  # bases créées avant l'ajout du secteur
        con.execute("ALTER TABLE tickers ADD COLUMN secteur TEXT")
    colonnes = {r["name"] for r in con.execute("PRAGMA table_info(transactions)")}
    if "provisoire" not in colonnes:
        con.execute("ALTER TABLE transactions ADD COLUMN provisoire INTEGER NOT NULL DEFAULT 0")
    return con


def inserer(con: sqlite3.Connection, avis: dict, fichier: str) -> str:
    """Insère l'avis. Renvoie « importé », « remplacé » (un avis définitif remplace la
    saisie provisoire de même référence) ou « doublon »."""
    ligne = dict(avis, fichier_source=fichier,
                 date_execution=avis["date_execution"].strftime("%Y-%m-%d %H:%M:%S"))
    ligne = {k: float(v) if isinstance(v, Decimal) else v for k, v in ligne.items()}
    ligne["provisoire"] = int(bool(avis.get("provisoire")))
    remplace = False
    if not ligne["provisoire"]:
        remplace = con.execute("DELETE FROM transactions WHERE reference = ? AND provisoire = 1",
                               (avis["reference"],)).rowcount > 0
    cur = con.execute(
        f"INSERT OR IGNORE INTO transactions ({', '.join(CHAMPS)}) "
        f"VALUES ({', '.join(':' + c for c in CHAMPS)})",
        ligne,
    )
    if cur.rowcount != 1:
        return "doublon"
    return "remplacé" if remplace else "importé"


def appliquer(etat: dict, t) -> dict:
    """Applique une transaction à l'état d'une position (PRU moyen pondéré, frais inclus)."""
    e = etat.setdefault(t["isin"], {"libelle": t["libelle"], "qte": 0,
                                    "cout": Decimal(0), "pv": Decimal(0)})
    e["libelle"] = t["libelle"]
    q, net = t["quantite"], Decimal(str(t["montant_net"]))
    if t["sens"] == "ACHAT":
        e["cout"] += net
        e["qte"] += q
    else:
        pru = e["cout"] / e["qte"] if e["qte"] else Decimal(0)
        e["pv"] += net - pru * q
        e["cout"] -= pru * q
        e["qte"] -= q
    return e


def recalculer_valeurs(con: sqlite3.Connection) -> list[str]:
    """Reconstruit `valeurs` depuis `transactions` (PRU moyen pondéré, frais inclus).

    Renvoie la liste des écarts entre la quantité en base et le « solde négociable »
    indiqué par la banque sur les avis.
    """
    etat = {}
    soldes_banque = {}           # (isin, jour) -> solde annoncé par la banque
    quantite_fin_jour = {}   # (isin, jour) -> quantité en base après le jour
    for t in con.execute("SELECT * FROM transactions ORDER BY date_execution, id"):
        e = appliquer(etat, t)
        jour = (t["isin"], t["date_execution"][:10])
        quantite_fin_jour[jour] = e["qte"]
        if t["solde_titres"] is not None:
            soldes_banque[jour] = t["solde_titres"]

    con.execute("DELETE FROM valeurs")
    for isin, e in etat.items():
        pru = round(e["cout"] / e["qte"], 4) if e["qte"] else None
        con.execute(
            "INSERT INTO valeurs (isin, libelle, quantite, pru, montant_investi, "
            "plus_value_realisee, maj_le) VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))",
            (isin, e["libelle"], e["qte"], float(pru) if pru is not None else None,
             float(round(e["cout"], 2)) if e["qte"] else 0.0, float(round(e["pv"], 2))),
        )

    ecarts = []
    for (isin, jour), solde in sorted(soldes_banque.items()):
        en_base = quantite_fin_jour[(isin, jour)]
        if en_base != solde:
            ecarts.append(f"{etat[isin]['libelle']} ({isin}) au {jour} : {en_base} titre(s) "
                          f"en base, {solde} selon la banque, il manque des avis")
    return ecarts


def recalculer_historique(con: sqlite3.Connection) -> int:
    """Reconstruit `historique` : une ligne par jour de cotation depuis le premier achat.

    Chaque valeur est valorisée à sa dernière clôture connue à cette date (les places
    n'ont pas toutes les mêmes jours fériés) ; à défaut de cours, à son coût de revient.
    """
    transactions = con.execute("SELECT * FROM transactions ORDER BY date_execution, id").fetchall()
    if not transactions:
        return 0
    debut = transactions[0]["date_execution"][:10]
    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM cours WHERE date >= ? ORDER BY date", (debut,))]
    cours = defaultdict(dict)
    for r in con.execute("SELECT isin, date, cloture FROM cours ORDER BY date"):
        cours[r["date"]][r["isin"]] = Decimal(str(r["cloture"]))

    etat, dernier_cours, i = {}, {}, 0
    con.execute("DELETE FROM historique")
    for d in dates:
        while i < len(transactions) and transactions[i]["date_execution"][:10] <= d:
            appliquer(etat, transactions[i])
            i += 1
        dernier_cours.update(cours[d])
        investi = valorisation = pv_realisee = Decimal(0)
        for isin, e in etat.items():
            pv_realisee += e["pv"]
            if e["qte"] <= 0:
                continue
            investi += e["cout"]
            valorisation += e["qte"] * dernier_cours[isin] if isin in dernier_cours else e["cout"]
        con.execute(
            "INSERT INTO historique VALUES (?, ?, ?, ?, ?)",
            (d, float(round(investi, 2)), float(round(valorisation, 2)),
             float(round(valorisation - investi, 2)), float(round(pv_realisee, 2))),
        )
    return len(dates)
