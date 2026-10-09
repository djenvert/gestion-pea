"""Portefeuille témoin en ETF S&P 500 : réplication des achats et ventes, même montant net."""

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import exemple  # noqa: E402

D = Decimal


def transaction(sens, net, jour="2026-01-05"):
    return {"sens": sens, "montant_net": net, "libelle": "TEST", "date_execution": f"{jour} 10:00:00"}


class TestAppliquerEtf(unittest.TestCase):
    def setUp(self):
        self.etat = {"parts": D(0), "apports": D(0), "frais": D(0)}

    def test_achat_commission_comprise(self):
        # 1 005 € nets = 1 000 € de brut + 5 € de commission (0,50 %), soit 20 parts à 50 €
        self.assertIsNone(db.appliquer_etf(self.etat, transaction("ACHAT", 1005), D(50)))
        self.assertAlmostEqual(float(self.etat["parts"]), 20, places=9)
        self.assertEqual(self.etat["apports"], D(1005))
        self.assertAlmostEqual(float(self.etat["frais"]), 5, places=9)

    def test_vente_encaisse_le_meme_net(self):
        db.appliquer_etf(self.etat, transaction("ACHAT", 2010), D(50))  # 40 parts
        # 995 € nets = 1 000 € de brut - 5 € de commission, soit 20 parts à 50 €
        self.assertIsNone(db.appliquer_etf(self.etat, transaction("VENTE", 995), D(50)))
        self.assertAlmostEqual(float(self.etat["parts"]), 20, places=9)
        self.assertAlmostEqual(float(self.etat["apports"]), 2010 - 995, places=9)
        self.assertAlmostEqual(float(self.etat["frais"]), 10 + 5, places=9)

    def test_vente_superieure_au_temoin(self):
        db.appliquer_etf(self.etat, transaction("ACHAT", 1005), D(50))  # 20 parts, 1 000 € de brut
        alerte = db.appliquer_etf(self.etat, transaction("VENTE", 5000), D(50))
        self.assertIn("tout est vendu", alerte)
        self.assertEqual(self.etat["parts"], 0)
        self.assertAlmostEqual(float(self.etat["apports"]), 1005 - 995, places=9)


class TestHistoriqueEtf(unittest.TestCase):
    def setUp(self):
        self.con = exemple.base()

    def tearDown(self):
        self.con.close()

    def test_premier_jour(self):
        # Le jour du premier achat, le témoin a seulement payé sa commission
        h = self.con.execute("SELECT * FROM historique ORDER BY date LIMIT 1").fetchone()
        nets = self.con.execute("SELECT sum(montant_net) FROM transactions "
                                "WHERE substr(date_execution, 1, 10) = ?", (h["date"],)).fetchone()[0]
        self.assertAlmostEqual(h["valorisation_etf"], nets / 1.005, places=1)
        self.assertAlmostEqual(h["plus_value_etf"], -(nets - nets / 1.005), places=1)

    def test_toujours_renseigne(self):
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM historique WHERE valorisation_etf IS NULL").fetchone()[0], 0)

    def test_bilan(self):
        import bilan
        from datetime import date
        d = bilan.donnees(self.con, date(2026, 9, 25))
        fin = self.con.execute("SELECT * FROM historique WHERE date = ?", (d["date_fin"],)).fetchone()
        self.assertEqual(d["etf_total_euros"], fin["plus_value_etf"])
        self.assertIsNotNone(d["etf_semaine_pct"])
        self.assertTrue(all(r["etf"] is not None for r in d["historique"]))

    def test_sans_cours_etf(self):
        self.con.execute("DELETE FROM cours WHERE isin = ?", (db.ETF["isin"],))
        db.recalculer_historique(self.con)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM historique WHERE valorisation_etf IS NOT NULL").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
