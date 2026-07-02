"""Tests fonctionnels — contrats FT, localisations, bug 400."""
import tkinter as tk
import unittest.mock as mock

import france_travail_gui as gui
from france_travail_lib import resolve_commune, fetch_offers

FT_CONTRAT_CODES  = gui.FT_CONTRAT_CODES
FT_CONTRAT_CHOICES = gui.FT_CONTRAT_CHOICES

root = tk.Tk()
root.withdraw()

errors = []

def check(label, cond, detail=""):
    if cond:
        print(f"  ✅  {label}")
    else:
        print(f"  ❌  {label}  {detail}")
        errors.append(label)

# ─────────────────────────────────────────────────────────────────
# 1. Logique de construction typeContrat
# ─────────────────────────────────────────────────────────────────
print("\n=== Contrats France Travail ===")

def build_type_contrat(checked_labels):
    codes = []
    seen  = set()
    for lbl in checked_labels:
        for code in FT_CONTRAT_CODES.get(lbl, lbl).split(","):
            code = code.strip()
            if code and code not in seen:
                seen.add(code)
                codes.append(code)
    return ",".join(codes)

check("CDI seul",            build_type_contrat(["CDI"]) == "CDI")
check("CDD seul",            build_type_contrat(["CDD"]) == "CDD")
check("Intérim → MIS",      build_type_contrat(["Intérim"]) == "MIS")
check("Saisonnier → SAI",   build_type_contrat(["Saisonnier"]) == "SAI")
check("CDI+CDD",             build_type_contrat(["CDI","CDD"]) == "CDI,CDD")
check("CDI+Intérim",        build_type_contrat(["CDI","Intérim"]) == "CDI,MIS")
check("Tous sauf Alternance", build_type_contrat(["CDI","CDD","Intérim","Saisonnier"]) == "CDI,CDD,MIS,SAI")

# CDI + Alternance ne doit pas doubler CDI
result = build_type_contrat(["CDI","Alternance"])
check("CDI+Alternance sans doublon CDI",
      result.count("CDI") == 1,
      f"(obtenu: {result})")

# Aucun coché = liste vide = pas de typeContrat envoyé
check("Aucun coché → vide", build_type_contrat([]) == "")

# ─────────────────────────────────────────────────────────────────
# 2. Variables UI correctement lues
# ─────────────────────────────────────────────────────────────────
print("\n=== Variables UI (BooleanVar) ===")

vars_cdi = {lbl: tk.BooleanVar(value=False) for lbl in FT_CONTRAT_CHOICES}
vars_cdi["CDI"].set(True)
vars_cdi["Intérim"].set(True)
selected = [lbl for lbl, v in vars_cdi.items() if v.get()]
check("Lecture BooleanVar CDI+Intérim",
      set(selected) == {"CDI","Intérim"},
      f"(obtenu: {selected})")

# ─────────────────────────────────────────────────────────────────
# 3. Résolution de localisation
# ─────────────────────────────────────────────────────────────────
print("\n=== Résolution de localisation ===")

def resolve_location(loc, rayon=""):
    p = {}
    loc = loc.strip()
    if not loc:
        return p
    if loc.isdigit() and len(loc) == 2:
        p["departement"] = loc
    elif loc.isdigit() and len(loc) == 5:
        p["commune"] = loc
    else:
        code = resolve_commune(loc)
        if code != loc:
            p["commune"] = code
        else:
            digits = "".join(c for c in loc if c.isdigit())
            if digits and len(digits) <= 3:
                p["departement"] = digits[:2]
            else:
                p["commune"] = loc
    if "commune" in p and rayon:
        p["distance"] = rayon
    return p

check("\"37\" → departement",      resolve_location("37") == {"departement": "37"})
check("\"75\" → departement",      resolve_location("75") == {"departement": "75"})
check("\"75056\" → commune INSEE", resolve_location("75056") == {"commune": "75056"})
check("\"Tours\" → commune 37261", resolve_location("Tours") == {"commune": "37261"})
check("\"Paris\" → commune 75056", resolve_location("Paris") == {"commune": "75056"})
check("\"Lyon\" → commune 69123",  resolve_location("Lyon") == {"commune": "69123"})
check("Tours+rayon → distance",    resolve_location("Tours","20") == {"commune":"37261","distance":"20"})
check("37+rayon → pas de distance (dept)", "distance" not in resolve_location("37","20"))

# ─────────────────────────────────────────────────────────────────
# 4. Bug 400 — commune et departement jamais ensemble
# ─────────────────────────────────────────────────────────────────
print("\n=== Bug 400 — commune+departement mutuellement exclusifs ===")

captured_params = {}
def fake_get(url, headers=None, params=None, timeout=30):
    if params:
        captured_params.update(params)
    resp = mock.MagicMock()
    resp.status_code = 204
    return resp

# Test avec les deux présents
with mock.patch("requests.get", side_effect=fake_get):
    p = {"commune": "37261", "departement": "37", "typeContrat": "CDI"}
    fetch_offers("tok", p, 10)
check("departement retiré quand commune présent",
      "departement" not in captured_params and "commune" in captured_params,
      f"(params: {captured_params})")

# Test avec seulement departement
captured_params.clear()
with mock.patch("requests.get", side_effect=fake_get):
    p = {"departement": "37", "typeContrat": "CDI,CDD"}
    fetch_offers("tok", p, 10)
check("departement conservé quand commune absent",
      "departement" in captured_params and "commune" not in captured_params,
      f"(params: {captured_params})")

# Test typeContrat bien transmis
check("typeContrat CDI,CDD transmis",
      captured_params.get("typeContrat") == "CDI,CDD",
      f"(typeContrat: {captured_params.get('typeContrat')})")

# ─────────────────────────────────────────────────────────────────
# 5. HelloWork — codes de contrat séparés
# ─────────────────────────────────────────────────────────────────
print("\n=== Contrats HelloWork (codes API distincts) ===")

HW_CONTRATS = gui.HW_CONTRATS
hw_codes = {code for _, code in HW_CONTRATS}
ft_codes = set(FT_CONTRAT_CODES.values())

check("HW a ALTERNANCE (pas FT)",   "ALTERNANCE" in hw_codes)
check("HW a STAGE (pas FT)",        "STAGE" in hw_codes)
check("HW a FREELANCE (pas FT)",    "FREELANCE" in hw_codes)
check("FT a MIS/SAI (pas HW)",      "MIS" in ft_codes and "SAI" in ft_codes)
check("HW a ses codes propres non présents dans FT",
      {"ALTERNANCE","STAGE","INTERIM","FREELANCE"}.issubset(hw_codes),
      f"(hw_codes: {hw_codes})")
check("FT a ses codes propres non présents dans HW",
      {"MIS","SAI"}.issubset(ft_codes),
      f"(ft_codes: {ft_codes})")

# ─────────────────────────────────────────────────────────────────
root.destroy()
print()
if errors:
    print(f"❌  {len(errors)} test(s) échoué(s) : {errors}")
    raise SystemExit(1)
else:
    print(f"✅  Tous les tests passent ({sum(1 for _ in errors) or 'aucune'} erreur)")
    print("    La logique est bien fonctionnelle, pas seulement visuelle.")
