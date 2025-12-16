import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
import pandas as pd
import json
import requests
import re
import io
import unicodedata
from supabase import create_client, Client
from PIL import Image
from pypdf import PdfWriter, PdfReader

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
    categorie = Column(String)
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

def clean_filename(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    return text.replace(" ", "_").replace("/", "-")

def ajouter_client(data, uploads_dict):
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
    
    for cat, files in uploads_dict.items():
        if files:
            sauvegarder_fichiers(nouveau.id, files, cat)

def sauvegarder_fichiers(client_id, liste_fichiers, categorie):
    cat_clean = clean_filename(categorie)
    for fichier in liste_fichiers:
        nom_fic_clean = clean_filename(fichier.name)
        file_path = f"{client_id}/{cat_clean}_{nom_fic_clean}"
        try:
            fichier.seek(0)
            file_bytes = fichier.read()
            supabase.storage.from_(BUCKET_NAME).upload(path=file_path, file=file_bytes, file_options={"content-type": fichier.type, "x-upsert": "true"})
            public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(file_path)
            session.add(FichierClientModel(
                client_id=client_id, nom_fichier=fichier.name, categorie=categorie,
                path_storage=file_path, url_public=public_url
            ))
        except Exception as e: st.error(f"Erreur upload {fichier.name}: {str(e)}")
    session.commit()

def supprimer_un_fichier(fichier_id):
    fichier = session.query(FichierClientModel).get(fichier_id)
    if fichier:
        try: supabase.storage.from_(BUCKET_NAME).remove([fichier.path_storage])
        except: pass
        session.delete(fichier)
        session.commit()

def supprimer_categorie_entiere(client_id, categorie):
    fichiers = session.query(FichierClientModel).filter_by(client_id=client_id, categorie=categorie).all()
    if fichiers:
        paths = [f.path_storage for f in fichiers]
        try: supabase.storage.from_(BUCKET_NAME).remove(paths)
        except: pass
        for f in fichiers:
            session.delete(f)
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

def generer_pdf_fusionne(client_id):
    client = session.query(ClientModel).get(client_id)
    if not client: return None

    files_to_merge = []
    ordre_logique = ["Devis Signé", "Captures Géoportail", "Photos Local", "Pièces Supplémentaires"]
    
    for cat in ordre_logique:
        fichiers_cat = [f for f in client.fichiers if f.categorie == cat]
        for db_file in fichiers_cat:
            try:
                r = requests.get(db_file.url_public)
                if r.status_code == 200:
                    files_to_merge.append((db_file.nom_fichier, io.BytesIO(r.content)))
            except: pass

    if not files_to_merge: return None

    merger = PdfWriter()
    for name, file_bytes in files_to_merge:
        is_pdf = name.lower().endswith('.pdf')
        is_img = name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
        
        if is_pdf:
            try:
                reader = PdfReader(file_bytes)
                merger.append(reader)
            except: pass
        elif is_img:
            try:
                image = Image.open(file_bytes)
                if image.mode in ('RGBA', 'P'): image = image.convert('RGB')
                pdf_bytes = io.BytesIO()
                image.save(pdf_bytes, format='PDF')
                pdf_bytes.seek(0)
                merger.append(PdfReader(pdf_bytes))
            except: pass

    output = io.BytesIO()
    merger.write(output)
    merger.close()
    return output.getvalue()

def verifier_categories_completes(client_id):
    client = session.query(ClientModel).get(client_id)
    cats = {f.categorie for f in client.fichiers}
    required = {"Devis Signé", "Captures Géoportail", "Photos Local"}
    return required.issubset(cats)

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
            "Nb Éclairages": c.nb_eclairage, "Nb LEDs": c.nb_leds_preconise,
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
st.set_page_config(page_title="CRM V19 - Auto Clear", layout="wide")
if 'reset_needed' not in st.session_state: st.session_state['reset_needed'] = False
if 'uploader_key' not in st.session_state: st.session_state['uploader_key'] = 0
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
    st.text_input("Adresse Travaux", key="w_travaux")
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
    
    st.subheader("5. Fichiers (Catégories)")
    # UPLOADERS AVEC CLE DYNAMIQUE POUR LE RESET
    uk = st.session_state['uploader_key']
    up_devis = st.file_uploader("1. Devis Signé", accept_multiple_files=True, key=f"up_devis_{uk}")
    up_geo = st.file_uploader("2. Captures Géoportail", accept_multiple_files=True, key=f"up_geo_{uk}")
    up_photos = st.file_uploader("3. Photos Local/Bâtiment", accept_multiple_files=True, key=f"up_photos_{uk}")
    up_supp = st.file_uploader("4. Pièces Supplémentaires", accept_multiple_files=True, key=f"up_supp_{uk}")

    if st.button("✅ Enregistrer la fiche", type="primary"):
        nom_in = st.session_state.get("w_nom")
        if not nom_in: st.error("Nom obligatoire.")
        elif not is_valid_email(st.session_state.get("w_email")): st.error("Email invalide.")
        else:
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
            
            uploads_dict = {
                "Devis Signé": up_devis,
                "Captures Géoportail": up_geo,
                "Photos Local": up_photos,
                "Pièces Supplémentaires": up_supp
            }
            
            ajouter_client(data_client, uploads_dict)
            st.session_state['reset_needed'] = True
            st.session_state['uploader_key'] += 1 # On change la clé pour vider les champs
            st.success("Sauvegardé !")
            st.session_state['refresh'] = True
            st.rerun()

# --- TABS ---
tab1, tab2 = st.tabs(["📊 Tableau de Bord", "📁 Gestion"])

with tab1:
    st.title("Suivi Clients (Cloud)")
    search = st.text_input("Filtrer...", placeholder="Nom, Ville...")
    if 'refresh' not in st.session_state: st.session_state['refresh'] = False
    
    df = get_dataframe(search)
    st.session_state['df_view'] = df

    if not df.empty:
        col_conf = {"Statut": st.column_config.SelectboxColumn(options=["Nouveau", "Contacté", "Devis envoyé", "En négo", "Signé", "Perdu"], required=True)}
        st.data_editor(df, column_config=col_conf, disabled=[c for c in df.columns if c != "Statut"], hide_index=True, use_container_width=True, height=600, key="main_editor", on_change=update_from_editor)
    else: st.info("Vide.")

with tab2:
    st.header("Gestion Avancée")
    all_clients = session.query(ClientModel).all()
    opts = {c.id: f"{c.nom} {c.prenom or ''} ({c.entreprise or 'Indiv'})" for c in all_clients}
    sel_id = st.selectbox("Sélectionner le client à gérer :", options=opts.keys(), format_func=lambda x: opts[x]) if opts else None
    
    if sel_id:
        c_edit = session.query(ClientModel).get(sel_id)
        
        with st.expander("Modifier les informations", expanded=False):
            with st.form("edit_form"):
                e_nom = st.text_input("Nom", value=c_edit.nom or "")
                e_pre = st.text_input("Prénom", value=c_edit.prenom or "")
                e_email = st.text_input("Email", value=c_edit.email or "")
                e_tel = st.text_input("Tél", value=c_edit.telephone or "")
                e_ent = st.text_input("Entreprise", value=c_edit.entreprise or "")
                e_siret = st.text_input("SIRET", value=c_edit.siret or "")
                e_kbis = st.text_input("Adresse Siège", value=c_edit.adresse_kbis or "")
                e_trav = st.text_input("Adresse Travaux", value=c_edit.adresse_travaux or "")
                
                # DATA TECHNIQUE
                caracs_edit = {}
                if c_edit.caracteristiques_json:
                    try: caracs_edit = json.loads(c_edit.caracteristiques_json)
                    except: pass
                
                def get_float(k):
                    try: return float(caracs_edit.get(k, 0))
                    except: return 0.0
                def get_int(k):
                    try: return int(float(k))
                    except: return 0
                
                st.subheader("Technique & Comptage")
                c_tech1, c_tech2 = st.columns(2)
                e_surf = c_tech1.number_input("Superficie (m²)", value=get_float("Superficie (m²)"), step=1.0)
                e_haut = c_tech2.number_input("Hauteur (m)", value=get_float("Hauteur (m)"), step=0.1)
                e_type = st.text_input("Type Éclairage", value=caracs_edit.get("Type Éclairage", ""))
                e_puis = st.number_input("Puissance (W)", value=int(float(caracs_edit.get("Puissance (W)", 0))), step=1)
                
                c_cpt1, c_cpt2 = st.columns(2)
                e_nb = c_cpt1.number_input("Nb Actuel", value=get_int(c_edit.nb_eclairage))
                e_nb_led = c_cpt2.number_input("Nb LEDs Préco", value=get_int(c_edit.nb_leds_preconise))

                e_note = st.text_area("Note", value=c_edit.note or "")
                
                if st.form_submit_button("💾 Mettre à jour"):
                    c_edit.nom = e_nom; c_edit.prenom = e_pre; c_edit.email = e_email; c_edit.telephone = e_tel
                    c_edit.entreprise = e_ent; c_edit.siret = e_siret; c_edit.adresse_kbis = e_kbis
                    c_edit.adresse_travaux = e_trav; c_edit.note = e_note
                    c_edit.nb_eclairage = str(e_nb); c_edit.nb_leds_preconise = str(e_nb_led)
                    
                    new_caracs = {
                        "Superficie (m²)": str(e_surf) if e_surf else "",
                        "Hauteur (m)": str(e_haut) if e_haut else "",
                        "Type Éclairage": e_type,
                        "Puissance (W)": str(e_puis) if e_puis else ""
                    }
                    c_edit.caracteristiques_json = json.dumps(new_caracs)
                    
                    session.commit()
                    st.success("Mis à jour")
                    st.session_state['refresh'] = True
                    st.rerun()

        st.divider()
        st.subheader("Fichiers & Fusion")
        
        is_complet = verifier_categories_completes(c_edit.id)
        if is_complet:
            st.success("🌟 Dossier complet ! (Devis + Géoportail + Photos présents)")
            if st.button("📑 GÉNÉRER ET TÉLÉCHARGER LE DOSSIER PDF COMPLET"):
                with st.spinner("Fusion des documents et images en cours..."):
                    pdf_bytes = generer_pdf_fusionne(c_edit.id)
                    if pdf_bytes:
                        st.download_button(
                            label="⬇️ Télécharger le Dossier Fusionné (.pdf)",
                            data=pdf_bytes,
                            file_name=f"Dossier_Complet_{c_edit.nom}.pdf",
                            mime="application/pdf"
                        )
                    else:
                        st.error("Erreur génération PDF.")
        else:
            st.info("💡 Dossier incomplet pour la fusion (Manque Devis, Géoportail ou Photos).")

        # AFFICHAGE PAR CATEGORIES
        categories_ordre = ["Devis Signé", "Captures Géoportail", "Photos Local", "Pièces Supplémentaires"]
        uk = st.session_state['uploader_key'] # Recup clé dynamique
        
        for cat in categories_ordre:
            # HEADER AVEC BOUTON SUPPRIMER TOUT
            col_titre, col_del_all = st.columns([4, 1])
            col_titre.markdown(f"### 📁 {cat}")
            if col_del_all.button("🗑 Tout supprimer", key=f"del_cat_{cat}", help=f"Supprime tous les fichiers de {cat}"):
                 supprimer_categorie_entiere(c_edit.id, cat)
                 st.session_state['refresh'] = True
                 st.rerun()

            with st.expander(f"Voir/Ajouter fichiers dans {cat}", expanded=True):
                # Liste existante
                fichiers_cat = [f for f in c_edit.fichiers if f.categorie == cat]
                if fichiers_cat:
                    for f in fichiers_cat:
                        c1, c2, c3 = st.columns([4, 2, 1])
                        c1.text(f"📄 {f.nom_fichier}")
                        c2.markdown(f"[Voir]({f.url_public})")
                        if c3.button("❌", key=f"d_{f.id}"):
                            supprimer_un_fichier(f.id)
                            st.session_state['refresh'] = True
                            st.rerun()
                else:
                    st.caption("Aucun fichier.")
                
                # Upload rapide avec CLÉ DYNAMIQUE pour vidage auto
                add_files = st.file_uploader(f"Ajouter dans {cat}", accept_multiple_files=True, key=f"add_{cat}_{uk}")
                if add_files:
                    if st.button(f"Envoyer vers {cat}", key=f"btn_{cat}"):
                        sauvegarder_fichiers(c_edit.id, add_files, cat)
                        st.session_state['uploader_key'] += 1 # On vide les champs
                        st.success("Envoyé !")
                        st.session_state['refresh'] = True
                        st.rerun()

        st.divider()
        if st.button("🗑 SUPPRIMER CLIENT", type="primary"):
            supprimer_client_entier(c_edit.id)
            st.session_state['refresh'] = True
            st.rerun()
