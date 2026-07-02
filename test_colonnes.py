"""
test_colonnes.py — Sélection de colonnes CSV
"""
import os, csv, tempfile, shutil
import tkinter as tk

root = tk.Tk()
root.withdraw()

import france_travail_lib as ftl
import hellowork_lib as hwl
import export_common as ec
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
    print(f"\n{'='*55}\n  {title}\n{'='*55}")

def make_row(i):
    return {
        "source": "France Travail", "id": f"FT-{i:04d}",
        "intitule": f"Poste {i}", "entreprise": f"Corp {i}",
        "entreprise_url": "", "ville": "Tours", "region": "",
        "code_postal": "37000", "secteur": "Construction", "domaine": "BTP",
        "type_contrat": "CDI", "teletravail": "",
        "salaire_libelle": "30k", "salaire_min": "28000", "salaire_max": "32000",
        "experience": "2 ans", "formation": "CAP",
        "competences": "Toiture", "taille_entreprise": "20",
        "effectif_entreprise": "10-50", "date_publication": "2026-07-01",
        "description": f"Description {i}.",
        "url": f"https://offre.fr/FT-{i:04d}",
    }

rows = [make_row(i) for i in range(5)]
TMPDIR = "/Volumes/disque dur externe 1/scrapa/__test_tmp__"
os.makedirs(TMPDIR, exist_ok=True)

# ── 1. Export avec colonnes partielles ────────────────────────────────────
section("1. export_csv_rows avec colonnes sélectionnées")

partial = ["url", "intitule", "entreprise", "ville", "type_contrat"]
p1 = os.path.join(TMPDIR, "partial.csv")
n1 = ec.export_csv_rows(rows, p1, fieldnames=partial)
check("export partiel retourne 5", n1 == 5)

with open(p1, encoding="utf-8-sig") as f:
    r1 = list(csv.DictReader(f, delimiter=";"))
    c1 = list(r1[0].keys())

check("5 colonnes seulement", len(c1) == 5)
check("url en col 1", c1[0] == "url")
check("description absente", "description" not in c1)
check("salaire absent", "salaire_libelle" not in c1)
check("données intactes", r1[0]["intitule"] == "Poste 0")
check("ville présente", r1[0]["ville"] == "Tours")

# ── 2. Export toutes colonnes ──────────────────────────────────────────────
section("2. Export toutes les colonnes UNIFIED_FIELDNAMES")

p2 = os.path.join(TMPDIR, "complet.csv")
n2 = ec.export_csv_rows(rows, p2, fieldnames=hwl.UNIFIED_FIELDNAMES)
with open(p2, encoding="utf-8-sig") as f:
    c2 = list(csv.DictReader(f, delimiter=";").fieldnames)
check("toutes colonnes présentes", len(c2) == len(hwl.UNIFIED_FIELDNAMES))
check("url toujours en col 1", c2[0] == "url")

# ── 3. Export colonne inexistante → vide sans crash ────────────────────────
section("3. Colonne inexistante → vide, pas de crash")

p3 = os.path.join(TMPDIR, "ghost.csv")
try:
    n3 = ec.export_csv_rows(rows, p3, fieldnames=["url", "champ_fantome"])
    with open(p3, encoding="utf-8-sig") as f:
        r3 = list(csv.DictReader(f, delimiter=";"))
    check("export sans crash", True)
    check("champ_fantome = vide", r3[0].get("champ_fantome", "") == "")
    check("url toujours là", r3[0]["url"].startswith("https://"))
except Exception as e:
    check("export sans crash", False, str(e))

# ── 4. _get_selected_columns via App ──────────────────────────────────────
section("4. GUI — _get_selected_columns")

try:
    app = gui.App.__new__(gui.App)
    tk.Tk.__init__(app)
    app.withdraw()
    app._res_sort_col = None
    app._res_sort_asc = True
    app._res_filtered = []
    app._col_vars = {}
    app._setup_style()

    # Initialise toutes les vars à True
    for field in hwl.UNIFIED_FIELDNAMES:
        app._col_vars[field] = tk.BooleanVar(value=True)

    # Toutes cochées → retourne tout
    sel_all = app._get_selected_columns()
    check("tout coché → toutes colonnes", len(sel_all) == len(hwl.UNIFIED_FIELDNAMES))
    check("url en premier", sel_all[0] == "url")

    # Désélectionner description + salaires
    for f in ["description", "salaire_min", "salaire_max", "salaire_libelle"]:
        app._col_vars[f].set(False)
    sel_partial = app._get_selected_columns()
    check("description absente", "description" not in sel_partial)
    check("salaire_min absent", "salaire_min" not in sel_partial)
    check(f"{len(hwl.UNIFIED_FIELDNAMES)-4} colonnes restantes",
          len(sel_partial) == len(hwl.UNIFIED_FIELDNAMES) - 4)

    # _select_essential_cols
    app._select_essential_cols()
    sel_ess = app._get_selected_columns()
    ESSENTIELLES = {"url","intitule","entreprise","ville","type_contrat",
                    "salaire_libelle","teletravail","date_publication",
                    "experience","secteur","description"}
    check("url dans essentielles", "url" in sel_ess)
    check("description dans essentielles", "description" in sel_ess)
    check("salaire_min absent des essentielles", "salaire_min" not in sel_ess)
    check("code_postal absent des essentielles", "code_postal" not in sel_ess)
    check("11 colonnes essentielles", len(sel_ess) == 11)

    # _select_all_cols
    app._select_all_cols()
    check("select_all → tout", len(app._get_selected_columns()) == len(hwl.UNIFIED_FIELDNAMES))

    # _deselect_all_cols → fallback 3 min
    app._deselect_all_cols()
    sel_none = app._get_selected_columns()
    check("deselect_all → fallback 3 colonnes", len(sel_none) == 3)
    check("fallback url", "url" in sel_none)
    check("fallback intitule", "intitule" in sel_none)
    check("fallback entreprise", "entreprise" in sel_none)

    # _col_info_text
    app._select_all_cols()
    info = app._col_info_text()
    check("col_info_text format", "/" in info and "colonnes" in info)

    app.destroy()
    check("app.destroy() OK", True)

except Exception as e:
    check("GUI _get_selected_columns", False, str(e))
    import traceback; traceback.print_exc()

# ── 5. Export avec colonnes essentielles — données FT+HW intactes ─────────
section("5. Colonnes essentielles — données FT et HW intactes")

ft_row = ftl.flatten_offer_unified({
    "id": "FT-E01",
    "intitule": "Couvreur",
    "entreprise": {"nom": "BTP Tours"},
    "lieuTravail": {"libelle": "Tours (37)", "codePostal": "37000"},
    "salaire": {"libelle": "2200-2500 €/mois"},
    "typeContrat": "CDI",
    "experienceLibelle": "3 ans min.",
    "secteurActiviteLibelle": "Construction",
    "dateCreation": "2026-07-01T08:00:00Z",
    "origineOffre": {"urlOrigine": "https://ft.fr/E01"},
    "description": "Couvreur à Tours.",
})

hw_row = {
    "source":"HelloWork","id":"HW-E01","intitule":"Développeur Python",
    "entreprise":"Tech Co","entreprise_url":"https://techco.com",
    "ville":"Paris","region":"IDF","code_postal":"75001",
    "secteur":"Informatique","domaine":"Dev","type_contrat":"CDI",
    "teletravail":"FULL","salaire_libelle":"45-55k",
    "salaire_min":45000,"salaire_max":55000,"experience":"3 ans",
    "formation":"Bac+5","competences":"Python, Django",
    "taille_entreprise":"50","effectif_entreprise":"50-100",
    "date_publication":"2026-07-01","description":"Dev Python remote.",
    "url":"https://hellowork.com/HW-E01",
}

ess_fields = ["url","intitule","entreprise","ville","type_contrat",
              "salaire_libelle","teletravail","date_publication",
              "experience","secteur","description"]

p5 = os.path.join(TMPDIR, "essentielles.csv")
n5 = ec.export_csv_rows([ft_row, hw_row], p5, fieldnames=ess_fields)
check("export essentielles OK", n5 == 2)

with open(p5, encoding="utf-8-sig") as f:
    reader5 = csv.DictReader(f, delimiter=";")
    rows5 = list(reader5)
    cols5 = reader5.fieldnames

check("11 colonnes dans CSV", len(cols5) == 11)
check("url col 1", cols5[0] == "url")
ft5 = rows5[0]
hw5 = rows5[1]
check("FT url",        ft5["url"] == "https://ft.fr/E01")
check("FT entreprise", ft5["entreprise"] == "BTP Tours")
check("FT salaire",    ft5["salaire_libelle"] == "2200-2500 €/mois")
check("FT secteur",    ft5["secteur"] == "Construction")
check("HW url",        hw5["url"] == "https://hellowork.com/HW-E01")
check("HW teletravail",hw5["teletravail"] == "FULL")
check("HW salaire",    hw5["salaire_libelle"] == "45-55k")
check("code_postal absent", "code_postal" not in cols5)
check("salaire_min absent", "salaire_min" not in cols5)

# ── 6. Ordre colonnes — url remontée auto ─────────────────────────────────
section("6. Ordre des colonnes — url toujours en col 1")

custom = ["intitule", "url", "ville", "type_contrat"]
p6 = os.path.join(TMPDIR, "ordre.csv")
ec.export_csv_rows([ft_row], p6, fieldnames=custom)
with open(p6, encoding="utf-8-sig") as f:
    c6 = list(csv.DictReader(f, delimiter=";").fieldnames)
check("url remontée en col 1 (ordre custom)", c6[0] == "url")
check("autres colonnes dans l'ordre", c6[1:] == ["intitule", "ville", "type_contrat"])

# ── Nettoyage et résumé ────────────────────────────────────────────────────
shutil.rmtree(TMPDIR, ignore_errors=True)
root.destroy()

total = passed + failed
print(f"\n{'='*55}")
print(f"  RÉSULTATS : {passed}/{total} tests passés")
if failed:
    print(f"  ❌  {failed} test(s) échoué(s)")
else:
    print(f"  ✅  Sélection de colonnes 100% fonctionnelle")
    print(f"  ✅  Essentielles / Tout / Rien opérationnels")
    print(f"  ✅  Données FT+HW intactes après sélection")
    print(f"  ✅  URL toujours en première colonne")
print(f"{'='*55}\n")

if failed:
    raise SystemExit(1)
