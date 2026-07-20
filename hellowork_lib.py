"""
hellowork_lib.py
================
Intégration Apify pour scraper HelloWork (solidcode/hellowork-scraper par défaut).
"""

import time
from typing import Optional

import requests

from export_common import export_csv_rows, build_maps_url, build_google_dirigeant_url

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

    # Contact / recruteur
    hiring = job.get("hiringOrganization") or {}
    contact_name = (
        job.get("contactName")
        or job.get("recruiterName")
        or job.get("hiringManagerName")
        or hiring.get("name")
        or ""
    )
    contact_phone = (
        job.get("contactPhone")
        or job.get("phone")
        or job.get("recruiterPhone")
        or hiring.get("telephone")
        or ""
    )
    contact_email = (
        job.get("contactEmail")
        or job.get("email")
        or job.get("recruiterEmail")
        or hiring.get("email")
        or ""
    )

    return {
        "source": "HelloWork",
        "id": job.get("jobId") or job.get("id"),
        "intitule": job.get("title"),
        "entreprise": job.get("company"),
        "entreprise_url": job.get("companyUrl"),
        "entreprise_logo": job.get("companyLogo"),
        "siret": job.get("companySiret") or job.get("siret") or "",
        "entreprise_description": (job.get("companyDescription") or "")[:400],
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
        # Contact recruteur
        "contact_nom": contact_name,
        "contact_telephone": contact_phone,
        "contact_email": contact_email,
        "lien_maps": build_maps_url(job.get("company"), job.get("city") or job.get("location")),
        "lien_recherche_dirigeant": build_google_dirigeant_url(job.get("company"), job.get("city") or job.get("location")),
    }


def flatten_enriched_job(job: dict) -> dict:
    skills = job.get("skills") or []
    if isinstance(skills, list):
        skills_str = ", ".join(str(s) for s in skills)
    else:
        skills_str = str(skills) if skills else ""

    headcount = job.get("headcount") or job.get("companyHeadcount") or job.get("employeeCount")
    industry = job.get("industry") or job.get("sector")

    # Dirigeants / contact — on cherche dans plusieurs structures possibles
    ceo_name = (
        job.get("ceo")
        or job.get("ceoName")
        or job.get("directorName")
        or job.get("presidentName")
        or job.get("leaderName")
        or ""
    )
    contact_name = (
        job.get("contactName")
        or job.get("recruiterName")
        or job.get("hiringManagerName")
        or job.get("managerName")
        or ""
    )
    contact_phone = (
        job.get("contactPhone")
        or job.get("phone")
        or job.get("companyPhone")
        or job.get("recruiterPhone")
        or job.get("phoneNumber")
        or ""
    )
    contact_email = (
        job.get("contactEmail")
        or job.get("email")
        or job.get("companyEmail")
        or job.get("recruiterEmail")
        or ""
    )

    # Réseaux sociaux entreprise
    linkedin_company = (
        job.get("companyLinkedinUrl")
        or job.get("linkedinUrl")
        or job.get("companyLinkedin")
        or ""
    )
    linkedin_contact = (
        job.get("contactLinkedinUrl")
        or job.get("recruiterLinkedinUrl")
        or ""
    )
    twitter = job.get("companyTwitter") or job.get("twitter") or ""
    facebook = job.get("companyFacebook") or job.get("facebook") or ""
    website = job.get("companyWebsite") or job.get("website") or ""

    return {
        "source": "HelloWork",
        "id": job.get("jobKey") or job.get("jobId"),
        "intitule": job.get("title"),

        # Entreprise
        "entreprise": job.get("company"),
        "entreprise_url": job.get("companyUrl"),
        "entreprise_logo": job.get("companyLogo"),
        "siret": job.get("companySiret") or job.get("siret") or "",
        "entreprise_description": (job.get("companyDescription") or job.get("aboutCompany") or "")[:400],
        "site_web_entreprise": website,
        "linkedin_entreprise": linkedin_company,
        "twitter_entreprise": twitter,
        "facebook_entreprise": facebook,
        "annee_creation_entreprise": job.get("foundedYear") or job.get("companyFoundedYear") or "",
        "chiffre_affaires_entreprise": job.get("revenue") or job.get("companyRevenue") or "",
        "secteur_entreprise": industry,

        # Dirigeant
        "dirigeant_nom": ceo_name,
        "dirigeant_titre": job.get("ceoTitle") or job.get("leaderTitle") or "",
        "dirigeant_linkedin": job.get("ceoLinkedinUrl") or job.get("leaderLinkedinUrl") or "",

        # Lieu
        "ville": job.get("location"),
        "region": job.get("locationRegion"),
        "code_postal": job.get("locationPostalCode"),
        "pays": job.get("locationCountry"),

        # Catégories
        "secteur": industry,
        "domaine": job.get("occupationalCategory") or industry,

        # Contrat
        "type_contrat": job.get("contractType"),
        "type_contrat_libelle": job.get("employmentType"),
        "teletravail": job.get("telework"),

        # Salaire
        "salaire_libelle": job.get("salaryText") or job.get("salary"),
        "salaire_min": job.get("salaryMin"),
        "salaire_max": job.get("salaryMax"),
        "salaire_devise": job.get("salaryCurrency"),
        "salaire_periode": job.get("salaryUnit") or job.get("salaryPeriod"),

        # Candidat
        "experience": job.get("experience") or _months_to_label(job.get("experienceMonths")),
        "formation": job.get("educationLevel") or job.get("education"),
        "competences": skills_str,
        "qualifications": job.get("qualifications"),

        # Dates
        "date_publication": job.get("postedAt") or job.get("datePosted"),
        "date_expiration": job.get("validThrough"),

        # Description
        "description": (job.get("descriptionText") or job.get("description") or "")[:800],
        "url": job.get("canonicalUrl") or job.get("jobUrl") or job.get("sourceUrl"),
        "mots_cles_recherche": job.get("searchQuery"),

        # Taille entreprise
        "taille_entreprise": headcount,
        "effectif_entreprise": job.get("companySizeLabel") or job.get("headcountLabel"),

        # Contact recruteur
        "contact_nom": contact_name,
        "contact_telephone": contact_phone,
        "contact_email": contact_email,
        "contact_linkedin": linkedin_contact,

        # Liens
        "lien_maps": build_maps_url(job.get("company"), job.get("location")),
        "lien_recherche_dirigeant": build_google_dirigeant_url(job.get("company"), job.get("location")),
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


# ---------------------------------------------------------------------------
# Résolution département → nom lisible pour HelloWork
# ---------------------------------------------------------------------------

# Mapping numéro de département → nom officiel
_DEPARTEMENTS: dict[str, str] = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence",
    "05": "Hautes-Alpes", "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes",
    "09": "Ariège", "10": "Aube", "11": "Aude", "12": "Aveyron",
    "13": "Bouches-du-Rhône", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Corrèze", "2A": "Corse-du-Sud",
    "2B": "Haute-Corse", "21": "Côte-d'Or", "22": "Côtes-d'Armor", "23": "Creuse",
    "24": "Dordogne", "25": "Doubs", "26": "Drôme", "27": "Eure",
    "28": "Eure-et-Loir", "29": "Finistère", "30": "Gard", "31": "Haute-Garonne",
    "32": "Gers", "33": "Gironde", "34": "Hérault", "35": "Ille-et-Vilaine",
    "36": "Indre", "37": "Indre-et-Loire", "38": "Isère", "39": "Jura",
    "40": "Landes", "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire",
    "44": "Loire-Atlantique", "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne",
    "48": "Lozère", "49": "Maine-et-Loire", "50": "Manche", "51": "Marne",
    "52": "Haute-Marne", "53": "Mayenne", "54": "Meurthe-et-Moselle", "55": "Meuse",
    "56": "Morbihan", "57": "Moselle", "58": "Nièvre", "59": "Nord",
    "60": "Oise", "61": "Orne", "62": "Pas-de-Calais", "63": "Puy-de-Dôme",
    "64": "Pyrénées-Atlantiques", "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales",
    "67": "Bas-Rhin", "68": "Haut-Rhin", "69": "Rhône", "70": "Haute-Saône",
    "71": "Saône-et-Loire", "72": "Sarthe", "73": "Savoie", "74": "Haute-Savoie",
    "75": "Paris", "76": "Seine-Maritime", "77": "Seine-et-Marne",
    "78": "Yvelines", "79": "Deux-Sèvres", "80": "Somme", "81": "Tarn",
    "82": "Tarn-et-Garonne", "83": "Var", "84": "Vaucluse", "85": "Vendée",
    "86": "Vienne", "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne",
    "90": "Territoire de Belfort", "91": "Essonne", "92": "Hauts-de-Seine",
    "93": "Seine-Saint-Denis", "94": "Val-de-Marne", "95": "Val-d'Oise",
    "971": "Guadeloupe", "972": "Martinique", "973": "Guyane",
    "974": "La Réunion", "976": "Mayotte",
}

import re as _re

def resolve_hw_location(text: str) -> tuple[str, bool]:
    """
    Résout une saisie utilisateur en nom de lieu compatible HelloWork.

    - Si text est un code de département (ex: "37", "2A", "971") → retourne le nom du département.
    - Si text est un code postal (ex: "37000", "75013") → extrait le numéro de département et retourne son nom.
    - Sinon → retourne text inchangé.

    Retourne (location_string, was_resolved) où was_resolved=True si une conversion a eu lieu.
    """
    t = text.strip()
    if not t:
        return t, False

    # Code postal 5 chiffres → extraire le département
    if _re.fullmatch(r"\d{5}", t):
        # Corse : 20xxx → 2A/2B selon la sous-préfecture, on utilise "Corse" générique
        if t.startswith("20"):
            sub = int(t[2])
            dept_code = "2B" if sub >= 2 else "2A"
        # DOM : 971xx – 976xx
        elif t[:3] in _DEPARTEMENTS:
            dept_code = t[:3]
        else:
            dept_code = t[:2].lstrip("0") or t[:2]
            # Normaliser en 2 chiffres pour les <10
            dept_code = t[:2]
        name = _DEPARTEMENTS.get(dept_code) or _DEPARTEMENTS.get(dept_code.lstrip("0"))
        if name:
            return name, True
        return t, False

    # Code département seul : 1–2 chiffres ou 2A/2B ou 3 chiffres DOM
    t_upper = t.upper()
    if t_upper in _DEPARTEMENTS:
        return _DEPARTEMENTS[t_upper], True
    # Numéro sans zéro initial ex: "7" → "07"
    if _re.fullmatch(r"\d{1,2}", t):
        padded = t.zfill(2)
        if padded in _DEPARTEMENTS:
            return _DEPARTEMENTS[padded], True
    # DOM 3 chiffres ex: "971"
    if _re.fullmatch(r"97[1-6]", t):
        if t in _DEPARTEMENTS:
            return _DEPARTEMENTS[t], True

    return t, False


UNIFIED_FIELDNAMES = [
    # Identification
    "source", "id", "intitule", "url", "date_publication", "date_expiration",
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
    "ville", "region", "code_postal", "pays", "lien_maps", "lien_recherche_dirigeant",
    # Catégories
    "secteur", "domaine",
    # Contrat & conditions
    "type_contrat", "type_contrat_libelle", "teletravail",
    "salaire_libelle", "salaire_min", "salaire_max", "salaire_devise", "salaire_periode",
    # Candidat
    "experience", "formation", "competences", "qualifications",
    # Meta
    "mots_cles_recherche",
    # Description
    "description",
]


def export_hellowork_csv(rows: list[dict], output_path: str) -> int:
    return export_csv_rows(rows, output_path, fieldnames=UNIFIED_FIELDNAMES)
