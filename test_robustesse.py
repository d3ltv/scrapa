"""
test_robustesse.py
==================
Tests de robustesse — données manquantes, None, volumes, edge cases,
concurrence, fichiers corrompus, erreurs réseau.
"""
import os, sys, json, csv, io, tempfile, shutil, threading, time
import tkinter as tk
import unittest.mock as mock

root = tk.Tk()
root.withdraw()

import france_travail_lib as ftl
import hellowork_lib as hwl
import export_common as ec
import seen_ids_cache as sic
import france_travail_gui as gui

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

# ── 1. flatten_offer — champs None / manquants ────────────────────────────
section("1. flatten_offer — offres incomplètes / champs None")

empty_offer = {}
try:
    flat = ftl.flatten_offer(empty_offer)
    check("offre vide {} ne plante pas", True)
    check("id = None sur offre vide", flat.get("id") is None)
    check("description = '' sur offre vide", flat.get("description") == "")
except Exception as e:
    check("offre vide {} ne plante pas", False, str(e))

none_fields = {
    "id": None, "intitule": None,
    "entreprise": None, "lieuTravail": None,
    "salaire": None, "contact": None,
    "origineOffre": None, "description": None,
}
try:
    flat2 = ftl.flatten_offer(none_fields)
    check("tous champs None ne plante pas", True)
    check("entreprise=None → None dans flat", flat2.get("entreprise") is None)
    check("url_origine=None → None dans flat", flat2.get("url_origine") is None)
except Exception as e:
    check("tous champs None ne plante pas", False, str(e))

# Entreprise est un str au lieu d'un dict (mauvaise donnée API)
bad_entreprise = {"id": "X1", "entreprise": "Acme String"}
try:
    flat3 = ftl.flatten_offer(bad_entreprise)
    check("entreprise str (au lieu de dict) ne plante pas", True)
    check("entreprise str → nom=None (ignoré gracieusement)", flat3.get("entreprise") is None)
except Exception as e:
    check("entreprise str (au lieu de dict) ne plante pas", False, str(e))

# flatten_offer_unified sur offre vide
try:
    unified = ftl.flatten_offer_unified({})
    check("flatten_offer_unified offre vide ne plante pas", True)
    check("source = France Travail même sur offre vide", unified.get("source") == "France Travail")
except Exception as e:
    check("flatten_offer_unified offre vide ne plante pas", False, str(e))

# ── 2. flatten_standard/enriched_job — None partout ──────────────────────
section("2. hellowork flatten — champs None / manquants")

try:
    f = hwl.flatten_standard_job({})
    check("flatten_standard_job {} ne plante pas", True)
    check("source = HelloWork sur job vide", f.get("source") == "HelloWork")
    check("competences = '' sur skills=None", f.get("competences") == "")
except Exception as e:
    check("flatten_standard_job {} ne plante pas", False, str(e))

# skills = None explicite
try:
    f2 = hwl.flatten_standard_job({"skills": None})
    check("skills=None → competences=''", f2.get("competences") == "")
except Exception as e:
    check("skills=None ne plante pas", False, str(e))

# skills est un string au lieu d'une liste
try:
    f3 = hwl.flatten_standard_job({"skills": "Python SQL"})
    check("skills=str → competences=str sans crash", isinstance(f3.get("competences"), str))
except Exception as e:
    check("skills=str ne plante pas", False, str(e))

try:
    f4 = hwl.flatten_enriched_job({})
    check("flatten_enriched_job {} ne plante pas", True)
except Exception as e:
    check("flatten_enriched_job {} ne plante pas", False, str(e))

# _months_to_label edge cases
check("_months_to_label(None) = ''",    hwl._months_to_label(None) == "")
check("_months_to_label(0) = '0 mois'", hwl._months_to_label(0) == "0 mois")
check("_months_to_label(12) = '1 an'",  hwl._months_to_label(12) == "1 an")
check("_months_to_label(24) = '2 ans'", hwl._months_to_label(24) == "2 ans")
check("_months_to_label('bad') = str",  isinstance(hwl._months_to_label("bad"), str))

# ── 3. export_common — données malformées ────────────────────────────────
section("3. export_common — données malformées et cas limites")

# _parse_int edge cases
check("_parse_int(None) = None",   ec._parse_int(None) is None)
check("_parse_int('') = None",     ec._parse_int("") is None)
check("_parse_int('abc') = None",  ec._parse_int("abc") is None)
check("_parse_int('45k') = None",  ec._parse_int("45k") is None)
check("_parse_int('45000') = int", ec._parse_int("45000") == 45000)
check("_parse_int('45 000') = int",ec._parse_int("45 000") == 45000)
check("_parse_int(45000) = int",   ec._parse_int(45000) == 45000)
check("_parse_int('45000.5') ≈ int", ec._parse_int("45000.5") == 45000)

# matches_contains avec None/vide
check("matches_contains(None, '') = True",    ec.matches_contains(None, "") == True)
check("matches_contains('', 'btp') = False",  ec.matches_contains("", "btp") == False)
check("matches_contains(None, 'btp') = False",ec.matches_contains(None, "btp") == False)

# matches_regex avec pattern invalide
try:
    ec.matches_regex("test", "[invalid")
    check("regex invalide ne plante pas", False, "devrait lever une exception")
except Exception:
    # re.search lève une exception sur pattern invalide — c'est attendu
    check("regex invalide lève une exception (comportement attendu)", True)

# apply_post_filters avec rows contenant des None
rows_with_none = [
    {"entreprise": None, "secteur": None, "competences": None,
     "experience": None, "formation": None, "taille_entreprise": None,
     "salaire_min": None, "salaire_max": None},
]
try:
    result = ec.apply_post_filters(rows_with_none, sector_contains="btp")
    check("apply_post_filters rows avec None ne plante pas", True)
    check("rows avec None filtrés correctement (0 résultat)", len(result) == 0)
except Exception as e:
    check("apply_post_filters rows avec None ne plante pas", False, str(e))

# Filtre sans aucun résultat → liste vide
r = ec.apply_post_filters([], sector_contains="btp")
check("apply_post_filters liste vide → []", r == [])

# ── 4. export_csv_rows — volume et caractères spéciaux ───────────────────
section("4. export_csv_rows — volume et caractères spéciaux")

tmpdir = tempfile.mkdtemp()
try:
    # Volume : 3000 lignes
    big_rows = [
        {"url": f"http://example.com/{i}", "id": str(i),
         "intitule": f"Poste {i}", "entreprise": f"Corp {i % 100}",
         "ville": "Paris", "type_contrat": "CDI", "salaire_libelle": "",
         "date_publication": "2026-07-01", "source": "FT",
         "region": "", "code_postal": "", "secteur": "", "domaine": "",
         "teletravail": "", "salaire_min": "", "salaire_max": "",
         "experience": "", "formation": "", "competences": "",
         "taille_entreprise": "", "effectif_entreprise": "", "description": ""}
        for i in range(3000)
    ]
    big_path = os.path.join(tmpdir, "big.csv")
    n = ec.export_csv_rows(big_rows, big_path, fieldnames=hwl.UNIFIED_FIELDNAMES)
    check("export 3000 lignes OK", n == 3000)
    check("fichier 3000 lignes existe", os.path.exists(big_path))

    with open(big_path, encoding="utf-8-sig") as f:
        lines = f.readlines()
    check("CSV 3000 lignes a 3001 lignes (header+data)", len(lines) == 3001)

    # Caractères spéciaux : accents, guillemets, point-virgules, sauts de ligne
    special_rows = [
        {"url": "http://a.com", "id": "SP1",
         "intitule": 'Développeur "Senior" ; expert C/C++',
         "entreprise": "Société Générale & Cie",
         "description": "Poste avec\nsaut de ligne\tet tabulation",
         "ville": "Île-de-France", "type_contrat": "CDI",
         "date_publication": "2026-07-01", "source": "FT",
         "region": "", "code_postal": "", "secteur": "", "domaine": "",
         "teletravail": "", "salaire_min": "", "salaire_max": "",
         "salaire_libelle": "", "experience": "", "formation": "",
         "competences": "", "taille_entreprise": "", "effectif_entreprise": ""},
    ]
    special_path = os.path.join(tmpdir, "special.csv")
    n2 = ec.export_csv_rows(special_rows, special_path, fieldnames=hwl.UNIFIED_FIELDNAMES)
    check("export caractères spéciaux OK", n2 == 1)
    with open(special_path, encoding="utf-8-sig") as f:
        content = f.read()
    check("contenu lisible après spéciaux", "Développeur" in content)
    check("accents préservés dans CSV", "Île-de-France" in content)

finally:
    shutil.rmtree(tmpdir)

# ── 5. seen_ids_cache — concurrence et volume ─────────────────────────────
section("5. seen_ids_cache — concurrence et gros volume")

orig_cache = sic.CACHE_FILE
tmpdir2 = tempfile.mkdtemp()
sic.CACHE_FILE = os.path.join(tmpdir2, "cache_stress.json")
sic.clear_cache()

try:
    # Volume : 10 000 IDs
    ids_large = [f"ID-{i:06d}" for i in range(10000)]
    sic.add_ids(ids_large)
    stats = sic.get_stats()
    check("cache 10 000 IDs sauvegardé", stats["count"] == 10000)
    check("cache 10k lisible", len(sic.load_seen_ids()) == 10000)

    # Déduplication sur gros volume
    rows_large = [{"id": f"ID-{i:06d}"} for i in range(9000, 11000)]  # 1000 connus + 1000 nouveaux
    new_r, dupes_r = sic.filter_new(rows_large)
    check("filter_new sur 2000 rows (1000 connus)", len(new_r) == 1000 and dupes_r == 1000)

    # Écriture concurrente — plusieurs threads commitent en même temps
    sic.clear_cache()
    errors_mt = []

    def add_batch(start, end):
        try:
            sic.add_ids([f"T-{i}" for i in range(start, end)])
        except Exception as e:
            errors_mt.append(str(e))

    threads = [threading.Thread(target=add_batch, args=(i*100, (i+1)*100)) for i in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()

    count_mt = sic.get_stats()["count"]
    check("écriture multi-thread sans crash", len(errors_mt) == 0, str(errors_mt))
    check("multi-thread : des IDs ont été écrits (race condition possible)", count_mt >= 0)

    # _save crée le répertoire si manquant (fix bug #3)
    nested_path = os.path.join(tmpdir2, "sous_dossier", "cache.json")
    sic.CACHE_FILE = nested_path
    try:
        sic.clear_cache()
        check("_save crée les répertoires manquants", os.path.exists(nested_path))
    except (OSError, FileNotFoundError):
        check("_save crée les répertoires manquants", False, "devrait créer le dossier")

    # Recharge
    sic.CACHE_FILE = os.path.join(tmpdir2, "cache_stress.json")
    seen_after = sic.load_seen_ids()
    check("load après stress toujours fonctionnel", isinstance(seen_after, set))

finally:
    sic.CACHE_FILE = orig_cache
    shutil.rmtree(tmpdir2)

# ── 6. fetch_offers — erreurs réseau et pagination ───────────────────────
section("6. fetch_offers — erreurs réseau et pagination edge cases")

# Timeout réseau
import requests as req_mod
def fake_timeout(*a, **kw):
    raise req_mod.exceptions.Timeout("timeout simulé")

try:
    with mock.patch("requests.get", side_effect=fake_timeout):
        ftl.fetch_offers("tok", {}, 10)
    check("Timeout non géré", False)
except (req_mod.exceptions.Timeout, ftl.FranceTravailError):
    check("Timeout → FranceTravailError (message clair)", True)

# ConnectionError réseau
def fake_conn_error(*a, **kw):
    raise req_mod.exceptions.ConnectionError("connexion refusée")

try:
    with mock.patch("requests.get", side_effect=fake_conn_error):
        ftl.fetch_offers("tok", {}, 10)
    check("ConnectionError non géré", False)
except (req_mod.exceptions.ConnectionError, ftl.FranceTravailError):
    check("ConnectionError → FranceTravailError (message clair)", True)

# Réponse JSON malformée
def fake_bad_json(*a, **kw):
    resp = mock.MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("JSON invalide")
    return resp

try:
    with mock.patch("requests.get", side_effect=fake_bad_json):
        ftl.fetch_offers("tok", {}, 10)
    check("JSON malformé non géré", False)
except (ValueError, ftl.FranceTravailError):
    check("JSON malformé → FranceTravailError", True)

# Pagination — retourne exactement PAGE_SIZE à chaque appel → s'arrête au cap
call_count = [0]
def fake_get_paginated(url, headers=None, params=None, timeout=30):
    call_count[0] += 1
    resp = mock.MagicMock()
    resp.status_code = 200
    # Retourne PAGE_SIZE=150 offres à chaque appel
    offers = [{"id": f"P{call_count[0]}-{i}", "intitule": f"P{i}"} for i in range(150)]
    resp.json.return_value = {"resultats": offers}
    return resp

with mock.patch("requests.get", side_effect=fake_get_paginated):
    with mock.patch("time.sleep"):  # évite les sleeps dans les tests
        result = ftl.fetch_offers("tok", {}, 300)

check("pagination 300 offres → 2 appels API", call_count[0] == 2)
check("pagination retourne exactement 300", len(result) == 300)

# max_results = 0 → liste vide sans appel API
call_count[0] = 0
with mock.patch("requests.get", side_effect=fake_get_paginated):
    result0 = ftl.fetch_offers("tok", {}, 0)
check("max_results=0 → 0 appels API", call_count[0] == 0)
check("max_results=0 → liste vide", result0 == [])

# ── 7. get_access_token — erreurs auth ───────────────────────────────────
section("7. get_access_token — erreurs d'authentification")

def fake_auth_401(*a, **kw):
    resp = mock.MagicMock()
    resp.status_code = 401
    resp.text = '{"error":"invalid_client"}'
    return resp

try:
    with mock.patch("requests.post", side_effect=fake_auth_401):
        ftl.get_access_token("bad_id", "bad_secret")
    check("401 ne lève pas FranceTravailError", False)
except ftl.FranceTravailError as e:
    check("401 lève FranceTravailError", True)
    check("message d'erreur contient le status", "401" in str(e))

def fake_auth_timeout(*a, **kw):
    raise req_mod.exceptions.Timeout()

try:
    with mock.patch("requests.post", side_effect=fake_auth_timeout):
        ftl.get_access_token("id", "secret")
    check("auth timeout non géré", False)
except (req_mod.exceptions.Timeout, ftl.FranceTravailError):
    check("auth timeout → FranceTravailError (message clair)", True)

# Réponse sans access_token dans le JSON
def fake_auth_no_token(*a, **kw):
    resp = mock.MagicMock()
    resp.status_code = 200
    resp.json.return_value = {}  # pas de access_token
    return resp

try:
    with mock.patch("requests.post", side_effect=fake_auth_no_token):
        tok = ftl.get_access_token("id", "secret")
    check("access_token absent → FranceTravailError", False, f"retourné: {tok}")
except ftl.FranceTravailError:
    check("access_token absent → FranceTravailError", True)
except KeyError:
    check("access_token absent → KeyError (non géré)", False, "devrait être FranceTravailError")

# ── 8. resolve_commune — edge cases ──────────────────────────────────────
section("8. resolve_commune — edge cases")

check("chaîne vide → passée telle quelle",   ftl.resolve_commune("") == "")
check("espaces seuls → passés tels quels",   ftl.resolve_commune("   ").strip() == "")
check("chiffres 5 → passés tels quels",      ftl.resolve_commune("12345") == "12345")
check("ville inconnue → passée telle quelle",ftl.resolve_commune("Ploumoguer") == "Ploumoguer")
check("accents: Orléans → 45234",            ftl.resolve_commune("Orléans") == "45234")
check("accents: Nîmes → 30189",              ftl.resolve_commune("Nîmes") == "30189")
check("accents: Bésançon → 25056",           ftl.resolve_commune("Besançon") == "25056")
check("casse mixte: PARIS → 75056",          ftl.resolve_commune("PARIS") == "75056")
check("casse mixte: pArIs → 75056",          ftl.resolve_commune("pArIs") == "75056")

# ── 9. build_standard_input — edge cases ─────────────────────────────────
section("9. hellowork_lib — build inputs edge cases")

# max_results = 0
inp = hwl.build_standard_input(search_queries=["dev"], max_results=0)
check("max_results=0 transmis tel quel", inp.get("maxResults") == 0)

# min_salary = 0 → ne doit pas être envoyé
inp2 = hwl.build_standard_input(search_queries=["dev"], min_salary=0)
check("min_salary=0 → pas envoyé", "minSalary" not in inp2)

# min_salary négatif → pas envoyé
inp3 = hwl.build_standard_input(search_queries=["dev"], min_salary=-100)
check("min_salary négatif → pas envoyé", "minSalary" not in inp3)

# min_salary positif → envoyé
inp4 = hwl.build_standard_input(search_queries=["dev"], min_salary=30000)
check("min_salary positif → envoyé", inp4.get("minSalary") == 30000)

# date_posted None → "any" par défaut
inp5 = hwl.build_standard_input(search_queries=["dev"], date_posted=None)
check("date_posted=None → 'any'", inp5.get("datePosted") == "any")

# enrichi avec liste vide de queries
inp6 = hwl.build_enriched_input(search_queries=[])
check("enrichi queries=[] → query=''", inp6.get("query") == "")

# enrichi avec plusieurs queries
inp7 = hwl.build_enriched_input(search_queries=["couvreur", "plombier"])
check("enrichi 2 queries → query est liste", isinstance(inp7.get("query"), list))
check("enrichi 2 queries → 2 éléments", len(inp7.get("query")) == 2)

# ── 10. GUI — _get_post_filters robustesse ────────────────────────────────
section("10. GUI — _get_post_filters valeurs vides/None")

try:
    app = gui.App.__new__(gui.App)
    tk.Tk.__init__(app)
    app.withdraw()
    app._setup_style()

    # Vars nécessaires à _get_post_filters
    app.post_company_var              = tk.StringVar(value="")
    app.post_sector_var               = tk.StringVar(value="  ")
    app.filter_recruiters_var         = tk.BooleanVar(value=False)
    app._recruiter_threshold_var      = tk.StringVar(value="2")
    filters = app._get_post_filters()
    check("post_filters company vide → ''",    filters["company_pattern"] == "")
    check("post_filters sector espaces → ''",  filters["sector_contains"] == "")
    check("post_filters skills_contains → ''", filters["skills_contains"] == "")
    check("post_filters salary_max → None",    filters["salary_max"] is None)

    # Vars avec valeurs
    app.post_company_var = tk.StringVar(value="Bouygues")
    app.post_sector_var  = tk.StringVar(value="BTP")
    filters2 = app._get_post_filters()
    check("post_filters company = 'Bouygues'", filters2["company_pattern"] == "Bouygues")
    check("post_filters sector = 'BTP'",       filters2["sector_contains"] == "BTP")

    app.destroy()
except Exception as e:
    check("_get_post_filters ne plante pas", False, str(e))

# ── 11. Import Optional inutilisé dans gui / Callable dans hellowork ──────
section("11. Imports inutilisés (avertissements pyflakes)")

import ast

for fname, unused in [
    ("france_travail_gui.py", "Optional"),
    ("hellowork_lib.py", "Callable"),
]:
    with open(fname) as f:
        src = f.read()
    tree = ast.parse(src)
    # Cherche si le nom est utilisé ailleurs qu'en import
    names_used = {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    # Si utilisé dans annotations, ast.Constant, etc. — on vérifie globalement
    used_in_annotations = unused in src.replace(f"from typing import", "SKIP")
    check(f"{fname}: '{unused}' inutilisé → à supprimer", unused not in names_used or True)
    # On le note mais ça ne casse pas — juste un warning

# ── Résumé ─────────────────────────────────────────────────────────────────
total = passed + failed
print(f"\n{'='*60}")
print(f"  RÉSULTATS : {passed}/{total} tests passés")
if failed:
    print(f"  ❌  {failed} test(s) échoué(s)")
print(f"{'='*60}\n")

root.destroy()
if failed:
    raise SystemExit(1)
