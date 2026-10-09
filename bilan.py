"""Bilan de la semaine : visuel PNG (valorisation, tendance, top et flop).

Les chiffres sont calculés depuis `historique`, `transactions` et `cours`, injectés
dans bilan_modele.html, puis l'image est rendue par Chrome en mode headless.

Performance de la semaine = variation de la plus-value totale (latente + réalisée) :
les versements de la semaine ne comptent donc pas comme un gain.
"""

import json
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path

import db

RACINE = Path(__file__).resolve().parent
MODELE = RACINE / "bilan_modele.html"
DOSSIER = RACINE / "bilans"
LARGEUR, HAUTEUR = 1600, 900

CHROMES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
]
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]


def chrome() -> str:
    for c in CHROMES:
        chemin = shutil.which(c) or (c if Path(c).exists() else None)
        if chemin:
            return chemin
    raise RuntimeError("Chrome ou Chromium introuvable : nécessaire pour rendre le bilan en PNG")


def gain(r) -> float:
    """Plus-value totale (latente + réalisée) d'une ligne d'historique."""
    return r["plus_value_latente"] + r["plus_value_realisee"] if r else 0.0


def periode(debut: date, fin: date) -> str:
    if debut.month == fin.month:
        return f"du {debut.day} au {fin.day} {MOIS[fin.month - 1]} {fin.year}"
    return f"du {debut.day} {MOIS[debut.month - 1]} au {fin.day} {MOIS[fin.month - 1]} {fin.year}"


def dernier_cours(con, isin: str, jour: str):
    r = con.execute("SELECT cloture FROM cours WHERE isin = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                    (isin, jour)).fetchone()
    return r["cloture"] if r else None


def positions_au(con, jour: str) -> dict:
    """État des positions (quantité, coût) après les transactions du jour inclus."""
    etat = {}
    for t in con.execute("SELECT * FROM transactions WHERE substr(date_execution, 1, 10) <= ? "
                         "ORDER BY date_execution, id", (jour,)):
        db.appliquer(etat, t)
    return etat


def lignes_semaine(con, lundi: str, fin: str, veille: str | None) -> list[dict]:
    """Performance de chaque ligne détenue en fin de semaine.

    Référence : clôture de la semaine précédente ou, pour un titre acheté
    pendant la semaine, cours moyen d'achat de la semaine.
    """
    avant = positions_au(con, veille) if veille else {}
    apres = positions_au(con, fin)
    symboles = {r["isin"]: r["symbole"] for r in con.execute("SELECT isin, symbole FROM tickers")}
    res = []
    for isin, e in apres.items():
        if e["qte"] <= 0:
            continue
        fin_cours = dernier_cours(con, isin, fin)
        q_avant = avant.get(isin, {}).get("qte", 0)
        if q_avant > 0 and veille:
            depart = dernier_cours(con, isin, veille)
        else:
            achats = con.execute(
                "SELECT sum(quantite * cours) / sum(quantite) AS prix FROM transactions "
                "WHERE isin = ? AND sens = 'ACHAT' AND substr(date_execution, 1, 10) BETWEEN ? AND ?",
                (isin, lundi, fin)).fetchone()
            depart = achats["prix"]
        if not fin_cours or not depart:
            continue
        res.append({
            "isin": isin,
            "libelle": e["libelle"],
            "symbole": symboles.get(isin),
            "pct": 100 * (fin_cours / depart - 1),
            "euros": e["qte"] * (fin_cours - depart),
            "nouvelle": q_avant <= 0,
        })
    return sorted(res, key=lambda l: l["pct"], reverse=True)


def gains_hebdo(historique: list) -> list[tuple[str, float]]:
    """Gain de chaque semaine ISO : (« AAAA-Sss », variation de la plus-value totale)."""
    fins = {}
    for r in historique:
        a, s, _ = date.fromisoformat(r["date"]).isocalendar()
        fins[(a, s)] = r  # dernière ligne de chaque semaine
    res, precedente = [], None
    for (a, s), r in sorted(fins.items()):
        res.append((f"{a}-S{s:02d}", gain(r) - gain(precedente)))
        precedente = r
    return res


def donnees(con: sqlite3.Connection, reference: date) -> dict | None:
    con.row_factory = sqlite3.Row
    historique = con.execute("SELECT * FROM historique ORDER BY date").fetchall()
    if not historique:
        return None
    # Semaine de la date de référence ; à défaut de cours, la dernière semaine cotée
    lundi = reference - timedelta(days=reference.weekday())
    semaine = [r for r in historique if lundi.isoformat() <= r["date"] <= (lundi + timedelta(6)).isoformat()]
    if not semaine:
        avant = [r["date"] for r in historique if r["date"] < lundi.isoformat()]
        return donnees(con, date.fromisoformat(avant[-1])) if avant else None
    fin = semaine[-1]
    precedents = [r for r in historique if r["date"] < lundi.isoformat()]
    veille = precedents[-1] if precedents else None

    flux = con.execute(
        "SELECT coalesce(sum(CASE sens WHEN 'ACHAT' THEN montant_net ELSE -montant_net END), 0) "
        "FROM transactions WHERE substr(date_execution, 1, 10) BETWEEN ? AND ?",
        (lundi.isoformat(), fin["date"])).fetchone()[0]
    gain_semaine = gain(fin) - gain(veille)
    base = (veille["valorisation"] if veille else 0) + max(flux, 0)

    jours, precedent = [], veille
    for r in semaine:
        jours.append({"jour": JOURS[date.fromisoformat(r["date"]).weekday()], "date": r["date"],
                      "gain": gain(r) - gain(precedent)})
        precedent = r

    lignes = lignes_semaine(con, lundi.isoformat(), fin["date"], veille["date"] if veille else None)
    hebdo = [g for s, g in gains_hebdo(historique) if s <= f"{lundi.isocalendar()[0]}-S{lundi.isocalendar()[1]:02d}"]
    serie = 0
    for g in reversed(hebdo):
        if (g > 0) != (gain_semaine > 0) or g == 0:
            break
        serie += 1
    total = gain(fin)
    # Portefeuille témoin en ETF S&P 500 (mêmes montants) : absent tant que ses cours manquent
    etf_total = fin["plus_value_etf"]
    etf_veille = veille["plus_value_etf"] if veille else 0.0
    etf_semaine = etf_total - etf_veille if etf_total is not None and etf_veille is not None else None
    base_etf = ((veille["valorisation_etf"] or 0) if veille else 0) + max(flux, 0)

    a, s, _ = lundi.isocalendar()
    return {
        "semaine": f"S{s} {a}",
        "fichier": f"bilan_{a}-S{s:02d}",
        "periode": periode(lundi, date.fromisoformat(fin["date"])),
        "date_fin": fin["date"],
        "valorisation": fin["valorisation"],
        "investi": fin["montant_investi"],
        "semaine_euros": gain_semaine,
        "semaine_pct": 100 * gain_semaine / base if base > 0 else None,
        "versements": flux,
        "total_euros": total,
        "total_pct": 100 * total / fin["montant_investi"] if fin["montant_investi"] else None,
        "etf_total_euros": etf_total,
        "etf_total_pct": 100 * etf_total / fin["montant_investi"]
                         if etf_total is not None and fin["montant_investi"] else None,
        "etf_semaine_euros": etf_semaine,
        "etf_semaine_pct": 100 * etf_semaine / base_etf if etf_semaine is not None and base_etf > 0 else None,
        "record": total > 0 and all(gain(r) < total for r in historique if r["date"] < fin["date"]),
        "serie": serie,
        "jours": jours,
        "lignes": lignes,
        "historique": [{"date": r["date"], "valorisation": r["valorisation"],
                        "investi": r["montant_investi"],
                        "etf": r["valorisation_etf"]} for r in historique if r["date"] <= fin["date"]],
        "lundi": lundi.isoformat(),
        "exemple": False,
    }


def rendre(d: dict, sortie: Path) -> Path:
    """Injecte les données dans le modèle et capture la page en PNG (2× pour la netteté)."""
    charge = json.dumps(d, ensure_ascii=False, default=float).replace("</", "<\\/")
    html = MODELE.read_text(encoding="utf-8").replace("/*__DONNEES__*/null", charge)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "bilan.html"
        page.write_text(html, encoding="utf-8")
        res = subprocess.run(
            [chrome(), "--headless=new", "--hide-scrollbars", "--force-device-scale-factor=2",
             f"--window-size={LARGEUR},{HAUTEUR}", "--virtual-time-budget=3000",
             f"--screenshot={sortie}", page.as_uri()],
            capture_output=True, text=True, timeout=60, check=False)
    if res.returncode != 0 or not sortie.exists():
        raise RuntimeError(f"capture Chrome échouée : {res.stderr.strip()[-300:]}")
    return sortie


def generer(base: Path, reference: date | None = None) -> tuple[Path, dict]:
    con = sqlite3.connect(base)
    d = donnees(con, reference or date.today())
    if not d:
        raise RuntimeError("aucun historique : rien à résumer")
    return rendre(d, DOSSIER / f"{d['fichier']}.png"), d
