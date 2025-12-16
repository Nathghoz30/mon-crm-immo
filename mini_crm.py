import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
import pandas as pd
import json
import requests
import re
import urllib.parse
from supabase import create_client, Client

# --- CONFIGURATION SUPABASE ---
try:
    SUPABASE_URL = st.secrets["supabase"]["url"]
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    DATABASE_URL = st.secrets["supabase"]["db_url"].replace("postgres://", "postgresql://")
except FileNotFoundError:
    st.error("Secrets introuvables.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET_NAME = "fichiers_clients"
engine = create_engine(DATABASE_URL, echo=False)
Base = declarative_base()

# --- MODELES ---
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
        if response.status_code == 200 and response.json().get('results'):
            data = response.json()['results'][0]
            return {
                "nom_complet": data.get('nom_complet'),
                "adresse": data.get('siege', {}).get('adresse'),
                "siret_clean": siret_clean
            }
    except: return None
    return None

def get_geo_links(adresse):
    """
    Génère deux liens :
    1. Géoportail "Safe Mode" (Juste les coordonnées, pas de couches qui font planter).
    2. Google Maps (Satellite) en secours.
    """
    if not adresse:
        return None, None
    
    base_api = "https://api-adresse.data.gouv.fr/search/"
    params = {"q": adresse, "limit": 1}
    
    try:
        r = requests.get(base_api, params=params)
        data = r.json()
        
        if data.get("features"):
            coords = data["features"][0]["geometry"]["coordinates"]
            lon = round(coords[0], 4)
            lat = round(coords[1], 4)
            
            # LIEN 1 : GEOPORTAIL (Mode minimaliste pour éviter l'écran gris)
            # On retire l0 et l1. On garde juste le centrage (?c=) et le zoom (?z=)
            link_geo = f"https://www.geoportail.gouv.fr/carte?c={lon},{lat}&z=17"
            
            # LIEN 2 : GOOGLE MAPS (Satellite)
            # t=k active la vue Satellite (k=hYbrid en réalité)
            link_gmaps = f"https://www.google.com/maps?q={lat},{lon}&t=k"
            
            return link_geo, link_gmaps
    except:
        return None, None
    return None, None

# ... (Fonctions DB inchangées : ajouter_client, sauvegarder_fichiers, etc.) ...
def ajouter_client(data, fichiers_uploades):
    caract_remplies = {k: v for k, v in data['caracteristiques'].items() if v}
    caract_json = json.dumps(caract_remplies) if caract_remplies else None
    nouveau = ClientModel(
        nom=data['nom'], prenom=data['prenom'], entreprise=data['entreprise'],
        siret=data['siret'], adresse_kbis=data['adresse_kbis'], adresse_travaux=data['adresse_travaux'],
        email=data['email'], telephone=data['telephone'], nb_eclairage=str(data['nb_eclairage']),
        nb_leds_preconise=str(data['nb_leds_preconise']), statut="Nouveau", note=data['note'],
        caracteristiques_json=caract_json
    )
    session.add(nouveau)
    session.commit()
    if fichiers_uploades: sauvegarder_fichiers(nouveau.id, fichiers_uploades)

def sauvegarder_fichiers(client_id, liste_fichiers):
    for fichier in liste_fichiers:
        file_path = f"{client_id}/{fichier.name}"
        try:
            fichier.seek(0)
            file_bytes = fichier.read()
            supabase.storage.from_(BUCKET_NAME).upload(path=file_path, file=file_bytes, file_options={"content-type": fichier.type, "x-upsert": "true"})
            public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(file_path)
            session.add(FichierClientModel(client_id=client_id, nom_fichier=fichier.name, path_storage=file_path, url_public=public_url))
        except Exception as e: st.error(f"Erreur upload {fichier.name}: {str(e)}")
    session.commit()

def supprimer_un_fichier(fichier_id):
    fichier = session.query(FichierClientModel).get(fichier_id)
    if fichier:
        try: supabase.storage.from_(BUCKET_NAME).remove([fichier.path_storage])
        except: pass
        session.delete(fichier)
        session.commit()

def supprimer_client_entier(client_id):
    client = session.query(ClientModel).get(client_id)
    if client:
        try: 
            paths = [f.path_storage for f in client.fichiers]
            if paths: supabase.storage.from_(BUCKET_NAME).remove(paths)
        except: pass
        session.delete(client)
        session.commit()

def get_dataframe(recherche=""):
    query = session.query(ClientModel)
    if recherche:
        term = f"%{recherche}%"
        query = query.filter((ClientModel.nom.ilike(term)) | (ClientModel.prenom.ilike(term)) | (ClientModel.entreprise.ilike(term)))
    clients = query.all()
    data = []
    for c in clients:
        c_str = ""
        if c.caracteristiques_json:
            try: c_str = ", ".join([f"{k}: {v}" for k, v in json.loads(c.caracteristiques_json).items()])
            except: pass
        data.append({
            "ID": c.id, "Statut": c.statut, "Entreprise": c.entreprise, "Nom": c.nom, "Prénom": c.prenom,
            "Nb Éclairages (Actuel)": c.nb_eclairage, "Nb LEDs (Préco)": c.nb_leds_preconise,
            "Adresse KBIS": c.adresse_kbis, "Adresse Travaux": c.adresse_travaux, "Email": c.email,
            "Téléphone": c.telephone, "SIRET": c.siret, "Caractéristiques": c_str, "Note": c.note,
            "Fichiers": f"{len(c.fichiers)} fichier(s)"
        })
    return pd.DataFrame(data)

def update_from_editor():
    changes = st.session_state.get('main_editor')
    if not changes or not changes.get('edited_rows'): return
    df = st.session_state['df_view']
    for row_idx, modifications in changes['edited_rows'].items():
        c_id = df.iloc[int(row_idx)]['ID']
        client = session.query(ClientModel).get(int(c_id))
        if client and "Statut" in modifications:
            client.statut = modifications["Statut"]
            session.commit()
    st.session_state['refresh'] = True

def clear_form_logic():
    if st.session_state.get('reset_needed'):
        for k in ["w_nom", "w_prenom", "w_email", "w_tel", "w_note", "w_siret_input", "w_siret_valide", "w_ent", "w_kbis", "w_travaux", "w_ecl_type"]:
            if k in st.session_state: st.session_state[k] = ""
        for k in ["w_surf", "w_haut"]:
            if k in st.session_state: st.session_state[k] = 0.0
        for k in ["w_ecl_puis", "w_nbecl", "w_nbled"]:
            if k in st.session_state: st.session_state[k] = 0
        if "w_checkbox_same" in st.session_state: st.session_state["w_checkbox_same"] = False
        st.session_state['reset_needed'] = False

def auto_fill_siret():
    siret_in = st.session_state.get("w_siret_input", "")
    if siret_in:
        infos = fetch_siret_data(siret_in)
        if infos:
            st.session_state['w_ent'] = infos['nom_complet']
            st.session_state['w_kbis'] = infos['adresse']
            st.session_state['w_siret_valide'] = infos['siret_clean']
            st.toast("✅ Trouvé !", icon="🏢")
        else: st.toast("❌ SIRET introuvable.", icon="⚠️")

def auto_copy_address():
    if st.session_state.get("w_checkbox_same", False):
        st.session_state['w_travaux'] = st.session_state.get("w_kbis", "")

def is_valid_phone(phone_str):
    if not phone_str: return True
    clean_p = re.sub(r'[\s\-\.]', '', phone_str)
    return bool(re.match(r"^0\d{9}$", clean_p) or re.match(r"^\+33\d{9}$", clean_p))

def is_valid_email(email_str):
    if not email_str: return True
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", email_str))

# --- INTERFACE ---
st.set_page_config(page_title="CRM V14 - Stable", layout="wide")
if 'reset_needed' not in st.session_state: st.session_state['reset_needed'] = False
clear_form_logic() 

with st.sidebar:
    st.header("Nouveau Client")
    st.subheader("1. Contact")
    c_nom, c_prenom = st.columns(2)
    c_nom.text_input("Nom *", key="w_nom")
    c_prenom.text_input("Prénom", key="w_prenom")
    st.text_input("Mail", key="w_email")
    st.text_input("Téléphone", key="w_tel")
    st.text_area("Note (Interne)", key="w_note", height=80)
    st.divider()

    st.subheader("2. Entreprise")
    st.text_input("Nom Entreprise", key="w_ent")
    col_s1, col_s2 = st.columns([3, 1])
    col_s1.text_input("Recherche SIRET", key="w_siret_input", label_visibility="collapsed", max_chars=14)
    col_s2.button("🔍", on_click=auto_fill_siret)
    st.text_input("Adresse Siège", key="w_kbis")
    st.checkbox("Adresse travaux identique ?", key="w_checkbox_same", on_change=auto_copy_address)
    
    # --- ZONE ADRESSE TRAVAUX ---
    addr_travaux = st.text_input("Adresse Travaux", key="w_travaux")
    if addr_travaux:
        link_geo, link_gmaps = get_geo_links(addr_travaux)
        if link_geo:
            col_btn1, col_btn2 = st.columns(2)
            # Bouton 1 : Geoportail Normal (Sans layers pour éviter le crash)
            col_btn1.link_button("🗺️ Géoportail", link_geo, help="Ouvre Géoportail centré. Sélectionnez 'Cadastre' dans les couches.")
            # Bouton 2 : Google Maps (Secours)
            col_btn2.link_button("🛰️ Google Maps", link_gmaps, help="Vue Satellite Google")
        else:
            st.caption("⚠️ Adresse non localisée.")
    # ---------------------------

    st.text_input("SIRET Validé", key="w_siret_valide", max_chars=14)
    st.divider()
    st.subheader("3. Technique")
    st.number_input("Superficie (m²)", min_value=0.0, step=1.0, format="%.0f", key="w_surf")
    st.number_input("Hauteur ss plafond (m)", min_value=0.0, step=0.1, format="%.2f", key="w_haut")
    st.text_input("Type Éclairage", key="w_ecl_type")
    st.number_input("Puissance (W)", min_value=0, step=1, key="w_ecl_puis")
    st.divider()
    st.subheader("4. Comptage")
    cc1, cc2 = st.columns(2)
    cc1.number_input("Nb Actuel", min_value=0, step=1, key="w_nbecl")
    cc2.number_input("Nb LEDs Préco", min_value=0, step=1, key="w_nbled")
    st.divider()
    val_files = st.file_uploader("Fichiers", accept_multiple_files=True)

    if st.button("✅ Enregistrer", type="primary"):
        nom_in = st.session_state.get("w_nom")
        if not nom_in: st.error("Nom obligatoire.")
        elif not is_valid_email(st.session_state.get("w_email")): st.error("Email invalide.")
        else:
            # (Logique sauvegarde identique...)
            surf_val = str(st.session_state.get("w_surf")) if st.session_state.get("w_surf") > 0 else ""
            haut_val = str(st.session_state.get("w_haut")) if st.session_state.get("w_haut") > 0 else ""
            puis_val = str(st.session_state.get("w_ecl_puis")) if st.session_state.get("w_ecl_puis") > 0 else ""
            caracs = {"Superficie (m²)": surf_val, "Hauteur (m)": haut_val, "Type Éclairage": st.session_state.get("w_ecl_type"), "Puissance (W)": puis_val}
            data_client = {
                "nom": nom_in, "prenom": st.session_state.get("w_prenom"), "entreprise": st.session_state.get("w_ent"),
                "siret": st.session_state.get("w_siret_valide"), "email": st.session_state.get("w_email"),
                "telephone": re.sub(r'[\s\-\.]', '', st.session_state.get("w_tel") or ""),
                "adresse_kbis": st.session_state.get("w_kbis"), "adresse_travaux": st.session_state.get("w_travaux"),
                "nb_eclairage": st.session_state.get("w_nbecl"), "nb_leds_preconise": st.session_state.get("w_nbled"),
                "note": st.session_state.get("w_note"), "caracteristiques": caracs
            }
            ajouter_client(data_client, val_files)
            st.session_state['reset_needed'] = True
            st.success("Sauvegardé !")
            st.session_state['refresh'] = True
            st.rerun()

# --- TABS ---
tab1, tab2 = st.tabs(["📊 Tableau de Bord", "📁 Gestion"])
with tab1:
    st.title("Suivi Clients (Cloud)")
    search = st.text_input("Filtrer...", placeholder="Nom, Ville...")
    df = get_dataframe(search)
    st.session_state['df_view'] = df
    if not df.empty:
        col_conf = {"Statut": st.column_config.SelectboxColumn(options=["Nouveau", "Contacté", "Devis envoyé", "En négo", "Signé", "Perdu"], required=True)}
        st.data_editor(df, column_config=col_conf, disabled=[c for c in df.columns if c != "Statut"], hide_index=True, use_container_width=True, height=600, key="main_editor", on_change=update_from_editor)
    else: st.info("Vide.")

with tab2:
    st.header("Gestion Avancée")
    opts = {c.id: f"{c.nom} {c.prenom or ''}" for c in session.query(ClientModel).all()}
    sel_id = st.selectbox("Client :", options=opts.keys(), format_func=lambda x: opts[x]) if opts else None
    
    if sel_id:
        c_edit = session.query(ClientModel).get(sel_id)
        with st.expander("Modifier", expanded=True):
            with st.form("edit_form"):
                e_nom = st.text_input("Nom", value=c_edit.nom or "")
                # ... (Reste des champs identique au code précédent)
                e_trav = st.text_input("Adresse Travaux", value=c_edit.adresse_travaux or "")
                if st.form_submit_button("💾 Mettre à jour"):
                     # ... (Logique update identique)
                     c_edit.nom = e_nom
                     # ...
                     session.commit()
                     st.success("OK")
                     st.session_state['refresh'] = True
                     st.rerun()
            
            # Boutons Geoportail aussi dans l'onglet Gestion
            if e_trav:
                lg, lgm = get_geo_links(e_trav)
                if lg:
                    c1, c2 = st.columns(2)
                    c1.link_button("🗺️ Géoportail", lg)
                    c2.link_button("🛰️ Google Maps", lgm)

        # Gestion fichiers (identique)...
        col_f, col_a = st.columns(2)
        with col_f:
             if c_edit.fichiers:
                 for f in c_edit.fichiers:
                     st.markdown(f"📄 [{f.nom_fichier}]({f.url_public})")
                     if st.button("❌", key=f"d_{f.id}"): 
                         supprimer_un_fichier(f.id)
                         st.session_state['refresh'] = True
                         st.rerun()
        with col_a:
             nf = st.file_uploader("Ajout", accept_multiple_files=True, key="up_m")
             if st.button("Envoyer") and nf:
                 sauvegarder_fichiers(c_edit.id, nf)
                 st.session_state['refresh'] = True
                 st.rerun()
        
        if st.button("🗑 SUPPRIMER", type="primary"):
            supprimer_client_entier(c_edit.id)
            st.session_state['refresh'] = True
            st.rerun()