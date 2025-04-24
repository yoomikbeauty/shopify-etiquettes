# Import des bibliothèques nécessaires
import textwrap
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import streamlit as st  # pour l'interface web
import requests  # pour faire des requêtes HTTP vers l'API Shopify
import pandas as pd  # pour manipuler les données sous forme de tableaux
import time  # pour ajouter des pauses entre les requêtes
import re  # pour lire la pagination
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from io import BytesIO

# Configuration de la page Streamlit
st.set_page_config(page_title="Shopify Product Viewer", layout="wide")

# Définir la couleur de fond avec du CSS inline
st.markdown("""
    <style>

    .st-emotion-cache-1r4qj8v {
        background: #ec7f82 !important;
        color: rgb(39 48 90);
    }
    .st-emotion-cache-4uzi61 {
        background-color: #ffe3e3 !important;
        border: 1px solid rgba(255, 255, 255, 0.4);
        border-radius: 0.5rem;
        padding: 1rem;
    }
    .st-emotion-cache-8atqhb {
        width: 100%;
        text-align: -webkit-center;
    }
            
.st-emotion-cache-1h9usn1 {

    background: #ffffff;
}
                        .st-b7 {
    background-color: rgb(255 255 255);
}
.stAlertContainer.st-ax.st-ay.st-az.st-b0.st-b1.st-b2.st-di.st-dj.st-dk.st-dl.st-dm.st-dn.st-do.st-ce.st-cd.st-dp.st-at.st-av.st-aw.st-au.st-dq.st-dw.st-dr.st-ar.st-ds.st-af.st-dt.st-du.st-d2.st-dv {
    color: white;
    background-color: rgb(39 48 90) !important;
}
.stDownloadButton {
    width: 100%;
    text-align: -webkit-center;
}
</style>
""", unsafe_allow_html=True)

# Enregistrement des polices personnalisées
pdfmetrics.registerFont(TTFont("NotoSans-Italic", "NotoSans-Italic.ttf"))
pdfmetrics.registerFont(TTFont("AdobeSansMM", "adobe-sans-mm.ttf"))
pdfmetrics.registerFont(TTFont("IbarraRealNova-Bold", "IbarraRealNova-Bold.ttf"))
pdfmetrics.registerFont(TTFont("IbarraRealNova-Regular", "IbarraRealNova-Regular.ttf"))
pdfmetrics.registerFont(TTFont("IbarraRealNova-SemiBold", "IbarraRealNova-SemiBold.ttf"))

# Nettoyage doublon ou ancien code inutile

# Titre principal affiché sur la page
st.image("logo.png", width=250)
st.markdown("<h1 style='text-align:center'>Créateur de carte via Shopify</h1>", unsafe_allow_html=True)



# Bouton pour mettre à jour les données Shopify
import os

# Zone d'authentification et mise à jour en bas
with st.expander("🔧 Paramètres API et mise à jour Shopify"):
    with st.form("auth_form"):
        shop_url = st.text_input("Nom de la boutique Shopify", value="0rynik-1k.myshopify.com")
        access_token = st.text_input("Token d'accès API privé", type="password")
        mode_complet = st.checkbox("Inclure les métadonnées personnalisées (plus lent)", value=True)
        only_recent = st.checkbox("Afficher uniquement les 50 derniers produits ajoutés")
        submitted = st.form_submit_button("Se connecter et récupérer les produits")

    if submitted:
        st.session_state.shop_url = shop_url
        st.session_state.access_token = access_token
        st.session_state.mode_complet = mode_complet
        st.session_state.only_recent = only_recent

# Chargement initial depuis fichier CSV si existant
if "df" not in st.session_state:
    if os.path.exists("produits_shopify.csv"):
        st.session_state['df'] = pd.read_csv("produits_shopify.csv")
        st.success("Base produits chargée depuis le fichier local.")

# Mise à jour manuelle
if st.button("Mettre à jour la base produits depuis Shopify"):
    st.info("Connexion à Shopify...")

    headers = {
        "X-Shopify-Access-Token": access_token
    }

    base_url = f"https://{shop_url}/admin/api/2023-10/products.json"
    metafield_url_template = f"https://{shop_url}/admin/api/2023-10/products/{{product_id}}/metafields.json"

    products = []
    page_info = None

    with st.spinner("Chargement des produits..."):
        params = {"limit": 50, "order": "created_at desc"} 

        while True:
            response = requests.get(base_url, headers=headers, params=params)
            response.raise_for_status()
            batch = response.json().get("products", [])
            products.extend([p for p in batch if p.get("status") == "active"])

            if only_recent:
                break

            link_header = response.headers.get("Link")
            if link_header and 'rel="next"' in link_header:
                match = re.search(r'<[^>]*[?&]page_info=([^&>]*)[^>]*>; rel="next"', link_header)
                if match:
                    page_info = match.group(1)
                    params = {"limit": 250, "page_info": page_info}
                else:
                    break
            else:
                break

    if not products:
        st.warning("Aucun produit trouvé.")
    else:
        data = []
        if mode_complet:
            metafield_keys = [
                "moyenne_description","taille","ingredients", "routine",
                "info_bestseller", "info_cruelty_free", "info_vegan", "info_clean_beauty",
                "tout_type", "peau_grasse", "peau_mature", "peau_seche", "peau_sensible", "peau_acneique"
                
            ]

            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, p in enumerate(products):
                product_id = p.get("id")
                title = p.get("title")
                status_text.text(f"Récupération des métadonnées pour : {title} (ID {product_id})")
                meta_fail = False

                time.sleep(0.5)
                metafields_response = requests.get(metafield_url_template.format(product_id=product_id), headers=headers)
                metafields = metafields_response.json().get("metafields", [])
                metafield_data = {key: "" for key in metafield_keys}

                for meta in metafields:
                    key = meta.get("key")
                    if meta.get("namespace") == "custom" and key in metafield_keys:
                        value = meta.get("value")
                        # Vérification + Retry si value vide
                        retry_count = 0
                        while (value is None or value == "") and retry_count < 3:
                            time.sleep(0.7)
                            retry_response = requests.get(metafield_url_template.format(product_id=product_id), headers=headers)
                            retry_meta = retry_response.json().get("metafields", [])
                            for retry_item in retry_meta:
                                if retry_item.get("namespace") == "custom" and retry_item.get("key") == key:
                                    value = retry_item.get("value")
                                    break
                            retry_count += 1
                        if value:
                            metafield_data[key] = str(value)
                        else:
                            meta_fail = True

                data.append({
                    "ID": p.get("id"),
                    "Vendor": p.get("vendor"),
                    "Title": title,
                    "Type": p.get("product_type"),
                    "Variant Price": p.get("variants", [{}])[0].get("price"),
                    **{f"custom.{key}": metafield_data[key] for key in metafield_keys}
                })
                progress_bar.progress((i + 1) / len(products))
            status_text.text("Récupération terminée.")
            if meta_fail:
                st.warning(f"⚠️ Certains produits n'ont pas toutes leurs métadonnées. Veuillez vérifier manuellement.")

        else:
            for p in products:
                data.append({
                    "Vendor": p.get("vendor"),
                    "Title": p.get("title"),
                    "Type": p.get("product_type"),
                    "Variant Price": p.get("variants", [{}])[0].get("price")
                })

        data = sorted(data, key=lambda x: x["ID"], reverse=True)
        df = pd.DataFrame(data)
        df.to_csv("produits_shopify.csv", index=False)
        st.session_state['df'] = df
        st.success(f"{len(df)} produits récupérés et enregistrés dans 'produits_shopify.csv'.")

# Affichage + export CSV + sélection PDF si données présentes
if 'df' in st.session_state:
    df = st.session_state['df']
    st.dataframe(df, use_container_width=True)

    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="Télécharger en CSV",
        data=csv,
        file_name="produits_shopify.csv",
        mime="text/csv",
    )

    # Choix des produits à étiqueter
    st.markdown("### Étiquettes à imprimer")
    df['label'] = df['Vendor'] + ' - ' + df['Title'] + df['custom.taille'].apply(lambda x: f" ({x})" if pd.notna(x) and str(x).strip() != '' else '')
    df = df.sort_values('ID', ascending=False)  # Tri par ID Shopify (plus récent en haut)
    df = df.reset_index(drop=True)
    selected_labels = st.multiselect("Sélectionnez les produits à imprimer : (8 max par page)", options=df['label'].tolist(),
    placeholder="Choisissez un ou plusieurs produits...")
    filtered_df = df[df['label'].isin(selected_labels)].reset_index(drop=True)
    

    if st.button("Générer les étiquettes PDF (8 par page)") and not filtered_df.empty:
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)

        width, height = A4
        margin_x, margin_y = 19 * mm, 38.52 * mm
        label_width, label_height = 86 * mm, 55 * mm

        for i, row in filtered_df.iterrows():
            if i > 0 and i % 8 == 0:
                c.showPage()

            col = i % 2
            row_pos = (i // 2) % 4
            x = margin_x + col * label_width
            y = height - margin_y - (row_pos + 1) * label_height

    
            
            

            # CADRE de l'étiquette
            c.setFont("Helvetica", 8)
            
            c.setStrokeColorRGB(0, 0, 0)
            c.rect(x, y, label_width, label_height, stroke=0, fill=0)
  



            # VENDOR
            c.setFont("IbarraRealNova-SemiBold", 11.5)
            if 'Vendor' in row:
                vendor_text = c.beginText()
                vendor_text.setTextOrigin(x + 2.5 * mm, y + label_height - 5 * mm)
                wrapped_vendor = textwrap.wrap(row['Vendor'], width=28)
                for line in wrapped_vendor[:2]:
                    vendor_text.textLine(line)
                c.drawText(vendor_text)

            # Séparateur après Vendor
            c.setLineWidth(0.1)
            c.line(x + 0 * mm, y + label_height - 6.5 * mm, x + label_width - 0 * mm, y + label_height - 6.5 * mm)

            # TAILLE
            taille_val = str(row.get('custom.taille', '')).strip()
            if taille_val and taille_val.lower() != 'nan':
                c.setFont("IbarraRealNova-SemiBold", 11.5)
                c.drawRightString(x + label_width - 2.5 * mm, y + label_height - 5 * mm, taille_val)

            # TITRE
            c.setFont("IbarraRealNova-Bold", 14)
            if 'Title' in row:
                wrapped_title = textwrap.wrap(row['Title'], width=30)
                line_height = 13
                total_lines = len(wrapped_title[:2])
                for idx, line in enumerate(wrapped_title[:2]):
                    text_width = pdfmetrics.stringWidth(line, "IbarraRealNova-Bold", 14)
                    # Si une seule ligne, centré verticalement
                    y_offset = y + label_height - 14 * mm if total_lines == 1 else y + label_height - 12 * mm - (idx * line_height)
                    c.drawString(x + (label_width - text_width) / 2, y_offset, line)

            # Séparateur après Titre
            c.setLineWidth(0.1)
            c.line(x + 0 * mm, y + label_height - 18 * mm, x + label_width - 0 * mm, y + label_height - 18 * mm)



            # DESCRIPTION
            if 'custom.moyenne_description' in row:
                c.setFont("AdobeSansMM", 7.5)
                desc = str(row['custom.moyenne_description'])
                line_height = 10.2
                for idx, line in enumerate(textwrap.wrap(desc, width=61.5)):
                    c.drawString(x + 2.5 * mm, y + label_height - 22 * mm - (idx * line_height), line)

    # Séparateur après Description (gris)
            c.setLineWidth(0.1)
            c.setStrokeColorRGB(0.7, 0.7, 0.7)
            c.line(x + 0 * mm, y + label_height - 38 * mm, x + label_width - 0 * mm, y + label_height - 38 * mm)
            c.setStrokeColorRGB(0, 0, 0)
                    


            # ROUTINE (étape)
            routine_val = str(row.get('custom.routine', '')).strip()
            if routine_val and routine_val.lower() != 'nan':
                c.setFont("IbarraRealNova-Regular", 7)
                c.setFillColorRGB(0.4, 0.4, 0.4)  # Gris
                c.drawRightString(x + label_width - 2.5 * mm, y + 9 * mm, f"Étape n° {routine_val}")
                c.setFillColorRGB(0, 0, 0)  # Réinitialise la couleur


            # ICONES CONDITIONNELLES
            icon_size = 12 * mm
            icon_y = y + label_height - 53 * mm
            icon_x = x + 2 * mm
            if str(row.get('custom.info_vegan', '')).lower() == 'true':
                try:
                    c.drawImage("vegan.png", icon_x, icon_y, width=icon_size, height=icon_size, preserveAspectRatio=True)
                    icon_x += icon_size + 0
                except Exception as e:
                    c.setFillColorRGB(1, 0, 0)
                    c.rect(icon_x, icon_y, icon_size, icon_size, fill=1)
                    icon_x += icon_size + 0
            if str(row.get('custom.info_cruelty_free', '')).lower() == 'true':
                try:
                    c.drawImage("cruelty.png", icon_x, icon_y, width=icon_size, height=icon_size, preserveAspectRatio=True)
                    icon_x += icon_size + 0
                except Exception as e:
                    c.setFillColorRGB(1, 0, 0)
                    c.rect(icon_x, icon_y, icon_size, icon_size, fill=1)
                    icon_x += icon_size + 0
            if str(row.get('custom.info_clean_beauty', '')).lower() == 'true':
                try:
                    c.drawImage("clean.png", icon_x, icon_y, width=icon_size, height=icon_size, preserveAspectRatio=True)
                    icon_x += icon_size + 0
                except Exception as e:
                    c.setFillColorRGB(1, 0, 0)
                    c.rect(icon_x, icon_y, icon_size, icon_size, fill=1)
                    icon_x += icon_size + 0

            # TYPE DE PEAU
            types = []
            if row['Type'] == 'P':
                if str(row.get('custom.tout_type', '')).lower() in ['true', '1']: types.append("• tout type")
                if str(row.get('custom.peau_acneique', '')).lower() in ['true', '1']: types.append("• acnéique")
                if str(row.get('custom.peau_grasse', '')).lower() in ['true', '1']: types.append("• grasse")
                if str(row.get('custom.peau_seche', '')).lower() in ['true', '1']: types.append("• sèche")
                if str(row.get('custom.peau_sensible', '')).lower() in ['true', '1']: types.append("• sensible")
                if str(row.get('custom.peau_mature', '')).lower() in ['true', '1']: types.append("• mature")
                label = "Type de peau :"
            elif row['Type'] == 'C':
                if str(row.get('custom.tout_type', '')).lower() in ['true', '1']: types.append("• tout type")
                if str(row.get('custom.peau_grasse', '')).lower() in ['true', '1']: types.append("• gras")
                if str(row.get('custom.peau_seche', '')).lower() in ['true', '1']: types.append("• sec")
                if str(row.get('custom.peau_sensible', '')).lower() in ['true', '1']: types.append("• sensible")
                label = "Type de cheveux :"
            else:
                types = []
                label = ""
            if types:
                c.setFont("NotoSans-Italic", 9)
                line = f"{label} {' '.join(types)}"
                c.drawRightString(x + label_width - 2.5 * mm, y + label_height - 42 * mm, line)


            c.setFillColorRGB(0, 0, 0)  # Réinitialiser la couleur pour le texte


            # PRIX
            if 'Variant Price' in row:
                c.setFont("IbarraRealNova-Bold", 20)
                prix = float(row['Variant Price'])
                c.drawRightString(x + label_width - 2.5 * mm, y + 3 * mm, f"{prix:.2f}".replace('.', ',') + " €")



                
        c.save()

        buffer.seek(0)
        st.download_button(
            label="Télécharger les étiquettes en PDF",
            data=buffer.getvalue(),
            file_name="etiquettes_shopify.pdf",
            mime="application/pdf",
        )


