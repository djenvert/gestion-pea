"""Lecture des avis d'opéré de la banque (PDF) pour un compte PEA.

Deux mises en page connues :
  - ACHAT/VENTE AU COMPTANT (Euronext Paris)
  - ACHAT/VENTE EN BOURSE ETRANGERE
Le texte est extrait avec `pdftotext -layout`, puis les montants sont lus
colonne par colonne sous leurs en-têtes (une cellule vide vaut 0).
"""

import re
import shutil
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path

PDFTOTEXT = shutil.which("pdftotext") or "/opt/homebrew/bin/pdftotext"

# Montant au format français suivi d'une devise : "1 330,60 EUR"
RE_MONTANT = re.compile(r"-?\d{1,3}(?: \d{3})*,\d+ [A-Z]{3}")
# Cellules d'une ligne en mode layout : séparées par au moins deux espaces
RE_CELLULE = re.compile(r"\S+(?: \S+)*")

# Libellés d'en-tête → champ normalisé
COLONNES = {
    "Montant brut": "brut",
    "Montant transaction brut": "brut",
    "Courtages (1)": "courtages",
    "Commission": "commission",
    "Frais (1)": "frais",
    "Frais divers": "frais",
    "Montant net au débit de votre compte": "net",
    "Montant net au crédit de votre compte": "net",
}

TYPES = {
    "ACHAT AU COMPTANT": ("ACHAT", "Achat au comptant"),
    "ACHAT EN BOURSE ETRANGERE": ("ACHAT", "Achat en bourse étrangère"),
    "VENTE AU COMPTANT": ("VENTE", "Vente au comptant"),
    "VENTE EN BOURSE ETRANGERE": ("VENTE", "Vente en bourse étrangère"),
}


class AvisInvalide(Exception):
    """Le PDF n'est pas un avis reconnu ou ses montants sont incohérents."""


def nombre(texte: str) -> Decimal:
    """'1 330,60 EUR' → Decimal('1330.60')."""
    texte = re.sub(r"\s*[A-Z]{3}$", "", texte.strip())
    return Decimal(texte.replace(" ", "").replace(",", "."))


def extraire_texte(pdf: Path) -> str:
    res = subprocess.run(
        [PDFTOTEXT, "-layout", str(pdf), "-"],
        capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        raise AvisInvalide(f"pdftotext a échoué : {res.stderr.strip()}")
    return res.stdout


def _chercher(motif: str, texte: str, obligatoire: bool = True):
    m = re.search(motif, texte, re.MULTILINE)
    if not m:
        if obligatoire:
            raise AvisInvalide(f"motif introuvable : {motif}")
        return None
    return m.group(1).strip()


def _montants(lignes: list[str]) -> dict[str, Decimal]:
    """Associe chaque montant à l'en-tête de colonne le plus proche horizontalement."""
    montants: dict[str, Decimal] = {}
    for i, ligne in enumerate(lignes):
        entetes = [
            (m.start(), m.end(), COLONNES[m.group()])
            for m in RE_CELLULE.finditer(ligne)
            if m.group() in COLONNES
        ]
        if not entetes:
            continue
        # Toutes les cellules de la ligne d'en-tête, pour ne pas rattacher un
        # montant à une colonne voisine non suivie (ex. « Intérêts »).
        cellules = [(m.start(), m.end(), COLONNES.get(m.group())) for m in RE_CELLULE.finditer(ligne)]
        suivante = next((l for l in lignes[i + 1:] if l.strip()), "")
        for m in RE_MONTANT.finditer(suivante):
            centre = (m.start() + m.end()) / 2
            debut, fin, champ = min(
                cellules,
                key=lambda c: 0 if c[0] <= centre <= c[1] else min(abs(centre - c[0]), abs(centre - c[1])),
            )
            if champ:
                montants[champ] = nombre(m.group())
    return montants


def parse(pdf: Path) -> dict:
    return parse_texte(extraire_texte(pdf))


def parse_texte(texte: str) -> dict:
    """Avis déjà converti par `pdftotext -layout` (les fixtures de test sont stockées ainsi)."""
    lignes = texte.splitlines()

    entete = next((t for t in TYPES if re.search(rf"^{t}\s*$", texte, re.MULTILINE)), None)
    if not entete:
        raise AvisInvalide("type d'opération non reconnu")
    sens, type_transaction = TYPES[entete]

    m = re.search(
        r"^\s*(\d{2}/\d{2}/\d{4})\s+(\d{1,3}(?: \d{3})*)\s{2,}(.+?)\s{2,}Référence :\s+(\S+)",
        texte, re.MULTILINE,
    )
    if not m:
        raise AvisInvalide("ligne date / quantité / valeur introuvable")
    date, quantite, libelle, reference = m.groups()
    heure = _chercher(r"^\s*(\d{2}:\d{2}:\d{2})\b", texte[m.end():])
    date_execution = datetime.strptime(f"{date} {heure}", "%d/%m/%Y %H:%M:%S")

    cours_txt = _chercher(r"Cours exécuté :\s+(.+?)\s*$", texte)
    solde = _chercher(r"^\s*(\d{1,3}(?: \d{3})*) TITRES\s*$", texte, obligatoire=False)

    montants = _montants(lignes)
    for champ in ("brut", "commission", "net"):
        if champ not in montants:
            raise AvisInvalide(f"montant « {champ} » introuvable")

    avis = {
        "reference": reference,
        "date_execution": date_execution,
        "sens": sens,
        "type_transaction": type_transaction,
        "type_ordre": _chercher(r"Type d'ordre :\s+(.+?)\s*$", texte, obligatoire=False),
        "libelle": libelle.strip(),
        "isin": _chercher(r"Code ISIN :\s+([A-Z]{2}[A-Z0-9]{9}\d)\b", texte),
        "quantite": int(quantite.replace(" ", "")),
        "cours": nombre(cours_txt),
        "devise": cours_txt.split()[-1],
        "lieu_execution": _chercher(r"Lieu d'exécution :\s+(.+?)\s*$", texte, obligatoire=False),
        "montant_brut": montants["brut"],
        "courtages": montants.get("courtages", Decimal(0)),
        "commission": montants["commission"],
        "frais": montants.get("frais", Decimal(0)),
        "montant_net": montants["net"],
        "solde_titres": int(solde.replace(" ", "")) if solde else None,
        "provisoire": False,
    }
    verifier(avis)
    return avis


def parse_ordre(texte: str) -> dict:
    """Copier-coller d'un ordre exécuté (« Suivi des ordres » du site de la banque), en attendant l'avis.

    La commission n'y figure pas : la transaction est marquée provisoire et sera remplacée
    par l'avis d'opéré portant la même référence (le n° d'ordre).
    """
    texte = texte.replace("\u00a0", " ").replace("\u202f", " ")
    m = re.search(r"^\s*(.+?)\s{2,}([A-Z]{2}[A-Z0-9]{9}\d)\b", texte, re.MULTILINE)
    if not m:
        raise AvisInvalide("libellé / ISIN introuvables dans l'ordre")
    libelle, isin = m.group(1).strip(), m.group(2)
    etat = _chercher(r"Etat\s*:\s*(\S+)", texte)
    if etat != "Exécuté":
        raise AvisInvalide(f"ordre non exécuté (état : {etat})")
    date = _chercher(r"Date d'exécution\s+(\d{2}/\d{2}/\d{4})", texte)
    cours_txt = _chercher(r"Cours d'exécution\s+(\d[\d ]*(?:,\d+)?\s+[A-Z]{3})", texte)
    frais = re.findall(r"\d{1,3}(?: \d{3})*,\d+ ?[A-Z]{3}",
                       _chercher(r"^\s*Frais\b(.*)$", texte))
    brut = nombre(_chercher(r"Montant brut\s+(\d[\d ]*,\d+)", texte))
    net = nombre(_chercher(r"Montant net\s+(\d[\d ]*,\d+)", texte))
    sens = "ACHAT" if net >= brut else "VENTE"
    avis = {
        "reference": _chercher(r"Ordre N°\s*(\S+)", texte),
        "date_execution": datetime.strptime(date, "%d/%m/%Y"),
        "sens": sens,
        "type_transaction": f"{sens.capitalize()} (suivi des ordres, provisoire)",
        "type_ordre": _chercher(r"Type de l'ordre[ \t]+(\S.*?)\s*$", texte, obligatoire=False),
        "libelle": libelle,
        "isin": isin,
        "quantite": int(_chercher(r"Quantité exécutée\s+(\d[\d ]*)", texte).replace(" ", "")),
        "cours": nombre(cours_txt),
        "devise": cours_txt.split()[-1],
        "lieu_execution": None,
        "montant_brut": brut,
        "courtages": Decimal(0),
        "commission": Decimal(0),   # inconnue tant que l'avis n'est pas arrivé
        "frais": nombre(frais[-1]) if frais else Decimal(0),
        "montant_net": net,
        "solde_titres": None,
        "provisoire": True,
    }
    verifier(avis)
    return avis


def lire(fichier: Path) -> dict:
    """Avis PDF, ou ordre copié-collé depuis le site dans un fichier .txt."""
    if fichier.suffix.lower() == ".txt":
        return parse_ordre(fichier.read_text(encoding="utf-8"))
    return parse(fichier)


def verifier(avis: dict) -> None:
    couts = avis["courtages"] + avis["commission"] + avis["frais"]
    attendu = avis["montant_brut"] + couts if avis["sens"] == "ACHAT" else avis["montant_brut"] - couts
    if attendu != avis["montant_net"]:
        raise AvisInvalide(
            f"montants incohérents : brut {avis['montant_brut']} ± frais {couts} = {attendu}, "
            f"net lu {avis['montant_net']}"
        )
    ecart = abs(avis["quantite"] * avis["cours"] - avis["montant_brut"])
    if ecart > Decimal("0.01"):
        raise AvisInvalide(
            f"quantité × cours ({avis['quantite']} × {avis['cours']}) ≠ brut {avis['montant_brut']}"
        )
