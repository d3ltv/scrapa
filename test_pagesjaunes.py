#!/usr/bin/env python3
"""
test_pagesjaunes.py
===================
Test de scraping Pages Jaunes via Apify (scrapersdelight/pagesjaunes-france-scraper).

Teste la récupération de numéros de téléphone pour 3 entreprises connues.

Usage :
    python3 test_pagesjaunes.py
    python3 test_pagesjaunes.py --entreprise "BNP Paribas" --ville "Paris"
"""

import os
import sys
import json
import argparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pagesjaunes_lib import search_phone, PagesJaunesError

# ---------------------------------------------------------------------------
# Cas de test par défaut — entreprises connues et facilement trouvables
# ---------------------------------------------------------------------------

DEFAULT_TEST_CASES = [
    {"company": "Boulangerie Paul", "city": "Paris"},
    {"company": "Leroy Merlin",      "city": "Lyon"},
    {"company": "Mairie",            "city": "Tours"},
]


def run_tests(token: str, test_cases: list[dict]) -> None:
    print(f"\n{'═'*60}")
    print("Test Pages Jaunes — récupération numéros de téléphone")
    print(f"{'═'*60}\n")

    total = len(test_cases)
    found = 0

    for i, case in enumerate(test_cases, 1):
        company = case["company"]
        city    = case.get("city", "")
        print(f"[{i}/{total}] {company} — {city}")
        print(f"{'─'*50}")

        results = search_phone(token, company, city, max_results=3)

        if not results:
            print("  ❌ Aucun résultat\n")
            continue

        for j, r in enumerate(results, 1):
            tél  = r["pj_telephone"] or "(non disponible)"
            mail = r["pj_email"]     or "(non disponible)"
            siret = r["pj_siret"]    or "(non disponible)"
            site = r["pj_site_web"]  or "(non disponible)"
            print(f"  Résultat {j} : {r['pj_nom']}")
            print(f"    📞 Téléphone : {tél}")
            print(f"    📧 Email     : {mail}")
            print(f"    🏢 SIRET     : {siret}")
            print(f"    🌐 Site web  : {site}")
            print(f"    📍 Adresse   : {r['pj_adresse']} {r['pj_ville']} {r['pj_code_postal']}")
            print(f"    🔗 Fiche PJ  : {r['pj_url_fiche']}")
            print()

        if results[0]["pj_telephone"]:
            found += 1

        print()

    print(f"{'═'*60}")
    print(f"Résumé : {found}/{total} entreprises avec numéro de téléphone trouvé")
    print(f"{'═'*60}\n")

    # Dump JSON brut du dernier résultat pour inspection
    if test_cases:
        last_case = test_cases[-1]
        last_results = search_phone(
            token,
            last_case["company"],
            last_case.get("city", ""),
            max_results=1,
            log=lambda *a: None,  # silencieux
        )
        if last_results and last_results[0].get("pj_raw"):
            print("📋 Données brutes du dernier résultat (debug) :")
            raw = last_results[0]["pj_raw"]
            # Retirer la clé pj_raw récursive si elle existe
            raw_clean = {k: v for k, v in raw.items() if k != "pj_raw"}
            print(json.dumps(raw_clean, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Test scraping Pages Jaunes")
    parser.add_argument("--entreprise", default=None, help="Nom d'entreprise à tester")
    parser.add_argument("--ville", default="", help="Ville")
    parser.add_argument("--max", type=int, default=3, help="Nombre max de résultats")
    args = parser.parse_args()

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("[ERREUR] APIFY_TOKEN manquant dans .env")

    if args.entreprise:
        test_cases = [{"company": args.entreprise, "city": args.ville}]
    else:
        test_cases = DEFAULT_TEST_CASES

    run_tests(token, test_cases)


if __name__ == "__main__":
    main()
