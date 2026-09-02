# -*- coding: utf-8 -*-
"""
worker.py

Watek roboczy (QThread) uruchamiajacy BDOOImporter w tle, aby
interfejs QGIS nie zawieszal sie podczas przetwarzania wsadowego (pkt 22
zalozen: dlugie operacje w osobnym watku + pasek postepu).

Kompatybilnosc PyQt5/PyQt6: import warunkowy przez qgis.PyQt, ktory sam
mapuje na wlasciwa wersje Qt w zaleznosci od wersji QGIS.
"""

import traceback

from qgis.PyQt.QtCore import QThread, pyqtSignal

from .importer import BDOOImporter


class ImportWorker(QThread):
    log_message = pyqtSignal(str, str)          # (tekst, poziom: "info"|"warning"|"error")
    progress = pyqtSignal(int, int, str)         # (wykonano, wszystkich, biezacy_element)
    finished_ok = pyqtSignal(dict)               # podsumowanie koncowe

    def __init__(self, input_dir, output_dir, selected_layers,
                 output_name, skip_closed_lifecycle, single_file_suffix,
                 export_gpkg=True, export_gml=False, export_gdb=False,
                 export_sqlite=False, export_gml_schema=False,
                 export_gml_schema_validate=True, export_geoparquet=False, parent=None):
        super().__init__(parent)
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.selected_layers = selected_layers
        self.output_name = output_name
        self.skip_closed_lifecycle = skip_closed_lifecycle
        self.single_file_suffix = single_file_suffix
        self.export_gpkg = export_gpkg
        self.export_gml = export_gml
        self.export_gdb = export_gdb
        self.export_sqlite = export_sqlite
        self.export_gml_schema = export_gml_schema
        self.export_gml_schema_validate = export_gml_schema_validate
        self.export_geoparquet = export_geoparquet
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def _is_cancelled(self):
        return self._stop_requested

    def run(self):
        # KRYTYCZNE: bez tego try/except kazdy nieobslugiwany wyjatek w
        # importer.run() (albo w kodzie wolanym przez niego) po cichu
        # zabija watek - QThread konczy dzialanie, sygnal finished_ok
        # NIGDY nie zostaje wyslany, wiec przycisk "Start" zostaje
        # zablokowany, a log przestaje sie aktualizowac NA ZAWSZE, bez
        # zadnego komunikatu o bledzie. Z zewnatrz wyglada to dokladnie
        # jak zawieszenie wtyczki (0% obciazenia CPU, brak reakcji), a w
        # rzeczywistosci watek po prostu juz nie zyje. Ten blok gwarantuje,
        # ze kazdy taki przypadek trafia do logu z pelnym sladem bledu,
        # a interfejs zawsze zostaje odblokowany.
        try:
            importer = BDOOImporter(
                input_dir=self.input_dir,
                output_dir=self.output_dir,
                log_cb=lambda msg, level="info": self.log_message.emit(msg, level),
                progress_cb=lambda done, total, current: self.progress.emit(done, total, current),
                is_cancelled_cb=self._is_cancelled,
            )
            summary = importer.run(
                selected_layers=self.selected_layers,
                output_name=self.output_name,
                skip_closed_lifecycle=self.skip_closed_lifecycle,
                single_file_suffix=self.single_file_suffix,
                export_gpkg=self.export_gpkg,
                export_gml=self.export_gml,
                export_gdb=self.export_gdb,
                export_sqlite=self.export_sqlite,
                export_gml_schema=self.export_gml_schema,
                export_gml_schema_validate=self.export_gml_schema_validate,
                export_geoparquet=self.export_geoparquet,
            )
        except Exception:
            tb = traceback.format_exc()
            self.log_message.emit(
                "NIEOCZEKIWANY BLAD - przetwarzanie przerwane. Szczegoly:\n" + tb, "error")
            summary = {"ok": 0, "errors": 1, "skipped_layers": []}
        self.finished_ok.emit(summary)


class ScanWorker(QThread):
    """Krotki watek do wstepnego skanowania nazw warstw w plikach .zip,
    zeby zbudowac liste checkboxow bez blokowania interfejsu. Dodatkowo
    przekazuje info o wykrytej wersji schematu (stary/nowy) - interfejs
    uzywa tego do wyszarzenia checkboxa GML, jesli wykryto stary
    schemat (dla ktorego GML nigdy nie jest generowany)."""
    log_message = pyqtSignal(str, str)
    scan_finished = pyqtSignal(list, bool, bool)  # (warstwy, ma_stary_schemat, ma_nowy_schemat)

    def __init__(self, input_dir, parent=None):
        super().__init__(parent)
        self.input_dir = input_dir

    def run(self):
        has_old = False
        has_new = False
        try:
            importer = BDOOImporter(
                input_dir=self.input_dir,
                output_dir=self.input_dir,
                log_cb=lambda msg, level="info": self.log_message.emit(msg, level),
            )
            layers = importer.scan_available_layers()
            has_old = importer.last_scan_has_old_schema
            has_new = importer.last_scan_has_new_schema
        except Exception:
            tb = traceback.format_exc()
            self.log_message.emit("NIEOCZEKIWANY BLAD przy skanowaniu warstw:\n" + tb, "error")
            layers = []
        self.scan_finished.emit(layers, has_old, has_new)


class ClearCacheWorker(QThread):
    """Krotki watek czyszczacy caly katalog cache plikow posrednich
    (.bdoo_cache) na zadanie uzytkownika, bez blokowania interfejsu."""
    log_message = pyqtSignal(str, str)
    cleared = pyqtSignal()

    def __init__(self, input_dir, parent=None):
        super().__init__(parent)
        self.input_dir = input_dir

    def run(self):
        try:
            importer = BDOOImporter(
                input_dir=self.input_dir,
                output_dir=self.input_dir,
                log_cb=lambda msg, level="info": self.log_message.emit(msg, level),
            )
            importer.clear_cache()
        except Exception:
            tb = traceback.format_exc()
            self.log_message.emit("NIEOCZEKIWANY BLAD przy czyszczeniu cache:\n" + tb, "error")
        self.cleared.emit()
