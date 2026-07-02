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


def flatten_offer(offer: dict) -> dict:
    entreprise = offer.get("entreprise")
    # Robustesse : l'API peut exceptionnellement renvoyer un string au lieu d'un dict
    if not isinstance(entreprise, dict):
        entreprise = {}
    lieu = offer.get("lieuTravail") or {}
    salaire = offer.get("salaire") or {}
    contact = offer.get("contact") or {}

    return {
        "id": offer.get("id"),
        "intitule": offer.get("intitule"),
        "entreprise": entreprise.get("nom"),
        "entreprise_description": (entreprise.get("description") or "")[:300],
        "secteur_activite": offer.get("secteurActiviteLibelle"),
        "ville": lieu.get("libelle"),
        "code_postal": lieu.get("codePostal"),
        "departement": (lieu.get("codePostal") or "")[:2],
        "type_contrat": offer.get("typeContrat"),
        "type_contrat_libelle": offer.get("typeContratLibelle"),
        "duree_travail": offer.get("dureeTravailLibelle"),
        "experience_exigee": offer.get("experienceLibelle"),
        "qualification": offer.get("qualificationLibelle"),
        "salaire_libelle": salaire.get("libelle"),
        "date_creation": offer.get("dateCreation"),
        "date_actualisation": offer.get("dateActualisation"),
        "description": (offer.get("description") or "").replace("\n", " ")[:500],
        "url_origine": (offer.get("origineOffre") or {}).get("urlOrigine"),
        "contact_nom": contact.get("nom"),
        "contact_email": contact.get("courriel"),
    }


def flatten_offer_unified(offer: dict) -> dict:
    """Format commun avec HelloWork pour export fusionné."""
    row = flatten_offer(offer)
    return {
        "source": "France Travail",
        "id": row["id"],
        "intitule": row["intitule"],
        "entreprise": row["entreprise"],
        "entreprise_url": "",
        "ville": row["ville"],
        "region": "",
        "code_postal": row["code_postal"],
        "secteur": row["secteur_activite"],
        "domaine": row["qualification"],
        "type_contrat": row["type_contrat"],
        "teletravail": "",
        "salaire_libelle": row["salaire_libelle"],
        "salaire_min": "",
        "salaire_max": "",
        "experience": row["experience_exigee"],
        "formation": row["qualification"],
        "competences": "",
        "taille_entreprise": "",
        "effectif_entreprise": "",
        "date_publication": row["date_creation"],
        "description": row["description"],
        "url": row["url_origine"],
    }


def export_csv(offers: list, output_path: str):
    if not offers:
        return 0
    fieldnames = list(flatten_offer(offers[0]).keys())
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        for offer in offers:
            writer.writerow(flatten_offer(offer))
    return len(offers)
