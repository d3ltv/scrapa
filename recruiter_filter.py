"""
recruiter_filter.py
====================
Détecte et filtre les annonces postées par des cabinets de recrutement externes.

Les cabinets se reconnaissent à plusieurs signaux :
1. Nom de l'entreprise contient des mots-clés typiques
2. Description contient des formulations de chasseur de tête
3. L'URL pointe vers un site de cabinet (optionnel)

Le filtre est configurable : blacklist extensible, seuil de confiance, mode log.
"""

import re

# ---------------------------------------------------------------------------
# Patterns — noms d'entreprises typiques de cabinets
# ---------------------------------------------------------------------------

# Mots/expressions dans le nom de l'entreprise → fort indicateur cabinet
COMPANY_NAME_PATTERNS = [
    # Générique
    r"\bcabinet\b",
    r"\brecrutement\b",
    r"\brecruting\b",
    r"\brecruitment\b",
    r"\bconsulting\b",
    r"\bconsultant[s]?\b",
    r"\bchasse\s+de\s+têtes?\b",
    r"\bchasseur[s]?\s+de\s+têtes?\b",
    r"\bhead\s*hunting\b",
    r"\bheadhunter[s]?\b",
    r"\bexecutive\s+search\b",
    r"\btalent[s]?\s+acquisition\b",
    r"\btalent[s]?\s+management\b",
    r"\bstaffing\b",
    r"\boutplacement\b",
    r"\bplacement\b",
    r"\bsourcing\b",
    r"\brh\s+externalis",
    r"\bressources\s+humaines\b",
    r"\bgestion\s+des?\s+talents?\b",
    r"\bpartenaire[s]?\s+rh\b",
    r"\bconseil\s+rh\b",
    r"\bsolutions?\s+rh\b",

    # Grands cabinets français / internationaux (noms propres)
    r"\brandstad\b",
    r"\bmanpower\b",
    r"\bradecco\b",
    r"\badecco\b",
    r"\bakkodis\b",
    r"\bgigroup\b",
    r"\bgi\s+group\b",
    r"\bproman\b",
    r"\bsynergie\b",
    r"\bcrit\b",
    r"\bbis\b",
    r"\bvedior\b",
    r"\bkelley\b",
    r"\brendstaf\b",
    r"\blhh\b",
    r"\blee\s+hecht\b",
    r"\bheidrick\b",
    r"\bkorn\s*ferry\b",
    r"\bspencer\s+stuart\b",
    r"\segon\s+zehnder\b",
    r"\bpage\s*(group|personnel|executive)\b",
    r"\bmichael\s+page\b",
    r"\bhays\b",
    r"\breed\b",
    r"\broberthalf\b",
    r"\brobert\s+half\b",
    r"\btalentup\b",
    r"\bcleamind\b",
    r"\btalentia\b",
    r"\baltedia\b",
    r"\bbpi\s+group\b",
    r"\boracle\s+search\b",
    r"\boptimum\s+recherche\b",
    r"\bg2r\b",
    r"\bhrm\b",
    r"\bactua\b",
    r"\bflexi\b",
    r"\bineo\b",
    r"\binterim[e]?\b",
    r"\bintérim[e]?\b",
    r"\btravail\s+temporaire\b",
    r"\bagence\s+d.emploi\b",
    r"\bagence\s+de\s+(recrutement|travail|placement)\b",
]

# Mots dans la DESCRIPTION → indicateurs cabinet
DESCRIPTION_PATTERNS = [
    r"notre\s+client\b",                    # "Notre client, leader dans..."
    r"notre\s+cabinet\b",
    r"pour\s+le\s+compte\s+de\b",           # "pour le compte de notre client"
    r"pour\s+notre\s+client\b",
    r"pour\s+un\s+de\s+nos\s+clients?\b",
    r"au\s+nom\s+de\b",
    r"nous\s+recrutons\s+pour\b",
    r"notre\s+partenaire\b",
    r"notre\s+mandant\b",
    r"confié\s+par\b",
    r"miss?ion\s+confi[ée]e\b",
    r"cabinet\s+de\s+recrutement\b",
    r"cabinet\s+conseil\b",
    r"chasseur[s]?\s+de\s+têtes?\b",
    r"recruteur[s]?\s+indépendant[s]?\b",
    r"prestataire\s+rh\b",
    r"consultant[s]?\s+rh\b",
    r"votre\s+consultant[e]?\b",
    r"notre\s+consultant[e]?\b",
    r"référence\s+(du\s+poste|offre)\s*:",    # champ de suivi cabinet
    r"réf\s*\.?\s*\w+[-–]\w+[-–]\w+",        # "Réf. AB-2024-001" (3 segments)
    r"\bposte\s+(?:réf|ref)\.?\s*:",          # "Poste réf. : 123"
    r"\boffre\s+n[°o]",                       # "Offre n°2026-042"
    r"\bnotre\s+équipe\s+de\s+consultant",    # "notre équipe de consultants RH"
]

# URL de sites connus de cabinets
RECRUITER_URL_DOMAINS = {
    "manpower.fr", "adecco.fr", "randstad.fr", "hays.fr",
    "michaelpage.fr", "pagepersonnel.fr", "robertwalters.fr",
    "roberthalf.fr", "cornerferry.com", "lhh.com",
    "synergie.fr", "proman.com", "gi-group.fr", "crit.fr",
    "talent.io", "welcometothejungle.com", "erecruit.fr",
}

# ---------------------------------------------------------------------------
# Moteur de détection
# ---------------------------------------------------------------------------

_COMPANY_RE = [re.compile(p, re.IGNORECASE) for p in COMPANY_NAME_PATTERNS]
_DESC_RE     = [re.compile(p, re.IGNORECASE) for p in DESCRIPTION_PATTERNS]


def _score_row(row: dict) -> tuple[int, list[str]]:
    """
    Calcule un score de suspicion cabinet + la liste des signaux détectés.
    Score : 0 = aucun signal, ≥1 = cabinet probable, ≥3 = cabinet certain.
    """
    reasons: list[str] = []
    score = 0

    company = str(row.get("entreprise") or "")
    description = str(row.get("description") or "")
    url = str(row.get("url") or row.get("entreprise_url") or "")

    # Signal fort : nom de l'entreprise
    for pat in _COMPANY_RE:
        if pat.search(company):
            reasons.append(f"nom: «{pat.pattern}»")
            score += 2
            break  # un seul match nom suffit

    # Signal modéré : description
    desc_hits = 0
    for pat in _DESC_RE:
        if pat.search(description):
            reasons.append(f"desc: «{pat.pattern}»")
            score += 1
            desc_hits += 1
            if desc_hits >= 2:  # cap à 2 signaux description
                break

    # Signal léger : domaine URL
    for domain in RECRUITER_URL_DOMAINS:
        if domain in url.lower():
            reasons.append(f"url: {domain}")
            score += 1
            break

    return score, reasons


def is_recruiter(row: dict, threshold: int = 2) -> bool:
    """
    Retourne True si l'annonce semble provenir d'un cabinet de recrutement.
    threshold=1 → très agressif (peu de faux négatifs, plus de faux positifs)
    threshold=2 → équilibré (défaut)
    threshold=3 → conservateur (seulement les cas certains)
    """
    score, _ = _score_row(row)
    return score >= threshold


def filter_out_recruiters(
    rows: list[dict],
    threshold: int = 2,
    log=None,
) -> tuple[list[dict], list[dict]]:
    """
    Sépare les annonces en (offres_directes, cabinets_détectés).
    log : fonction de logging optionnelle.
    """
    direct: list[dict] = []
    recruiters: list[dict] = []

    for row in rows:
        score, reasons = _score_row(row)
        if score >= threshold:
            recruiters.append(row)
            if log:
                company = row.get("entreprise") or "(sans nom)"
                log(f"  🚫 Cabinet évincé : {company}  [{', '.join(reasons[:2])}]")
        else:
            direct.append(row)

    return direct, recruiters


def get_detection_report(row: dict) -> dict:
    """Retourne un rapport détaillé pour une offre."""
    score, reasons = _score_row(row)
    return {
        "is_recruiter": score >= 2,
        "score": score,
        "reasons": reasons,
        "company": row.get("entreprise"),
        "url": row.get("url"),
    }
