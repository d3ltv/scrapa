"""
seen_ids_cache.py
=================
Persistance des IDs d'annonces déjà exportées.
Évite les doublons entre plusieurs sessions de recherche.

Stockage : fichier JSON  <dossier_projet>/seen_ids.json
Format   : {"ids": ["id1", "id2", ...], "count": 42, "last_cleared": "2026-07-01T12:00:00"}

Pour les offres sans ID, un fingerprint stable est généré à partir du contenu
(titre + entreprise + ville + url) afin de garantir la déduplication.

Archive : dossier <dossier_projet>/exports_archive/
  Chaque export CSV y est copié automatiquement, avec un index JSON
  (exports_archive/index.json) permettant de retrouver n'importe quel export
  passé même si le fichier original a été perdu ou déplacé.
"""

import csv
import hashlib
import json
import os
import shutil
from datetime import datetime

CACHE_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_ids.json")
ARCHIVE_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports_archive")
ARCHIVE_INDEX = os.path.join(ARCHIVE_DIR, "index.json")


def _fingerprint(row: dict) -> str:
    """
    Génère un identifiant stable pour une offre sans ID.
    Basé sur : url > (intitule + entreprise + ville + date_publication).
    Préfixe 'fp:' pour distinguer des vrais IDs.

    Champs URL supportés (toutes sources) :
      - 'url'          → format unifié HW et FT après flatten
      - 'url_origine'  → format brut FT (ftl_flatten_for_dedup)
    """
    # L'URL est la meilleure clé de dédup car elle est unique par offre
    url = (
        row.get("url")
        or row.get("url_origine")
        or row.get("urlOrigine")
        or ""
    ).strip()
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


# ---------------------------------------------------------------------------
# Archive des exports
# ---------------------------------------------------------------------------

def _load_index() -> list[dict]:
    """Charge l'index des exports archivés."""
    if not os.path.exists(ARCHIVE_INDEX):
        return []
    try:
        with open(ARCHIVE_INDEX, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(entries: list[dict]):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    with open(ARCHIVE_INDEX, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def archive_csv(source_path: str, rows: list[dict], label: str = "") -> str | None:
    """
    Copie un CSV exporté dans exports_archive/ et enregistre l'entrée dans
    l'index JSON.  Retourne le chemin du fichier archivé, ou None en cas d'échec.

    Paramètres :
      source_path : chemin du CSV qui vient d'être écrit (peut être vide/None si
                    le CSV n'a pas encore été écrit sur disque — dans ce cas on
                    crée le fichier archivé directement depuis les rows).
      rows        : liste de dicts représentant les offres exportées.
      label       : description optionnelle (ex: "company_search", "offres FT").
    """
    if not rows:
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(source_path) if source_path else f"export_{ts}.csv"
    # Nom unique dans l'archive : horodatage + nom original
    archive_name = f"{ts}_{base}" if not base.startswith(ts) else base
    archive_path = os.path.join(ARCHIVE_DIR, archive_name)

    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    try:
        if source_path and os.path.exists(source_path):
            shutil.copy2(source_path, archive_path)
        else:
            # Fallback : réécrire depuis les rows si le fichier source n'existe pas
            if rows:
                fieldnames = list(rows[0].keys())
                with open(archive_path, "w", newline="", encoding="utf-8-sig") as f:
                    import csv as _csv
                    writer = _csv.DictWriter(f, fieldnames=fieldnames, delimiter=";",
                                            extrasaction="ignore")
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({k: row.get(k, "") for k in fieldnames})
    except OSError:
        return None

    # Enregistre dans l'index
    entry = {
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "archive_file": archive_name,
        "original_path": source_path or "",
        "label": label,
        "count": len(rows),
        "ids": [get_row_id(r) for r in rows],
    }
    entries = _load_index()
    entries.append(entry)
    _save_index(entries)

    return archive_path


def list_archives() -> list[dict]:
    """
    Retourne la liste des exports archivés (plus récent en premier).
    Chaque entrée contient : archived_at, archive_file, original_path,
    label, count, ids, et archive_path (chemin absolu).
    """
    entries = _load_index()
    result = []
    for e in reversed(entries):
        e = dict(e)
        e["archive_path"] = os.path.join(ARCHIVE_DIR, e.get("archive_file", ""))
        e["exists"] = os.path.exists(e["archive_path"])
        result.append(e)
    return result


def restore_from_archive(archive_file: str, destination: str) -> bool:
    """
    Copie un fichier archivé vers destination.
    Retourne True si la restauration a réussi.
    """
    src = os.path.join(ARCHIVE_DIR, archive_file)
    if not os.path.exists(src):
        return False
    try:
        shutil.copy2(src, destination)
        return True
    except OSError:
        return False


def get_archive_stats() -> dict:
    """Retourne les stats de l'archive."""
    entries = _load_index()
    total_files = len(entries)
    total_offers = sum(e.get("count", 0) for e in entries)
    size_bytes = 0
    if os.path.exists(ARCHIVE_DIR):
        for f in os.listdir(ARCHIVE_DIR):
            fp = os.path.join(ARCHIVE_DIR, f)
            if os.path.isfile(fp):
                size_bytes += os.path.getsize(fp)
    return {
        "total_exports": total_files,
        "total_offers": total_offers,
        "size_kb": round(size_bytes / 1024, 1),
        "path": ARCHIVE_DIR,
    }
