# SAMS / EMS Portal V3.1 — Whitelist par ID Discord

Aucun bot Discord et aucune API Discord ne sont nécessaires.

## Connexion
Un employé peut entrer sur le site uniquement si :
- son ID Discord est enregistré ;
- la Direction a coché « Autoriser cet ID à se connecter » ;
- son compte est actif ;
- son mot de passe est correct.

Connaître seulement l'ID Discord de quelqu'un ne suffit donc pas.

## Compte Direction de démonstration
ID Discord : `111111111111111111`
Mot de passe : `SAMS2026!`

Connecte-toi avec ce compte, ouvre **Administration > Personnel**, puis remplace immédiatement cet ID de démonstration par ton vrai ID Discord et coche l'autorisation.

## Autoriser un employé
Dans **Administration > Personnel** :
1. ouvre ou crée sa fiche ;
2. colle son ID Discord ;
3. coche **Autoriser cet ID à se connecter** ;
4. donne-lui son mot de passe ;
5. sauvegarde.

Pour retirer l'accès : décoche l'autorisation ou suspends le compte.

## Trouver un ID Discord
Dans Discord : Paramètres utilisateur > Avancé > active **Mode développeur**, puis clic droit sur le membre > **Copier l'identifiant utilisateur**.

## Lancement
```powershell
python -m pip install -r requirements.txt
python app.py
```
Puis ouvre `http://127.0.0.1:5000`.

## Avant mise en ligne publique
Change `SAMS_SECRET_KEY`, active HTTPS, ajoute une protection CSRF, utilise un serveur WSGI, et utilise un mot de passe unique par employé.


## Déploiement 24/7
Cette édition est prête pour un hébergeur Python avec Gunicorn.
- Commande de build : `pip install -r requirements.txt`
- Commande de démarrage : `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`
- Variable obligatoire : `SAMS_SECRET_KEY`
- Variable recommandée avec stockage persistant : `DATA_DIR`
- `render.yaml` est fourni pour un Blueprint Render.

Important : SQLite doit être placé sur un disque persistant. Le fichier `render.yaml` prévoit un disque pour cela.
