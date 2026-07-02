"""
seen_ids_cache.py
=================
Persistance des IDs d'annonces déjà exportées.
Évite les doublons entre plusieurs sessions de recherche.

Stockage : fichier JSON  <dossier_projet>/seen_ids.json
Format   : {"ids": ["id1", "id2", ...], "count": 42, "last_cleared": "2026-07-01T12:00:00"}

Pour les offres sans ID, un fingerprint stable est généré à partir du contenu
(titre + entreprise + ville + url) afin de garantir la déduplication.
"""

import hashlib
import json
import os
from datetime import datetime

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_ids.json")


def _fingerprint(row: dict) -> str:
    """
    Génère un identifiant stable pour une offre sans ID.
    Basé sur : url > (intitule + entreprise + ville + date_publication).
    Préfixe 'fp:' pour distinguer des vrais IDs.
    """
    # L'URL est la meilleure clé de dédup car elle est unique par offre
    url = (row.get("url") or row.get("url_origine") or "").strip()
    if url:
        key = url
    else:
        # Fallback : hash du contenu structuré
        parts = [
            str(row.get("intitule") or ""),
            str(row.get("entreprise") or ""),
            str(row.get("ville") or ""),
            str(row.get("date_publication") or row.get("date_creation") or ""),
            str(row.get("type_contrat") or ""),
        ]
        key = "|".join(parts)
    return "fp:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def get_row_id(row: dict, id_field: str = "id") -> str:
    """
    Retourne l'identifiant d'une row :
    - le vrai ID s'il existe et n'est pas vide
    - sinon un fingerprint stable basé sur le contenu
    """
    rid = str(row.get(id_field) or "").strip()
    if rid:
        return rid
    return _fingerprint(row)


def _load() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {"ids": [], "count": 0, "last_cleared": None}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "ids" not in data:
            data["ids"] = []
        return data
    except (json.JSONDecodeError, OSError):
        return {"ids": [], "count": 0, "last_cleared": None}


def _save(data: dict):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_seen_ids() -> set:
    """Charge et retourne l'ensemble des IDs déjà vus."""
    return set(_load()["ids"])


def save_seen_ids(ids: set):
    """Fusionne et sauvegarde les IDs dans le cache."""
    data = _load()
    existing = set(data["ids"])
    merged = existing | ids
    data["ids"] = sorted(merged)
    data["count"] = len(merged)
    _save(data)


def add_ids(new_ids: list) -> int:
    """
    Ajoute les nouveaux IDs au cache.
    Retourne le nombre d'IDs ajoutés (non déjà connus).
    """
    data = _load()
    existing = set(data["ids"])
    to_add = {str(i) for i in new_ids if str(i) not in existing and i}
    if not to_add:
        return 0
    merged = existing | to_add
    data["ids"] = sorted(merged)
    data["count"] = len(merged)
    _save(data)
    return len(to_add)


def filter_new(rows: list[dict], id_field: str = "id") -> tuple[list[dict], int]:
    """
    Filtre une liste de rows : ne garde que ceux dont l'identifiant n'est pas
    déjà dans le cache. Utilise le vrai ID ou un fingerprint si ID absent.
    Retourne (nouvelles_rows, nb_doublons_ignorés).
    """
    seen = load_seen_ids()
    new_rows = []
    dupes = 0
    for row in rows:
        rid = get_row_id(row, id_field)
        if rid in seen:
            dupes += 1
        else:
            new_rows.append(row)
    return new_rows, dupes


def commit_rows(rows: list[dict], id_field: str = "id"):
    """Enregistre les identifiants des rows dans le cache après export."""
    ids = [get_row_id(r, id_field) for r in rows]
    ids = [i for i in ids if i]  # filtre les chaînes vides (ne devrait pas arriver)
    if ids:
        add_ids(ids)


def get_stats() -> dict:
    """Retourne les stats du cache : nb d'IDs, taille fichier, date dernier clear."""
    data = _load()
    size = 0
    if os.path.exists(CACHE_FILE):
        size = os.path.getsize(CACHE_FILE)
    return {
        "count": data.get("count", len(data.get("ids", []))),
        "size_kb": round(size / 1024, 1),
        "last_cleared": data.get("last_cleared"),
        "path": CACHE_FILE,
    }


def clear_cache():
    """Vide complètement le cache."""
    data = {
        "ids": [],
        "count": 0,
        "last_cleared": datetime.now().isoformat(timespec="seconds"),
    }
    _save(data)
