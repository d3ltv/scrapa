"""
test_fusion.py
==============
Vérifie que FT + HW se fusionnent correctement dans un seul CSV
même avec des critères de recherche complètement différents sur chaque API.

Scénarios testés :
- FT : couvreur, département 37, CDI
- HW : développeur python, Paris, CDI+CDD
- Fusion des colonnes communes UNIFIED_FIELDNAMES
- Pas de crash si champs absents d'un côté
- Déduplication fonctionne sur le CSV fusionné
- Ordre : FT d'abord, HW ensuite (dans all_rows)
- Colonnes présentes pour les deux sources
- url en première colonne
"""
import os, csv, tempfile, shutil
import unittest.mock as mock
import tkinter as tk

root = tk.Tk()
root.withdraw()

import france_travail_lib as ftl
import hellowork_lib as hwl
import export_common as ec
import seen_ids_cache as sic

passed = failed = 0

def check(label, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  ✅  {label}")
        passed += 1
    else:
        print(f"  ❌  {label}  {detail}")
        failed += 1

def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")

# ── Données de test réalistes ─────────────────────────────────────────────

def make_ft_offer(i, metier="couvreur", dept="37", contrat="CDI"):
    """Simule une offre France Travail brute (format API)."""
    return {
        "id": f"FT-{dept}-{i:04d}",
        "intitule": f"{metier.capitalize()} expérimenté #{i}",
        "entreprise": {"nom": f"Bâtiment {dept} SARL", "description": "PME BTP"},
        "lieuTravail": {"libelle": f"Tours ({dept})", "codePostal": f"{dept}000"},
        "salaire": {"libelle": "2200-2800 € brut/mois"},
        "typeContrat": contrat,
        "typeContratLibelle": "Contrat à durée indéterminée",
        "experienceLibelle": "3 ans minimum",
        "qualificationLibelle": "Ouvrier qualifié",
        "secteurActiviteLibelle": "Construction",
        "dureeTravailLibelle": "35H",
        "dateCreation": "2026-07-01T08:00:00Z",
        "dateActualisation": "2026-07-01T10:00:00Z",
        "origineOffre": {"urlOrigine": f"https://ft.fr/offre/FT-{dept}-{i:04d}"},
        "contact": {"nom": "RH Bâtiment", "courriel": "rh@batiment.fr"},
        "description": f"Poste de {metier} en {dept}. Travaux de toiture et étanchéité.",
    }

def make_hw_offer(i, metier="développeur python", ville="Paris", contrat="CDI"):
    """Simule une offre HelloWork aplatie (format après flatten_standard_job)."""
    return {
        "source": "HelloWork",
        "id": f"HW-{ville[:3].upper()}-{i:04d}",
        "intitule": f"{metier.capitalize()} #{i}",
        "entreprise": f"Tech Corp {i}",
        "entreprise_url": f"https://techcorp{i}.com",
        "ville": ville,
        "region": "Île-de-France",
        "code_postal": "75001",
        "secteur": "Informatique",
        "domaine": "Développement logiciel",
        "type_contrat": contrat,
        "teletravail": "FULL",
        "salaire_libelle": "45-55k",
        "salaire_min": 45000,
        "salaire_max": 55000,
        "experience": "3 ans",
        "formation": "Bac+5",
        "competences": "Python, Django, PostgreSQL",
        "taille_entreprise": "50",
        "effectif_entreprise": "50-100",
        "date_publication": "2026-07-01",
        "description": f"Poste de {metier} en télétravail à {ville}.",
        "url": f"https://hellowork.com/job/HW-{ville[:3].upper()}-{i:04d}",
    }

# ── Section 1 : fusion de base FT + HW ───────────────────────────────────
section("1. Fusion FT + HW — colonnes communes UNIFIED_FIELDNAMES")

# 5 offres FT (couvreur, 37, CDI)
ft_raw = [make_ft_offer(i, "couvreur", "37", "CDI") for i in range(5)]
ft_unified = [ftl.flatten_offer_unified(o) for o in ft_raw]

# 5 offres HW (dev python, Paris, CDI+CDD mix)
hw_rows = [make_hw_offer(i, "développeur python", "Paris", "CDI" if i%2==0 else "CDD") for i in range(5)]

all_rows = ft_unified + hw_rows
check("10 rows au total (5 FT + 5 HW)", len(all_rows) == 10)
check("FT en premier dans all_rows", all_rows[0]["source"] == "France Travail")
check("HW en deuxième moitié", all_rows[5]["source"] == "HelloWork")

# Vérif colonnes présentes sur chaque row
for field in hwl.UNIFIED_FIELDNAMES:
    ft_has = all(field in r for r in ft_unified)
    hw_has = all(field in r for r in hw_rows)
    check(f"champ '{field}' présent dans FT rows", ft_has)
    check(f"champ '{field}' présent dans HW rows", hw_has)

# ── Section 2 : export CSV fusionné ──────────────────────────────────────
section("2. Export CSV fusionné — structure et contenu")

tmpdir = tempfile.mkdtemp()
try:
    csv_path = os.path.join(tmpdir, "fusion.csv")
    count = ec.export_csv_rows(all_rows, csv_path, fieldnames=hwl.UNIFIED_FIELDNAMES)
    check("export retourne 10", count == 10)
    check("fichier créé", os.path.exists(csv_path))

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        csv_rows = list(reader)
        fieldnames_csv = reader.fieldnames

    check("CSV a 10 lignes", len(csv_rows) == 10)
    check("première colonne = url", list(csv_rows[0].keys())[0] == "url")

    # FT rows
    ft_csv = [r for r in csv_rows if r["source"] == "France Travail"]
    hw_csv = [r for r in csv_rows if r["source"] == "HelloWork"]
    check("5 lignes FT dans CSV", len(ft_csv) == 5)
    check("5 lignes HW dans CSV", len(hw_csv) == 5)

    # FT : vérifier les champs clés
    check("FT url présente",       all(r["url"] for r in ft_csv))
    check("FT intitule présent",   all("Couvreur" in r["intitule"] for r in ft_csv))
    check("FT ville présente",     all("Tours" in r["ville"] for r in ft_csv))
    check("FT contrat = CDI",      all(r["type_contrat"] == "CDI" for r in ft_csv))
    check("FT secteur = Construction", all(r["secteur"] == "Construction" for r in ft_csv))

    # HW : vérifier les champs propres à HW
    check("HW url présente",       all(r["url"] for r in hw_csv))
    check("HW télétravail rempli", all(r["teletravail"] == "FULL" for r in hw_csv))
    check("HW salaire_min rempli", all(r["salaire_min"] == "45000" for r in hw_csv))
    check("HW competences remplies", all("Python" in r["competences"] for r in hw_csv))
    check("HW ville = Paris",      all(r["ville"] == "Paris" for r in hw_csv))

    # FT : champs vides pour les colonnes HW-only
    check("FT télétravail vide (normal)", all(r["teletravail"] == "" for r in ft_csv))
    check("FT competences vide (normal)", all(r["competences"] == "" for r in ft_csv))

    # HW : champs vides pour les colonnes FT-only
    check("HW secteur = Informatique", all(r["secteur"] == "Informatique" for r in hw_csv))

finally:
    shutil.rmtree(tmpdir)

# ── Section 3 : recherches différentes sur chaque API ─────────────────────
section("3. Critères différents FT (BTP/37) vs HW (IT/Paris) — pas de conflit")

# FT : plombier, département 69, CDD
ft_plombier = [ftl.flatten_offer_unified(make_ft_offer(i, "plombier", "69", "CDD")) for i in range(3)]
# HW : data scientist, Lyon, CDI
hw_data = [make_hw_offer(i, "data scientist", "Lyon", "CDI") for i in range(3)]

mixed = ft_plombier + hw_data
check("6 rows dans la fusion mixte", len(mixed) == 6)

# IDs non-conflictuels
ft_ids = {r["id"] for r in ft_plombier}
hw_ids = {r["id"] for r in hw_data}
check("pas de collision d'IDs entre FT et HW", len(ft_ids & hw_ids) == 0)

tmpdir2 = tempfile.mkdtemp()
try:
    csv2 = os.path.join(tmpdir2, "mixte.csv")
    n2 = ec.export_csv_rows(mixed, csv2, fieldnames=hwl.UNIFIED_FIELDNAMES)
    check("export mixte OK sans crash", n2 == 6)

    with open(csv2, encoding="utf-8-sig") as f:
        rows2 = list(csv.DictReader(f, delimiter=";"))

    ft_r = [r for r in rows2 if r["source"] == "France Travail"]
    hw_r = [r for r in rows2 if r["source"] == "HelloWork"]
    check("3 FT (plombier/69)", len(ft_r) == 3)
    check("3 HW (data/Lyon)",   len(hw_r) == 3)
    check("FT métier = Plombier", all("Plombier" in r["intitule"] for r in ft_r))
    check("HW métier = Data",     all("Data" in r["intitule"] for r in hw_r))
    check("FT ville = Tours (69)", all("69" in r["ville"] for r in ft_r))
    check("HW ville = Lyon",       all(r["ville"] == "Lyon" for r in hw_r))

finally:
    shutil.rmtree(tmpdir2)

# ── Section 4 : champs manquants / None d'un côté ────────────────────────
section("4. Champs manquants — pas de crash si un côté a des None")

# FT sans description ni contact
ft_minimal = ftl.flatten_offer_unified({
    "id": "FT-MIN-001",
    "intitule": "Poste minimal",
    "entreprise": None,
    "lieuTravail": None,
    "salaire": None,
    "origineOffre": None,
    "typeContrat": None,
    "dateCreation": None,
})

# HW sans salaire ni compétences
hw_minimal = {
    "source": "HelloWork",
    "id": "HW-MIN-001",
    "intitule": "Poste minimal HW",
    "entreprise": None,
    "entreprise_url": None,
    "ville": None,
    "region": None,
    "code_postal": None,
    "secteur": None,
    "domaine": None,
    "type_contrat": None,
    "teletravail": None,
    "salaire_libelle": None,
    "salaire_min": None,
    "salaire_max": None,
    "experience": None,
    "formation": None,
    "competences": None,
    "taille_entreprise": None,
    "effectif_entreprise": None,
    "date_publication": None,
    "description": None,
    "url": None,
}

minimal_rows = [ft_minimal, hw_minimal]

tmpdir3 = tempfile.mkdtemp()
try:
    csv3 = os.path.join(tmpdir3, "minimal.csv")
    try:
        n3 = ec.export_csv_rows(minimal_rows, csv3, fieldnames=hwl.UNIFIED_FIELDNAMES)
        check("export rows avec None ne crash pas", True)
        check("export retourne 2", n3 == 2)

        with open(csv3, encoding="utf-8-sig") as f:
            rows3 = list(csv.DictReader(f, delimiter=";"))
        check("CSV 2 lignes avec None", len(rows3) == 2)
        check("FT minimal a source=France Travail", rows3[0]["source"] == "France Travail")
        check("HW minimal a source=HelloWork", rows3[1]["source"] == "HelloWork")
    except Exception as e:
        check("export rows avec None ne crash pas", False, str(e))

finally:
    shutil.rmtree(tmpdir3)

# ── Section 5 : déduplication sur CSV fusionné ────────────────────────────
section("5. Déduplication sur le CSV fusionné FT+HW")

orig_cache = sic.CACHE_FILE
tmpdir4 = tempfile.mkdtemp()
sic.CACHE_FILE = os.path.join(tmpdir4, "cache_fusion.json")
sic.clear_cache()

try:
    ft5 = [ftl.flatten_offer_unified(make_ft_offer(i)) for i in range(3)]
    hw5 = [make_hw_offer(i) for i in range(3)]
    fusion5 = ft5 + hw5

    # Recherche 1 — tout est nouveau
    new5, dup5 = sic.filter_new(fusion5)
    check("fusion: 6 nouvelles au départ", len(new5) == 6 and dup5 == 0)

    # Téléchargement — commit tout
    sic.commit_rows(new5)
    check("cache 6 IDs après DL fusion", sic.get_stats()["count"] == 6)

    # Recherche 2 — même fusion → tout doublon
    new5b, dup5b = sic.filter_new(fusion5)
    check("recherche 2: 0 nouvelles, 6 doublons", len(new5b) == 0 and dup5b == 6)

    # Recherche 3 — fusion avec nouvelles offres des deux sources
    ft5_new = [ftl.flatten_offer_unified(make_ft_offer(i+10)) for i in range(2)]
    hw5_new = [make_hw_offer(i+10) for i in range(2)]
    fusion5c = ft5 + hw5 + ft5_new + hw5_new  # 6 connus + 4 nouveaux

    new5c, dup5c = sic.filter_new(fusion5c)
    check("recherche 3: 4 nouvelles (2 FT + 2 HW)", len(new5c) == 4)
    check("recherche 3: 6 doublons", dup5c == 6)

    ft_new_ids = {r["id"] for r in new5c if r["source"] == "France Travail"}
    hw_new_ids = {r["id"] for r in new5c if r["source"] == "HelloWork"}
    check("2 nouvelles FT détectées", len(ft_new_ids) == 2)
    check("2 nouvelles HW détectées", len(hw_new_ids) == 2)

    # Vérif finale au moment du DL — simulé directement
    clean_dl, skip_dl = sic.filter_new(new5c)
    check("vérif finale DL: 4 clean (toutes nouvelles)", len(clean_dl) == 4 and skip_dl == 0)

    sic.commit_rows(clean_dl)
    check("cache 10 IDs après 2ème DL", sic.get_stats()["count"] == 10)

finally:
    sic.CACHE_FILE = orig_cache
    shutil.rmtree(tmpdir4)

# ── Section 6 : apply_post_filters sur fusion ─────────────────────────────
section("6. Filtres post-récupération sur la fusion FT+HW")

ft6 = [ftl.flatten_offer_unified(make_ft_offer(i, "couvreur", "37", "CDI")) for i in range(4)]
hw6 = [make_hw_offer(i, "développeur python", "Paris", "CDI") for i in range(4)]
fusion6 = ft6 + hw6

# Filtre secteur "Construction" → seulement FT
f_construction = ec.apply_post_filters(fusion6, sector_contains="Construction")
check("filtre Construction → 4 FT seulement", len(f_construction) == 4)
check("tous source=FT", all(r["source"] == "France Travail" for r in f_construction))

# Filtre secteur "Informatique" → seulement HW
f_info = ec.apply_post_filters(fusion6, sector_contains="Informatique")
check("filtre Informatique → 4 HW seulement", len(f_info) == 4)
check("tous source=HW", all(r["source"] == "HelloWork" for r in f_info))

# Filtre compétences "Python" → seulement HW (FT n'a pas de compétences structurées)
f_python = ec.apply_post_filters(fusion6, skills_contains="Python")
check("filtre Python → 4 HW (compétences)", len(f_python) == 4)

# Filtre sans critère → tout passe
f_all = ec.apply_post_filters(fusion6)
check("sans filtre → 8 rows (FT+HW)", len(f_all) == 8)

# Filtre qui matche rien → liste vide
f_none = ec.apply_post_filters(fusion6, sector_contains="Aéronautique")
check("filtre inexistant → 0 résultat", len(f_none) == 0)

# ── Résumé ─────────────────────────────────────────────────────────────────
root.destroy()
total = passed + failed
print(f"\n{'='*60}")
print(f"  RÉSULTATS : {passed}/{total} tests passés")
if failed:
    print(f"  ❌  {failed} test(s) échoué(s)")
else:
    print(f"  ✅  FT + HW fusionnés correctement dans le CSV")
    print(f"  ✅  Colonnes cohérentes même avec critères différents")
    print(f"  ✅  Champs None/manquants sans crash")
    print(f"  ✅  Déduplication active sur le CSV fusionné")
print(f"{'='*60}\n")

if failed:
    raise SystemExit(1)
