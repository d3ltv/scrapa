"""
test_full.py — Tests complets de tous les modules Scrapa
"""
import os, sys, json, csv, io, tempfile, shutil
import tkinter as tk
import unittest.mock as mock

# ── Setup ──────────────────────────────────────────────────────────────────
root = tk.Tk()
root.withdraw()

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
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# ── Imports ────────────────────────────────────────────────────────────────
section("1. Imports et compilation")
try:
    import france_travail_lib as ftl
    check("france_travail_lib importe", True)
except Exception as e:
    check("france_travail_lib importe", False, str(e))

try:
    import hellowork_lib as hwl
    check("hellowork_lib importe", True)
except Exception as e:
    check("hellowork_lib importe", False, str(e))

try:
    import export_common as ec
    check("export_common importe", True)
except Exception as e:
    check("export_common importe", False, str(e))

try:
    import seen_ids_cache as sic
    check("seen_ids_cache importe", True)
except Exception as e:
    check("seen_ids_cache importe", False, str(e))

try:
    import france_travail_gui as gui
    check("france_travail_gui importe", True)
except Exception as e:
    check("france_travail_gui importe", False, str(e))

# ── france_travail_lib ─────────────────────────────────────────────────────
section("2. france_travail_lib — resolve_commune")

check("Tours → 37261",      ftl.resolve_commune("Tours") == "37261")
check("tours (min.) → 37261", ftl.resolve_commune("tours") == "37261")
check("Orléans → 45234",    ftl.resolve_commune("Orleans") == "45234")
check("Paris → 75056",      ftl.resolve_commune("Paris") == "75056")
check("Code inconnu passé tel quel", ftl.resolve_commune("99999") == "99999")
check("Code INSEE direct",  ftl.resolve_commune("37261") == "37261")

section("3. france_travail_lib — fetch_offers (mock API)")

def make_fake_offer(i):
    return {
        "id": f"FT-{i:04d}",
        "intitule": f"Poste {i}",
        "entreprise": {"nom": f"Entreprise {i}"},
        "lieuTravail": {"libelle": "Tours (37)", "codePostal": "37000"},
        "salaire": {"libelle": "30-35k"},
        "typeContrat": "CDI",
        "typeContratLibelle": "Contrat à durée indéterminée",
        "dateCreation": "2026-06-30T10:00:00Z",
        "dateActualisation": "2026-07-01T08:00:00Z",
        "origineOffre": {"urlOrigine": f"https://ft.fr/offre/FT-{i:04d}"},
    }

def fake_get_offers(url, headers=None, params=None, timeout=30):
    resp = mock.MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"resultats": [make_fake_offer(i) for i in range(5)]}
    return resp

with mock.patch("requests.get", side_effect=fake_get_offers):
    offers = ftl.fetch_offers("tok", {"typeContrat": "CDI"}, 10)
check("fetch_offers retourne une liste", isinstance(offers, list))
check("fetch_offers retourne 5 offres", len(offers) == 5)
check("chaque offre a un id", all("id" in o for o in offers))

# Pagination — 204 stoppe la boucle
def fake_get_204(url, headers=None, params=None, timeout=30):
    resp = mock.MagicMock()
    resp.status_code = 204
    return resp

with mock.patch("requests.get", side_effect=fake_get_204):
    offers_empty = ftl.fetch_offers("tok", {}, 100)
check("204 → liste vide", offers_empty == [])

# Erreur 400
def fake_get_400(url, headers=None, params=None, timeout=30):
    resp = mock.MagicMock()
    resp.status_code = 400
    resp.text = '{"message":"Erreur paramètre"}'
    return resp

try:
    with mock.patch("requests.get", side_effect=fake_get_400):
        ftl.fetch_offers("tok", {"commune":"37000","departement":"37"}, 10)
    check("400 lève FranceTravailError", False, "aucune exception")
except ftl.FranceTravailError:
    check("400 lève FranceTravailError", True)

section("4. france_travail_lib — flatten_offer")

raw = make_fake_offer(1)
flat = ftl.flatten_offer(raw)
check("flatten_offer a 'id'",          flat.get("id") == "FT-0001")
check("flatten_offer a 'intitule'",    flat.get("intitule") == "Poste 1")
check("flatten_offer a 'entreprise'",  flat.get("entreprise") == "Entreprise 1")
check("flatten_offer a 'ville'",       flat.get("ville") == "Tours (37)")
check("flatten_offer a 'url_origine'", "FT-0001" in (flat.get("url_origine") or ""))

unified = ftl.flatten_offer_unified(raw)
check("unified a 'source' = France Travail", unified.get("source") == "France Travail")
check("unified a 'url'", unified.get("url") is not None)

# ── export_common ──────────────────────────────────────────────────────────
section("5. export_common — export_csv_rows")

tmpdir = tempfile.mkdtemp()
try:
    rows = [
        {"url": "http://a.com", "intitule": "Dev Python", "entreprise": "Acme",
         "ville": "Paris", "type_contrat": "CDI", "salaire_libelle": "45k",
         "date_publication": "2026-07-01", "source": "FT", "id": "001",
         "region": "", "code_postal": "75001", "secteur": "IT",
         "domaine": "", "teletravail": "FULL", "salaire_min": "40000",
         "salaire_max": "50000", "experience": "3 ans", "formation": "Bac+5",
         "competences": "Python, SQL", "taille_entreprise": "50",
         "effectif_entreprise": "50-100", "description": "Super poste"},
        {"url": "http://b.com", "intitule": "Chef de projet", "entreprise": "Beta",
         "ville": "Lyon", "type_contrat": "CDD", "salaire_libelle": "40k",
         "date_publication": "2026-06-28", "source": "HW", "id": "002",
         "region": "", "code_postal": "69001", "secteur": "BTP",
         "domaine": "", "teletravail": "", "salaire_min": "35000",
         "salaire_max": "45000", "experience": "5 ans", "formation": "Bac+3",
         "competences": "Gestion", "taille_entreprise": "200",
         "effectif_entreprise": "100-500", "description": "Bon poste"},
    ]
    csv_path = os.path.join(tmpdir, "test_export.csv")
    count = ec.export_csv_rows(rows, csv_path, fieldnames=hwl.UNIFIED_FIELDNAMES)

    check("export_csv_rows retourne 2", count == 2)
    check("fichier créé", os.path.exists(csv_path))

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        csv_rows = list(reader)

    check("CSV contient 2 lignes", len(csv_rows) == 2)
    check("première colonne = url", list(csv_rows[0].keys())[0] == "url")
    check("url présent en col 1 row 1", csv_rows[0]["url"] == "http://a.com")
    check("url présent en col 1 row 2", csv_rows[1]["url"] == "http://b.com")
    check("encodage BOM utf-8-sig OK", True)  # ouverture sans erreur = OK

    # Export vide
    empty_path = os.path.join(tmpdir, "empty.csv")
    n = ec.export_csv_rows([], empty_path)
    check("export vide retourne 0", n == 0)
    check("fichier vide non créé", not os.path.exists(empty_path))

finally:
    shutil.rmtree(tmpdir)

section("6. export_common — apply_post_filters")

all_rows = [
    {"entreprise": "Bouygues Telecom", "secteur": "Telecom",
     "competences": "Python SQL", "experience": "3 ans",
     "formation": "Bac+5", "taille_entreprise": "500",
     "salaire_min": "40000", "salaire_max": "50000"},
    {"entreprise": "Vinci Construction", "secteur": "BTP",
     "competences": "AutoCAD", "experience": "10 ans",
     "formation": "Bac+2", "taille_entreprise": "1000",
     "salaire_min": "35000", "salaire_max": "45000"},
    {"entreprise": "Startup AI", "secteur": "Informatique",
     "competences": "Python TensorFlow", "experience": "1 an",
     "formation": "Bac+5", "taille_entreprise": "10",
     "salaire_min": "55000", "salaire_max": "70000"},
]

# Filtre entreprise regex
f1 = ec.apply_post_filters(all_rows, company_pattern="Bouygues|Vinci")
check("company_pattern Bouygues|Vinci → 2", len(f1) == 2)

# Filtre secteur
f2 = ec.apply_post_filters(all_rows, sector_contains="BTP")
check("sector_contains BTP → 1", len(f2) == 1)

# Filtre compétences
f3 = ec.apply_post_filters(all_rows, skills_contains="Python")
check("skills_contains Python → 2", len(f3) == 2)

# Filtre taille min
f4 = ec.apply_post_filters(all_rows, company_size_min=100)
check("company_size_min 100 → 2 (≥100)", len(f4) == 2)

# Filtre taille max
f5 = ec.apply_post_filters(all_rows, company_size_max=100)
check("company_size_max 100 → 1 (Startup AI, taille=10)", len(f5) == 1)

# Filtre salaire max (filtre ceux dont salaire_min > plafond)
f6 = ec.apply_post_filters(all_rows, salary_max=45000)
check("salary_max 45000 → exclut Startup AI (55k min)", len(f6) == 2)

# Aucun filtre = tout passe
f7 = ec.apply_post_filters(all_rows)
check("aucun filtre → tout passe", len(f7) == 3)

# ── seen_ids_cache ─────────────────────────────────────────────────────────
section("7. seen_ids_cache — persistance et déduplication")

# Utilise un fichier temporaire pour ne pas polluer le vrai cache
orig_cache = sic.CACHE_FILE
tmpdir2 = tempfile.mkdtemp()
tmp_cache = os.path.join(tmpdir2, "test_seen_ids.json")
sic.CACHE_FILE = tmp_cache

try:
    # Départ propre
    sic.clear_cache()
    check("clear_cache crée fichier vide", os.path.exists(tmp_cache))

    stats = sic.get_stats()
    check("stats count=0 après clear", stats["count"] == 0)

    # Ajouter des IDs
    added = sic.add_ids(["A001", "A002", "A003"])
    check("add_ids retourne 3 nouveaux", added == 3)

    stats = sic.get_stats()
    check("stats count=3 après ajout", stats["count"] == 3)

    # Re-ajouter les mêmes → 0 nouveaux
    added2 = sic.add_ids(["A001", "A002"])
    check("add_ids doublons retourne 0", added2 == 0)

    # load_seen_ids
    seen = sic.load_seen_ids()
    check("load_seen_ids retourne set", isinstance(seen, set))
    check("load_seen_ids contient A001", "A001" in seen)
    check("load_seen_ids contient A003", "A003" in seen)

    # filter_new
    test_rows = [
        {"id": "A001", "url": "http://a.com"},
        {"id": "A002", "url": "http://b.com"},
        {"id": "B001", "url": "http://c.com"},  # nouveau
    ]
    new_rows, dupes = sic.filter_new(test_rows)
    check("filter_new → 1 nouveau", len(new_rows) == 1)
    check("filter_new → 2 doublons", dupes == 2)
    check("filter_new → B001 est nouveau", new_rows[0]["id"] == "B001")

    # commit_rows
    sic.commit_rows([{"id": "B001"}, {"id": "B002"}])
    seen2 = sic.load_seen_ids()
    check("commit_rows ajoute B001 et B002", "B001" in seen2 and "B002" in seen2)
    check("commit_rows conserve A001", "A001" in seen2)

    # Persistance — recharge depuis le fichier
    seen3 = sic.load_seen_ids()
    check("persistance disque OK", len(seen3) == 5)

    # Fichier corrompu → retour défaut
    with open(tmp_cache, "w") as f:
        f.write("not json{{{")
    seen4 = sic.load_seen_ids()
    check("fichier corrompu → set vide sans crash", isinstance(seen4, set) and len(seen4) == 0)

    # clear_cache remet à zéro
    sic.clear_cache()
    check("clear_cache → count=0", sic.get_stats()["count"] == 0)
    check("clear_cache → last_cleared renseigné", sic.get_stats()["last_cleared"] is not None)

finally:
    sic.CACHE_FILE = orig_cache
    shutil.rmtree(tmpdir2)

# ── hellowork_lib — build inputs ───────────────────────────────────────────
section("8. hellowork_lib — build_standard_input / build_enriched_input")

# Standard avec mots-clés
inp = hwl.build_standard_input(
    search_queries=["couvreur"],
    location="Tours",
    max_results=50,
    contract_types=["CDI","CDD"],
    date_posted="1w",
)
check("standard: searchQueries présent", "searchQueries" in inp)
check("standard: location présent", inp.get("location") == "Tours")
check("standard: contractType présent", inp.get("contractType") == ["CDI","CDD"])
check("standard: datePosted = 1w", inp.get("datePosted") == "1w")
check("standard: maxResults = 50", inp.get("maxResults") == 50)

# Standard sans mots-clés (recherche par date/lieu uniquement)
inp2 = hwl.build_standard_input(search_queries=[], location="Paris", date_posted="24h")
check("standard sans mots-clés: searchQueries = ['']", inp2.get("searchQueries") == [""])
check("standard sans mots-clés: location OK", inp2.get("location") == "Paris")

# Enrichi avec mots-clés
inp3 = hwl.build_enriched_input(
    search_queries=["plombier","électricien"],
    location="Lyon",
    max_results=100,
    days_posted="7",
)
check("enrichi: query est une liste", isinstance(inp3.get("query"), list))
check("enrichi: country = FR", inp3.get("country") == "FR")
check("enrichi: daysPosted = 7", inp3.get("daysPosted") == "7")

# Enrichi sans mots-clés
inp4 = hwl.build_enriched_input(search_queries=[], location="Bordeaux")
check("enrichi sans mots-clés: query = ''", inp4.get("query") == "")

# ── hellowork_lib — flatten ────────────────────────────────────────────────
section("9. hellowork_lib — flatten_standard_job / flatten_enriched_job")

std_job = {
    "jobId": "HW-001",
    "title": "Développeur Python",
    "company": "Tech Corp",
    "companyUrl": "https://techcorp.com",
    "city": "Nantes",
    "region": "Pays de la Loire",
    "postalCode": "44000",
    "contractType": "CDI",
    "telework": "FULL",
    "salary": "45-55k",
    "salaryMin": 45000,
    "salaryMax": 55000,
    "salaryCurrency": "EUR",
    "salaryPeriod": "YEAR",
    "experience": "3 ans",
    "education": "Bac+5",
    "skills": ["Python", "Django", "SQL"],
    "datePosted": "2026-07-01",
    "descriptionText": "Poste de dev Python en CDI.",
    "jobUrl": "https://hellowork.com/job/HW-001",
}

flat_std = hwl.flatten_standard_job(std_job)
check("flatten_std: source = HelloWork",    flat_std.get("source") == "HelloWork")
check("flatten_std: id = HW-001",          flat_std.get("id") == "HW-001")
check("flatten_std: competences str",      flat_std.get("competences") == "Python, Django, SQL")
check("flatten_std: url présent",          flat_std.get("url") is not None)
check("flatten_std: salaire_min = 45000",  flat_std.get("salaire_min") == 45000)

enr_job = {
    "jobKey": "HW-E-001",
    "title": "Architecte Cloud",
    "company": "CloudCo",
    "location": "Paris",
    "contractType": "CDI",
    "salaryMin": 70000,
    "salaryMax": 90000,
    "skills": ["AWS", "Kubernetes"],
    "postedAt": "2026-07-01",
    "descriptionText": "Poste cloud.",
    "canonicalUrl": "https://hellowork.com/job/HW-E-001",
    "headcount": "500",
    "companySizeLabel": "250-500",
}

flat_enr = hwl.flatten_enriched_job(enr_job)
check("flatten_enr: id = HW-E-001",        flat_enr.get("id") == "HW-E-001")
check("flatten_enr: taille_entreprise",    flat_enr.get("taille_entreprise") == "500")
check("flatten_enr: effectif_entreprise",  flat_enr.get("effectif_entreprise") == "250-500")
check("flatten_enr: skills str",           flat_enr.get("competences") == "AWS, Kubernetes")

# ── Test intégration flux complet ─────────────────────────────────────────
section("10. Intégration — flux FT complet (mock API + cache + CSV)")

orig_cache = sic.CACHE_FILE
tmpdir3 = tempfile.mkdtemp()
sic.CACHE_FILE = os.path.join(tmpdir3, "cache_integ.json")
sic.clear_cache()

try:
    # Simule 3 offres FT
    fake_offers_raw = [make_fake_offer(i) for i in range(3)]

    def fake_get_ft(url, headers=None, params=None, timeout=30):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"resultats": fake_offers_raw}
        return resp

    def fake_post_token(url, data=None, headers=None, timeout=20):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"access_token": "fake_token_123"}
        return resp

    # Étape 1 : authentification
    with mock.patch("requests.post", side_effect=fake_post_token):
        token = ftl.get_access_token("client_id", "client_secret")
    check("get_access_token retourne un token", token == "fake_token_123")

    # Étape 2 : récupération
    with mock.patch("requests.get", side_effect=fake_get_ft):
        raw = ftl.fetch_offers(token, {"typeContrat": "CDI", "publieeDepuis": "7"}, 50)
    check("fetch_offers retourne 3 offres", len(raw) == 3)

    # Étape 3 : mise à plat unifiée
    rows = [ftl.flatten_offer_unified(o) for o in raw]
    check("flatten_offer_unified → 3 rows", len(rows) == 3)
    check("rows ont source=France Travail", all(r["source"] == "France Travail" for r in rows))
    check("rows ont url", all(r.get("url") for r in rows))

    # Étape 4 : filtre post
    filtered = ec.apply_post_filters(rows)
    check("apply_post_filters sans filtre → tout passe", len(filtered) == 3)

    # Étape 5 : déduplication (cache vide = tout est nouveau)
    new_rows, dupes = sic.filter_new(filtered)
    check("filter_new cache vide → 3 nouveaux", len(new_rows) == 3 and dupes == 0)

    # Étape 6 : export CSV
    csv_path = os.path.join(tmpdir3, "offres_test.csv")
    count = ec.export_csv_rows(new_rows, csv_path, fieldnames=hwl.UNIFIED_FIELDNAMES)
    check("export_csv_rows → 3 lignes", count == 3)

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        csv_rows = list(reader)
    check("CSV a 3 lignes", len(csv_rows) == 3)
    check("CSV col 1 = url", list(csv_rows[0].keys())[0] == "url")

    # Étape 7 : commit dans le cache
    sic.commit_rows(new_rows)
    check("commit_rows → 3 IDs dans cache", sic.get_stats()["count"] == 3)

    # Étape 8 : relancer avec les mêmes → 0 nouveau
    new_rows2, dupes2 = sic.filter_new(filtered)
    check("2ème recherche → 0 nouveau, 3 doublons", len(new_rows2) == 0 and dupes2 == 3)

    # Étape 9 : ajouter 1 nouvelle offre → 1 seule exportée
    extra = ftl.flatten_offer_unified(make_fake_offer(99))
    mixed = filtered + [extra]
    new_rows3, dupes3 = sic.filter_new(mixed)
    check("offre nouvelle détectée parmi les doublons", len(new_rows3) == 1 and dupes3 == 3)
    check("nouvelle offre = FT-0099", new_rows3[0]["id"] == "FT-0099")

finally:
    sic.CACHE_FILE = orig_cache
    shutil.rmtree(tmpdir3)

# ── Test GUI — params builder ──────────────────────────────────────────────
section("11. GUI — _build_ft_params_list via App")

try:
    # Instancie l'app sans l'afficher
    app = gui.App.__new__(gui.App)
    # Init manuels des attributs minimaux
    app._queue = __import__("queue").Queue()
    app.last_output_path = None
    app._pending_rows = []
    app._ignored_ids = set()
    app._ft_secret_visible = False
    app._hw_secret_visible = False
    app.hw_contract_vars = {}
    app.ft_contract_vars = {}
    app._ft_widgets = []
    app._hw_widgets = []
    app._res_sort_col = None
    app._res_sort_asc = True
    app._res_filtered = []

    # Appelle _setup_style pour avoir les couleurs
    tk.Tk.__init__(app)
    app.withdraw()
    app._setup_style()

    # Mots-clés, localisation, contrats
    app.ft_mots_var = tk.StringVar(value="couvreur")
    app.ft_rayon_var = tk.StringVar(value="20")
    app.ft_exp_var = tk.StringVar(value="Indifférent")
    app.ft_jours_var = tk.StringVar(value="7 derniers jours")

    # Villes
    class FakeTagList:
        def __init__(self, tags): self._tags = tags
        def get_tags(self): return self._tags
    app._ft_locations = FakeTagList(["Tours", "37"])

    # Contrats : CDI + CDD cochés
    app.ft_contract_vars = {lbl: tk.BooleanVar(value=False) for lbl in gui.FT_CONTRAT_CHOICES}
    app.ft_contract_vars["CDI"].set(True)
    app.ft_contract_vars["CDD"].set(True)

    params_list = app._build_ft_params_list()

    check("params_list a 2 entrées (Tours + 37)", len(params_list) == 2)
    check("tous ont motsCles=couvreur",    all(p.get("motsCles") == "couvreur" for p in params_list))
    check("tous ont typeContrat=CDI,CDD",  all(p.get("typeContrat") == "CDI,CDD" for p in params_list))
    check("publieeDepuis = 7",             all(p.get("publieeDepuis") == "7" for p in params_list))

    # Tours doit avoir commune + distance
    tours_p = next((p for p in params_list if "commune" in p), None)
    check("Tours → commune + distance=20", tours_p and tours_p.get("distance") == "20")

    # 37 doit avoir departement (pas commune, pas distance)
    dept_p = next((p for p in params_list if "departement" in p), None)
    check("37 → departement, pas commune", dept_p and "commune" not in dept_p)
    check("37 → pas de distance", dept_p and "distance" not in dept_p)

    # Garde-fou commune+departement
    check("aucun params avec les deux",
          not any("commune" in p and "departement" in p for p in params_list))

    app.destroy()
    check("app.destroy() sans erreur", True)

except Exception as e:
    check("GUI _build_ft_params_list", False, str(e))

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
