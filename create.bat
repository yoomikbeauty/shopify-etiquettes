pyinstaller --onefile --windowed --icon=logo.ico --collect-submodules streamlit --add-data "logo.png;." --add-data "*.ttf;." --add-data "*.png;." --add-data "*.csv;," shopify_app.py
