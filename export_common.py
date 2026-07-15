"""Utilitaires partagés pour l'export CSV et les filtres post-récupération."""

import csv
import re
from typing import Callable, Optional
from urllib.parse import quote


def build_maps_url(entreprise: str | None, ville: str | None) -> str:
    """
    Construit un lien Google Maps pour rechercher une entreprise dans une ville.
    Retourne une chaîne vide si l'un ou l'autre des paramètres est absent ou vide.
    Les caractères spéciaux (accents, apostrophes, espaces, etc.) sont encodés proprement.
    """
    nom = (entreprise or "").strip()
    loc = (ville or "").strip()
    if not nom or not loc:
        return ""
    query = f"{nom} {loc}"
    encoded = quote(query, safe="")
    return f"https://www.google.com/maps/search/?api=1&query={encoded}"


def matches_regex(text: str, pattern: str) -> bool:
    if not pattern:
        return True
    return bool(re.search(pattern, text or "", re.IGNORECASE))


def matches_contains(text: str, needle: str) -> bool:
    if not needle:
        return True
    return needle.lower() in (text or "").lower()


def export_csv_rows(rows: list[dict], output_path: str, fieldnames: Optional[list[str]] = None) -> int:
    if not rows:
        return 0
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    ordered = list(fieldnames)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=ordered, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in ordered})
    return len(rows)


def apply_post_filters(
    rows: list[dict],
    *,
    company_pattern: str = "",
    sector_contains: str = "",
    skills_contains: str = "",
    experience_contains: str = "",
    education_contains: str = "",
    company_size_min: Optional[int] = None,
    company_size_max: Optional[int] = None,
    salary_max: Optional[int] = None,
    get_company: Callable[[dict], str] = lambda r: r.get("entreprise", "") or "",
    get_sector: Callable[[dict], str] = lambda r: " ".join(filter(None, [
        r.get("secteur", "") or "",
        r.get("domaine", "") or "",
    ])),
    get_skills: Callable[[dict], str] = lambda r: " ".join(filter(None, [
        r.get("competences", "") or "",
        r.get("description", "") or "",
    ])),
    get_experience: Callable[[dict], str] = lambda r: r.get("experience", "") or "",
    get_education: Callable[[dict], str] = lambda r: r.get("formation", "") or "",
    get_company_size: Callable[[dict], Optional[int]] = lambda r: _parse_int(r.get("taille_entreprise")),
    get_salary_min: Callable[[dict], Optional[int]] = lambda r: _parse_int(r.get("salaire_min")),
    get_salary_max: Callable[[dict], Optional[int]] = lambda r: _parse_int(r.get("salaire_max")),
) -> list[dict]:
    result = []
    for row in rows:
        if company_pattern and not matches_regex(get_company(row), company_pattern):
            continue
        if sector_contains and not matches_contains(get_sector(row), sector_contains):
            continue
        if skills_contains and not matches_contains(get_skills(row), skills_contains):
            continue
        if experience_contains and not matches_contains(get_experience(row), experience_contains):
            continue
        if education_contains and not matches_contains(get_education(row), education_contains):
            continue

        size = get_company_size(row)
        if company_size_min is not None and size is not None and size < company_size_min:
            continue
        if company_size_max is not None and size is not None and size > company_size_max:
            continue

        sal_min = get_salary_min(row)
        if salary_max is not None and sal_min is not None and sal_min > salary_max:
            continue

        result.append(row)
    return result


def _parse_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(" ", "").replace(",", ".")))
    except (TypeError, ValueError):
        return None
