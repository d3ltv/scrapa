"""
cross_dedup.py
==============
Déduplication cross-sources : détecte la même annonce postée sur
France Travail, HelloWork ET Indeed simultanément.

Problème : chaque site a sa propre URL → le fingerprint par URL ne suffit pas.
Solution : fingerprint sémantique basé sur (titre normalisé + entreprise normalisée + ville normalisée).

Une offre est considérée identique si :
  - même entreprise (insensible casse/accents)
  - même titre normalisé (≥ 80% de tokens communs)
  - même ville normalisée

Priorité de conservation quand doublon détecté :
  France Travail > HelloWork > Indeed
  (FT = source officielle avec plus de détails)
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Lowercase, retire accents, ponctuation, espaces multiples."""
    if not text:
        return ""
    # Accents
    s = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    # Lowercase
    s = s.lower()
    # Retire ponctuation sauf espace
    s = re.sub(r"[^\w\s]", " ", s)
    # Espaces multiples
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Suffixes juridiques à ignorer pour le matching entreprise
_SUFFIXES = re.compile(
    r"\b(sas|sarl|sa|sasu|eurl|sci|snc|scp|se|spa|ltd|gmbh|inc|llc|bv|nv"
    r"|groupe|group|holding|services?|solutions?|technologies|tech|systems?"
    r"|industries?|industrie|france|french)\b",
    re.IGNORECASE,
)

# Mots vides pour le titre
_STOPWORDS = {
    "h", "f", "hf", "h/f", "f/h", "homme", "femme", "poste", "emploi",
    "recherche", "offre", "cdi", "cdd", "alternance", "stage", "interim",
    "intérim", "interimaire", "temps", "plein", "partiel", "senior", "junior",
    "confirmé", "confirme", "debutant", "experience", "exp", "ans",
    "le", "la", "les", "de", "du", "des", "un", "une", "en", "au", "aux",
    "et", "ou", "pour", "sur", "dans", "par", "avec", "sans", "sous",
    "notre", "votre", "nos", "vos", "ce", "cette", "ces", "son", "sa", "ses",
}


def _norm_company(company: str) -> str:
    """Normalise un nom d'entreprise pour comparaison."""
    s = _norm(company)
    s = _SUFFIXES.sub(" ", s)
    # Retire les connecteurs courants : &, et, or
    s = re.sub(r"\b(et|and|&|or|ou)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _company_tokens(company: str) -> set[str]:
    """Tokens significatifs d'un nom d'entreprise (≥ 3 car)."""
    s = _norm_company(company)
    return {t for t in s.split() if len(t) >= 3}


def _company_similarity(a: str, b: str) -> float:
    """
    Similarité entre deux noms d'entreprise.
    Utilise le ratio d'inclusion (overlap) plutôt que Jaccard strict,
    ce qui gère mieux les cas où un nom est un sous-ensemble de l'autre
    ex: "SNCF" / "SNCF Réseau" → 100% (tous les tokens de SNCF sont dans SNCF Réseau)
    """
    ta = _company_tokens(a)
    tb = _company_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    if not inter:
        return 0.0
    # Overlap coefficient = |inter| / min(|ta|, |tb|)
    # (meilleur que Jaccard quand un ensemble est sous-ensemble de l'autre)
    overlap = len(inter) / min(len(ta), len(tb))
    jaccard  = len(inter) / len(ta | tb)
    # On prend le max des deux pour être tolérant
    return max(overlap, jaccard)


def _title_tokens(title: str) -> set[str]:
    """Extrait les tokens significatifs d'un titre de poste."""
    s = _norm(title)
    tokens = set(s.split())
    tokens -= _STOPWORDS
    # Retire les tokens trop courts (1-2 car) sauf acronymes connus
    tokens = {t for t in tokens if len(t) >= 3}
    return tokens


def _norm_city(city: str) -> str:
    """Normalise une ville pour comparaison."""
    s = _norm(city)
    # Retire le préfixe département France Travail : "37 - tours" → "tours"
    s = re.sub(r"^\d{1,3}\s+", "", s).strip()
    # Retire les codes département Corse/DOM
    s = re.sub(r"^2[ab]\s+", "", s).strip()
    # Retire arrondissements parisiens/lyonnais : "paris 15e arrondissement" → "paris"
    # et "paris 15e" → "paris", "lyon 03" → "lyon"
    s = re.sub(r"\s+\d+e?(me|eme|ieme|ième)?\s*(arrondissement)?$", "", s).strip()
    s = re.sub(r"\s+\d{1,2}$", "", s).strip()
    return s


# ---------------------------------------------------------------------------
# Clé de déduplication sémantique
# ---------------------------------------------------------------------------

def semantic_key(row: dict) -> str | None:
    """
    Génère une clé de déduplication sémantique cross-source.
    Retourne None si les données sont insuffisantes.

    Clé = "<entreprise_norm>||<titre_tokens_triés>||<ville_norm>"
    """
    company = _norm_company(row.get("entreprise") or "")
    city    = _norm_city(row.get("ville") or "")
    title   = row.get("intitule") or ""
    tokens  = _title_tokens(title)

    if not company or not tokens:
        return None

    tokens_str = " ".join(sorted(tokens))
    return f"{company}||{tokens_str}||{city}"


def title_similarity(title_a: str, title_b: str) -> float:
    """
    Jaccard similarity entre les tokens de deux titres.
    Retourne 0.0–1.0.
    """
    ta = _title_tokens(title_a)
    tb = _title_tokens(title_b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


# ---------------------------------------------------------------------------
# Déduplication principale
# ---------------------------------------------------------------------------

# Ordre de priorité des sources (la source avec le plus d'infos prime)
_SOURCE_PRIORITY = {"France Travail": 0, "HelloWork": 1, "Indeed": 2}


def _source_rank(row: dict) -> int:
    return _SOURCE_PRIORITY.get(row.get("source") or "", 99)


def deduplicate_cross_source(
    rows: list[dict],
    similarity_threshold: float = 0.75,
    log=None,
) -> tuple[list[dict], list[dict]]:
    """
    Déduplique une liste d'offres provenant de sources différentes.

    Stratégie en deux passes :
      1. Clé exacte (entreprise + tokens titre + ville) → doublons certains
      2. Clé partielle (entreprise + ville) + similarité titre ≥ threshold
         → doublons probables (même entreprise, même ville, titre similaire)

    En cas de doublon, conserve la source ayant la meilleure priorité :
      France Travail > HelloWork > Indeed

    Retourne (uniques, doublons_éliminés).
    """
    # Passe 1 — clé exacte
    exact_groups: dict[str, list[int]] = defaultdict(list)
    no_key: list[int] = []

    for i, row in enumerate(rows):
        key = semantic_key(row)
        if key:
            exact_groups[key].append(i)
        else:
            no_key.append(i)

    kept_indices: set[int] = set()
    eliminated: list[dict] = []

    for key, indices in exact_groups.items():
        if len(indices) == 1:
            kept_indices.add(indices[0])
            continue
        # Plusieurs offres avec la même clé → garder la meilleure source
        best = min(indices, key=lambda i: _source_rank(rows[i]))
        kept_indices.add(best)
        for i in indices:
            if i != best:
                dup = rows[i]
                if log:
                    log(
                        f"  🔁 Doublon cross-source : "
                        f"{dup.get('source')} «{dup.get('intitule','')[:40]}» "
                        f"({dup.get('entreprise','')}) "
                        f"→ conservé depuis {rows[best].get('source')}"
                    )
                eliminated.append(dup)

    # Passe 2 — similarité titre + entreprise, regroupé par ville
    # Regroupe par ville_norm, compare chaque paire (entreprise × titre)
    city_groups: dict[str, list[int]] = defaultdict(list)
    for i in kept_indices:
        row  = rows[i]
        city = _norm_city(row.get("ville") or "")
        city_groups[city if city else "__no_city__"].append(i)

    final_kept: set[int] = set()
    for city_key, indices in city_groups.items():
        if len(indices) == 1:
            final_kept.add(indices[0])
            continue

        marked_dup: set[int] = set()
        for a in range(len(indices)):
            if indices[a] in marked_dup:
                continue
            for b in range(a + 1, len(indices)):
                if indices[b] in marked_dup:
                    continue
                row_a = rows[indices[a]]
                row_b = rows[indices[b]]

                # Similarité entreprise (seuil 0.6 pour tolérer les variantes de forme)
                comp_sim = _company_similarity(
                    row_a.get("entreprise") or "",
                    row_b.get("entreprise") or "",
                )
                if comp_sim < 0.6:
                    continue

                # Similarité titre
                title_sim = title_similarity(
                    row_a.get("intitule") or "",
                    row_b.get("intitule") or "",
                )
                if title_sim >= similarity_threshold:
                    rank_a = _source_rank(row_a)
                    rank_b = _source_rank(row_b)
                    loser  = indices[b] if rank_a <= rank_b else indices[a]
                    winner = indices[a] if rank_a <= rank_b else indices[b]
                    marked_dup.add(loser)
                    dup = rows[loser]
                    if log:
                        log(
                            f"  🔁 Doublon probable (titre={title_sim:.0%} ent={comp_sim:.0%}) : "
                            f"{dup.get('source')} «{dup.get('intitule','')[:35]}» "
                            f"→ conservé depuis {rows[winner].get('source')}"
                        )
                    eliminated.append(dup)

        for i in indices:
            if i not in marked_dup:
                final_kept.add(i)

    # Ajouter les lignes sans clé (données insuffisantes → pas de dédup possible)
    for i in no_key:
        final_kept.add(i)

    # Reconstruire dans l'ordre original
    unique = [rows[i] for i in range(len(rows)) if i in final_kept]

    return unique, eliminated


# ---------------------------------------------------------------------------
# Stats utilitaires
# ---------------------------------------------------------------------------

def dedup_stats(original: list[dict], unique: list[dict], eliminated: list[dict]) -> str:
    by_source: dict[str, int] = defaultdict(int)
    for r in eliminated:
        by_source[r.get("source") or "?"] += 1
    detail = ", ".join(f"{s}: {n}" for s, n in sorted(by_source.items()))
    return (
        f"{len(original)} offres → {len(unique)} uniques "
        f"({len(eliminated)} doublons cross-source supprimés"
        + (f" [{detail}]" if detail else "")
        + ")"
    )
