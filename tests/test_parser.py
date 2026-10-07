import sqlite3
import sys
import unittest
from decimal import Decimal
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

import db  # noqa: E402
import parser_avis  # noqa: E402
import tableau  # noqa: E402

FIXTURES = RACINE / "tests" / "fixtures"
D = Decimal


def fixture(isin: str) -> Path:
    """Avis anonymisé (texte de `pdftotext -layout`), produit par tests/anonymiser.py."""
    return next(FIXTURES.glob(f"AvisOperation_{isin}_*.txt"))


def avis(isin: str) -> dict:
    return parser_avis.parse_texte(fixture(isin).read_text(encoding="utf-8"))


class TestParser(unittest.TestCase):
    def test_achat_au_comptant(self):
        a = avis("FR0000121667")
        self.assertEqual(a["reference"], "32551981")
        self.assertEqual(a["type_transaction"], "Achat au comptant")
        self.assertEqual(a["type_ordre"], "au marché")
        self.assertEqual(a["libelle"], "ESSILORLUXOTTICA")
        self.assertEqual(str(a["date_execution"]), "2026-09-15 15:54:23")
        self.assertEqual((a["quantite"], a["cours"]), (9, D("145.875")))
        self.assertEqual((a["montant_brut"], a["courtages"], a["commission"], a["frais"], a["montant_net"]),
                         (D("1312.88"), 0, D("6.59"), D("5.25"), D("1324.72")))
        self.assertEqual(a["solde_titres"], 9)

    def test_achat_au_comptant_sans_frais(self):
        a = avis("NL0010273215")
        self.assertEqual(a["cours"], D("1588.8"))
        self.assertEqual(a["frais"], 0)
        self.assertEqual(a["commission"], D("7.94"))
        self.assertEqual(a["montant_net"], D("1596.74"))
        self.assertEqual(a["solde_titres"], 5)

    def test_achat_bourse_etrangere(self):
        a = avis("DE000ENER6Y0")
        self.assertEqual(a["reference"], "G260915U2766")
        self.assertEqual(a["type_transaction"], "Achat en bourse étrangère")
        self.assertIsNone(a["type_ordre"])
        self.assertEqual(a["lieu_execution"], "IBIS")
        self.assertEqual((a["courtages"], a["commission"], a["frais"]), (D("0.13"), D("6.52"), 0))
        self.assertEqual(a["montant_net"], D("1337.25"))
        self.assertIsNone(a["solde_titres"])

    def test_ferrari_courtages(self):
        a = avis("NL0011585146")
        self.assertEqual((a["quantite"], a["courtages"], a["commission"]), (3, D("0.10"), D("5.11")))
        self.assertEqual(a["lieu_execution"], "EURONEXT MILAN")
        self.assertEqual(a["montant_net"], D("1048.16"))

    def test_lieu_multi_mots(self):
        self.assertEqual(avis("ES0113211835")["lieu_execution"], "CRIEE MARCHE ELECTRONIQUE")

    def test_incoherence_detectee(self):
        a = avis("IT0003128367")
        a["commission"] += 1
        with self.assertRaises(parser_avis.AvisInvalide):
            parser_avis.verifier(a)


class TestOrdreProvisoire(unittest.TestCase):
    ORDRE = FIXTURES / "Ordre_DE0008430026_20260921.txt"

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(db.SCHEMA)

    def test_lecture(self):
        a = parser_avis.lire(self.ORDRE)
        self.assertEqual((a["reference"], a["isin"], a["libelle"]), ("G260921U5177", "DE0008430026", "MUENCH.RUECKVERS. NA O.N."))
        self.assertEqual((a["sens"], a["quantite"], a["cours"]), ("ACHAT", 4, D("509.4")))
        self.assertEqual((a["montant_brut"], a["frais"], a["commission"], a["montant_net"]), (D("2037.60"), D("0.20"), 0, D("2037.80")))
        self.assertTrue(a["provisoire"])
        self.assertIsNone(a["type_ordre"])

    def test_espaces_insecables(self):
        texte = self.ORDRE.read_text().replace("2 037", "2\u202f037").replace("    ", "\u00a0\u00a0 ")
        self.assertEqual(parser_avis.parse_ordre(texte)["montant_net"], D("2037.80"))

    def test_ordre_non_execute(self):
        with self.assertRaises(parser_avis.AvisInvalide):
            parser_avis.parse_ordre(self.ORDRE.read_text().replace("Exécuté", "Annulé"))

    def test_avis_remplace_provisoire(self):
        provisoire = parser_avis.lire(self.ORDRE)
        self.assertEqual(db.inserer(self.con, provisoire, "ordre.txt"), "importé")
        self.assertEqual(db.inserer(self.con, provisoire, "ordre.txt"), "doublon")
        definitif = dict(provisoire, provisoire=False, commission=D("9.98"), montant_net=D("2047.78"))
        self.assertEqual(db.inserer(self.con, definitif, "avis.pdf"), "remplacé")
        t = self.con.execute("SELECT * FROM transactions").fetchall()
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["provisoire"], t[0]["montant_net"]), (0, 2047.78))
        # Un ordre recollé après coup ne revient pas écraser l'avis
        self.assertEqual(db.inserer(self.con, provisoire, "ordre.txt"), "doublon")


class TestValeurs(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(db.SCHEMA)
        for f in sorted(FIXTURES.glob("AvisOperation_*.txt")):
            db.inserer(self.con, parser_avis.parse_texte(f.read_text(encoding="utf-8")), f.name)

    def valeur(self, isin):
        return self.con.execute("SELECT * FROM valeurs WHERE isin = ?", (isin,)).fetchone()

    def test_pru_frais_inclus(self):
        db.recalculer_valeurs(self.con)
        self.assertAlmostEqual(self.valeur("FR0000121667")["pru"], 147.1911, places=4)
        self.assertAlmostEqual(self.valeur("DE000ENER6Y0")["pru"], 133.725, places=4)
        self.assertAlmostEqual(self.valeur("NL0011585146")["pru"], 349.3867, places=4)

    def test_doublon_ignore(self):
        self.assertEqual(db.inserer(self.con, avis("FR0000121667"), fixture("FR0000121667").name), "doublon")

    def test_rapprochement_asml(self):
        ecarts = db.recalculer_valeurs(self.con)
        self.assertEqual(len(ecarts), 1)
        self.assertIn("NL0010273215", ecarts[0])

    def test_situation_apres_chaque_transaction(self):
        a = avis("FR0000121667")
        a.update(reference="A2", quantite=1, montant_net=D("150"))
        a["date_execution"] = a["date_execution"].replace(day=20)
        db.inserer(self.con, a, "achat2.pdf")
        sit = tableau.situations(self.con)
        self.assertEqual((sit["32551981"]["qte_avant"], sit["32551981"]["qte_apres"]), (0, 9))
        self.assertEqual((sit["A2"]["qte_avant"], sit["A2"]["qte_apres"]), (9, 10))
        self.assertAlmostEqual(sit["A2"]["pru_apres"], (1324.72 + 150) / 10, places=4)

    def test_vente_garde_le_pru(self):
        a = avis("FR0000121667")
        a.update(reference="V1", sens="VENTE", quantite=3, montant_net=D("500"))
        a["date_execution"] = a["date_execution"].replace(day=20)
        db.inserer(self.con, a, "vente.pdf")
        db.recalculer_valeurs(self.con)
        v = self.valeur("FR0000121667")
        self.assertEqual(v["quantite"], 6)
        self.assertAlmostEqual(v["pru"], 147.1911, places=4)
        self.assertAlmostEqual(v["plus_value_realisee"], round(500 - 1324.72 / 3, 2), places=2)


class TestHistorique(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(db.SCHEMA)
        for isin in ("FR0000121667", "NL0010273215"):  # achats du 15/09 et du 29/09
            db.inserer(self.con, avis(isin), fixture(isin).name)
        self.con.executemany("INSERT INTO cours (isin, date, cloture) VALUES (?, ?, ?)", [
            ("FR0000121667", "2026-09-15", 150.0),
            ("FR0000121667", "2026-09-29", 140.0),
            ("NL0010273215", "2026-09-29", 1600.0),
            ("NL0010273215", "2026-09-30", 1700.0),  # pas de cours Essilor ce jour-là
        ])

    def test_valorisation_quotidienne(self):
        self.assertEqual(db.recalculer_historique(self.con), 3)
        h = {r["date"]: r for r in self.con.execute("SELECT * FROM historique")}
        self.assertEqual(h["2026-09-15"]["montant_investi"], 1324.72)
        self.assertEqual(h["2026-09-15"]["valorisation"], 9 * 150.0)
        self.assertEqual(h["2026-09-29"]["valorisation"], 9 * 140.0 + 1600.0)
        # Essilor reprend sa dernière clôture connue (140) le 30/09
        self.assertEqual(h["2026-09-30"]["valorisation"], 9 * 140.0 + 1700.0)
        self.assertAlmostEqual(h["2026-09-30"]["plus_value_latente"], 2960.0 - 1324.72 - 1596.74, places=2)


if __name__ == "__main__":
    unittest.main()
