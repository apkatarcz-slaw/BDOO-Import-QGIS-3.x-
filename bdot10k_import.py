# -*- coding: utf-8 -*-
"""
bdot10k_import.py

Glowna klasa wtyczki - rejestruje ikone na pasku narzedzi i pozycje w menu
Wtyczki, otwiera okno dialogowe (pkt 4 zalozen: wtyczka ma sie otwierac
z paska zadan z ikona).
"""

import os

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon


class BDOOImportPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "resources", "icon.png")
        self.action = QAction(QIcon(icon_path), "Import BDOO", self.iface.mainWindow())
        self.action.setToolTip("Import i scalanie wojewodzkich baz BDOO do GeoPackage / SQLite")
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Import BDOO", self.action)

    def unload(self):
        self.iface.removePluginMenu("&Import BDOO", self.action)
        self.iface.removeToolBarIcon(self.action)
        self.action = None

    def run(self):
        from .dialog import BDOOImportDialog
        if self.dialog is None:
            self.dialog = BDOOImportDialog(self.plugin_dir, parent=self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
