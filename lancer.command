#!/bin/bash
# Double-clique pour lancer l'interface graphique Scrapa.
cd "$(dirname "$0")"

# Si le venv n'existe pas, on lance le setup automatiquement
if [ ! -d .venv ]; then
    echo "Première utilisation — installation des dépendances..."
    bash setup.sh
fi

# Vérif tkinter
if ! .venv/bin/python3 -c "import tkinter" 2>/dev/null; then
    osascript -e 'display dialog "Tkinter manquant.\nSur Mac : brew install python-tk" buttons {"OK"} default button 1 with title "Scrapa"'
    exit 1
fi

exec .venv/bin/python3 france_travail_gui.py
