# BECoM – App de pointage (Flask)

## Démarrage rapide

1) Créez un virtualenv et installez :
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

2) Lancez l'app :
```bash
export FLASK_APP=app.py  # Windows PowerShell: $env:FLASK_APP="app.py"
python app.py
# Puis ouvrez http://localhost:5000
```

3) Connexion admin par défaut :
- identifiant: `admin.admin`
- mot de passe: `becom2025!`

4) Créer des employés :
```bash
flask --app app.py create-user
# Saisir Prénom/Nom/Mot de passe ; identifiant généré = nom.prenom
```

5) Utilisation
- Les employés pointent en cliquant un jour et choisissant Jour / Nuit / Déplacement.
- L'admin valide ou refuse dans l'onglet Admin.
- Export PDF mensuel : bouton "Exporter PDF (zip)" dans l'onglet Admin.

## Notes
- Base de données SQLite (`becom.db`) ; pour repartir de zéro, supprimez le fichier.
- Pour un export automatique en fin de mois, planifiez une tâche cron qui appelle l'URL `/admin/export_pdfs?year=AAAA&month=MM` (derrière un VPN / réseau interne). 
- Changez `SECRET_KEY` et le mot de passe admin en production.
