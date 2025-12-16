import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
import pandas as pd
import json
import requests
import re
import urllib.parse # Pour encoder l'adresse proprement
from supabase import create_client, Client

# --- CONFIGURATION SUPABASE & DB ---

try:
    SUPABASE_URL = st.secrets["supabase"]["url"]
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    # Remplacement postgres:// par postgresql:// pour SQLAlchemy
    DATABASE_URL = st.secrets["supabase"]["db_url"].replace("postgres://", "postgresql://")
except FileNotFoundError:
    st.error("Fichier secrets.toml introuvable ou mal configuré.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET_NAME = "fichiers_clients"

engine = create_engine(DATABASE_URL, echo=False)
Base = declarative_base()

# --- MODÈLES DE DONNÉES ---

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
    
    nb_eclairage = Column(String, nullable=True)
    nb_leds_preconise = Column(String, nullable=True)
    
    caracteristiques_json = Column(Text, nullable=True)
    fichiers = relationship("FichierClientModel", back_populates="client", cascade="all, delete-orphan")

class FichierClientModel(Base):
    __tablename__ = 'fichiers_clients'
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('clients.id'))
    nom_fichier = Column(String)
    path_storage = Column(String)
    url_public = Column(String)
    client = relationship("ClientModel", back_populates="fichiers")

Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

# --- FONCTIONS ---

def fetch_siret_data(siret):
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
    except: return None
    return None

def get_geoportail_link(adresse):
    """
    Génère un lien Géoportail centré sur l'adresse.
    Couches : Orthophotos (Satellite) + Parcelles Cadastrales.
    """
    if not adresse:
        return None
        
    # 1. Géocodage via API Adresse (BAN)
    base_api = "https://api-adresse.data.gouv.fr/search/"
    params = {"q": adresse, "limit": 1}
    
    try:
        r = requests.get(base_api, params=params)
        data = r.json()
        
        if data.get("features"):
            coords = data["features"][0]["geometry"]["coordinates"]
            lon, lat = coords[0], coords[1]
            
            # 2. Construction URL Géoportail
            # l0 = Layer 0 (Fond) : ORTHOIMAGERY.ORTHOPHOTOS (Satellite)
            # l1 = Layer 1 (Dessus) : CADASTRALPARCELS.PARCELS (Cadastre)
            # opacity=1 -> 100%, opacity=0.7 -> 70%
            base_geo = "https://www.geoportail.gouv.fr/carte"
            # Syntaxe URL Géoportail : ?c=Lon,Lat&z=Zoom&l0=LAYER(opacity)&l1=LAYER(opacity)
            link = f"{base_geo}?c={lon},{lat}&z=19&l0=ORTHOIMAGERY.ORTHOPHOTOS(100)&l1=CADASTRALPARCELS.PARCELS(100)&permalink=yes"
            return link
    except:
        return None
    return None

def ajouter_client(data, fichiers_uploades):
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
    
    if fichiers_uploades:
        sauvegarder_fichiers(nouveau.id, fichiers_uploades)

def sauvegarder_fichiers(client_id, liste_fichiers):
    for fichier in liste_fichiers:
        file_path = f"{client_id}/{fichier.name}"
        try:
            fichier.seek(0)
            file_bytes = fichier.read()
            supabase.storage.from_(BUCKET_NAME).upload(
                path=file_path,
                file=file_bytes,
                file_options={"content-type": fichier.type, "x-upsert": "true"}
            )
            public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(file_path)
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
    fichier = session.query(FichierClientModel).get(fichier_id)
    if fichier:
        try:
            supabase.storage.from_(BUCKET_NAME).remove([fichier.path_storage])
        except Exception as e:
            print(f"Erreur suppression cloud: {e}")
        session.delete(fichier)
        session.commit()

def supprimer_client_entier(client_id):
    client = session.query(ClientModel).get(client_id)
    if client:
        fichiers = client.fichiers
        paths_to_remove = [f.path_storage for f in fichiers]
        if paths_to_remove:
            try:
                supabase.storage.from_(BUCKET_NAME).remove(paths_to_remove)
            except Exception as e:
                print(f"Erreur suppression fichiers: {e}")
        session.delete(client)
        session.commit()

def get_dataframe(recherche=""):
    query = session.query(ClientModel)
    if recherche:
        term = f"%{recherche}%"
        query = query.filter(
            (ClientModel.nom.ilike(term)) | (ClientModel.prenom.ilike(term)) | (ClientModel.entreprise.ilike(term))
        )
    clients = query.all()
    data = []
    for c in clients:
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
            "Nb Éclairages (Actuel)": c.nb_eclairage,
            "Nb LEDs (Préco)": c.nb_leds_preconise,
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

def clear_form_logic():
    if st.session_state.get('reset_needed'):
        text_keys = ["w_nom", "w_prenom", "w_email", "w_tel", "w_note", "w_siret_input", 
                     "w_siret_valide", "w_ent", "w_kbis", "w_travaux", "w_ecl_type"]
        for k in text_keys:
            if k in st.session_state: st.session_state[k] = ""
        float_keys = ["w_surf", "w_haut"]
        for k in float_keys:
            if k in st.session_state: st.session_state[k] = 0.0
        int_keys = ["w_ecl_puis", "w_nbecl", "w_nbled"]
        for k in int_keys:
            if k in st.session_state: st.session_state[k] = 0
        if "w_checkbox_same" in st.session_state: st.session_state["w_checkbox_same"] = False
        st.session_state['reset_needed'] = False

# --- VERIFICATIONS ---
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

def auto_copy_address():
    if st.session_state.get("w_checkbox_same", False):
        st.session_state['w_travaux'] = st.session_state.get("w_kbis", "")

def is_valid_phone(phone_str):
    if not phone_str: return True
    clean_p = re.sub(r'[\s\-\.]', '', phone_str)
    if re.match(r"^0\d{9}$", clean_p) or re.match(r"^\+33\d{9}$", clean_p):
        return True
    return False

def is_valid_email(email_str):
    if not email_str: return True
    if re.match(r"[^@]+@[^@]+\.[^@]+", email_str):
        return True
    return False

# --- INTERFACE ---

st.set_page_config(page_title="CRM V13 - Geoportail", layout="wide")

if 'reset_needed' not in st.session_state: st.session_state['reset_needed'] = False
clear_form_logic() 

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

    # 2. ENTREPRISE
    st.subheader("2. Entreprise")
    st.text_input("Nom Entreprise", key="w_ent")
    col_s1, col_s2 = st.columns([3, 1])
    col_s1.text_input("Recherche SIRET", key="w_siret_input", label_visibility="collapsed", max_chars=14)
    col_s2.button("🔍", on_click=auto_fill_siret)
    st.text_input("Adresse Siège", key="w_kbis")
    
    st.checkbox("Adresse travaux identique ?", key="w_checkbox_same", on_change=auto_copy_address)
    
    # --- ZONE ADRESSE TRAVAUX & GEOPORTAIL ---
    addr_travaux = st.text_input("Adresse Travaux", key="w_travaux")
    
    # Bouton Geoportail Conditionnel (si adresse remplie)
    if addr_travaux:
        link_geo = get_geoportail_link(addr_travaux)
        if link_geo:
            st.link_button("🗺️ Voir Cadastre & Satellite", link_geo, help="Ouvre Géoportail centré sur l'adresse")
        else:
            st.caption("⚠️ Adresse non localisée pour le plan.")
    # ----------------------------------------
            
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

    if st.button("✅ Enregistrer la fiche", type="primary"):
        nom_in = st.session_state.get("w_nom")
        email_in = st.session_state.get("w_email")
        tel_in = st.session_state.get("w_tel")

        if not nom_in:
            st.error("Le Nom est obligatoire.")
        elif not is_valid_email(email_in):
            st.error(f"Format Email invalide.")
        elif not is_valid_phone(tel_in):
            st.error(f"Numéro de téléphone invalide.")
        else:
            surf_val = str(st.session_state.get("w_surf")) if st.session_state.get("w_surf") > 0 else ""
            haut_val = str(st.session_state.get("w_haut")) if st.session_state.get("w_haut") > 0 else ""
            puis_val = str(st.session_state.get("w_ecl_puis")) if st.session_state.get("w_ecl_puis") > 0 else ""
            tel_clean = re.sub(r'[\s\-\.]', '', tel_in) if tel_in else ""

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
            st.success("Sauvegardé !")
            st.session_state['refresh'] = True
            st.rerun()

# --- PRINCIPAL ---
tab1, tab2 = st.tabs(["📊 Tableau de Bord", "📁 Gestion & Fichiers"])

with tab1:
    st.title("Suivi Clients (Cloud)")
    search = st.text_input("Filtrer le tableau...", placeholder="Nom, Ville, Entreprise...")
    if 'refresh' not in st.session_state: st.session_state['refresh'] = False
    
    df = get_dataframe(search)
    st.session_state['df_view'] = df

    if not df.empty:
        col_conf = {
            "Statut": st.column_config.SelectboxColumn(
                options=["Nouveau", "Contacté", "Devis envoyé", "En négo", "Signé", "Perdu"], required=True
            )
        }
        colonnes_verrouillees = [c for c in df.columns if c != "Statut"]
        st.data_editor(df, column_config=col_conf, disabled=colonnes_verrouillees, hide_index=True, use_container_width=True, height=600, key="main_editor", on_change=update_from_editor)
    else:
        st.info("Aucun client.")

with tab2:
    st.header("Gestion Avancée")
    all_clients = session.query(ClientModel).all()
    opts = {c.id: f"{c.nom} {c.prenom or ''} ({c.entreprise or 'Indiv'})" for c in all_clients}
    sel_id = st.selectbox("Sélectionner le client :", options=opts.keys(), format_func=lambda x: opts[x])
    
    if sel_id:
        c_edit = session.query(ClientModel).get(sel_id)
        
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
                
                # Ajout du bouton Geoportail aussi ici pour le mode Edit
                e_trav = st.text_input("Adresse Travaux", value=c_edit.adresse_travaux or "")
                if e_trav:
                     # Lien calculé pour l'édition mais affiché en dehors du form pour être cliquable
                     link_edit_geo = get_geoportail_link(e_trav)
                
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
                    elif not is_valid_email(e_email): st.error("Email invalide.")
                    elif not is_valid_phone(e_tel): st.error("Tel invalide.")
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
                        st.success("Mis à jour !")
                        st.session_state['refresh'] = True
                        st.rerun()

            # Affichage du bouton Geoportail Edit (hors du form pour éviter le reload intempestif)
            if e_trav and link_edit_geo:
                st.link_button("🗺️ Voir Cadastre (Adresse Travaux)", link_edit_geo)

        st.divider()
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
                st.caption("Vide")

        with col_ajout:
            st.subheader("Ajout")
            new_files = st.file_uploader("Upload", accept_multiple_files=True, key="new_uploads_manage")
            if st.button("Envoyer au Cloud"):
                if new_files:
                    sauvegarder_fichiers(c_edit.id, new_files)
                    st.success("Envoyé !")
                    st.session_state['refresh'] = True
                    st.rerun()

        st.divider()
        if st.button("🗑 SUPPRIMER CLIENT", type="primary"):
            supprimer_client_entier(c_edit.id)
            st.session_state['refresh'] = True
            st.rerun()