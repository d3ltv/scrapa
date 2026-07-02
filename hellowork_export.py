#!/usr/bin/env python3
"""Export HelloWork via Apify — ligne de commande."""

import argparse
import os
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from export_common import apply_post_filters
from hellowork_lib import fetch_hellowork_offers, export_hellowork_csv, HelloWorkError


def main():
    p = argparse.ArgumentParser(description="Export HelloWork via Apify")
    p.add_argument("--mots-cles", nargs="+", help="Mots-clés de recherche")
    p.add_argument("--lieu", default="", help="Ville ou région")
    p.add_argument("--url", action="append", dest="urls", help="URL de recherche HelloWork (répétable)")
    p.add_argument("--contrat", action="append", dest="contrats", help="CDI, CDD, ALTERNANCE, STAGE, INTERIM, FREELANCE")
    p.add_argument("--teletravail", action="append", dest="telework", help="FULL, PARTIAL, OCCASIONAL, NONE")
    p.add_argument("--date", default="any", help="any, 24h, 3d, 1w, 1m")
    p.add_argument("--salaire-min", type=int, default=None)
    p.add_argument("--max", type=int, default=200)
    p.add_argument("--enrichi", action="store_true", help="Mode profil entreprise (effectifs)")
    p.add_argument("--entreprise-contient", default="")
    p.add_argument("--secteur-contient", default="")
    p.add_argument("--output", default=None)
    args = p.parse_args()

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("[ERREUR] APIFY_TOKEN manquant dans .env")

    if not args.mots_cles and not args.urls:
        sys.exit("[ERREUR] --mots-cles ou --url requis")

    try:
        rows = fetch_hellowork_offers(
            token,
            enriched_mode=args.enrichi,
            search_queries=args.mots_cles or [],
            location=args.lieu,
            start_urls=args.urls,
            max_results=args.max,
            contract_types=args.contrats,
            telework_modes=args.telework,
            date_posted=args.date,
            include_company_profile=args.enrichi,
        )
        rows = apply_post_filters(
            rows,
            company_pattern=args.entreprise_contient,
            sector_contains=args.secteur_contient,
        )
    except HelloWorkError as e:
        sys.exit(f"[ERREUR] {e}")

    out = args.output or f"offres_hellowork_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    n = export_hellowork_csv(rows, out)
    print(f"✅ {n} offres exportées : {out}")


if __name__ == "__main__":
    main()
