"""Garde-fou avant publication : aucune donnée personnelle dans les fichiers du dépôt.

Deux niveaux :
  - contrôles structurels sur les fixtures, sans aucune donnée réelle dans ce fichier ;
  - recherche des chaînes listées dans tests/interdits.txt (une par ligne : nom, rue,
    numéros de compte…). Ce fichier est exclu par .gitignore et n'existe que sur la
    machine du titulaire : sans lui, ce test est sauté.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "tests"))

import anonymiser  # noqa: E402

FIXTURES = RACINE / "tests" / "fixtures"
INTERDITS = RACINE / "tests" / "interdits.txt"


def fichiers_publies() -> list[Path]:
    """Fichiers suivis par git ou qu'un `git add .` ajouterait (le .gitignore s'applique)."""
    res = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=RACINE, capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        return sorted(FIXTURES.glob("*.txt"))
    return [RACINE / f for f in res.stdout.split("\0") if f and (RACINE / f).is_file()]


def compact(texte: str) -> str:
    """Sans espaces ni casse : « 30003 02840 » et « 3000302840 » se comparent pareil."""
    return re.sub(r"\s+", "", texte).casefold()


class TestAnonymat(unittest.TestCase):
    def test_fixtures_anonymisees(self):
        for f in sorted(FIXTURES.glob("AvisOperation_*.txt")):
            with self.subTest(f.name):
                texte = f.read_text(encoding="utf-8")
                self.assertEqual(set(anonymiser.RE_COMPTE.findall(texte)), set(anonymiser.COMPTES))
                for ligne in anonymiser.ADRESSE + [anonymiser.AGENCE]:
                    self.assertIn(ligne, texte)
                # En-tête : plus aucun code-barres avant la formule d'introduction
                entete = texte.split("Nous avons le plaisir")[0]
                self.assertEqual(entete.split(), ["OPERATION", "DE", "BOURSE"])
                self.assertTrue(all(set(lot[1:]) == {"0"} for lot in anonymiser.RE_LOT.findall(texte)))

    def test_pas_de_pdf_publie(self):
        self.assertEqual([f for f in fichiers_publies() if f.suffix.lower() == ".pdf"], [])

    def test_aucune_donnee_interdite(self):
        if not INTERDITS.exists():
            self.skipTest("tests/interdits.txt absent (normal hors de la machine du titulaire)")
        interdits = [l.strip() for l in INTERDITS.read_text(encoding="utf-8").splitlines()
                     if l.strip() and not l.startswith("#")]
        self.assertTrue(interdits, "tests/interdits.txt est vide")
        for f in fichiers_publies():
            try:
                contenu = compact(f.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                contenu = compact(f.read_bytes().decode("latin-1"))
            for n, chaine in enumerate(interdits, 1):
                # Le numéro de ligne seulement : le message d'échec ne doit pas répéter la donnée
                self.assertFalse(compact(chaine) in contenu,
                                 f"{f.relative_to(RACINE)} contient la ligne {n} de tests/interdits.txt")


if __name__ == "__main__":
    unittest.main()
