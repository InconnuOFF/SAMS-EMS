# SAMS / EMS Portal V3 — Hébergement gratuit Render + Neon

Cette version remplace SQLite par PostgreSQL pour éviter la perte des données
quand Render Free redémarre ou met le site en veille.

## Architecture gratuite

- Site Flask : Render Web Service Free
- Base de données : Neon PostgreSQL Free
- Coût : 0 € tant que tu restes dans les limites gratuites

## Important

Render Free met le site en veille après une période d'inactivité.
La première ouverture après la veille peut donc être plus lente.

La base Neon reste séparée du disque local de Render, donc les employés,
IDs Discord autorisés, sanctions, dossiers, planning, réglages du Studio,
etc. ne dépendent plus du stockage temporaire du serveur Render.

## 1. Créer la base Neon

1. Crée un compte Neon.
2. Crée un nouveau projet PostgreSQL.
3. Clique sur **Connect**.
4. Copie la chaîne de connexion, qui ressemble à :

```text
postgresql://utilisateur:motdepasse@xxxxx.neon.tech/neondb?sslmode=require
```

Garde cette URL privée.

## 2. Mettre les fichiers sur GitHub

Remplace les fichiers de ton dépôt SAMS-EMS par cette version.
Les fichiers importants sont notamment :

- app.py
- requirements.txt
- render.yaml
- Procfile
- templates/
- static/

## 3. Render

Dans Render :

1. New + → Blueprint
2. Sélectionne ton dépôt GitHub SAMS-EMS
3. Render lit `render.yaml`
4. Pour `DATABASE_URL`, colle l'URL copiée depuis Neon
5. Lance le déploiement

`SAMS_SECRET_KEY` est générée automatiquement par Render.

## Premier compte Direction

À la toute première création d'une base vide :

ID Discord :
111111111111111111

Mot de passe :
SAMS2026!

Connecte-toi puis remplace cet ID de démonstration par ton vrai ID Discord
dans Administration → Personnel.

## Données

Toutes les données applicatives sont enregistrées dans PostgreSQL Neon :
personnel, whitelist Discord, grades, divisions, appels, véhicules,
patients, rapports, formations, planning, congés, sanctions, candidatures,
notifications, réglages et contenu du Studio.

## Sécurité

Ne mets jamais `DATABASE_URL` dans GitHub.
Elle doit uniquement être enregistrée comme variable d'environnement Render.
