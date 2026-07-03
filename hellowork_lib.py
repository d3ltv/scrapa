"""
hellowork_lib.py
================
Intégration Apify pour scraper HelloWork (solidcode/hellowork-scraper par défaut).
"""

import time
from typing import Optional

import requests

from export_common import export_csv_rows

ACTOR_STANDARD = "solidcode/hellowork-scraper"
ACTOR_ENRICHED = "blackfalcondata/hellowork-scraper"

APIFY_BASE = "https://api.apify.com/v2"
POLL_INTERVAL = 3
MAX_WAIT = 600


class HelloWorkError(Exception):
    pass


def verify_apify_token(token: str) -> None:
    resp = requests.get(f"{APIFY_BASE}/users/me", params={"token": token}, timeout=20)
    if resp.status_code != 200:
        raise HelloWorkError(
            f"Token Apify invalide ({resp.status_code}).\n"
            "Récupère ton token sur https://console.apify.com/account/integrations"
        )


def build_standard_input(
    *,
    search_queries: list[str],
    location: str = "",
    radius_km: Optional[int] = None,
    start_urls: list[str] | None = None,
    max_results: int = 100,
    contract_types: list[str] | None = None,
    telework_modes: list[str] | None = None,
    date_posted: str = "any",
    min_salary: Optional[int] = None,
    include_job_details: bool = True,
) -> dict:
    payload: dict = {
        "maxResults": max_results,
        "contractType": contract_types or [],
        "telework": telework_modes or [],
        "datePosted": date_posted or "any",
        "includeJobDetails": include_job_details,
    }
    if search_queries:
        payload["searchQueries"] = search_queries
    elif location.strip():
        payload["searchQueries"] = [""]
    else:
        payload["searchQueries"] = [""]
    if location.strip():
        payload["location"] = location.strip()
    if radius_km is not None and radius_km > 0:
        payload["radius"] = radius_km
    if start_urls:
        payload["startUrls"] = start_urls
    if min_salary is not None and min_salary > 0:
        payload["minSalary"] = min_salary
    return payload


def build_enriched_input(
    *,
    search_queries: list[str],
    location: str = "",
    start_urls: list[str] | None = None,
    max_results: int = 100,
    days_posted: str = "all",
    include_details: bool = True,
    include_company_profile: bool = True,
) -> dict:
    # Mots-clés optionnels : query vide = toutes les offres du lieu/période
    if search_queries:
        query = search_queries[0] if len(search_queries) == 1 else search_queries
    else:
        query = ""
    payload: dict = {
        "query": query,
        "country": "FR",
        "maxResults": max_results,
        "daysPosted": days_posted or "all",
        "includeDetails": include_details,
        "includeCompanyProfile": include_company_profile,
    }
    if location.strip():
        payload["location"] = location.strip()
    if start_urls:
        payload["startUrls"] = start_urls
    return payload


def _actor_slug(actor_id: str) -> str:
    return actor_id.replace("/", "~")


def run_apify_actor(token: str, actor_id: str, actor_input: dict, log=print, check_cancel=None) -> list[dict]:
    slug = _actor_slug(actor_id)
    log(f"Lancement Apify ({actor_id})...")

    resp = requests.post(
        f"{APIFY_BASE}/acts/{slug}/runs",
        params={"token": token},
        json=actor_input,
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise HelloWorkError(f"Impossible de lancer l'acteur Apify ({resp.status_code}) : {resp.text}")

    run = resp.json().get("data") or {}
    run_id = run.get("id")
    if not run_id:
        raise HelloWorkError("Réponse Apify inattendue (run id manquant).")

    log(f"  Run Apify démarré : {run_id}")
    elapsed = 0
    while elapsed < MAX_WAIT:
        # Check annulation
        if check_cancel and check_cancel():
            log("  🛑 Annulation — envoi de la requête d'abort à Apify...")
            try:
                requests.post(
                    f"{APIFY_BASE}/actor-runs/{run_id}/abort",
                    params={"token": token},
                    timeout=15,
                )
                log(f"  ✓ Run Apify {run_id} aborted.")
            except Exception:
                log(f"  ⚠️  Impossible d'aborter le run {run_id} — il continuera côté serveur.")
            raise HelloWorkError("Recherche annulée par l'utilisateur.")

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

        status_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}",
            params={"token": token},
            timeout=30,
        )
        status_resp.raise_for_status()
        data = status_resp.json().get("data") or {}
        status = data.get("status")
        log(f"  Statut Apify : {status} ({elapsed}s)")

        if status == "SUCCEEDED":
            dataset_id = data.get("defaultDatasetId")
            return _fetch_dataset(token, dataset_id, log)
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise HelloWorkError(f"Run Apify échoué : {status}")

    raise HelloWorkError(f"Délai dépassé ({MAX_WAIT}s) en attendant Apify.")


def _fetch_dataset(token: str, dataset_id: str, log=print) -> list[dict]:
    items: list[dict] = []
    offset = 0
    limit = 500
    while True:
        resp = requests.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            params={"token": token, "offset": offset, "limit": limit, "clean": "true"},
            timeout=60,
        )
        if resp.status_code != 200:
            raise HelloWorkError(f"Erreur lecture dataset ({resp.status_code}) : {resp.text}")
        batch = resp.json()
        if not batch:
            break
        items.extend(batch)
        log(f"  -> {len(items)} offres HelloWork récupérées...")
        if len(batch) < limit:
            break
        offset += limit
    return items


def flatten_standard_job(job: dict) -> dict:
    skills = job.get("skills") or []
    if isinstance(skills, list):
        skills_str = ", ".join(str(s) for s in skills)
    else:
        skills_str = str(skills) if skills else ""

    return {
        "source": "HelloWork",
        "id": job.get("jobId"),
        "intitule": job.get("title"),
        "entreprise": job.get("company"),
        "entreprise_url": job.get("companyUrl"),
        "entreprise_logo": job.get("companyLogo"),
        "ville": job.get("city") or job.get("location"),
        "region": job.get("region"),
        "code_postal": job.get("postalCode"),
        "pays": job.get("country"),
        "secteur": job.get("sector"),
        "domaine": job.get("occupationalCategory") or job.get("sector"),
        "type_contrat": job.get("contractType"),
        "type_contrat_libelle": job.get("employmentTypeRaw"),
        "teletravail": job.get("telework"),
        "salaire_libelle": job.get("salary"),
        "salaire_min": job.get("salaryMin"),
        "salaire_max": job.get("salaryMax"),
        "salaire_devise": job.get("salaryCurrency"),
        "salaire_periode": job.get("salaryPeriod"),
        "experience": job.get("experience"),
        "formation": job.get("education"),
        "competences": skills_str,
        "qualifications": job.get("qualifications"),
        "date_publication": job.get("datePosted"),
        "date_expiration": job.get("validThrough"),
        "description": (job.get("descriptionText") or job.get("snippet") or "").replace("\n", " ")[:800],
        "url": job.get("jobUrl"),
        "mots_cles_recherche": job.get("searchQuery"),
        "taille_entreprise": None,
        "effectif_entreprise": None,
    }


def flatten_enriched_job(job: dict) -> dict:
    skills = job.get("skills") or []
    if isinstance(skills, list):
        skills_str = ", ".join(str(s) for s in skills)
    else:
        skills_str = str(skills) if skills else ""

    headcount = job.get("headcount") or job.get("companyHeadcount") or job.get("employeeCount")
    industry = job.get("industry") or job.get("sector")

    return {
        "source": "HelloWork",
        "id": job.get("jobKey") or job.get("jobId"),
        "intitule": job.get("title"),
        "entreprise": job.get("company"),
        "entreprise_url": job.get("companyUrl"),
        "entreprise_logo": job.get("companyLogo"),
        "ville": job.get("location"),
        "region": job.get("locationRegion"),
        "code_postal": job.get("locationPostalCode"),
        "pays": job.get("locationCountry"),
        "secteur": industry,
        "domaine": job.get("occupationalCategory") or industry,
        "type_contrat": job.get("contractType"),
        "type_contrat_libelle": job.get("employmentType"),
        "teletravail": job.get("telework"),
        "salaire_libelle": job.get("salaryText") or job.get("salary"),
        "salaire_min": job.get("salaryMin"),
        "salaire_max": job.get("salaryMax"),
        "salaire_devise": job.get("salaryCurrency"),
        "salaire_periode": job.get("salaryUnit") or job.get("salaryPeriod"),
        "experience": job.get("experience") or _months_to_label(job.get("experienceMonths")),
        "formation": job.get("educationLevel") or job.get("education"),
        "competences": skills_str,
        "qualifications": job.get("qualifications"),
        "date_publication": job.get("postedAt") or job.get("datePosted"),
        "date_expiration": job.get("validThrough"),
        "description": (job.get("descriptionText") or job.get("description") or "")[:800],
        "url": job.get("canonicalUrl") or job.get("jobUrl") or job.get("sourceUrl"),
        "mots_cles_recherche": job.get("searchQuery"),
        "taille_entreprise": headcount,
        "effectif_entreprise": job.get("companySizeLabel") or job.get("headcountLabel"),
    }


def _months_to_label(months) -> str:
    if months is None:
        return ""
    try:
        m = int(months)
        if m < 12:
            return f"{m} mois"
        years = m // 12
        return f"{years} an{'s' if years > 1 else ''}"
    except (TypeError, ValueError):
        return str(months)


def fetch_hellowork_offers(
    token: str,
    *,
    enriched_mode: bool = False,
    search_queries: list[str],
    location: str = "",
    radius_km: Optional[int] = None,
    start_urls: list[str] | None = None,
    max_results: int = 100,
    contract_types: list[str] | None = None,
    telework_modes: list[str] | None = None,
    date_posted: str = "any",
    days_posted: str = "all",
    min_salary: Optional[int] = None,
    include_job_details: bool = True,
    include_company_profile: bool = True,
    log=print,
    check_cancel=None,
) -> list[dict]:
    if enriched_mode:
        actor_input = build_enriched_input(
            search_queries=search_queries,
            location=location,
            start_urls=start_urls,
            max_results=max_results,
            days_posted=days_posted,
            include_details=include_job_details,
            include_company_profile=include_company_profile,
        )
        actor_id = ACTOR_ENRICHED
        flatten = flatten_enriched_job
    else:
        actor_input = build_standard_input(
            search_queries=search_queries,
            location=location,
            radius_km=radius_km,
            start_urls=start_urls,
            max_results=max_results,
            contract_types=contract_types,
            telework_modes=telework_modes,
            date_posted=date_posted,
            min_salary=min_salary,
            include_job_details=include_job_details,
        )
        actor_id = ACTOR_STANDARD
        flatten = flatten_standard_job

    log(f"Paramètres HelloWork : {actor_input}")
    raw_jobs = run_apify_actor(token, actor_id, actor_input, log=log, check_cancel=check_cancel)
    return [flatten(j) for j in raw_jobs]


UNIFIED_FIELDNAMES = [
    "intitule", "entreprise", "ville", "url",
    "source", "id", "entreprise_url", "region",
    "code_postal", "secteur", "domaine", "type_contrat", "teletravail",
    "salaire_libelle", "salaire_min", "salaire_max", "experience", "formation",
    "competences", "taille_entreprise", "effectif_entreprise",
    "date_publication", "description",
]


def export_hellowork_csv(rows: list[dict], output_path: str) -> int:
    return export_csv_rows(rows, output_path, fieldnames=UNIFIED_FIELDNAMES)
