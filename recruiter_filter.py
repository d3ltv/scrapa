"""
recruiter_filter.py
====================
Détecte et filtre les annonces postées par des cabinets de recrutement
externes et des agences d'intérim / travail temporaire.
"""

import re

# ---------------------------------------------------------------------------
# Patterns — noms d'entreprises typiques de cabinets
# ---------------------------------------------------------------------------

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
    r"\btalent[s]?\s+search\b",
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
    r"\bservices?\s+rh\b",
    r"\bemploi\s+et\s+comp[ée]tences?\b",
    # Tout nom contenant "rh" en tant que mot isolé (ex: "Dupont RH", "RH Conseil", "ABC RH Services")
    r"\brh\b",

    # Grands cabinets (noms propres)
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
    r"\bvedior\b",
    r"\brendstaf\b",
    r"\blhh\b",
    r"\blee\s+hecht\b",
    r"\bheidrick\b",
    r"\bkorn\s*ferry\b",
    r"\bspencer\s+stuart\b",
    r"\begon\s+zehnder\b",
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
    r"\bactua\b",
    r"\bineo\b",
    r"\binterim[e]?\b",
    r"\bintérim[e]?\b",
    r"\btravail\s+temporaire\b",
    r"\bagence\s+d.emploi\b",
    r"\bagence\s+de\s+(recrutement|travail|placement)\b",
    r"\bapec\b",
    r"\bapec\s+recrutement\b",

    # Agences d'intérim / travail temporaire
    r"\btemporis\b",
    r"\btriangle\s+(interim|intérim|solutions?|emploi)\b",
    r"\bsamsic\s+(emploi|rh|interim|intérim)?\b",
    r"\bgrafton\b",
    r"\bdomino\s+(rh|staff|missions?|interim|intérim)?\b",
    r"\bflexijob\b",
    r"\bkelly\s*(services?|ocg)?\b",
    r"\bolvea\b",
    r"\bups\s+interim\b",
    r"\bstart\s+(people|rh)\b",
    r"\bipse\b",
    r"\btalentpeople\b",
    r"\binteractive\s+interim\b",
    r"\bbis\s+interim\b",
    r"\bbis\s+recrutement\b",
    r"\boffice\s+depot\s+interim\b",
    r"\bsolint\b",
    r"\bforce\s+travail\b",
    r"\bmooveus\b",
    r"\beffectif\s+service\b",
    r"\bmanpower\s+interim\b",
    r"\bmission\s+locale\b",
    r"\bemploi\s+intérim\b",
    r"\bemploi\s+interim\b",
    r"\bagence\s+intérim\b",
    r"\bagence\s+interim\b",
    r"\btravailleur[s]?\s+temporaire[s]?\b",
    r"\bsté\s+(d'?interim|d'?intérim)\b",
    r"\bsociété\s+(d'?interim|d'?intérim)\b",
    r"\bcontrat\s+(d'?interim|d'?intérim)\b",

    # Cabinets spécialisés BTP / industrie fréquents
    r"\bbtp\s+recrutement\b",
    r"\brecrutement\s+btp\b",
    r"\brecrutement\s+construction\b",
    r"\bingéni(eur|erie)\s+recrutement\b",
    r"\btechni(que)?\s+recrutement\b",
    r"\bconstruct'?if\b",
    r"\bsodifrance\b",
    r"\bexperta\b",
    r"\bceliade\b",
    r"\bphénix\s+rh\b",
    r"\bphenix\s+rh\b",
    r"\bbâti\s+recrutement\b",
    r"\bbati\s+recrutement\b",
    r"\bgroupe\s+(crit|bis|actua|partnaire|adéquat|adequat)\b",
    r"\bpartnaire\b",
    r"\badéquat\b",
    r"\badequat\b",
    r"\bsofitex\b",
    r"\bjob\s*link\b",
    r"\brecru[it]+eur[s]?\b",
    r"\bmission[s]?\s+interim\b",
    r"\bmission[s]?\s+intérim\b",
    r"\bgroupement\s+d.employeurs?\b",
    r"\bgroupement\s+employeurs?\b",
    r"\bgeiq\b",
]

# Mots dans la DESCRIPTION → indicateurs cabinet
DESCRIPTION_PATTERNS = [
    # Formulations "pour le compte de"
    (r"notre\s+client\b", 2),
    (r"notre\s+cabinet\b", 2),
    (r"pour\s+le\s+compte\s+de\b", 2),
    (r"pour\s+notre\s+client\b", 2),
    (r"pour\s+un\s+de\s+nos\s+clients?\b", 2),
    (r"au\s+nom\s+de\b", 1),
    (r"nous\s+recrutons\s+pour\b", 2),
    (r"notre\s+partenaire\b", 1),
    (r"notre\s+mandant\b", 2),
    (r"confié\s+par\b", 2),
    (r"miss?ion\s+confi[ée]e\b", 2),
    (r"cabinet\s+de\s+recrutement\b", 2),
    (r"cabinet\s+conseil\b", 1),
    (r"chasseur[s]?\s+de\s+têtes?\b", 2),
    (r"recruteur[s]?\s+indépendant[s]?\b", 2),
    (r"prestataire\s+rh\b", 2),
    (r"consultant[s]?\s+rh\b", 1),
    (r"votre\s+consultant[e]?\b", 1),
    (r"notre\s+consultant[e]?\b", 1),
    # Références de suivi cabinet
    (r"référence\s+(du\s+poste|offre)\s*:", 1),
    (r"réf\s*\.?\s*\w+[-–]\w+[-–]\w+", 1),
    (r"\bposte\s+(?:réf|ref)\.?\s*:", 1),
    (r"\boffre\s+n[°o]", 1),
    (r"\bnotre\s+équipe\s+de\s+consultant", 1),
    # Formulations BTP spécifiques
    (r"acteur\s+(incontournable|majeur|reconnu)\s+(du\s+)?(btp|bâtiment|construction|travaux)", 2),
    (r"société\s+(spécialisée|leader|reconnue).{0,40}(btp|bâtiment|construction).{0,40}recrute\s+pour", 2),
    (r"notre\s+client.{0,60}(btp|bâtiment|construction|travaux\s+publics)", 2),
    (r"nous\s+accompagnons.{0,40}(entreprise[s]?|société[s]?).{0,40}(btp|construction)", 1),
    (r"dans\s+le\s+cadre\s+du\s+développement\s+de\s+notre\s+client", 2),
    (r"entreprise\s+(partenaire|cliente)\b", 2),
    (r"l['']entreprise\s+(partenaire|cliente)\b", 2),
    (r"mission\s+en\s+(intérim|interim|cdi|cdd)\s+(pour|chez)\s+l[''un]", 1),
    (r"poste\s+à\s+pourvoir\s+(chez|pour)\s+(notre|un)\s+(client|partenaire)", 2),
    (r"cabinet\s+spécialisé", 2),
    (r"agence\s+(d[e']?\s+)?recrutement\b", 2),
    (r"agence\s+d[''']emploi\b", 2),
    # Formulations typiques intérim
    (r"mission\s+(d[''']?|en\s+)(intérim|interim)\b", 2),
    (r"poste\s+en\s+(intérim|interim)\b", 2),
    (r"agence\s+(d[''']?|de\s+)(travail\s+temporaire|intérim|interim)\b", 2),
    (r"contrat\s+(d[''']?)(intérim|interim)\b", 2),
    (r"contrat\s+de\s+travail\s+temporaire\b", 2),
    (r"travailleur[s]?\s+temporaire[s]?\b", 1),
    (r"mise\s+à\s+disposition\b", 1),
    (r"entreprise\s+de\s+travail\s+temporaire\b", 2),
    (r"ETT\b", 1),
    (r"soci[ée]t[ée]\s+(d[''']|de\s+)(travail\s+temporaire|interim|intérim)\b", 2),
]

# URL de sites connus de cabinets
RECRUITER_URL_DOMAINS = {
    "manpower.fr", "adecco.fr", "randstad.fr", "hays.fr",
    "michaelpage.fr", "pagepersonnel.fr", "robertwalters.fr",
    "roberthalf.fr", "kornferry.com", "lhh.com",
    "synergie.fr", "proman.com", "gi-group.fr", "crit.fr",
    "adequat.com", "partnaire.fr", "sofitex.fr",
    "actua.fr", "joblink.fr", "apec.fr",
    # Agences d'intérim supplémentaires
    "temporis.fr", "triangle-interim.fr", "samsic-emploi.fr",
    "grafton.fr", "domino-rh.com", "startpeople.fr",
    "kellyservices.fr", "kelly.fr", "flexijob.fr",
    "bis-interim.fr", "solint.fr", "force-travail.fr",
    "interaction-interim.fr", "appel-interim.fr",
    "welljob.com", "jubil-interim.com", "rh-solutions.fr",
}

# ---------------------------------------------------------------------------
# Moteur de détection
# ---------------------------------------------------------------------------

_COMPANY_RE = [re.compile(p, re.IGNORECASE) for p in COMPANY_NAME_PATTERNS]
_DESC_RE    = [(re.compile(p, re.IGNORECASE), score) for p, score in DESCRIPTION_PATTERNS]


def _score_row(row: dict) -> tuple[int, list[str]]:
    """
    Score de suspicion cabinet / agence d'intérim.
    Score ≥ 2 → intermédiaire RH probable (seuil défaut)
    """
    reasons: list[str] = []
    score = 0

    company     = str(row.get("entreprise") or "")
    description = str(row.get("description") or "")
    url         = str(row.get("url") or row.get("entreprise_url") or "")

    # Signal fort : nom de l'entreprise (score +2, on s'arrête au premier match)
    for pat in _COMPANY_RE:
        if pat.search(company):
            reasons.append(f"nom: «{pat.pattern}»")
            score += 2
            break

    # Signal description (score variable, cap à 4 points max depuis la description)
    desc_score = 0
    for pat, pts in _DESC_RE:
        if pat.search(description):
            reasons.append(f"desc: «{pat.pattern}»")
            desc_score += pts
            if desc_score >= 4:
                break
    score += min(desc_score, 4)

    # Signal léger : domaine URL
    for domain in RECRUITER_URL_DOMAINS:
        if domain in url.lower():
            reasons.append(f"url: {domain}")
            score += 1
            break

    return score, reasons


def is_recruiter(row: dict, threshold: int = 2) -> bool:
    """
    Retourne True si l'annonce semble provenir d'un cabinet de recrutement
    ou d'une agence d'intérim / travail temporaire.
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
    Sépare les annonces en (offres_directes, intermédiaires_détectés).
    Filtre à la fois les cabinets de recrutement et les agences d'intérim.
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
                log(f"  🚫 Intermédiaire RH évincé : {company}  [{', '.join(reasons[:2])}]")
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
