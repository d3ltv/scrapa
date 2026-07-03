#!/usr/bin/env python3
"""Interface graphique Scrapa — France Travail + HelloWork."""

import os
import sys
import subprocess
import threading
import queue
import webbrowser
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from dotenv import load_dotenv, set_key
    load_dotenv()
    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False

from export_common import apply_post_filters, export_csv_rows
from france_travail_lib import (
    get_access_token, fetch_offers, matches_company_filter,
    flatten_offer_unified, FranceTravailError, resolve_commune,
)
from hellowork_lib import (
    verify_apify_token, fetch_hellowork_offers,
    HelloWorkError, UNIFIED_FIELDNAMES as HW_UNIFIED_FIELDNAMES,
)
from seen_ids_cache import filter_new, commit_rows, get_stats, clear_cache, load_seen_ids, get_row_id
from recruiter_filter import filter_out_recruiters


def ftl_flatten_for_dedup(offer: dict) -> dict:
    """Extrait les champs minimaux d'une offre FT brute pour le fingerprinting."""
    return {
        "id":  offer.get("id"),
        "url": (offer.get("origineOffre") or {}).get("urlOrigine"),
        "intitule":  offer.get("intitule"),
        "entreprise": (offer.get("entreprise") or {}).get("nom") if isinstance(offer.get("entreprise"), dict) else None,
        "ville": (offer.get("lieuTravail") or {}).get("libelle"),
        "date_publication": offer.get("dateCreation"),
        "type_contrat": offer.get("typeContrat"),
    }

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

FT_CONTRAT = {
    "Tous": "", "CDI": "CDI", "CDD": "CDD",
    "Intérim": "MIS", "Saisonnier": "SAI",
}
FT_CONTRAT_CHOICES = ["CDI", "CDD", "Intérim", "Saisonnier", "Alternance"]
FT_CONTRAT_CODES = {
    "CDI": "CDI",
    "CDD": "CDD",
    "Intérim": "MIS",
    "Saisonnier": "SAI",
    "Alternance": "CDI,CDD",   # France Travail n'a pas de code dédié alternance
}

FT_EXPERIENCE = {
    "Indifférent": "", "Débutant accepté": "1", "1 à 3 ans": "2", "Plus de 3 ans": "3",
}
FT_JOURS = {
    "Toutes dates": "", "24 dernières heures": "1", "3 derniers jours": "3",
    "7 derniers jours": "7", "14 derniers jours": "14", "31 derniers jours": "31",
}
HW_CONTRATS = [
    ("CDI", "CDI"), ("CDD", "CDD"), ("Alternance", "ALTERNANCE"),
    ("Stage", "STAGE"), ("Intérim", "INTERIM"), ("Freelance", "FREELANCE"),
]
HW_DATE_POSTED = {
    "Toutes dates": "any", "24h": "24h", "3 jours": "3d",
    "7 jours": "1w", "31 jours": "1m",
}

# ---------------------------------------------------------------------------
# Secteurs prédéfinis — codes NAF (France Travail) + mots-clés métiers (HelloWork)
# ---------------------------------------------------------------------------

SECTEURS: dict[str, dict] = {
    "BTP / Construction": {
        "naf": [
            "41", "42", "43",        # Construction générale, génie civil, travaux spécialisés
            "2311", "2312", "2320",  # Verre, céramique
            "2351", "2352",          # Ciment, béton
            "4669",                  # Négoce matériaux
            "7111", "7112",          # Architecture, ingénierie
            "4312", "4313",          # Terrassement, forages
            "4321", "4322", "4329",  # Électricité, plomberie, autres install.
            "4331", "4332", "4333",  # Plâtrerie, menuiserie, revêtements
            "4334", "4339",          # Peinture, finitions
            "4391", "4399",          # Couverture, autres travaux spécialisés
        ],
        "keywords": [
            # Gros œuvre
            "maçon", "maçon coffreur", "coffreur bancheur", "ferrailleur",
            "bétonnier", "terrassier", "conducteur engins BTP",
            "chef de chantier gros œuvre", "conducteur travaux gros œuvre",
            # Second œuvre
            "plombier", "plombier chauffagiste", "chauffagiste",
            "électricien bâtiment", "électricien tertiaire",
            "plaquiste", "plaquiste peintre", "peintre bâtiment",
            "carreleur", "solier", "poseur revêtements sols",
            "menuisier", "menuisier poseur", "charpentier bois",
            "couvreur", "couvreur zingueur", "étancheur",
            "façadier", "bardeur", "isolateur", "plâtrier",
            # Génie civil / TP
            "génie civil", "canalisateur", "paveur",
            "technicien travaux publics", "conducteur travaux TP",
            # Encadrement / bureau
            "conducteur de travaux", "chef de chantier",
            "ingénieur BTP", "ingénieur travaux", "ingénieur structure",
            "architecte", "dessinateur projeteur BTP", "métreur",
            "économiste de la construction", "technicien bureau d'études BTP",
            "responsable QSE BTP", "directeur travaux",
        ],
    },
    "Restauration / Hôtellerie": {
        "naf": [
            "5610", "5621", "5629", "5630",  # Restaurants, traiteurs, débits de boissons
            "5510", "5520", "5530", "5590",  # Hébergement
        ],
        "keywords": [
            "cuisinier", "chef cuisinier", "commis de cuisine", "pâtissier",
            "boulanger", "serveur", "barman", "réceptionniste hôtel",
            "maître d'hôtel", "responsable restauration", "plongeur",
            "directeur restauration", "gérant restaurant", "pizzaïolo",
            "sushi", "traiteur", "sommelier", "fast food", "cuisine collective",
        ],
    },
    "Transport / Logistique": {
        "naf": [
            "4941", "4942",          # Transport routier marchandises et voyageurs
            "5010", "5020", "5030",  # Transport maritime et fluvial
            "5110", "5121",          # Transport aérien
            "5210", "5224", "5229",  # Entreposage, manutention
            "5320",                  # Autres activités de poste et courrier
        ],
        "keywords": [
            "chauffeur poids lourd", "chauffeur SPL", "chauffeur livreur",
            "conducteur transport", "logisticien", "préparateur commandes",
            "agent quai", "cariste", "magasinier", "responsable logistique",
            "gestionnaire stock", "coordinateur transport", "exploitant transport",
            "transitaire", "agent fret", "manutentionnaire",
        ],
    },
    "Informatique / Tech": {
        "naf": [
            "6201", "6202", "6203", "6209",  # Programmation, conseil informatique
            "6311", "6312",                  # Traitement données, hébergement
            "6201", "7022",                  # Conseil ingénierie
        ],
        "keywords": [
            "développeur", "développeur web", "développeur Python", "développeur Java",
            "développeur React", "ingénieur logiciel", "DevOps", "SRE",
            "data engineer", "data scientist", "analyste données", "cybersécurité",
            "administrateur système", "administrateur réseau", "chef de projet IT",
            "product owner", "scrum master", "UX designer", "testeur QA",
            "architecte solutions", "technicien support informatique",
        ],
    },
    "Commerce / Vente": {
        "naf": [
            "4711", "4719", "4721", "4722", "4723", "4724", "4725",
            "4726", "4727", "4729", "4730", "4741", "4742", "4743",
            "4751", "4752", "4753", "4754", "4759", "4761", "4762",
            "4763", "4764", "4765", "4771", "4772", "4773", "4774",
            "4775", "4776", "4777", "4778", "4779", "4781", "4782",
            "4789", "4791", "4799",  # Commerce de détail
            "4611", "4612", "4613", "4614", "4615", "4616", "4617",
            "4618", "4619", "4621", "4622", "4623", "4624", "4631",
            "4632", "4633", "4634", "4635", "4636", "4637", "4638",
            "4639", "4641", "4642", "4643", "4644", "4645", "4646",
            "4647", "4648", "4649", "4651", "4652", "4661", "4662",
            "4663", "4664", "4665", "4666", "4669", "4671", "4672",
            "4673", "4674", "4675", "4676", "4677",  # Commerce de gros
        ],
        "keywords": [
            "vendeur", "conseiller de vente", "commercial", "technico-commercial",
            "responsable des ventes", "chef des ventes", "directeur commercial",
            "account manager", "business developer", "attaché commercial",
            "chargé d'affaires", "responsable magasin", "chef de rayon",
            "caissier", "hôte de caisse", "merchandiser",
        ],
    },
    "Santé / Médico-social": {
        "naf": [
            "8610", "8621", "8622", "8623", "8690",  # Activités hospitalières, médecins, dentistes
            "8710", "8720", "8730", "8790",           # Hébergement médico-social
            "8810", "8891", "8899",                   # Action sociale sans hébergement
        ],
        "keywords": [
            "infirmier", "infirmière", "aide-soignant", "médecin",
            "pharmacien", "kinésithérapeute", "ergothérapeute",
            "orthophoniste", "psychologue", "auxiliaire de vie",
            "aide à domicile", "éducateur spécialisé", "moniteur éducateur",
            "assistant social", "sage-femme", "radiologue", "anesthésiste",
            "urgentiste", "directeur EHPAD", "coordinateur soins",
        ],
    },
    "Industrie / Production": {
        "naf": [
            "10", "11", "12", "13", "14", "15",      # Agroalimentaire, boissons, textile
            "16", "17", "18", "19", "20", "21",      # Bois, papier, chimie, pharma
            "22", "23", "24", "25", "26", "27",      # Plastique, verre, métal, électronique
            "28", "29", "30", "31", "32", "33",      # Machines, autos, meubles, réparation
        ],
        "keywords": [
            "opérateur de production", "conducteur de ligne", "technicien de maintenance",
            "agent de fabrication", "tourneur fraiseur", "soudeur",
            "chaudronnier", "ajusteur", "électrotechnicien", "automaticien",
            "ingénieur production", "responsable qualité", "contrôleur qualité",
            "chef d'équipe production", "technicien méthodes", "ingénieur process",
        ],
    },
    "Agriculture / Espaces verts": {
        "naf": [
            "0111", "0112", "0113", "0114", "0115", "0116", "0119",  # Cultures
            "0121", "0122", "0123", "0124", "0125", "0126", "0127",  # Cultures permanentes
            "0128", "0129",
            "0130", "0141", "0142", "0143", "0144", "0145", "0146",  # Élevage
            "0147", "0149", "0150", "0161", "0162", "0163", "0164",
            "0170", "0210", "0220", "0230", "0240",                   # Sylviculture
            "8130",                                                    # Services paysage / jardins
        ],
        "keywords": [
            "agriculteur", "maraîcher", "arboriculteur", "viticulteur",
            "ouvrier agricole", "technicien agricole", "jardinier",
            "paysagiste", "agent espaces verts", "élagueur",
            "conducteur engins agricoles", "responsable exploitation",
            "agronome", "conseiller agricole", "chef de culture",
        ],
    },
    "Finance / Banque / Assurance": {
        "naf": [
            "6411", "6419", "6420", "6430", "6491", "6492", "6499",  # Banque, finance
            "6511", "6512", "6521", "6522",                           # Assurance
            "6611", "6612", "6619", "6621", "6622", "6629", "6630",  # Auxiliaires finance/assurance
        ],
        "keywords": [
            "conseiller bancaire", "gestionnaire de patrimoine", "analyste financier",
            "contrôleur de gestion", "comptable", "expert-comptable", "auditeur",
            "chargé de clientèle banque", "directeur agence bancaire",
            "gestionnaire sinistres", "conseiller assurance", "actuaire",
            "trader", "analyste crédit", "responsable conformité",
        ],
    },
    "Éducation / Formation": {
        "naf": [
            "8510", "8520", "8531", "8532",  # Enseignement primaire, secondaire
            "8541", "8542",                  # Enseignement supérieur
            "8551", "8552", "8553", "8559",  # Formation continue, conduite, autres
            "8560",                          # Activités de soutien
        ],
        "keywords": [
            "enseignant", "professeur", "instituteur", "formateur",
            "conseiller pédagogique", "directeur d'école", "CPE",
            "éducateur", "animateur", "BAFA", "directeur centre de formation",
            "ingénieur pédagogique", "tuteur", "coach professionnel",
        ],
    },
    "Immobilier": {
        "naf": ["6810", "6820", "6831", "6832"],
        "keywords": [
            "agent immobilier", "négociateur immobilier", "conseiller immobilier",
            "gestionnaire locatif", "property manager", "syndic de copropriété",
            "administrateur de biens", "expert immobilier", "promoteur immobilier",
            "directeur agence immobilière", "chargé de transaction", "asset manager",
        ],
    },
    "Juridique / Droit": {
        "naf": ["6910", "6920"],
        "keywords": [
            "juriste", "avocat", "notaire", "huissier", "greffier",
            "juriste d'entreprise", "juriste droit social", "juriste droit des affaires",
            "paralégal", "responsable juridique", "directeur juridique",
            "chargé de conformité", "compliance officer",
        ],
    },
    "Marketing / Communication": {
        "naf": ["7311", "7312", "7021", "7022", "6391", "6399"],
        "keywords": [
            "chargé de communication", "responsable marketing", "chef de projet marketing",
            "community manager", "content manager", "traffic manager",
            "responsable SEO", "chargé de relations presse", "directeur marketing",
            "brand manager", "chargé d'études marketing", "graphic designer",
            "concepteur rédacteur", "chef de publicité",
        ],
    },
    "Ressources Humaines": {
        "naf": ["7810", "7820", "7830"],
        "keywords": [
            "chargé RH", "responsable RH", "DRH", "gestionnaire paie",
            "responsable paie", "chargé de recrutement", "talent acquisition",
            "responsable formation", "HRBP", "business partner RH",
            "responsable GPEC", "responsable relations sociales", "assistant RH",
        ],
    },
    "Hôpital / Urgences / Bloc": {
        "naf": ["8610"],
        "keywords": [
            "infirmier bloc opératoire", "IBODE", "IADE", "infirmier urgences",
            "aide-soignant urgences", "brancardier", "ambulancier",
            "manipulateur radio", "préparateur en pharmacie hospitalière",
            "chirurgien", "médecin urgentiste", "réanimateur",
            "cadre de santé", "directeur soins infirmiers",
        ],
    },
    "Énergie / Environnement": {
        "naf": [
            "3511", "3512", "3513", "3514",
            "3521", "3522", "3523",
            "3600", "3811", "3812", "3821", "3822", "3900", "7112",
        ],
        "keywords": [
            "technicien énergie", "ingénieur énergie", "électricien industriel",
            "technicien photovoltaïque", "installateur solaire",
            "chargé de mission environnement", "responsable HSE",
            "technicien traitement eau", "ingénieur génie de l'environnement",
            "auditeur énergétique", "gestionnaire réseau électrique",
            "technicien maintenance éolienne", "chef de projet ENR",
        ],
    },
    "Sécurité / Gardiennage": {
        "naf": ["8010", "8020", "8030"],
        "keywords": [
            "agent de sécurité", "agent de surveillance", "vigile",
            "agent cynophile", "agent de sûreté", "responsable sécurité",
            "chef de poste sécurité", "SSIAP", "rondier", "gardien",
            "opérateur vidéosurveillance", "technicien alarme",
            "installateur systèmes sécurité",
        ],
    },
    "Automobile / Mécanique": {
        "naf": [
            "2910", "2920", "2931", "2932",
            "4511", "4519", "4520", "4531", "4532", "4540",
        ],
        "keywords": [
            "mécanicien automobile", "technicien automobile", "carrossier",
            "peintre carrosserie", "électricien automobile", "contrôleur technique",
            "réceptionnaire après-vente", "chef d'atelier automobile",
            "vendeur automobile", "conseiller commercial automobiles",
            "préparateur véhicule", "mécanicien poids lourd", "technicien diagnostic",
        ],
    },
    "Aéronautique / Défense": {
        "naf": ["3030", "3315", "8422"],
        "keywords": [
            "technicien aéronautique", "mécanicien aéronautique", "ingénieur aéronautique",
            "technicien de maintenance avion", "agent piste", "agent escale",
            "contrôleur aérien", "ingénieur systèmes embarqués", "technicien avionique",
            "ingénieur navigabilité", "responsable qualité aéronautique",
            "chef de projet aéronautique",
        ],
    },
    "Tourisme / Loisirs / Sport": {
        "naf": [
            "7911", "7912", "7990",
            "9311", "9312", "9313", "9319", "9321", "9329",
        ],
        "keywords": [
            "agent de voyage", "conseiller voyages", "chef de produit tourisme",
            "guide touristique", "accompagnateur tourisme", "animateur vacances",
            "directeur camping", "responsable club de sport",
            "éducateur sportif", "coach sportif", "maître nageur",
            "moniteur ski", "animateur culturel", "directeur centre de loisirs",
        ],
    },
    "Nettoyage / Propreté": {
        "naf": ["8121", "8122", "8129"],
        "keywords": [
            "agent de nettoyage", "agent de propreté", "technicien de surface",
            "responsable nettoyage", "chef d'équipe propreté",
            "agent de nettoyage industriel", "laveur de vitres",
            "responsable exploitation propreté", "chef de secteur propreté",
        ],
    },
    "Audiovisuel / Médias / Culture": {
        "naf": [
            "5911", "5912", "5913", "5914", "5920",
            "6010", "6020",
            "9001", "9002", "9003", "9004",
        ],
        "keywords": [
            "journaliste", "rédacteur", "présentateur", "caméraman",
            "monteur vidéo", "chef opérateur", "réalisateur",
            "chargé de production audiovisuelle", "régisseur",
            "technicien son", "ingénieur du son", "éclairagiste",
            "responsable éditorial", "directeur artistique",
        ],
    },
    "Conseil / Management": {
        "naf": ["7021", "7022", "6920", "8299"],
        "keywords": [
            "consultant", "consultant en management", "manager de transition",
            "chef de projet", "directeur de projet", "PMO",
            "business analyst", "consultant stratégie", "consultant organisation",
            "directeur général", "DGA", "responsable opérations",
        ],
    },
    "Supply Chain / Achats": {
        "naf": ["4619", "4631", "4632", "4633", "5210", "5224", "5229"],
        "keywords": [
            "acheteur", "responsable achats", "directeur achats",
            "approvisionneur", "responsable approvisionnement",
            "gestionnaire supply chain", "responsable supply chain",
            "planificateur", "demand planner", "supply planner",
            "responsable entrepôt", "coordinateur logistique",
            "chef de projet supply chain", "responsable douane",
        ],
    },
    "Informatique / Réseaux / Télécom": {
        "naf": ["6110", "6120", "6130", "6190", "6201", "6202", "6203", "6209"],
        "keywords": [
            "technicien réseau", "administrateur réseau", "ingénieur télécoms",
            "technicien télécom", "intégrateur réseaux", "NOC engineer",
            "ingénieur infrastructure", "technicien fibre optique",
            "responsable infrastructure SI", "ingénieur cloud",
            "technicien helpdesk", "technicien support N2", "technicien N3",
        ],
    },
    "Comptabilité / Gestion": {
        "naf": ["6920"],
        "keywords": [
            "comptable", "comptable général", "comptable fournisseurs",
            "comptable clients", "assistant comptable", "chef comptable",
            "expert-comptable", "contrôleur de gestion", "analyste financier",
            "trésorier", "responsable administratif et financier", "DAF",
            "auditeur interne", "auditeur externe", "responsable consolidation",
        ],
    },
    "Services à la personne": {
        "naf": ["8810", "8891", "8899", "9601", "9602", "9609"],
        "keywords": [
            "aide à domicile", "auxiliaire de vie", "assistant de vie",
            "garde d'enfants", "nounou", "auxiliaire parentale",
            "accompagnant personnes handicapées", "ADVF",
            "responsable secteur aide à domicile", "coordinateur services",
            "employé familial", "femme de ménage", "homme toutes mains",
        ],
    },
    "Naval / Maritime": {
        "naf": ["3011", "3012", "5010", "5020", "5030"],
        "keywords": [
            "marin", "matelot", "officier de port", "capitaine",
            "mécanicien naval", "technicien naval", "soudeur naval",
            "chaudronnier naval", "outilleur naval", "ingénieur naval",
            "agent portuaire", "agent maritime", "chef de bord",
            "électricien naval", "responsable flotte",
        ],
    },
    "Pharmacie / Cosmétique / Chimie": {
        "naf": ["2011", "2012", "2013", "2014", "2020", "2110", "2120"],
        "keywords": [
            "pharmacien", "préparateur en pharmacie", "responsable assurance qualité",
            "ingénieur chimiste", "technicien laboratoire", "technicien qualité",
            "responsable production pharmaceutique", "ingénieur procédés",
            "chef de produit cosmétique", "formateur cosmétique",
            "regulatory affairs", "affaires réglementaires",
        ],
    },
}

SECTEUR_CHOICES = ["(aucun)"] + sorted(SECTEURS.keys())

# Colonnes du tableau résultats
COLUMNS = [
    ("url",             "Lien",          180),
    ("source",          "Source",         80),
    ("intitule",        "Poste",         200),
    ("entreprise",      "Entreprise",    160),
    ("ville",           "Ville",         110),
    ("type_contrat",    "Contrat",        70),
    ("salaire_libelle", "Salaire",       120),
    ("date_publication","Date",           90),
    ("teletravail",     "Télétravail",    90),
    ("secteur",         "Secteur",       140),
    ("experience",      "Expérience",    110),
    ("description",     "Description",   300),
]
COL_KEYS   = [c[0] for c in COLUMNS]
COL_LABELS = {c[0]: c[1] for c in COLUMNS}
COL_WIDTHS = {c[0]: c[2] for c in COLUMNS}


# ---------------------------------------------------------------------------
# Widget TagList — liste de tags avec bouton + et × pour supprimer
# ---------------------------------------------------------------------------

class TagList(ttk.Frame):
    """
    Zone de tags empilables.
    - choices  : liste des valeurs proposées dans le menu déroulant du +
    - freetext : si True, permet la saisie libre (pas de liste fixe)
    - placeholder : texte affiché quand vide
    """

    def __init__(self, parent, choices: list[str] | None = None,
                 freetext: bool = False, placeholder: str = "Ajouter…", **kw):
        super().__init__(parent, **kw)
        self._choices = choices or []
        self._freetext = freetext
        self._placeholder = placeholder
        self._tags: list[str] = []
        self._tag_frames: list[ttk.Frame] = []
        self._on_change: list = []

        self._tags_frame = ttk.Frame(self)
        self._tags_frame.pack(side="left", fill="x", expand=True)

        self._add_btn = ttk.Button(self, text="＋", width=3, command=self._open_add)
        self._add_btn.pack(side="left", padx=(4, 0))

    def get_tags(self) -> list[str]:
        return list(self._tags)

    def set_tags(self, tags: list[str]):
        self._tags.clear()
        for w in self._tag_frames:
            w.destroy()
        self._tag_frames.clear()
        for t in tags:
            self._add_tag(t)

    def bind_change(self, fn):
        self._on_change.append(fn)

    def configure_state(self, state: str):
        self._add_btn.configure(state=state)
        for f in self._tag_frames:
            for child in f.winfo_children():
                try:
                    child.configure(state=state)
                except tk.TclError:
                    pass

    def _add_tag(self, value: str):
        if not value.strip() or value in self._tags:
            return
        self._tags.append(value)
        try:
            f = ttk.Frame(self._tags_frame, style="Tag.TFrame")
        except Exception:
            f = ttk.Frame(self._tags_frame, relief="solid", borderwidth=1)
        f.pack(side="left", padx=(0, 6), pady=2)
        try:
            ttk.Label(f, text=value, style="Tag.TLabel", padding=(6, 2)).pack(side="left")
            btn = ttk.Button(f, text="✕", style="TagX.TButton",
                             command=lambda v=value: self._remove_tag(v))
        except Exception:
            ttk.Label(f, text=value, padding=(4, 1)).pack(side="left")
            btn = ttk.Button(f, text="×", width=2,
                             command=lambda v=value: self._remove_tag(v))
        btn.pack(side="left", padx=(0, 2))
        self._tag_frames.append(f)
        for fn in self._on_change:
            fn()

    def _remove_tag(self, value: str):
        if value not in self._tags:
            return
        idx = self._tags.index(value)
        self._tags.remove(value)
        self._tag_frames[idx].destroy()
        self._tag_frames.pop(idx)
        for fn in self._on_change:
            fn()

    def _open_add(self):
        if self._freetext:
            self._open_freetext_popup()
        else:
            self._open_choice_popup()

    def _open_choice_popup(self):
        available = [c for c in self._choices if c not in self._tags]
        if not available:
            return
        popup = tk.Toplevel(self)
        popup.title("Ajouter")
        popup.resizable(False, False)
        popup.transient(self)
        popup.grab_set()
        x = self._add_btn.winfo_rootx()
        y = self._add_btn.winfo_rooty() + self._add_btn.winfo_height()
        popup.geometry(f"+{x}+{y}")
        for choice in available:
            ttk.Button(
                popup, text=choice, width=18,
                command=lambda c=choice, p=popup: (self._add_tag(c), p.destroy()),
            ).pack(fill="x", padx=4, pady=2)
        popup.bind("<Escape>", lambda e: popup.destroy())
        popup.bind("<FocusOut>", lambda e: popup.destroy())

    def _open_freetext_popup(self):
        popup = tk.Toplevel(self)
        popup.title("Ajouter")
        popup.resizable(False, False)
        popup.transient(self)
        popup.grab_set()
        x = self._add_btn.winfo_rootx()
        y = self._add_btn.winfo_rooty() + self._add_btn.winfo_height()
        popup.geometry(f"220x60+{x}+{y}")
        var = tk.StringVar()
        e = ttk.Entry(popup, textvariable=var, width=24)
        e.pack(side="left", padx=6, pady=10)
        e.focus_set()

        def confirm(ev=None):
            v = var.get().strip()
            if v:
                self._add_tag(v)
            popup.destroy()

        e.bind("<Return>", confirm)
        ttk.Button(popup, text="OK", width=4, command=confirm).pack(side="left", padx=4)
        popup.bind("<Escape>", lambda e: popup.destroy())


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Scrapa — Offres d'emploi")
        self.geometry("900x940")
        self.minsize(780, 780)

        self._queue = queue.Queue()
        self.last_output_path = None
        self._pending_rows: list[dict] = []
        self._ignored_ids: set = set()
        self._search_mode: str = "reset"
        self._ft_secret_visible = False
        self._hw_secret_visible = False
        self.hw_contract_vars: dict[str, tk.BooleanVar] = {}
        self.ft_contract_vars: dict[str, tk.BooleanVar] = {}
        self._ft_widgets: list = []
        self._hw_widgets: list = []
        self._res_sort_col: str | None = None
        self._res_sort_asc: bool = True
        self._res_filtered: list[dict] = []
        self._col_vars: dict[str, tk.BooleanVar] = {}
        self._hide_dupes_active: bool = False
        self._cancel_search: bool = False   # flag d'annulation
        self._recruiter_threshold_var = tk.StringVar(value="2")
        self.filter_recruiters_var    = tk.BooleanVar(value=False)
        self.post_company_var         = tk.StringVar(value="")
        self.post_sector_var          = tk.StringVar(value="")
        self._dark_mode: bool = False

        self._setup_style()
        self._build_ui()
        self._apply_theme()   # initialise les styles ttk au démarrage
        self.after(100, self._poll_queue)

    def _font(self, size: int, weight: str = "normal") -> tuple:
        name = "SF Pro Display" if sys.platform == "darwin" else "Segoe UI"
        return (name, size, weight)

    def _card(self, title: str) -> tk.LabelFrame:
        lf = tk.LabelFrame(
            self._search_scroll_frame, text=f"  {title}  ",
            bg=self._CARD, fg=self._FG,
            font=self._font(11, "bold"),
            relief="flat", bd=1, highlightbackground=self._BORDER,
            highlightthickness=1, padx=0, pady=6,
        )
        lf.pack(fill="x", padx=12, pady=(6, 0))
        return lf

    def _field_row(self, parent, label: str, hint: str = "") -> ttk.Frame:
        row = tk.Frame(parent, bg=self._CARD)
        row.pack(fill="x", padx=12, pady=3)
        tk.Label(row, text=label, bg=self._CARD, fg=self._FG, width=16, anchor="e",
                 font=self._font(10)).pack(side="left", padx=(0, 8))
        content = tk.Frame(row, bg=self._CARD)
        content.pack(side="left", fill="x", expand=True)
        if hint:
            tk.Label(row, text=hint, bg=self._CARD, fg=self._FG_MUTED,
                     font=self._font(9)).pack(side="left", padx=(8, 0))
        return content

    # ------------------------------------------------------------------
    # Palettes jour / nuit
    # ------------------------------------------------------------------

    THEME_LIGHT = {
        "BG":       "#F5F5F7",
        "CARD":     "#FFFFFF",
        "ACCENT":   "#0071E3",
        "FG":       "#1D1D1F",
        "FG_MUTED": "#6E6E73",
        "BORDER":   "#D2D2D7",
        # composites
        "BTN_SECONDARY_BG":  "#E8E8ED",
        "BTN_SECONDARY_FG":  "#1D1D1F",
        "BTN_SECONDARY_ACT": "#D2D2D7",
        "TAG_BG":   "#E8F0FE",
        "TAG_FG":   "#0071E3",
        "TAG_ACT":  "#C8D8F8",
        "LOG_BG":   "#1E1E2E",
        "LOG_FG":   "#CDD6F4",
        "LOG_SEL":  "#313244",
        "STAT_BG":  "#FFFFFF",
    }

    THEME_DARK = {
        "BG":       "#1C1C1E",
        "CARD":     "#2C2C2E",
        "ACCENT":   "#0A84FF",
        "FG":       "#F2F2F7",
        "FG_MUTED": "#8E8E93",
        "BORDER":   "#3A3A3C",
        # composites
        "BTN_SECONDARY_BG":  "#3A3A3C",
        "BTN_SECONDARY_FG":  "#F2F2F7",
        "BTN_SECONDARY_ACT": "#48484A",
        "TAG_BG":   "#1C3A5E",
        "TAG_FG":   "#4CA3FF",
        "TAG_ACT":  "#0A3A6E",
        "LOG_BG":   "#111113",
        "LOG_FG":   "#CDD6F4",
        "LOG_SEL":  "#313244",
        "STAT_BG":  "#2C2C2E",
    }

    # ------------------------------------------------------------------
    # Style
    # ------------------------------------------------------------------

    def _setup_style(self):
        T = self.THEME_DARK if self._dark_mode else self.THEME_LIGHT
        BG       = T["BG"]
        CARD     = T["CARD"]
        ACCENT   = T["ACCENT"]
        FG       = T["FG"]
        FG_MUTED = T["FG_MUTED"]
        BORDER   = T["BORDER"]

        self._BG = BG
        self._CARD = CARD
        self._ACCENT = ACCENT
        self._FG = FG
        self._FG_MUTED = FG_MUTED
        self._BORDER = BORDER
        self._T = T   # palette complète accessible partout

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.configure(bg=BG)
        style.configure(".", background=BG, foreground=FG, font=self._font(11))

        style.configure("TLabelframe", background=CARD, relief="flat", borderwidth=1)
        style.configure("TLabelframe.Label", font=self._font(11, "bold"),
                        foreground=FG, background=BG)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Card.TLabel", background=CARD, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=FG_MUTED,
                        font=self._font(9))
        style.configure("CardMuted.TLabel", background=CARD, foreground=FG_MUTED,
                        font=self._font(9))
        style.configure("Run.TButton", font=self._font(12, "bold"),
                        foreground="white", background=ACCENT, padding=(20, 8))
        style.map("Run.TButton",
                  background=[("active", "#005BBB"), ("disabled", "#A0A0A0")],
                  foreground=[("disabled", "#D0D0D0")])
        style.configure("TButton", padding=(6, 4), background=T["BTN_SECONDARY_BG"],
                        foreground=FG)
        style.map("TButton", background=[("active", T["BTN_SECONDARY_ACT"])])
        style.configure("TEntry", fieldbackground=CARD, foreground=FG, padding=(6, 4))
        style.configure("TCombobox", fieldbackground=CARD, foreground=FG,
                        selectbackground=ACCENT, selectforeground="white", padding=(4, 3))
        style.map("TCombobox",
                  fieldbackground=[("readonly", CARD)],
                  foreground=[("readonly", FG)],
                  selectbackground=[("readonly", ACCENT)])
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.configure("Card.TCheckbutton", background=CARD, foreground=FG)
        style.configure("TSeparator", background=BORDER)
        style.configure("TProgressbar", troughcolor=BORDER, background=ACCENT, thickness=6)
        style.configure("TScrollbar", background=T["BTN_SECONDARY_BG"],
                        troughcolor=BG, bordercolor=BORDER, arrowcolor=FG)
        style.configure("TNotebook", background=BG, bordercolor=BORDER)
        style.configure("TNotebook.Tab", background=T["BTN_SECONDARY_BG"],
                        foreground=FG, padding=(10, 4))
        style.map("TNotebook.Tab",
                  background=[("selected", CARD)],
                  foreground=[("selected", ACCENT)])
        style.configure("Tag.TFrame", background=T["TAG_BG"], relief="flat")
        style.configure("Tag.TLabel", background=T["TAG_BG"], foreground=T["TAG_FG"],
                        font=self._font(10))
        style.configure("TagX.TButton", background=T["TAG_BG"], foreground=T["TAG_FG"],
                        padding=(2, 0), relief="flat", font=self._font(10, "bold"))
        style.map("TagX.TButton", background=[("active", T["TAG_ACT"])])

    # ------------------------------------------------------------------
    # Top-level layout — navbar + page container
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.configure(bg=self._BG)

        # ── Fixed top navbar ─────────────────────────────────────────
        navbar = tk.Frame(self, bg=self._ACCENT, pady=10)
        navbar.pack(fill="x", side="top")

        # Left: logo
        tk.Label(navbar, text="Scrapa", bg=self._ACCENT, fg="white",
                 font=self._font(18, "bold")).pack(side="left", padx=18)

        # Right: theme toggle + settings
        tk.Button(
            navbar, text="⚙️", bg=self._ACCENT, fg="white",
            activebackground="#005BBB", activeforeground="white",
            relief="flat", cursor="hand2", padx=10,
            font=self._font(14),
            command=self._open_settings,
        ).pack(side="right", padx=14)

        self._theme_btn = tk.Button(
            navbar, text="🌙", bg=self._ACCENT, fg="white",
            activebackground="#005BBB", activeforeground="white",
            relief="flat", cursor="hand2", padx=10,
            font=self._font(14),
            command=self._toggle_theme,
        )
        self._theme_btn.pack(side="right", padx=(0, 4))

        # Middle: nav tabs
        nav_mid = tk.Frame(navbar, bg=self._ACCENT)
        nav_mid.pack(side="left", padx=20)

        self._nav_search_btn = tk.Button(
            nav_mid, text="🔍 Recherche",
            relief="flat", cursor="hand2", padx=14, pady=4,
            font=self._font(11, "bold"),
            bg=self._CARD, fg=self._ACCENT,
            activebackground=self._ACCENT, activeforeground="white",
            command=self._show_search_page,
        )
        self._nav_search_btn.pack(side="left", padx=(0, 4))

        self._nav_results_var = tk.StringVar(value="📊 Résultats (0)")
        self._nav_results_btn = tk.Button(
            nav_mid, textvariable=self._nav_results_var,
            relief="flat", cursor="hand2", padx=14, pady=4,
            font=self._font(11, "bold"),
            bg=self._ACCENT, fg="white",
            activebackground="#005BBB", activeforeground="white",
            command=self._show_results_page,
        )
        self._nav_results_btn.pack(side="left", padx=(0, 4))

        # ── Page container ───────────────────────────────────────────
        self._page_container = tk.Frame(self, bg=self._BG)
        self._page_container.pack(fill="both", expand=True)

        # Build both pages
        self._build_search_page()
        self._build_results_page()

        # Start on search page
        self._show_search_page()

    # ------------------------------------------------------------------
    # Thème jour / nuit
    # ------------------------------------------------------------------

    def _toggle_theme(self):
        self._dark_mode = not self._dark_mode
        self._setup_style()       # recalcule les variables de couleur + styles ttk
        self._apply_theme()       # repeint tous les widgets tk.*
        self._theme_btn.configure(
            text="☀️" if self._dark_mode else "🌙",
            bg=self._ACCENT,
        )

    def _apply_theme(self):
        """Repeint récursivement tous les widgets avec la palette courante."""
        T        = self._T
        BG       = self._BG
        CARD     = self._CARD
        FG       = self._FG
        FG_MUTED = self._FG_MUTED
        ACCENT   = self._ACCENT
        BORDER   = self._BORDER
        SEC_BG   = T["BTN_SECONDARY_BG"]
        SEC_FG   = T["BTN_SECONDARY_FG"]
        SEC_ACT  = T["BTN_SECONDARY_ACT"]

        # ── Couleurs connues des deux thèmes ─────────────────────────
        ALL_BG   = {"#F5F5F7", "#1C1C1E"}   # fonds page
        ALL_CARD = {"#FFFFFF", "#2C2C2E"}   # fonds card
        ALL_FG   = {"#1D1D1F", "#F2F2F7"}   # texte principal
        ALL_MUTED= {"#6E6E73", "#8E8E93"}   # texte muted
        ALL_SEC  = {"#E8E8ED", "#3A3A3C", "#D2D2D7", "#48484A"}  # secondaire
        ALL_ACNT = {"#0071E3", "#0A84FF"}   # accent

        def recolor(widget):
            wtype = widget.winfo_class()
            try:
                if isinstance(widget, tk.Tk):
                    widget.configure(bg=BG)

                elif wtype == "Frame":
                    cur = widget.cget("bg")
                    if cur in ALL_CARD:
                        widget.configure(bg=CARD)
                    elif cur in ALL_BG:
                        widget.configure(bg=BG)

                elif wtype == "Labelframe":
                    widget.configure(bg=CARD, fg=FG, highlightbackground=BORDER)

                elif wtype == "Label":
                    cur_bg = widget.cget("bg")
                    cur_fg = widget.cget("fg")
                    if cur_bg in ALL_CARD:
                        widget.configure(bg=CARD)
                    elif cur_bg in ALL_BG:
                        widget.configure(bg=BG)
                    if cur_fg in ALL_FG:
                        widget.configure(fg=FG)
                    elif cur_fg in ALL_MUTED:
                        widget.configure(fg=FG_MUTED)

                elif wtype == "Checkbutton":
                    cur_bg = widget.cget("bg")
                    new_bg = CARD if cur_bg in ALL_CARD else BG
                    check_color = "#30D158" if self._dark_mode else ACCENT
                    widget.configure(bg=new_bg, fg=FG,
                                     activebackground=new_bg, selectcolor=check_color)

                elif wtype == "Button":
                    cur_bg = widget.cget("bg")
                    if cur_bg in {"#FF3B30", "#CC2D24"}:
                        pass   # rouge — inchangé
                    elif cur_bg in {"#34C759"}:
                        pass   # vert — inchangé
                    else:
                        # Tout le reste passe en bleu accent + texte blanc
                        widget.configure(bg=ACCENT, fg="white",
                                         activebackground="#005BBB",
                                         activeforeground="white")

                elif wtype == "Text":
                    widget.configure(bg=T["LOG_BG"], fg=T["LOG_FG"],
                                     insertbackground=T["LOG_FG"],
                                     selectbackground=T["LOG_SEL"])

                elif wtype == "Listbox":
                    widget.configure(bg=T["LOG_BG"], fg=T["LOG_FG"],
                                     selectbackground=T["LOG_SEL"])

                # ── ttk widgets — on force via option_add ─────────────
                elif wtype in ("TButton", "TCheckbutton"):
                    pass   # géré par ttk.Style

                elif wtype == "TEntry":
                    widget.configure(style="TEntry")

                elif wtype == "TCombobox":
                    widget.configure(style="TCombobox")

            except tk.TclError:
                pass

            for child in widget.winfo_children():
                recolor(child)

        recolor(self)

        # ── Boutons navbar — état selon page active ───────────────────
        # On détecte la page active et on réapplique les couleurs correctes
        try:
            if self._search_page.winfo_ismapped():
                self._nav_search_btn.configure(bg=CARD, fg=ACCENT,
                                               activebackground=ACCENT, activeforeground="white")
                self._nav_results_btn.configure(bg=ACCENT, fg="white",
                                                activebackground="#005BBB", activeforeground="white")
            else:
                self._nav_results_btn.configure(bg=CARD, fg=ACCENT,
                                                activebackground=ACCENT, activeforeground="white")
                self._nav_search_btn.configure(bg=ACCENT, fg="white",
                                               activebackground="#005BBB", activeforeground="white")
        except Exception:
            pass

        # ── Forcer les styles ttk après la récursion ─────────────────
        style = ttk.Style(self)

        # Entry
        style.configure("TEntry",
                         fieldbackground=CARD, foreground=FG,
                         insertcolor=FG, selectbackground=ACCENT,
                         selectforeground="white", bordercolor=BORDER)
        style.map("TEntry",
                  fieldbackground=[("disabled", BG), ("readonly", BG)],
                  foreground=[("disabled", FG_MUTED)])

        # Combobox
        style.configure("TCombobox",
                         fieldbackground=CARD, foreground=FG,
                         selectbackground=ACCENT, selectforeground="white",
                         arrowcolor=FG, bordercolor=BORDER)
        style.map("TCombobox",
                  fieldbackground=[("readonly", CARD), ("disabled", BG)],
                  foreground=[("readonly", FG), ("disabled", FG_MUTED)],
                  arrowcolor=[("disabled", FG_MUTED)])

        # Button ttk
        style.configure("TButton",
                         background=ACCENT, foreground="white",
                         bordercolor=ACCENT, darkcolor=ACCENT, lightcolor=ACCENT,
                         relief="flat", padding=(6, 4))
        style.map("TButton",
                  background=[("active", "#005BBB"), ("disabled", "#A0A0A0")],
                  foreground=[("disabled", "#D0D0D0")])

        # Run button
        style.configure("Run.TButton",
                         background=ACCENT, foreground="white",
                         font=self._font(12, "bold"), padding=(20, 8))
        style.map("Run.TButton",
                  background=[("active", SEC_ACT), ("disabled", "#A0A0A0")],
                  foreground=[("disabled", "#D0D0D0")])

        # Checkbutton ttk
        CHECK_COLOR = "#30D158" if self._dark_mode else ACCENT
        style.configure("TCheckbutton",
                         background=BG, foreground=FG,
                         indicatorcolor=CARD, indicatorrelief="flat")
        style.map("TCheckbutton",
                  background=[("active", BG)],
                  foreground=[("active", FG)],
                  indicatorcolor=[("selected", CHECK_COLOR), ("pressed", CHECK_COLOR)])

        style.configure("Card.TCheckbutton",
                         background=CARD, foreground=FG,
                         indicatorcolor=CARD)
        style.map("Card.TCheckbutton",
                  background=[("active", CARD)],
                  indicatorcolor=[("selected", CHECK_COLOR)])

        # Scrollbar
        style.configure("TScrollbar",
                         background=SEC_BG, troughcolor=BG,
                         bordercolor=BORDER, arrowcolor=FG,
                         darkcolor=SEC_BG, lightcolor=SEC_BG)

        # Notebook
        style.configure("TNotebook", background=BG, bordercolor=BORDER)
        style.configure("TNotebook.Tab",
                         background=SEC_BG, foreground=FG, padding=(10, 4))
        style.map("TNotebook.Tab",
                  background=[("selected", CARD)],
                  foreground=[("selected", ACCENT)])

        # Labelframe ttk
        style.configure("TLabelframe", background=CARD, bordercolor=BORDER)
        style.configure("TLabelframe.Label", background=BG, foreground=FG,
                         font=self._font(11, "bold"))

        # Treeview
        style.configure("Treeview",
                         background=CARD, foreground=FG,
                         fieldbackground=CARD, rowheight=22,
                         bordercolor=BORDER)
        style.configure("Treeview.Heading",
                         background=SEC_BG, foreground=FG,
                         relief="flat", bordercolor=BORDER)
        style.map("Treeview",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "white")])
        style.map("Treeview.Heading",
                  background=[("active", SEC_ACT)])
        try:
            self._res_tree.tag_configure("odd",  background=BG)
            self._res_tree.tag_configure("even", background=CARD)
        except Exception:
            pass

        # Progressbar
        style.configure("TProgressbar",
                         troughcolor=BORDER, background=ACCENT, thickness=6)

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def _show_search_page(self):
        self._results_page.pack_forget()
        self._search_page.pack(fill="both", expand=True)
        # Onglet actif : fond blanc/card + texte accent
        # Onglet inactif : fond accent + texte blanc → lisible dans les deux thèmes
        self._nav_search_btn.configure(bg=self._CARD, fg=self._ACCENT,
                                       activebackground=self._ACCENT, activeforeground="white")
        self._nav_results_btn.configure(bg=self._ACCENT, fg="white",
                                        activebackground="#005BBB", activeforeground="white")

    def _show_results_page(self):
        self._search_page.pack_forget()
        self._results_page.pack(fill="both", expand=True)
        self._nav_results_btn.configure(bg=self._CARD, fg=self._ACCENT,
                                        activebackground=self._ACCENT, activeforeground="white")
        self._nav_search_btn.configure(bg=self._ACCENT, fg="white",
                                       activebackground="#005BBB", activeforeground="white")

    def _update_results_count(self, n: int):
        self._nav_results_var.set(f"📊 Résultats ({n})")

    # ------------------------------------------------------------------
    # Page 1 — Recherche
    # ------------------------------------------------------------------

    def _build_search_page(self):
        self._search_page = tk.Frame(self._page_container, bg=self._BG)

        # Scrollable canvas for the form
        canvas = tk.Canvas(self._search_page, highlightthickness=0, bg=self._BG)
        vsb = ttk.Scrollbar(self._search_page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._search_scroll_frame = ttk.Frame(canvas, style="TFrame")
        _win_id = canvas.create_window((0, 0), window=self._search_scroll_frame, anchor="nw")

        self._search_scroll_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(_win_id, width=e.width))
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
            add="+",
        )

        # Form sections
        self._build_credentials()
        self._build_ft_section()
        self._build_hw_section()
        self._build_post_section()
        self._build_actions()
        self._build_log()

    # ------------------------------------------------------------------
    # Page 2 — Résultats
    # ------------------------------------------------------------------

    def _build_results_page(self):
        self._results_page = tk.Frame(self._page_container, bg=self._BG)

        # ── Top toolbar ──────────────────────────────────────────────
        tb = tk.Frame(self._results_page, bg=self._BG, pady=8)
        tb.pack(fill="x", padx=12)

        tk.Button(
            tb, text="← Retour", relief="flat", cursor="hand2",
            bg=self._ACCENT, fg="white", activebackground="#005BBB",
            activeforeground="white", font=self._font(10, "bold"),
            padx=10, command=self._show_search_page,
        ).pack(side="left", padx=(0, 12))

        tk.Label(tb, text="🔎", bg=self._BG, fg=self._FG,
                 font=self._font(11)).pack(side="left")
        self._res_filter_var = tk.StringVar()
        self._res_filter_var.trace_add("write", self._on_res_filter_change)
        ttk.Entry(tb, textvariable=self._res_filter_var, width=28).pack(side="left", padx=(2, 12))

        tk.Label(tb, text="Trier par :", bg=self._BG, fg=self._FG,
                 font=self._font(10)).pack(side="left", padx=(0, 4))
        self._res_sort_var = tk.StringVar(value="date_publication")
        ttk.Combobox(tb, textvariable=self._res_sort_var, values=COL_KEYS,
                     state="readonly", width=18).pack(side="left", padx=(0, 4))
        self._res_sort_asc_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tb, text="↑ Croissant",
                        variable=self._res_sort_asc_var).pack(side="left", padx=(0, 6))
        ttk.Button(tb, text="Trier",
                   command=self._apply_res_sort).pack(side="left", padx=(0, 16))

        # Right: poubelle + download buttons
        tk.Button(
            tb, text="🗑  Vider", relief="flat", cursor="hand2",
            bg="#FF3B30", fg="white", activebackground="#CC2D24",
            activeforeground="white", font=self._font(10),
            padx=8, command=self._clear_results,
        ).pack(side="right", padx=(4, 0))
        self._dl_sel_var = tk.StringVar(value="💾 Télécharger la sélection (0)")
        self._dl_sel_btn = tk.Button(
            tb, textvariable=self._dl_sel_var,
            relief="flat", cursor="hand2",
            bg=self._ACCENT, fg="white",
            activebackground="#005BBB", activeforeground="white",
            font=self._font(10), padx=8, command=self._download_selection,
        )
        self._dl_sel_btn.pack(side="right", padx=(4, 0))

        self._dl_all_var = tk.StringVar(value="💾 Télécharger tout (0)")
        self._dl_all_btn = tk.Button(
            tb, textvariable=self._dl_all_var,
            relief="flat", cursor="hand2", bg=self._ACCENT, fg="white",
            activebackground="#005BBB", font=self._font(10, "bold"),
            padx=8, command=self._download_all_results,
        )
        self._dl_all_btn.pack(side="right", padx=(4, 4))

        # ── Barre de statut résultats ─────────────────────────────────
        stat_bar = tk.Frame(self._results_page, bg=self._CARD,
                            highlightbackground=self._BORDER, highlightthickness=1)
        stat_bar.pack(fill="x", padx=12, pady=(0, 4))

        self._res_stat_var = tk.StringVar(value="")
        tk.Label(stat_bar, textvariable=self._res_stat_var,
                 bg=self._CARD, fg=self._FG, font=self._font(10),
                 anchor="w", padx=12, pady=5).pack(side="left", fill="x", expand=True)

        # Bouton "Cacher les doublons"
        self._hide_dupes_btn = tk.Button(
            stat_bar, text="👁  Cacher les doublons du cache",
            relief="solid", cursor="hand2",
            bg=self._ACCENT, fg="white",
            highlightbackground=self._BORDER, highlightthickness=1,
            activebackground="#005BBB", activeforeground="white",
            font=self._font(9), padx=10,
            command=self._toggle_hide_dupes,
        )
        self._hide_dupes_btn.pack(side="right", padx=6, pady=4)

        # ── Main Treeview ─────────────────────────────────────────────
        tree_frame = tk.Frame(self._results_page, bg=self._BG)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        vsb2 = ttk.Scrollbar(tree_frame, orient="vertical")
        hsb2 = ttk.Scrollbar(tree_frame, orient="horizontal")
        self._res_tree = ttk.Treeview(
            tree_frame,
            columns=COL_KEYS,
            show="headings",
            yscrollcommand=vsb2.set,
            xscrollcommand=hsb2.set,
            selectmode="extended",
        )
        vsb2.configure(command=self._res_tree.yview)
        hsb2.configure(command=self._res_tree.xview)

        for key in COL_KEYS:
            lbl = COL_LABELS[key]
            w   = COL_WIDTHS[key]
            self._res_tree.heading(
                key, text=lbl,
                command=lambda k=key: self._sort_by_col(k),
            )
            self._res_tree.column(
                key, width=w, minwidth=50, stretch=(key == "description"),
            )

        self._res_tree.tag_configure("odd",  background="#F9F9FB")
        self._res_tree.tag_configure("even", background="#FFFFFF")

        self._res_tree.grid(row=0, column=0, sticky="nsew")
        vsb2.grid(row=0, column=1, sticky="ns")
        hsb2.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self._res_tree.bind("<Double-1>", self._on_res_double_click)

        # ── Bottom notebook ───────────────────────────────────────────
        self._res_nb = ttk.Notebook(self._results_page)
        self._res_nb.pack(fill="x", padx=12, pady=(0, 6))

        self._res_tab_summary = ttk.Frame(self._res_nb)
        self._res_nb.add(self._res_tab_summary, text="  📋 Résultats (0)  ")

        self._res_tab_ignored = ttk.Frame(self._res_nb)
        self._res_nb.add(self._res_tab_ignored, text="  🚫 Déjà exportés (0)  ")

        self._res_tab_cols = ttk.Frame(self._res_nb)
        self._res_nb.add(self._res_tab_cols, text="  🗂  Colonnes CSV  ")

        self._res_summary_lbl = tk.Label(
            self._res_tab_summary, text="Aucun résultat.",
            bg=self._BG, fg=self._FG_MUTED, font=self._font(10),
        )
        self._res_summary_lbl.pack(anchor="w", padx=12, pady=8)

        # Ignored listbox (built lazily in _refresh_results_page)
        self._res_ignored_frame = tk.Frame(self._res_tab_ignored, bg=self._BG)
        self._res_ignored_frame.pack(fill="both", expand=True)

        # Colonnes CSV — checkboxes
        self._build_col_selector(self._res_tab_cols)

    # ------------------------------------------------------------------
    # Sélecteur de colonnes CSV
    # ------------------------------------------------------------------

    def _build_col_selector(self, parent: tk.Frame):
        """Panneau de sélection des colonnes à inclure dans le CSV."""
        self._col_vars: dict[str, tk.BooleanVar] = {}
        LABELS = {
            "url":              "Lien (URL)",
            "source":           "Source (FT / HW)",
            "id":               "ID offre",
            "intitule":         "Intitulé du poste",
            "entreprise":       "Entreprise",
            "entreprise_url":   "URL entreprise",
            "ville":            "Ville",
            "region":           "Région",
            "code_postal":      "Code postal",
            "secteur":          "Secteur",
            "domaine":          "Domaine",
            "type_contrat":     "Type de contrat",
            "teletravail":      "Télétravail",
            "salaire_libelle":  "Salaire (libellé)",
            "salaire_min":      "Salaire min (€)",
            "salaire_max":      "Salaire max (€)",
            "experience":       "Expérience requise",
            "formation":        "Formation requise",
            "competences":      "Compétences",
            "taille_entreprise":"Taille entreprise",
            "effectif_entreprise":"Effectif entreprise",
            "date_publication": "Date de publication",
            "description":      "Description",
        }

        # ── En-tête avec boutons ──────────────────────────────────────
        hdr = tk.Frame(parent, bg=self._BG)
        hdr.pack(fill="x", padx=10, pady=(6, 4))

        tk.Label(hdr, text="Choisir les colonnes à inclure dans le CSV :",
                 bg=self._BG, fg=self._FG, font=self._font(10, "bold")).pack(side="left")

        tk.Button(hdr, text="✓ Tout sélectionner",
                  bg=self._ACCENT, fg="white", relief="flat", cursor="hand2",
                  activebackground="#005BBB", activeforeground="white",
                  font=self._font(9), padx=8,
                  command=self._select_all_cols).pack(side="right", padx=(4, 0))
        tk.Button(hdr, text="✗ Tout désélectionner",
                  bg=self._ACCENT, fg="white", relief="flat", cursor="hand2",
                  activebackground="#005BBB", activeforeground="white",
                  font=self._font(9), padx=8,
                  command=self._deselect_all_cols).pack(side="right", padx=(4, 0))
        tk.Button(hdr, text="★ Essentielles",
                  bg=self._ACCENT, fg="white", relief="flat", cursor="hand2",
                  font=self._font(9), padx=8,
                  command=self._select_essential_cols).pack(side="right", padx=(4, 0))
        # ── Grille de checkboxes ──────────────────────────────────────
        grid = tk.Frame(parent, bg=self._BG)
        grid.pack(fill="x", padx=10, pady=(0, 6))

        ALL_FIELDS = HW_UNIFIED_FIELDNAMES
        cols_per_row = 4

        for i, field in enumerate(ALL_FIELDS):
            var = tk.BooleanVar(value=True)
            self._col_vars[field] = var
            label = LABELS.get(field, field)
            cb = tk.Checkbutton(
                grid, text=label, variable=var,
                bg=self._BG, fg=self._FG,
                activebackground=self._BG, selectcolor=self._CARD,
                font=self._font(9),
                command=self._on_col_selection_change,
            )
            row_i = i // cols_per_row
            col_i = i % cols_per_row
            cb.grid(row=row_i, column=col_i, sticky="w", padx=(0, 16), pady=2)

        # ── Info + Aperçu ─────────────────────────────────────────────
        footer = tk.Frame(parent, bg=self._BG)
        footer.pack(fill="x", padx=10, pady=(2, 4))

        self._col_info_var = tk.StringVar(value=self._col_info_text())
        tk.Label(footer, textvariable=self._col_info_var,
                 bg=self._BG, fg=self._FG_MUTED, font=self._font(9)).pack(side="left")

        tk.Button(
            footer, text="👁  Aperçu du tableau",
            bg=self._ACCENT, fg="white", relief="flat", cursor="hand2",
            activebackground="#005BBB", font=self._font(9, "bold"), padx=10,
            command=self._apply_col_preview,
        ).pack(side="right", padx=(4, 0))

    def _col_info_text(self) -> str:
        if not hasattr(self, "_col_vars"):
            return ""
        selected = [k for k, v in self._col_vars.items() if v.get()]
        total = len(self._col_vars)
        return f"{len(selected)}/{total} colonnes sélectionnées"

    def _on_col_selection_change(self):
        if hasattr(self, "_col_info_var"):
            self._col_info_var.set(self._col_info_text())

    def _select_all_cols(self):
        for var in self._col_vars.values():
            var.set(True)
        self._on_col_selection_change()

    def _deselect_all_cols(self):
        for var in self._col_vars.values():
            var.set(False)
        self._on_col_selection_change()

    def _select_essential_cols(self):
        """Sélectionne uniquement les colonnes les plus utiles."""
        essential = {
            "url", "intitule", "entreprise", "ville",
            "type_contrat", "salaire_libelle", "teletravail",
            "date_publication", "experience", "secteur", "description",
        }
        for field, var in self._col_vars.items():
            var.set(field in essential)
        self._on_col_selection_change()

    def _get_selected_columns(self) -> list[str]:
        """Retourne la liste ordonnée des colonnes sélectionnées (ordre de UNIFIED_FIELDNAMES)."""
        ALL_FIELDS = HW_UNIFIED_FIELDNAMES
        selected = [f for f in ALL_FIELDS if self._col_vars.get(f, tk.BooleanVar(value=True)).get()]
        # Garantit qu'au minimum les colonnes essentielles sont présentes
        if not selected:
            selected = ["intitule", "entreprise", "ville", "url"]
        return selected

    def _refresh_results_page(self):
        """Populate the results page with current _pending_rows / _ignored_ids."""
        rows = self._pending_rows
        ignored = self._ignored_ids
        n = len(rows)

        self._update_results_count(n)
        self._dl_all_var.set(f"💾 Télécharger tout ({n})")
        self._dl_sel_var.set(f"💾 Télécharger la sélection ({n})")

        # Reset mode cacher les doublons
        self._hide_dupes_active = False
        self._hide_dupes_btn.configure(
            text="👁  Cacher les doublons du cache",
            bg=self._CARD, fg=self._FG,
        )

        # Reset filter & sort
        self._res_filter_var.set("")
        self._res_filtered = list(rows)
        self._res_sort_col = None
        self._res_sort_asc = True
        for k in COL_KEYS:
            self._res_tree.heading(k, text=COL_LABELS[k])

        self._populate_tree(self._res_filtered)
        self._update_stat_bar()

        # Summary tab
        self._res_nb.tab(0, text=f"  📋 Résultats ({n})  ")
        self._res_summary_lbl.configure(
            text=f"{n} offre(s) nouvelle(s)"
            + (f"  •  {len(ignored)} ignorée(s) (déjà dans le cache)" if ignored else "")
        )

        # Ignored tab
        self._res_nb.tab(1, text=f"  🚫 Déjà exportés ({len(ignored)})  ")
        for w in self._res_ignored_frame.winfo_children():
            w.destroy()
        if ignored:
            vsb = ttk.Scrollbar(self._res_ignored_frame, orient="vertical")
            lb = tk.Listbox(
                self._res_ignored_frame, yscrollcommand=vsb.set,
                font=("Menlo", 9) if sys.platform == "darwin" else ("Consolas", 9),
                bg="#1E1E2E", fg="#CDD6F4", selectbackground="#313244",
                relief="flat", height=5,
            )
            vsb.configure(command=lb.yview)
            lb.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")
            for i, iid in enumerate(sorted(ignored)):
                lb.insert("end", f"  {iid}")
                lb.itemconfigure(i, background="#1E1E2E" if i % 2 == 0 else "#1A1A2A")
        else:
            tk.Label(
                self._res_ignored_frame,
                text="Aucun doublon détecté lors de cette recherche.",
                bg=self._BG, fg=self._FG_MUTED, font=self._font(10),
            ).pack(pady=10)

    def _populate_tree(self, rows: list[dict]):
        self._res_tree.delete(*self._res_tree.get_children())
        for i, row in enumerate(rows):
            vals = tuple(str(row.get(k) or "") for k in COL_KEYS)
            tag  = "odd" if i % 2 else "even"
            self._res_tree.insert("", "end", iid=str(i), values=vals, tags=(tag,))
        n_shown = len(rows)
        n_total = len(self._pending_rows)
        self._dl_sel_var.set(f"💾 Télécharger la sélection ({n_shown})")
        self._dl_all_var.set(f"💾 Télécharger tout ({n_total})")
        self._update_stat_bar()

    def _update_stat_bar(self):
        """Met à jour la barre de statut : total / sans doublons / vue actuelle."""
        if not hasattr(self, "_res_stat_var"):
            return
        n_total = len(self._pending_rows)
        if n_total == 0:
            self._res_stat_var.set("Aucun résultat.")
            return

        # Calcul sans doublons (lecture seule du cache)
        new_rows, dupes = filter_new(self._pending_rows)
        n_new = len(new_rows)
        n_dupes = dupes

        n_view = len(self._res_filtered)

        parts = [f"Total : {n_total}"]
        if n_dupes:
            parts.append(f"Nouveaux (hors cache) : {n_new}  •  Doublons : {n_dupes}")
        else:
            parts.append("Aucun doublon dans le cache")
        if n_view != n_total:
            parts.append(f"Vue filtrée : {n_view}")

        self._res_stat_var.set("   |   ".join(parts))

    def _toggle_hide_dupes(self):
        """Active/désactive le mode 'cacher les doublons du cache' dans le tableau."""
        if not self._pending_rows:
            return

        self._hide_dupes_active = not self._hide_dupes_active

        if self._hide_dupes_active:
            # Filtre en lecture seule — ne modifie pas _pending_rows
            new_rows, _ = filter_new(self._pending_rows)
            self._res_filtered = new_rows
            n = len(new_rows)
            n_hidden = len(self._pending_rows) - n
            self._hide_dupes_btn.configure(
                text=f"✅  Doublons cachés ({n_hidden})  — Tout réafficher",
                bg="#34C759", fg="white",
            )
            self._dl_all_var.set(f"💾 Télécharger tout ({n})")
        else:
            self._res_filtered = list(self._pending_rows)
            self._hide_dupes_btn.configure(
                text="👁  Cacher les doublons du cache",
                bg=self._ACCENT, fg="white",
            )
            n = len(self._pending_rows)
            self._dl_all_var.set(f"💾 Télécharger tout ({n})")

        # Réapplique le filtre texte si présent
        needle = self._res_filter_var.get().strip().lower()
        if needle:
            self._res_filtered = [
                r for r in self._res_filtered
                if any(needle in str(v).lower() for v in r.values())
            ]

        self._populate_tree(self._res_filtered)

    def _apply_col_preview(self):
        """Recharge le tableau en n'affichant que les colonnes CSV sélectionnées."""
        selected_cols = self._get_selected_columns()
        # On réaffiche le Treeview avec seulement les colonnes sélectionnées
        # Les colonnes du tableau (COL_KEYS) restent fixes — on masque les autres
        for key in COL_KEYS:
            if key in selected_cols:
                # Restaure la largeur d'origine
                self._res_tree.column(key, width=COL_WIDTHS[key], minwidth=40,
                                      stretch=(key == "description"))
            else:
                # Masque la colonne (largeur 0, minwidth 0)
                self._res_tree.column(key, width=0, minwidth=0, stretch=False)
        # Bascule sur l'onglet résultats pour voir le résultat
        self._res_nb.select(0)

    # ------------------------------------------------------------------
    # Results page — filter, sort, interactions
    # ------------------------------------------------------------------

    def _on_res_filter_change(self, *_):
        needle = self._res_filter_var.get().strip().lower()
        # Base = tous les pending, ou sans doublons si mode actif
        if self._hide_dupes_active:
            base, _ = filter_new(self._pending_rows)
        else:
            base = list(self._pending_rows)

        if not needle:
            self._res_filtered = base
        else:
            self._res_filtered = [
                r for r in base
                if any(needle in str(v).lower() for v in r.values())
            ]
        self._populate_tree(self._res_filtered)

    def _sort_by_col(self, col: str):
        """Sort by clicking column header — toggles asc/desc."""
        if self._res_sort_col == col:
            self._res_sort_asc = not self._res_sort_asc
        else:
            self._res_sort_col = col
            self._res_sort_asc = True
        self._res_sort_var.set(col)
        self._res_sort_asc_var.set(self._res_sort_asc)
        self._do_sort(self._res_filtered, col, self._res_sort_asc)
        for k in COL_KEYS:
            arrow = ""
            if k == col:
                arrow = "  ↑" if self._res_sort_asc else "  ↓"
            self._res_tree.heading(k, text=COL_LABELS[k] + arrow)
        self._populate_tree(self._res_filtered)

    def _apply_res_sort(self):
        col = self._res_sort_var.get()
        asc = self._res_sort_asc_var.get()
        self._res_sort_col = col
        self._res_sort_asc = asc
        self._do_sort(self._res_filtered, col, asc)
        for k in COL_KEYS:
            arrow = ""
            if k == col:
                arrow = "  ↑" if asc else "  ↓"
            self._res_tree.heading(k, text=COL_LABELS[k] + arrow)
        self._populate_tree(self._res_filtered)

    @staticmethod
    def _do_sort(rows: list[dict], col: str, asc: bool):
        def key(r):
            v = r.get(col) or ""
            try:
                return (0, float(str(v).replace(" ", "").replace(",", ".")))
            except (ValueError, TypeError):
                return (1, str(v).lower())
        rows.sort(key=key, reverse=not asc)

    def _on_res_double_click(self, event):
        item = self._res_tree.identify_row(event.y)
        if not item:
            return
        vals = self._res_tree.item(item, "values")
        if not vals:
            return
        url = vals[0]
        if url and url.startswith("http"):
            webbrowser.open(url)

    # ------------------------------------------------------------------
    # Results page — download actions
    # ------------------------------------------------------------------

    def _clear_results(self):
        """Vide tous les résultats en mémoire sans télécharger ni toucher au cache."""
        if not self._pending_rows:
            return
        n = len(self._pending_rows)
        if not messagebox.askyesno(
            "Vider les résultats",
            f"Supprimer les {n} offre(s) en mémoire ?\n\n"
            "Les IDs ne seront PAS enregistrés dans le cache — "
            "ces offres réapparaîtront lors de la prochaine recherche.",
            parent=self,
        ):
            return
        self._pending_rows = []
        self._ignored_ids  = set()
        self._res_filtered = []
        self._search_mode  = "reset"
        self._populate_tree([])
        self._update_results_count(0)
        self._dl_all_var.set("💾 Télécharger tout (0)")
        self._dl_sel_var.set("💾 Télécharger la sélection (0)")
        self._download_btn.configure(state="disabled")
        self._add_btn.configure(state="disabled")
        self._res_nb.tab(0, text="  📋 Résultats (0)  ")
        self._res_nb.tab(1, text="  🚫 Déjà exportés (0)  ")
        self._res_summary_lbl.configure(text="Aucun résultat.")
        self._status_var.set("Résultats vidés — prêt pour une nouvelle recherche.")
        self._log_msg(f"🗑  {n} offre(s) supprimée(s) de la mémoire (cache intact).")

    def _download_all_results(self):
        rows = self._pending_rows
        if not rows:
            messagebox.showinfo("Vide", "Aucune offre à télécharger.")
            return
        self._do_download(rows, label=f"{len(rows)} offres (tout)")

    def _download_selection(self):
        rows = self._res_filtered
        if not rows:
            messagebox.showinfo("Vide", "Aucune offre dans la sélection.")
            return
        self._do_download(rows, label=f"{len(rows)} offres (sélection filtrée)")

    def _do_download(self, rows: list[dict], label: str = ""):
        if not rows:
            return

        # ── Vérification finale anti-doublons ──────────────────────────────
        # Re-filtre contre le cache au moment du téléchargement, pas à la recherche.
        # Couvre le cas où le cache aurait changé entre la recherche et le DL
        # (ex: deux sessions en parallèle, ou téléchargements partiels successifs).
        clean_rows, skipped = filter_new(rows)

        if skipped:
            self._log_msg(
                f"⚠️  {skipped} offre(s) déjà dans le cache supprimée(s) avant export."
            )

        if not clean_rows:
            messagebox.showinfo(
                "Rien à exporter",
                f"Toutes les offres sélectionnées ({len(rows)}) sont déjà dans le cache.\n"
                "Aucun fichier créé.",
                parent=self,
            )
            return

        if skipped:
            # Demander confirmation si on a retiré des lignes
            if not messagebox.askyesno(
                "Doublons supprimés",
                f"{skipped} offre(s) déjà exportée(s) ont été retirées.\n"
                f"Il reste {len(clean_rows)} offre(s) à télécharger.\n\n"
                "Continuer ?",
                parent=self,
            ):
                return

        default_name = f"offres_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        output_path = filedialog.asksaveasfilename(
            title=f"Enregistrer — {label}",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV", "*.csv")],
            parent=self,
        )
        if not output_path:
            return

        count = export_csv_rows(clean_rows, output_path, fieldnames=self._get_selected_columns())
        commit_rows(clean_rows)

        # Retire toutes les rows téléchargées de la mémoire (via get_row_id pour les sans-ID)
        dl_keys = {get_row_id(r) for r in clean_rows}
        self._pending_rows = [r for r in self._pending_rows if get_row_id(r) not in dl_keys]
        self._res_filtered  = [r for r in self._res_filtered  if get_row_id(r) not in dl_keys]

        self.last_output_path = output_path
        self._open_btn.configure(state="normal")
        self._status_var.set(f"✅  {count} offres exportées  —  {output_path}")
        self._log_msg(f"💾 {count} offres enregistrées → {output_path}")
        if skipped:
            self._log_msg(f"   ({skipped} doublon(s) supprimé(s) avant export)")
        self._update_results_count(len(self._pending_rows))
        self._dl_all_var.set(f"💾 Télécharger tout ({len(self._pending_rows)})")
        self._populate_tree(self._res_filtered)
        messagebox.showinfo(
            "Export terminé",
            f"{count} offres enregistrées."
            + (f"\n{skipped} doublon(s) ignoré(s)." if skipped else "")
            + "\nIDs mémorisés dans le cache.",
            parent=self,
        )

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    def _build_credentials(self):
        lf = self._card("Identifiants")

        for (label_text, id_var_name, secret_var_name, secret_entry_attr,
             toggle_cmd, test_cmd, test_btn_attr, status_attr, env_id, env_secret) in [
            ("France Travail", "client_id_var", "client_secret_var", "_ft_secret_entry",
             self._toggle_ft_secret, self._test_ft, "_test_ft_btn", "_ft_status_lbl",
             "FRANCE_TRAVAIL_CLIENT_ID", "FRANCE_TRAVAIL_CLIENT_SECRET"),
            ("Apify / HelloWork", "apify_id_dummy", "apify_token_var", "_hw_secret_entry",
             self._toggle_hw_secret, self._test_apify, "_test_hw_btn", "_hw_status_lbl",
             None, "APIFY_TOKEN"),
        ]:
            row = ttk.Frame(lf, style="Card.TFrame")
            row.pack(fill="x", padx=12, pady=4)
            ttk.Label(row, text=label_text, style="Card.TLabel",
                      width=16, anchor="e").pack(side="left", padx=(0, 8))

            if id_var_name == "apify_id_dummy":
                setattr(self, "apify_token_var",
                        tk.StringVar(value=os.environ.get("APIFY_TOKEN", "")))
                entry = ttk.Entry(row, textvariable=self.apify_token_var,
                                  width=44, show="•")
                entry.pack(side="left", padx=(0, 4))
                setattr(self, secret_entry_attr, entry)
            else:
                setattr(self, id_var_name,
                        tk.StringVar(value=os.environ.get(env_id, "")))
                setattr(self, secret_var_name,
                        tk.StringVar(value=os.environ.get(env_secret, "")))
                e1 = ttk.Entry(row, textvariable=getattr(self, id_var_name), width=22)
                e1.pack(side="left", padx=(0, 4))
                ttk.Label(row, text="/", style="Card.TLabel").pack(side="left", padx=2)
                entry = ttk.Entry(row,
                                  textvariable=getattr(self, secret_var_name),
                                  width=22, show="•")
                entry.pack(side="left", padx=(0, 4))
                setattr(self, secret_entry_attr, entry)

            ttk.Button(row, text="👁", width=3,
                       command=toggle_cmd).pack(side="left", padx=(0, 6))
            btn = ttk.Button(row, text="Tester", command=test_cmd, width=7)
            btn.pack(side="left")
            setattr(self, test_btn_attr, btn)
            lbl = ttk.Label(row, text="", style="Card.TLabel", width=10)
            lbl.pack(side="left", padx=6)
            setattr(self, status_attr, lbl)

        cb_row = ttk.Frame(lf, style="Card.TFrame")
        cb_row.pack(fill="x", padx=12, pady=(2, 8))
        self.save_creds_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cb_row, text="Mémoriser dans .env",
                        variable=self.save_creds_var,
                        style="Card.TCheckbutton").pack(side="left")

    # ------------------------------------------------------------------
    # France Travail section
    # ------------------------------------------------------------------

    def _build_ft_section(self):
        lf = self._card("🇫🇷  France Travail")

        en_row = tk.Frame(lf, bg=self._CARD)
        en_row.pack(fill="x", padx=12, pady=(4, 2))
        self.use_ft_var = tk.BooleanVar(value=True)
        tk.Checkbutton(en_row, text="Activer cette source", variable=self.use_ft_var,
                       bg=self._CARD, fg=self._FG, activebackground=self._CARD,
                       selectcolor=self._CARD,
                       command=self._toggle_ft_fields).pack(side="left")

        c = self._field_row(lf, "Mots-clés", "ex: couvreur, BTP, développeur")
        self.ft_mots_var = tk.StringVar()
        e = ttk.Entry(c, textvariable=self.ft_mots_var, width=40)
        e.pack(side="left")
        self._ft_widgets.append(e)

        c = self._field_row(lf, "Secteur", "remplace les mots-clés si rempli")
        self.ft_secteur_var = tk.StringVar(value="(aucun)")
        ft_sect_cb = ttk.Combobox(c, textvariable=self.ft_secteur_var,
                                   values=SECTEUR_CHOICES, state="readonly", width=28)
        ft_sect_cb.pack(side="left", padx=(0, 8))
        self._ft_widgets.append(ft_sect_cb)
        self._ft_secteur_info = tk.Label(c, text="", bg=self._CARD, fg=self._FG_MUTED,
                                          font=self._font(9))
        self._ft_secteur_info.pack(side="left")
        ft_sect_cb.bind("<<ComboboxSelected>>", self._on_ft_secteur_change)

        c = self._field_row(lf, "Villes / Dépts", "Tours, 37, Paris…  ＋ pour ajouter")
        self._ft_locations = TagList(c, freetext=True)
        self._ft_locations.pack(side="left", fill="x", expand=True)

        c = self._field_row(lf, "Rayon", "km (pour les villes, pas les dépts)")
        self.ft_rayon_var = tk.StringVar()
        ray_e = ttk.Entry(c, textvariable=self.ft_rayon_var, width=6)
        ray_e.pack(side="left")
        self._ft_widgets.append(ray_e)

        c = self._field_row(lf, "Contrats", "Aucune sélection = tous")
        self.ft_contract_vars: dict[str, tk.BooleanVar] = {}
        for label in FT_CONTRAT_CHOICES:
            var = tk.BooleanVar(value=False)
            self.ft_contract_vars[label] = var
            cb = tk.Checkbutton(c, text=label, variable=var,
                                bg=self._CARD, fg=self._FG,
                                activebackground=self._CARD, selectcolor=self._CARD)
            cb.pack(side="left", padx=(0, 10))
            self._ft_widgets.append(cb)

        c = self._field_row(lf, "Publiée depuis")
        self.ft_jours_var = tk.StringVar(value="Toutes dates")
        j_cb = ttk.Combobox(c, textvariable=self.ft_jours_var,
                             values=list(FT_JOURS.keys()), state="readonly", width=18)
        j_cb.pack(side="left", padx=(0, 16))
        self._ft_widgets.append(j_cb)
        tk.Label(c, text="Expérience", bg=self._CARD, fg=self._FG).pack(side="left", padx=(0, 4))
        self.ft_exp_var = tk.StringVar(value="Indifférent")
        e_cb = ttk.Combobox(c, textvariable=self.ft_exp_var,
                             values=list(FT_EXPERIENCE.keys()), state="readonly", width=16)
        e_cb.pack(side="left", padx=(0, 16))
        self._ft_widgets.append(e_cb)
        tk.Label(c, text="Max offres", bg=self._CARD, fg=self._FG).pack(side="left", padx=(0, 4))
        self.ft_max_var = tk.StringVar(value="500")
        m_e = ttk.Entry(c, textvariable=self.ft_max_var, width=6)
        m_e.pack(side="left")
        self._ft_widgets.append(m_e)

        tk.Frame(lf, bg=self._CARD, height=6).pack()

    # ------------------------------------------------------------------
    # HelloWork section
    # ------------------------------------------------------------------

    def _build_hw_section(self):
        lf = self._card("🟠  HelloWork  (via Apify)")

        en_row = tk.Frame(lf, bg=self._CARD)
        en_row.pack(fill="x", padx=12, pady=(4, 2))
        self.use_hw_var = tk.BooleanVar(value=True)
        tk.Checkbutton(en_row, text="Activer cette source", variable=self.use_hw_var,
                       bg=self._CARD, fg=self._FG, activebackground=self._CARD,
                       selectcolor=self._CARD,
                       command=self._toggle_hw_fields).pack(side="left")

        c = self._field_row(lf, "Mots-clés", "ex: couvreur, plombier, BTP")
        self.hw_keywords_var = tk.StringVar()
        kw_e = ttk.Entry(c, textvariable=self.hw_keywords_var, width=40)
        kw_e.pack(side="left")
        self._hw_widgets.append(kw_e)

        c = self._field_row(lf, "Secteur", "envoie les métiers du secteur auto")
        self.hw_secteur_var = tk.StringVar(value="(aucun)")
        hw_sect_cb = ttk.Combobox(c, textvariable=self.hw_secteur_var,
                                   values=SECTEUR_CHOICES, state="readonly", width=28)
        hw_sect_cb.pack(side="left", padx=(0, 8))
        self._hw_widgets.append(hw_sect_cb)
        self._hw_secteur_info = tk.Label(c, text="", bg=self._CARD, fg=self._FG_MUTED,
                                          font=self._font(9))
        self._hw_secteur_info.pack(side="left")
        hw_sect_cb.bind("<<ComboboxSelected>>", self._on_hw_secteur_change)

        c = self._field_row(lf, "Lieu", "Tours, Lyon, Île-de-France…")
        self.hw_location_var = tk.StringVar()
        hw_loc_e = ttk.Entry(c, textvariable=self.hw_location_var, width=22)
        hw_loc_e.pack(side="left")
        self._hw_widgets.append(hw_loc_e)
        tk.Label(c, text="Rayon", bg=self._CARD, fg=self._FG,
                 font=self._font(10)).pack(side="left", padx=(12, 4))
        self.hw_rayon_var = tk.StringVar()
        hw_ray_e = ttk.Entry(c, textvariable=self.hw_rayon_var, width=5)
        hw_ray_e.pack(side="left")
        self._hw_widgets.append(hw_ray_e)
        tk.Label(c, text="km", bg=self._CARD, fg=self._FG_MUTED,
                 font=self._font(9)).pack(side="left", padx=(2, 0))

        c = self._field_row(lf, "Publiée depuis")
        self.hw_date_var = tk.StringVar(value="Toutes dates")
        hw_d_cb = ttk.Combobox(c, textvariable=self.hw_date_var,
                                values=list(HW_DATE_POSTED.keys()), state="readonly", width=16)
        hw_d_cb.pack(side="left")
        self._hw_widgets.append(hw_d_cb)

        c = self._field_row(lf, "Contrats", "Aucune sélection = tous")
        self.hw_contract_vars = {}
        for label, code in HW_CONTRATS:
            var = tk.BooleanVar(value=False)
            self.hw_contract_vars[code] = var
            cb = tk.Checkbutton(c, text=label, variable=var,
                                bg=self._CARD, fg=self._FG,
                                activebackground=self._CARD, selectcolor=self._CARD)
            cb.pack(side="left", padx=(0, 10))
            self._hw_widgets.append(cb)

        c = self._field_row(lf, "Max résultats", "max 5000 via Apify")
        self.hw_max_var = tk.StringVar(value="200")
        hw_m_e = ttk.Entry(c, textvariable=self.hw_max_var, width=7)
        hw_m_e.pack(side="left")
        self._hw_widgets.append(hw_m_e)

        c = self._field_row(lf, "Métiers/groupe", "si secteur : nb de métiers par run Apify")
        self.hw_group_size_var = tk.StringVar(value="5")
        hw_gs_e = ttk.Entry(c, textvariable=self.hw_group_size_var, width=4)
        hw_gs_e.pack(side="left")
        self._hw_widgets.append(hw_gs_e)
        tk.Label(c, text="(1 run Apify par groupe — plus précis, plus lent)",
                 bg=self._CARD, fg=self._FG_MUTED,
                 font=self._font(9)).pack(side="left", padx=(8, 0))

        tk.Frame(lf, bg=self._CARD, height=6).pack()

    # ------------------------------------------------------------------
    # Post-filters
    # ------------------------------------------------------------------

    def _build_post_section(self):
        lf = self._card("🔎  Filtres post-récupération  (optionnel)")

        c = self._field_row(lf, "Entreprise (regex)", "ex: Bouygues|Vinci")
        self.post_company_var = tk.StringVar()
        ttk.Entry(c, textvariable=self.post_company_var, width=30).pack(side="left")

        c = self._field_row(lf, "Secteur contient", "ex: BTP, Informatique")
        self.post_sector_var = tk.StringVar()
        ttk.Entry(c, textvariable=self.post_sector_var, width=30).pack(side="left")

        # Filtre cabinets de recrutement
        c = self._field_row(lf, "Cabinets externes", "évince les chasseurs de têtes")
        self.filter_recruiters_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            c, text="Exclure les cabinets de recrutement",
            variable=self.filter_recruiters_var,
            bg=self._CARD, fg=self._FG,
            activebackground=self._CARD, selectcolor=self._CARD,
        ).pack(side="left")

        # Seuil de sensibilité (caché par défaut, visible si cochée)
        self._recruiter_threshold_var = tk.StringVar(value="2")
        thresh_row = tk.Frame(c, bg=self._CARD)
        thresh_row.pack(side="left", padx=(16, 0))
        tk.Label(thresh_row, text="Sensibilité :", bg=self._CARD, fg=self._FG_MUTED,
                 font=self._font(9)).pack(side="left")
        ttk.Combobox(
            thresh_row, textvariable=self._recruiter_threshold_var,
            values=["1 — agressif", "2 — équilibré", "3 — conservateur"],
            state="readonly", width=18,
        ).pack(side="left", padx=(4, 0))

        tk.Frame(lf, bg=self._CARD, height=6).pack()

    # ------------------------------------------------------------------
    # Actions bar (inside search page)
    # ------------------------------------------------------------------

    def _build_actions(self):
        f = tk.Frame(self._search_scroll_frame, bg=self._BG)
        f.pack(fill="x", padx=12, pady=(10, 6))

        self._run_btn = ttk.Button(
            f, text="  🔍  Nouvelle recherche  ",
            style="Run.TButton", command=self._on_search,
        )
        self._run_btn.pack(side="left", padx=(0, 10))

        # Bouton annuler (caché par défaut)
        self._cancel_btn = tk.Button(
            f, text="  ✖  Annuler la recherche  ",
            bg="#FF3B30", fg="white",
            activebackground="#CC2D24", activeforeground="white",
            font=self._font(12, "bold"), relief="flat", cursor="hand2",
            padx=20, pady=8,
            command=self._on_cancel_search,
        )
        # Pas pack() ici — sera affiché dynamiquement

        self._add_btn = ttk.Button(
            f, text="  ➕  Ajouter aux résultats  ",
            command=self._on_search_add, state="disabled",
        )
        self._add_btn.pack(side="left", padx=(0, 10))

        self._download_btn = ttk.Button(
            f, text="  💾  Télécharger le CSV  ",
            command=self._on_download, state="disabled",
        )
        self._download_btn.pack(side="left", padx=(0, 10))

        self._open_btn = ttk.Button(
            f, text="📂  Ouvrir dernier export",
            command=self._open_last_csv, state="disabled",
        )
        self._open_btn.pack(side="left")

    # ------------------------------------------------------------------
    # Log / status (inside search page)
    # ------------------------------------------------------------------

    def _build_log(self):
        pf = tk.Frame(self._search_scroll_frame, bg=self._BG)
        pf.pack(fill="x", padx=12, pady=(0, 4))
        self._progress = ttk.Progressbar(pf, mode="indeterminate", style="TProgressbar")
        self._progress.pack(fill="x")

        log_card = tk.LabelFrame(
            self._search_scroll_frame, text="  Journal  ",
            bg=self._CARD, fg=self._FG,
            font=self._font(10, "bold"),
            relief="flat", bd=1,
            highlightbackground=self._BORDER, highlightthickness=1,
        )
        log_card.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        vsb = ttk.Scrollbar(log_card)
        vsb.pack(side="right", fill="y")
        self._log_text = tk.Text(
            log_card, height=9, state="disabled", wrap="word",
            yscrollcommand=vsb.set,
            font=("SF Mono", 9) if sys.platform == "darwin" else ("Consolas", 9),
            bg="#1E1E2E", fg="#CDD6F4",
            insertbackground="#CDD6F4",
            relief="flat", padx=8, pady=6,
            selectbackground="#313244",
        )
        self._log_text.pack(fill="both", expand=True)
        vsb.config(command=self._log_text.yview)

        self._status_var = tk.StringVar(
            value="Prêt  —  Configure tes identifiants et lance une recherche."
        )
        status = tk.Label(
            self._search_scroll_frame, textvariable=self._status_var,
            bg=self._BORDER, fg=self._FG, anchor="w", padx=12, pady=4,
            font=self._font(9),
        )
        status.pack(fill="x")

    # ------------------------------------------------------------------
    # Paramètres — fenêtre cache IDs
    # ------------------------------------------------------------------

    def _open_settings(self):
        win = tk.Toplevel(self)
        win.title("Paramètres — Cache des annonces")
        win.resizable(False, False)
        win.configure(bg=self._BG)
        win.transient(self)
        win.grab_set()
        win.geometry("480x280")

        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 480) // 2
        y = self.winfo_y() + (self.winfo_height() - 280) // 2
        win.geometry(f"+{x}+{y}")

        tk.Frame(win, bg=self._ACCENT, height=4).pack(fill="x")
        body = tk.Frame(win, bg=self._BG, padx=24, pady=16)
        body.pack(fill="both", expand=True)

        tk.Label(body, text="Cache des annonces vues",
                 bg=self._BG, fg=self._FG,
                 font=self._font(13, "bold")).pack(anchor="w")
        tk.Label(body,
                 text="Chaque annonce exportée est mémorisée par son ID.\n"
                      "Les prochaines recherches n'exporteront jamais deux fois la même annonce.",
                 bg=self._BG, fg=self._FG_MUTED, justify="left",
                 wraplength=420, font=self._font(10)).pack(anchor="w", pady=(6, 14))

        stats_var = tk.StringVar()

        def refresh_stats():
            s = get_stats()
            last = s["last_cleared"] or "jamais"
            stats_var.set(
                f"IDs mémorisés : {s['count']:,}    •    "
                f"Taille fichier : {s['size_kb']} Ko    •    "
                f"Dernier vidage : {last}"
            )

        refresh_stats()
        tk.Label(body, textvariable=stats_var, bg=self._CARD, fg=self._FG,
                 relief="flat", padx=12, pady=8,
                 font=("SF Mono", 9) if sys.platform == "darwin" else ("Consolas", 9),
                 ).pack(fill="x", pady=(0, 16))

        btn_row = tk.Frame(body, bg=self._BG)
        btn_row.pack(fill="x")

        def do_clear():
            if messagebox.askyesno(
                "Vider le cache",
                "Supprimer tous les IDs mémorisés ?\n\n"
                "Les prochaines recherches pourront à nouveau exporter des annonces déjà vues.",
                parent=win,
            ):
                clear_cache()
                refresh_stats()
                messagebox.showinfo("Cache vidé", "Le cache a été vidé.", parent=win)

        ttk.Button(btn_row, text="🗑  Vider le cache",
                   command=do_clear).pack(side="left", padx=(0, 10))

        def open_cache_file():
            s = get_stats()
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", s["path"]], check=False)
            elif sys.platform.startswith("win"):
                subprocess.run(["explorer", "/select,", s["path"]], check=False)
            else:
                subprocess.run(["xdg-open", os.path.dirname(s["path"])], check=False)

        ttk.Button(btn_row, text="📁  Voir le fichier",
                   command=open_cache_file).pack(side="left")
        ttk.Button(btn_row, text="Fermer",
                   command=win.destroy).pack(side="right")

    # ------------------------------------------------------------------
    # Secteur callbacks
    # ------------------------------------------------------------------

    def _on_ft_secteur_change(self, event=None):
        s = self.ft_secteur_var.get()
        if s == "(aucun)" or s not in SECTEURS:
            self._ft_secteur_info.configure(text="")
        else:
            n = len(SECTEURS[s]["naf"])
            self._ft_secteur_info.configure(
                text=f"{n} codes NAF · filtre secteur API"
            )

    def _on_hw_secteur_change(self, event=None):
        s = self.hw_secteur_var.get()
        if s == "(aucun)" or s not in SECTEURS:
            self._hw_secteur_info.configure(text="")
        else:
            n = len(SECTEURS[s]["keywords"])
            self._hw_secteur_info.configure(
                text=f"{n} métiers envoyés en recherche"
            )

    # ------------------------------------------------------------------
    # Toggle helpers
    # ------------------------------------------------------------------

    def _toggle_ft_fields(self):
        state = "normal" if self.use_ft_var.get() else "disabled"
        for w in self._ft_widgets:
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        self._ft_locations.configure_state(state)

    def _toggle_hw_fields(self):
        state = "normal" if self.use_hw_var.get() else "disabled"
        for w in self._hw_widgets:
            try:
                w.configure(state=state)
            except tk.TclError:
                pass

    def _toggle_ft_secret(self):
        self._ft_secret_visible = not self._ft_secret_visible
        self._ft_secret_entry.configure(show="" if self._ft_secret_visible else "•")

    def _toggle_hw_secret(self):
        self._hw_secret_visible = not self._hw_secret_visible
        self._hw_secret_entry.configure(show="" if self._hw_secret_visible else "•")

    # ------------------------------------------------------------------
    # Env persistence
    # ------------------------------------------------------------------

    def _save_env(self, key: str, value: str):
        if not self.save_creds_var.get() or not HAS_DOTENV:
            return
        try:
            if not os.path.exists(ENV_PATH):
                open(ENV_PATH, "a").close()
            set_key(ENV_PATH, key, value)
        except OSError as e:
            self._log_msg(f"(Avertissement : impossible d'enregistrer {key} : {e})")

    # ------------------------------------------------------------------
    # Logging / queue polling
    # ------------------------------------------------------------------

    def _log_msg(self, msg: str):
        self._queue.put(("log", msg))

    # Keep _log as alias for worker thread usage
    def _log(self, msg: str):
        self._queue.put(("log", msg))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._log_text.configure(state="normal")
                    self._log_text.insert("end", payload + "\n")
                    self._log_text.see("end")
                    self._log_text.configure(state="disabled")
                elif kind == "done":
                    self._progress.stop()
                    self._set_ui_enabled(True)
                    if payload:
                        self.last_output_path = payload
                        self._open_btn.configure(state="normal")
                        self._status_var.set(f"✅  Export terminé  —  {payload}")
                    else:
                        self._status_var.set("Aucune offre trouvée.")
                elif kind == "search_done":
                    self._progress.stop()
                    self._set_ui_enabled(True)
                    if payload:
                        new_rows, ignored_ids = payload
                        if self._search_mode == "add":
                            # Accumulation — déduplique contre les rows déjà en mémoire
                            existing_keys = {get_row_id(r) for r in self._pending_rows}
                            added = [r for r in new_rows if get_row_id(r) not in existing_keys]
                            self._pending_rows.extend(added)
                            self._ignored_ids.update(ignored_ids)
                            n_added = len(added)
                            n_total = len(self._pending_rows)
                            self._status_var.set(
                                f"➕  {n_added} offre(s) ajoutée(s)"
                                + (f"  •  {len(new_rows)-n_added} doublon(s) interne(s)" if n_added < len(new_rows) else "")
                                + f"  →  {n_total} au total"
                            )
                            self._log(f"\n{'─'*48}")
                            self._log(f"  Ajout : {n_added} nouvelles  •  Total accumulé : {n_total}")
                            self._log(f"{'─'*48}")
                        else:
                            # Reset normal
                            self._pending_rows = new_rows
                            self._ignored_ids  = ignored_ids
                            self._status_var.set(
                                f"✅  {len(new_rows)} offre(s) nouvelle(s)"
                                + (f"  •  {len(ignored_ids)} ignorée(s)" if ignored_ids else "")
                                + "  —  Voir résultats ou télécharger directement"
                            )
                        self._download_btn.configure(state="normal")
                        self._add_btn.configure(state="normal")
                        self._refresh_results_page()
                        self._show_results_page()
                    else:
                        if self._search_mode == "add":
                            self._status_var.set(
                                f"Aucune nouvelle offre trouvée — "
                                f"{len(self._pending_rows)} offre(s) toujours disponibles."
                            )
                            # On garde les résultats existants
                        else:
                            self._pending_rows = []
                            self._ignored_ids  = set()
                            self._download_btn.configure(state="disabled")
                            self._add_btn.configure(state="disabled")
                            self._update_results_count(0)
                            self._status_var.set("Aucune nouvelle offre trouvée.")
                elif kind == "ft_ok":
                    self._ft_status_lbl.configure(text="✅ Connecté", fg="green")
                elif kind == "hw_ok":
                    self._hw_status_lbl.configure(text="✅ Connecté", fg="green")
                elif kind == "cancelled":
                    self._progress.stop()
                    self._set_ui_enabled(True)
                    self._status_var.set("🛑  Recherche annulée — aucun résultat partiel.")
                    self._log_msg("🛑  Recherche annulée.")
                elif kind == "search_done_partial":
                    # Annulation avec résultats partiels récupérés
                    self._progress.stop()
                    self._set_ui_enabled(True)
                    partial_rows = payload
                    if partial_rows:
                        new_rows, dupes = filter_new(partial_rows)
                        ignored_ids = set()
                        if dupes:
                            seen = load_seen_ids()
                            ignored_ids = {get_row_id(r) for r in partial_rows if get_row_id(r) in seen}
                        if self._search_mode == "add":
                            existing_keys = {get_row_id(r) for r in self._pending_rows}
                            added = [r for r in new_rows if get_row_id(r) not in existing_keys]
                            self._pending_rows.extend(added)
                            self._ignored_ids.update(ignored_ids)
                        else:
                            self._pending_rows = new_rows
                            self._ignored_ids = ignored_ids
                        n = len(new_rows)
                        self._status_var.set(
                            f"🛑  Annulée — {n} résultat(s) partiel(s) récupéré(s)"
                            + (f"  •  {dupes} doublon(s) ignoré(s)" if dupes else "")
                        )
                        self._download_btn.configure(state="normal")
                        self._add_btn.configure(state="normal")
                        self._refresh_results_page()
                        self._show_results_page()
                    else:
                        self._status_var.set("🛑  Annulée — aucun résultat partiel.")
                elif kind == "error":
                    self._progress.stop()
                    self._set_ui_enabled(True)
                    self._status_var.set("❌  Erreur — voir ci-dessous")
                    # Remet les labels de statut credentials à l'état initial
                    if hasattr(self, "_ft_status_lbl"):
                        current = self._ft_status_lbl.cget("text")
                        if current == "Test…":
                            self._ft_status_lbl.configure(text="", fg=self._FG_MUTED)
                    if hasattr(self, "_hw_status_lbl"):
                        current = self._hw_status_lbl.cget("text")
                        if current == "Test…":
                            self._hw_status_lbl.configure(text="", fg=self._FG_MUTED)
                    messagebox.showerror("Erreur", payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _set_ui_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self._run_btn.configure(state=state)
        self._test_ft_btn.configure(state=state)
        self._test_hw_btn.configure(state=state)
        # _add_btn actif seulement si on a déjà des résultats et que l'UI est active
        if enabled and self._pending_rows:
            self._add_btn.configure(state="normal")
        else:
            self._add_btn.configure(state="disabled")

        # Swap annuler → recherche quand l'UI redevient active
        if enabled:
            self._cancel_btn.pack_forget()
            self._run_btn.pack(side="left", padx=(0, 10))

    # ------------------------------------------------------------------
    # Test buttons
    # ------------------------------------------------------------------

    def _test_ft(self):
        cid = self.client_id_var.get().strip()
        secret = self.client_secret_var.get().strip()
        if not cid or not secret:
            messagebox.showwarning(
                "Identifiants", "Renseigne Client ID et Client Secret France Travail.")
            return
        self._save_env("FRANCE_TRAVAIL_CLIENT_ID", cid)
        self._save_env("FRANCE_TRAVAIL_CLIENT_SECRET", secret)
        self._test_ft_btn.configure(state="disabled")
        self._ft_status_lbl.configure(text="Test…", foreground="gray")

        def worker():
            try:
                get_access_token(cid, secret)
                self._queue.put(("ft_ok", None))
            except FranceTravailError as e:
                self._queue.put(("error", str(e)))
            finally:
                self.after(0, lambda: self._test_ft_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def _test_apify(self):
        token = self.apify_token_var.get().strip()
        if not token:
            messagebox.showwarning("Identifiants", "Renseigne ton token Apify.")
            return
        self._save_env("APIFY_TOKEN", token)
        self._test_hw_btn.configure(state="disabled")
        self._hw_status_lbl.configure(text="Test…", foreground="gray")

        def worker():
            try:
                verify_apify_token(token)
                self._queue.put(("hw_ok", None))
            except HelloWorkError as e:
                self._queue.put(("error", str(e)))
            finally:
                self.after(0, lambda: self._test_hw_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # France Travail params builder
    # ------------------------------------------------------------------

    def _resolve_location(self, loc: str) -> dict:
        p: dict = {}
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
        if "commune" in p and self.ft_rayon_var.get().strip():
            p["distance"] = self.ft_rayon_var.get().strip()
        return p

    def _build_ft_params_list(self) -> list[dict]:
        base: dict = {}
        if self.ft_mots_var.get().strip():
            base["motsCles"] = self.ft_mots_var.get().strip()
        exp = FT_EXPERIENCE.get(self.ft_exp_var.get(), "")
        if exp:
            base["experience"] = exp
        jours = FT_JOURS.get(self.ft_jours_var.get(), "")
        if jours:
            base["publieeDepuis"] = jours

        # Contrats
        selected_labels = [lbl for lbl, var in self.ft_contract_vars.items() if var.get()]
        if selected_labels:
            codes: list[str] = []
            seen_codes: set[str] = set()
            for lbl in selected_labels:
                for code in FT_CONTRAT_CODES.get(lbl, lbl).split(","):
                    code = code.strip()
                    if code and code not in seen_codes:
                        seen_codes.add(code)
                        codes.append(code)
            base["typeContrat"] = ",".join(codes)

        # Secteur prédéfini → on utilise les mots-clés métiers (comme HW)
        # secteurActivite est abandonné car l'API exige des codes NAF précis
        # et accepte seulement 2 codes max, ce qui est trop limitant.
        ft_secteur = self.ft_secteur_var.get()
        kw_groups: list[str] = []  # chaque élément = un mot-clé pour motsCles
        if ft_secteur != "(aucun)" and ft_secteur in SECTEURS:
            sector_kw = SECTEURS[ft_secteur]["keywords"]
            kw_groups = sector_kw
            base.pop("motsCles", None)  # le secteur remplace les mots-clés manuels

        # Localisations
        locs = self._ft_locations.get_tags()
        loc_params = [self._resolve_location(l) for l in locs] if locs else [{}]

        result = []
        seen: set[str] = set()

        if kw_groups:
            # Une requête par mot-clé × localisation
            for kw in kw_groups:
                for lp in loc_params:
                    p = {**base, "motsCles": kw, **lp}
                    key = str(sorted(p.items()))
                    if key not in seen:
                        seen.add(key)
                        result.append(p)
        else:
            for lp in loc_params:
                p = {**base, **lp}
                key = str(sorted(p.items()))
                if key not in seen:
                    seen.add(key)
                    result.append(p)

        return result

    def _build_ft_params(self) -> dict:
        params_list = self._build_ft_params_list()
        return params_list[0] if params_list else {}

    def _apply_recruiter_filter(self, rows: list[dict], post: dict) -> list[dict]:
        """Applique le filtre cabinets de recrutement si activé."""
        if not post.get("exclude_recruiters"):
            return rows
        threshold = post.get("recruiter_threshold", 2)
        direct, evicted = filter_out_recruiters(rows, threshold=threshold, log=self._log)
        if evicted:
            self._log(f"  🚫 {len(evicted)} cabinet(s) évincé(s) sur {len(rows)} offres")
        return direct

    def _get_post_filters(self) -> dict:
        # Extrait le chiffre du seuil (ex: "2 — équilibré" → 2)
        thresh_raw = self._recruiter_threshold_var.get().split(" ")[0]
        try:
            thresh = int(thresh_raw)
        except ValueError:
            thresh = 2
        return {
            "company_pattern": self.post_company_var.get().strip(),
            "sector_contains": self.post_sector_var.get().strip(),
            "skills_contains": "",
            "experience_contains": "",
            "education_contains": "",
            "company_size_min": None,
            "company_size_max": None,
            "salary_max": None,
            "exclude_recruiters": self.filter_recruiters_var.get(),
            "recruiter_threshold": thresh,
        }

    # ------------------------------------------------------------------
    # Search trigger
    # ------------------------------------------------------------------

    def _on_search(self):
        """Nouvelle recherche — remet les résultats à zéro."""
        self._search_mode = "reset"
        self._launch_search()

    def _on_search_add(self):
        """Ajoute les résultats aux offres déjà trouvées sans les effacer."""
        self._search_mode = "add"
        self._launch_search(clear_log=False)

    def _launch_search(self, clear_log: bool = True):
        use_ft = self.use_ft_var.get()
        use_hw = self.use_hw_var.get()
        if not use_ft and not use_hw:
            messagebox.showwarning("Sources", "Active au moins une source.")
            return

        cid    = self.client_id_var.get().strip()
        secret = self.client_secret_var.get().strip()
        apify  = self.apify_token_var.get().strip()

        # Si une source est activée mais vide de critères, on la désactive silencieusement
        ft_has_criteria = bool(
            self.ft_mots_var.get().strip()
            or self._ft_locations.get_tags()
            or self.ft_secteur_var.get() != "(aucun)"
        )
        hw_has_criteria = bool(
            self.hw_keywords_var.get().strip()
            or self.hw_location_var.get().strip()
            or self.hw_secteur_var.get() != "(aucun)"
        )

        if use_ft and not ft_has_criteria:
            use_ft = False
        if use_hw and not hw_has_criteria:
            use_hw = False

        if not use_ft and not use_hw:
            messagebox.showwarning(
                "Critères manquants",
                "Les deux sources sont activées mais aucun critère n'a été rempli.\n\n"
                "Renseigne au moins un mot-clé ou un lieu pour chaque source que tu veux utiliser.",
            )
            return

        if use_ft and (not cid or not secret):
            messagebox.showwarning(
                "Identifiants", "France Travail : Client ID et Secret requis.")
            return
        if use_hw and not apify:
            messagebox.showwarning("Identifiants", "HelloWork : token Apify requis.")
            return

        try:
            ft_max = int(self.ft_max_var.get().strip() or "500")
            hw_max = int(self.hw_max_var.get().strip() or "200")
        except ValueError:
            messagebox.showwarning("Valeur invalide", "Les limites doivent être des entiers.")
            return

        if self.save_creds_var.get():
            if cid:    self._save_env("FRANCE_TRAVAIL_CLIENT_ID", cid)
            if secret: self._save_env("FRANCE_TRAVAIL_CLIENT_SECRET", secret)
            if apify:  self._save_env("APIFY_TOKEN", apify)

        if self._search_mode == "reset":
            self._pending_rows = []
            self._ignored_ids  = set()
            self._download_btn.configure(state="disabled")
            self._add_btn.configure(state="disabled")
            self._update_results_count(0)

        self._cancel_search = False  # reset du flag
        self._set_ui_enabled(False)
        # Swap bouton recherche → annuler
        self._run_btn.pack_forget()
        self._cancel_btn.pack(side="left", padx=(0, 10))
        self._progress.start(12)
        self._status_var.set(
            "Recherche en cours…" if self._search_mode == "reset"
            else f"Ajout en cours… ({len(self._pending_rows)} offres existantes)"
        )

        if clear_log:
            self._log_text.configure(state="normal")
            self._log_text.delete("1.0", "end")
            self._log_text.configure(state="disabled")
        else:
            self._log(f"\n{'━'*48}")
            self._log(f"  ➕  Nouvelle recherche à ajouter aux {len(self._pending_rows)} résultats existants")
            self._log(f"{'━'*48}")

        # Mémorise quelles sources étaient cochées par l'utilisateur (avant le filtre critères)
        ft_checked = self.use_ft_var.get()
        hw_checked = self.use_hw_var.get()

        threading.Thread(
            target=self._worker_search,
            args=(use_ft, use_hw, cid, secret, apify, ft_max, hw_max,
                  ft_checked, hw_checked),
            daemon=True,
        ).start()

    def _on_download(self):
        """Quick download from search page without going to results page."""
        if not self._pending_rows:
            messagebox.showinfo("Aucun résultat", "Lance d'abord une recherche.")
            return
        self._do_download(self._pending_rows,
                          label=f"{len(self._pending_rows)} offres")

    def _on_cancel_search(self):
        """Annule la recherche en cours."""
        self._cancel_search = True
        self._log_msg("🛑  Annulation demandée — arrêt de la recherche en cours...")
        self._status_var.set("⚠️  Annulation en cours...")

    def _open_last_csv(self):
        if not self.last_output_path or not os.path.exists(self.last_output_path):
            messagebox.showinfo("Aucun fichier", "Aucun export disponible.")
            return
        if sys.platform == "darwin":
            subprocess.run(["open", self.last_output_path], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(self.last_output_path)  # noqa: S606
        else:
            subprocess.run(["xdg-open", self.last_output_path], check=False)

    # ------------------------------------------------------------------
    # Worker — search only, no export
    # ------------------------------------------------------------------

    def _worker_search(self, use_ft, use_hw, cid, secret, apify, ft_max, hw_max,
                       ft_checked=True, hw_checked=True):
        try:
            # Check annulation dès le début
            if self._cancel_search:
                self._queue.put(("log", "🛑  Recherche annulée avant démarrage."))
                self._queue.put(("search_done", None))
                return

            all_rows: list[dict] = []
            post = self._get_post_filters()

            # Informe si une source cochée a été ignorée faute de critères
            if ft_checked and not use_ft:
                self._log(
                    "ℹ️  France Travail activée mais aucun critère rempli — source ignorée."
                )
            if hw_checked and not use_hw:
                self._log(
                    "ℹ️  HelloWork activée mais aucun critère rempli — source ignorée."
                )

            if use_ft:
                self._log("=== France Travail ===")
                token = get_access_token(cid, secret)
                params_list = self._build_ft_params_list()
                if len(params_list) == 1 and not params_list[0]:
                    self._log(
                        "⚠️  Aucun filtre — toutes les offres disponibles seront "
                        "retournées (limité à max offres)"
                    )
                self._log(f"{len(params_list)} combinaison(s) de recherche")

                seen_ids: set = set()
                raw_all = []
                for i, params in enumerate(params_list, 1):
                    self._log(f"  [{i}/{len(params_list)}] {params or '(aucun filtre)'}")
                    raw = fetch_offers(token, params, ft_max, log=self._log)
                    for o in raw:
                        # Bug #18 fix : utilise get_row_id pour les offres sans ID
                        oid = get_row_id(ftl_flatten_for_dedup(o))
                        if oid not in seen_ids:
                            seen_ids.add(oid)
                            raw_all.append(o)
                    self._log(f"  → {len(raw_all)} offres uniques cumulées")

                ft_company = post.get("company_pattern", "")
                if ft_company:
                    before = len(raw_all)
                    raw_all = [o for o in raw_all if matches_company_filter(o, ft_company)]
                    self._log(f"Filtre entreprise : {before} → {len(raw_all)}")
                rows = [flatten_offer_unified(o) for o in raw_all]
                # Extrait les clés connues de apply_post_filters
                post_kw = {k: v for k, v in post.items()
                           if k not in ("exclude_recruiters", "recruiter_threshold")}
                rows = apply_post_filters(rows, **post_kw)
                rows = self._apply_recruiter_filter(rows, post)
                self._log(f"France Travail : {len(rows)} offres après filtres\n")
                all_rows.extend(rows)

            # Check annulation entre les deux sources — on garde les résultats FT déjà trouvés
            if self._cancel_search:
                self._log("🛑  Recherche annulée — récupération des résultats partiels...")
                # On continue avec all_rows déjà rempli par FT si applicable

            if use_hw:
                self._log("=== HelloWork (Apify) ===")
                kw_raw = self.hw_keywords_var.get().strip()
                keywords = [k.strip() for k in kw_raw.replace(",", "\n").split("\n")
                            if k.strip()]

                # Secteur prédéfini → remplace ou complète les mots-clés manuels
                hw_secteur = self.hw_secteur_var.get()
                sector_mode = hw_secteur != "(aucun)" and hw_secteur in SECTEURS
                if sector_mode:
                    sector_kw = SECTEURS[hw_secteur]["keywords"]
                    if keywords:
                        keywords = keywords + [k for k in sector_kw if k not in keywords]
                    else:
                        keywords = list(sector_kw)
                    self._log(
                        f"Secteur «{hw_secteur}» → {len(keywords)} métiers au total"
                    )

                if not keywords and not self.hw_location_var.get().strip():
                    raise HelloWorkError(
                        "HelloWork : renseigne au moins un mot-clé ou un lieu.")

                contract_types = [code for code, var in self.hw_contract_vars.items()
                                  if var.get()]
                date_posted = HW_DATE_POSTED.get(self.hw_date_var.get(), "any")
                location = self.hw_location_var.get().strip()
                try:
                    radius_km = int(self.hw_rayon_var.get().strip()) if self.hw_rayon_var.get().strip() else None
                except ValueError:
                    radius_km = None
                try:
                    group_size = max(1, int(self.hw_group_size_var.get().strip() or "5"))
                except ValueError:
                    group_size = 5

                rayon_log = f" +{radius_km}km" if radius_km else ""
                self._log(f"Lieu : {location or '(tous)'}{rayon_log} | Depuis : {self.hw_date_var.get()}")

                # Avertissement si secteur sans lieu
                if sector_mode and not location:
                    self._log(
                        "⚠️  Aucun lieu renseigné avec un secteur — "
                        "la recherche couvre toute la France."
                    )

                # ── Découpage en groupes ─────────────────────────────
                if sector_mode and len(keywords) > group_size:
                    groups = [keywords[i:i+group_size]
                              for i in range(0, len(keywords), group_size)]
                    self._log(
                        f"{len(keywords)} métiers → {len(groups)} groupes de {group_size} "
                        f"→ {len(groups)} runs Apify"
                    )
                else:
                    groups = [keywords] if keywords else [[]]

                # Max par groupe : répartition équitable, plafonnée à hw_max au total
                hw_rows_all: list[dict] = []
                seen_hw_ids: set = set()
                max_per_group = max(1, hw_max // max(len(groups), 1))

                for gi, grp in enumerate(groups, 1):
                    # Stop dès que le total global est atteint
                    if len(hw_rows_all) >= hw_max:
                        self._log(f"  ✓ Limite de {hw_max} résultats atteinte — groupes restants ignorés.")
                        break
                    if self._cancel_search:
                        self._log(f"🛑  Annulation après {gi-1}/{len(groups)} groupes — résultats partiels conservés.")
                        break
                    remaining = hw_max - len(hw_rows_all)
                    cap = min(max_per_group, remaining)
                    self._log(f"  Groupe [{gi}/{len(groups)}] : {grp}  (max {cap})")
                    grp_rows = fetch_hellowork_offers(
                        apify,
                        search_queries=grp,
                        location=location,
                        radius_km=radius_km,
                        max_results=cap,
                        contract_types=contract_types or None,
                        date_posted=date_posted,
                        log=self._log,
                        check_cancel=lambda: self._cancel_search,
                    )
                    # Déduplication inter-groupes par get_row_id
                    added = 0
                    for r in grp_rows:
                        rid = get_row_id(r)
                        if rid not in seen_hw_ids:
                            seen_hw_ids.add(rid)
                            hw_rows_all.append(r)
                            added += 1
                    self._log(f"  → {added} nouvelles offres (total cumulé : {len(hw_rows_all)})")

                hw_rows = hw_rows_all
                post_kw = {k: v for k, v in post.items()
                           if k not in ("exclude_recruiters", "recruiter_threshold")}
                hw_rows = apply_post_filters(hw_rows, **post_kw)
                hw_rows = self._apply_recruiter_filter(hw_rows, post)
                self._log(f"HelloWork : {len(hw_rows)} offres après filtres\n")
                all_rows.extend(hw_rows)

            if not all_rows:
                if self._cancel_search:
                    self._log("🛑  Recherche annulée — aucun résultat partiel disponible.")
                else:
                    self._log("Aucune offre trouvée avec ces critères.")
                self._queue.put(("search_done", None))
                return

            if self._cancel_search:
                self._log(f"🛑  Recherche annulée — {len(all_rows)} offre(s) partielle(s) récupérées.")
                # Court-circuit : on envoie directement les résultats partiels
                # sans passer par la déduplication cache (faite dans _poll_queue)
                self._queue.put(("search_done_partial", all_rows))
                return

            # Deduplication against seen-IDs cache (read-only)
            new_rows, dupes = filter_new(all_rows)
            # Bug #2 fix : utilise get_row_id (fingerprint inclus) pour les ignored_ids
            ignored_ids = set()
            if dupes:
                seen = load_seen_ids()
                ignored_ids = {
                    get_row_id(r) for r in all_rows
                    if get_row_id(r) in seen
                }

            self._log("─" * 48)
            self._log(f"  Total trouvé    : {len(all_rows)}")
            if dupes:
                self._log(
                    f"  Déjà exportés   : {dupes}  (ignorés — déjà dans le cache)")
            self._log(f"  Nouveaux        : {len(new_rows)}")
            self._log("─" * 48)

            if not new_rows:
                self._log(
                    "\n✅ Toutes ces annonces ont déjà été exportées. Rien de nouveau.")
                self._queue.put(("search_done", None))
                return

            self._log(
                f"\n👉  {len(new_rows)} annonce(s) nouvelle(s) — "
                "Navigue sur Résultats pour prévisualiser."
            )
            self._queue.put(("search_done", (new_rows, ignored_ids)))

        except (FranceTravailError, HelloWorkError) as e:
            if self._cancel_search and all_rows:
                # Annulation avec résultats partiels — on les envoie quand même
                self._log(f"🛑  Annulation — envoi de {len(all_rows)} résultat(s) partiel(s).")
                self._queue.put(("search_done_partial", all_rows))
            elif self._cancel_search:
                self._queue.put(("cancelled", None))
            else:
                self._queue.put(("error", str(e)))
        except Exception as e:
            if self._cancel_search and all_rows:
                self._log(f"🛑  Annulation — envoi de {len(all_rows)} résultat(s) partiel(s).")
                self._queue.put(("search_done_partial", all_rows))
            elif self._cancel_search:
                self._queue.put(("cancelled", None))
            else:
                self._queue.put(("error", f"Erreur inattendue : {e}"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = App()
    app.mainloop()
