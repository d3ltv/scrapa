#!/usr/bin/env python3
"""
company_search.py
=================
Recherche les offres d'emploi d'une liste d'entreprises via HelloWork (Apify).

Workflow :
  1. Charger un CSV contenant les noms d'entreprises
  2. Pour chaque entreprise, lancer un run Apify (par lots de 10)
  3. Filtrer les résultats pour ne garder que les offres de l'entreprise ciblée
  4. Exporter toutes les offres trouvées dans un CSV final

Usage :
  python3 company_search.py --input entreprises.csv --colonne "Nom entreprise"
  python3 company_search.py --input entreprises.csv --colonne "company" --output resultats.csv
  python3 company_search.py --input entreprises.csv --colonne "Nom" --jours 30 --contrat CDI

Voir --help pour toutes les options.
"""

import os
import sys
import csv
import time
import unicodedata
import argparse
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from hellowork_lib import (
    fetch_hellowork_offers,
    HelloWorkError,
)
from export_common import export_csv_rows
from seen_ids_cache import commit_rows, archive_csv

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

BATCH_SIZE = 5           # entreprises traitées par lot (Apify est lent, on limite)
DELAY_BETWEEN_CALLS = 1.0   # secondes entre chaque appel dans un lot
DELAY_BETWEEN_BATCHES = 3.0  # pause entre les lots
MAX_OFFERS_PER_COMPANY = 100  # max offres récupérées par entreprise

# Colonnes du CSV de sortie
OUTPUT_FIELDNAMES = [
    "source", "id", "intitule", "url",
    # Entreprise
    "entreprise", "entreprise_url", "entreprise_logo", "siret",
    "entreprise_description", "site_web_entreprise",
    "linkedin_entreprise", "twitter_entreprise", "facebook_entreprise",
    "annee_creation_entreprise", "chiffre_affaires_entreprise",
    "taille_entreprise", "effectif_entreprise",
    # Dirigeant
    "dirigeant_nom", "dirigeant_titre", "dirigeant_linkedin",
    # Contact recruteur
    "contact_nom", "contact_telephone", "contact_email", "contact_linkedin",
    # Lieu
    "ville", "region", "code_postal", "pays", "lien_maps",
    # Catégories
    "secteur", "domaine",
    # Contrat
    "type_contrat", "teletravail",
    "salaire_libelle", "salaire_min", "salaire_max",
    "experience", "formation", "competences",
    # Dates
    "date_publication", "date_expiration",
    # Description
    "description",
    # Traçabilité
    "entreprise_recherchee", "nb_offres_entreprise",
]


# ---------------------------------------------------------------------------
# Normalisation pour matching souple
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    s = (s or "").lower().strip()
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _company_matches(row: dict, company_name: str) -> bool:
    """
    Vérifie si une offre HelloWork correspond à l'entreprise recherchée.
    Matching souple : l'un contient l'autre (insensible à la casse/accents).
    """
    nom = (row.get("entreprise") or "")
    if not nom:
        return False
    n_search = _normalize(company_name)
    n_offer  = _normalize(nom)
    return n_search in n_offer or n_offer in n_search


# ---------------------------------------------------------------------------
# Lecture du CSV d'entrée
# ---------------------------------------------------------------------------

def load_company_names(csv_path: str, column_name: str) -> list[str]:
    """
    Lit le CSV et retourne la liste des noms d'entreprises (dédupliqués, non vides).
    Supporte les séparateurs ; et ,
    """
    companies = []
    seen = set()

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        sample = f.read(2048)
    sep = ";" if sample.count(";") >= sample.count(",") else ","

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=sep)
        headers = reader.fieldnames or []

        if column_name not in headers:
            match = next(
                (h for h in headers if h.strip().lower() == column_name.strip().lower()),
                None,
            )
            if match:
                column_name = match
            else:
                raise ValueError(
                    f"Colonne '{column_name}' introuvable dans le CSV.\n"
                    f"Colonnes disponibles : {headers}"
                )

        for row in reader:
            name = (row.get(column_name) or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                companies.append(name)

    return companies


def list_csv_columns(csv_path: str) -> list[str]:
    """Retourne la liste des colonnes d'un CSV."""
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        sample = f.read(2048)
    sep = ";" if sample.count(";") >= sample.count(",") else ","
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=sep)
        return list(reader.fieldnames or [])


# ---------------------------------------------------------------------------
# Recherche HelloWork par entreprise
# ---------------------------------------------------------------------------

def search_company_hw(
    token: str,
    company_name: str,
    extra_params: dict,
    max_offers: int,
    log=print,
) -> list[dict]:
    """
    Lance un run Apify HelloWork avec le nom de l'entreprise comme mot-clé,
    puis filtre les résultats pour ne garder que les offres de cette entreprise.
    """
    contract_types = extra_params.get("contractTypes") or None
    date_posted    = extra_params.get("datePosted", "any")
    location       = extra_params.get("location", "")
    radius_km      = extra_params.get("radius_km")

    try:
        rows = fetch_hellowork_offers(
            token,
            search_queries=[company_name],
            location=location,
            radius_km=radius_km,
            max_results=max_offers,
            contract_types=contract_types,
            date_posted=date_posted,
            log=log,
        )
        log(f"    [{company_name}] → {len(rows)} offre(s) trouvée(s)")
    except HelloWorkError as e:
        log(f"    ⚠️  [{company_name}] Erreur HelloWork : {e}")
        return []

    # On retourne toutes les offres — HelloWork cherche par mots-clés dans les
    # titres/descriptions, pas par filtre exact. Le nom recherché est tracé via
    # entreprise_recherchee dans le CSV final.
    return rows


# ---------------------------------------------------------------------------
# Traitement par lots
# ---------------------------------------------------------------------------

def process_companies_in_batches(
    token: str,
    companies: list[str],
    extra_params: dict,
    max_offers_per_company: int,
    log=print,
) -> list[dict]:
    """
    Parcourt toutes les entreprises par lots de BATCH_SIZE.
    Retourne la liste fusionnée de toutes les offres.
    """
    total = len(companies)
    all_rows: list[dict] = []
    seen_offer_ids: set[str] = set()

    for batch_start in range(0, total, BATCH_SIZE):
        batch = companies[batch_start: batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        log(f"\n{'─'*60}")
        log(f"Lot {batch_num}/{total_batches} — entreprises {batch_start+1}–{min(batch_start+BATCH_SIZE, total)}/{total}")
        log(f"{'─'*60}")

        for company in batch:
            log(f"\n  🔍 Recherche : {company}")
            rows = search_company_hw(token, company, extra_params, max_offers_per_company, log=log)

            new_count = 0
            for row in rows:
                offer_id = str(row.get("id") or "")
                if offer_id and offer_id in seen_offer_ids:
                    continue
                if offer_id:
                    seen_offer_ids.add(offer_id)
                row["entreprise_recherchee"] = company
                row["nb_offres_entreprise"] = len(rows)
                all_rows.append(row)
                new_count += 1

            log(f"    ✅ {new_count} nouvelle(s) offre(s) — total cumulé : {len(all_rows)}")
            time.sleep(DELAY_BETWEEN_CALLS)

        if batch_start + BATCH_SIZE < total:
            log(f"\n⏸  Pause {DELAY_BETWEEN_BATCHES}s avant le prochain lot…")
            time.sleep(DELAY_BETWEEN_BATCHES)

    return all_rows


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------

def export_results(rows: list[dict], output_path: str, log=print) -> int:
    if not rows:
        log("Aucune offre à exporter.")
        return 0
    count = export_csv_rows(rows, output_path, fieldnames=OUTPUT_FIELDNAMES)
    log(f"\n✅ {count} offres exportées dans : {output_path}")
    # Commit IDs dans le cache anti-doublon + archivage pour récupération future
    commit_rows(rows)
    archive_csv(output_path, rows, label="company_search_cli")
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Recherche d'offres HelloWork pour une liste d'entreprises (CSV → CSV)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  # Lister les colonnes du CSV
  python3 company_search.py --list-columns entreprises.csv

  # Recherche simple
  python3 company_search.py --input entreprises.csv --colonne "Nom entreprise"

  # Avec filtres
  python3 company_search.py --input entreprises.csv --colonne "company" \\
      --jours 30 --contrat CDI --output offres.csv
        """,
    )

    parser.add_argument("--input", "-i", required=False, help="Chemin du CSV d'entreprises")
    parser.add_argument("--colonne", "-c", default=None, help="Colonne contenant les noms d'entreprises")
    parser.add_argument("--output", "-o", default=None, help="Fichier CSV de sortie")
    parser.add_argument("--list-columns", metavar="CSV", default=None, help="Affiche les colonnes d'un CSV et quitte")
    parser.add_argument("--location", help="Lieu de recherche (ville, département)")
    parser.add_argument("--contrat", help="CDI, CDD, ALTERNANCE, STAGE, INTERIM, FREELANCE")
    parser.add_argument("--jours", help="Offres publiées depuis : 24h, 3d, 1w, 1m")
    parser.add_argument("--max-par-entreprise", type=int, default=MAX_OFFERS_PER_COMPANY,
                        help=f"Max offres par entreprise (défaut : {MAX_OFFERS_PER_COMPANY})")

    args = parser.parse_args()

    if args.list_columns:
        try:
            cols = list_csv_columns(args.list_columns)
            print(f"Colonnes du fichier '{args.list_columns}' :")
            for i, col in enumerate(cols, 1):
                print(f"  {i:2}. {col}")
        except Exception as e:
            sys.exit(f"[ERREUR] {e}")
        return

    if not args.input:
        parser.error("--input est requis")
    if not args.colonne:
        parser.error("--colonne est requis")

    apify_token = os.environ.get("APIFY_TOKEN")
    if not apify_token:
        sys.exit("[ERREUR] APIFY_TOKEN manquant dans le .env")

    print(f"\n📂 Chargement : {args.input}")
    try:
        companies = load_company_names(args.input, args.colonne)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(f"[ERREUR] {e}")

    if not companies:
        sys.exit("[ERREUR] Aucune entreprise trouvée dans le CSV.")

    print(f"   → {len(companies)} entreprise(s) à traiter")

    extra_params: dict = {}
    if args.contrat:
        extra_params["contractTypes"] = [args.contrat]
    if args.jours:
        extra_params["datePosted"] = args.jours
    if args.location:
        extra_params["location"] = args.location

    print(f"\n🚀 Début de la recherche ({len(companies)} entreprises, lots de {BATCH_SIZE})…")
    start_time = time.time()

    all_rows = process_companies_in_batches(
        token=apify_token,
        companies=companies,
        extra_params=extra_params,
        max_offers_per_company=args.max_par_entreprise,
        log=print,
    )

    elapsed = time.time() - start_time
    print(f"\n{'═'*60}")
    print(f"Traitement terminé en {elapsed:.1f}s — {len(all_rows)} offres trouvées")

    output_path = args.output or f"offres_entreprises_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    export_results(all_rows, output_path, log=print)

    from collections import Counter
    print("\n📊 Résumé par entreprise :")
    counts = Counter(r["entreprise_recherchee"] for r in all_rows)
    for company in companies:
        n = counts.get(company, 0)
        print(f"  {n:4d}  {company}")


if __name__ == "__main__":
    main()
