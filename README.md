# 🏗️ Mini CRM - Gestion Clients & Chantiers

Une application de gestion de la relation client (CRM) ultra-légère et performante, conçue spécifiquement pour les artisans et professionnels du bâtiment/immobilier.

Développée en **Python** avec **Streamlit**, elle est entièrement hébergée dans le Cloud grâce à **Supabase**.

## 🚀 Fonctionnalités

### 📊 Tableau de Bord
* Vue d'ensemble de tous les clients sous forme de tableau interactif.
* **Filtrage en temps réel** (par nom, ville, entreprise).
* Modification rapide du **Statut** (Nouveau, Devis envoyé, Signé, etc.) directement depuis le tableau.

### 📝 Gestion Clients Complète
* Ajout de clients avec **autocomplétion automatique via le SIRET** (API Gouv).
* Saisie des coordonnées (Email, Téléphone) et adresses (Siège, Travaux).
* Saisie des données techniques (Superficie, Hauteur sous plafond, Type d'éclairage, etc.).
* Notes internes pour le suivi commercial.

### 📁 Gestion Documentaire (Cloud)
* Upload de fichiers (Devis, Photos, Plans) associé à chaque client.
* Stockage sécurisé sur **Supabase Storage**.
* Consultation et suppression des fichiers directement depuis l'interface.

---

## 🛠️ Stack Technique

* **Frontend & Backend :** [Streamlit](https://streamlit.io/) (Python)
* **Base de données :** [Supabase](https://supabase.com/) (PostgreSQL)
* **Stockage Fichiers :** Supabase Storage buckets
* **ORM :** SQLAlchemy
* **API Externe :** API Recherche Entreprises (Data.gouv.fr)

---

## ⚙️ Installation & Lancement Local

Si vous souhaitez modifier le code ou lancer le projet sur votre propre ordinateur, suivez ces étapes :

### 1. Récupérer le projet
    git clone https://github.com/votre-pseudo/mon-crm-immo.git
    cd mon-crm-immo

### 2. Créer l'environnement virtuel (Recommandé)
Cela permet d'isoler les bibliothèques du projet.

**Windows :**
    python -m venv venv
    venv\Scripts\activate

**Mac / Linux :**
    python3 -m venv venv
    source venv/bin/activate

### 3. Installer les dépendances
    pip install -r requirements.txt

### 4. Configurer les Secrets (Important ⚠️)
L'application a besoin de vos clés Supabase pour fonctionner.
Créez un dossier nommé `.streamlit` à la racine du projet, puis créez un fichier `secrets.toml` à l'intérieur.

**Fichier :** `.streamlit/secrets.toml`

    [supabase]
    url = "VOTRE_URL_SUPABASE_ICI"
    key = "VOTRE_CLE_ANON_PUBLIC_ICI"
    db_url = "postgresql://postgres:[PASSWORD]@[HOST]:6543/postgres"

*(Remplacez les valeurs par celles trouvées dans votre tableau de bord Supabase > Project Settings > API / Database)*

### 5. Lancer l'application
    streamlit run mini_crm.py

Une fenêtre de navigateur s'ouvrira automatiquement sur `http://localhost:8501`.

---

## ☁️ Déploiement sur Streamlit Cloud

Ce projet est configuré pour être déployé gratuitement et facilement :

1.  Hébergez ce code sur **GitHub**.
2.  Connectez-vous sur [share.streamlit.io](https://share.streamlit.io/).
3.  Cliquez sur **"New App"** et sélectionnez votre dépôt GitHub.
4.  Dans les paramètres avancés (**Advanced Settings**), collez le contenu de votre fichier `secrets.toml` dans la zone "Secrets".
5.  Cliquez sur **Deploy**.

---

## 🗄️ Structure de la Base de Données

L'application génère automatiquement les tables nécessaires au premier lancement via SQLAlchemy.

**Tables créées :**
* `clients` : Contient les infos texte (Nom, SIRET, Note, Caractéristiques JSON...).
* `fichiers_clients` : Contient les liens vers les fichiers stockés et l'URL publique.

**Configuration requise sur Supabase :**
* Un Bucket Storage nommé `fichiers_clients` doit être créé et rendu "Public".
* Les politiques de sécurité (RLS) du Storage doivent autoriser l'écriture pour que l'upload fonctionne.

---

## 👤 Auteur

Projet maintenu par Ghozlan Nathan - MYWEBCREATOR.
