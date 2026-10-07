# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Suivi d'un PEA : les avis d'opéré PDF déposés dans `inbox/` alimentent une base SQLite (`pea.db`), les cours de clôture viennent de Yahoo Finance, et un tableau de bord HTML autonome (`tableau.html`) est régénéré automatiquement. Code, commentaires, logs et interface sont en français.

## Commandes

Python 3 avec la seule bibliothèque standard, plus `pdftotext` (poppler, `/opt/homebrew/bin/pdftotext`). Rien à installer.

```sh
python3 pea.py traiter      # traite inbox/ (PDF et .txt), archive dans outbox/AAAA-MM/ ou erreurs/
python3 pea.py recalculer   # reconstruit valeurs + historique depuis transactions, puis le tableau
python3 pea.py cours        # récupère les clôtures manquantes (réseau), puis historique + tableau
python3 pea.py tableau      # régénère seulement tableau.html
python3 exemple.py          # tableau_exemple.html : données fictives (graine fixe), publiable

python3 -m unittest tests/test_parser.py tests/test_anonymat.py                 # tous les tests
python3 -m unittest tests.test_parser.TestParser.test_achat_bourse_etrangere    # un test

pdftotext -layout fichier.pdf -   # inspecter la mise en page d'un nouvel avis avant d'adapter le parseur
```

Les tests utilisent une base SQLite en mémoire : ils ne touchent jamais `pea.db`. Ils lisent des avis anonymisés : le texte `pdftotext -layout` des vrais avis, dont le titulaire, l'adresse, l'agence, les comptes, les codes-barres et la référence d'ordre ont été remplacés par `tests/anonymiser.py` (largeur des champs conservée, car le parseur lit les colonnes par position). Les PDF d'origine restent dans `tests/fixtures/` mais sont exclus par `.gitignore`. Pour ajouter un avis : `python3 tests/anonymiser.py tests/fixtures/AvisOperation_….pdf`.

`tests/test_anonymat.py` vérifie qu'aucune donnée personnelle n'est publiable : contrôles structurels, plus les chaînes de `tests/interdits.txt` (fichier local, non versionné ; ne jamais écrire ces données ailleurs).

## Automatisation (launchd)

- `~/Library/LaunchAgents/com.guillaume.gestion-pea.plist` : `WatchPaths` sur `inbox/` lance `pea.py traiter`.
- `~/Library/LaunchAgents/com.guillaume.gestion-pea-cours.plist` : `pea.py cours`, du lundi au vendredi à 18h30.
- Sortie des deux agents : `~/Library/Logs/gestion-pea-launchd.log` ; journal applicatif : `logs/pea.log`.

Les agents tournent avec un PATH minimal : garder des chemins absolus. `pea.py` prend un verrou (`.pea.lock`) : un seul traitement à la fois. Attention, déposer un fichier dans `inbox/` déclenche un vrai import dans `pea.db`. Pour tester, passer par les tests unitaires plutôt que par l'inbox.

## Architecture

Flux : `parser_avis.lire()` → `db.inserer()` → `db.recalculer_valeurs()` + `db.recalculer_historique()` → `tableau.generer()`. Un import réussi enchaîne sur `cours()`, pour qu'une nouvelle valeur soit valorisée tout de suite.

- `transactions` est la seule source de vérité. `valeurs` (positions, PRU) et `historique` (valorisation quotidienne) sont supprimées puis reconstruites intégralement à chaque passage, dans l'ordre chronologique : ne jamais les modifier à la main. L'ordre de dépôt des avis est donc indifférent.
- Le PRU est un coût moyen pondéré, frais inclus (`montant_net` = brut + courtages + commission + frais). La logique est centralisée dans `db.appliquer()`, réutilisée par `recalculer_valeurs`, `recalculer_historique` et `tableau.situations()` (position après chaque transaction, pour le texte « Partager »). Une vente laisse le PRU inchangé et cumule la plus-value réalisée.
- `historique` valorise chaque titre à sa dernière clôture connue (les places ont des jours fériés différents), ou à son coût s'il n'a encore aucun cours.
- `v_positions` (vue SQL) joint les positions au dernier cours.
- `tickers` associe un ISIN à un symbole Yahoo et à un secteur. La table est remplie automatiquement, mais un symbole corrigé à la main est conservé.
- Les migrations de schéma sont faites à la main dans `db.connecter()` (`ALTER TABLE` si une colonne manque). Ajouter une colonne demande de la déclarer à la fois dans `SCHEMA` et dans cette migration.

### Parseur des avis (`parser_avis.py`)

- Deux mises en page connues, repérées par l'en-tête : « ACHAT AU COMPTANT » (Paris : type d'ordre, solde négociable) et « ACHAT EN BOURSE ETRANGERE » (lieu d'exécution, ligne « Courtages (1) » en plus). Les variantes VENTE sont prévues, mais aucun vrai avis de vente n'a encore été vu.
- Les montants sont associés à l'en-tête de colonne le plus proche horizontalement (`_montants`), car une colonne peut être vide (ex. « Frais (1) » sans montant = 0).
- Le mappage des coûts vers les colonnes en base passe par le dictionnaire `COLONNES`.
- `verifier()` exige brut ± coûts = net, et quantité × cours ≈ brut. Tout avis qui échoue part dans `erreurs/` au lieu d'entrer en base.
- `parse_ordre()` lit un copier-coller du « suivi des ordres » du site de la banque déposé en `.txt`. La commission y est inconnue : la transaction est inscrite avec `provisoire = 1`. L'avis PDF de même `reference` (le n° d'ordre, ex. `G260921U5177`) la remplacera. `db.inserer()` renvoie `importé`, `remplacé` ou `doublon`.
- Rapprochement : le `solde_titres` des avis au comptant est comparé à la quantité en base en fin de journée, et un écart produit un avertissement dans le log. La base est volontairement incomplète : le titulaire n'a pas tous les avis.

### Tableau de bord

`tableau.py` injecte un JSON à la place de `/*__DONNEES__*/null` dans `tableau_modele.html`. Modifier le modèle, jamais `tableau.html`, qui est écrasé à chaque génération.

Le modèle est en HTML/SVG/JS natif, sans bibliothèque externe, et fonctionne hors ligne. Il suit ces règles :
- couleurs en variables CSS, avec un mode sombre défini à la fois par `prefers-color-scheme` et par `data-theme` ;
- bleu = gain et rouge = perte (paire validée pour le daltonisme) ;
- textes insérés uniquement via `textContent` ;
- chaque graphique a son équivalent en tableau.

Vérifier un changement visuel par une capture : `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --screenshot=… --window-size=1280,2000 file://…/tableau.html`. En mode headless, la largeur minimale est de 500 px : pour tester l'affichage mobile, intégrer la page dans une iframe de 390 px.
