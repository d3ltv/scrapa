#!/bin/bash
# Installation unique — crée l'environnement Python et installe les dépendances.
set -e
cd "$(dirname "$0")"

echo "=== Configuration de France Travail Export ==="
echo ""

# Cherche un Python Homebrew qui a Tkinter, sinon fallback sur python3 système
PYTHON=""
for candidate in /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$candidate" &>/dev/null && "$candidate" -c "import tkinter" 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Erreur : aucun Python avec Tkinter trouvé."
    echo "Installe-le via Homebrew :"
    echo "  brew install python-tk@3.14"
    exit 1
fi

echo "Python : $($PYTHON --version) ($PYTHON)"
echo "Tkinter : OK"

echo ""
echo "Création de l'environnement virtuel (.venv)..."
"$PYTHON" -m venv .venv

echo "Installation des dépendances..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "Fichier .env créé — ouvre-le et renseigne tes identifiants francetravail.io"
else
    echo ""
    echo "Fichier .env déjà présent (identifiants conservés)."
fi

echo ""
echo "=== Installation terminée ==="
echo ""
echo "Pour lancer l'interface graphique :"
echo "  • Double-clique sur « Lancer France Travail.command »"
echo "  • Ou exécute : ./Lancer\\ France\\ Travail.command"
echo ""
