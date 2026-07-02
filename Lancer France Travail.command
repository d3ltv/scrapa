#!/bin/bash
# Double-clique sur ce fichier pour ouvrir l'interface graphique.
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    osascript -e 'display dialog "Lance d'\''abord setup.sh pour installer l'\''application (une seule fois)." buttons {"OK"} default button 1 with title "France Travail Export"'
    exit 1
fi

if ! .venv/bin/python3 -c "import tkinter" 2>/dev/null; then
    osascript -e 'display dialog "Tkinter est manquant. Sur Mac avec Homebrew : brew install python-tk" buttons {"OK"} default button 1 with title "France Travail Export"'
    exit 1
fi

exec .venv/bin/python3 france_travail_gui.py
