import sys
import unittest
from datetime import date
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

import bilan  # noqa: E402
import exemple  # noqa: E402


class TestBilan(unittest.TestCase):
    """Calculs du bilan hebdomadaire sur le portefeuille fictif d'exemple.py."""

    @classmethod
    def setUpClass(cls):
        cls.con = exemple.base()
        cls.d = bilan.donnees(cls.con, date(2026, 9, 25))

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def test_semaine_iso(self):
        self.assertEqual((self.d["semaine"], self.d["fichier"]), ("S39 2026", "bilan_2026-S39"))
        self.assertEqual(self.d["periode"], "du 21 au 25 septembre 2026")
        self.assertEqual(self.d["date_fin"], "2026-09-25")

    def test_jours_somment_la_semaine(self):
        self.assertEqual([j["jour"] for j in self.d["jours"]], ["lun.", "mar.", "mer.", "jeu.", "ven."])
        self.assertAlmostEqual(sum(j["gain"] for j in self.d["jours"]), self.d["semaine_euros"], places=6)

    def test_versements_hors_performance(self):
        # Semaine du 7 au 11 septembre : achat Air Liquide, la performance n'inclut pas le versement
        d = bilan.donnees(self.con, date(2026, 9, 9))
        self.assertGreater(d["versements"], 1000)
        self.assertLess(abs(d["semaine_euros"]), d["versements"])
        h = {r["date"]: r for r in self.con.execute("SELECT * FROM historique")}
        attendu = bilan.gain(h["2026-09-11"]) - bilan.gain(h["2026-09-04"])
        self.assertAlmostEqual(d["semaine_euros"], attendu, places=6)

    def test_top_et_flop(self):
        lignes = self.d["lignes"]
        self.assertEqual(len(lignes), 11)
        self.assertEqual(lignes, sorted(lignes, key=lambda l: l["pct"], reverse=True))
        top = lignes[0]
        debut = bilan.dernier_cours(self.con, top["isin"], "2026-09-18")
        fin = bilan.dernier_cours(self.con, top["isin"], "2026-09-25")
        self.assertAlmostEqual(top["pct"], 100 * (fin / debut - 1), places=6)

    def test_ligne_entree_dans_la_semaine(self):
        # Allianz renforcé le 29/09 mais déjà détenu ; semaine 40 incomplète (jusqu'au 30/09)
        d = bilan.donnees(self.con, date(2026, 9, 30))
        self.assertEqual(d["date_fin"], "2026-09-30")
        self.assertFalse(any(l["nouvelle"] for l in d["lignes"]))

    def test_semaine_sans_cours(self):
        # Une semaine postérieure aux données retombe sur la dernière semaine cotée
        d = bilan.donnees(self.con, date(2026, 10, 14))
        self.assertEqual(d["semaine"], "S40 2026")


if __name__ == "__main__":
    unittest.main()
