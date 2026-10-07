#!/usr/bin/env python3
"""Transforme de vrais avis d'opéré en fixtures de test anonymes.

Usage :
  python3 tests/anonymiser.py tests/fixtures/AvisOperation_*.pdf
  python3 tests/anonymiser.py tests/fixtures/Ordre_*.txt

Un avis PDF devient `tests/fixtures/<même nom>.txt` : le texte de `pdftotext -layout`
où le titulaire, l'adresse, l'agence, les numéros de compte, les codes-barres et la
référence d'ordre sont remplacés. Les montants, quantités, dates et valeurs sont
conservés, et la largeur de chaque champ aussi, car le parseur lit les montants
par position de colonne. Un ordre .txt est réécrit sur place (seule sa référence change).

Aucune donnée réelle n'est écrite ici : les zones sont repérées par leur place
dans la mise en page. Le script vérifie à la fin que l'avis anonymisé se lit
exactement comme l'original, à la référence près.
"""

import re
import sys
import zlib
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

import parser_avis  # noqa: E402

ADRESSE = ["MR JEAN DUPONT", "1 RUE DE LA PAIX", "75000 PARIS"]
AGENCE = "AGENCE EXEMPLE"
# Comptes fictifs, dans l'ordre où ils apparaissent : titres puis espèces
COMPTES = ["12345 00000 00000000001", "12345 00000 00000000002"]
RE_COMPTE = re.compile(r"\b\d{5} \d{5} \d{11}\b")
RE_REFERENCE = re.compile(r"(Référence :\s+)(\S+)")
RE_LOT = re.compile(r"\bP\d{3,5}\b")  # code de lot d'impression, en tête et en pied de page


def ajuster(texte: str, largeur: int) -> str:
    """Même largeur que le texte remplacé, pour ne décaler aucune colonne."""
    return texte[:largeur].ljust(largeur)


def fausse_reference(reference: str, isin: str, date: str) -> str:
    """Référence fictive, stable pour un même ISIN et une même date.

    Un ordre .txt et l'avis PDF correspondant reçoivent donc la même, comme dans la réalité.
    Format conservé : « G260915U2766 » (bourse étrangère) ou 8 chiffres (Paris).
    """
    n = zlib.crc32(f"{isin} {date}".encode())
    m = re.fullmatch(r"([A-Z]\d{6}[A-Z])(\d+)", reference)
    if m:
        chiffres = len(m.group(2))
        return m.group(1) + str(n % 10 ** chiffres).zfill(chiffres)
    return str(n % 10 ** len(reference)).zfill(len(reference))


def remplacer_cellule(ligne: str, ancien: str, nouveau: str) -> str:
    debut = ligne.index(ancien)
    return ligne[:debut] + ajuster(nouveau, len(ancien)) + ligne[debut + len(ancien):]


def anonymiser_avis(texte: str) -> str:
    original = parser_avis.parse_texte(texte)
    lignes = texte.splitlines()

    # En-tête : seules les lignes « OPERATION DE BOURSE » et vides restent (codes-barres retirés)
    debut_lettre = next(i for i, l in enumerate(lignes) if l.startswith("Nous avons le plaisir"))
    for i in range(debut_lettre):
        if lignes[i].strip() != "OPERATION DE BOURSE":
            lignes[i] = ""

    # Bloc d'adresse : lignes non vides entre la formule d'introduction et « Nom de l'agence »
    fin_intro = next(i for i, l in enumerate(lignes) if l.strip() == "vous nous avez confiée.")
    agence = next(i for i, l in enumerate(lignes) if "Nom de l'agence" in l)
    adresse = [i for i in range(fin_intro + 1, agence) if lignes[i].strip()]
    if len(adresse) > len(ADRESSE):
        raise ValueError(f"bloc d'adresse inattendu ({len(adresse)} lignes)")
    for i, fausse in zip(adresse, ADRESSE):
        retrait = len(lignes[i]) - len(lignes[i].lstrip())
        lignes[i] = " " * retrait + ajuster(fausse, len(lignes[i].strip())).rstrip()

    # Ligne agence + comptes : première ligne contenant un numéro de compte
    i = next(i for i in range(agence, len(lignes)) if RE_COMPTE.search(lignes[i]))
    nom_agence = re.match(r"\s*(\S+(?: \S+)*)", lignes[i]).group(1)
    if RE_COMPTE.match(nom_agence):
        raise ValueError("nom d'agence introuvable")
    lignes[i] = remplacer_cellule(lignes[i], nom_agence, AGENCE)

    texte = "\n".join(lignes) + "\n"
    comptes = iter(COMPTES)
    texte = RE_COMPTE.sub(lambda m: next(comptes), texte)
    texte = RE_LOT.sub(lambda m: "P" + "0" * (len(m.group()) - 1), texte)
    reference = fausse_reference(original["reference"], original["isin"],
                                 original["date_execution"].strftime("%Y-%m-%d"))
    texte = RE_REFERENCE.sub(lambda m: m.group(1) + reference, texte, count=1)

    anonyme = parser_avis.parse_texte(texte)
    if dict(anonyme, reference=None) != dict(original, reference=None):
        raise ValueError("l'avis anonymisé ne se lit plus comme l'original")
    return texte


def anonymiser_ordre(texte: str) -> str:
    original = parser_avis.parse_ordre(texte)
    reference = fausse_reference(original["reference"], original["isin"],
                                 original["date_execution"].strftime("%Y-%m-%d"))
    texte = texte.replace(original["reference"], reference)
    if dict(parser_avis.parse_ordre(texte), reference=None) != dict(original, reference=None):
        raise ValueError("l'ordre anonymisé ne se lit plus comme l'original")
    return texte


def main(fichiers: list[str]) -> None:
    for nom in fichiers:
        source = Path(nom).resolve()
        if source.suffix.lower() == ".pdf":
            cible = source.with_suffix(".txt")
            cible.write_text(anonymiser_avis(parser_avis.extraire_texte(source)), encoding="utf-8")
        else:
            cible = source
            cible.write_text(anonymiser_ordre(source.read_text(encoding="utf-8")), encoding="utf-8")
        print(f"{source.name} → {cible.relative_to(RACINE)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
