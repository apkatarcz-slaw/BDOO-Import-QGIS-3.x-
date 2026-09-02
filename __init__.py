# -*- coding: utf-8 -*-
"""
Import BDOO - wtyczka QGIS.

Punkt wejscia wymagany przez QGIS Plugin Manager. QGIS wywoluje funkcje
classFactory() przy ladowaniu wtyczki, przekazujac referencje do interfejsu
QGIS (QgisInterface).
"""


def classFactory(iface):
    from .bdot10k_import import BDOOImportPlugin
    return BDOOImportPlugin(iface)
