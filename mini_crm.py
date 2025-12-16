import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
import pandas as pd
import json
import requests
import re
from supabase import create_client, Client

# --- 1. CONFIGURATION & CONNEXION ---
try:
    SUPABASE_URL = st.secrets["supabase"]["url"]
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    # Correction automatique pour SQLAlchemy (postgres:// -> postgresql://)
    DATABASE_URL = st.secrets["supabase"]["db_url"].replace("postgres://", "postgresql://")
except FileNotFoundError:
    st.error("Le fichier .streamlit/secrets.toml est introuvable.")
    st.stop()

# Connexion Supabase (Stockage fichiers)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET_NAME = "fichiers_clients"

# Connexion Base de Données
engine = create_engine(DATABASE_URL, echo=False)
Base = declarative_base()
Session = sessionmaker(bind=engine)
session = Session()

# --- 2. MODÈLES DE DONNÉES (TABLES) ---
class ClientModel(Base):
    __tablename__ = 'clients'
    id = Column(Integer, primary_key=True)
    nom = Column(String) 
    prenom = Column(String, nullable=True)
    entreprise = Column(String, nullable=True)
    siret = Column(String, nullable=True)
    adresse_kbis = Column(String, nullable=True)
    adresse_travaux = Column(String, nullable=True)
    email = Column(String, nullable=True)
    telephone = Column(String, nullable=True)
    statut = Column(String, default="Nouveau")
    note = Column(Text, nullable=True)
    
    # Champs techniques
    nb_eclairage = Column(String, nullable=True)
    nb_leds_preconise = Column(String, nullable=True)
    
    # Stockage JSON pour extensions futures
    caracteristiques_json = Column(Text, nullable=True)
    
    # Relation avec les fichiers
    fichiers = relationship("FichierClientModel", back_populates="client", cascade="all, delete-orphan")

class FichierClientModel(Base):
    __tablename__ = 'fichiers_clients'
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('clients.id'))
    nom_fichier = Column(String)
    path_storage = Column(String)
    url_public = Column(String)
    client = relationship("ClientModel", back_populates="fichiers")

# Création des tables si elles n'existent pas
Base.metadata.create_all(engine)

# --- 3. FONCTIONS UTILITAIRES ---

def fetch_siret_data(siret):
    """Récupère les infos entreprise via API Gouv"""
    siret_clean = siret.replace(" ", "")
    url = f"https://recherche-entreprises.api.gouv.fr/search?q={siret_clean}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            results = response.json().get('results', [])
            if results:
                data = results[0]
                return {
                    "nom_complet": data.get('nom_complet'),
                    "adresse": data.get('siege', {}).get('adresse'),
                    "siret_clean": siret_clean
                }
    except:
        return None
    return None

def ajouter_client(data, fichiers_uploades):
    """Crée un client en base + Upload fichiers"""
    # Nettoyage des caractéristiques vides
    caract_remplies = {k: v for k, v in data['caracteristiques'].items() if v}
    caract_json = json.dumps(caract_remplies) if caract_remplies else None

    nouveau = ClientModel(
        nom=data['nom'],
        prenom=data['prenom'],
        entreprise=data['entreprise'],
        siret=data['siret'],
        adresse_kbis=data['adresse_kbis'],
        adresse_travaux=data['adresse_travaux'],
        email=data['email'],
        telephone=data['telephone'],
        nb_eclairage=str(data['nb_eclairage']),
        nb_leds_preconise=str(data['nb_leds_preconise']),
        statut="Nouveau",
        note=data['note'],
        caracteristiques_json=caract_json
    )
    session.add(nouveau)
    session.commit()
    
    # Upload des fichiers si présents
    if fichiers_uploades:
        sauvegarder_fichiers(nouveau.id, fichiers_uploades)

def sauvegarder_fichiers(client_id, liste_fichiers):
    """Envoie les fichiers vers Supabase Storage"""
    for fichier in liste_fichiers:
        file_path = f"{client_id}/{fichier.name}"
        try:
            # 1. Upload Cloud
            fichier.seek(0)
            file_bytes = fichier.read()
            supabase.storage.from_(BUCKET_NAME).upload(
                path=file_path,
                file=file_bytes,
                file_options={"content-type": fichier.type, "x-upsert": "true"}
            )
            # 2. Récupération URL
            public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(file_path)
            
            # 3. Enregistrement en base
            db_f = FichierClientModel(
                client_id=client_id, 
                nom_fichier=fichier.name, 
                path_storage=file_path,
                url_public=public_url
            )
            session.add(db_f)
        except Exception as e:
            st.error(f"Erreur upload {fichier.name}: {str(e)}")
    session.commit()

def supprimer_un_fichier(fichier_id):
    """Supprime un fichier du Cloud et de la DB"""
    fichier = session.query(FichierClientModel).get(fichier_id)
    if fichier:
        try:
            supabase.storage.from_(BUCKET_NAME).remove([fichier.path_storage])
        except Exception as e:
            print(f"Erreur suppression cloud: {e}")
        session.delete(fichier)
        session.commit()

def supprimer_client_entier(client_id):
    """Supprime le client et tous ses fichiers"""
    client = session.query(ClientModel).get(client_id)
    if client:
        # Suppression fichiers Cloud
        fichiers = client.fichiers
        paths_to_remove = [f.path_storage for f in fichiers]
        if paths_to_remove:
            try:
                supabase.storage.from_(BUCKET_NAME).remove(paths_to_remove)
            except Exception as e:
                print(f"Erreur suppression fichiers: {e}")
        # Suppression DB
        session.delete(client)
        session.commit()

def get_dataframe(recherche=""):
    """Prépare les données pour le tableau de bord"""
    query = session.query(ClientModel)
    if recherche:
        term = f"%{recherche}%"
        query = query.filter(
            (ClientModel.nom.ilike(term)) | 
            (ClientModel.prenom.ilike(term)) | 
            (ClientModel.entreprise.ilike(term))
        )
    clients = query.all()
    
    data = []
    for c in clients:
        # Formatage technique
        c_str = ""
        if c.caracteristiques_json:
            try:
                d = json.loads(c.caracteristiques_json)
                c_str = ", ".join([f"{k}: {v}" for k, v in d.items()])
            except: pass
        
        data.append({
            "ID": c.id,
            "Statut": c.statut,
            "Entreprise": c.entreprise,
            "Nom": c.nom,
            "Prénom": c.prenom,
            "Nb Éclairages": c.nb_eclairage,
            "Nb LEDs Préco": c.nb_leds_preconise,
            "Adresse KBIS": c.adresse_kbis,
            "Adresse Travaux": c.adresse_travaux,
            "Email": c.email,
            "Téléphone": c.telephone,
            "SIRET": c.siret,
            "Caractéristiques": c_str,
            "Note": c.note,
            "Fichiers": f"{len(c.fichiers)} fichier(s)"
        })
    return pd.DataFrame(data)

def update_from_editor():
    """Gère les modifications directes dans le tableau (Statut)"""
    changes = st.session_state.get('main_editor')
    if not changes or not changes.get('edited_rows'): return

    df = st.session_state['df_view']
    for row_idx, modifications in changes['edited_rows'].items():
        row_idx = int(row_idx)
        c_id = df.iloc[row_idx]['ID']
        client = session.query(ClientModel).get(int(c_id))
        
        if client:
            for col, val in modifications.items():
                if col == "Statut": 
                    client.statut = val
                    session.commit()
    st.session_state['refresh'] = True

# Gestion du Reset de formulaire
def clear_form_logic():
    if st.session_state.get('reset_needed'):
        keys_to_clear = [
            "w_nom", "w_prenom", "w_email", "w_tel", "w_note", 
            "w_siret_input", "w_siret_valide", "w_ent", "w_kbis", 
            "w_travaux", "w_ecl_type"
        ]
        for k in keys_to_clear:
            if k in st.session_state: st.session_state[k] = ""
            
        keys_zero_float = ["w_surf", "w_haut"]
        for k in keys_zero_float:
            if k in st.session_state: st.session_state[k] = 0.0
            
        keys_zero_int = ["w_ecl_puis", "w_nbecl", "w_nbled"]
        for k in keys_zero_int:
            if k in st.session_state: st.session_state[k] = 0
            
        if "w_checkbox_same" in st.session_state: st.session_state["w_checkbox_same"] = False
        st.session_state['reset_needed'] = False

# --- 4. INTERFACE GRAPHIQUE ---

st.set_page_config(page_title="CRM - Version Stable", layout="wide")

# Init Session State
if 'reset_needed' not in st.session_state: st.session_state['reset_needed'] = False
if 'refresh' not in st.session_state: st.session_state['refresh'] = False
clear_form_logic() 

# --- SIDEBAR : FORMULAIRE D'AJOUT ---
with st.sidebar:
    st.header("Nouveau Client")
    
    # 1. CONTACT
    st.subheader("1. Contact")
    c_nom, c_prenom = st.columns(2)
    c_nom.text_input("Nom *", key="w_nom")
    c_prenom.text_input("Prénom", key="w_prenom")
    st.text_input("Mail", key="w_email")
    st.text_input("Téléphone", key="w_tel")
    st.text_area("Note (Interne)", key="w_note", height=80)
    st.divider()

    # 2. ENTREPRISE (SIRET)
    st.subheader("2. Entreprise")
    st.text_input("Nom Entreprise", key="w_ent")
    
    col_s1, col_s2 = st.columns([3, 1])
    col_s1.text_input("Recherche SIRET", key="w_siret_input", label_visibility="collapsed", max_chars=14)
    
    def auto_fill_siret():
        siret_in = st.session_state.get("w_siret_input", "")
        if siret_in:
            infos = fetch_siret_data(siret_in)
            if infos:
                st.session_state['w_ent'] = infos['nom_complet']
                st.session_state['w_kbis'] = infos['adresse']
                st.session_state['w_siret_valide'] = infos['siret_clean']
                st.toast("✅ Trouvé !", icon="🏢")
            else:
                st.toast("❌ SIRET introuvable.", icon="⚠️")
                
    col_s2.button("🔍", on_click=auto_fill_siret)
    
    st.text_input("Adresse Siège", key="w_kbis")
    
    def auto_copy_address():
        if st.session_state.get("w_checkbox_same", False):
            st.session_state['w_travaux'] = st.session_state.get("w_kbis", "")
            
    st.checkbox("Adresse travaux identique ?", key="w_checkbox_same", on_change=auto_copy_address)
    st.text_input("Adresse Travaux", key="w_travaux")
    st.text_input("SIRET Validé", key="w_siret_valide", max_chars=14)
    st.divider()

    # 3. TECHNIQUE
    st.subheader("3. Technique")
    st.number_input("Superficie (m²)", min_value=0.0, step=1.0, format="%.0f", key="w_surf")
    st.number_input("Hauteur ss plafond (m)", min_value=0.0, step=0.1, format="%.2f", key="w_haut")
    st.text_input("Type Éclairage", key="w_ecl_type")
    st.number_input("Puissance (W)", min_value=0, step=1, key="w_ecl_puis")
    st.divider()

    # 4. COMPTAGE
    st.subheader("4. Comptage")
    cc1, cc2 = st.columns(2)
    cc1.number_input("Nb Actuel", min_value=0, step=1, key="w_nbecl")
    cc2.number_input("Nb LEDs Préco", min_value=0, step=1, key="w_nbled")

    # 5. FICHIERS
    st.divider()
    val_files = st.file_uploader("Fichiers", accept_multiple_files=True)

    # BOUTON ENREGISTRER
    if st.button("✅ Enregistrer la fiche", type="primary"):
        # Validation basique
        nom_in = st.session_state.get("w_nom")
        email_in = st.session_state.get("w_email")
        
        def is_valid_email(email):
            if not email: return True
            return re.match(r"[^@]+@[^@]+\.[^@]+", email)

        if not nom_in:
            st.error("Le Nom est obligatoire.")
        elif not is_valid_email(email_in):
            st.error("Format Email invalide.")
        else:
            # Préparation données
            surf_val = str(st.session_state.get("w_surf")) if st.session_state.get("w_surf") > 0 else ""
            haut_val = str(st.session_state.get("w_haut")) if st.session_state.get("w_haut") > 0 else ""
            puis_val = str(st.session_state.get("w_ecl_puis")) if st.session_state.get("w_ecl_puis") > 0 else ""
            tel_clean = re.sub(r'[\s\-\.]', '', st.session_state.get("w_tel") or "")

            caracs = {
                "Superficie (m²)": surf_val, 
                "Hauteur (m)": haut_val,
                "Type Éclairage": st.session_state.get("w_ecl_type"),
                "Puissance (W)": puis_val
            }
            
            data_client = {
                "nom": nom_in, 
                "prenom": st.session_state.get("w_prenom"), 
                "entreprise": st.session_state.get("w_ent"),
                "siret": st.session_state.get("w_siret_valide"), 
                "email": email_in, 
                "telephone": tel_clean,
                "adresse_kbis": st.session_state.get("w_kbis"), 
                "adresse_travaux": st.session_state.get("w_travaux"),
                "nb_eclairage": st.session_state.get("w_nbecl"), 
                "nb_leds_preconise": st.session_state.get("w_nbled"),
                "note": st.session_state.get("w_note"), 
                "caracteristiques": caracs
            }
            
            ajouter_client(data_client, val_files)
            st.session_state['reset_needed'] = True
            st.success("Client sauvegardé avec succès !")
            st.session_state['refresh'] = True
            st.rerun()

# --- MAIN : TABLEAU DE BORD ---
tab1, tab2 = st.tabs(["📊 Tableau de Bord", "📁 Gestion & Fichiers"])

# TAB 1 : VUE GLOBALE
with tab1:
    st.title("Suivi Clients (Cloud)")
    search = st.text_input("Filtrer le tableau...", placeholder="Nom, Ville, Entreprise...")
    
    df = get_dataframe(search)
    st.session_state['df_view'] = df

    if not df.empty:
        col_conf = {
            "Statut": st.column_config.SelectboxColumn(
                options=["Nouveau", "Contacté", "Devis envoyé", "En négo", "Signé", "Perdu"],
                required=True
            )
        }
        colonnes_verrouillees = [c for c in df.columns if c != "Statut"]
        
        st.data_editor(
            df,
            column_config=col_conf,
            disabled=colonnes_verrouillees,
            hide_index=True,
            use_container_width=True,
            height=600,
            key="main_editor",
            on_change=update_from_editor
        )
    else:
        st.info("Aucun client dans la base.")

# TAB 2 : GESTION COMPLETE
with tab2:
    st.header("Gestion Avancée")
    
    # Sélecteur Client
    all_clients = session.query(ClientModel).all()
    opts = {c.id: f"{c.nom} {c.prenom or ''} ({c.entreprise or 'Indiv'})" for c in all_clients}
    sel_id = st.selectbox("Sélectionner le client :", options=opts.keys(), format_func=lambda x: opts[x]) if opts else None
    
    if sel_id:
        c_edit = session.query(ClientModel).get(sel_id)
        
        # Formulaire Modification
        with st.expander("Modifier les informations", expanded=True):
            with st.form("edit_form"):
                st.subheader("Contact")
                c_nom, c_pre = st.columns(2)
                e_nom = c_nom.text_input("Nom", value=c_edit.nom or "")
                e_pre = c_pre.text_input("Prénom", value=c_edit.prenom or "")
                e_email = st.text_input("Email", value=c_edit.email or "")
                e_tel = st.text_input("Tél", value=c_edit.telephone or "")
                
                st.subheader("Entreprise")
                e_ent = st.text_input("Entreprise", value=c_edit.entreprise or "")
                e_siret = st.text_input("SIRET", value=c_edit.siret or "", max_chars=14)
                e_kbis = st.text_input("Adresse Siège", value=c_edit.adresse_kbis or "")
                e_trav = st.text_input("Adresse Travaux", value=c_edit.adresse_travaux or "")
                
                st.subheader("Technique")
                def safe_int(val):
                    try: return int(float(val))
                    except: return 0
                c_tech1, c_tech2 = st.columns(2)
                e_nb = c_tech1.number_input("Nb Actuel", value=safe_int(c_edit.nb_eclairage))
                e_nb_led = c_tech2.number_input("Nb LEDs Préco", value=safe_int(c_edit.nb_leds_preconise))
                e_note = st.text_area("Note", value=c_edit.note or "")
                
                if st.form_submit_button("💾 Mettre à jour"):
                    if not e_nom: st.error("Nom requis.")
                    else:
                        cl_tel = re.sub(r'[\s\-\.]', '', e_tel) if e_tel else ""
                        c_edit.entreprise = e_ent
                        c_edit.nom = e_nom
                        c_edit.prenom = e_pre
                        c_edit.siret = e_siret
                        c_edit.email = e_email
                        c_edit.telephone = cl_tel
                        c_edit.adresse_kbis = e_kbis
                        c_edit.adresse_travaux = e_trav
                        c_edit.nb_eclairage = str(e_nb)
                        c_edit.nb_leds_preconise = str(e_nb_led)
                        c_edit.note = e_note
                        session.commit()
                        st.success("Informations mises à jour !")
                        st.session_state['refresh'] = True
                        st.rerun()

        st.divider()
        
        # Gestion Fichiers
        col_fichiers, col_ajout = st.columns([1, 1])
        
        with col_fichiers:
            st.subheader("Fichiers Cloud")
            if c_edit.fichiers:
                for f in c_edit.fichiers:
                    c1, c2, c3 = st.columns([4, 2, 1])
                    c1.text(f"📄 {f.nom_fichier}")
                    c2.markdown(f"[⬇️ Ouvrir]({f.url_public})")
                    if c3.button("❌", key=f"del_{f.id}"):
                        supprimer_un_fichier(f.id)
                        st.session_state['refresh'] = True
                        st.rerun()
            else:
                st.caption("Aucun fichier pour ce client.")

        with col_ajout:
            st.subheader("Ajouter un fichier")
            new_files = st.file_uploader("Upload", accept_multiple_files=True, key="new_uploads_manage")
            if st.button("Envoyer au Cloud"):
                if new_files:
                    sauvegarder_fichiers(c_edit.id, new_files)
                    st.success("Fichiers envoyés !")
                    st.session_state['refresh'] = True
                    st.rerun()

        st.divider()
        
        # Suppression Client
        if st.button("🗑 SUPPRIMER CE CLIENT", type="primary"):
            supprimer_client_entier(c_edit.id)
            st.success("Client supprimé.")
            st.session_state['refresh'] = True
            st.rerun()
