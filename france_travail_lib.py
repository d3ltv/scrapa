"""
france_travail_lib.py
======================
Logique partagée pour interroger l'API France Travail "Offres d'emploi v2"
et exporter les résultats en CSV.
"""

import csv
import re
import time
import unicodedata
import requests
from export_common import build_maps_url, build_google_dirigeant_url

TOKEN_URL = "https://entreprise.pole-emploi.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
SCOPE = "api_offresdemploiv2 o2dsoffre"

PAGE_SIZE = 150
MAX_RESULTS_HARD_CAP = 3000


class FranceTravailError(Exception):
    pass


# ---------------------------------------------------------------------------
# Codes INSEE des principales villes françaises
# ---------------------------------------------------------------------------

VILLES_INSEE = {
    "paris": "75056", "lyon": "69123", "marseille": "13055",
    "toulouse": "31555", "bordeaux": "33063", "nantes": "44109",
    "lille": "59350", "strasbourg": "67482", "rennes": "35238",
    "grenoble": "38185", "montpellier": "34172", "nice": "06088",
    "tours": "37261", "orleans": "45234", "le mans": "72181",
    "angers": "49007", "clermont-ferrand": "63113", "dijon": "21231",
    "rouen": "76540", "reims": "51454", "saint-etienne": "42218",
    "toulon": "83137", "brest": "29019", "limoges": "87085",
    "nimes": "30189", "amiens": "80021", "metz": "57463",
    "caen": "14118", "nancy": "54395", "pau": "64445",
    "perpignan": "66136", "besancon": "25056", "poitiers": "86194",
    "la rochelle": "17300", "chartres": "28085", "blois": "41018",
    "chateauroux": "36044", "bourges": "18033", "troyes": "10387",
    "auxerre": "89024", "nevers": "58194", "cherbourg": "50129",
    "laval": "53130", "le havre": "76351", "versailles": "78646",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def resolve_commune(city_or_code: str) -> str:
    """Retourne le code INSEE depuis un nom de ville, ou le code tel quel si déjà numérique."""
    s = city_or_code.strip().lower()
    if s in VILLES_INSEE:
        return VILLES_INSEE[s]
    normalized = _strip_accents(s)
    for k, v in VILLES_INSEE.items():
        if normalized == _strip_accents(k):
            return v
    return city_or_code.strip()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_access_token(client_id: str, client_secret: str) -> str:
    """Récupère un jeton d'accès OAuth2 (Client Credentials Grant)."""
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": SCOPE,
    }
    try:
        resp = requests.post(
            TOKEN_URL, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
    except requests.exceptions.Timeout:
        raise FranceTravailError("Délai dépassé lors de l'authentification. Vérifie ta connexion.")
    except requests.exceptions.ConnectionError:
        raise FranceTravailError("Impossible de joindre le serveur d'authentification France Travail.")
    if resp.status_code != 200:
        raise FranceTravailError(
            f"Échec de l'authentification ({resp.status_code}).\n"
            f"Réponse : {resp.text}\n\n"
            "Vérifie ton client_id / client_secret et que ton application "
            "est bien abonnée à l'API 'Offres d'emploi v2' sur francetravail.io."
        )
    token = resp.json().get("access_token")
    if not token:
        raise FranceTravailError(
            "Authentification OK mais access_token absent de la réponse.\n"
            f"Réponse reçue : {resp.text}"
        )
    return token


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_offers(token: str, params: dict, max_results: int, log=print) -> list:
    """Récupère les offres avec pagination via l'en-tête Range."""
    if "commune" in params and "departement" in params:
        del params["departement"]

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    all_offers = []
    start = 0
    cap = min(max_results, MAX_RESULTS_HARD_CAP)

    while start < cap:
        end = min(start + PAGE_SIZE - 1, cap - 1)
        headers["Range"] = f"offres={start}-{end}"
        try:
            resp = requests.get(SEARCH_URL, headers=headers, params=params, timeout=30)
        except requests.exceptions.Timeout:
            raise FranceTravailError(
                "Délai de connexion dépassé (timeout 30s).\n"
                "Vérifie ta connexion internet et réessaie."
            )
        except requests.exceptions.ConnectionError:
            raise FranceTravailError(
                "Impossible de joindre l'API France Travail.\n"
                "Vérifie ta connexion internet."
            )

        if resp.status_code == 204:
            break
        if resp.status_code not in (200, 206):
            raise FranceTravailError(f"Requête échouée ({resp.status_code}) : {resp.text}")

        try:
            payload = resp.json()
        except ValueError:
            raise FranceTravailError("Réponse API invalide (JSON malformé).")

        offers = payload.get("resultats", [])
        if not offers:
            break

        all_offers.extend(offers)
        log(f"  -> {len(all_offers)} offres récupérées...")

        if len(offers) < (end - start + 1):
            break

        start += PAGE_SIZE
        time.sleep(0.3)

    return all_offers


# ---------------------------------------------------------------------------
# Filtres et mise à plat
# ---------------------------------------------------------------------------

def matches_company_filter(offer: dict, company_pattern: str) -> bool:
    if not company_pattern:
        return True
    nom = (offer.get("entreprise") or {}).get("nom", "") or ""
    return bool(re.search(company_pattern, nom, re.IGNORECASE))


def _join_list(items, key=None, sep=", ") -> str:
    """Sérialise une liste de dicts ou de strings en chaîne."""
    if not items:
        return ""
    if key:
        return sep.join(str(i.get(key, "")) for i in items if i.get(key))
    return sep.join(str(i) for i in items if i)


def _clean_ville(libelle: str | None) -> str | None:
    """
    Nettoie le libellé de lieu retourné par l'API France Travail.
    Exemples :
      "37 - Tours"                        → "Tours"
      "75 - Paris 15e Arrondissement"     → "Paris 15e Arrondissement"
      "69 - Lyon 03"                      → "Lyon 03"
      "Tours"                             → "Tours"  (inchangé)
    """
    if not libelle:
        return libelle
    # Pattern : "XX - Ville" ou "XXX - Ville" (codes numériques + 2A + 2B + DOM)
    cleaned = re.sub(r"^(?:\d{1,3}|2[AB])\s*-\s*", "", libelle).strip()
    return cleaned if cleaned else libelle


def flatten_offer(offer: dict) -> dict:
    entreprise = offer.get("entreprise")
    # Robustesse : l'API peut exceptionnellement renvoyer un string au lieu d'un dict
    if not isinstance(entreprise, dict):
        entreprise = {}
    lieu = offer.get("lieuTravail") or {}
    salaire = offer.get("salaire") or {}
    contact = offer.get("contact") or {}
    origine = offer.get("origineOffre") or {}

    nom_entreprise = entreprise.get("nom")
    ville_libelle = _clean_ville(lieu.get("libelle"))

    # Formations : liste de dicts avec niveauLibelle et domaineLibelle
    formations = offer.get("formations") or []
    formations_str = "; ".join(
        filter(None, [
            f"{f.get('niveauLibelle', '')} {f.get('domaineLibelle', '')}".strip()
            for f in formations
        ])
    )

    # Compétences : liste de dicts avec libelle et exigence
    competences = offer.get("competences") or []
    competences_str = "; ".join(
        f"{c.get('libelle', '')} ({c.get('exigence', '')})" if c.get("exigence") else c.get("libelle", "")
        for c in competences
        if c.get("libelle")
    )

    # Langues
    langues = offer.get("langues") or []
    langues_str = "; ".join(
        f"{l.get('libelle', '')} ({l.get('niveauLibelle', '')})" if l.get("niveauLibelle") else l.get("libelle", "")
        for l in langues
        if l.get("libelle")
    )

    # Permis de conduire
    permis = offer.get("permis") or []
    permis_str = _join_list(permis, key="libelle")

    # Qualités professionnelles
    qualites = offer.get("qualitesProfessionnelles") or []
    qualites_str = _join_list(qualites, key="libelle")

    # Partenaires diffuseurs
    partenaires = origine.get("partenaires") or []
    partenaires_str = "; ".join(
        f"{p.get('nom', '')} {p.get('url', '')}".strip()
        for p in partenaires
        if p.get("nom") or p.get("url")
    )

    return {
        # --- Identification offre ---
        "id": offer.get("id"),
        "intitule": offer.get("intitule"),
        "date_creation": offer.get("dateCreation"),
        "date_actualisation": offer.get("dateActualisation"),
        "url_origine": origine.get("urlOrigine"),
        "partenaires_diffusion": partenaires_str,

        # --- Entreprise ---
        "entreprise": nom_entreprise,
        "entreprise_description": (entreprise.get("description") or "")[:400],
        "entreprise_url": entreprise.get("url") or "",
        "entreprise_logo": entreprise.get("logo") or "",
        "entreprise_adaptee": entreprise.get("entrepriseAdaptee"),  # ESAT / EA
        "siret": entreprise.get("siret") or "",
        "secteur_activite": offer.get("secteurActiviteLibelle"),
        "code_naf": offer.get("secteurActivite") or "",
        "nature_offre": offer.get("natureOffre"),

        # --- Lieu ---
        "ville": ville_libelle,
        "code_postal": lieu.get("codePostal"),
        "departement": (lieu.get("codePostal") or "")[:2],
        "commune_insee": lieu.get("commune"),
        "latitude": lieu.get("latitude"),
        "longitude": lieu.get("longitude"),

        # --- Contrat & conditions ---
        "type_contrat": offer.get("typeContrat"),
        "type_contrat_libelle": offer.get("typeContratLibelle"),
        "duree_travail": offer.get("dureeTravailLibelle"),
        "duree_travail_convertie": offer.get("dureeTravailLibelleConverti"),
        "temps_plein": offer.get("tempsPlein"),
        "experience_exigee": offer.get("experienceLibelle"),
        "experience_commentaire": offer.get("experienceCommentaire") or "",
        "qualification": offer.get("qualificationLibelle"),
        "code_qualification": offer.get("qualification") or "",
        "accessibilite_emploi": offer.get("accessibleTH"),  # Travailleur handicapé
        "nombre_postes": offer.get("nombrePostes"),
        "alt_licence": offer.get("alternanceLibelle"),

        # --- Salaire ---
        "salaire_libelle": salaire.get("libelle"),
        "salaire_complement1": salaire.get("complement1") or "",
        "salaire_complement2": salaire.get("complement2") or "",
        "salaire_commentaire": salaire.get("commentaire") or "",

        # --- Compétences & formation ---
        "competences": competences_str,
        "formations": formations_str,
        "langues": langues_str,
        "permis": permis_str,
        "qualites_professionnelles": qualites_str,

        # --- Contact recruteur ---
        "contact_nom": contact.get("nom"),
        "contact_coordonnees1": contact.get("coordonnees1") or "",
        "contact_coordonnees2": contact.get("coordonnees2") or "",
        "contact_coordonnees3": contact.get("coordonnees3") or "",
        "contact_telephone": contact.get("telephone") or "",
        "contact_email": contact.get("courriel"),
        "contact_url_recrutement": contact.get("urlRecruteur") or "",
        "contact_url_postuler": contact.get("urlPostulation") or "",
        "contact_commentaire": (contact.get("commentaire") or "")[:200],

        # --- Description & lien ---
        "description": (offer.get("description") or "").replace("\n", " ")[:800],
        "lien_maps": build_maps_url(nom_entreprise, ville_libelle),
        "lien_recherche_dirigeant": build_google_dirigeant_url(nom_entreprise, ville_libelle),
    }


def flatten_offer_unified(offer: dict) -> dict:
    """Format commun avec HelloWork pour export fusionné."""
    row = flatten_offer(offer)
    return {
        "source": "France Travail",
        "id": row["id"],
        "intitule": row["intitule"],
        "entreprise": row["entreprise"],
        "entreprise_url": row["entreprise_url"],
        "entreprise_logo": row["entreprise_logo"],
        "siret": row["siret"],
        "entreprise_description": row["entreprise_description"],
        "ville": row["ville"],
        "region": "",
        "code_postal": row["code_postal"],
        "commune_insee": row["commune_insee"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "secteur": row["secteur_activite"],
        "code_naf": row["code_naf"],
        "domaine": row["qualification"],
        "type_contrat": row["type_contrat"],
        "type_contrat_libelle": row["type_contrat_libelle"],
        "duree_travail": row["duree_travail"],
        "duree_travail_convertie": row["duree_travail_convertie"],
        "temps_plein": row["temps_plein"],
        "teletravail": "",
        "nombre_postes": row["nombre_postes"],
        "salaire_libelle": row["salaire_libelle"],
        "salaire_complement1": row["salaire_complement1"],
        "salaire_complement2": row["salaire_complement2"],
        "salaire_commentaire": row["salaire_commentaire"],
        "salaire_min": "",
        "salaire_max": "",
        "experience": row["experience_exigee"],
        "experience_commentaire": row["experience_commentaire"],
        "formation": row["formations"],
        "competences": row["competences"],
        "langues": row["langues"],
        "permis": row["permis"],
        "qualites_professionnelles": row["qualites_professionnelles"],
        "taille_entreprise": "",
        "effectif_entreprise": "",
        "accessibilite_emploi": row["accessibilite_emploi"],
        "date_publication": row["date_creation"],
        "date_actualisation": row["date_actualisation"],
        "description": row["description"],
        "url": row["url_origine"],
        "partenaires_diffusion": row["partenaires_diffusion"],
        "contact_nom": row["contact_nom"],
        "contact_telephone": row["contact_telephone"],
        "contact_email": row["contact_email"],
        "contact_coordonnees1": row["contact_coordonnees1"],
        "contact_coordonnees2": row["contact_coordonnees2"],
        "contact_coordonnees3": row["contact_coordonnees3"],
        "contact_url_recrutement": row["contact_url_recrutement"],
        "contact_url_postuler": row["contact_url_postuler"],
        "contact_commentaire": row["contact_commentaire"],
        "lien_maps": row["lien_maps"],
        "lien_recherche_dirigeant": row["lien_recherche_dirigeant"],
    }


def export_csv(offers: list, output_path: str):
    if not offers:
        return 0
    # On génère la liste des colonnes depuis la première offre aplatie
    # pour rester aligné avec les champs définis dans flatten_offer
    sample = flatten_offer(offers[0])
    fieldnames = list(sample.keys())
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for offer in offers:
            writer.writerow(flatten_offer(offer))
    return len(offers)
