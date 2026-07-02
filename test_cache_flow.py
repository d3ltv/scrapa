"""
test_cache_flow.py
==================
Vérifie que :
1. filter_new (lecture cache) est appelé pendant la RECHERCHE
2. commit_rows (écriture cache) est appelé UNIQUEMENT lors du TÉLÉCHARGEMENT
3. Ça fonctionne pour FT et HW
4. Si on ferme sans télécharger, le cache reste vide
5. Téléchargement partiel (sélection filtrée) ne commit que les rows téléchargées
"""
import os, sys, csv, tempfile, shutil
import tkinter as tk
import unittest.mock as mock
import seen_ids_cache as sic
import france_travail_lib as ftl
import hellowork_lib as hwl
import export_common as ec

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
    print(f"\n{'='*60}\n  {title}\n{'='*60}")

# Isole le cache dans un dossier temporaire
orig_cache = sic.CACHE_FILE
tmpdir = tempfile.mkdtemp()
sic.CACHE_FILE = os.path.join(tmpdir, "test_flow_cache.json")
sic.clear_cache()

# ── Données de test ────────────────────────────────────────────────────────
def make_ft_row(i):
    return ftl.flatten_offer_unified({
        "id": f"FT-{i:04d}",
        "intitule": f"Poste FT {i}",
        "entreprise": {"nom": f"Corp {i}"},
        "lieuTravail": {"libelle": "Tours", "codePostal": "37000"},
        "salaire": {}, "contact": {},
        "origineOffre": {"urlOrigine": f"https://ft.fr/{i}"},
        "typeContrat": "CDI",
        "dateCreation": "2026-07-01T00:00:00Z",
    })

def make_hw_row(i):
    return hwl.flatten_standard_job({
        "jobId": f"HW-{i:04d}",
        "title": f"Poste HW {i}",
        "company": f"Corp {i}",
        "city": "Paris",
        "contractType": "CDI",
        "datePosted": "2026-07-01",
        "jobUrl": f"https://hellowork.com/{i}",
    })

ft_rows = [make_ft_row(i) for i in range(5)]   # FT-0000..FT-0004
hw_rows = [make_hw_row(i) for i in range(5)]   # HW-0000..HW-0004
all_rows = ft_rows + hw_rows  # 10 offres au total

# ── 1. RECHERCHE — filter_new en lecture seule ─────────────────────────────
section("1. RECHERCHE — filter_new ne modifie pas le cache")

check("cache vide au départ", sic.get_stats()["count"] == 0)

new_rows, dupes = sic.filter_new(all_rows)

check("filter_new retourne 10 nouvelles (cache vide)", len(new_rows) == 10)
check("filter_new retourne 0 doublon", dupes == 0)
check("cache TOUJOURS vide après filter_new", sic.get_stats()["count"] == 0)

# Simuler une 2ème recherche sans avoir téléchargé
new_rows2, dupes2 = sic.filter_new(all_rows)
check("2ème recherche sans DL → encore 10 nouvelles", len(new_rows2) == 10)
check("cache TOUJOURS vide (pas de DL entre les deux)", sic.get_stats()["count"] == 0)

# ── 2. TÉLÉCHARGEMENT TOUT — commit uniquement à ce moment ────────────────
section("2. TÉLÉCHARGEMENT TOUT — commit après export CSV")

csv_path = os.path.join(tmpdir, "export_tout.csv")
ec.export_csv_rows(all_rows, csv_path, fieldnames=hwl.UNIFIED_FIELDNAMES)
check("CSV créé", os.path.exists(csv_path))

# Seulement maintenant on commit
sic.commit_rows(all_rows)

check("cache contient 10 IDs après commit", sic.get_stats()["count"] == 10)

# Vérifie que FT et HW sont tous les deux dans le cache
seen = sic.load_seen_ids()
check("FT-0000 dans le cache", "FT-0000" in seen)
check("FT-0004 dans le cache", "FT-0004" in seen)
check("HW-0000 dans le cache", "HW-0000" in seen)
check("HW-0004 dans le cache", "HW-0004" in seen)

# ── 3. RECHERCHE APRÈS DL — doublons détectés ─────────────────────────────
section("3. RECHERCHE APRÈS DL — doublons FT et HW détectés")

new_rows3, dupes3 = sic.filter_new(all_rows)
check("après DL: 0 nouvelles", len(new_rows3) == 0)
check("après DL: 10 doublons", dupes3 == 10)
check("cache non modifié (lecture seule)", sic.get_stats()["count"] == 10)

# Mélange connu + nouveau
extra_ft = make_ft_row(99)   # FT-0099 — nouveau
extra_hw = make_hw_row(99)   # HW-0099 — nouveau
mixed = all_rows + [extra_ft, extra_hw]

new_rows4, dupes4 = sic.filter_new(mixed)
check("mélange: 2 nouvelles (FT+HW)", len(new_rows4) == 2)
check("mélange: 10 doublons", dupes4 == 10)
check("FT-0099 est nouveau", any(r["id"] == "FT-0099" for r in new_rows4))
check("HW-0099 est nouveau", any(r["id"] == "HW-0099" for r in new_rows4))
check("cache non modifié après filter_new", sic.get_stats()["count"] == 10)

# ── 4. TÉLÉCHARGEMENT PARTIEL (sélection filtrée) ─────────────────────────
section("4. TÉLÉCHARGEMENT PARTIEL — seuls les rows téléchargées sont commitées")

# Reset cache
sic.clear_cache()
check("cache remis à 0", sic.get_stats()["count"] == 0)

# On a 10 rows mais on télécharge seulement les 3 premières (FT-0000, FT-0001, FT-0002)
partial = ft_rows[:3]
csv_partial = os.path.join(tmpdir, "export_partiel.csv")
ec.export_csv_rows(partial, csv_partial, fieldnames=hwl.UNIFIED_FIELDNAMES)
sic.commit_rows(partial)

check("cache contient 3 IDs après DL partiel", sic.get_stats()["count"] == 3)

seen2 = sic.load_seen_ids()
check("FT-0000 commitée", "FT-0000" in seen2)
check("FT-0001 commitée", "FT-0001" in seen2)
check("FT-0002 commitée", "FT-0002" in seen2)
check("FT-0003 PAS commitée (non téléchargée)", "FT-0003" not in seen2)
check("HW-0000 PAS commitée (non téléchargée)", "HW-0000" not in seen2)

# Les rows non téléchargées restent disponibles pour la prochaine recherche
remaining = ft_rows[3:] + hw_rows  # FT-0003, FT-0004, HW-0000..HW-0004
new_remaining, dupes_remaining = sic.filter_new(remaining)
check("7 offres restantes sont encore nouvelles", len(new_remaining) == 7)
check("0 doublon parmi les non-téléchargées", dupes_remaining == 0)

# Les 3 commitées sont des doublons
already_dl = ft_rows[:3]
new_dl, dupes_dl = sic.filter_new(already_dl)
check("3 offres DL sont des doublons", dupes_dl == 3)
check("0 nouvelle parmi les DL", len(new_dl) == 0)

# ── 5. PAS DE COMMIT SI PAS DE TÉLÉCHARGEMENT ─────────────────────────────
section("5. SANS TÉLÉCHARGEMENT — cache intact")

sic.clear_cache()
check("cache vide au départ", sic.get_stats()["count"] == 0)

# Simule le workflow complet SANS télécharger
new_sim, dupes_sim = sic.filter_new(all_rows)  # recherche
# Ici l'utilisateur ferme sans télécharger → aucun commit
check("après recherche sans DL: cache reste vide", sic.get_stats()["count"] == 0)

# Relance une recherche — les mêmes offres sont encore disponibles
new_sim2, _ = sic.filter_new(all_rows)
check("relance sans DL précédent: 10 nouvelles toujours disponibles", len(new_sim2) == 10)

# ── 6. IDEMPOTENCE — double commit ne duplique pas ────────────────────────
section("6. IDEMPOTENCE — double commit sans effet")

sic.clear_cache()
sic.commit_rows(ft_rows[:2])
sic.commit_rows(ft_rows[:2])  # même rows commitées deux fois
check("double commit → toujours 2 IDs (pas de duplication)", sic.get_stats()["count"] == 2)

# ── 7. Rows sans ID — fingerprint et déduplication ────────────────────────
section("7. Rows sans ID — fingerprint stable, zéro doublon possible")

sic.clear_cache()
no_id_rows = [
    {"id": None,  "url": "http://a.com", "intitule": "Sans ID",   "entreprise": "A", "ville": "Paris"},
    {"url": "http://b.com", "intitule": "Pas de champ id",        "entreprise": "B", "ville": "Lyon"},
    {"id": "",    "url": "http://c.com", "intitule": "ID vide",   "entreprise": "C", "ville": "Tours"},
    {"id": "VALID-001", "url": "http://d.com", "intitule": "Avec ID"},
]

# Vérifier que get_row_id génère bien un fingerprint pour les sans-ID
fp1 = sic.get_row_id(no_id_rows[0])
fp2 = sic.get_row_id(no_id_rows[1])
fp3 = sic.get_row_id(no_id_rows[2])
fp4 = sic.get_row_id(no_id_rows[3])

check("fingerprint généré pour id=None",   fp1.startswith("fp:"))
check("fingerprint généré pour sans clé",  fp2.startswith("fp:"))
check("fingerprint généré pour id=''",     fp3.startswith("fp:"))
check("vrai ID conservé pour VALID-001",   fp4 == "VALID-001")
check("fingerprints différents (urls diff)", len({fp1, fp2, fp3}) == 3)

# Stabilité : même row → même fingerprint
fp1b = sic.get_row_id(no_id_rows[0])
check("fingerprint stable (même row = même fp)", fp1 == fp1b)

# commit + dédup
try:
    sic.commit_rows(no_id_rows)
    check("commit rows sans ID ne plante pas", True)
    check("4 entrées dans le cache (3 fp + 1 vrai ID)", sic.get_stats()["count"] == 4)
    check("VALID-001 dans le cache", "VALID-001" in sic.load_seen_ids())
    check("fingerprints dans le cache", fp1 in sic.load_seen_ids())
except Exception as e:
    check("commit rows sans ID ne plante pas", False, str(e))

# filter_new — les mêmes rows ne passent plus
new_no_id, dupes_no_id = sic.filter_new(no_id_rows)
check("rows sans ID détectées comme doublons après commit", dupes_no_id == 4)
check("0 nouvelle après commit", len(new_no_id) == 0)

# Même offre avec URL identique mais ID différent → doublon via fingerprint
same_url_diff_id = {"id": "NEW-999", "url": "http://a.com", "intitule": "Sans ID", "entreprise": "A", "ville": "Paris"}
# get_row_id préfère le vrai ID s'il existe
fp_same = sic.get_row_id(same_url_diff_id)
check("offre avec vrai ID → utilise l'ID, pas le fingerprint", fp_same == "NEW-999")

# ── Résumé ─────────────────────────────────────────────────────────────────
sic.CACHE_FILE = orig_cache
shutil.rmtree(tmpdir)
root.destroy()

total = passed + failed
print(f"\n{'='*60}")
print(f"  RÉSULTATS : {passed}/{total} tests passés")
if failed:
    print(f"  ❌  {failed} test(s) échoué(s)")
else:
    print(f"  ✅  Les IDs sont enregistrés UNIQUEMENT au téléchargement")
    print(f"  ✅  Actif pour France Travail ET HelloWork")
    print(f"  ✅  filter_new (recherche) = lecture seule, jamais d'écriture")
print(f"{'='*60}\n")

if failed:
    raise SystemExit(1)

# ── 8. Vérification finale au moment du DL ────────────────────────────────
section("8. Vérification finale — doublons supprimés au moment du téléchargement")

sic.clear_cache()

# Simule : on a fait une recherche, on a 5 rows prêtes
search_rows = [make_ft_row(i) for i in range(5)]   # FT-0000..FT-0004

# Entre la recherche et le DL, quelqu'un (autre session) a commité FT-0000 et FT-0002
sic.commit_rows([search_rows[0], search_rows[2]])
check("cache contient 2 IDs avant DL", sic.get_stats()["count"] == 2)

# Au moment du DL, on re-filtre
clean, skipped = sic.filter_new(search_rows)
check("vérification finale : 3 clean, 2 skipped", len(clean) == 3 and skipped == 2)
check("FT-0001 dans clean", any(r["id"] == "FT-0001" for r in clean))
check("FT-0003 dans clean", any(r["id"] == "FT-0003" for r in clean))
check("FT-0004 dans clean", any(r["id"] == "FT-0004" for r in clean))
check("FT-0000 absent de clean", not any(r["id"] == "FT-0000" for r in clean))
check("FT-0002 absent de clean", not any(r["id"] == "FT-0002" for r in clean))

# On commit seulement les clean rows
sic.commit_rows(clean)
check("cache contient 5 IDs après DL", sic.get_stats()["count"] == 5)

# Relance : tout est doublon maintenant
new_after, dupes_after = sic.filter_new(search_rows)
check("après DL: 0 nouvelles, 5 doublons", len(new_after) == 0 and dupes_after == 5)

# ── 9. Vérification finale avec offres sans ID ────────────────────────────
section("9. Vérification finale — offres sans ID, fingerprint utilisé")

sic.clear_cache()

no_id_1 = {"id": None, "url": "https://offre.fr/001", "intitule": "Couvreur", "entreprise": "BTP+", "ville": "Tours", "date_publication": "2026-07-01", "type_contrat": "CDI"}
no_id_2 = {"id": None, "url": "https://offre.fr/002", "intitule": "Plombier", "entreprise": "Pro", "ville": "Paris", "date_publication": "2026-07-01", "type_contrat": "CDI"}
no_id_3 = {"id": None, "url": "https://offre.fr/003", "intitule": "Peintre",  "entreprise": "Deco", "ville": "Lyon", "date_publication": "2026-07-01", "type_contrat": "CDD"}

rows_no_id = [no_id_1, no_id_2, no_id_3]

# Première recherche → tout nouveau
new1, dup1 = sic.filter_new(rows_no_id)
check("sans ID: 3 nouvelles au départ", len(new1) == 3 and dup1 == 0)
check("cache intact après filter_new", sic.get_stats()["count"] == 0)

# DL partiel : commit no_id_1 seulement
sic.commit_rows([no_id_1])
check("cache 1 ID après DL partiel", sic.get_stats()["count"] == 1)

# Vérification finale avant DL des 3 rows
clean2, skip2 = sic.filter_new(rows_no_id)
check("vérif finale: 2 clean, 1 skipped", len(clean2) == 2 and skip2 == 1)
check("no_id_1 (déjà DL) absent de clean2", no_id_1 not in clean2)
check("no_id_2 et no_id_3 présents", no_id_2 in clean2 and no_id_3 in clean2)

# Commit des 2 restantes
sic.commit_rows(clean2)
check("cache 3 entrées (fingerprints)", sic.get_stats()["count"] == 3)

# Re-recherche des mêmes offres → tout doublon
new3, dup3 = sic.filter_new(rows_no_id)
check("après DL complet: 0 nouvelles sans ID", len(new3) == 0 and dup3 == 3)

# ── 10. Cas extrême — toutes les rows déjà dans le cache au moment du DL ──
section("10. Cas extrême — toutes les rows sont des doublons au moment du DL")

sic.clear_cache()
rows_all = [make_ft_row(i) for i in range(3)]

# Commit avant même le DL (simule 2 sessions simultanées)
sic.commit_rows(rows_all)
check("cache pré-rempli", sic.get_stats()["count"] == 3)

clean_all, skip_all = sic.filter_new(rows_all)
check("vérif finale: 0 clean, 3 skipped", len(clean_all) == 0 and skip_all == 3)
# Dans ce cas _do_download n'exporterait rien et informerait l'utilisateur
check("message 'rien à exporter' serait affiché", len(clean_all) == 0)

# ── 11. get_row_id — priorité ID > URL > contenu ─────────────────────────
section("11. get_row_id — priorité et stabilité")

# Priorité 1 : vrai ID
r_id = {"id": "FT-123", "url": "https://x.com", "intitule": "Dev"}
check("vrai ID utilisé en priorité", sic.get_row_id(r_id) == "FT-123")

# Priorité 2 : URL si pas d'ID
r_url = {"id": None, "url": "https://offre.fr/unique", "intitule": "Dev"}
fp_url = sic.get_row_id(r_url)
check("fp basé sur URL (préfixe fp:)", fp_url.startswith("fp:"))

# Même URL = même fingerprint
r_url_same = {"id": "", "url": "https://offre.fr/unique", "intitule": "Autre titre"}
check("même URL → même fingerprint", sic.get_row_id(r_url_same) == fp_url)

# URL différente = fingerprint différent
r_url_diff = {"id": None, "url": "https://offre.fr/autre"}
check("URL différente → fingerprint différent", sic.get_row_id(r_url_diff) != fp_url)

# Priorité 3 : contenu si pas d'URL
r_content = {"id": None, "url": None, "intitule": "Couvreur", "entreprise": "BTP+", "ville": "Tours", "date_publication": "2026-07-01", "type_contrat": "CDI"}
fp_content = sic.get_row_id(r_content)
check("sans URL: fp basé sur contenu", fp_content.startswith("fp:"))
check("contenu stable", sic.get_row_id(r_content) == fp_content)

# Contenu différent → fingerprint différent
r_content2 = {"id": None, "url": None, "intitule": "Plombier", "entreprise": "BTP+", "ville": "Tours", "date_publication": "2026-07-01", "type_contrat": "CDI"}
check("contenu différent → fingerprint différent", sic.get_row_id(r_content2) != fp_content)
