#!/usr/bin/env python3
"""
France Travail - Export d'offres d'emploi vers CSV (ligne de commande)
=========================================================================

Interroge l'API publique "Offres d'emploi v2" de France Travail
(https://francetravail.io) et exporte les résultats filtrés en CSV.

Prérequis :
  1. Créer un compte sur https://francetravail.io
  2. Créer une "application" et s'abonner à l'API "Offres d'emploi v2"
  3. Récupérer le client_id et le client_secret
  4. Les renseigner dans un fichier .env (voir .env.example) ou en
     variables d'environnement

Usage :
  python3 france_travail_export.py --ville Paris --rayon 20 --contrat CDI --jours 7

Voir README.md pour la liste complète des options et des exemples.

Pour une interface graphique, lance plutôt : python3 france_travail_gui.py
"""

import os
import sys
import argparse
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from france_travail_lib import (
    get_access_token,
    fetch_offers,
    matches_company_filter,
    export_csv,
    FranceTravailError,
)


def build_params(args) -> dict:
    params = {}
    if args.mots_cles:
        params["motsCles"] = args.mots_cles
    if args.commune:
        params["commune"] = args.commune
    if args.departement:
        params["departement"] = args.departement
    if args.rayon and args.commune:
        params["distance"] = args.rayon
    if args.contrat:
        params["typeContrat"] = args.contrat
    if args.qualification:
        params["qualificationCode"] = args.qualification
    if args.experience:
        params["experience"] = args.experience
    if args.secteur:
        params["secteurActivite"] = args.secteur
    if args.temps_plein is not None:
        params["tempsPlein"] = "true" if args.temps_plein else "false"
    if args.salaire_min:
        params["salaireMin"] = args.salaire_min
    if args.jours:
        params["publieeDepuis"] = args.jours
    if args.tri:
        params["sort"] = args.tri
    return params


def main():
    parser = argparse.ArgumentParser(description="Export d'offres France Travail vers CSV")

    parser.add_argument("--mots-cles", help="Mots-clés (ex: 'développeur python')")
    parser.add_argument("--commune", help="Code INSEE de la commune (ex: 75056 pour Paris)")
    parser.add_argument("--departement", help="Code département (ex: 75)")
    parser.add_argument("--rayon", type=int, help="Rayon de recherche en km autour de --commune")
    parser.add_argument("--contrat", help="Code type de contrat: CDI, CDD, MIS, SAI, ...")
    parser.add_argument("--qualification", help="Code qualification (voir doc API)")
    parser.add_argument("--experience", help="1=débutant, 2=1-3 ans, 3=+3 ans")
    parser.add_argument("--secteur", help="Code secteur d'activité / NAF")
    parser.add_argument("--temps-plein", dest="temps_plein", action="store_true", default=None)
    parser.add_argument("--temps-partiel", dest="temps_plein", action="store_false")
    parser.add_argument("--salaire-min", help="Salaire minimum annuel brut")
    parser.add_argument("--jours", type=int, default=None, help="Offres publiées depuis N jours (1,3,7,14,31)")
    parser.add_argument("--tri", help="Critère de tri (voir doc API, ex: '0' = pertinence)")
    parser.add_argument("--entreprise-contient", help="Filtre regex sur le nom de l'entreprise")
    parser.add_argument("--max", type=int, default=500, help="Nombre maximum d'offres à récupérer (défaut: 500)")
    parser.add_argument("--output", default=None, help="Chemin du fichier CSV de sortie")

    args = parser.parse_args()

    client_id = os.environ.get("FRANCE_TRAVAIL_CLIENT_ID")
    client_secret = os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET")

    if not client_id or not client_secret:
        sys.exit(
            "[ERREUR] Identifiants manquants.\n"
            "Crée un fichier .env (copie .env.example) avec :\n"
            "  FRANCE_TRAVAIL_CLIENT_ID=...\n"
            "  FRANCE_TRAVAIL_CLIENT_SECRET=...\n"
            "Obtenus sur https://francetravail.io"
        )

    print("Authentification auprès de France Travail...")
    try:
        token = get_access_token(client_id, client_secret)
    except FranceTravailError as e:
        sys.exit(f"[ERREUR] {e}")

    params = build_params(args)
    print(f"Critères de recherche : {params}")

    print("Récupération des offres...")
    try:
        offers = fetch_offers(token, params, args.max)
    except FranceTravailError as e:
        sys.exit(f"[ERREUR] {e}")

    if args.entreprise_contient:
        before = len(offers)
        offers = [o for o in offers if matches_company_filter(o, args.entreprise_contient)]
        print(f"Filtre entreprise appliqué : {before} -> {len(offers)} offres")

    output_path = args.output or f"offres_france_travail_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    count = export_csv(offers, output_path)
    if count:
        print(f"\n✅ {count} offres exportées dans : {output_path}")
    else:
        print("\nAucune offre à exporter.")


if __name__ == "__main__":
    main()
