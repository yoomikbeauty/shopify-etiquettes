# Import des bibliothèques nécessaires
import textwrap
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from docx import Document
from docx.shared import Pt, Inches
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
import streamlit as st  # pour l'interface web
import requests  # pour faire des requêtes HTTP vers l'API Shopify
import pandas as pd  # pour manipuler les données sous forme de tableaux
import time  # pour ajouter des pauses entre les requêtes
import re  # pour lire la pagination
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from io import BytesIO


from html.parser import HTMLParser

class DocxHTMLParser(HTMLParser):
    def __init__(self, paragraph):
        super().__init__()
        self.paragraph = paragraph
        self.bold = False
        self.italic = False
        self.underline = False

    def handle_starttag(self, tag, attrs):
        if tag == 'b':
            self.bold = True
        elif tag == 'i':
            self.italic = True
        elif tag == 'u':
            self.underline = True

    def handle_endtag(self, tag):
        if tag == 'b':
            self.bold = False
        elif tag == 'i':
            self.italic = False
        elif tag == 'u':
            self.underline = False

    def handle_data(self, data):
        run = self.paragraph.add_run(data)
        run.bold = self.bold
        run.italic = self.italic
        run.underline = self.underline

class PDFTextHTMLParser(HTMLParser):
    def __init__(self, canvas, x, y, font, font_size):
        super().__init__()
        self.c = canvas
        self.x = x
        self.y = y
        self.font = font
        self.font_size = font_size
        self.bold = False
        self.x_cursor = x

    def handle_starttag(self, tag, attrs):
        if tag == 'b':
            self.bold = True

    def handle_endtag(self, tag):
        if tag == 'b':
            self.bold = False

    def handle_data(self, data):
        if self.bold:
            self.c.setFont(self.font + "T", self.font_size)
        else:
            self.c.setFont(self.font, self.font_size)

        self.c.drawString(self.x_cursor, self.y, data)
        text_width = self.c.stringWidth(data, self.font + ("T" if self.bold else ""), self.font_size)
        self.x_cursor += text_width

def draw_box_rich(c, x, y, width, height, html_text, font="BellCentennial", font_size=7, padding=2):
    import textwrap

    c.setLineWidth(0.3)
    c.setStrokeColorRGB(1, 0, 0)
    c.rect(x, y, width, height)

    plain_text = html_text.replace("<b>", "").replace("</b>", "")
    line_height = font_size + 1.5
    max_fit_lines = int(height // line_height)
    wrapped = textwrap.wrap(plain_text, width=int((width - padding * 2) / (font_size * 0.5)))
    wrapped = wrapped[:max_fit_lines]

    total_text_height = len(wrapped) * line_height
    start_y = y + (height - total_text_height) / 2 + total_text_height - line_height

    for line in wrapped:
        parser = PDFTextHTMLParser(c, x + padding, start_y, font, font_size)
        parser.feed(html_text)  # rend le texte avec balises <b>
        start_y -= line_height

def wrap_and_draw(text, start_y, x=6, font_size=4.5, font="BellCentennial", max_width=130):
    """
    Affiche du texte ligne par ligne à partir de x, y. Retourne y final.
    """
    line_height = font_size + 1.5
    c.setFont(font, font_size)

    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        test_line = f"{current_line} {word}".strip()
        if c.stringWidth(test_line, font, font_size) > max_width:
            lines.append(current_line.strip())
            current_line = word
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line.strip())

    for line in lines:
        c.drawString(x, start_y, line)
        start_y -= line_height

    return start_y

PRECAUTION_DEFAULT = (
    "<b>Avertissement!</b> Usage externe uniquement. Éviter tout contact avec les yeux. "
    "Tenir hors de portée des enfants. En cas d'apparition de rougeurs, de gonflements ou de démangeaisons pendant ou après l'utilisation, consultez un médecin. "
    "<br><b>A consommer de préférence avant le / Numéro de lot :</b> indiqué sur l'emballage."
)

INFO_BLOCK_TEMPLATE = (
    "<b>Fabricant :</b> {vendor}<br>"
    "<b>EU RP :</b> Emmanuelle Kueny - Yoomi k-beauty, 19 rue merciere, 68100 Mulhouse, France - 03 65 67 40 62 - SIREN 932 945 256<br>"
    "<b>Fabriqué en Corée</b>"
)

@st.cache_data(ttl=300)
def preparer_stock_csv(csv_path_or_obj, shop_url, access_token):
    df_fournisseur = pd.read_csv(csv_path_or_obj)

    def extraire_barcode(nom):
        match = re.search(r'barcode[\s:-]*([\d]{8,14})', str(nom), re.IGNORECASE)
        return match.group(1) if match else None

    df_fournisseur['Barcode'] = df_fournisseur['Product Name'].apply(extraire_barcode)

    df_fournisseur['Qty'] = (
        df_fournisseur['Qty']
        .astype(str)
        .str.extract(r'(\d+)')
        .fillna(0)
        .astype(int)
    )

    headers = {"X-Shopify-Access-Token": access_token}
    df_variants = get_all_shopify_variants(shop_url, access_token)
    df_merged = pd.merge(df_fournisseur, df_variants, on="Barcode", how="left")

    # 📍 Récupération emplacement (1 seule fois)
    loc_resp = requests.get(f"https://{shop_url}/admin/api/2023-10/locations.json", headers=headers)
    if loc_resp.ok:
        location_id = loc_resp.json()["locations"][0]["id"]
    else:
        location_id = None

    stock_actuels = []
    for i, row in df_merged.iterrows():
        if pd.isna(row["Inventory Item ID"]) or location_id is None:
            stock_actuels.append(None)
            continue
        time.sleep(0.6)  # protection quota
        inv_url = f"https://{shop_url}/admin/api/2023-10/inventory_levels.json"
        params = {"inventory_item_ids": int(row["Inventory Item ID"]), "location_ids": location_id}
        inv_resp = requests.get(inv_url, headers=headers, params=params)
        if inv_resp.ok:
            inv_data = inv_resp.json().get("inventory_levels", [])
            stock_actuels.append(inv_data[0]["available"] if inv_data else 0)
        else:
            stock_actuels.append(None)

    df_merged["Stock actuel"] = stock_actuels
    df_merged["location_id"] = location_id
    return df_merged


# Configuration de la page Streamlit
st.set_page_config(page_title="Shopify Product Viewer", layout="wide")

# Définir la couleur de fond avec du CSS inline


# Enregistrement des polices personnalisées
pdfmetrics.registerFont(TTFont("NotoSans-Italic", "fonts/NotoSans-Italic.ttf"))
pdfmetrics.registerFont(TTFont("AdobeSansMM", "fonts/adobe-sans-mm.ttf"))
pdfmetrics.registerFont(TTFont("IbarraRealNova-Bold", "fonts/IbarraRealNova-Bold.ttf"))
pdfmetrics.registerFont(TTFont("IbarraRealNova-Regular", "fonts/IbarraRealNova-Regular.ttf"))
pdfmetrics.registerFont(TTFont("IbarraRealNova-SemiBold", "fonts/IbarraRealNova-SemiBold.ttf"))
pdfmetrics.registerFont(TTFont("BellCentennial", "fonts/BellCentennialStd-Address.ttf"))
pdfmetrics.registerFont(TTFont("BellCentennialT", "fonts/BellCentennialStd-NameNum.ttf"))



# Titre principal affiché sur la page
st.image("images/logo.png", width=250)
st.markdown("<h1 style='text-align:center'>Créateur de carte YOOMI</h1>", unsafe_allow_html=True)


tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(["Base de données", "Étiquettes prix", "Étiquettes de traduction Fournisseur", "Étiquettes de traduction Boutique (beta)", "📦 Stock fournisseur", "📦 Gestion stock manuels", "💸 Gestion Soldes" ])



with tab1:
    st.markdown("## Base de données produits")
    # Bouton pour mettre à jour les données Shopify
    with st.expander("🛠 Paramètres de récupération de la base de données"):
        import os
        # 👉 Shopify credentials via Streamlit Cloud secrets
        shop_url = st.secrets["shopify"]["shop_url"]
        access_token = st.secrets["shopify"]["access_token"]
        mode_complet = st.checkbox("Inclure les métadonnées personnalisées (plus lent)", value=True)
        only_recent = st.checkbox("Afficher uniquement les 50 derniers produits ajoutés")
        force_update = st.checkbox("🔁 Forcer une mise à jour complète (ignorer les dates)", value=False)
        st.session_state.force_update = force_update
        st.success(f"🔐 Connecté à {shop_url}")

    # Chargement initial depuis fichier CSV si existant
    if os.path.exists("data/produits_shopify.csv"):
        try:
            df_temp = pd.read_csv("data/produits_shopify.csv")
            if "updated_at" in df_temp.columns:
                last_update_display = pd.to_datetime(df_temp["updated_at"], errors="coerce").max()
                total_count = len(df_temp)
                st.markdown(f"<div style='text-align:center; margin-top:10px; font-size:14px; color:#333;'>❤️ Dernière mise à jour : <b>{last_update_display.strftime('%d/%m/%Y %H:%M')}</b> — {total_count} produits enregistrés</div>", unsafe_allow_html=True)
        except:
            pass
    if "df" not in st.session_state:
        if os.path.exists("data/produits_shopify.csv"):
            st.session_state['df'] = pd.read_csv("data/produits_shopify.csv")
            st.success("Base produits chargée depuis le fichier local.")

    # Mise à jour manuelle
    last_updated = None
    if os.path.exists("data/produits_shopify.csv"):
            try:
                old_df = pd.read_csv("data/produits_shopify.csv")
                if "updated_at" in old_df.columns:
                    last_updated = pd.to_datetime(old_df["updated_at"], errors="coerce").max()
            except:
                last_updated = None
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
            params = {"limit": 250, "order": "updated_at asc"}
            if last_updated is not None and not st.session_state.get('force_update', False):
                params["updated_at_min"] = last_updated.isoformat() 

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
            all_new_data = []
            if mode_complet:
                metafield_keys = [
                    "mini_description", "moyenne_description", "utilisation", "taille","ingredients", "routine",
                    "info_bestseller", "info_cruelty_free", "info_vegan", "info_clean_beauty",
                    "tout_type", "peau_grasse", "peau_mature", "peau_seche", "peau_sensible", "peau_acneique", "periode_mois", "texte_recyclage"
                    
                ]

                progress_bar = st.progress(0)
                status_text = st.empty()

                for i, p in enumerate(products):
                    product_id = p.get("id")
                    title = p.get("title")
                    status_text.text(f"Récupération des métadonnées pour : {title} (ID {product_id})")
                    meta_fail = False

                    time.sleep(0.8)
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

                    all_new_data.append({
                        "ID": p.get("id"),
                        "updated_at": p.get("updated_at"),
                        "Vendor": p.get("vendor"),
                        "Title": title,
                        "Type": p.get("product_type"),
                        "Variant Price": p.get("variants", [{}])[0].get("price"),
                        "Variant Compare Price": p.get("variants", [{}])[0].get("compare_at_price"),
                        "Variant Barcode": p.get("variants", [{}])[0].get("barcode"),
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
                        "Variant Price": p.get("variants", [{}])[0].get("price"),
                        "Variant Barcode": p.get("variants", [{}])[0].get("barcode")
                    })

            data = sorted(data, key=lambda x: x["ID"], reverse=True)
            df = pd.DataFrame(all_new_data)
            if os.path.exists("data/produits_shopify.csv"):
                try:
                    old_df = pd.read_csv("data/produits_shopify.csv")
                    combined_df = pd.concat([old_df, df], ignore_index=True)
                    df = combined_df.drop_duplicates(subset="ID", keep="last")
                except:
                    pass
            df.to_csv("data/produits_shopify.csv", index=False)
            st.session_state['df'] = df
            st.success(f"{len(df)} produits récupérés et enregistrés dans 'data/produits_shopify.csv'.")

    # Affichage + export CSV + sélection PDF si données présentes
    if 'df' in st.session_state:
        df = st.session_state['df']
        with st.expander("### 🔍 Filtrer à partir d’un fichier de commande"):


            commande_file = st.file_uploader("Uploader un fichier CSV de commande", type=["csv"])
            if commande_file:
                try:
                    df_commande = pd.read_csv(commande_file)

                    # Extraction du barcode depuis le Product Name
                    def extraire_barcode(nom):
                        match = re.search(r'barcode[\s:-]*([\d]{8,14})', str(nom), re.IGNORECASE)
                        return match.group(1) if match else None

                    df_commande['extracted_barcode'] = df_commande['Product Name'].apply(extraire_barcode)
                    barcodes_commande = df_commande['extracted_barcode'].dropna().unique().tolist()

                    if len(barcodes_commande) > 0:
                        df_barcodes = df['Variant Barcode'].astype(str)
                        barcodes_trouves = df_barcodes[df_barcodes.isin(barcodes_commande)].unique().tolist()
                        barcodes_non_trouves = sorted(set(barcodes_commande) - set(barcodes_trouves))

                        df = df[df['Variant Barcode'].astype(str).isin(barcodes_commande)]
                        st.success(f"{len(df)} produits trouvés avec les barcodes extraits ({len(barcodes_commande)} attendus).")

                    if barcodes_non_trouves:
                        lignes_non_trouvees = df_commande[df_commande['extracted_barcode'].isin(barcodes_non_trouves)]
                        with st.expander("⚠️ Produits non trouvés dans Shopify (clique pour afficher)"):
                            st.write(f"{len(barcodes_non_trouves)} articles ignorés car non trouvés dans la base :")
                            for _, row in lignes_non_trouvees.iterrows():
                                st.markdown(f"- `{row['Product Name']}`")
                    else:
                        st.warning("Aucun code barre valide n’a été extrait.")
                except Exception as e:
                    st.error(f"Erreur lors du traitement du fichier : {e}")

        st.dataframe(df, use_container_width=True)   

        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Télécharger en CSV",
            data=csv,
            file_name="data/produits_shopify.csv",
            mime="text/csv",
        )



        with tab2:
            st.markdown("## Création d’étiquettes prix")
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
                            c.drawImage("images/vegan.png", icon_x, icon_y, width=icon_size, height=icon_size, preserveAspectRatio=True)
                            icon_x += icon_size + 0
                        except Exception as e:
                            c.setFillColorRGB(1, 0, 0)
                            c.rect(icon_x, icon_y, icon_size, icon_size, fill=1)
                            icon_x += icon_size + 0
                    if str(row.get('custom.info_cruelty_free', '')).lower() == 'true':
                        try:
                            c.drawImage("images/cruelty.png", icon_x, icon_y, width=icon_size, height=icon_size, preserveAspectRatio=True)
                            icon_x += icon_size + 0
                        except Exception as e:
                            c.setFillColorRGB(1, 0, 0)
                            c.rect(icon_x, icon_y, icon_size, icon_size, fill=1)
                            icon_x += icon_size + 0
                    if str(row.get('custom.info_clean_beauty', '')).lower() == 'true':
                        try:
                            c.drawImage("images/clean.png", icon_x, icon_y, width=icon_size, height=icon_size, preserveAspectRatio=True)
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


                    # PRIX AVEC PRIX BARRÉ SI DISPONIBLE
                    try:
                        prix = float(row['Variant Price'])
                        prix_str = f"{prix:.2f}".replace('.', ',') + "€"

                        compare_price = row.get('Variant Compare Price', None)
                        affiche_compare = False

                        if compare_price not in [None, '', 'nan']:
                            try:
                                compare_price_float = float(compare_price)
                                if compare_price_float > prix:
                                    affiche_compare = True
                                    # Affichage du prix barré en gris au-dessus du prix actuel
                                    c.setFont("IbarraRealNova-Regular", 14)
                                    compare_price_str = f"{compare_price_float:.2f}".replace('.', ',') + "€"
                                    text_width = pdfmetrics.stringWidth(compare_price_str, "IbarraRealNova-Regular", 10)
                                    compare_price_x = x + label_width - 30 * mm - text_width
                                    compare_price_y = y + 3 * mm

                                    # Texte gris
                                    c.setFillColorRGB(0, 0, 0)
                                    c.drawString(compare_price_x, compare_price_y, compare_price_str)

                                    # Ligne barrée
                                    c.setLineWidth(0.5)
                                    c.line(compare_price_x, compare_price_y + 4, compare_price_x + text_width, compare_price_y + 4)

                                    c.setFillColorRGB(0, 0, 0)  # reset color
                            except:
                                pass  # en cas de mauvaise donnée de compare price

                        # Prix actuel affiché en grand dans tous les cas
                        c.setFont("IbarraRealNova-Bold", 20)
                        if affiche_compare:
                            # Si soldé, affichage en rouge
                            c.setFillColorRGB(1, 0, 0)
                        else:
                            # Sinon, noir
                            c.setFillColorRGB(0, 0, 0)

                        c.drawRightString(x + label_width - 2.5 * mm, y + 3 * mm, prix_str)
                        c.setFillColorRGB(0, 0, 0)  # reset après écriture
                    except Exception as e:
                        c.setFont("Helvetica", 8)
                        c.drawString(x + 2 * mm, y + 3 * mm, f"Erreur prix: {e}")


                        
                c.save()

                buffer.seek(0)
                st.download_button(
                    label="Télécharger les étiquettes en PDF",
                    data=buffer.getvalue(),
                    file_name="etiquettes_shopify.pdf",
                    mime="application/pdf",
                )


# === 📦 MISE À JOUR STOCK FOURNISSEUR =======================
with tab5:
    st.markdown("## Mise à jour du stock via bon de commande fournisseur (STYLE KOREAN)")
    csv_fournisseur = st.file_uploader("📁 Uploader le fichier CSV fournisseur", type=["csv"])
    
    # Fonction de prétraitement + cache
    @st.cache_data(ttl=600)
    def preparer_stock_csv(csv_file, shop_url, access_token):
        df_fournisseur = pd.read_csv(csv_file)

        def extraire_barcode(nom):
            match = re.search(r'barcode[\s:-]*([\d]{8,14})', str(nom), re.IGNORECASE)
            return match.group(1) if match else None

        df_fournisseur['Barcode'] = df_fournisseur['Product Name'].apply(extraire_barcode)

        df_fournisseur['Qty'] = (
            df_fournisseur['Qty']
            .astype(str)
            .str.extract(r'(\d+)')
            .fillna(0)
            .astype(int)
        )

        headers = {"X-Shopify-Access-Token": access_token}

        # Récupération des variantes Shopify
        def get_all_variants():
            all_variants = []
            url = f"https://{shop_url}/admin/api/2023-10/products.json?limit=250"
            while url:
                resp = requests.get(url, headers=headers)
                resp.raise_for_status()
                products = resp.json().get("products", [])
                for p in products:
                    for v in p.get("variants", []):
                        all_variants.append({
                            "Product Title": p["title"],
                            "Variant Title": v["title"],
                            "Barcode": v.get("barcode"),
                            "Variant ID": v["id"],
                            "Inventory Item ID": v["inventory_item_id"]
                        })
                link = resp.headers.get("Link", "")
                if 'rel="next"' in link:
                    match = re.search(r'<([^>]+)>; rel="next"', link)
                    url = match.group(1) if match else None
                else:
                    url = None
            return pd.DataFrame(all_variants)

        df_variants = get_all_variants()
        df_merged = pd.merge(df_fournisseur, df_variants, on="Barcode", how="left")

        # Récupération location
        loc_resp = requests.get(f"https://{shop_url}/admin/api/2023-10/locations.json", headers=headers)
        if loc_resp.ok:
            location_id = loc_resp.json()["locations"][0]["id"]
        else:
            location_id = None

        stock_actuels = []
        for i, row in df_merged.iterrows():
            if pd.isna(row["Inventory Item ID"]) or location_id is None:
                stock_actuels.append(None)
                continue
            time.sleep(0.6)  # anti quota
            inv_url = f"https://{shop_url}/admin/api/2023-10/inventory_levels.json"
            params = {"inventory_item_ids": int(row["Inventory Item ID"]), "location_ids": location_id}
            inv_resp = requests.get(inv_url, headers=headers, params=params)
            if inv_resp.ok:
                inv_data = inv_resp.json().get("inventory_levels", [])
                stock_actuels.append(inv_data[0]["available"] if inv_data else 0)
            else:
                stock_actuels.append(None)

        df_merged["Stock actuel"] = stock_actuels
        df_merged["location_id"] = location_id
        return df_merged

    if csv_fournisseur:
        df_merged = preparer_stock_csv(csv_fournisseur, shop_url, access_token)
        st.session_state['df_stock_update'] = df_merged

        st.markdown("### 📊 Aperçu")
        st.dataframe(df_merged[["Product Name", "Barcode", "Stock actuel", "Qty"]], use_container_width=True)

        if st.button("✅ Mettre à jour tous les stocks", key="maj_global"):
            for i, row in df_merged.iterrows():
                if pd.isna(row["Inventory Item ID"]) or pd.isna(row["location_id"]):
                    st.warning(f"⚠️ Produit introuvable : {row['Product Name']}")
                    continue
                payload = {
                    "location_id": int(row["location_id"]),
                    "inventory_item_id": int(row["Inventory Item ID"]),
                    "available_adjustment": int(row["Qty"])
                }
                resp = requests.post(
                    f"https://{shop_url}/admin/api/2023-10/inventory_levels/adjust.json",
                    headers={"X-Shopify-Access-Token": access_token},
                    json=payload
                )
                if resp.ok:
                    st.success(f"✔️ {row['Product Name']} → +{row['Qty']}")
                else:
                    st.error(f"❌ Échec : {row['Product Name']}")

        # 🔘 MAJ individuelle sans recalcul
        st.markdown("### 🛠 Mise à jour individuelle")
        for i, row in st.session_state.get('df_stock_update', pd.DataFrame()).iterrows():
            with st.expander(f"🔹 {row['Product Name']} — Barcode: {row['Barcode']}"):
                st.write(f"Stock actuel : **{row['Stock actuel']}**")
                st.write(f"Ajouter : **{row['Qty']}**")

                if st.button(f"Mettre à jour ce produit", key=f"btn_indiv_{i}"):
                    payload = {
                        "location_id": int(row["location_id"]),
                        "inventory_item_id": int(row["Inventory Item ID"]),
                        "available_adjustment": int(row["Qty"])
                    }
                    resp = requests.post(
                        f"https://{shop_url}/admin/api/2023-10/inventory_levels/adjust.json",
                        headers={"X-Shopify-Access-Token": access_token},
                        json=payload
                    )
                    if resp.ok:
                        st.success(f"✔️ Stock mis à jour : {row['Product Name']}")
                    else:
                        st.error(f"❌ Erreur : {row['Product Name']}")







with tab6:
    st.markdown("## Mise à jour manuelle du stock par barcode")

    barcode_input = st.text_input("🔍 Entrez le barcode du produit à mettre à jour")
    qty_input = st.number_input("📦 Quantité à ajouter (positive ou négative)", value=0, step=1)

    if st.button("Mettre à jour le stock Shopify"):
        if not barcode_input or qty_input == 0:
            st.warning("Saisis un barcode valide et une quantité différente de 0.")
        else:
            with st.spinner("Connexion à Shopify..."):
                try:
                    # 🔍 Étape 1 : Recherche du produit par barcode
                    search_url = f"https://{shop_url}/admin/api/2023-10/products.json"
                    params = {"fields": "id,title,variants", "limit": 250}
                    headers = {"X-Shopify-Access-Token": access_token}
                    found_variant = None

                    while True:
                        resp = requests.get(search_url, headers=headers, params=params)
                        resp.raise_for_status()
                        products = resp.json()["products"]

                        for p in products:
                            for v in p.get("variants", []):
                                if str(v.get("barcode", "")).strip() == barcode_input.strip():
                                    found_variant = v
                                    break
                            if found_variant:
                                break

                        link = resp.headers.get("Link", "")
                        if 'rel="next"' in link:
                            match = re.search(r'<([^>]+)>; rel="next"', link)
                            if match:
                                search_url = match.group(1)
                            else:
                                break
                        else:
                            break

                    if not found_variant:
                        st.error("❌ Aucun produit trouvé avec ce barcode.")
                    else:
                        variant_id = found_variant["id"]
                        inventory_item_id = found_variant["inventory_item_id"]

                        # 🔧 Étape 2 : Récupérer location_id
                        loc_url = f"https://{shop_url}/admin/api/2023-10/locations.json"
                        loc_resp = requests.get(loc_url, headers=headers)
                        loc_resp.raise_for_status()
                        location_id = loc_resp.json()["locations"][0]["id"]

                        # 🔎 Étape 3 : Afficher stock actuel
                        inv_url = f"https://{shop_url}/admin/api/2023-10/inventory_levels.json"
                        inv_params = {"inventory_item_ids": inventory_item_id, "location_ids": location_id}
                        inv_resp = requests.get(inv_url, headers=headers, params=inv_params)
                        inv_resp.raise_for_status()
                        inv_data = inv_resp.json().get("inventory_levels", [])
                        stock_actuel = inv_data[0]["available"] if inv_data else 0

                        st.info(f"Stock actuel : {stock_actuel} → après mise à jour : {stock_actuel + qty_input}")

                        # 🔁 Étape 4 : Envoyer la mise à jour
                        payload = {
                            "location_id": location_id,
                            "inventory_item_id": inventory_item_id,
                            "available_adjustment": qty_input
                        }
                        update_url = f"https://{shop_url}/admin/api/2023-10/inventory_levels/adjust.json"
                        update_resp = requests.post(update_url, headers=headers, json=payload)
                        update_resp.raise_for_status()

                        st.success("✅ Stock mis à jour avec succès !")
                        st.json(update_resp.json())

                except Exception as e:
                    st.error(f"❌ Erreur : {e}")

with tab3:
    st.markdown("## 📄 Générateur d’étiquettes Word pour la traduction/fournisseur")
    uploaded_csv = st.file_uploader("📁 Fichier produits (CSV)", type=["csv"])
    if uploaded_csv:
        df = pd.read_csv(uploaded_csv)
        doc = Document()
        doc.add_paragraph()

        for idx, row in df.iterrows():
            table = doc.add_table(rows=1, cols=1)
            cell = table.cell(0, 0)

            def feed_html(p, html_text):
                parser = DocxHTMLParser(p)
                parser.feed(html_text.replace("<br>", "\n"))

            # Titre
            feed_html(cell.add_paragraph(), f"<b>{row.get('Vendor', '')}</b>\n<b>{row.get('Title', '')}</b>")

            # Contenance
            cont = row.get('custom.taille', '')
            if not pd.isna(cont) and str(cont).strip():
                feed_html(cell.add_paragraph(), f"<b>Contenance :</b> {str(cont).strip()}")

            # Barcode
            barcode = str(row.get('Variant Barcode', ''))
            feed_html(cell.add_paragraph(), f"<b>Barcode :</b> {barcode}")

            # Utilisation
            util = str(row.get('custom.utilisation', ''))
            if util and util.lower() != 'nan':
                feed_html(cell.add_paragraph(), f"<b>Mode d'emploi :</b> {util}")

            # Ingrédients
            ing = str(row.get('custom.ingredients', ''))
            feed_html(cell.add_paragraph(), f"<b>Ingrédients :</b> {ing}")

            # Précaution
            precaution = PRECAUTION_DEFAULT
            feed_html(cell.add_paragraph(), precaution)

            # Infos fabricant
            vendor = str(row.get('Vendor', ''))
            feed_html(cell.add_paragraph(), INFO_BLOCK_TEMPLATE.format(vendor=vendor))

            # Icônes
            pao_value = row.get('custom.periode_mois', '')
            if pd.isna(pao_value) or str(pao_value).strip() == '':
                pao_icon = "pao_12m.png"
            else:
                try:
                    pao_int = int(float(pao_value))
                    pao_icon = f"pao_{pao_int}m.png"
                except:
                    pao_icon = "pao_12m.png"

            tri_value = str(row.get('custom.texte_recyclage', '')).strip().lower().replace(' ', '_')
            tri_icon = f"{tri_value}.png" if tri_value not in ['', 'nan'] else "tri_standard.png"

            p = cell.add_paragraph()
            run = p.add_run()

            if os.path.exists(f"icones/{pao_icon}"):
                run.add_picture(f"icones/{pao_icon}", width=Inches(0.6))
            if os.path.exists(f"icones/{tri_icon}"):
                run.add_picture(f"icones/{tri_icon}", width=Inches(2))

            # Bordures
            cell._element.get_or_add_tcPr().append(parse_xml(r'<w:tcBorders %s>'
                r'<w:top w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
                r'<w:left w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
                r'<w:bottom w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
                r'<w:right w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
                r'</w:tcBorders>' % nsdecls('w')))

            doc.add_paragraph()

        # Export Word
        from io import BytesIO
        buffer = BytesIO()
        doc.save(buffer)
        buffer.seek(0)

        st.download_button(
            label="📥 Télécharger l'étiquette Word",
            data=buffer,
            file_name="Etiquettes_Produits_YOOMI.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

with tab4:
    st.markdown("## 📄 Générateur d’étiquettes de traduction autocollantes (5×5 cm)")

    if "df" not in st.session_state:
        st.warning("⚠️ La base produits doit être chargée au préalable (voir onglet 1).")
    else:
        df = st.session_state["df"].copy()

        df['label_trad'] = df['Vendor'] + ' - ' + df['Title']
        selected = st.multiselect("📌 Sélectionnez les produits à imprimer", options=df['label_trad'].tolist())

        to_print = df[df['label_trad'].isin(selected)].reset_index(drop=True)

        if not to_print.empty and st.button("🖨️ Générer PDF 5×5 cm (une étiquette par page)"):
            from reportlab.lib.pagesizes import inch
            from reportlab.pdfgen import canvas
            from io import BytesIO
            import textwrap
            import os

            import fitz  # PyMuPDF
            from PIL import Image

            PAGE_SIZE = (141.7, 141.7)  # 5cm x 5cm en points
            buffer = BytesIO()
            c = canvas.Canvas(buffer, pagesize=PAGE_SIZE)

            for _, row in to_print.iterrows():
                # Zone texte : 5x5 cm
                x, y = 5, 5
                width, height = 131.7, 131.7  # avec marges
                line_height = 10

                bloc_text = (
                    f"<b>{row['Vendor']}</b>\n"
                    f"<b>{row['Title']}</b>\n\n"
                    f"<b>Contenance :</b> {row.get('custom.taille', '')}\n"
                    f"<b>Mode d'emploi :</b> {row.get('custom.utilisation', '')}\n"
                    f"<b>Ingrédients :</b> {row.get('custom.ingredients', '')}\n"
                    "<b>Avertissement!</b> Usage externe uniquement. Éviter tout contact avec les yeux. "
                    "Tenir hors de portée des enfants. En cas d'apparition de rougeurs, consultez un médecin.\n"
                    "<b>A consommer de préférence avant le :</b> voir emballage.\n"
                    "<b>EU RP :</b> Emmanuelle Kueny - Yoomi k-beauty, 19 rue merciere, 68100 Mulhouse, France\n"
                    "<b>Fabriqué en Corée</b>"
                )

                draw_box_rich(c, x, y, width, height, bloc_text, font="BellCentennial", font_size=6.8)
                c.showPage()

                # Bloc fabricant
                c.setFont("BellCentennial", 4.5)
                c.drawString(6, y, "📦 Fabricant :")
                y -= line_height

                c.setFont("BellCentennial", 4.5)
                y = wrap_and_draw(f"{row.get('Vendor', '')}", y)
                y = wrap_and_draw(
                    "EU RP : Emmanuelle Kueny - Yoomi k-beauty, 19 rue merciere, 68100 Mulhouse, France - 03 65 67 40 62 - SIREN 932 945 256",
                    y
                )
                y = wrap_and_draw("Fabriqué en Corée", y)
                y -= line_height

                # Icônes PAO + recyclage
                pao_val = row.get("custom.periode_mois", "")
                try:
                    pao_int = int(float(pao_val))
                    pao_icon = f"icones/pao_{pao_int}m.png"
                except:
                    pao_icon = "icones/pao_12m.png"

                tri_txt = str(row.get("custom.texte_recyclage", "")).strip().lower().replace(" ", "_")
                tri_icon = f"icones/{tri_txt}.png" if tri_txt and tri_txt != "nan" else "icones/tri_standard.png"

                x_icon = 6
                if os.path.exists(pao_icon):
                    c.drawImage(pao_icon, x_icon, 6, width=25, height=25, preserveAspectRatio=True)
                    x_icon += 28
                if os.path.exists(tri_icon):
                    c.drawImage(tri_icon, x_icon, 6, width=25, height=25, preserveAspectRatio=True)

                c.showPage()

            c.save()
            buffer.seek(0)

            st.download_button(
                label="📥 Télécharger les étiquettes PDF",
                data=buffer,
                file_name="etiquettes_traduction_yoomi.pdf",
                mime="application/pdf"
            )

            # ✅ Aperçu rapide (image de la 1re page)
            with st.expander("👁 Aperçu rapide de la première étiquette (5×5 cm)"):
                try:
                    pdf = fitz.open(stream=buffer.getvalue(), filetype="pdf")
                    page = pdf.load_page(0)
                    pix = page.get_pixmap(dpi=300)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    st.image(img, caption="Aperçu première page", use_column_width=False)
                except Exception as e:
                    st.warning(f"Impossible d'afficher un aperçu : {e}")

with tab7:
    st.markdown("## 💸 Gestion des Soldes (manuelle par sélection)")

    if "df" in st.session_state:
        df = st.session_state["df"].copy()
        df['label_soldes'] = df['Vendor'] + ' - ' + df['Title']
        selected_soldes = st.multiselect("🛍️ Sélectionne les produits à solder", options=df['label_soldes'].tolist(), key="soldes_selection")

        # Saisie du tag à appliquer (ex : soldes30, soldes50)
        tag_to_apply = st.text_input("🏷️ Tag à appliquer (ex : soldes30)", value="soldes30")

        # Bouton pour ajouter le tag
        if st.button("✅ Ajouter le tag aux produits sélectionnés"):
            headers = {"X-Shopify-Access-Token": access_token}
            products = []
            base_url = f"https://{shop_url}/admin/api/2024-01/products.json?limit=250"
            url = base_url

            # Récupération des produits pour mapping titre → ID
            while url:
                resp = requests.get(url, headers=headers)
                resp.raise_for_status()
                products += resp.json().get("products", [])
                link = resp.headers.get("Link", "")
                if 'rel="next"' in link:
                    match = re.search(r'<([^>]+)>; rel="next"', link)
                    url = match.group(1) if match else None
                else:
                    break

            titre_to_id = {p['title']: p for p in products}

            for label in selected_soldes:
                vendor, title = label.split(" - ", 1)
                produit = titre_to_id.get(title)
                if not produit:
                    st.warning(f"❌ Produit introuvable : {title}")
                    continue

                tags_existants = produit.get("tags", "")
                nouveaux_tags = [t.strip() for t in tags_existants.split(",") if t.strip()]
                if tag_to_apply.lower() not in [t.lower() for t in nouveaux_tags]:
                    nouveaux_tags.append(tag_to_apply)

                    update_url = f"https://{shop_url}/admin/api/2024-01/products/{produit['id']}.json"
                    payload = {"product": {"id": produit["id"], "tags": ", ".join(nouveaux_tags)}}
                    update_resp = requests.put(update_url, headers=headers, json=payload)

                    if update_resp.ok:
                        st.success(f"🏷️ Tag '{tag_to_apply}' ajouté à {title}")
                    else:
                        st.error(f"❌ Erreur API sur {title} : {update_resp.text}")
                else:
                    st.info(f"ℹ️ Tag déjà présent sur {title}")

    st.markdown("## 💸 Gestion des soldes automatiques Shopify")

    if "df" not in st.session_state:
        st.warning("Charge d'abord les produits dans l’onglet 1.")
    else:
        shop_url = st.secrets["shopify"]["shop_url"]
        access_token = st.secrets["shopify"]["access_token"]
        api_version = "2024-01"

        headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json"
        }

        def round_up_to_0_05(value):
            return round((value * 20 + 0.9999) // 1 / 20, 2)

        def get_all_products():
            all_products = []
            url = f"https://{shop_url}/admin/api/{api_version}/products.json?limit=250"
            while url:
                resp = requests.get(url, headers=headers)
                if resp.status_code != 200:
                    st.error(f"Erreur API : {resp.status_code} - {resp.text}")
                    break
                products = resp.json().get("products", [])
                all_products.extend(products)

                link = resp.headers.get("Link", "")
                next_url = None
                if 'rel="next"' in link:
                    parts = link.split(",")
                    for part in parts:
                        if 'rel="next"' in part:
                            next_url = part.split(";")[0].strip().strip("<>").replace(" ", "")
                url = next_url
            return all_products

        def extract_discount(tags):
            for tag in tags.split(","):
                tag = tag.strip().lower()
                if tag.startswith("soldes"):
                    try:
                        return int(tag.replace("soldes", ""))
                    except:
                        return None
            return None

        def apply_discount(product, discount_percent):
            for variant in product["variants"]:
                current_price = float(variant["price"])
                compare_at = variant.get("compare_at_price")
                compare_price = float(compare_at) if compare_at else current_price

                discounted = round_up_to_0_05(compare_price * (1 - discount_percent / 100))

                needs_update = (
                    compare_at is None or
                    abs(compare_price - current_price) < 0.01 or
                    abs(current_price - discounted) > 0.01
                )

                if not needs_update:
                    continue

                variant_payload = {
                    "variant": {
                        "id": variant["id"],
                        "price": str(discounted),
                        "compare_at_price": str(compare_price)
                    }
                }

                resp = requests.put(
                    f"https://{shop_url}/admin/api/{api_version}/variants/{variant['id']}.json",
                    headers=headers,
                    json=variant_payload
                )

                if resp.ok:
                    st.success(f"✔️ {product['title']} → {compare_price}€ → {discounted}€")
                else:
                    st.error(f"❌ {product['title']} : {resp.text}")

        def revert_discount(product, soldes_tag):
            title = product["title"]
            product_id = product["id"]
            tags = product.get("tags", "")
            new_tags = [tag for tag in tags.split(",") if tag.strip().lower() != soldes_tag]

            updated = False
            for variant in product["variants"]:
                compare_at = variant.get("compare_at_price")
                if compare_at:
                    update = {
                        "variant": {
                            "id": variant["id"],
                            "price": str(compare_at),
                            "compare_at_price": None
                        }
                    }
                    resp = requests.put(
                        f"https://{shop_url}/admin/api/{api_version}/variants/{variant['id']}.json",
                        headers=headers,
                        json=update
                    )
                    if resp.ok:
                        st.success(f"♻️ {title} : retour à {compare_at}€")
                        updated = True
                    else:
                        st.error(f"❌ {title} : {resp.text}")

            if updated:
                tag_payload = {
                    "product": {
                        "id": product_id,
                        "tags": ", ".join(new_tags)
                    }
                }
                tag_resp = requests.put(
                    f"https://{shop_url}/admin/api/{api_version}/products/{product_id}.json",
                    headers=headers,
                    json=tag_payload
                )
                if tag_resp.ok:
                    st.info(f"🧹 Tag '{soldes_tag}' supprimé de {title}")
                else:
                    st.warning(f"⚠️ Tags non mis à jour pour {title}")

        if st.button("✅ Appliquer les remises selon les tags (ex: soldes30)"):
            produits = get_all_products()
            for prod in produits:
                remise = extract_discount(prod.get("tags", ""))
                if remise:
                    apply_discount(prod, remise)

        if st.button("🔁 Annuler les soldes et restaurer les prix d’origine"):
            produits = get_all_products()
            for prod in produits:
                tag_soldes = extract_discount(prod.get("tags", ""))
                if tag_soldes:
                    revert_discount(prod, f"soldes{tag_soldes}")
