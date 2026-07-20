"""
indeed_lib.py
=============
Intégration Apify — valig/indeed-jobs-scraper

Même architecture que hellowork_lib.py :
  - build_indeed_input()     → payload actor
  - flatten_indeed_job()     → dict unifié (UNIFIED_FIELDNAMES)
  - fetch_indeed_offers()    → point d'entrée principal
  - export_indeed_csv()      → export CSV

Champs bruts retournés par valig/indeed-jobs-scraper :
  key, url, title, jobUrl, datePublished, employer{name, ceoName,
  corporateWebsite, employeesCount, revenue, logoUrl, briefDescription,
  industry, address}, location{city, postalCode, countryName, latitude,
  longitude}, baseSalary{min, max, unitOfWork, currencyCode},
  attributes{}, jobTypes{}, description{text}
"""

from __future__ import annotations

import time
from typing import Optional

import requests

from export_common import export_csv_rows, build_maps_url, build_google_dirigeant_url
from hellowork_lib import run_apify_actor, _fetch_dataset, APIFY_BASE

ACTOR_INDEED = "valig/indeed-jobs-scraper"

# Pays par défaut pour Indeed France
INDEED_COUNTRY = "fr"

# Mapping datePosted UI → valeur actor
INDEED_DATE_POSTED: dict[str, str] = {
    "Toutes dates": "",
    "24h":          "1",
    "3 jours":      "3",
    "7 jours":      "7",
    "14 jours":     "14",
}


class IndeedError(Exception):
    pass


# ---------------------------------------------------------------------------
# Input builder
# ---------------------------------------------------------------------------

def build_indeed_input(
    *,
    title: str = "",
    location: str = "",
    country: str = INDEED_COUNTRY,
    limit: int = 100,
    date_posted: str = "",
) -> dict:
    """
    Construit le payload pour valig/indeed-jobs-scraper.

    Paramètres actor :
      title      : mots-clés / intitulé poste (champ "title")
      location   : ville, code postal ou "remote"
      country    : code ISO 2 lettres (défaut "fr")
      limit      : nb max de résultats (1–1000)
      date_posted: "" | "1" | "3" | "7" | "14"
    """
    payload: dict = {
        "country": country.lower(),
        "limit": max(1, min(limit, 1000)),
    }
    if title.strip():
        payload["title"] = title.strip()
    if location.strip():
        payload["location"] = location.strip()
    if date_posted.strip():
        payload["datePosted"] = date_posted.strip()
    return payload


# ---------------------------------------------------------------------------
# Flatten — mapping champs Indeed → UNIFIED_FIELDNAMES
# ---------------------------------------------------------------------------

def flatten_indeed_job(job: dict) -> dict:
    """
    Normalise un résultat brut valig/indeed-jobs-scraper vers le format unifié.
    Tous les champs de UNIFIED_FIELDNAMES sont présents (vides si absent).
    """
    employer    = job.get("employer") or {}
    location    = job.get("location") or {}
    salary      = job.get("baseSalary") or {}
    desc_obj    = job.get("description") or {}

    # Compétences : dict attributes {code: libelle}
    attrs = job.get("attributes") or {}
    skills_str = ", ".join(str(v) for v in attrs.values() if v) if attrs else ""

    # Type de contrat : dict jobTypes {code: libelle}
    job_types = job.get("jobTypes") or {}
    contrat_str = ", ".join(str(v) for v in job_types.values() if v) if job_types else ""

    # Catégories / secteurs : dict occupations {code: libelle}
    occupations = job.get("occupations") or {}
    secteur_str = ", ".join(str(v) for v in occupations.values() if v) if occupations else ""

    company_name = employer.get("name") or ""
    city         = location.get("city") or ""

    # Description : texte brut, tronqué à 800 car.
    description = (
        (desc_obj.get("text") or "")
        .replace("\n", " ")
        [:800]
    )

    return {
        # ── Identification ──────────────────────────────────────────
        "source":           "Indeed",
        "id":               job.get("key") or "",
        "intitule":         job.get("title") or "",
        "url":              job.get("url") or job.get("jobUrl") or "",
        "date_publication": job.get("datePublished") or job.get("dateOnIndeed") or "",
        "date_expiration":  job.get("expirationDate") or "",

        # ── Entreprise ──────────────────────────────────────────────
        "entreprise":               company_name,
        "entreprise_url":           employer.get("companyPageUrl") or "",
        "entreprise_logo":          employer.get("logoUrl") or "",
        "siret":                    "",
        "entreprise_description":   (employer.get("briefDescription") or "")[:400],
        "site_web_entreprise":      employer.get("corporateWebsite") or "",
        "linkedin_entreprise":      "",
        "twitter_entreprise":       "",
        "facebook_entreprise":      "",
        "annee_creation_entreprise": "",
        "chiffre_affaires_entreprise": employer.get("revenue") or "",
        "taille_entreprise":        "",
        "effectif_entreprise":      employer.get("employeesCount") or "",

        # ── Dirigeant ───────────────────────────────────────────────
        "dirigeant_nom":      employer.get("ceoName") or "",
        "dirigeant_titre":    "CEO" if employer.get("ceoName") else "",
        "dirigeant_linkedin": "",

        # ── Contact recruteur ────────────────────────────────────────
        "contact_nom":       "",
        "contact_telephone": "",
        "contact_email":     "",
        "contact_linkedin":  "",

        # ── Lieu ────────────────────────────────────────────────────
        "ville":       city,
        "region":      location.get("admin1Code") or "",
        "code_postal": location.get("postalCode") or "",
        "pays":        location.get("countryName") or "",
        "lien_maps":              build_maps_url(company_name, city),
        "lien_recherche_dirigeant": build_google_dirigeant_url(company_name, city),

        # ── Catégories ──────────────────────────────────────────────
        "secteur": secteur_str,
        "domaine": secteur_str,

        # ── Contrat & conditions ────────────────────────────────────
        "type_contrat":        contrat_str,
        "type_contrat_libelle": contrat_str,
        "teletravail":         "",
        "salaire_libelle":     _fmt_salary(salary),
        "salaire_min":         salary.get("min") or "",
        "salaire_max":         salary.get("max") or "",
        "salaire_devise":      salary.get("currencyCode") or "",
        "salaire_periode":     salary.get("unitOfWork") or "",

        # ── Candidat ────────────────────────────────────────────────
        "experience":    "",
        "formation":     "",
        "competences":   skills_str,
        "qualifications": "",

        # ── Meta ────────────────────────────────────────────────────
        "mots_cles_recherche": "",

        # ── Description ─────────────────────────────────────────────
        "description": description,
    }


def _fmt_salary(salary: dict) -> str:
    """Formate le salaire brut en libellé lisible."""
    mn   = salary.get("min")
    mx   = salary.get("max")
    unit = salary.get("unitOfWork") or ""
    cur  = salary.get("currencyCode") or "€"
    if mn and mx:
        return f"{mn}–{mx} {cur}/{unit}".strip("/").strip()
    if mn:
        return f"À partir de {mn} {cur}/{unit}".strip("/").strip()
    if mx:
        return f"Jusqu'à {mx} {cur}/{unit}".strip("/").strip()
    return ""


# ---------------------------------------------------------------------------
# Fetch principal
# ---------------------------------------------------------------------------

def fetch_indeed_offers(
    token: str,
    *,
    search_queries: list[str],
    location: str = "",
    country: str = INDEED_COUNTRY,
    max_results: int = 100,
    date_posted: str = "",
    log=print,
    check_cancel=None,
) -> list[dict]:
    """
    Récupère les offres Indeed pour une ou plusieurs requêtes.

    Pour plusieurs keywords, lance un run par keyword et déduplique.
    Retourne une liste de dicts au format UNIFIED_FIELDNAMES.
    """
    all_rows: list[dict] = []
    seen_ids: set[str] = set()
    queries = [q for q in (search_queries or []) if q.strip()] or [""]

    for i, query in enumerate(queries, 1):
        if check_cancel and check_cancel():
            log("🛑 Annulation Indeed.")
            break

        actor_input = build_indeed_input(
            title=query,
            location=location,
            country=country,
            limit=max_results,
            date_posted=date_posted,
        )
        log(f"Indeed [{i}/{len(queries)}] : {actor_input}")

        try:
            raw = run_apify_actor(
                token, ACTOR_INDEED, actor_input,
                log=log, check_cancel=check_cancel,
            )
        except Exception as exc:
            raise IndeedError(str(exc)) from exc

        added = 0
        for job in raw:
            row   = flatten_indeed_job(job)
            rid   = row.get("id") or row.get("url") or ""
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            all_rows.append(row)
            added += 1

        log(f"  → {added} offres Indeed ajoutées (total : {len(all_rows)})")

    return all_rows


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------

from hellowork_lib import UNIFIED_FIELDNAMES


def export_indeed_csv(rows: list[dict], output_path: str) -> int:
    return export_csv_rows(rows, output_path, fieldnames=UNIFIED_FIELDNAMES)
