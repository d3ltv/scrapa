#!/usr/bin/env python3
"""
enrich_phone.py
===============
Enrichit un CSV d'offres d'emploi avec les numéros de téléphone des entreprises
via Pages Jaunes (Apify — scrapersdelight/pagesjaunes-france-scraper).

Workflow :
  1. Lit un CSV exporté par Scrapa (France Travail ou HelloWork)
  2. Déduplique les entreprises (même entreprise + ville = 1 seul appel PJ)
  3. Pour chaque entreprise unique, lance une recherche Pages Jaunes
  4. Réécrit le CSV avec les colonnes pj_telephone, pj_email, pj_siret,
     pj_site_web, pj_adresse, pj_url_fiche ajoutées

Usage :
  python3 enrich_phone.py offres_20260720.csv
  python3 enrich_phone.py offres.csv --output offres_enrichi.csv
  python3 enrich_phone.py offres.csv --delay 2 --max-results 3

Notes :
  - Coût Apify : ~0,003 $ par entreprise (3 $ / 1 000 leads)
  - ~10–15s par entreprise unique (run Apify + proxy résidentiel FR)
  - Si une entreprise n'est pas trouvée sur PJ, les colonnes pj_* restent vides
  - Le CSV original n'est jamais modifié (--output crée un nouveau fichier)
"""

import csv
import os
import sys
import re
import time
import argparse
import unicodedata
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pagesjaunes_lib import search_phone, PagesJaunesError

# Colonnes ajoutées par l'enrichissement
ENRICH_COLS = [
    "pj_telephone",
    "pj_email",
    "pj_siret",
    "pj_site_web",
    "pj_adresse",
    "pj_url_fiche",
]

# Délai entre chaque appel Apify (secondes) — respecter les limites de taux
DEFAULT_DELAY = 1.5


# ---------------------------------------------------------------------------
# Lecture / écriture CSV
# ---------------------------------------------------------------------------

def _detect_sep(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig") as f:
        sample = f.read(4096)
    return ";" if sample.count(";") >= sample.count(",") else ","


def read_csv(path: str) -> tuple[list[dict], list[str]]:
    """Retourne (rows, fieldnames)."""
    sep = _detect_sep(path)
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=sep)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return rows, fieldnames


def write_csv(rows: list[dict], fieldnames: list[str], path: str):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";",
                                extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


# ---------------------------------------------------------------------------
# Nettoyage des champs
# ---------------------------------------------------------------------------

def _clean_city(city: str) -> str:
    """
    Nettoie le champ ville pour la recherche Pages Jaunes.
    Gère les deux formats produits par France Travail :
      "37 - Tours"              → "Tours"
      "2A - Ajaccio"            → "Ajaccio"
      "971 - Pointe-à-Pitre"    → "Pointe-à-Pitre"
      "Tours (37)"              → "Tours"   (format legacy)
      "Tours"                   → "Tours"   (déjà propre)
    """
    city = (city or "").strip()
    # Format "XX - Ville" ou "2A/2B - Ville" ou "971 - Ville"
    city = re.sub(r"^(?:\d{1,3}|2[AB])\s*-\s*", "", city).strip()
    # Format "Ville (XX)" — parenthèses en fin
    city = re.sub(r"\s*\(.*?\)\s*$", "", city).strip()
    return city


def _normalize_key(company: str, city: str) -> str:
    """Clé de déduplication insensible à la casse et aux accents."""
    def _n(s):
        s = (s or "").lower().strip()
        return "".join(
            c for c in unicodedata.normalize("NFD", s)
            if unicodedata.category(c) != "Mn"
        )
    return f"{_n(company)}||{_n(city)}"


# Noms génériques qui ne correspondent pas à une vraie entreprise identifiable
_GENERIC_NAMES = {
    "confidentiel", "entreprise confidentielle", "employeur confidentiel",
    "non renseigné", "non renseigne", "nr", "n/a", "na", "nc",
    "a définir", "a definir", "à définir", "a préciser", "a preciser",
    "divers", "plusieurs entreprises", "groupement d employeurs",
    "cabinet de recrutement", "agence d interim", "agence interim",
    "société confidentielle", "societe confidentielle",
}


def _should_skip(company: str, existing_phone: str = "") -> tuple[bool, str]:
    """
    Retourne (skip, raison) — True si l'entreprise ne vaut pas un appel Pages Jaunes.

    Critères :
    - Nom vide ou trop court (< 3 caractères)
    - Nom générique/confidentiel (liste noire)
    - Téléphone déjà renseigné dans le CSV source (contact_telephone / pj_telephone)
    """
    name = (company or "").strip()

    if not name:
        return True, "nom vide"

    if len(name) < 3:
        return True, f"nom trop court ({len(name)} car.)"

    # Normalisation pour comparaison insensible accents/casse
    def _n(s):
        s = s.lower().strip()
        return "".join(
            c for c in unicodedata.normalize("NFD", s)
            if unicodedata.category(c) != "Mn"
        )

    if _n(name) in _GENERIC_NAMES:
        return True, "nom générique/confidentiel"

    if (existing_phone or "").strip():
        return True, "téléphone déjà disponible"

    return False, ""


# ---------------------------------------------------------------------------
# Enrichissement
# ---------------------------------------------------------------------------

def enrich_csv(
    input_path: str,
    output_path: str,
    token: str,
    delay: float = DEFAULT_DELAY,
    max_results: int = 3,
    company_col: str = "entreprise",
    city_col: str = "ville",
    log=print,
) -> int:
    """
    Lit le CSV, enrichit chaque ligne avec les données Pages Jaunes,
    écrit le CSV enrichi. Retourne le nombre de lignes enrichies avec un tél.

    Stratégie de déduplication :
      - On regroupe les lignes par (entreprise, ville)
      - Un seul appel Apify par groupe → résultat appliqué à toutes les lignes du groupe
      - Les entreprises sans nom sont ignorées (colonnes pj_* vides)
    """
    rows, fieldnames = read_csv(input_path)
    if not rows:
        log("⚠️  CSV vide, rien à enrichir.")
        return 0

    # Ajouter les colonnes pj_* si absentes
    for col in ENRICH_COLS:
        if col not in fieldnames:
            fieldnames.append(col)

    # Initialiser les colonnes pj_* à vide
    for row in rows:
        for col in ENRICH_COLS:
            if col not in row:
                row[col] = ""

    # ── Déduplication : construire le catalogue entreprise → résultat PJ ──
    # clé → premier résultat PJ (ou None si déjà cherché sans succès)
    cache: dict[str, dict | None] = {}
    # clé → liste des indices de lignes correspondantes
    groups: dict[str, list[int]] = {}
    # clé → raison du skip (entreprises ignorées)
    skipped: dict[str, str] = {}

    for i, row in enumerate(rows):
        company = (row.get(company_col) or "").strip()
        city    = _clean_city(row.get(city_col) or "")
        # Téléphone déjà présent dans le CSV source (FT/HW ou enrichissement précédent)
        existing_phone = (
            row.get("contact_telephone") or
            row.get("pj_telephone") or
            row.get("telephone") or
            ""
        )
        if not company:
            continue
        key = _normalize_key(company, city)
        if key in groups or key in skipped:
            # Groupe déjà enregistré — on ajoute juste l'indice
            if key in groups:
                groups[key].append(i)
            continue
        skip, reason = _should_skip(company, existing_phone)
        if skip:
            skipped[key] = f"{company} — {reason}"
        else:
            groups[key] = [i]
            cache[key] = None

    unique_companies = [
        (key, rows[indices[0]], indices)
        for key, indices in groups.items()
    ]

    log(f"\n{'═'*60}")
    log(f"Enrichissement Pages Jaunes")
    log(f"  Fichier        : {os.path.basename(input_path)}")
    log(f"  Lignes         : {len(rows)}")
    log(f"  À enrichir     : {len(unique_companies)} entreprises uniques")
    log(f"  Ignorées       : {len(skipped)} (vides, génériques ou tél. déjà dispo)")
    if skipped:
        for reason in list(skipped.values())[:5]:
            log(f"    ↳ {reason}")
        if len(skipped) > 5:
            log(f"    ↳ … et {len(skipped) - 5} autre(s)")
    log(f"{'═'*60}\n")

    enriched_count = 0

    for idx, (key, sample_row, row_indices) in enumerate(unique_companies, 1):
        company = (sample_row.get(company_col) or "").strip()
        city    = _clean_city(sample_row.get(city_col) or "")

        log(f"[{idx}/{len(unique_companies)}] {company} — {city}")

        results = search_phone(token, company, city, max_results=max_results, log=log)

        if results:
            best = results[0]
            pj_data = {
                "pj_telephone": best["pj_telephone"],
                "pj_email":     best["pj_email"],
                "pj_siret":     best["pj_siret"],
                "pj_site_web":  best["pj_site_web"],
                "pj_adresse":   f"{best['pj_adresse']} {best['pj_ville']} {best['pj_code_postal']}".strip(),
                "pj_url_fiche": best["pj_url_fiche"],
            }
            tél = best["pj_telephone"] or "(non trouvé)"
            log(f"  ✅ Téléphone : {tél}  |  SIRET : {best['pj_siret'] or '-'}")
            if best["pj_telephone"]:
                enriched_count += 1
        else:
            pj_data = {col: "" for col in ENRICH_COLS}
            log(f"  ❌ Aucun résultat Pages Jaunes")

        # Appliquer à toutes les lignes du groupe
        for i in row_indices:
            rows[i].update(pj_data)

        if idx < len(unique_companies):
            time.sleep(delay)

    # ── Écriture du CSV enrichi ──────────────────────────────────────────
    write_csv(rows, fieldnames, output_path)
    log(f"\n{'═'*60}")
    log(f"✅ CSV enrichi écrit : {output_path}")
    log(f"   {enriched_count}/{len(unique_companies)} entreprises avec un numéro trouvé")
    log(f"   {len(skipped)} entreprise(s) ignorée(s) — crédits économisés")
    log(f"{'═'*60}\n")

    return enriched_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Enrichit un CSV Scrapa avec les tél. Pages Jaunes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python3 enrich_phone.py offres_20260720.csv
  python3 enrich_phone.py offres.csv --output offres_enrichi.csv
  python3 enrich_phone.py offres.csv --delay 2.0 --max-results 5
  python3 enrich_phone.py offres.csv --col-entreprise "company" --col-ville "city"
        """,
    )
    parser.add_argument("input", help="Chemin du CSV à enrichir")
    parser.add_argument("--output", "-o", default=None,
                        help="CSV de sortie (défaut : <input>_enrichi.csv)")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Délai entre appels Apify en secondes (défaut : {DEFAULT_DELAY})")
    parser.add_argument("--max-results", type=int, default=3,
                        help="Nombre max de résultats PJ par entreprise (défaut : 3)")
    parser.add_argument("--col-entreprise", default="entreprise",
                        help="Nom de la colonne entreprise (défaut : entreprise)")
    parser.add_argument("--col-ville", default="ville",
                        help="Nom de la colonne ville (défaut : ville)")
    args = parser.parse_args()

    # Validation entrée
    if not os.path.exists(args.input):
        sys.exit(f"[ERREUR] Fichier introuvable : {args.input}")

    # Token Apify
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("[ERREUR] APIFY_TOKEN manquant dans .env")

    # Chemin de sortie
    if args.output:
        output_path = args.output
    else:
        base, ext = os.path.splitext(args.input)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"{base}_enrichi_{ts}{ext}"

    start = time.time()
    enrich_csv(
        input_path=args.input,
        output_path=output_path,
        token=token,
        delay=args.delay,
        max_results=args.max_results,
        company_col=args.col_entreprise,
        city_col=args.col_ville,
        log=print,
    )
    elapsed = time.time() - start
    print(f"Durée totale : {elapsed:.1f}s")


if __name__ == "__main__":
    main()
