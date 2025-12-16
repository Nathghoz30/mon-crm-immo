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

### 🛠️ Stack Technique
* **Frontend & Backend :** [Streamlit](https://streamlit.io/) (Python)
* **Base de données :** [Supabase](https://supabase.com/) (PostgreSQL)
* **Stockage Fichiers :** Supabase Storage buckets
* **ORM :** SQLAlchemy
* **API Externe :** API Recherche Entreprises (Data.gouv.fr)

---

## ⚙️ Installation Locale

Pour faire tourner ce projet sur votre machine :

### 1. Cloner le projet
```bash
git clone [https://github.com/votre-pseudo/mon-crm-immo.git](https://github.com/votre-pseudo/mon-crm-immo.git)
cd mon-crm-immo
