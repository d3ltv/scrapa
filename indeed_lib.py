"""
indeed_lib.py
=============
Intégration Apify — valig/indeed-jobs-scraper

Même architecture que hellowork_lib.py :
  - build_indeed_input()     → payload actor
  - flatten_indeed_job()     → dict unifié (UNIFIED_FIELDNAMES)
  - fetch_indeed_offers()    → point d'entrée principal (groupes de mots-clés,
                               déduplication, annulation, filtres contrats)
  - export_indeed_csv()      → export CSV

Champs bruts retournés par valig/indeed-jobs-scraper :
  key, url, title, jobUrl, datePublished, dateOnIndeed, expirationDate,
  employer{name, ceoName, corporateWebsite, employeesCount, revenue,
  logoUrl, briefDescription, industry, companyPageUrl},
  location{city, postalCode, countryName, admin1Code, admin2Code,
  latitude, longitude},
  baseSalary{min, max, unitOfWork, currencyCode},
  attributes{code: libelle},   ← compétences / mots-clés
  jobTypes{code: libelle},     ← type de contrat
  occupations{code: libelle},  ← catégories métiers
  description{text, html}
"""

from __future__ import annotations

import re as _re
import unicodedata
from typing import Optional

from export_common import export_csv_rows, build_maps_url, build_google_dirigeant_url
from hellowork_lib import run_apify_actor, UNIFIED_FIELDNAMES

ACTOR_INDEED = "valig/indeed-jobs-scraper"
INDEED_COUNTRY = "fr"


class IndeedError(Exception):
    pass


# ---------------------------------------------------------------------------
# Constantes UI
# ---------------------------------------------------------------------------

INDEED_DATE_POSTED: dict[str, str] = {
    "Toutes dates": "",
    "24h":          "1",
    "3 jours":      "3",
    "7 jours":      "7",
    "14 jours":     "14",
}

# Types de contrats — libellés Indeed (jobTypes) → code normalisé
# Indeed renvoie des libellés en français : "Temps plein", "CDI", "Stage", etc.
_CONTRAT_NORMALIZE: dict[str, str] = {
    # Temps plein / partiel → on garde le libellé brut (pas de code Indeed)
    "temps plein":      "Temps plein",
    "temps partiel":    "Temps partiel",
    # Types de contrat
    "cdi":              "CDI",
    "cdd":              "CDD",
    "alternance":       "Alternance",
    "stage":            "Stage",
    "interim":          "Intérim",
    "intérim":          "Intérim",
    "freelance":        "Freelance",
    "indépendant":      "Freelance",
    "independant":      "Freelance",
    "contrat pro":      "Alternance",
    "apprentissage":    "Alternance",
    "en présentiel":    "",   # attribut lieu, pas un contrat → filtrer
    "remote":           "",
    "télétravail":      "",
    "teletravail":      "",
}

# Contrats proposés dans l'UI (label affiché, code à rechercher dans jobTypes)
INDEED_CONTRATS: list[tuple[str, str]] = [
    ("CDI",        "CDI"),
    ("CDD",        "CDD"),
    ("Alternance", "Alternance"),
    ("Stage",      "Stage"),
    ("Intérim",    "Intérim"),
    ("Freelance",  "Freelance"),
]


# ---------------------------------------------------------------------------
# Résolution département (même logique que hellowork_lib)
# ---------------------------------------------------------------------------

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


def resolve_indeed_location(text: str) -> tuple[str, bool]:
    """
    Résout un code département / code postal en nom de ville pour Indeed.
    Même logique que resolve_hw_location dans hellowork_lib.
    Retourne (location_string, was_resolved).
    """
    t = text.strip()
    if not t:
        return t, False

    if _re.fullmatch(r"\d{5}", t):
        if t.startswith("20"):
            sub = int(t[2])
            dept_code = "2B" if sub >= 2 else "2A"
        elif t[:3] in _DEPARTEMENTS:
            dept_code = t[:3]
        else:
            dept_code = t[:2]
        name = _DEPARTEMENTS.get(dept_code) or _DEPARTEMENTS.get(dept_code.lstrip("0"))
        return (name, True) if name else (t, False)

    t_upper = t.upper()
    if t_upper in _DEPARTEMENTS:
        return _DEPARTEMENTS[t_upper], True
    if _re.fullmatch(r"\d{1,2}", t):
        padded = t.zfill(2)
        if padded in _DEPARTEMENTS:
            return _DEPARTEMENTS[padded], True
    if _re.fullmatch(r"97[1-6]", t):
        if t in _DEPARTEMENTS:
            return _DEPARTEMENTS[t], True

    return t, False


# ---------------------------------------------------------------------------
# Normalisation du type de contrat
# ---------------------------------------------------------------------------

def _normalize_contrat(raw: str) -> str:
    """
    Normalise un libellé Indeed (ex: 'Temps plein', 'En présentiel')
    → libellé propre ou chaîne vide si non pertinent.
    """
    key = (raw or "").lower().strip()
    # Supprimer les accents pour la comparaison
    key_no_acc = "".join(
        c for c in unicodedata.normalize("NFD", key)
        if unicodedata.category(c) != "Mn"
    )
    for pattern, normalized in _CONTRAT_NORMALIZE.items():
        pattern_no_acc = "".join(
            c for c in unicodedata.normalize("NFD", pattern)
            if unicodedata.category(c) != "Mn"
        )
        if key_no_acc == pattern_no_acc:
            return normalized
    # Pas dans la liste → retourner tel quel (capitalisé)
    return raw.strip() if raw.strip() else ""


def _extract_contrats(job_types: dict, attributes: dict) -> tuple[str, str]:
    """
    Extrait le type de contrat et le mode de travail depuis jobTypes + attributes.
    Retourne (type_contrat_libelle, teletravail).
    """
    contrats = []
    teletravail = ""

    # jobTypes : {"CF3CP": "Temps plein", ...}
    for v in (job_types or {}).values():
        norm = _normalize_contrat(str(v))
        if norm in ("", "Temps plein", "Temps partiel"):
            continue  # pas un type de contrat juridique
        if norm not in contrats:
            contrats.append(norm)

    # attributes : peut aussi contenir des types de contrat ou infos télétravail
    for v in (attributes or {}).values():
        v_str = str(v or "").strip()
        v_lower = v_str.lower()
        if "télétravail" in v_lower or "teletravail" in v_lower or "remote" in v_lower:
            teletravail = v_str
        elif "présentiel" in v_lower or "presentiel" in v_lower:
            teletravail = "Non"

    return ", ".join(contrats) if contrats else "", teletravail


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
      title      : mots-clés / intitulé poste
      location   : ville, code postal ou "remote"
      country    : code ISO 2 lettres (défaut "fr")
      limit      : nb max de résultats (1–1000, cap actor)
      date_posted: "" | "1" | "3" | "7" | "14"
    """
    payload: dict = {
        "country": country.lower(),
        "limit":   max(1, min(limit, 1000)),
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

def flatten_indeed_job(job: dict, search_query: str = "") -> dict:
    """
    Normalise un résultat brut valig/indeed-jobs-scraper vers le format unifié.
    Tous les 48 champs de UNIFIED_FIELDNAMES sont présents.
    """
    employer   = job.get("employer") or {}
    loc        = job.get("location") or {}
    salary     = job.get("baseSalary") or {}
    desc_obj   = job.get("description") or {}
    attrs      = job.get("attributes") or {}
    job_types  = job.get("jobTypes") or {}
    occupations = job.get("occupations") or {}

    company_name = employer.get("name") or ""
    city         = loc.get("city") or ""

    # ── Compétences : attributes (hors contrat/lieu) ─────────────────
    _skip_attrs = {"temps plein", "temps partiel", "présentiel", "presentiel",
                   "télétravail", "teletravail", "remote"}
    skills_list = [
        str(v) for v in attrs.values()
        if v and str(v).lower().strip() not in _skip_attrs
        and "présentiel" not in str(v).lower()
        and "teletravail" not in str(v).lower().replace("é", "e")
    ]
    skills_str = ", ".join(skills_list)

    # ── Type de contrat + télétravail ────────────────────────────────
    contrat_str, teletravail_str = _extract_contrats(job_types, attrs)

    # Temps plein / partiel depuis jobTypes (attribut de durée, pas juridique)
    duree_str = ""
    for v in job_types.values():
        v_l = str(v or "").lower().strip()
        if v_l in ("temps plein", "temps partiel"):
            duree_str = str(v)
            break

    # ── Secteurs / catégories métiers ────────────────────────────────
    secteur_list = [str(v) for v in occupations.values() if v]
    secteur_str = ", ".join(secteur_list)

    # ── Salaire ──────────────────────────────────────────────────────
    sal_min  = salary.get("min") or ""
    sal_max  = salary.get("max") or ""
    sal_cur  = salary.get("currencyCode") or "EUR"
    sal_per  = salary.get("unitOfWork") or ""

    # ── Description brute ────────────────────────────────────────────
    description = (
        (desc_obj.get("text") or "")
        .replace("\n", " ")
        .strip()
        [:800]
    )

    return {
        # ── Identification ──────────────────────────────────────────
        "source":            "Indeed",
        "id":                job.get("key") or "",
        "intitule":          job.get("title") or "",
        "url":               job.get("url") or job.get("jobUrl") or "",
        "date_publication":  job.get("datePublished") or job.get("dateOnIndeed") or "",
        "date_expiration":   job.get("expirationDate") or "",

        # ── Entreprise ──────────────────────────────────────────────
        "entreprise":                  company_name,
        "entreprise_url":              employer.get("companyPageUrl") or "",
        "entreprise_logo":             employer.get("logoUrl") or "",
        "siret":                       "",
        "entreprise_description":      (employer.get("briefDescription") or "")[:400],
        "site_web_entreprise":         employer.get("corporateWebsite") or "",
        "linkedin_entreprise":         "",
        "twitter_entreprise":          "",
        "facebook_entreprise":         "",
        "annee_creation_entreprise":   "",
        "chiffre_affaires_entreprise": employer.get("revenue") or "",
        "taille_entreprise":           "",
        "effectif_entreprise":         employer.get("employeesCount") or "",

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
        "region":      loc.get("admin1Code") or "",
        "code_postal": loc.get("postalCode") or "",
        "pays":        loc.get("countryName") or "",
        "lien_maps":               build_maps_url(company_name, city),
        "lien_recherche_dirigeant": build_google_dirigeant_url(company_name, city),

        # ── Catégories ──────────────────────────────────────────────
        "secteur": secteur_str,
        "domaine": secteur_str,

        # ── Contrat & conditions ─────────────────────────────────────
        "type_contrat":         contrat_str,
        "type_contrat_libelle": contrat_str or duree_str,
        "teletravail":          teletravail_str,
        "salaire_libelle":      _fmt_salary(salary),
        "salaire_min":          sal_min,
        "salaire_max":          sal_max,
        "salaire_devise":       sal_cur,
        "salaire_periode":      sal_per,

        # ── Candidat ─────────────────────────────────────────────────
        "experience":    "",
        "formation":     "",
        "competences":   skills_str,
        "qualifications": "",

        # ── Meta ─────────────────────────────────────────────────────
        "mots_cles_recherche": search_query,

        # ── Description ──────────────────────────────────────────────
        "description": description,
    }


def _fmt_salary(salary: dict) -> str:
    mn  = salary.get("min")
    mx  = salary.get("max")
    per = salary.get("unitOfWork") or ""
    cur = salary.get("currencyCode") or "EUR"
    per_label = {"YEAR": "/an", "MONTH": "/mois", "WEEK": "/sem.", "HOUR": "/h"}.get(per, f"/{per}" if per else "")
    if mn and mx:
        return f"{mn:,}–{mx:,} {cur}{per_label}".replace(",", " ")
    if mn:
        return f"À partir de {mn:,} {cur}{per_label}".replace(",", " ")
    if mx:
        return f"Jusqu'à {mx:,} {cur}{per_label}".replace(",", " ")
    return ""


# ---------------------------------------------------------------------------
# Filtre contrats post-fetch
# ---------------------------------------------------------------------------

def _matches_contract_filter(row: dict, contract_filter: list[str]) -> bool:
    """
    Vérifie si une offre correspond aux types de contrats sélectionnés.
    Retourne True si pas de filtre ou si au moins un contrat correspond.
    """
    if not contract_filter:
        return True
    contrat = (row.get("type_contrat") or "").lower()
    for c in contract_filter:
        if c.lower() in contrat:
            return True
    return False


# ---------------------------------------------------------------------------
# Fetch principal — groupes de mots-clés, déduplification, annulation
# ---------------------------------------------------------------------------

def fetch_indeed_offers(
    token: str,
    *,
    search_queries: list[str],
    location: str = "",
    country: str = INDEED_COUNTRY,
    max_results: int = 100,
    date_posted: str = "",
    contract_types: list[str] | None = None,
    group_size: int = 1,
    log=print,
    check_cancel=None,
    check_stop=None,
) -> list[dict]:
    """
    Récupère les offres Indeed.

    - check_cancel : lambda → bool — interrompt le run Apify en cours (abort)
    - check_stop   : lambda → bool — finit le run en cours puis s'arrête (arrêt doux)
    """
    all_rows: list[dict] = []
    seen_ids: set[str] = set()

    queries = [q.strip() for q in (search_queries or []) if q.strip()] or [""]

    if group_size > 1:
        groups = [
            " ".join(queries[i:i + group_size])
            for i in range(0, len(queries), group_size)
        ]
    else:
        groups = queries

    total_groups = len(groups)
    log(f"Indeed : {len(queries)} mot(s)-clé(s) → {total_groups} run(s) Apify")

    for gi, query in enumerate(groups, 1):
        if check_cancel and check_cancel():
            log("🛑 Annulation Indeed.")
            break
        # Arrêt doux : on ne démarre pas de nouveau run si demandé
        if check_stop and check_stop() and gi > 1:
            log(f"⏹  Arrêt Indeed après {gi-1}/{total_groups} groupe(s).")
            break
        if len(all_rows) >= max_results:
            log(f"  ✓ Limite {max_results} atteinte — runs restants ignorés.")
            break

        remaining = max_results - len(all_rows)
        cap = min(remaining, 1000)

        actor_input = build_indeed_input(
            title=query,
            location=location,
            country=country,
            limit=cap,
            date_posted=date_posted,
        )
        log(f"  [{gi}/{total_groups}] Indeed : {actor_input}")

        try:
            raw = run_apify_actor(
                token, ACTOR_INDEED, actor_input,
                log=log, check_cancel=check_cancel,
            )
        except Exception as exc:
            raise IndeedError(str(exc)) from exc

        added = 0
        for job in raw:
            row = flatten_indeed_job(job, search_query=query)
            rid = row.get("id") or row.get("url") or ""
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            if not _matches_contract_filter(row, contract_types or []):
                continue
            all_rows.append(row)
            added += 1

        log(f"  → {added} offres ajoutées (total : {len(all_rows)})")

    return all_rows


# ---------------------------------------------------------------------------
# Export CSV — utilise UNIFIED_FIELDNAMES partagé avec HW et FT
# ---------------------------------------------------------------------------

def export_indeed_csv(rows: list[dict], output_path: str) -> int:
    return export_csv_rows(rows, output_path, fieldnames=UNIFIED_FIELDNAMES)
