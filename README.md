# GitHub Leaderboard

Génère un classement des contributeurs d'un ou plusieurs dépôts GitHub (ou d'une organisation entière) à partir d'une date/commit de départ, et le publie comme site statique via GitHub Pages.

## Métriques

| Métrique | Description |
|----|----|
| `commits` | Nombre de commits authored |
| `java_lines` | Lignes modifiées (add + del) sur fichiers `.java` |
| `gaml_lines` | Lignes modifiées sur fichiers `.gaml` |
| `wiki_lines` | Lignes modifiées sur les dépôts wiki (`*.wiki`) |
| `issues_opened` | Issues ouvertes par l'utilisateur |
| `issues_closed` | Issues ouvertes par l'utilisateur et depuis closes |
| `prs_opened` | Pull requests ouvertes par l'utilisateur |
| `prs_merged` | Pull requests de l'utilisateur qui ont été mergées |
| `global_score` | Moyenne normalisée des métriques ci-dessus (0–1), recalculée dans l'UI en fonction du filtre de dates |

Toutes les métriques sont aussi stockées dans un `timeline` mensuel (`YYYY-MM`) par utilisateur, ce qui permet à l'UI :
- de filtrer par plage de dates (mois de début / fin),
- d'afficher un graphique cumulatif d'évolution pour le top 5 sur la métrique sélectionnée.

## Structure

```
gama-leaderboard/
├── config.json              # repos / org / date ou commit de départ
├── scripts/build.py         # fetch API GitHub → site/data.json
├── site/                    # contenu publié sur GitHub Pages
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── data.json            # généré par le script
└── .github/workflows/build.yml
```

## Configuration (`config.json`)

- `org`: nom d'une organisation GitHub — tous ses repos publics seront analysés.
- `repos`: liste explicite de `owner/repo` (ajoutée aux repos de l'org).
- `since`: ISO date (`2024-01-01T00:00:00Z`) appliqué à tous les repos par défaut.
- `repo_overrides`: override par repo (peut être un SHA de commit de 40 chars).
- `exclude_repos`, `exclude_bots`, `max_commits_per_repo`.

Exemple : analyser gama-platform depuis un commit précis sur `gama` et une date sur le reste :

```json
{
  "org": "gama-platform",
  "since": "2024-01-01T00:00:00Z",
  "repo_overrides": {
    "gama-platform/gama": { "since": "abcdef0123456789abcdef0123456789abcdef01" }
  }
}
```

## Usage local

```bash
export GITHUB_TOKEN=ghp_xxx   # recommandé (5000 req/h au lieu de 60)
python scripts/build.py
# ouvrez site/index.html dans un navigateur
```

Sans token, l'API publique est limitée à 60 requêtes/heure — utilisable pour tester avec 1–2 petits repos seulement.

## Publication sur GitHub Pages

1. Créez un dépôt GitHub, pushez ce projet.
2. Dans **Settings → Pages**, choisissez **Source: GitHub Actions**.
3. Le workflow `.github/workflows/build.yml` s'exécute :
   - à chaque push sur `main`,
   - chaque jour à 06:00 UTC (cron),
   - à la demande (`workflow_dispatch`).
4. Le `GITHUB_TOKEN` fourni par Actions est utilisé automatiquement.

## Notes & limites

- Pour les stats par type de fichier, le script doit récupérer le détail de chaque commit → 1 requête par commit. Le cap `max_commits_per_repo` (défaut 2000) évite les dépassements.
- Les dépôts wiki GitHub (`repo.wiki.git`) ne sont pas exposés via l'API REST : le projet traite à la place un repo dont le nom se termine par `.wiki` (convention utilisée p.ex. par `gama-platform/gama.wiki`).
- `issues_closed` compte les issues ouvertes par l'utilisateur qui sont aujourd'hui `closed`, pas celles closes *par* cet utilisateur (non trivialement disponible via l'API).
- Le `global_score` est une moyenne de ratios `metric / max(metric)` sur tous les utilisateurs retenus.
