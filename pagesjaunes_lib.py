"""
pagesjaunes_lib.py
==================
Intégration Apify — scrapersdelight/pagesjaunes-france-scraper

Permet d'enrichir une entreprise (nom + ville) avec son numéro de téléphone,
email, SIRET/SIREN et site web depuis Pages Jaunes.
"""

import time
from typing import Optional
import requests

ACTOR_ID = "scrapersdelight~pagesjaunes-france-scraper"
APIFY_BASE = "https://api.apify.com/v2"
POLL_INTERVAL = 4
MAX_WAIT = 300  # 5 min max par run


class PagesJaunesError(Exception):
    pass


# ---------------------------------------------------------------------------
# Input builder
# ---------------------------------------------------------------------------

def build_pj_input(
    what: str,
    where: str = "",
    max_items: int = 5,
    max_pages: int = 1,
    include_details: bool = True,
) -> dict:
    """
    Construit le payload d'entrée pour l'actor Pages Jaunes.

    - what  : activité / nom d'entreprise (champ "quoi")
    - where : ville ou code postal (champ "où")
    - max_items    : nombre max de résultats (garde 5 par défaut pour l'enrichissement)
    - max_pages    : pages de résultats à parcourir
    - include_details : ouvre la fiche pour récupérer email, SIRET, site web
    """
    payload: dict = {
        "maxItems": max_items,
        "maxPages": max_pages,
        "includeCompanyDetails": include_details,
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
            "apifyProxyCountry": "FR",
        },
        "requestConcurrency": 3,
    }
    if what.strip():
        payload["what"] = what.strip()
    if where.strip():
        payload["where"] = where.strip()
    return payload


# ---------------------------------------------------------------------------
# Apify runner (réutilise le pattern de hellowork_lib)
# ---------------------------------------------------------------------------

def _run_actor(token: str, actor_input: dict, log=print) -> list[dict]:
    log(f"  Lancement Apify Pages Jaunes ({ACTOR_ID})…")
    resp = requests.post(
        f"{APIFY_BASE}/acts/{ACTOR_ID}/runs",
        params={"token": token},
        json=actor_input,
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise PagesJaunesError(
            f"Impossible de lancer l'actor ({resp.status_code}) : {resp.text}"
        )

    run = resp.json().get("data") or {}
    run_id = run.get("id")
    if not run_id:
        raise PagesJaunesError("Réponse Apify inattendue (run id manquant).")

    log(f"  Run démarré : {run_id}")
    elapsed = 0
    while elapsed < MAX_WAIT:
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
        log(f"  Statut : {status} ({elapsed}s)")

        if status == "SUCCEEDED":
            dataset_id = data.get("defaultDatasetId")
            return _fetch_dataset(token, dataset_id, log)
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise PagesJaunesError(f"Run Apify échoué : {status}")

    raise PagesJaunesError(f"Délai dépassé ({MAX_WAIT}s).")


def _fetch_dataset(token: str, dataset_id: str, log=print) -> list[dict]:
    items: list[dict] = []
    offset, limit = 0, 200
    while True:
        resp = requests.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            params={"token": token, "offset": offset, "limit": limit, "clean": "true"},
            timeout=60,
        )
        if resp.status_code != 200:
            raise PagesJaunesError(f"Erreur lecture dataset ({resp.status_code}) : {resp.text}")
        batch = resp.json()
        if not batch:
            break
        items.extend(batch)
        log(f"  → {len(items)} résultat(s) Pages Jaunes récupéré(s)…")
        if len(batch) < limit:
            break
        offset += limit
    return items


# ---------------------------------------------------------------------------
# Flatten
# ---------------------------------------------------------------------------

def flatten_pj_result(item: dict) -> dict:
    """
    Normalise un résultat Pages Jaunes brut en dict plat.
    Structure retournée par scrapersdelight/pagesjaunes-france-scraper :
      name, phone, phone_display, email, street, city, postal_code,
      website, siret, siren, naf_code, rating, review_count, detail_url, ...
    """
    # Téléphone : préférer le format international (+33...) sinon le display
    phone = (
        item.get("phone")
        or (item.get("phones") or [None])[0]
        or item.get("phone_display")
        or item.get("phoneNumber")
        or item.get("tel")
        or ""
    )

    return {
        "pj_nom":          item.get("name") or item.get("title") or "",
        "pj_telephone":    phone or "",
        "pj_telephone_display": item.get("phone_display") or "",
        "pj_email":        item.get("email") or "",
        "pj_site_web":     item.get("website") or item.get("url") or "",
        "pj_adresse":      item.get("street") or item.get("address") or "",
        "pj_ville":        item.get("city") or "",
        "pj_code_postal":  item.get("postal_code") or item.get("postalCode") or "",
        "pj_siret":        item.get("siret") or "",
        "pj_siren":        item.get("siren") or "",
        "pj_naf":          item.get("naf_code") or item.get("nafCode") or item.get("ape") or "",
        "pj_categorie":    item.get("category") or "",
        "pj_note":         item.get("rating") or "",
        "pj_nb_avis":      item.get("review_count") or item.get("reviewCount") or "",
        "pj_url_fiche":    item.get("detail_url") or item.get("pageUrl") or item.get("detailUrl") or "",
        "pj_raw":          item,  # données brutes pour debug
    }


# ---------------------------------------------------------------------------
# Fonction principale d'enrichissement
# ---------------------------------------------------------------------------

def search_phone(
    token: str,
    company_name: str,
    city: str = "",
    max_results: int = 5,
    log=print,
) -> list[dict]:
    """
    Cherche une entreprise sur Pages Jaunes et retourne les résultats aplatis.

    Args:
        token        : token Apify
        company_name : nom de l'entreprise à chercher
        city         : ville (améliore la précision)
        max_results  : nombre max de fiches retournées
        log          : fonction de log (print par défaut)

    Returns:
        Liste de dicts avec pj_telephone, pj_email, pj_siret, etc.
        Liste vide si aucun résultat ou erreur.
    """
    actor_input = build_pj_input(
        what=company_name,
        where=city,
        max_items=max_results,
        max_pages=1,
        include_details=True,
    )
    log(f"Pages Jaunes — recherche : '{company_name}' à '{city}'")
    log(f"  Input : {actor_input}")

    try:
        raw_results = _run_actor(token, actor_input, log=log)
    except PagesJaunesError as e:
        log(f"  ⚠️  Erreur : {e}")
        return []

    results = [flatten_pj_result(r) for r in raw_results]
    log(f"  → {len(results)} résultat(s) trouvé(s)")
    for r in results:
        log(f"     • {r['pj_nom']} | tél: {r['pj_telephone']} | email: {r['pj_email']} | SIRET: {r['pj_siret']}")
    return results


def enrich_row_with_phone(
    token: str,
    row: dict,
    company_key: str = "entreprise",
    city_key: str = "ville",
    log=print,
) -> dict:
    """
    Enrichit un dict (ligne CSV) avec le premier résultat Pages Jaunes trouvé.
    Ajoute les colonnes pj_* directement dans le dict.

    Retourne le dict enrichi (modifié en place).
    """
    company = (row.get(company_key) or "").strip()
    city    = (row.get(city_key) or "").strip()

    # Nettoyer la ville : enlever les codes postaux entre parenthèses
    # ex: "Tours (37)" → "Tours"
    import re
    city_clean = re.sub(r"\s*\(.*?\)", "", city).strip()

    if not company:
        row["pj_telephone"] = ""
        row["pj_email"] = ""
        row["pj_siret"] = ""
        row["pj_site_web"] = ""
        return row

    results = search_phone(token, company, city_clean, max_results=3, log=log)

    if results:
        best = results[0]
        row["pj_telephone"] = best["pj_telephone"]
        row["pj_email"]     = best["pj_email"]
        row["pj_siret"]     = best["pj_siret"] or row.get("siret", "")
        row["pj_site_web"]  = best["pj_site_web"] or row.get("entreprise_url", "")
        row["pj_adresse"]   = best["pj_adresse"]
        row["pj_url_fiche"] = best["pj_url_fiche"]
    else:
        row["pj_telephone"] = ""
        row["pj_email"]     = ""
        row["pj_siret"]     = ""
        row["pj_site_web"]  = ""
        row["pj_adresse"]   = ""
        row["pj_url_fiche"] = ""

    return row
