# Export d'offres d'emploi — France Travail & HelloWork

Outil Python (interface graphique) qui agrège des offres d'emploi depuis :

- **France Travail** — API officielle (francetravail.io)
- **HelloWork** — via **Apify** (salaries, télétravail, compétences, profils entreprise)

Export CSV fusionné ou séparé, avec filtres avancés post-récupération.

## Fichiers

| Fichier | Rôle |
|---|---|
| `france_travail_gui.py` | Interface graphique (recommandé) |
| `france_travail_export.py` | CLI France Travail |
| `hellowork_export.py` | CLI HelloWork (Apify) |
| `france_travail_lib.py` | Moteur France Travail |
| `hellowork_lib.py` | Moteur HelloWork / Apify |
| `export_common.py` | Export CSV et filtres communs |

## 1. Identifiants

### France Travail (gratuit)

1. Compte sur **https://francetravail.io**
2. Application abonnée à l'API **« Offres d'emploi v2 »**
3. `FRANCE_TRAVAIL_CLIENT_ID` + `FRANCE_TRAVAIL_CLIENT_SECRET`

### Apify — HelloWork

1. Compte sur **https://console.apify.com**
2. Token dans **Account → Integrations**
3. Ajoute dans `.env` : `APIFY_TOKEN=apify_api_...`

> Apify facture à l'usage (~1 € / 1000 offres HelloWork selon l'acteur).

## 2. Installation sur ton Mac (une seule fois)

Ouvre le Terminal dans le dossier du projet, puis :

```bash
chmod +x setup.sh
./setup.sh
```

Ce script crée un environnement Python isolé (`.venv`), installe les dépendances,
vérifie que Tkinter est disponible, et copie `.env.example` vers `.env` si besoin.

Ensuite, ouvre `.env` et renseigne tes identifiants (`FRANCE_TRAVAIL_*` et `APIFY_TOKEN`).

> **Alternative manuelle** : `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`

## 3. Utilisation — Interface graphique (recommandé)

**Méthode la plus simple** : double-clique sur **`Lancer France Travail.command`**

Ou depuis le Terminal :

```bash
.venv/bin/python3 france_travail_gui.py
```

Une fenêtre s'ouvre avec 5 onglets :

1. **Identifiants** — France Travail + token Apify (boutons « Tester »)
2. **Sources** — cocher France Travail, HelloWork, ou les deux ; CSV fusionné ou séparé
3. **France Travail** — mots-clés, commune INSEE, département, contrat, expérience, NAF, salaire…
4. **HelloWork (Apify)** — deux modes :
   - **Standard** : contrats (CDI, CDD, alternance…), télétravail, date, salaire min, descriptions complètes
   - **Enrichi** : profil entreprise avec **effectifs / taille**
   - **URLs HelloWork** : colle une URL de recherche HelloWork pour filtres UI (taille entreprise, domaine…)
5. **Filtres avancés** — post-filtres sur toutes les sources : entreprise (regex), secteur, compétences, expérience, formation, effectif min/max, salaire max

Clique **« Rechercher et exporter »**, puis **« Ouvrir le dernier CSV »**.

> Sur Mac M2 avec Python installé depuis python.org, Tkinter est inclus
> d'office. Pour vérifier que ça fonctionne avant de lancer l'app :
> ```bash
> python3 -m tkinter
> ```
> Une petite fenêtre de test doit s'afficher. Si tu utilises Python installé
> via Homebrew et que Tkinter manque : `brew install python-tk`.

## 4. Utilisation — Ligne de commande

France Travail :

```bash
.venv/bin/python3 france_travail_export.py --commune 75056 --rayon 20 --contrat CDI --jours 7
```

HelloWork (Apify) :

```bash
.venv/bin/python3 hellowork_export.py --mots-cles développeur --lieu Paris --contrat CDI --teletravail FULL --max 200
```

Mode enrichi (effectifs entreprise) :

```bash
.venv/bin/python3 hellowork_export.py --mots-cles "data engineer" --lieu Lyon --enrichi --max 100
```

Le fichier CSV est généré dans le même dossier, prêt à ouvrir dans Excel / Numbers
(séparateur `;`, encodage UTF-8 avec BOM pour un affichage correct des accents).

## 5. Options disponibles (ligne de commande)

| Option | Description |
|---|---|
| `--mots-cles` | Mots-clés libres (intitulé, description...) |
| `--commune` | Code INSEE de la commune (ex: `75056` pour Paris) |
| `--departement` | Code département (ex: `75`) |
| `--rayon` | Rayon en km autour de `--commune` |
| `--contrat` | `CDI`, `CDD`, `MIS` (intérim), `SAI` (saisonnier)... |
| `--qualification` | Code qualification (voir doc API) |
| `--experience` | `1`=débutant, `2`=1-3 ans, `3`=+3 ans |
| `--secteur` | Code secteur d'activité / NAF — utile pour cibler un "type d'entreprise" |
| `--temps-plein` / `--temps-partiel` | Filtre temps de travail |
| `--salaire-min` | Salaire annuel brut minimum |
| `--jours` | Offres publiées depuis N jours (`1`, `3`, `7`, `14`, `31`) |
| `--entreprise-contient` | Filtre regex appliqué sur le nom de l'entreprise (ex: `"Tech|Digital"`) |
| `--max` | Nombre maximum d'offres à récupérer (défaut 500, plafond API ~3000) |
| `--output` | Nom du fichier CSV de sortie |

La liste complète des codes (qualifications, secteurs NAF, types de contrat...)
est dans la documentation officielle : https://francetravail.io/produits-partages/catalogue/offres-emploi/documentation

## 6. Champs exportés (CSV fusionné)

`source`, `id`, `intitule`, `entreprise`, `entreprise_url`, `ville`, `region`,
`code_postal`, `secteur`, `domaine`, `type_contrat`, `teletravail`,
`salaire_libelle`, `salaire_min`, `salaire_max`, `experience`, `formation`,
`competences`, `taille_entreprise`, `effectif_entreprise`, `date_publication`,
`description`, `url`

## 7. Automatiser (optionnel, ligne de commande uniquement)

Pour exécuter ce script automatiquement chaque jour sur ton Mac, tu peux
utiliser `cron` ou un Automator/Calendrier. Exemple cron (tous les jours à 8h) :

```bash
crontab -e
# puis ajouter :
0 8 * * * cd /chemin/vers/le/dossier && /usr/bin/python3 france_travail_export.py --departement 75 --jours 1 --output "offres_$(date +\%Y\%m\%d).csv"
```

## À propos d'Indeed

L'API publique de recherche d'offres d'Indeed n'est plus accessible aux
développeurs tiers depuis plusieurs années — l'accès est aujourd'hui réservé
à des partenaires validés (programmes Indeed Apply / ATS), et scraper le site
violerait leurs conditions d'utilisation. Ce script ne couvre donc que
France Travail. Si tu veux élargir tes sources de données légalement, des
pistes intéressantes : l'API **Adzuna** (agrégateur avec API publique
gratuite) ou l'API de l'**APEC** pour les cadres.

## Limites à connaître

- L'API France Travail plafonne les résultats accessibles par recherche à
  environ 3000 offres au total (pagination par paquets de 150).
- Le filtre "type d'entreprise" n'existe pas nativement dans l'API ; le script
  s'appuie sur `--secteur` (code NAF) côté API et sur `--entreprise-contient`
  (filtre texte appliqué après récupération) pour s'en approcher.
- Respecte les CGU de l'API (usage raisonnable, pas de réutilisation
  commerciale interdite par les conditions de francetravail.io — relis-les
  pour ton cas d'usage de mise en relation recruteurs/candidats).
