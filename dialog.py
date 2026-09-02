# -*- coding: utf-8 -*-
"""
dialog.py

Interfejs wtyczki Import BDOO. Budowany programowo (bez pliku .ui),
zeby uniknac kroku kompilacji pyuic i uproscic utrzymanie. Kompatybilny
z PyQt5 (QGIS 3.x) i PyQt6 (QGIS 4.x) dzieki importom przez qgis.PyQt.
"""

import os

from qgis.PyQt.QtCore import Qt, QSettings, QTimer
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QFileDialog, QScrollArea, QWidget,
    QProgressBar, QTextEdit, QGroupBox, QSizePolicy, QMessageBox,
)

from .worker import ImportWorker, ScanWorker, ClearCacheWorker

SETTINGS_GROUP = "BDOOImportQGIS3"

DEFAULTS = {
    "input_dir": "",
    "output_dir": "",
    "suffix": "_BDOO",
    "output_name": "BDOO_wojewodztwa",
    "skip_closed_lifecycle": True,
    "export_gpkg": True,
    "export_gml": False,
    "export_gml_schema": False,
    "export_gml_schema_validate": False,
    "export_gdb": False,
    "export_sqlite": False,
    "export_geoparquet": False,
}


class BDOOImportDialog(QDialog):

    def __init__(self, plugin_dir, parent=None):
        super().__init__(parent)
        self.plugin_dir = plugin_dir
        self.setWindowTitle("Import BDOO")
        self.setMinimumWidth(560)
        self.resize(600, 720)
        # Standardowe, natywne przyciski minimalizuj/przywroc/zamknij w
        # pasku tytulowym okna (obsluguje je system operacyjny - minimalizacja
        # do paska zadan i przywracanie dzialaja bez zadnego dodatkowego
        # kodu, identycznie jak w kazdym innym oknie systemowym).
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)

        self._worker = None
        self._scan_worker = None
        self._layer_checkboxes = {}

        self._clear_cache_worker = None

        self._build_ui()
        self._load_settings()
        self._check_gdb_availability()
        self._check_geoparquet_availability()
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self.status_label.clear())

    # ------------------------------------------------------------------
    # Budowa interfejsu
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # --- pasek tytulowy: flaga + nazwa wtyczki ---
        title_row = QHBoxLayout()
        flag_label = QLabel()
        flag_path = os.path.join(self.plugin_dir, "resources", "flag_pl.png")
        if os.path.exists(flag_path):
            flag_label.setPixmap(QPixmap(flag_path).scaledToHeight(20, Qt.SmoothTransformation))
        title_row.addWidget(flag_label)
        title_lbl = QLabel("<b>Import BDOO</b>")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        layout.addLayout(title_row)

        # --- katalog wejsciowy ---
        self.input_dir_edit = QLineEdit()
        self.input_dir_edit.setToolTip(
            "Katalog zawierajacy pliki .zip z paczkami wojewodzkimi BDOO.\n"
            "Nazwa samego pliku .zip jest ignorowana - wojewodztwo jest\n"
            "rozpoznawane wylacznie po prefiksie w nazwach rozpakowanych\n"
            "plikow GML (np. \"PL.PZGiK.201.02__OT_PTWP_A.xml\" -> wojewodztwo\n"
            "\"PL.PZGiK.201.02\"). Wtyczka rozpakowuje paczki automatycznie do\n"
            "katalogu tymczasowego przed importem - wraz z wbudowanymi\n"
            "schematami XSD, ktorych uzytkownik nie musi sam dostarczac.\n"
            "Kazdy plik jest automatycznie rozpoznawany co do wersji schematu\n"
            "(2011 lub 2021). Dane w starym schemacie sa importowane bez\n"
            "mapowania na nowy model, do osobnego podkatalogu\n"
            "\"bdoo_wojewodztwa_stary_schemat\".")
        btn_in = QPushButton("Wybierz...")
        btn_in.clicked.connect(self._choose_input_dir)
        input_row = self._labeled_row("Katalog wejsciowy:", self.input_dir_edit, btn_in)
        layout.addWidget(input_row)

        cache_info = QLabel(
            "Wyniki konwersji posredniej sa cache'owane w podkatalogu "
            ".bdoo_cache obok katalogu wejsciowego i automatycznie "
            "sprzatane przy kazdym imporcie.")
        cache_info.setWordWrap(True)
        layout.addWidget(cache_info)
        cache_row = QHBoxLayout()
        cache_row.addStretch()
        self.clear_cache_btn = QPushButton("Wyczysc cache teraz")
        self.clear_cache_btn.setToolTip(
            "Usuwa caly cache plikow posrednich dla biezacego katalogu wejsciowego.\n"
            "Kolejny import bedzie musial ponownie przekonwertowac wszystkie pliki GML.")
        self.clear_cache_btn.clicked.connect(self._clear_cache)
        cache_row.addWidget(self.clear_cache_btn)
        layout.addLayout(cache_row)

        # --- katalog wyjsciowy ---
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setToolTip(
            "Katalog, w ktorym zostanie zapisana baza wynikowa. Jesli plik\n"
            "o tej samej nazwie juz istnieje - zostanie nadpisany.")
        btn_out = QPushButton("Wybierz...")
        btn_out.clicked.connect(self._choose_output_dir)
        layout.addWidget(self._labeled_row("Katalog wyjsciowy:", self.output_dir_edit, btn_out))

        # --- sufiks / nazwa bazy scalonej ---
        grid = QGridLayout()
        self.suffix_edit = QLineEdit()
        self.suffix_edit.setToolTip(
            "Sufiks dodawany do nazwy pliku wyjsciowego przy imporcie\n"
            "pojedynczego wojewodztwa: nazwa = prefiks pliku wejsciowego + ten sufiks.")
        grid.addWidget(QLabel("Sufiks nazwy pliku:"), 0, 0)
        grid.addWidget(self.suffix_edit, 1, 0)
        layout.addLayout(grid)

        # --- nazwa bazy scalonej ---
        self.output_name_edit = QLineEdit()
        self.output_name_edit.setToolTip(
            "Nazwa pliku bazy wynikowej powstalej ze scalenia wielu wojewodztw\n"
            "w jedna baze. Nieuzywane, gdy wybrano tylko jedno wojewodztwo -\n"
            "wtedy nazwa pochodzi z prefiksu pliku + sufiksu.")
        output_name_label = QLabel("Nazwa bazy wynikowej\n(przy scalaniu wielu wojewodztw):")
        layout.addWidget(output_name_label)
        layout.addWidget(self.output_name_edit)

        # --- opcje ---
        self.skip_lifecycle_cb = QCheckBox("Pomin obiekty z zamknietym cyklem zycia")
        self.skip_lifecycle_cb.setToolTip(
            "Jesli zaznaczone, obiekty z wypelnionym polem\n"
            "koniecWersjiObiektu sa pomijane przy imporcie.")
        layout.addWidget(self.skip_lifecycle_cb)

        # --- formaty eksportu ---
        # Baza robocza uzywana do scalania wojewodztw, docinania szerokosci
        # pol i deduplikacji jest ZAWSZE SQLite/SpatiaLite (wewnetrznie, w
        # katalogu tymczasowym). Ponizsze checkboxy okreslaja WYLACZNIE, ktore
        # formaty maja trafic do katalogu wyjsciowego - kazdy budowany z juz
        # gotowej, scalonej i zdeduplikowanej bazy roboczej. Wymagany jest co
        # najmniej jeden zaznaczony format.
        formats_box = QGroupBox("Formaty eksportu (co najmniej jeden)")
        formats_layout = QVBoxLayout(formats_box)

        self.export_gpkg_cb = QCheckBox("GeoPackage (.gpkg)")
        self.export_gpkg_cb.setToolTip(
            "Zapisz baze wynikowa w formacie GeoPackage - jeden plik z\n"
            "wszystkimi przetworzonymi warstwami, gotowy do wczytania w QGIS.")
        formats_layout.addWidget(self.export_gpkg_cb)

        self.export_gml_cb = QCheckBox("GML/XML (jeden plik na kazdy typ warstwy)")
        self.export_gml_cb.setToolTip(
            "Dodatkowo zapisz dla kazdego wybranego typu warstwy oddzielny\n"
            "plik GML (jeden plik na warstwe), scalony ze wszystkich\n"
            "przetworzonych wojewodztw. Eksport czyta juz gotowa, przefiltrowana\n"
            "i zdeduplikowana warstwe z bazy roboczej - nie dotyka ponownie\n"
            "oryginalnych plikow XML. Pliki trafiaja do podkatalogu BDOO_Polska\n"
            "(wewnatrz katalogu wyjsciowego) z prefiksem PL.PZGiK.201.22__ w\n"
            "nazwie - ulatwia to wizualizacje w dedykowanych wtyczkach BDOO.")
        formats_layout.addWidget(self.export_gml_cb)

        gml_schema_row = QHBoxLayout()
        self.export_gml_schema_cb = QCheckBox("GML/XML zgodny ze schematem")
        self.export_gml_schema_cb.setChecked(False)
        self.export_gml_schema_cb.setToolTip(
            "Dodatkowy, osobny eksport GML - niezalezny od checkboxa powyzej,\n"
            "mozna zaznaczyc oba naraz. Roznice wzgledem zwyklego eksportu GML:\n"
            "- kolejnosc pol wymuszona zgodnie z sekwencja zadeklarowana w\n"
            "  schemacie BDOT10k_BDOO.xsd (nie kolejnosc, w jakiej pola\n"
            "  zostaly napotkane przy scalaniu wojewodztw),\n"
            "- oficjalna przestrzen nazw/prefiks \"ot:\" i odwolanie\n"
            "  xsi:schemaLocation do wbudowanego schematu (kopiowanego obok\n"
            "  wyniku w podkatalogu BDOO_Polska_zgodny_ze_schematem),\n"
            "- wynik jest dodatkowo WALIDOWANY wzgledem tego schematu (jesli\n"
            "  modul lxml jest dostepny) - plik zostaje zapisany niezaleznie\n"
            "  od wyniku walidacji, a przy bledach obok niego trafia plik\n"
            "  tekstowy z ich lista.\n"
            "Wolniejsze niz zwykly eksport GML (dochodzi ustalanie kolejnosci\n"
            "pol i walidacja). Dotyczy wylacznie danych w NOWYM schemacie (2021).")
        gml_schema_row.addWidget(self.export_gml_schema_cb)
        gml_schema_hint = QLabel("Eksport dokladny, ale wolniejszy")
        gml_schema_hint.setStyleSheet("color: #666666; font-style: italic;")
        gml_schema_row.addWidget(gml_schema_hint)
        gml_schema_row.addStretch()
        formats_layout.addLayout(gml_schema_row)

        gml_schema_validate_row = QHBoxLayout()
        gml_schema_validate_row.addSpacing(24)
        self.export_gml_schema_validate_cb = QCheckBox("Waliduj wynik wzgledem schematu")
        self.export_gml_schema_validate_cb.setChecked(False)
        self.export_gml_schema_validate_cb.setEnabled(self.export_gml_schema_cb.isChecked())
        self.export_gml_schema_validate_cb.setToolTip(
            "Po zapisie sprawdz plik GML przez lxml wzgledem wbudowanego\n"
            "schematu BDOT10k_BDOO.xsd (przy bledach obok pliku trafia\n"
            "dodatkowo plik tekstowy z ich lista). To najwolniejszy etap\n"
            "eksportu 'zgodnego ze schematem' - domyslnie odznaczone.\n"
            "Zaznacz, jesli chcesz miec faktyczne potwierdzenie zgodnosci\n"
            "wyniku ze schematem (kosztem dluzszego czasu eksportu).")
        self.export_gml_schema_cb.toggled.connect(self.export_gml_schema_validate_cb.setEnabled)
        gml_schema_validate_row.addWidget(self.export_gml_schema_validate_cb)
        gml_schema_validate_hint = QLabel("(wydluza czas eksportu)")
        gml_schema_validate_hint.setStyleSheet("color: #666666; font-style: italic;")
        gml_schema_validate_row.addWidget(gml_schema_validate_hint)
        gml_schema_validate_row.addStretch()
        formats_layout.addLayout(gml_schema_validate_row)

        self.export_gdb_cb = QCheckBox("ESRI File Geodatabase (.gdb)")
        self.export_gdb_cb.setToolTip(
            "Dodatkowo zapisz wszystkie przetworzone warstwy do jednej\n"
            "geobazy plikowej ESRI (.gdb), obok bazy wynikowej. Uzywa\n"
            "wbudowanego w GDAL sterownika OpenFileGDB (wymaga GDAL >= 3.6) -\n"
            "nie wymaga zadnego dodatkowego oprogramowania ESRI. Eksport\n"
            "czyta juz gotowa, przefiltrowana i zdeduplikowana warstwe z\n"
            "bazy roboczej, nie dotyka ponownie oryginalnych plikow XML.")
        formats_layout.addWidget(self.export_gdb_cb)

        self.export_sqlite_cb = QCheckBox("SQLite / SpatiaLite (.sqlite)")
        self.export_sqlite_cb.setToolTip(
            "Wtyczka zawsze buduje wewnetrzna baze robocza w formacie\n"
            "SQLite/SpatiaLite, zeby scalic wojewodztwa i wykonac\n"
            "deduplikacje. Zaznacz, jesli chcesz zeby ta baza zostala\n"
            "rowniez skopiowana do katalogu wyjsciowego jako widoczny\n"
            "plik wynikowy (w przeciwnym razie jest kasowana po imporcie).")
        formats_layout.addWidget(self.export_sqlite_cb)

        self.export_geoparquet_cb = QCheckBox("GeoParquet (.parquet)")
        self.export_geoparquet_cb.setToolTip(
            "Dodatkowo zapisz kazda warstwe do wlasnego pliku GeoParquet\n"
            "(kazdy plik .parquet to dokladnie jedna warstwa - w odroznieniu\n"
            "od GPKG/SQLite/.gdb, ktore trzymaja wiele warstw w jednym\n"
            "pliku). Dziala dla obu schematow (stary i nowy). Nazwa pliku\n"
            "pochodzi z pola 'Nazwa bazy wynikowej' ponizej + nazwy warstwy,\n"
            "np. 'BDOO_wojewodztwa__OT_PTWP_A.parquet'. Pliki dla nowego\n"
            "schematu trafiaja do podkatalogu 'geoparquet' w katalogu\n"
            "wyjsciowym; dla starego schematu - do takiego samego\n"
            "podkatalogu wewnatrz podkatalogu starego schematu.")
        formats_layout.addWidget(self.export_geoparquet_cb)

        layout.addWidget(formats_box)

        # --- lista warstw ---
        layers_box = QGroupBox("Warstwy do importu")
        layers_layout = QVBoxLayout(layers_box)

        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("Skanuj katalog wejsciowy")
        self.scan_btn.setToolTip("Przeszukaj pliki .zip w katalogu wejsciowym i zbuduj liste dostepnych warstw.")
        self.scan_btn.clicked.connect(self._scan_layers)
        btn_row.addWidget(self.scan_btn)
        btn_row.addStretch()
        self.select_all_btn = QPushButton("Zaznacz wszystkie")
        self.select_all_btn.clicked.connect(lambda: self._set_all_layers(True))
        self.deselect_all_btn = QPushButton("Odznacz wszystkie")
        self.deselect_all_btn.clicked.connect(lambda: self._set_all_layers(False))
        btn_row.addWidget(self.select_all_btn)
        btn_row.addWidget(self.deselect_all_btn)
        layers_layout.addLayout(btn_row)

        self.layers_scroll = QScrollArea()
        self.layers_scroll.setWidgetResizable(True)
        self.layers_scroll.setMinimumHeight(160)
        self.layers_scroll.setMaximumHeight(220)
        self.layers_container = QWidget()
        self.layers_container_layout = QVBoxLayout(self.layers_container)
        placeholder_label = QLabel("Kliknij \u201eSkanuj katalog wejsciowy\u201d, aby wczytac liste warstw.")
        placeholder_label.setWordWrap(True)
        self.layers_container_layout.addWidget(placeholder_label)
        self.layers_scroll.setWidget(self.layers_container)
        layers_layout.addWidget(self.layers_scroll)
        layout.addWidget(layers_box)

        # --- pasek postepu ---
        self.progress_label = QLabel("")
        layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)

        # --- log ---
        log_header_row = QHBoxLayout()
        log_header_row.addWidget(QLabel("Log przetwarzania:"))
        log_header_row.addStretch()
        self.export_log_btn = QPushButton("Eksportuj log...")
        self.export_log_btn.setToolTip("Zapisz caly log przetwarzania do pliku tekstowego.")
        self.export_log_btn.clicked.connect(self._export_log)
        log_header_row.addWidget(self.export_log_btn)
        layout.addLayout(log_header_row)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(140)
        layout.addWidget(self.log_view)

        # --- status znikajacy (komunikaty na czerwono, 2 sekundy) ---
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #c8262a; font-weight: bold;")
        layout.addWidget(self.status_label)

        layout.addStretch()

        # --- przyciski dolne ---
        bottom_row = QHBoxLayout()
        self.close_btn = QPushButton("Zamknij")
        self.close_btn.clicked.connect(self.close)
        bottom_row.addWidget(self.close_btn)

        self.restore_defaults_btn = QPushButton("Przywroc domyslne")
        self.restore_defaults_btn.setToolTip("Przywraca wszystkie pola i zaznaczenia do wartosci domyslnych.")
        self.restore_defaults_btn.clicked.connect(self._restore_defaults)
        bottom_row.addWidget(self.restore_defaults_btn)

        bottom_row.addStretch()

        self.stop_btn = QPushButton("Zatrzymaj")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_import)
        bottom_row.addWidget(self.stop_btn)

        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._start_import)
        bottom_row.addWidget(self.start_btn)

        outer.addLayout(bottom_row)

    def _labeled_row(self, label_text, line_edit, button):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(QLabel(label_text))
        h = QHBoxLayout()
        h.addWidget(line_edit)
        h.addWidget(button)
        v.addLayout(h)
        return w

    # ------------------------------------------------------------------
    # QSettings - zapamietywanie ustawien miedzy sesjami
    # ------------------------------------------------------------------

    def _load_settings(self):
        s = QSettings()
        s.beginGroup(SETTINGS_GROUP)
        self.input_dir_edit.setText(s.value("input_dir", DEFAULTS["input_dir"]))
        self.output_dir_edit.setText(s.value("output_dir", DEFAULTS["output_dir"]))
        self.suffix_edit.setText(s.value("suffix", DEFAULTS["suffix"]))
        self.output_name_edit.setText(s.value("output_name", DEFAULTS["output_name"]))
        self.skip_lifecycle_cb.setChecked(_to_bool(s.value("skip_closed_lifecycle", DEFAULTS["skip_closed_lifecycle"])))
        self.export_gpkg_cb.setChecked(_to_bool(s.value("export_gpkg", DEFAULTS["export_gpkg"])))
        self.export_gml_cb.setChecked(_to_bool(s.value("export_gml", DEFAULTS["export_gml"])))
        self.export_gml_schema_cb.setChecked(_to_bool(s.value("export_gml_schema", DEFAULTS["export_gml_schema"])))
        self.export_gml_schema_validate_cb.setChecked(_to_bool(s.value("export_gml_schema_validate", DEFAULTS["export_gml_schema_validate"])))
        self.export_gdb_cb.setChecked(_to_bool(s.value("export_gdb", DEFAULTS["export_gdb"])))
        self.export_sqlite_cb.setChecked(_to_bool(s.value("export_sqlite", DEFAULTS["export_sqlite"])))
        self.export_geoparquet_cb.setChecked(_to_bool(s.value("export_geoparquet", DEFAULTS["export_geoparquet"])))
        s.endGroup()

    def _save_settings(self):
        s = QSettings()
        s.beginGroup(SETTINGS_GROUP)
        s.setValue("input_dir", self.input_dir_edit.text())
        s.setValue("output_dir", self.output_dir_edit.text())
        s.setValue("suffix", self.suffix_edit.text())
        s.setValue("output_name", self.output_name_edit.text())
        s.setValue("skip_closed_lifecycle", self.skip_lifecycle_cb.isChecked())
        s.setValue("export_gpkg", self.export_gpkg_cb.isChecked())
        s.setValue("export_gml", self.export_gml_cb.isChecked())
        s.setValue("export_gml_schema", self.export_gml_schema_cb.isChecked())
        s.setValue("export_gml_schema_validate", self.export_gml_schema_validate_cb.isChecked())
        s.setValue("export_gdb", self.export_gdb_cb.isChecked())
        s.setValue("export_sqlite", self.export_sqlite_cb.isChecked())
        s.setValue("export_geoparquet", self.export_geoparquet_cb.isChecked())
        s.endGroup()

    def _restore_defaults(self):
        self.input_dir_edit.setText(DEFAULTS["input_dir"])
        self.output_dir_edit.setText(DEFAULTS["output_dir"])
        self.suffix_edit.setText(DEFAULTS["suffix"])
        self.output_name_edit.setText(DEFAULTS["output_name"])
        self.skip_lifecycle_cb.setChecked(DEFAULTS["skip_closed_lifecycle"])
        self.export_gpkg_cb.setChecked(DEFAULTS["export_gpkg"])
        if self.export_gml_cb.isEnabled():
            self.export_gml_cb.setChecked(DEFAULTS["export_gml"])
        if self.export_gml_schema_cb.isEnabled():
            self.export_gml_schema_cb.setChecked(DEFAULTS["export_gml_schema"])
            self.export_gml_schema_validate_cb.setChecked(DEFAULTS["export_gml_schema_validate"])
        if self.export_gdb_cb.isEnabled():
            self.export_gdb_cb.setChecked(DEFAULTS["export_gdb"])
        self.export_sqlite_cb.setChecked(DEFAULTS["export_sqlite"])
        if self.export_geoparquet_cb.isEnabled():
            self.export_geoparquet_cb.setChecked(DEFAULTS["export_geoparquet"])
        self._set_all_layers(True)
        self._show_status("Przywrocono ustawienia domyslne.")

    # ------------------------------------------------------------------
    # Wybor katalogow
    # ------------------------------------------------------------------

    def _choose_input_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Wybierz katalog wejsciowy", self.input_dir_edit.text())
        if d:
            self.input_dir_edit.setText(d)
            self._scan_layers()

    def _choose_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Wybierz katalog wyjsciowy", self.output_dir_edit.text())
        if d:
            self.output_dir_edit.setText(d)

    # ------------------------------------------------------------------
    # Skanowanie warstw
    # ------------------------------------------------------------------

    def _scan_layers(self):
        input_dir = self.input_dir_edit.text().strip()
        if not input_dir or not os.path.isdir(input_dir):
            self._show_status("Wskaz najpierw poprawny katalog wejsciowy.")
            return
        self.scan_btn.setEnabled(False)
        self._append_log("Skanowanie katalogu wejsciowego...", "info")
        self._scan_worker = ScanWorker(input_dir)
        self._scan_worker.log_message.connect(self._append_log)
        self._scan_worker.scan_finished.connect(self._on_scan_finished)
        self._scan_worker.start()

    def _on_scan_finished(self, layer_names, has_old_schema, has_new_schema):
        self.scan_btn.setEnabled(True)
        self._populate_layers(layer_names)
        if not layer_names:
            self._append_log("Nie znaleziono zadnych warstw w katalogu wejsciowym.", "error")
        else:
            self._append_log(f"Znaleziono {len(layer_names)} typ(ow) warstw.", "info")

        # Jesli wsrod zeskanowanych danych wystapil STARY schemat (nawet
        # w mieszanym zbiorze razem z nowym) - GML nigdy nie jest dla
        # niego generowany, wiec wyszarzamy checkbox GML (widoczny, ale
        # nieaktywny), zeby nie sugerowac mozliwosci, ktora i tak nic by
        # nie dala dla tej czesci danych.
        if has_old_schema:
            self.export_gml_cb.setChecked(False)
            self.export_gml_cb.setEnabled(False)
            self.export_gml_cb.setToolTip(
                "Niedostepne: w zeskanowanych danych wykryto STARY schemat (2011),\n"
                "dla ktorego GML nigdy nie jest generowany (patrz plik informacyjny\n"
                "w podkatalogu bdoo_wojewodztwa_stary_schemat). Dotyczy to rowniez\n"
                "mieszanych zbiorow - w takim przypadku ta opcja zostaje wyszarzona\n"
                "dla calego przebiegu.")
            self.export_gml_schema_cb.setChecked(False)
            self.export_gml_schema_cb.setEnabled(False)
            self.export_gml_schema_cb.setToolTip(
                "Niedostepne: w zeskanowanych danych wykryto STARY schemat (2011),\n"
                "dla ktorego GML nigdy nie jest generowany. Dotyczy to rowniez\n"
                "mieszanych zbiorow - w takim przypadku ta opcja zostaje wyszarzona\n"
                "dla calego przebiegu.")
            self.export_gml_schema_validate_cb.setEnabled(False)
        else:
            self.export_gml_cb.setEnabled(True)
            self.export_gml_cb.setToolTip(
                "Dodatkowo zapisz dla kazdego wybranego typu warstwy oddzielny\n"
                "plik GML (jeden plik na warstwe), scalony ze wszystkich\n"
                "przetworzonych wojewodztw. Eksport czyta juz gotowa, przefiltrowana\n"
                "i zdeduplikowana warstwe z bazy roboczej - nie dotyka ponownie\n"
                "oryginalnych plikow XML. Pliki trafiaja do podkatalogu BDOO_Polska\n"
                "(wewnatrz katalogu wyjsciowego) z prefiksem PL.PZGiK.201.22__ w\n"
                "nazwie - ulatwia to wizualizacje w dedykowanych wtyczkach BDOO.")
            self.export_gml_schema_cb.setEnabled(True)
            self.export_gml_schema_validate_cb.setEnabled(self.export_gml_schema_cb.isChecked())

    def _populate_layers(self, layer_names):
        while self.layers_container_layout.count():
            item = self.layers_container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._layer_checkboxes = {}

        if not layer_names:
            self.layers_container_layout.addWidget(QLabel("Brak warstw do wyswietlenia."))
            return

        for name in layer_names:
            cb = QCheckBox(name)
            cb.setChecked(True)
            self.layers_container_layout.addWidget(cb)
            self._layer_checkboxes[name] = cb

    def _set_all_layers(self, checked):
        for cb in self._layer_checkboxes.values():
            cb.setChecked(checked)

    # ------------------------------------------------------------------
    # Dostepnosc sterownika OpenFileGDB (zapis)
    # ------------------------------------------------------------------

    def _check_gdb_availability(self):
        available = False
        try:
            from osgeo import ogr
            drv = ogr.GetDriverByName("OpenFileGDB")
            available = drv is not None and drv.TestCapability(ogr.ODrCCreateDataSource)
        except Exception:
            available = False
        self.export_gdb_cb.setEnabled(available)
        if not available:
            self.export_gdb_cb.setChecked(False)
            self.export_gdb_cb.setToolTip(
                "Niedostepne w tej instalacji QGIS/GDAL: sterownik OpenFileGDB\n"
                "z obsluga zapisu wymaga GDAL >= 3.6. Zaktualizuj QGIS/OSGeo4W,\n"
                "aby odblokowac te opcje.")

    def _check_geoparquet_availability(self):
        available = False
        try:
            from osgeo import ogr
            drv = ogr.GetDriverByName("Parquet")
            available = drv is not None and drv.TestCapability(ogr.ODrCCreateDataSource)
        except Exception:
            available = False
        self.export_geoparquet_cb.setEnabled(available)
        if not available:
            self.export_geoparquet_cb.setChecked(False)
            self.export_geoparquet_cb.setToolTip(
                "Niedostepne w tej instalacji QGIS/GDAL: sterownik GeoParquet\n"
                "(Apache Arrow/Parquet) nie jest wbudowany w te instalacje GDAL -\n"
                "to opcjonalna zaleznosc GDAL, nie kazda instalacja QGIS/OSGeo4W\n"
                "ja ma. Zaktualizuj QGIS/OSGeo4W (wersja z obsluga Parquet), aby\n"
                "odblokowac te opcje.")

    # ------------------------------------------------------------------
    # Czyszczenie cache plikow posrednich
    # ------------------------------------------------------------------

    def _clear_cache(self):
        input_dir = self.input_dir_edit.text().strip()
        if not input_dir or not os.path.isdir(input_dir):
            self._show_status("Wskaz najpierw poprawny katalog wejsciowy.")
            return
        self.clear_cache_btn.setEnabled(False)
        self._clear_cache_worker = ClearCacheWorker(input_dir)
        self._clear_cache_worker.log_message.connect(self._append_log)
        self._clear_cache_worker.cleared.connect(self._on_cache_cleared)
        self._clear_cache_worker.start()

    def _on_cache_cleared(self):
        self.clear_cache_btn.setEnabled(True)
        self._show_status("Cache wyczyszczony.")

    # ------------------------------------------------------------------
    # Uruchamianie / zatrzymywanie importu
    # ------------------------------------------------------------------

    def _selected_layers(self):
        return [name for name, cb in self._layer_checkboxes.items() if cb.isChecked()]

    def _start_import(self):
        input_dir = self.input_dir_edit.text().strip()
        output_dir = self.output_dir_edit.text().strip()

        if not input_dir or not os.path.isdir(input_dir):
            self._show_status("Wskaz poprawny katalog wejsciowy.")
            return
        if not output_dir:
            self._show_status("Wskaz katalog wyjsciowy.")
            return
        selected = self._selected_layers()
        if not selected:
            self._show_status("Zaznacz przynajmniej jedna warstwe do importu.")
            return
        if not (self.export_gpkg_cb.isChecked() or self.export_gml_cb.isChecked()
                or self.export_gdb_cb.isChecked() or self.export_sqlite_cb.isChecked()
                or self.export_geoparquet_cb.isChecked()):
            self._show_status("Zaznacz przynajmniej jeden format eksportu (GeoPackage/GML/ESRI geobaza/SQLite/GeoParquet).")
            return

        self._save_settings()
        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.scan_btn.setEnabled(False)

        self._worker = ImportWorker(
            input_dir=input_dir,
            output_dir=output_dir,
            selected_layers=selected,
            output_name=self.output_name_edit.text().strip() or DEFAULTS["output_name"],
            skip_closed_lifecycle=self.skip_lifecycle_cb.isChecked(),
            single_file_suffix=self.suffix_edit.text().strip() or DEFAULTS["suffix"],
            export_gpkg=self.export_gpkg_cb.isChecked(),
            export_gml=self.export_gml_cb.isChecked() and self.export_gml_cb.isEnabled(),
            export_gdb=self.export_gdb_cb.isChecked(),
            export_sqlite=self.export_sqlite_cb.isChecked(),
            export_gml_schema=self.export_gml_schema_cb.isChecked() and self.export_gml_schema_cb.isEnabled(),
            export_gml_schema_validate=self.export_gml_schema_validate_cb.isChecked() and self.export_gml_schema_validate_cb.isEnabled(),
            export_geoparquet=self.export_geoparquet_cb.isChecked() and self.export_geoparquet_cb.isEnabled(),
        )
        self._worker.log_message.connect(self._append_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.start()

    def _stop_import(self):
        if self._worker is not None:
            self._worker.stop()
            self._append_log("Zatrzymywanie... biezacy plik zostanie dokonczony, kolejne przerwane.", "warning")
            self.stop_btn.setEnabled(False)

    def _on_progress(self, done, total, current_name):
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(done)
        self.progress_label.setText(f"Przetwarzanie: {current_name}  ({done} / {total})")

    def _on_finished(self, summary):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)
        errors = summary.get("errors", 0) + len(summary.get("skipped_layers", []))
        self._append_log(f"Raport koncowy: OK = {summary.get('ok', 0)}, bledy/pominiete = {errors}.", "info")
        self._show_status("Zakonczono przetwarzanie.")
        # Komunikat o wtyczce wizualizacyjnej ma sens tylko, jesli w tym
        # przebiegu faktycznie przetworzono dane w NOWYM schemacie (tylko
        # dla niego generowany jest eksport GML, na ktorym ta wtyczka
        # bazuje) - import obejmujacy wylacznie stary schemat (2011) nie
        # produkuje zadnych plikow GML, wiec komunikat byby mylacy.
        if summary.get("had_new_schema"):
            QMessageBox.information(
                self, "Import zakonczony",
                "W celu wizualizacji kartograficznej, uzyj wtyczki BDOO_GML")

    # ------------------------------------------------------------------
    # Log i komunikaty statusowe
    # ------------------------------------------------------------------

    def _append_log(self, message, level="info"):
        color = {"error": "#c8262a", "warning": "#b56a00", "info": "#1a1a1a"}.get(level, "#1a1a1a")
        self.log_view.append(f'<span style="color:{color};">{message}</span>')

    def _export_log(self):
        text = self.log_view.toPlainText()
        if not text.strip():
            self._show_status("Log jest pusty - nie ma czego eksportowac.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Eksportuj log", "bdoo_import_log.txt", "Pliki tekstowe (*.txt);;Wszystkie pliki (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self._show_status(f"Log zapisano do: {os.path.basename(path)}")
        except OSError as exc:
            self._show_status(f"Nie udalo sie zapisac logu: {exc}")

    def _show_status(self, text):
        self.status_label.setText(text)
        self._status_timer.start(2000)

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            confirm = QMessageBox.question(
                self, "Import w toku",
                "Import jest w trakcie przetwarzania. Zatrzymac i zamknac?",
                QMessageBox.Yes | QMessageBox.No)
            if confirm != QMessageBox.Yes:
                event.ignore()
                return
            self._worker.stop()
            self._worker.wait(3000)
        self._save_settings()
        event.accept()


def _to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes")
