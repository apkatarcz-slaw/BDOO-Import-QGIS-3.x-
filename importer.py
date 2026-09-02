# -*- coding: utf-8 -*-
"""
importer.py

Rdzen logiki wtyczki Import BDOO. Modul NIE zalezy od PyQt - moze byc
uzywany/testowany niezaleznie od interfejsu. Cala komunikacja z UI odbywa
sie przez callbacki (funkcje) przekazywane do metody run().

Dane wejsciowe: Baza Danych Obiektow Ogolnogeograficznych (BDOO) - produkt
GUGiK/PZGiK bedacy zgeneralizowana (skala 1:250 000) pochodna BDOT10k,
dystrybuowany w paczkach .zip PER WOJEWODZTWO (nie per powiat). Model
danych (nazwy klas obiektow, nazwy pol) jest wspoldzielony z BDOT10k,
stad plik schematu nazywa sie "BDOT10k_BDOO.xsd".

=== Architektura importu (wersja z konwersja posrednia) ===

Zamiast wielokrotnie parsowac te same pliki GML srodowiskiem Pythona,
kazdy plik zrodlowy jest RAZ przetwarzany na lekki format posredni
(GeoPackage) przez gdal.VectorTranslate - filtr (pominiecie zamknietego
cyklu zycia) i reprojekcja do EPSG:2180 dzieja sie w tym samym kroku,
wykonywane w skompilowanym kodzie GDAL/C++, nie w petli Pythona.

Wynik konwersji jest CACHE'OWANY na dysku (w podkatalogu .bdoo_cache
obok katalogu wejsciowego), kluczowany nazwa wojewodztwa + nazwa warstwy +
data modyfikacji zrodlowego pliku .zip. Kolejne uruchomienia importu na
tych samych danych zrodlowych (np. zmiana wyboru warstw, poprawka opcji)
NIE parsuja GML ponownie - korzystaja z gotowego pliku posredniego.
Cache jest automatycznie sprzatany na starcie kazdego importu: pliki
posrednie, ktorych zrodlowy .zip zostal zmieniony (inna data modyfikacji)
lub usuniety, sa kasowane.

Dalsze etapy (na juz przekonwertowanych, znacznie szybszych do odczytu
plikach posrednich):
  - szerokosci pol tekstowych: zapytanie SQL MAX(LENGTH(...)) wykonywane
    przez silnik bazy, nie petla w Pythonie,
  - scalanie wojewodztw do wspolnej warstwy: gdal.VectorTranslate w trybie
    "append", opakowane w jedna transakcje na warstwe (zamiast commitu
    przy kazdym obiekcie z osobna - to zwykle najwiekszy pojedynczy
    czynnik spowalniajacy zapis do SQLite/GeoPackage),
  - deduplikacja po polu lokalnyId: pojedyncze zapytanie DELETE w SQL na
    juz zapisanej warstwie wyjsciowej,
  - eksport GML: gdal.VectorTranslate CZYTAJACY Z JUZ GOTOWEJ BAZY SQLite
    (out_ds) - nie z oryginalnych plikow XML. Warstwa w bazie wyjsciowej
    jest juz scalona, przefiltrowana, zdeduplikowana i z ustalonymi
    szerokosciami pol, wiec eksport GML jest zwyklym "przepisaniem" tej
    gotowej tabeli do innego formatu, bez ponownego dotykania zrodlowych
    plikow GML/XML i bez powielania logiki filtrowania/dedup.

Baza robocza (wewnetrzna, niewidoczna domyslnie): ZAWSZE SQLite/SpatiaLite,
budowana w katalogu tymczasowym. Formaty widoczne dla uzytkownika sa
z niej eksportowane niezaleznymi checkboxami: GeoPackage (domyslnie
zaznaczony), GML (jeden plik na warstwe, patrz nizej), ESRI File
Geodatabase (.gdb), oraz - na zyczenie - sama baza robocza SQLite
(skopiowana do katalogu wyjsciowego zamiast skasowana). Wymagany jest
co najmniej jeden zaznaczony format, w przeciwnym razie import jest
blokowany. GeoParquet zostal z wtyczki usuniety (decyzja uzytkownika)
- dedup przez SQL DELETE nie ma prostego odpowiednika dla Parquet
(brak trybu update/delete w miejscu), co komplikowaloby pipeline bez
wyraznej korzysci.

Zalozenia (ustalone z uzytkownikiem):
- Kazdy plik wejsciowy to archiwum .zip zawierajace katalog (typowo BDOO/)
  z plikami GML o nazwach "<PREFIKS>__OT_XXXX_Y.gml" (obslugiwane jest
  tez rozszerzenie .xml - niektore paczki/wersje danych je uzywaja).
  Naglowek kazdego pliku GML odwoluje sie do schematu przez
  xsi:schemaLocation ze sciezka wzgledna "../XSD/BDOT10k_BDOO.xsd" (tzn.
  katalog XSD/ jako RODZENSTWO katalogu z danymi GML). Rzeczywiste paczki
  BDOO pobrane z geoportalu NIE zawieraja jednak katalogu XSD/ - oba
  wymagane pliki schematow (BDOT10k_BDOO.xsd, KARTO.xsd) sa zaszyte na
  stale w zasobach wtyczki (resources/xsd/) i przy rozpakowywaniu kazdej
  paczki wtyczka automatycznie dogrywa je we wlasciwym miejscu wzgledem
  wykrytych plikow GML (patrz _ensure_embedded_xsd), tak zeby uzytkownik
  nigdy nie musial sam dostarczac pliku XSD.
- Uklad wspolrzednych danych wejsciowych: EPSG:2180 (PL-1992). Jesli
  odczytany z pliku CRS jest inny i rozpoznawalny - dane sa reprojekcje
  do EPSG:2180 w trakcie konwersji posredniej. Jesli CRS jest
  nieznany/nie do odczytania - plik jest pomijany, z wpisem w logu.
- Obiekty z niepustym polem "koniecWersjiObiektu" maja zakonczony cykl
  zycia - opcjonalnie pomijane na zyczenie uzytkownika (filtr SQL na
  etapie konwersji posredniej).
- Warstwy tego samego typu (np. OT_PTWP_A) z roznych wojewodztw trafiaja
  do JEDNEJ wspolnej tabeli/warstwy wyjsciowej.
- Szerokosc kazdego pola tekstowego w warstwie wyjsciowej = dlugosc
  najdluzszej faktycznie wystepujacej wartosci w danych wejsciowych.
- Deduplikacja obiektow scalonych z wielu wojewodztw po polu "lokalnyId".
- Nazwa warstwy wyjsciowej = nazwa pliku wejsciowego bez pierwszego czlonu
  (prefiksu identyfikatora PZGiK), np.
  "PL.PZGiK.201.02__OT_PTWP_A.gml" -> warstwa "OT_PTWP_A".
- Jesli plik wyjsciowy o tej samej nazwie juz istnieje - jest nadpisywany.
- Dodatkowy eksport: jeden plik GML NA KAZDY typ warstwy (nie jeden
  wspolny plik), scalony ze wszystkich wojewodztw, budowany z bazy
  wyjsciowej SQLite/GPKG (nie z surowych XML-i). Pliki trafiaja do
  podkatalogu "BDOO_Polska" wewnatrz katalogu wyjsciowego (tylko pliki
  GML - baza wynikowa i .gdb zostaja bezposrednio w katalogu wyjsciowym)
  i maja na stale doszyty prefiks "PL.PZGiK.201.22__" przed nazwa
  warstwy - ulatwia to rozpoznanie danych przez wtyczki wizualizacyjne
  BDOO. Prefiks dotyczy WYLACZNIE plikow GML - nie zmienia nazw warstw
  w bazie wynikowej ani w geobazie .gdb.
"""

import os
import re
import shutil
import tempfile
import zipfile
import glob
import xml.etree.ElementTree as ET

try:
    from osgeo import ogr, osr, gdal
    ogr.UseExceptions()
    osr.UseExceptions()
    gdal.UseExceptions()
    gdal.SetConfigOption("GML_SKIP_RESOLVE_ELEMS", "ALL")
    # Rozne wojewodztwa moga miec nieznacznie rozne schematy pol dla tej samej
    # warstwy (GML driver wykrywa schemat osobno per plik - pole obecne
    # tylko w czesci wojewodztw, albo o innym wykrytym typie np. Integer vs
    # String). Domyslna "szybka" sciezka VectorTranslate oparta o Arrow
    # wymaga dokladnej zgodnosci schematu zrodla i celu i wywala sie w
    # takich przypadkach ("Cannot find OGR field for Arrow array X",
    # "OGR field type is Integer whereas Arrow type implies String").
    # Klasyczna sciezka (per-obiekt, dopasowanie po nazwie pola) jest
    # tolerancyjna na takie roznice - wylaczamy Arrow kosztem czesci
    # szybkosci zapisu przy scalaniu.
    gdal.SetConfigOption("OGR2OGR_USE_ARROW_API", "NO")
except ImportError:
    ogr = None
    osr = None
    gdal = None

FIELD_LIFECYCLE_END = "koniecWersjiObiektu"
FIELD_LOCAL_ID = "lokalnyId"
# Obslugiwane sa oba rozszerzenia plikow danych - ".gml" (nazewnictwo
# faktycznie uzywane w paczkach BDOO) oraz ".xml" (starsze/alternatywne
# paczki uzywaja tego rozszerzenia dla tych samych danych GML).
# Rozpoznaje DWA typy plikow w paczce:
# 1) "klasyczne" warstwy o kodzie skroconym, np. "OT_ADJA_A", "OT_SKJZ_L"
#    (WIELKIE LITERY/cyfry + podkreslnik + jedna litera typu geometrii),
# 2) pliki pomocnicze/referencyjne bez sufiksu geometrii, nazwane pelna
#    nazwa klasy CamelCase, np. "OT_Ciek", "OT_LiniaKolejowa",
#    "OT_WezelKolejowy", "OT_SzlakDrogowy", "OT_ZbiornikWodny" - to
#    "slowniki" obiektow, na ktore wskazuja pola relacyjne (xlink:href)
#    w niektorych warstwach starego schematu. Rozroznienie po tym, czy
#    czesc po "OT_" jest CALA WIELKA (typ 1) czy zawiera male litery
#    (typ 2) - "OT_" samo w sobie jest rozpoznawane bez wzgledu na
#    wielkosc liter, reszta NIE (musi byc dokladnie tak jak w pliku, zeby
#    nie mylic obu typow).
FILE_NAME_RE = re.compile(
    r"^(?P<prefix>.+?)__(?P<layer>(?i:OT)_(?:[A-Z0-9]+_[A-Z]|[A-Za-z][A-Za-z0-9]*))\.(?i:gml|xml)$"
)
DATA_FILE_GLOB_PATTERNS = ("*.gml", "*.xml")
SOURCE_EPSG = 2180
CACHE_SUBDIR = ".bdoo_cache"

# Rozpoznawanie wersji schematu BDOT10k/BDOO wprost z naglowka pliku GML:
# do konca 2023 r. obowiazywal schemat wg rozporzadzenia z 2011 r.
# (namespace z sufiksem ":1.0"), od 2024 r. obowiazuje schemat wg
# rozporzadzenia z 2021 r. (namespace z sufiksem ":2.0", ten domyslnie
# obslugiwany przez reszte tej wtyczki). Oba sa rozpoznawane po prostym
# tekstowym dopasowaniu namespace w pierwszych bajtach pliku - nie trzeba
# w tym celu uruchamiac GDAL.
SCHEMA_NS_OLD = "bazaDanychObiektowTopograficznych10k:1.0"
SCHEMA_NS_NEW = "bazaDanychObiektowTopograficznych10k:2.0"
SCHEMA_HEADER_PEEK_BYTES = 4096

# Stary schemat (2011/1.0) zagniezdza identyfikator obiektu i informacje
# o cyklu zycia w osobnych podelementach (idIIP/BT_Identyfikator/lokalnyId,
# x_cyklZycia/BT_CyklZyciaInfo/koniecWersjiObiektu), wiec sterownik GML
# spłaszcza je do nazwy kolumny o nieznanej z gory postaci (zalezne od
# konkretnej wersji GDAL). Zamiast zakladac dokladna nazwe, szukamy w
# locie pola KONCZACEGO SIE na ponizszych sufiksach - dziala niezaleznie
# od zastosowanego przez GDAL sposobu spłaszczenia.
FIELD_LOCAL_ID_SUFFIX = "lokalnyid"
FIELD_LIFECYCLE_END_SUFFIX = "koniecwersjiobiektu"

# Pliki schematow XSD wymagane przez sterownik GML (odwolania przez
# xsi:schemaLocation w naglowku kazdego pliku GML), zaszyte na stale w
# zasobach wtyczki - rzeczywiste paczki BDOO ich nie zawieraja. Oba
# zestawy (nowy i stary schemat) sa dokladane jednoczesnie przy kazdym
# rozpakowaniu, niezaleznie od wykrytej wersji danego pliku - to
# najprostszy i najbardziej odporny wariant (nazwy plikow obu schematow
# sie nie pokrywaja, wiec nie ma konfliktu).
XSD_RESOURCE_DIR = os.path.join(os.path.dirname(__file__), "resources", "xsd")
XSD_OLD_RESOURCE_DIR = os.path.join(os.path.dirname(__file__), "resources", "xsd_old")
EMBEDDED_XSD_FILES = ("BDOT10k_BDOO.xsd", "KARTO.xsd")
EMBEDDED_XSD_OLD_FILES = ("OT_BDOT10k_BDOO.xsd", "BT_ModelPodstawowy.xsd",
                          "MZ_MapaZasadnicza.xsd", "OT_BDOT10k_Slowniki.xsd")

# Dane w starym schemacie (1.0) NIE sa mapowane na pola nowego schematu -
# na wyrazne zyczenie uzytkownika importowane sa "jak sa", pod wlasnymi,
# natywnymi nazwami pol, do CALKOWICIE OSOBNEGO podkatalogu wyjsciowego
# (nie sa scalane z warstwami w nowym schemacie, bo zestawy pol sie nie
# pokrywaja). Kazda warstwa trafia jako OSOBNY plik na kazdy zaznaczony
# format (np. OT_PTWP.gpkg, OT_PTWP.sqlite), nazwany samym kodem warstwy -
# BEZ zadnego sufiksu wersji schematu w nazwie (informacja o starym
# schemacie jest tylko w nazwie podkatalogu). Eksport GML nie dotyczy
# starego schematu w ogole (wtyczka wizualizacyjna BDOO IMPORT GML
# zaklada nowy model danych) - zamiast tego w podkatalogu ladowany jest
# jednorazowy plik informacyjny.
OLD_SCHEMA_OUTPUT_SUBDIR = "bdoo_wojewodztwa_stary_schemat"
OLD_SCHEMA_NOGML_NOTICE = "UWAGA_brak_plikow_GML.txt"

# Przestrzenie nazw XML uzywane w plikach GML starego schematu (1.0) -
# potrzebne do bezposredniego parsowania surowego XML przy rozwiazywaniu
# relacji xlink:href (patrz RELATION_MAP nizej). GDAL/OGR nie eksponuje
# tych odwolan jako zwyklych pol (mamy celowo ustawione
# GML_SKIP_RESOLVE_ELEMS=ALL), wiec ta jedna, wyodrebniona czesc
# pipeline'u czyta plik GML bezposrednio przez ElementTree, zamiast przez
# GDAL - jest to jedyne miejsce w calej wtyczce, ktore tak robi.
XML_NS = {
    "gml": "http://www.opengis.net/gml/3.2",
    "ot": "urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:1.0",
    "bt": "urn:gugik:specyfikacje:gmlas:modelPodstawowy:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}

# Mapa "dolaczania" danych z plikow pomocniczych/referencyjnych starego
# schematu (1.0) przez xlink:href - na wyrazne zyczenie uzytkownika.
# Klucz najwyzszego poziomu: nazwa warstwy zrodlowej (bez sufiksu
# __schemat1_0). Wartosc: slownik {nazwa_pola_relacyjnego: specyfikacja}.
# Specyfikacja:
#   "target": nazwa pliku docelowego (bez prefiksu/rozszerzenia), np.
#             "OT_Ciek" dla pliku "<prefiks>__OT_Ciek.xml"
#   "columns": lista (nazwa_kolumny_wynikowej, sciezka) - sciezka to lista
#              segmentow elementow do przejscia wewnatrz obiektu
#              docelowego; ostatni segment moze byc "@atrybut" dla
#              odczytania atrybutu XML zamiast tekstu elementu (np.
#              "@uom" dla <ot:dlugosc uom="km">12.6</ot:dlugosc>).
#   "chain": opcjonalnie - zagniezdzona relacja WEWNATRZ obiektu
#            docelowego (jeden poziom glebiej), o takiej samej strukturze
#            jak wpis najwyzszego poziomu - uzywane dla
#            liniaKolejowa -> wezelKolejowy1/2 -> OT_WezelKolejowy.
# Wynikowe kolumny w bazie sa nazywane
# "<pole_relacyjne>_<nazwa_kolumny>" (a dla lancucha:
# "<pole_relacyjne>_<pole_lancucha>_<nazwa_kolumny>").
RELATION_MAP = {
    "OT_SWRS_L": {
        "ciek1": {
            "target": "OT_Ciek",
            "columns": [
                ("nazwa", ["nazwa"]),
                ("dlugosc", ["dlugosc"]),
                ("dlugosc_uom", ["dlugosc", "@uom"]),
                ("przestrzenNazw", ["PRNG", "BT_ReferencjaDoObiektu", "idIIP", "BT_Identyfikator", "przestrzenNazw"]),
            ],
        },
    },
    "OT_SKTR_L": {
        "liniaKolejowa": {
            "target": "OT_LiniaKolejowa",
            "columns": [
                ("nrLinii", ["nrLinii"]),
            ],
            "chain": {
                "wezelKolejowy1": {"target": "OT_WezelKolejowy", "columns": [("nazwa", ["nazwa"])]},
                "wezelKolejowy2": {"target": "OT_WezelKolejowy", "columns": [("nazwa", ["nazwa"])]},
            },
        },
    },
    "OT_SKDR_L": {
        "szlakDrogowy2": {
            "target": "OT_SzlakDrogowy",
            "columns": [
                ("numer", ["numer"]),
            ],
        },
    },
    "OT_PTWP_A": {
        "zbiornikWodny1": {
            "target": "OT_ZbiornikWodny",
            "columns": [
                ("idPRNG", ["idPRNG"]),
                ("nazwa", ["nazwa"]),
            ],
        },
    },
}


# Dodatkowy eksport GML (opcja "export_merged_gml"): kazdy plik trafia do
# podkatalogu GML_EXPORT_SUBDIR wewnatrz katalogu wyjsciowego (tylko pliki
# GML - baza wynikowa i ewentualna geobaza .gdb zostaja bezposrednio w
# katalogu wyjsciowym) i ma na stale doszyty prefiks GML_EXPORT_PREFIX -
# ulatwia to rozpoznanie plikow przez wtyczki wizualizacyjne BDOO.
GML_EXPORT_SUBDIR = "BDOO_Polska"
GML_EXPORT_PREFIX = "PL.PZGiK.201.22__"

# --- Eksport GML/XML ZGODNY ZE SCHEMATEM (opcjonalny, osobny checkbox) ---
# Ta wersja, w odroznieniu od zwyklego eksportu GML powyzej: (1) wymusza
# kolejnosc pol zgodna z sekwencja zadeklarowana w BDOT10k_BDOO.xsd (nie
# kolejnosc, w jakiej pola zostaly napotkane przy scalaniu wojewodztw),
# (2) uzywa oficjalnej przestrzeni nazw/prefiksu "ot:" i odwolania
# xsi:schemaLocation do wbudowanego pliku XSD (kopiowanego obok wyniku),
# (3) po zapisie WALIDUJE wynik przez lxml wzgledem tego samego XSD -
# plik zostaje zapisany niezaleznie od wyniku walidacji, a przy bledach
# obok niego trafia plik tekstowy z ich lista. Wolniejsze niz zwykly
# eksport GML (dochodzi ustalanie kolejnosci pol + walidacja), dlatego
# to osobna, domyslnie odznaczona opcja w interfejsie.
SCHEMA_GML_OUTPUT_SUBDIR = "BDOO_Polska_zgodny_ze_schematem"
NEW_SCHEMA_TARGET_NAMESPACE = "urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:2.0"
NEW_SCHEMA_XSD_FILENAME = "BDOT10k_BDOO.xsd"

# --- Eksport GeoParquet (opcjonalny, osobny checkbox) ---
# GeoParquet, w odroznieniu od GPKG/SQLite/.gdb, nie mozna zapisac jako
# jednej wspolnej bazy z wieloma warstwami w srodku - kazdy plik .parquet
# to zawsze dokladnie jedna warstwa. Dziala dla OBU schematow (stary i
# nowy) - w przeciwienstwie do GML, GeoParquet nie jest przywiazany do
# zadnej konkretnej wtyczki wizualizacyjnej wymagajacej konkretnego
# ksztaltu danych. Nazwa kazdego pliku pochodzi z pola "nazwa bazy
# wynikowej" (output_name) - ta sama wartosc co dla glownej bazy
# wynikowej, niezaleznie od liczby wojewodztw (bez dynamicznej logiki
# "pierwsze wojewodztwo"/"pojedyncze wojewodztwo"). Pliki dla nowego
# schematu trafiaja do podkatalogu GEOPARQUET_OUTPUT_SUBDIR w katalogu
# wyjsciowym; pliki dla starego schematu - do takiego samego podkatalogu
# wewnatrz podkatalogu starego schematu.
GEOPARQUET_OUTPUT_SUBDIR = "geoparquet"


class ImportCancelled(Exception):
    pass


class BDOOImporter:

    def __init__(self, input_dir, output_dir, log_cb=None, progress_cb=None,
                 is_cancelled_cb=None):
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.log_cb = log_cb or (lambda msg, level="info": None)
        self.progress_cb = progress_cb or (lambda done, total, current: None)
        self.is_cancelled_cb = is_cancelled_cb or (lambda: False)
        self._tmp_root = None
        self._extracted_wojewodztwa = {}
        self._dir_layer_index_cache = {}
        self._old_schema_notice_written = False
        self._old_schema_dir = None
        self._old_working_path = None
        self._old_out_ds = None
        self._old_gpkg_ds = None
        self._old_gdb_ds = None
        self.last_scan_has_old_schema = False
        self.last_scan_has_new_schema = False
        self._compiled_xsd_schema = None
        self._compiled_xsd_schema_attempted = False
        # Szczegolowy raport koncowy: co dokladnie nie zostalo
        # zaimportowane i dlaczego (warstwy oraz - w miare mozliwosci -
        # liczby konkretnych obiektow).
        self.summary = {
            "ok": 0,
            "errors": 0,
            "skipped_layers": [],           # [(layer_name, powod), ...] - cala warstwa pominieta
            "skipped_source_files": [],      # [(kontekst, powod), ...] - .zip/plik pominiety w calosci
            "lifecycle_filtered": {},        # {layer_name: liczba obiektow pominietych z powodu zamknietego cyklu zycia (tylko swiezo przetworzone, nie z cache)}
            "duplicates_removed": {},        # {layer_name: liczba obiektow usunietych jako duplikaty (lokalnyId)}
            "empty_layers_from_xsd": [],     # [layer_name, ...] - warstwy bez danych, utworzone puste ze struktura z XSD
            "had_new_schema": False,
        }

    # ------------------------------------------------------------------
    # Skanowanie zipow -> lista dostepnych warstw
    # ------------------------------------------------------------------

    def scan_available_layers(self):
        # Zachowujemy ORYGINALNA wielkosc liter nazwy warstwy tak jak
        # wystapila w pliku (potrzebne pozniej do wyszukiwania plikow -
        # realne archiwa GUGiK potrafia miec niespojna wielkosc liter
        # miedzy paczkami, wiec normalizowanie tu na sile mogloby
        # rozjechac sie z rzeczywistymi nazwami plikow). Deduplikacja jest
        # jednak bez wzgledu na wielkosc liter - pierwsze napotkane
        # wystapienie danej nazwy wygrywa.
        self.last_scan_has_old_schema = False
        self.last_scan_has_new_schema = False
        layer_names = {}
        zip_paths = self._list_zip_files()
        if not zip_paths:
            self.log_cb("Katalog wejsciowy nie zawiera zadnych plikow .zip.", "error")
            return []
        for zp in zip_paths:
            try:
                with zipfile.ZipFile(zp) as zf:
                    for name in zf.namelist():
                        m = FILE_NAME_RE.match(os.path.basename(name))
                        if m:
                            key = m.group("layer").lower()
                            layer_names.setdefault(key, m.group("layer"))
                            # Tani "peek" naglowka pliku (bez pelnego
                            # otwarcia przez GDAL) - tylko po to, zeby
                            # wiedziec, czy w danych w ogole wystepuje
                            # stary/nowy schemat (uzywane do wyszarzenia
                            # checkboxa GML w interfejsie).
                            try:
                                with zf.open(name) as fh:
                                    head = fh.read(SCHEMA_HEADER_PEEK_BYTES).decode("utf-8", errors="ignore")
                                if SCHEMA_NS_OLD in head:
                                    self.last_scan_has_old_schema = True
                                elif SCHEMA_NS_NEW in head:
                                    self.last_scan_has_new_schema = True
                            except Exception:
                                pass
            except zipfile.BadZipFile:
                self.log_cb(f"Uszkodzone archiwum: {os.path.basename(zp)} - pominieto przy skanowaniu.", "error")
        return sorted(layer_names.values(), key=str.upper)

    def _list_zip_files(self):
        if not os.path.isdir(self.input_dir):
            return []
        return sorted(glob.glob(os.path.join(self.input_dir, "*.zip")))

    # ------------------------------------------------------------------
    # Cache plikow posrednich - sciezki i sprzatanie
    # ------------------------------------------------------------------

    def _cache_dir(self):
        path = os.path.join(self.input_dir, CACHE_SUBDIR)
        os.makedirs(path, exist_ok=True)
        return path

    def _cleanup_cache(self, cache_dir, valid_signature):
        """
        Usuwa z cache'u pliki posrednie, ktore:
          - odnosza sie do wojewodztwa, ktore nie zostalo rozpoznane w tym
            przebiegu (osierocone), albo
          - maja w nazwie inna date modyfikacji niz aktualne zrodlo
            (nieaktualne).
        Wywolywane automatycznie na poczatku kazdego importu.
        valid_signature: {nazwa_wojewodztwa: mtime} dla wojewodztw
        rozpoznanych w BIEZACYM przebiegu (patrz _determine_wojewodztwa -
        wojewodztwo jest rozpoznawane po prefiksie w rozpakowanych
        plikach, nie po nazwie pliku .zip). Loguje liczbe znalezionych
        plikow PRZED rozpoczeciem sprzatania (a nie dopiero po jego
        zakonczeniu) - przy bardzo duzej liczbie osieroconych plikow z
        wczesniejszych, przerwanych/nieudanych przebiegow samo sprzatanie
        (usuwanie plik po pliku z dysku) moze zajac zauwazalnie dlugo, a
        bez tego komunikatu wygladalo to jak zawieszenie wtyczki zamiast
        widocznej, dlugotrwalej operacji.
        """
        if not os.path.isdir(cache_dir):
            return

        try:
            all_files = os.listdir(cache_dir)
        except OSError as exc:
            self.log_cb(f"Nie udalo sie odczytac katalogu cache: {exc}", "warning")
            return

        if len(all_files) > 200:
            self.log_cb(
                f"Sprzatanie cache: znaleziono {len(all_files)} plikow do sprawdzenia - "
                f"to moze chwile potrwac (usuwanie osieroconych/nieaktualnych plikow z dysku).", "info")

        removed = 0
        checked = 0
        for fname in all_files:
            checked += 1
            if len(all_files) > 200 and checked % 500 == 0:
                self.log_cb(f"Sprzatanie cache: sprawdzono {checked}/{len(all_files)} plikow (usunieto dotychczas {removed})...", "info")
            if self.is_cancelled_cb():
                self.log_cb("Sprzatanie cache przerwane przez uzytkownika (przetwarzanie zostanie zatrzymane).", "warning")
                break
            if not fname.endswith(".gpkg"):
                continue
            parts = fname[:-len(".gpkg")].split("__")
            if len(parts) < 3:
                continue
            woj_name, mtime_str = parts[0], parts[-1]
            try:
                file_mtime = int(mtime_str)
            except ValueError:
                continue
            current_mtime = valid_signature.get(woj_name)
            if current_mtime is None or current_mtime != file_mtime:
                try:
                    os.remove(os.path.join(cache_dir, fname))
                    removed += 1
                except OSError:
                    pass
        if removed:
            self.log_cb(f"Sprzatanie cache: usunieto {removed} nieaktualny(ch)/osierocony(ch) plik(ow) posredni(ch).", "info")

    def clear_cache(self):
        """Usuwa caly katalog cache - dostepne np. pod przyciskiem 'Wyczysc cache' w UI."""
        cache_dir = os.path.join(self.input_dir, CACHE_SUBDIR)
        if os.path.isdir(cache_dir):
            shutil.rmtree(cache_dir, ignore_errors=True)
            self.log_cb("Cache plikow posrednich zostal wyczyszczony.", "info")

    # ------------------------------------------------------------------
    # Wlasciwy import
    # ------------------------------------------------------------------

    def run(self, selected_layers, output_name,
            skip_closed_lifecycle=True, single_file_suffix="_BDOO",
            export_gpkg=True, export_gml=False, export_gdb=False, export_sqlite=False,
            export_gml_schema=False, export_gml_schema_validate=True,
            export_geoparquet=False):
        if ogr is None:
            self.log_cb(
                "Modul osgeo.ogr niedostepny. Ta wtyczka wymaga srodowiska "
                "QGIS/OSGeo4W z zainstalowanym GDAL.", "error")
            return self.summary

        if not (export_gpkg or export_gml or export_gdb or export_sqlite or export_geoparquet):
            self.log_cb(
                "Nie wybrano zadnego formatu eksportu (GeoPackage/GML/ESRI geobaza/SQLite/GeoParquet) - "
                "przerwano.", "error")
            return self.summary

        os.makedirs(self.output_dir, exist_ok=True)
        self._tmp_root = tempfile.mkdtemp(prefix="bdoo_import_")
        self._extracted_wojewodztwa = {}
        self._dir_layer_index_cache = {}
        self._old_schema_notice_written = False
        self._old_schema_dir = None
        self._old_working_path = None
        self._old_out_ds = None
        self._old_gpkg_ds = None
        self._old_gdb_ds = None
        self.summary["skipped_layers"] = []
        self.summary["skipped_source_files"] = []
        self.summary["lifecycle_filtered"] = {}
        self.summary["duplicates_removed"] = {}
        self.summary["empty_layers_from_xsd"] = []

        try:
            zip_paths = self._list_zip_files()
            if not zip_paths:
                self.log_cb("Brak plikow .zip w katalogu wejsciowym - przerwano.", "error")
                return self.summary

            wojewodztwa = self._determine_wojewodztwa(zip_paths)
            if not wojewodztwa:
                self.log_cb("Nie udalo sie rozpoznac zadnego wojewodztwa (brak rozpoznawalnych plikow GML w paczkach) - przerwano.", "error")
                return self.summary
            self.log_cb(f"Rozpoznano {len(wojewodztwa)} wojewodztw(o) do przetworzenia.", "info")

            cache_dir = self._cache_dir()
            valid_signature = {name: mtime for name, _zp, mtime in wojewodztwa}
            self._cleanup_cache(cache_dir, valid_signature)
            wojewodztwa = [(name, zp) for name, zp, _mtime in wojewodztwa]

            # Baza robocza jest ZAWSZE SQLite/SpatiaLite, budowana w katalogu
            # tymczasowym - to na niej wykonywane jest scalanie wojewodztw,
            # docinanie szerokosci pol i deduplikacja po lokalnyId. Wszystkie
            # formaty widoczne dla uzytkownika (GeoPackage, GML, ESRI .gdb,
            # a takze sama SQLite na zyczenie) sa z niej eksportowane PO
            # zakonczeniu przetwarzania danej warstwy - dokladnie tak samo,
            # jak dzialal dotad eksport GML/.gdb.
            working_driver = ogr.GetDriverByName("SQLite")
            working_filename = self._build_output_filename(output_name, wojewodztwa, single_file_suffix, ".sqlite")
            working_path = os.path.join(self._tmp_root, working_filename)
            tmp_create_ds = working_driver.CreateDataSource(working_path, options=["SPATIALITE=YES"])
            if tmp_create_ds is None:
                self.log_cb(f"Nie udalo sie utworzyc bazy roboczej: {working_path}", "error")
                return self.summary
            tmp_create_ds = None  # zamknij, zeby ponizej otworzyc jako gdal.Dataset

            # UWAGA: gdal.VectorTranslate() jako cel (destDS) wymaga obiektu
            # osgeo.gdal.Dataset - obiekt zwracany przez driver.CreateDataSource()
            # (osgeo.ogr.DataSource) ma inny typ w wiazaniach Pythona (mimo ze
            # pod spodem to ten sam mechanizm GDAL) i zostaje odrzucony przez
            # SWIG z bledem "argument 1 of type 'GDALDatasetShadow *'". Otwarcie
            # przez gdal.OpenEx z flaga OF_VECTOR daje wlasciwy typ obiektu,
            # ktory nadal wspiera CreateLayer/ExecuteSQL/transakcje tak samo
            # jak ogr.DataSource (API warstwowe zostalo ujednolicone od GDAL 2.x).
            out_ds = gdal.OpenEx(working_path, gdal.OF_VECTOR | gdal.OF_UPDATE)
            if out_ds is None:
                self.log_cb(f"Nie udalo sie otworzyc bazy roboczej do zapisu: {working_path}", "error")
                return self.summary

            # --- opcjonalny eksport rownolegly: GeoPackage ---
            # Tworzony raz na caly przebieg, warstwy dopisywane w miare
            # przetwarzania - kazda warstwa czytana jest z juz gotowej,
            # scalonej i zdeduplikowanej wersji w bazie roboczej (out_ds).
            gpkg_ds = None
            gpkg_path = None
            if export_gpkg:
                gpkg_driver = ogr.GetDriverByName("GPKG")
                gpkg_path = os.path.join(
                    self.output_dir,
                    self._build_output_filename(output_name, wojewodztwa, single_file_suffix, ".gpkg"),
                )
                if os.path.exists(gpkg_path):
                    gpkg_driver.DeleteDataSource(gpkg_path)
                    self.log_cb(f"Plik GeoPackage juz istnial - zostanie nadpisany: {os.path.basename(gpkg_path)}", "warning")
                tmp_gpkg_ds = gpkg_driver.CreateDataSource(gpkg_path)
                if tmp_gpkg_ds is None:
                    self.log_cb(f"Nie udalo sie utworzyc pliku GeoPackage: {gpkg_path}", "error")
                else:
                    tmp_gpkg_ds = None
                    gpkg_ds = gdal.OpenEx(gpkg_path, gdal.OF_VECTOR | gdal.OF_UPDATE)
                    if gpkg_ds is None:
                        self.log_cb(f"Nie udalo sie otworzyc pliku GeoPackage do zapisu: {gpkg_path}", "error")

            # --- opcjonalny eksport rownolegly: geobaza plikowa ESRI (.gdb) ---
            # Tworzona raz na caly przebieg (jak out_ds), warstwy dopisywane
            # do niej w miare przetwarzania - kazda warstwa czytana jest z
            # juz gotowej, scalonej i zdeduplikowanej wersji w out_ds (nie z
            # surowych XML-i), analogicznie do eksportu GML.
            gdb_ds = None
            gdb_path = None
            if export_gdb:
                gdb_driver = ogr.GetDriverByName("OpenFileGDB")
                if gdb_driver is None or not gdb_driver.TestCapability(ogr.ODrCCreateDataSource):
                    self.log_cb(
                        "Sterownik OpenFileGDB z obsluga zapisu niedostepny w tej "
                        "instalacji GDAL (wymagany GDAL >= 3.6) - eksport do .gdb pominiety.",
                        "error")
                else:
                    gdb_path = os.path.join(
                        self.output_dir,
                        self._build_output_filename(output_name, wojewodztwa, single_file_suffix, ".gdb"),
                    )
                    if os.path.exists(gdb_path):
                        gdb_driver.DeleteDataSource(gdb_path)
                        self.log_cb(f"Geobaza .gdb juz istniala - zostanie nadpisana: {os.path.basename(gdb_path)}", "warning")
                    tmp_gdb_ds = gdb_driver.CreateDataSource(gdb_path)
                    if tmp_gdb_ds is None:
                        self.log_cb(f"Nie udalo sie utworzyc geobazy .gdb: {gdb_path}", "error")
                    else:
                        tmp_gdb_ds = None
                        gdb_ds = gdal.OpenEx(gdb_path, gdal.OF_VECTOR | gdal.OF_UPDATE)
                        if gdb_ds is None:
                            self.log_cb(f"Nie udalo sie otworzyc geobazy .gdb do zapisu: {gdb_path}", "error")

            total_units = len(selected_layers) * max(len(wojewodztwa), 1)
            done_units = 0

            for layer_name in selected_layers:
                if self.is_cancelled_cb():
                    raise ImportCancelled()
                try:
                    n_written = self._import_layer(
                        layer_name, wojewodztwa, out_ds, cache_dir,
                        skip_closed_lifecycle=skip_closed_lifecycle,
                        export_merged_gml=export_gml,
                        gdb_ds=gdb_ds,
                        gpkg_ds=gpkg_ds,
                        export_gpkg=export_gpkg,
                        export_gdb=export_gdb,
                        export_sqlite=export_sqlite,
                        output_name=output_name,
                        single_file_suffix=single_file_suffix,
                        export_gml_schema=export_gml_schema,
                        export_gml_schema_validate=export_gml_schema_validate,
                        export_geoparquet=export_geoparquet,
                    )
                    self.log_cb(f"{layer_name}: zaimportowano {n_written} obiektow (po deduplikacji).", "info")
                    self.summary["ok"] += 1
                except ImportCancelled:
                    raise
                except Exception as exc:
                    self.log_cb(f"{layer_name}: blad importu - {exc}", "error")
                    self.summary["errors"] += 1

                done_units += len(wojewodztwa)
                self.progress_cb(done_units, total_units, layer_name)

            # Jesli w tym przebiegu NIE zaimportowano ani jednego obiektu w
            # nowym schemacie (np. katalog wejsciowy zawieral wylacznie
            # archiwalne dane w starym schemacie), glowne pliki wyjsciowe
            # (SQLite/GeoPackage/.gdb) zostaly by utworzone, ale nigdy nie
            # dostaly zadnej warstwy - taki pusty plik (zwlaszcza SQLite) w
            # niektorych przypadkach QGIS/ArcGIS zglasza jako "Invalid Data
            # Source" zamiast po prostu pustego, ale poprawnego zbioru
            # danych. Zamiast zostawiac mylacy, pozornie "zepsuty" plik,
            # usuwamy go i informujemy uzytkownika wprost.
            main_layer_count = out_ds.GetLayerCount() if out_ds is not None else 0
            out_ds = None
            if main_layer_count == 0:
                self.log_cb(
                    "W tym przebiegu nie zaimportowano zadnych danych w NOWYM schemacie (2021) - "
                    "glowna baza wynikowa (SQLite/GeoPackage/.gdb) nie zostala zapisana (nie byloby w niej "
                    "zadnych warstw). Dane w starym schemacie (jesli byly) znajduja sie w podkatalogu "
                    f"'{OLD_SCHEMA_OUTPUT_SUBDIR}'.", "warning")
                if gpkg_ds is not None:
                    gpkg_ds = None
                if gpkg_path and os.path.exists(gpkg_path):
                    try:
                        os.remove(gpkg_path)
                    except OSError:
                        pass
                if gdb_ds is not None:
                    gdb_ds = None
                if gdb_path and os.path.exists(gdb_path):
                    try:
                        if os.path.isdir(gdb_path):
                            shutil.rmtree(gdb_path, ignore_errors=True)
                        else:
                            os.remove(gdb_path)
                    except OSError:
                        pass
            else:
                if gpkg_ds is not None:
                    gpkg_ds = None
                    self.log_cb("Zapisano plik GeoPackage.", "info")
                if gdb_ds is not None:
                    gdb_ds = None
                    self.log_cb("Zapisano geobaze plikowa ESRI (.gdb).", "info")

                if export_sqlite:
                    final_sqlite_path = os.path.join(self.output_dir, working_filename)
                    try:
                        if os.path.exists(final_sqlite_path):
                            os.remove(final_sqlite_path)
                        shutil.copyfile(working_path, final_sqlite_path)
                        self.log_cb(f"Zapisano baze SQLite: {os.path.basename(final_sqlite_path)}.", "info")
                    except OSError as exc:
                        self.log_cb(f"Nie udalo sie skopiowac bazy SQLite do katalogu wyjsciowego: {exc}", "error")

            # --- finalizacja wspolnych baz starego schematu (jesli w ogole
            # jakies dane w starym schemacie wystapily w tym przebiegu) ---
            if self._old_out_ds is not None:
                self._old_out_ds = None
                if self._old_gpkg_ds is not None:
                    self._old_gpkg_ds = None
                    self.log_cb(f"Zapisano plik GeoPackage (stary schemat) w '{OLD_SCHEMA_OUTPUT_SUBDIR}'.", "info")
                if self._old_gdb_ds is not None:
                    self._old_gdb_ds = None
                    self.log_cb(f"Zapisano geobaze plikowa ESRI (stary schemat) w '{OLD_SCHEMA_OUTPUT_SUBDIR}'.", "info")
                if export_sqlite and self._old_working_path and self._old_schema_dir:
                    final_old_sqlite = os.path.join(
                        self._old_schema_dir,
                        self._build_output_filename(output_name, wojewodztwa, single_file_suffix, ".sqlite"),
                    )
                    try:
                        if os.path.exists(final_old_sqlite):
                            os.remove(final_old_sqlite)
                        shutil.copyfile(self._old_working_path, final_old_sqlite)
                        self.log_cb(f"Zapisano baze SQLite (stary schemat): {os.path.basename(final_old_sqlite)}.", "info")
                    except OSError as exc:
                        self.log_cb(f"Nie udalo sie skopiowac bazy SQLite starego schematu: {exc}", "error")

            self.log_cb(
                f"Zakonczono. Warstwy OK: {self.summary['ok']}, "
                f"bledy/pominiete: {self.summary['errors'] + len(self.summary['skipped_layers'])}.",
                "info")
            self._log_detailed_summary()
            return self.summary

        except ImportCancelled:
            self.log_cb("Przetwarzanie zatrzymane przez uzytkownika.", "warning")
            self._log_detailed_summary()
            return self.summary
        finally:
            if self._tmp_root and os.path.isdir(self._tmp_root):
                shutil.rmtree(self._tmp_root, ignore_errors=True)

    # ------------------------------------------------------------------
    # Szczegolowy raport koncowy - co dokladnie nie zostalo
    # zaimportowane i dlaczego
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Eksport GML/XML zgodny ze schematem (opcjonalny, osobny checkbox)
    # ------------------------------------------------------------------

    def _export_schema_compliant_gml(self, layer_name, out_ds, gml_export_prefix, validate=True):
        """
        Eksportuje warstwe do GML z oficjalna przestrzenia nazw/prefiksem
        "ot:" i odwolaniem xsi:schemaLocation do wbudowanego schematu
        (kopiowanego obok wyniku). W odroznieniu od wczesniejszej wersji,
        NIE przeliczuje juz kolejnosci pol w miejscu eksportu - kolejnosc
        zgodna z XSD zostala ustalona RAZ, przy tworzeniu tabeli w bazie
        roboczej (patrz _merge_intermediates), wiec ten eksport to zwykle,
        szybkie skopiowanie calej juz poprawnie uporzadkowanej warstwy
        (geometria jest przenoszona automatycznie - nie trzeba jej
        wymieniac osobno, jak przy wczesniejszym podejsciu przez recznie
        budowane zapytanie SQL, ktore o niej "zapominalo"). Po zapisie,
        jesli 'validate' jest prawdziwe, wynik jest walidowany przez lxml
        wzgledem tego samego XSD - plik zostaje zapisany niezaleznie od
        wyniku walidacji; przy bledach obok niego trafia plik tekstowy
        z ich lista.
        """
        schema_gml_dir = os.path.join(self.output_dir, SCHEMA_GML_OUTPUT_SUBDIR)
        try:
            os.makedirs(schema_gml_dir, exist_ok=True)
        except OSError as exc:
            self.log_cb(f"{layer_name}: nie udalo sie utworzyc katalogu {SCHEMA_GML_OUTPUT_SUBDIR} - {exc}", "error")
            return

        for xsd_name in EMBEDDED_XSD_FILES:
            src = os.path.join(XSD_RESOURCE_DIR, xsd_name)
            dst = os.path.join(schema_gml_dir, xsd_name)
            if os.path.isfile(src) and not os.path.exists(dst):
                try:
                    shutil.copyfile(src, dst)
                except OSError:
                    pass

        gml_path = os.path.join(schema_gml_dir, f"{gml_export_prefix}{layer_name}.gml")
        if os.path.exists(gml_path):
            try:
                os.remove(gml_path)
            except OSError:
                pass

        try:
            options = gdal.VectorTranslateOptions(
                format="GML",
                layers=[layer_name],
                datasetCreationOptions=[
                    "FORMAT=GML3.2",
                    "PREFIX=ot",
                    f"TARGET_NAMESPACE={NEW_SCHEMA_TARGET_NAMESPACE}",
                    f"XSISCHEMAURI={NEW_SCHEMA_XSD_FILENAME}",
                ],
            )
            gdal.VectorTranslate(gml_path, out_ds, options=options)
            self.log_cb(
                f"{layer_name}: zapisano GML zgodny ze schematem "
                f"({SCHEMA_GML_OUTPUT_SUBDIR}/{os.path.basename(gml_path)}).", "info")
        except Exception as exc:
            self.log_cb(f"{layer_name}: eksport GML zgodny ze schematem nie powiodl sie - {exc}", "error")
            return

        self._fixup_gml_id_and_uom(gml_path, layer_name)

        if validate:
            self._validate_gml_against_xsd(gml_path, layer_name, schema_gml_dir)
        else:
            self.log_cb(f"{layer_name}: walidacja XSD pominieta (odznaczona opcja 'Waliduj wynik').", "info")

    def _fixup_gml_id_and_uom(self, gml_path, layer_name):
        """
        Poprawia strukture juz zapisanego pliku GML "zgodnego ze
        schematem": przenosi wartosc pola "gml_id" na wlasciwy atrybut
        XML gml:id obiektu (usuwajac oddzielny element <ot:gml_id>,
        jesli sterownik go zapisal), oraz dla kazdego pola o nazwie
        "{pole}_uom" przenosi jego wartosc na atrybut XML "uom"
        odpowiadajacego elementu "{pole}" (usuwajac oddzielny element
        "<ot:{pole}_uom>"). Generyczny writer GML w GDAL zapisuje oba te
        przypadki jako zwykle, osobne elementy danych, podczas gdy w
        oficjalnym schemacie sa to atrybuty XML - ta poprawka przenosi
        je z powrotem na wlasciwe miejsce, zeby struktura pliku byla
        mozliwie najblizsza 1:1 wobec oficjalnego schematu. Dziala
        bezpiecznie niezaleznie od tego, czy sterownik juz sam obsluguje
        gml_id poprawnie (wtedy po prostu nic nie znajduje do poprawy).
        """
        try:
            import xml.etree.ElementTree as ET
        except ImportError:
            return

        ns_gml = "http://www.opengis.net/gml/3.2"
        ET.register_namespace("gml", ns_gml)
        ET.register_namespace("ot", NEW_SCHEMA_TARGET_NAMESPACE)
        ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
        ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
        # Element opakowujacy <ogr:FeatureCollection> jest zawsze w tej
        # przestrzeni nazw, niezaleznie od ustawionego PREFIX=ot (ktory
        # dotyczy tylko elementow samych obiektow) - rejestrujemy ja tez,
        # zeby przy ponownym zapisie zachowac czytelny prefiks "ogr"
        # zamiast automatycznie wygenerowanego (np. "ns0").
        ET.register_namespace("ogr", "http://ogr.maptools.org/")

        try:
            tree = ET.parse(gml_path)
        except Exception as exc:
            self.log_cb(f"{layer_name}: nie udalo sie wczytac wygenerowanego GML do poprawki gml:id/uom - {exc}", "warning")
            return

        root = tree.getroot()
        gml_id_attr = f"{{{ns_gml}}}id"
        changed = False
        fixed_id_count = 0
        fixed_uom_count = 0

        for member in root.findall(f"{{{ns_gml}}}featureMember"):
            feat = next(iter(member), None)
            if feat is None:
                continue

            gmlid_elem = None
            uom_elems = []
            for child in list(feat):
                local = child.tag.rsplit("}", 1)[-1]
                if local == "gml_id":
                    gmlid_elem = child
                elif local.endswith("_uom"):
                    uom_elems.append((local[: -len("_uom")], child))

            if gmlid_elem is not None:
                if gmlid_elem.text and gml_id_attr not in feat.attrib:
                    feat.set(gml_id_attr, gmlid_elem.text.strip())
                    fixed_id_count += 1
                feat.remove(gmlid_elem)
                changed = True

            for base_name, uom_elem in uom_elems:
                base_elem = None
                for child in list(feat):
                    if child.tag.rsplit("}", 1)[-1] == base_name:
                        base_elem = child
                        break
                if base_elem is not None and uom_elem.text:
                    base_elem.set("uom", uom_elem.text.strip())
                    fixed_uom_count += 1
                feat.remove(uom_elem)
                changed = True

        if not changed:
            return

        try:
            tree.write(gml_path, encoding="UTF-8", xml_declaration=True)
            self.log_cb(
                f"{layer_name}: poprawiono strukture GML ({fixed_id_count} gml:id, "
                f"{fixed_uom_count} atrybut(ow) uom przywroconych z osobnych elementow).", "info")
        except Exception as exc:
            self.log_cb(f"{layer_name}: nie udalo sie zapisac poprawionego GML - {exc}", "warning")
    def _export_geoparquet_layer(self, layer_name, ds, output_name, target_dir):
        """
        Eksportuje jedna warstwe do wlasnego pliku GeoParquet (kazdy plik
        .parquet = dokladnie jedna warstwa, w odroznieniu od GPKG/SQLite/
        .gdb). Dziala identycznie dla nowego i starego schematu - ds i
        target_dir sa przekazywane przez wywolujacego (odpowiednio
        out_ds/GEOPARQUET_OUTPUT_SUBDIR w katalogu wyjsciowym dla nowego
        schematu, albo self._old_out_ds/GEOPARQUET_OUTPUT_SUBDIR wewnatrz
        podkatalogu starego schematu). Nazwa pliku pochodzi z pola "nazwa
        bazy wynikowej" (output_name), nie z prefiksu wojewodztwa.
        """
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as exc:
            self.log_cb(f"{layer_name}: nie udalo sie utworzyc katalogu {target_dir} - {exc}", "error")
            return

        fname = self._sanitize_filename(f"{output_name}__{layer_name}") + ".parquet"
        path = os.path.join(target_dir, fname)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

        try:
            options = gdal.VectorTranslateOptions(format="Parquet", layers=[layer_name])
            gdal.VectorTranslate(path, ds, options=options)
            self.log_cb(f"{layer_name}: zapisano GeoParquet ({os.path.basename(target_dir)}/{fname}).", "info")
        except Exception as exc:
            self.log_cb(f"{layer_name}: eksport GeoParquet nie powiodl sie - {exc}", "error")

    def _get_compiled_xsd_schema(self):
        """
        Kompiluje obiekt schematu XSD (lxml.etree.XMLSchema) RAZ na caly
        czas zycia instancji importera i buforuje go - kompilacja jest
        kosztowna (m.in. rozwiazywanie zdalnych importow gml.xsd/xlink.xsd
        z schemas.opengis.net), wiec bez tego bufora powtarzalaby sie
        osobno dla kazdej pojedynczej warstwy, znaczaco spowalniajac caly
        eksport przy wielu zaznaczonych warstwach. Zwraca None (i loguje
        ostrzezenie DOKLADNIE RAZ na caly przebieg), jesli modul lxml
        jest niedostepny albo kompilacja sie nie powiedzie.
        """
        if self._compiled_xsd_schema is not None or self._compiled_xsd_schema_attempted:
            return self._compiled_xsd_schema
        self._compiled_xsd_schema_attempted = True

        try:
            import lxml.etree as LET
        except ImportError:
            self.log_cb(
                "Modul lxml niedostepny w tym srodowisku Pythona - walidacja XSD bedzie pominieta "
                "dla wszystkich warstw w tym przebiegu (pliki GML i tak zostana zapisane).", "warning")
            return None

        xsd_path = os.path.join(XSD_RESOURCE_DIR, NEW_SCHEMA_XSD_FILENAME)
        if not os.path.isfile(xsd_path):
            return None

        try:
            schema_doc = LET.parse(xsd_path)
            self._compiled_xsd_schema = LET.XMLSchema(schema_doc)
            self.log_cb("Schemat XSD skompilowany do walidacji (jednorazowo, dla calego przebiegu).", "info")
        except Exception as exc:
            self.log_cb(
                f"Nie udalo sie skompilowac schematu XSD do walidacji (mozliwy brak dostepu do sieci "
                f"dla zdalnych importow gml.xsd/xlink.xsd, do ktorych odwoluje sie BDOT10k_BDOO.xsd) - "
                f"walidacja bedzie pominieta dla wszystkich warstw w tym przebiegu: {exc}", "warning")
            self._compiled_xsd_schema = None

        return self._compiled_xsd_schema

    def _validate_gml_against_xsd(self, gml_path, layer_name, out_dir):
        """Waliduje wygenerowany plik GML wzgledem wbudowanego
        BDOT10k_BDOO.xsd przez lxml (schemat skompilowany raz na caly
        przebieg - patrz _get_compiled_xsd_schema). Plik GML zostaje
        zapisany niezaleznie od wyniku - przy bledach walidacji obok
        niego trafia plik tekstowy z ich lista."""
        try:
            import lxml.etree as LET
        except ImportError:
            return  # ostrzezenie juz zalogowane przez _get_compiled_xsd_schema

        schema = self._get_compiled_xsd_schema()
        if schema is None:
            return

        try:
            doc = LET.parse(gml_path)
        except Exception as exc:
            self.log_cb(f"{layer_name}: nie udalo sie wczytac wygenerowanego GML do walidacji - {exc}", "warning")
            return

        if schema.validate(doc):
            self.log_cb(f"{layer_name}: plik GML zgodny ze schematem (walidacja XSD bez bledow).", "info")
            return

        errors = list(schema.error_log)
        self.log_cb(
            f"{layer_name}: plik GML NIE przeszedl walidacji XSD ({len(errors)} problem(ow)) - "
            f"plik i tak zostal zapisany, szczegoly w pliku tekstowym.", "error")
        error_txt_path = os.path.join(out_dir, f"{layer_name}_bledy_walidacji.txt")
        try:
            with open(error_txt_path, "w", encoding="utf-8") as f:
                f.write(f"Blad walidacji GML wzgledem schematu {NEW_SCHEMA_XSD_FILENAME} dla warstwy {layer_name}\n")
                f.write(f"Plik: {os.path.basename(gml_path)}\n\n")
                for err in errors:
                    f.write(f"Linia {err.line}: {err.message}\n")
            self.log_cb(f"{layer_name}: szczegoly bledow walidacji zapisane w {os.path.basename(error_txt_path)}.", "info")
        except OSError as exc:
            self.log_cb(f"Nie udalo sie zapisac pliku z bledami walidacji: {exc}", "warning")

    def _log_detailed_summary(self):
        lines = ["=== Szczegolowy raport koncowy ==="]

        if self.summary["skipped_layers"]:
            lines.append(f"Warstwy pominiete calkowicie ({len(self.summary['skipped_layers'])}):")
            for name, reason in self.summary["skipped_layers"]:
                lines.append(f"  - {name}: {reason}")
        else:
            lines.append("Warstwy pominiete calkowicie: brak.")

        if self.summary["skipped_source_files"]:
            lines.append(f"Pliki .zip pominiete w calosci ({len(self.summary['skipped_source_files'])}):")
            for name, reason in self.summary["skipped_source_files"]:
                lines.append(f"  - {name}: {reason}")

        if self.summary["lifecycle_filtered"]:
            total = sum(self.summary["lifecycle_filtered"].values())
            lines.append(
                f"Obiekty pominiete z powodu zamknietego cyklu zycia, razem {total} "
                f"(tylko dla plikow przetworzonych na swiezo w tym przebiegu - dane wziete z cache "
                f"nie sa tu ponownie liczone):")
            for name, count in sorted(self.summary["lifecycle_filtered"].items()):
                lines.append(f"  - {name}: {count} obiekt(ow)")

        if self.summary["duplicates_removed"]:
            total = sum(self.summary["duplicates_removed"].values())
            lines.append(f"Obiekty usuniete jako duplikaty (po polu lokalnyId), razem {total}:")
            for name, count in sorted(self.summary["duplicates_removed"].items()):
                lines.append(f"  - {name}: {count} obiekt(ow)")

        if self.summary["empty_layers_from_xsd"]:
            lines.append(
                f"Warstwy bez zadnych danych w tym przebiegu, utworzone jako PUSTE "
                f"(0 obiektow, struktura pol z XSD) ({len(self.summary['empty_layers_from_xsd'])}):")
            for name in sorted(self.summary["empty_layers_from_xsd"]):
                lines.append(f"  - {name}")

        if (not self.summary["skipped_layers"] and not self.summary["skipped_source_files"]
                and not self.summary["lifecycle_filtered"] and not self.summary["duplicates_removed"]
                and not self.summary["empty_layers_from_xsd"]):
            lines.append("Wszystko zaimportowane bez pominiec i duplikatow.")

        self.log_cb("\n".join(lines), "info")

    # ------------------------------------------------------------------
    # Rozpoznawanie wojewodztw (po prefiksie w rozpakowanych plikach,
    # NIE po nazwie pliku .zip)
    # ------------------------------------------------------------------

    def _determine_wojewodztwa(self, zip_paths):
        """
        Rozpakowuje kazdy plik .zip i rozpoznaje "nazwe wojewodztwa"
        WYLACZNIE na podstawie prefiksu znalezionego w nazwach
        rozpakowanych plikow GML (np. "PL.PZGiK.201.02__OT_PTWP_A.xml"
        -> prefiks "PL.PZGiK.201.02") - nazwa samego pliku .zip jest
        calkowicie ignorowana. Zwraca liste
        (nazwa_wojewodztwa, sciezka_zip, mtime_zip).
        """
        wojewodztwa = []
        for idx, zp in enumerate(zip_paths):
            if self.is_cancelled_cb():
                raise ImportCancelled()
            extracted_dir = os.path.join(self._tmp_root, f"src_{idx}")
            try:
                self._safe_extract(zp, extracted_dir)
            except zipfile.BadZipFile:
                self.log_cb(f"Uszkodzone archiwum: {os.path.basename(zp)} - pominieto.", "error")
                self.summary["skipped_source_files"].append((os.path.basename(zp), "uszkodzone archiwum .zip"))
                continue

            prefix = None
            for pattern in DATA_FILE_GLOB_PATTERNS:
                for path in glob.glob(os.path.join(extracted_dir, "**", pattern), recursive=True):
                    fm = FILE_NAME_RE.match(os.path.basename(path))
                    if fm:
                        prefix = fm.group("prefix")
                        break
                if prefix:
                    break

            if not prefix:
                self.log_cb(
                    f"{os.path.basename(zp)}: nie udalo sie rozpoznac prefiksu wojewodztwa w zadnym "
                    f"rozpakowanym pliku - pominieto.", "error")
                self.summary["skipped_source_files"].append(
                    (os.path.basename(zp), "nie rozpoznano prefiksu wojewodztwa w zadnym rozpakowanym pliku"))
                continue

            if prefix in self._extracted_wojewodztwa:
                self.log_cb(
                    f"{os.path.basename(zp)}: rozpoznany prefiks '{prefix}' juz wystapil w innym pliku "
                    f".zip - ten plik zostanie pominiety (zduplikowane wojewodztwo).", "warning")
                self.summary["skipped_source_files"].append(
                    (os.path.basename(zp), f"zduplikowane wojewodztwo (prefiks '{prefix}' juz wystapil)"))
                continue

            try:
                mtime = int(os.path.getmtime(zp))
            except OSError:
                self.log_cb(f"{os.path.basename(zp)}: nie udalo sie odczytac daty modyfikacji.", "error")
                self.summary["skipped_source_files"].append((os.path.basename(zp), "nie udalo sie odczytac daty modyfikacji pliku"))
                continue

            self._extracted_wojewodztwa[prefix] = extracted_dir
            wojewodztwa.append((prefix, zp, mtime))

        return wojewodztwa

    # ------------------------------------------------------------------
    # Pomocnicze - ogolne
    # ------------------------------------------------------------------

    def _safe_extract(self, zip_path, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                norm = os.path.normpath(member)
                if norm.startswith("..") or os.path.isabs(norm):
                    continue
            zf.extractall(dest_dir)
        self._ensure_embedded_xsd(dest_dir)

    def _ensure_embedded_xsd(self, extracted_dir):
        """
        Rzeczywiste paczki BDOO nie zawieraja katalogu XSD/, mimo ze
        naglowek kazdego pliku GML odwoluje sie do schematu poprzez
        xsi:schemaLocation ze sciezka wzgledna w postaci "../XSD/<plik>.xsd"
        (tj. katalog XSD/ jako rodzenstwo katalogu z danymi GML, np.
        <rozpakowany_katalog>/BDOO/plik.gml odwoluje sie do
        <rozpakowany_katalog>/XSD/BDOT10k_BDOO.xsd).

        Zeby uzytkownik nigdy nie musial samodzielnie dostarczac pliku XSD,
        wtyczka ma go zaszyty na stale w resources/xsd/ i po rozpakowaniu
        kazdej paczki dogrywa go we wszystkich miejscach, gdzie moze byc
        potrzebny: jako katalog XSD/ rodzenstwo KAZDEGO katalogu
        zawierajacego pliki .gml/.xml (obsluguje to zarowno typowa
        strukture z katalogiem BDOO/, jak i ewentualne plaskie
        rozpakowanie bez katalogu posredniego).
        """
        if not os.path.isdir(XSD_RESOURCE_DIR):
            self.log_cb(
                "Katalog z wbudowanymi schematami XSD (resources/xsd) nie istnieje "
                "w instalacji wtyczki - odczyt GML moze sie nie udac.", "warning")
            return

        data_dirs = set()
        for pattern in DATA_FILE_GLOB_PATTERNS:
            for path in glob.glob(os.path.join(extracted_dir, "**", pattern), recursive=True):
                data_dirs.add(os.path.dirname(path))

        if not data_dirs:
            return

        target_xsd_dirs = set()
        for data_dir in data_dirs:
            target_xsd_dirs.add(os.path.normpath(os.path.join(data_dir, os.pardir, "XSD")))

        for xsd_dir in target_xsd_dirs:
            try:
                os.makedirs(xsd_dir, exist_ok=True)
                for xsd_name in EMBEDDED_XSD_FILES:
                    src = os.path.join(XSD_RESOURCE_DIR, xsd_name)
                    dst = os.path.join(xsd_dir, xsd_name)
                    if os.path.isfile(src) and not os.path.exists(dst):
                        shutil.copyfile(src, dst)
                if os.path.isdir(XSD_OLD_RESOURCE_DIR):
                    for xsd_name in EMBEDDED_XSD_OLD_FILES:
                        src = os.path.join(XSD_OLD_RESOURCE_DIR, xsd_name)
                        dst = os.path.join(xsd_dir, xsd_name)
                        if os.path.isfile(src) and not os.path.exists(dst):
                            shutil.copyfile(src, dst)
            except OSError as exc:
                self.log_cb(f"Nie udalo sie podlozyc wbudowanego XSD w {xsd_dir}: {exc}", "warning")

    def _detect_schema_version(self, gml_path):
        """
        Rozpoznaje wersje schematu BDOT10k/BDOO na podstawie namespace w
        naglowku pliku GML, bez uruchamiania GDAL - wystarczy zajrzec w
        pierwsze kilka KB pliku. Zwraca "old" (schemat 2011, namespace
        z sufiksem ':1.0'), "new" (schemat 2021, sufiks ':2.0') albo None,
        jesli nie udalo sie rozpoznac (plik zostanie wtedy pominiety z
        ostrzezeniem w logu, zamiast probowac go zaimportowac na slepo).
        """
        try:
            with open(gml_path, "rb") as f:
                head = f.read(SCHEMA_HEADER_PEEK_BYTES).decode("utf-8", errors="ignore")
        except OSError:
            return None
        if SCHEMA_NS_NEW in head:
            return "new"
        if SCHEMA_NS_OLD in head:
            return "old"
        return None

    def _find_field_by_suffix(self, defn, suffix):
        """
        Szuka w definicji warstwy OGR pola, ktorego nazwa (bez uwzgl.
        wielkosci liter) konczy sie na podany sufiks. Uzywane dla starego
        schematu, gdzie identyfikator/cykl zycia sa zagniezdzone w
        podelementach i sterownik GML splaszcza je do nazwy kolumny o
        nieznanej z gory dokladnej postaci (np. "idIIP_lokalnyId",
        "idIIP.lokalnyId" - zalezne od wersji GDAL); dziala tak samo dla
        nowego schematu, gdzie pole jest juz plaskie (np. "lokalnyId"
        konczy sie na "lokalnyid" tak samo jak samo siebie).
        """
        suffix = suffix.lower()
        for i in range(defn.GetFieldCount()):
            name = defn.GetFieldDefn(i).GetName()
            if name.lower().endswith(suffix):
                return name
        return None

    # ------------------------------------------------------------------
    # Dolaczanie danych relacyjnych (xlink:href) - wylacznie stary schemat
    # ------------------------------------------------------------------
    # GDAL/OGR (z ustawionym GML_SKIP_RESOLVE_ELEMS=ALL) nie eksponuje
    # wartosci xlink:href jako zwyklych pol, wiec ponizsze funkcje czytaja
    # surowy XML bezposrednio przez ElementTree - to jedyne miejsce w
    # calej wtyczce, ktore nie przechodzi przez GDAL.

    @staticmethod
    def _href_to_gml_id(href):
        if not href:
            return None
        return href.split("#")[-1] if "#" in href else href.lstrip("#")

    @staticmethod
    def _xml_find_element(elem, path_segments):
        """Nawiguje w dol drzewa XML po kolejnych segmentach nazw
        elementow, probujac na kazdym kroku najpierw przestrzen nazw
        "ot", potem "bt" (oba wystepuja naprzemiennie w zaleznosci od
        tego, czy dany element pochodzi z glownego schematu BDOT10k/BDOO
        czy z modelu podstawowego)."""
        current = elem
        for seg in path_segments:
            found = current.find(f"{{{XML_NS['ot']}}}{seg}")
            if found is None:
                found = current.find(f"{{{XML_NS['bt']}}}{seg}")
            if found is None:
                return None
            current = found
        return current

    def _xml_findpath(self, elem, path_segments):
        """Jak _xml_find_element, ale zwraca tekst elementu docelowego,
        albo - jesli ostatni segment sciezki zaczyna sie od "@" - wartosc
        atrybutu XML o tej nazwie (np. ["dlugosc", "@uom"] odczyta
        atrybut uom elementu <ot:dlugosc uom="km">12.6</ot:dlugosc>)."""
        if path_segments and path_segments[-1].startswith("@"):
            attr_name = path_segments[-1][1:]
            target = self._xml_find_element(elem, path_segments[:-1])
            if target is None:
                return None
            return target.get(attr_name)
        target = self._xml_find_element(elem, path_segments)
        if target is None or target.text is None:
            return None
        return target.text.strip() or None

    def _get_dir_layer_index(self, extracted_dir):
        """
        Buduje (i buforuje) indeks wszystkich plikow .gml/.xml w danym
        rozpakowanym katalogu wojewodztwa, pogrupowanych po nazwie warstwy
        (klucz bez wzgledu na wielkosc liter). Bez tego kazde wyszukanie
        pliku warstwy wymagaloby osobnego, pelnego rekurencyjnego
        przeszukania calego katalogu - przy dziesiatkach warstw i
        wojewodztw (oraz dodatkowo relacjach dolaczajacych dane z innych
        plikow) to bardzo kosztowne i dawalo drastyczne spowolnienie
        importu. Indeks budowany jest raz na katalog i pamietany na czas
        calego przebiegu.
        """
        cached = self._dir_layer_index_cache.get(extracted_dir)
        if cached is not None:
            return cached
        index = {}
        for ext in ("gml", "xml"):
            for path in glob.glob(os.path.join(extracted_dir, "**", f"*.{ext}"), recursive=True):
                fm = FILE_NAME_RE.match(os.path.basename(path))
                if fm:
                    index.setdefault(fm.group("layer").lower(), []).append(path)
        self._dir_layer_index_cache[extracted_dir] = index
        return index

    def _find_layer_xml_in_dir(self, extracted_dir, layer_name):
        """Jak wyszukiwanie pliku w _get_or_build_intermediate, ale bez
        zaleznosci od konkretnego wojewodztwa/prefiksu - uzywane do
        odnajdywania plikow docelowych relacji (np. OT_Ciek.xml) w tym
        samym rozpakowanym katalogu. Korzysta z buforowanego indeksu
        katalogu (_get_dir_layer_index)."""
        matches = self._get_dir_layer_index(extracted_dir).get(layer_name.lower())
        return matches[0] if matches else None

    def _parse_gml_own_ids_and_refs(self, xml_path, relation_field_names):
        """Dla kazdego obiektu w pliku GML zwraca jego wlasny lokalnyId
        oraz - dla kazdego z relation_field_names - docelowy gml:id
        odczytany z atrybutu xlink:href danego pola relacyjnego (albo
        None, jesli pole jest puste/brak w tym obiekcie)."""
        result = {}
        try:
            tree = ET.parse(xml_path)
        except Exception as exc:
            self.log_cb(f"Nie udalo sie sparsowac {os.path.basename(xml_path)} do dolaczenia relacji: {exc}", "warning")
            return result
        root = tree.getroot()
        for member in root.findall(f"{{{XML_NS['gml']}}}featureMember"):
            feat = next(iter(member), None)
            if feat is None:
                continue
            own_id = self._xml_findpath(feat, ["idIIP", "BT_Identyfikator", "lokalnyId"])
            if not own_id:
                continue
            refs = {}
            for field in relation_field_names:
                ref_elem = self._xml_find_element(feat, [field])
                href = ref_elem.get(f"{{{XML_NS['xlink']}}}href") if ref_elem is not None else None
                refs[field] = self._href_to_gml_id(href)
            result[own_id] = refs
        return result

    def _parse_gml_aux_attributes(self, xml_path, columns_spec, chain_field_names):
        """Buduje indeks gml:id -> {"cols": {...}, "refs": {...}} dla
        pliku pomocniczego/referencyjnego (np. OT_Ciek.xml). "cols" to
        odczytane wartosci wg columns_spec, "refs" to (opcjonalnie)
        docelowe gml:id pol relacyjnych z chain_field_names - do
        rozwiazania relacji drugiego poziomu (np. wezelKolejowy1/2)."""
        index = {}
        try:
            tree = ET.parse(xml_path)
        except Exception as exc:
            self.log_cb(f"Nie udalo sie sparsowac pliku pomocniczego {os.path.basename(xml_path)}: {exc}", "warning")
            return index
        root = tree.getroot()
        for member in root.findall(f"{{{XML_NS['gml']}}}featureMember"):
            feat = next(iter(member), None)
            if feat is None:
                continue
            gml_id = feat.get(f"{{{XML_NS['gml']}}}id")
            if not gml_id:
                continue
            cols = {}
            for out_col, path in columns_spec:
                cols[out_col] = self._xml_findpath(feat, path)
            refs = {}
            for chain_field in chain_field_names:
                ref_elem = self._xml_find_element(feat, [chain_field])
                href = ref_elem.get(f"{{{XML_NS['xlink']}}}href") if ref_elem is not None else None
                refs[chain_field] = self._href_to_gml_id(href)
            index[gml_id] = {"cols": cols, "refs": refs}
        return index

    def _resolve_relation_for_woj(self, extracted_dir, source_layer_name, woj_name, relation_field, spec):
        """Rozwiazuje jedno pole relacyjne (np. "ciek1") dla jednego
        wojewodztwa: zwraca {wlasny_lokalnyId: {kolumna_wynikowa: wartosc}}
        z kolumnami juz nazwanymi z prefiksem pola relacyjnego (i - dla
        lancucha drugiego poziomu - takze pola lancucha)."""
        self.log_cb(f"[relacje] {source_layer_name}/{woj_name}/{relation_field}: szukam pliku zrodlowego...", "info")
        source_xml = self._find_layer_xml_in_dir(extracted_dir, source_layer_name)
        if not source_xml:
            self.log_cb(f"[relacje] {source_layer_name}/{woj_name}/{relation_field}: brak pliku zrodlowego - pomijam.", "info")
            return {}
        self.log_cb(f"[relacje] {source_layer_name}/{woj_name}/{relation_field}: parsuje plik zrodlowy ({os.path.basename(source_xml)})...", "info")
        own_refs = self._parse_gml_own_ids_and_refs(source_xml, [relation_field])
        self.log_cb(f"[relacje] {source_layer_name}/{woj_name}/{relation_field}: sparsowano {len(own_refs)} obiektow zrodlowych.", "info")

        target_xml = self._find_layer_xml_in_dir(extracted_dir, spec["target"])
        if not target_xml:
            self.log_cb(
                f"{source_layer_name}/{woj_name}: nie znaleziono pliku {spec['target']} do dolaczenia "
                f"danych ({relation_field}) - pominieto dla tego wojewodztwa.", "warning")
            return {}

        chain_specs = spec.get("chain") or {}
        self.log_cb(f"[relacje] {source_layer_name}/{woj_name}/{relation_field}: parsuje plik docelowy ({os.path.basename(target_xml)})...", "info")
        aux_index = self._parse_gml_aux_attributes(target_xml, spec["columns"], list(chain_specs.keys()))
        self.log_cb(f"[relacje] {source_layer_name}/{woj_name}/{relation_field}: sparsowano {len(aux_index)} obiektow docelowych.", "info")

        chain_aux_index = {}
        chain_target_cache = {}
        for chain_field, chain_spec in chain_specs.items():
            self.log_cb(f"[relacje] {source_layer_name}/{woj_name}/{relation_field}: rozwiazuje lancuch '{chain_field}'...", "info")
            chain_target_name = chain_spec["target"]
            if chain_target_name not in chain_target_cache:
                chain_target_xml = self._find_layer_xml_in_dir(extracted_dir, chain_target_name)
                chain_target_cache[chain_target_name] = (
                    self._parse_gml_aux_attributes(chain_target_xml, chain_spec["columns"], [])
                    if chain_target_xml else {}
                )
            chain_aux_index[chain_field] = chain_target_cache[chain_target_name]
            self.log_cb(f"[relacje] {source_layer_name}/{woj_name}/{relation_field}: lancuch '{chain_field}' - sparsowano {len(chain_aux_index[chain_field])} obiektow.", "info")

        self.log_cb(f"[relacje] {source_layer_name}/{woj_name}/{relation_field}: laczenie wynikow ({len(own_refs)} obiektow)...", "info")
        result = {}
        for own_id, refs in own_refs.items():
            target_gml_id = refs.get(relation_field)
            if not target_gml_id:
                continue
            aux_entry = aux_index.get(target_gml_id)
            if not aux_entry:
                continue
            row = {}
            for out_col, val in aux_entry["cols"].items():
                if val is not None:
                    row[f"{relation_field}_{out_col}"] = val
            for chain_field, chain_target_gml_id in aux_entry["refs"].items():
                if not chain_target_gml_id:
                    continue
                chain_entry = chain_aux_index.get(chain_field, {}).get(chain_target_gml_id)
                if chain_entry:
                    for out_col, val in chain_entry["cols"].items():
                        if val is not None:
                            row[f"{relation_field}_{chain_field}_{out_col}"] = val
            if row:
                result[own_id] = row
        self.log_cb(f"[relacje] {source_layer_name}/{woj_name}/{relation_field}: zakonczono - dopasowano {len(result)} obiektow.", "info")
        return result

    def _attach_related_data(self, out_layer_name, local_id_field, relation_spec, old_intermediates, out_ds):
        """Dolacza dane relacyjne (xlink:href) do juz scalonej warstwy
        starego schematu 'out_layer_name' (w praktyce zawsze rowne kodowi
        warstwy, np. "OT_SWRS_L" - stary schemat nie ma juz zadnego
        sufiksu w nazwie warstwy, tylko wlasny podkatalog wyjsciowy).
        Dla kazdego wojewodztwa i kazdego skonfigurowanego w RELATION_MAP
        pola relacyjnego buduje slownik lokalnyId -> nowe kolumny, laczy
        wyniki ze wszystkich wojewodztw, dokleja brakujace kolumny do
        warstwy wyjsciowej i w jednym przebiegu po obiektach wypelnia je
        wartosciami."""
        base_layer_name = out_layer_name

        self.log_cb(f"[relacje] {base_layer_name}: start dolaczania danych relacyjnych dla {len(old_intermediates)} wojewodztw(a).", "info")
        combined = {}
        new_columns = set()
        for woj_name, _ in old_intermediates:
            if self.is_cancelled_cb():
                self.log_cb(f"[relacje] {base_layer_name}: przerwano przez uzytkownika.", "warning")
                return
            extracted_dir = self._extracted_wojewodztwa.get(woj_name)
            if not extracted_dir:
                continue
            for relation_field, spec in relation_spec.items():
                resolved = self._resolve_relation_for_woj(extracted_dir, base_layer_name, woj_name, relation_field, spec)
                for own_id, row in resolved.items():
                    combined.setdefault(own_id, {}).update(row)
                    new_columns.update(row.keys())

        if not new_columns:
            self.log_cb(f"{base_layer_name}: nie udalo sie dolaczyc zadnych danych relacyjnych.", "warning")
            return

        self.log_cb(f"[relacje] {base_layer_name}: tworze {len(new_columns)} nowych kolumn w warstwie wynikowej...", "info")
        out_lyr = out_ds.GetLayerByName(out_layer_name)
        if out_lyr is None:
            self.log_cb(f"[relacje] {base_layer_name}: nie znaleziono warstwy wynikowej '{out_layer_name}' - przerywam.", "error")
            return
        defn = out_lyr.GetLayerDefn()
        for col in sorted(new_columns):
            if defn.GetFieldIndex(col) < 0:
                out_lyr.CreateField(ogr.FieldDefn(col, ogr.OFTString))

        total_features = out_lyr.GetFeatureCount()
        self.log_cb(f"[relacje] {base_layer_name}: wypelniam wartosci - {total_features} obiektow do przejrzenia...", "info")
        updated = 0
        checked = 0
        try:
            out_ds.StartTransaction()
        except Exception:
            pass
        out_lyr.ResetReading()
        feat = out_lyr.GetNextFeature()
        while feat is not None:
            if self.is_cancelled_cb():
                self.log_cb(f"[relacje] {base_layer_name}: przerwano przez uzytkownika w trakcie wypelniania.", "warning")
                break
            checked += 1
            if total_features and checked % 2000 == 0:
                self.log_cb(f"[relacje] {base_layer_name}: przejrzano {checked}/{total_features} obiektow (dopasowano {updated})...", "info")
            local_idx = feat.GetFieldIndex(local_id_field)
            own_id = feat.GetField(local_idx) if local_idx >= 0 else None
            row = combined.get(own_id) if own_id else None
            if row:
                for col, val in row.items():
                    feat.SetField(col, val)
                out_lyr.SetFeature(feat)
                updated += 1
            feat = out_lyr.GetNextFeature()
        try:
            out_ds.CommitTransaction()
        except Exception:
            pass
        self.log_cb(f"[relacje] {base_layer_name}: zakonczono wypelnianie ({checked} przejrzanych, {updated} dopasowanych).", "info")

    def _build_output_filename(self, output_name, wojewodztwa, single_suffix, ext):
        if len(wojewodztwa) == 1:
            return self._sanitize_filename(wojewodztwa[0][0] + single_suffix) + ext
        return self._sanitize_filename(output_name) + ext

    @staticmethod
    def _sanitize_filename(name):
        name = (name or "").strip() or "wynik_BDOO"
        return re.sub(r'[\\/:*?"<>|]', "_", name)

    # ------------------------------------------------------------------
    # Konwersja posrednia (z cache) - jedyne miejsce, gdzie dotykamy XML
    # ------------------------------------------------------------------

    def _get_or_build_intermediate(self, woj_name, zip_path, layer_name,
                                    cache_dir, skip_closed_lifecycle):
        """
        Zwraca sciezke do pliku posredniego (GeoPackage, jedna warstwa
        o nazwie layer_name, juz przefiltrowana i reprojekowana do
        EPSG:2180) dla danego wojewodztwa i typu warstwy. Buduje go, jesli
        nie ma jeszcze aktualnej wersji w cache'u. To jedyny etap calego
        pipeline'u, ktory otwiera oryginalny plik GML/XML - wszystkie
        kolejne etapy (szerokosci pol, scalanie, dedup, eksport GML)
        dzialaja juz na bazach SQLite/GeoPackage.
        """
        try:
            mtime = int(os.path.getmtime(zip_path))
        except OSError:
            self.log_cb(f"{layer_name}/{woj_name}: nie udalo sie odczytac daty modyfikacji pliku .zip.", "error")
            return None

        cache_path_prefix = os.path.join(cache_dir, f"{woj_name}__{layer_name}__")
        extracted_dir = self._extracted_wojewodztwa.get(woj_name)
        if extracted_dir is None:
            self.log_cb(f"{layer_name}/{woj_name}: brak rozpakowanych danych dla tego wojewodztwa - pominieto.", "error")
            return None

        # Wyszukiwanie plikow bez wzgledu na wielkosc liter nazwy warstwy -
        # realne archiwa GUGiK potrafia miec niespojna wielkosc liter
        # miedzy paczkami. Korzystamy z buforowanego indeksu calego
        # katalogu (_get_dir_layer_index) zamiast osobnego, pelnego
        # rekurencyjnego przeszukania przy kazdym wywolaniu tej metody -
        # to ostatnie przy dziesiatkach warstw x wojewodztw bylo bardzo
        # kosztowne (widoczne jako zawieszenie/drastyczne spowolnienie
        # importu). Nazwa wojewodztwa jest teraz rozpoznawana wylacznie
        # po prefiksie w rozpakowanych plikach (patrz _determine_wojewodztwa)
        # - nie porownujemy juz z nazwa pliku .zip, wiec nie ma tu zadnej
        # walidacji prefiksu do zrobienia.
        matches = self._get_dir_layer_index(extracted_dir).get(layer_name.lower(), [])
        if not matches:
            self.log_cb(f"{layer_name}/{woj_name}: brak pliku dla tego wojewodztwa - pominieto (wojewodztwo moze nie miec tej warstwy).", "warning")
            return None
        xml_path = matches[0]

        schema_version = self._detect_schema_version(xml_path)
        if schema_version is None:
            self.log_cb(
                f"{layer_name}/{woj_name}: nierozpoznany schemat GML (namespace nie pasuje ani do "
                f"schematu 2011 ani 2021) - plik pominieto.", "error")
            return None

        # Wersja schematu jest czescia klucza cache - bez tego trafienie w
        # cache zwracaloby sama sciezke bez informacji o schemacie, ktorej
        # potrzebuje wywolujacy (_import_layer decyduje na tej podstawie,
        # czy warstwa idzie do scalonej tabeli glownej, czy do osobnej
        # "__schemat1_0"). Sprawdzamy cache dopiero TERAZ (po tanim etapie
        # dopasowania nazwy pliku i odczytu naglowka), a przed kosztownym
        # ogr.Open() calego pliku GML - zeby przy trafieniu w cache w ogole
        # nie dotykac zrodlowego pliku.
        cache_path = cache_path_prefix + f"{schema_version}__{mtime}.gpkg"
        if os.path.exists(cache_path):
            return (cache_path, schema_version)

        src_ds = ogr.Open(xml_path)
        if src_ds is None:
            self.log_cb(f"{layer_name}/{woj_name}: nie udalo sie otworzyc pliku GML.", "error")
            return None
        src_lyr = self._find_layer_in_ds(src_ds, layer_name)
        if src_lyr is None:
            self.log_cb(f"{layer_name}/{woj_name}: warstwa nie zawiera danych (pusta lub brak elementow) - pominieto.", "warning")
            src_ds = None
            return None
        src_layer_actual_name = src_lyr.GetName()
        src_srs = src_lyr.GetSpatialRef()
        total_before_filter = src_lyr.GetFeatureCount()
        # Sufiksowe dopasowanie pola dziala jednakowo dla obu schematow:
        # w nowym schemacie "koniecWersjiObiektu" jest juz plaskim polem
        # (dopasuje samo siebie), w starym jest zagniezdzone i sterownik
        # GML splaszcza je do nazwy zawierajacej ten sam sufiks.
        lifecycle_field_name = self._find_field_by_suffix(src_lyr.GetLayerDefn(), FIELD_LIFECYCLE_END_SUFFIX)
        has_lifecycle_field = lifecycle_field_name is not None
        src_ds = None

        where = None
        if skip_closed_lifecycle and has_lifecycle_field:
            # Uwaga: sterownik GML uzywa domyslnego dialektu SQL OGR-a, ktory
            # NIE obsluguje funkcji TRIM() (blad "Undefined function 'TRIM'
            # used"). Zawezony warunek: NULL lub dokladnie pusty string -
            # nie lapie wartosci zlozonych z samych bialych znakow, ale to
            # skrajnie maloprawdopodobny przypadek w danych BDOT10k.
            where = '("' + lifecycle_field_name + '" IS NULL OR "' + lifecycle_field_name + '" = \'\')'
        elif skip_closed_lifecycle and not has_lifecycle_field:
            self.log_cb(
                f"{layer_name}/{woj_name}: nie znaleziono pola cyklu zycia (sufiks '{FIELD_LIFECYCLE_END}') - "
                f"filtr pominiety dla tej warstwy. Najczesciej oznacza to, ze w tym pliku ZADEN obiekt nigdy nie "
                f"mial wypelnionego tego pola (nic tu jeszcze nie zostalo 'zamkniete'/zastapione), wiec sterownik "
                f"GML nie utworzyl dla niego kolumny - to normalne dla danych archiwalnych, nie blad.", "warning")

        vt_kwargs = dict(
            format="GPKG",
            layers=[src_layer_actual_name],
            layerName=layer_name,
            where=where,
            makeValid=False,
        )
        if src_srs is None:
            self.log_cb(f"{layer_name}/{woj_name}: brak zdefiniowanego CRS w danych - przyjeto EPSG:{SOURCE_EPSG} bez reprojekcji.", "warning")
        else:
            vt_kwargs["dstSRS"] = f"EPSG:{SOURCE_EPSG}"
            vt_kwargs["reproject"] = True

        try:
            options = gdal.VectorTranslateOptions(**vt_kwargs)
            tmp_cache_path = cache_path + ".tmp"
            if os.path.exists(tmp_cache_path):
                os.remove(tmp_cache_path)
            result_ds = gdal.VectorTranslate(tmp_cache_path, xml_path, options=options)
            if result_ds is None:
                raise RuntimeError("gdal.VectorTranslate zwrocilo pusty wynik")
            result_ds = None
            os.replace(tmp_cache_path, cache_path)
        except Exception as exc:
            self.log_cb(f"{layer_name}/{woj_name}: blad konwersji posredniej - {exc}", "error")
            return None

        # Liczba obiektow pominietych przez filtr zamknietego cyklu zycia
        # w TYM pliku (tylko przy swiezej konwersji - przy trafieniu w
        # cache ten etap sie nie wykonuje, wiec nie dolicza sie ponownie;
        # wartosc w koncowym raporcie moze wiec nie objac plikow uzytych
        # z cache w danym przebiegu).
        if where is not None:
            try:
                check_ds = ogr.Open(cache_path)
                total_after_filter = check_ds.GetLayerByIndex(0).GetFeatureCount() if check_ds else None
                check_ds = None
                if total_after_filter is not None:
                    filtered_out = max(total_before_filter - total_after_filter, 0)
                    if filtered_out:
                        self.summary["lifecycle_filtered"][layer_name] = (
                            self.summary["lifecycle_filtered"].get(layer_name, 0) + filtered_out)
            except Exception:
                pass

        return (cache_path, schema_version)

    # ------------------------------------------------------------------
    # Import pojedynczej warstwy (scalanie wielu wojewodztw)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Puste warstwy strukturalne (brak danych w zadnym powiecie) - tylko
    # nowy schemat, struktura pol odczytana z wbudowanego XSD
    # ------------------------------------------------------------------

    @staticmethod
    def _xsd_map_simple_type(type_name):
        """Mapuje nazwe prostego typu XSD na typ pola OGR. Wszystko,
        czego nie rozpoznajemy (w tym zlozone typy list kodowych) domyslnie
        trafia do OFTString - bezpieczny wybor dla pustej warstwy
        placeholder, gdzie liczy sie przede wszystkim obecnosc wlasciwych
        nazw kolumn, nie scisly typ danych."""
        t = (type_name or "").split(":")[-1].lower()
        if t in ("integer", "int", "nonnegativeinteger", "positiveinteger", "short", "long"):
            return ogr.OFTInteger
        if t in ("double", "decimal", "float"):
            return ogr.OFTReal
        if t == "date":
            return ogr.OFTDate
        if t == "datetime":
            return ogr.OFTDateTime
        if t == "boolean":
            return ogr.OFTInteger
        return ogr.OFTString

    @staticmethod
    def _xsd_map_geometry_type(type_name):
        """Mapuje nazwe typu geometrii GML (np. "SurfacePropertyType")
        na typ geometrii OGR. Nierozpoznany typ -> wbUnknown (generyczny,
        wciaz poprawny dla pustej warstwy, tylko bez konkretnej ikony
        typu geometrii w QGIS)."""
        t = (type_name or "").split(":")[-1]
        mapping = {
            "PointPropertyType": ogr.wkbPoint,
            "MultiPointPropertyType": ogr.wkbMultiPoint,
            "CurvePropertyType": ogr.wkbLineString,
            "MultiCurvePropertyType": ogr.wkbMultiLineString,
            "SurfacePropertyType": ogr.wkbPolygon,
            "MultiSurfacePropertyType": ogr.wkbMultiPolygon,
            "GeometryPropertyType": ogr.wkbUnknown,
        }
        return mapping.get(t, ogr.wkbUnknown)

    def _get_xsd_layer_structure(self, layer_name):
        """
        Odczytuje z wbudowanego BDOT10k_BDOO.xsd (nowy schemat) liste pol
        warstwy WRAZ Z TYPAMI (rekurencyjnie, przez cala sciezke
        dziedziczenia <extension base="...">), oraz typ geometrii (z pola
        "geometria", jesli sie znajdzie). Zwraca (lista_pol, geom_type),
        gdzie lista_pol to [(nazwa, typ_OGR), ...]. Zwraca ([], None),
        jesli nie udalo sie ustalic struktury (np. warstwa w ogole nie
        wystepuje w tym schemacie).
        """
        try:
            xsd_path = os.path.join(XSD_RESOURCE_DIR, NEW_SCHEMA_XSD_FILENAME)
            if not os.path.isfile(xsd_path):
                return [], None
            xsd_text = open(xsd_path, encoding="utf-8").read()

            def get_type_name(text, element_name):
                m = re.search(
                    r'<element name="' + re.escape(element_name) + r'"[^/>]*type="(?:[a-zA-Z0-9]+:)?([A-Za-z0-9_]+)"',
                    text)
                return m.group(1) if m else None

            def get_complextype_block(text, type_name):
                pat = re.compile(r'<complexType name="' + re.escape(type_name) + r'"[^>]*>(.*?)\n\t</complexType>', re.S)
                m = pat.search(text)
                return m.group(1) if m else None

            def get_own_fields_with_types(block):
                # (nazwa, typ) dla elementow z jawnym atrybutem type=...;
                # elementy bez jawnego typu (np. anonimowe complexType z
                # atrybutem uom) pomijamy tu swiadomie - i tak nie
                # wystepuja w danych, ktorych nigdy nie widzielismy.
                return re.findall(r'<element name="([^"]+)"[^/>]*type="(?:[a-zA-Z0-9]+:)?([A-Za-z0-9_]+)"', block)

            def get_base(block):
                m = re.search(r'<extension base="(?:[a-zA-Z0-9]+:)?([A-Za-z0-9_]+)"', block)
                return m.group(1) if m else None

            def resolve_fields(text, type_name, seen=None):
                if seen is None:
                    seen = set()
                if type_name in seen:
                    return []
                seen.add(type_name)
                block = get_complextype_block(text, type_name)
                if block is None:
                    return None
                own = get_own_fields_with_types(block)
                base = get_base(block)
                base_fields = []
                if base and base != "AbstractFeatureType":
                    r = resolve_fields(text, base, seen)
                    base_fields = r if r else []
                return base_fields + own

            type_name = get_type_name(xsd_text, layer_name)
            if not type_name:
                return [], None
            raw_fields = resolve_fields(xsd_text, type_name)
            if not raw_fields:
                return [], None

            fields = []
            geom_type = None
            seen_names = set()
            for fname, ftype in raw_fields:
                if fname in seen_names:
                    continue
                seen_names.add(fname)
                if fname == "geometria":
                    geom_type = self._xsd_map_geometry_type(ftype)
                    continue
                fields.append((fname, self._xsd_map_simple_type(ftype)))
            return fields, geom_type
        except Exception as exc:
            self.log_cb(f"Nie udalo sie ustalic struktury pol z XSD dla {layer_name}: {exc}", "warning")
            return [], None

    def _create_empty_layer_from_xsd(self, layer_name, out_ds):
        """
        Tworzy PUSTA warstwe (0 obiektow) w bazie roboczej out_ds, z
        kolumnami i ich typami odczytanymi z BDOT10k_BDOO.xsd (nowy
        schemat) - dla warstw, dla ktorych w tym przebiegu nie znaleziono
        ZADNYCH danych zrodlowych w zadnym przetworzonym powiecie (ani w
        starym, ani w nowym schemacie). Zwraca True, jesli sie udalo
        (struktura zostala rozpoznana w XSD), False w przeciwnym razie
        (warstwa zostaje wtedy pominieta jak dotychczas).
        """
        if out_ds.GetLayerByName(layer_name) is not None:
            return True
        fields, geom_type = self._get_xsd_layer_structure(layer_name)
        if not fields:
            return False
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(SOURCE_EPSG)
        lyr = out_ds.CreateLayer(layer_name, srs=srs, geom_type=geom_type if geom_type is not None else ogr.wkbUnknown)
        for fname, ftype in fields:
            lyr.CreateField(ogr.FieldDefn(fname, ftype))
        return True

    def _merge_intermediates(self, out_layer_name, intermediates, out_ds, layer_log_label):
        """
        Wspolna logika scalania listy plikow posrednich (jednego typu
        warstwy) do warstwy `out_layer_name` w `out_ds`: zsumowany schemat
        (etap 1a/1b), append w transakcji (etap 2), deduplikacja po polu
        konczacym sie na "lokalnyId" (etap 3, dopasowanie sufiksowe -
        dziala dla plaskiego pola w nowym schemacie i splaszczonego w
        starym). Zwraca liczbe zapisanych obiektow (po deduplikacji).
        Wydzielone z _import_layer, zeby ten sam mechanizm obslugiwal
        zarowno normalna sciezke (nowy schemat), jak i osobna sciezke dla
        danych w starym schemacie (patrz _export_old_schema_layer).
        """
        field_order = []
        field_types = {}
        geom_type = None
        local_id_field_name = None
        type_conflicts_logged = set()

        for woj_name, interm_path in intermediates:
            ds = ogr.Open(interm_path)
            lyr = ds.GetLayerByName(out_layer_name) or ds.GetLayerByIndex(0)
            defn = lyr.GetLayerDefn()
            if geom_type is None:
                geom_type = defn.GetGeomType()
            if local_id_field_name is None:
                local_id_field_name = self._find_field_by_suffix(defn, FIELD_LOCAL_ID_SUFFIX)
            for i in range(defn.GetFieldCount()):
                fdef = defn.GetFieldDefn(i)
                fname = fdef.GetName()
                ftype = fdef.GetType()
                if fname not in field_types:
                    field_order.append(fname)
                    field_types[fname] = ftype
                else:
                    widened = self._widen_field_type(field_types[fname], ftype)
                    if widened != field_types[fname] and fname not in type_conflicts_logged:
                        self.log_cb(
                            f"{layer_log_label}: pole {fname} ma rozne typy w roznych wojewodztwach - "
                            f"uzyto szerszego typu.", "warning")
                        type_conflicts_logged.add(fname)
                    field_types[fname] = widened
            ds = None

        field_widths = {}
        string_fields = {f for f, t in field_types.items() if t == ogr.OFTString}
        if string_fields:
            for woj_name, interm_path in intermediates:
                ds = ogr.Open(interm_path)
                lyr = ds.GetLayerByName(out_layer_name) or ds.GetLayerByIndex(0)
                actual_name = lyr.GetName()
                for fname in string_fields:
                    if lyr.GetLayerDefn().GetFieldIndex(fname) < 0:
                        continue
                    sql = f'SELECT MAX(LENGTH("{fname}")) AS w FROM "{actual_name}"'
                    try:
                        result = ds.ExecuteSQL(sql)
                        feat = result.GetNextFeature() if result else None
                        w = feat.GetField("w") if feat is not None else None
                        if result:
                            ds.ReleaseResultSet(result)
                        field_widths[fname] = max(field_widths.get(fname, 0), int(w or 0))
                    except Exception:
                        pass
                ds = None

        srs = osr.SpatialReference()
        srs.ImportFromEPSG(SOURCE_EPSG)

        out_lyr = out_ds.GetLayerByName(out_layer_name)
        if out_lyr is None:
            # Kolejnosc pol w tabeli roboczej jest ustalana RAZ, w tym
            # miejscu (przy tworzeniu warstwy) - to NATURALNA kolejnosc
            # napotykania pol podczas skanowania warstwy posredniej
            # (field_order, budowany wyzej), czyli dokladnie taka, w
            # jakiej sterownik GML w GDAL odczytal je z ORYGINALNEGO
            # pliku zrodlowego (dla wielu wojewodztw: kolejnosc
            # pierwszego napotkanego pliku, z dolaczonymi na koncu
            # ewentualnymi dodatkowymi polami z kolejnych plikow, ktorych
            # tamten nie mial). To najblizsze mozliwe odwzorowanie 1:1
            # struktury pliku wejsciowego - bez zadnego przestawiania wg
            # zewnetrznej referencji (jak sekwencja w XSD), ktora dla
            # pol typu gml_id/*_uom (nieobecnych w XSD jako osobne
            # elementy) i tak zawsze pchala je na koniec, a dla starego
            # schematu w ogole nie mialaby zastosowania.
            out_lyr = out_ds.CreateLayer(out_layer_name, srs=srs, geom_type=geom_type)
            for fname in field_order:
                new_fdef = ogr.FieldDefn(fname, field_types[fname])
                if field_types[fname] == ogr.OFTString:
                    new_fdef.SetWidth(max(field_widths.get(fname, 1), 1))
                out_lyr.CreateField(new_fdef)

        try:
            out_ds.StartTransaction()
        except Exception:
            pass

        try:
            for woj_name, interm_path in intermediates:
                if self.is_cancelled_cb():
                    raise ImportCancelled()
                src_ds_tmp = ogr.Open(interm_path)
                actual_name = (src_ds_tmp.GetLayerByName(out_layer_name) or src_ds_tmp.GetLayerByIndex(0)).GetName()
                src_ds_tmp = None
                options = gdal.VectorTranslateOptions(
                    accessMode="append",
                    layers=[actual_name],
                    layerName=out_layer_name,
                )
                gdal.VectorTranslate(out_ds, interm_path, options=options)
            try:
                out_ds.CommitTransaction()
            except Exception:
                pass
        except ImportCancelled:
            try:
                out_ds.RollbackTransaction()
            except Exception:
                pass
            raise
        except Exception:
            try:
                out_ds.RollbackTransaction()
            except Exception:
                pass
            raise

        if local_id_field_name:
            count_before = out_lyr.GetFeatureCount()
            dedup_sql = (
                f'DELETE FROM "{out_layer_name}" WHERE rowid NOT IN '
                f'(SELECT MIN(rowid) FROM "{out_layer_name}" GROUP BY "{local_id_field_name}")'
            )
            try:
                out_ds.ExecuteSQL(dedup_sql)
                removed = max(count_before - out_lyr.GetFeatureCount(), 0)
                if removed:
                    self.summary["duplicates_removed"][layer_log_label] = (
                        self.summary["duplicates_removed"].get(layer_log_label, 0) + removed)
            except Exception as exc:
                self.log_cb(f"{layer_log_label}: deduplikacja po {local_id_field_name} nie powiodla sie - {exc}", "warning")
        else:
            self.log_cb(f"{layer_log_label}: nie znaleziono pola identyfikatora (sufiks '{FIELD_LOCAL_ID}') - deduplikacja pominieta.", "warning")

        return (out_lyr.GetFeatureCount(), local_id_field_name)

    def _import_layer(self, layer_name, wojewodztwa, out_ds, cache_dir,
                       skip_closed_lifecycle, export_merged_gml, gdb_ds=None, gpkg_ds=None,
                       export_gpkg=False, export_gdb=False, export_sqlite=False,
                       output_name=None, single_file_suffix="_BDOO", export_gml_schema=False,
                       export_gml_schema_validate=True, export_geoparquet=False):
        # --- etap 0: konwersja posrednia kazdego wojewodztwa (z cache) ---
        # Kazdy plik GML jest po drodze rozpoznawany co do wersji schematu
        # (namespace w naglowku) - pliki w starym schemacie (2011/1.0) NIE
        # sa mieszane/mapowane na pola nowego schematu (2021/2.0), tylko
        # eksportowane calkowicie osobno (patrz _merge_old_schema_layer) -
        # na wyrazne zyczenie uzytkownika, zeby dane archiwalne byly
        # dostepne "jak sa", bez prob dopasowania do nowego modelu danych.
        new_intermediates = []
        old_intermediates = []
        for woj_name, zip_path in wojewodztwa:
            if self.is_cancelled_cb():
                raise ImportCancelled()
            result = self._get_or_build_intermediate(
                woj_name, zip_path, layer_name, cache_dir, skip_closed_lifecycle)
            if not result:
                continue
            path, schema_version = result
            if schema_version == "old":
                old_intermediates.append((woj_name, path))
            else:
                new_intermediates.append((woj_name, path))

        if not new_intermediates and not old_intermediates:
            # Brak jakichkolwiek danych zrodlowych dla tej warstwy w
            # calym przebiegu - zamiast pomijac ja calkowicie, probujemy
            # utworzyc PUSTA warstwe (0 obiektow) ze struktura pol
            # odczytana z wbudowanego BDOT10k_BDOO.xsd (tylko nowy
            # schemat - stary schemat nie ma jednoznacznego,
            # jeden-do-jednego odpowiednika struktury w tym pliku).
            # Dzieki temu warstwa jest zawsze obecna we wszystkich
            # zaznaczonych formatach eksportu, z wlasciwymi kolumnami,
            # nawet jesli w danym imporcie nie mialo jej zadne
            # wojewodztwo.
            layer_created = self._create_empty_layer_from_xsd(layer_name, out_ds)
            if layer_created:
                self.log_cb(
                    f"{layer_name}: brak danych zrodlowych w zadnym przetworzonym wojewodztwie - "
                    f"utworzono PUSTA warstwe (0 obiektow) ze struktura pol z XSD.", "warning")
                self.summary["empty_layers_from_xsd"].append(layer_name)
                new_intermediates_present = True
            else:
                self.log_cb(f"{layer_name}: brak poprawnych danych zrodlowych we wszystkich wojewodztwach - warstwa pominieta.", "warning")
                self.summary["skipped_layers"].append(
                    (layer_name, "brak poprawnych danych zrodlowych w zadnym z przetworzonych wojewodztw"))
                return 0
        else:
            new_intermediates_present = bool(new_intermediates)

        written = 0

        if new_intermediates:
            n_new, _ = self._merge_intermediates(layer_name, new_intermediates, out_ds, layer_name)
            written += n_new
            if n_new > 0:
                self.summary["had_new_schema"] = True

        if old_intermediates:
            n_old = self._merge_old_schema_layer(
                layer_name, old_intermediates, wojewodztwa,
                export_gpkg=export_gpkg, export_gdb=export_gdb, export_sqlite=export_sqlite,
                output_name=output_name, single_file_suffix=single_file_suffix,
                export_geoparquet=export_geoparquet)
            written += n_old

        # --- eksport GML - CZYTAMY Z BAZY WYJSCIOWEJ (out_ds), NIE Z XML ---
        # Dotyczy wylacznie warstwy w nowym schemacie (ta konwencja nazw/
        # prefiksu jest przeznaczona pod wtyczke wizualizacyjna BDOO, ktora
        # zaklada nowy model danych) - dane w starym schemacie do eksportu
        # GML nie trafiaja (patrz plik informacyjny w podkatalogu starego
        # schematu, generowany przez _merge_old_schema_layer).
        if export_merged_gml and new_intermediates_present:
            gml_dir = os.path.join(self.output_dir, GML_EXPORT_SUBDIR)
            try:
                os.makedirs(gml_dir, exist_ok=True)
            except OSError as exc:
                self.log_cb(f"{layer_name}: nie udalo sie utworzyc katalogu {GML_EXPORT_SUBDIR} - {exc}", "error")
                gml_dir = None
            if gml_dir is not None:
                gml_path = os.path.join(gml_dir, f"{GML_EXPORT_PREFIX}{layer_name}.gml")
                if os.path.exists(gml_path):
                    try:
                        os.remove(gml_path)
                    except OSError:
                        pass
                try:
                    gml_options = gdal.VectorTranslateOptions(
                        format="GML",
                        layers=[layer_name],
                    )
                    gdal.VectorTranslate(gml_path, out_ds, options=gml_options)
                    self.log_cb(f"{layer_name}: zapisano scalony plik GML z bazy wyjsciowej ({GML_EXPORT_SUBDIR}/{os.path.basename(gml_path)}).", "info")
                except Exception as exc:
                    self.log_cb(f"{layer_name}: eksport GML nie powiodl sie - {exc}", "error")

        if export_gml_schema and new_intermediates_present:
            self._export_schema_compliant_gml(
                layer_name, out_ds, GML_EXPORT_PREFIX, validate=export_gml_schema_validate)

        if export_geoparquet and new_intermediates_present:
            self._export_geoparquet_layer(
                layer_name, out_ds, output_name,
                os.path.join(self.output_dir, GEOPARQUET_OUTPUT_SUBDIR))

        # --- eksport do GeoPackage / .gdb - rowniez z out_ds, dotyczy
        # wylacznie warstwy w nowym schemacie (stary schemat ma wlasny,
        # calkowicie osobny, ale rowniez scalony eksport - patrz
        # _merge_old_schema_layer) ---
        if new_intermediates_present:
            if gpkg_ds is not None:
                try:
                    gpkg_options = gdal.VectorTranslateOptions(layers=[layer_name], layerName=layer_name)
                    gdal.VectorTranslate(gpkg_ds, out_ds, options=gpkg_options)
                    self.log_cb(f"{layer_name}: zapisano warstwe do GeoPackage.", "info")
                except Exception as exc:
                    self.log_cb(f"{layer_name}: eksport do GeoPackage nie powiodl sie - {exc}", "error")

            if gdb_ds is not None:
                try:
                    gdb_options = gdal.VectorTranslateOptions(layers=[layer_name], layerName=layer_name)
                    gdal.VectorTranslate(gdb_ds, out_ds, options=gdb_options)
                    self.log_cb(f"{layer_name}: zapisano warstwe do geobazy .gdb.", "info")
                except Exception as exc:
                    self.log_cb(f"{layer_name}: eksport do .gdb nie powiodl sie - {exc}", "error")

        return written

    def _ensure_old_schema_datasources(self, wojewodztwa, output_name, single_file_suffix,
                                        export_gpkg, export_gdb, export_sqlite):
        """
        Tworzy (TYLKO RAZ na caly przebieg, przy pierwszym napotkanym
        obiekcie starego schematu) wspolne bazy wyjsciowe dla WSZYSTKICH
        warstw starego schematu (2011) razem - jeden plik na kazdy
        zaznaczony format (GeoPackage/SQLite/ESRI geobaza), z wieloma
        warstwami w srodku (dokladnie tak samo jak dla nowego schematu),
        umieszczone w osobnym podkatalogu OLD_SCHEMA_OUTPUT_SUBDIR - NIE
        osobne pliki per warstwa. Kolejne wywolania dla nastepnych warstw
        korzystaja juz z raz utworzonych, otwartych baz.
        """
        if self._old_out_ds is not None:
            return True  # juz utworzone w tym przebiegu

        old_dir = os.path.join(self.output_dir, OLD_SCHEMA_OUTPUT_SUBDIR)
        try:
            os.makedirs(old_dir, exist_ok=True)
        except OSError as exc:
            self.log_cb(f"Nie udalo sie utworzyc katalogu {OLD_SCHEMA_OUTPUT_SUBDIR}: {exc}", "error")
            return False
        self._old_schema_dir = old_dir

        if not self._old_schema_notice_written:
            notice_path = os.path.join(old_dir, OLD_SCHEMA_NOGML_NOTICE)
            try:
                with open(notice_path, "w", encoding="utf-8") as f:
                    f.write(
                        "Ten katalog zawiera dane BDOO w STARYM schemacie (wg rozporzadzenia z 2011 r.),\n"
                        "importowane bez mapowania na nowy model danych (wg rozporzadzenia z 2021 r.).\n\n"
                        "Dla tych danych NIE wygenerowano plikow GML. Eksport do GML w tej wtyczce jest\n"
                        "przeznaczony wylacznie pod wtyczke wizualizacyjna \"BDOO IMPORT GML\", ktora\n"
                        "zaklada nowy model danych (2021 r.) i nie obsluguje struktury pol starego\n"
                        "schematu (2011 r.) - dlatego eksport GML zostal dla tych warstw pominiety.\n"
                    )
                self._old_schema_notice_written = True
            except OSError as exc:
                self.log_cb(f"Nie udalo sie zapisac pliku informacyjnego w {old_dir}: {exc}", "warning")

        working_driver = ogr.GetDriverByName("SQLite")
        working_filename = self._build_output_filename(output_name, wojewodztwa, single_file_suffix, ".sqlite")
        working_path = os.path.join(self._tmp_root, "stary_schemat__" + working_filename)
        tmp_ds = working_driver.CreateDataSource(working_path, options=["SPATIALITE=YES"])
        if tmp_ds is None:
            self.log_cb("Nie udalo sie utworzyc bazy roboczej starego schematu.", "error")
            return False
        tmp_ds = None
        self._old_working_path = working_path
        self._old_out_ds = gdal.OpenEx(working_path, gdal.OF_VECTOR | gdal.OF_UPDATE)
        if self._old_out_ds is None:
            self.log_cb("Nie udalo sie otworzyc bazy roboczej starego schematu do zapisu.", "error")
            return False

        if export_gpkg:
            gpkg_driver = ogr.GetDriverByName("GPKG")
            gpkg_path = os.path.join(
                old_dir, self._build_output_filename(output_name, wojewodztwa, single_file_suffix, ".gpkg"))
            if os.path.exists(gpkg_path):
                gpkg_driver.DeleteDataSource(gpkg_path)
                self.log_cb(f"Plik GeoPackage (stary schemat) juz istnial - zostanie nadpisany: {os.path.basename(gpkg_path)}", "warning")
            tmp_gpkg_ds = gpkg_driver.CreateDataSource(gpkg_path)
            if tmp_gpkg_ds is None:
                self.log_cb(f"Nie udalo sie utworzyc pliku GeoPackage (stary schemat): {gpkg_path}", "error")
            else:
                tmp_gpkg_ds = None
                self._old_gpkg_ds = gdal.OpenEx(gpkg_path, gdal.OF_VECTOR | gdal.OF_UPDATE)
                if self._old_gpkg_ds is None:
                    self.log_cb(f"Nie udalo sie otworzyc pliku GeoPackage (stary schemat) do zapisu: {gpkg_path}", "error")

        if export_gdb:
            gdb_driver = ogr.GetDriverByName("OpenFileGDB")
            if gdb_driver is None or not gdb_driver.TestCapability(ogr.ODrCCreateDataSource):
                self.log_cb(
                    "Sterownik OpenFileGDB z obsluga zapisu niedostepny w tej instalacji GDAL (wymagany "
                    "GDAL >= 3.6) - eksport starego schematu do .gdb pominiety.", "warning")
            else:
                gdb_path = os.path.join(
                    old_dir, self._build_output_filename(output_name, wojewodztwa, single_file_suffix, ".gdb"))
                if os.path.exists(gdb_path):
                    gdb_driver.DeleteDataSource(gdb_path)
                    self.log_cb(f"Geobaza .gdb (stary schemat) juz istniala - zostanie nadpisana: {os.path.basename(gdb_path)}", "warning")
                tmp_gdb_ds = gdb_driver.CreateDataSource(gdb_path)
                if tmp_gdb_ds is None:
                    self.log_cb(f"Nie udalo sie utworzyc geobazy .gdb (stary schemat): {gdb_path}", "error")
                else:
                    tmp_gdb_ds = None
                    self._old_gdb_ds = gdal.OpenEx(gdb_path, gdal.OF_VECTOR | gdal.OF_UPDATE)
                    if self._old_gdb_ds is None:
                        self.log_cb(f"Nie udalo sie otworzyc geobazy .gdb (stary schemat) do zapisu: {gdb_path}", "error")

        return True

    def _merge_old_schema_layer(self, layer_name, old_intermediates, wojewodztwa,
                                 export_gpkg, export_gdb, export_sqlite,
                                 output_name, single_file_suffix, export_geoparquet=False):
        """
        Scala warstwe starego schematu (2011) do WSPOLNYCH baz wyjsciowych
        (jedna na caly przebieg, nie per warstwa) w podkatalogu
        OLD_SCHEMA_OUTPUT_SUBDIR - dokladnie tak samo jak dla nowego
        schematu, tylko w calkowicie osobnym zestawie plikow. Nazwa
        warstwy w srodku (np. "OT_PTWP_A") jest BEZ sufiksu wersji
        schematu - odroznienie od nowego schematu jest tylko dzieki temu,
        ze to inny plik/podkatalog.
        """
        if not (export_gpkg or export_gdb or export_sqlite or export_geoparquet):
            return 0
        if not self._ensure_old_schema_datasources(
                wojewodztwa, output_name, single_file_suffix, export_gpkg, export_gdb, export_sqlite):
            return 0

        n_written, local_id_field = self._merge_intermediates(
            layer_name, old_intermediates, self._old_out_ds, layer_name)
        self.log_cb(
            f"{layer_name}: zaimportowano {n_written} obiektow w starym schemacie (2011) do "
            f"'{OLD_SCHEMA_OUTPUT_SUBDIR}' - bez mapowania pol.", "info")

        relation_spec = RELATION_MAP.get(layer_name)
        if relation_spec and local_id_field:
            self._attach_related_data(layer_name, local_id_field, relation_spec, old_intermediates, self._old_out_ds)

        if self._old_gpkg_ds is not None:
            try:
                options = gdal.VectorTranslateOptions(layers=[layer_name], layerName=layer_name)
                gdal.VectorTranslate(self._old_gpkg_ds, self._old_out_ds, options=options)
                self.log_cb(f"{layer_name}: zapisano warstwe (stary schemat) do GeoPackage.", "info")
            except Exception as exc:
                self.log_cb(f"{layer_name}: eksport starego schematu do GeoPackage nie powiodl sie - {exc}", "error")

        if self._old_gdb_ds is not None:
            try:
                options = gdal.VectorTranslateOptions(layers=[layer_name], layerName=layer_name)
                gdal.VectorTranslate(self._old_gdb_ds, self._old_out_ds, options=options)
                self.log_cb(f"{layer_name}: zapisano warstwe (stary schemat) do geobazy .gdb.", "info")
            except Exception as exc:
                self.log_cb(f"{layer_name}: eksport starego schematu do .gdb nie powiodl sie - {exc}", "error")

        if export_geoparquet and self._old_schema_dir:
            self._export_geoparquet_layer(
                layer_name, self._old_out_ds, output_name,
                os.path.join(self._old_schema_dir, GEOPARQUET_OUTPUT_SUBDIR))

        return n_written

    @staticmethod
    def _widen_field_type(t1, t2):
        """Przy konflikcie typu tego samego pola miedzy wojewodztwami, wybierz
        bezpieczny nadzbior (nic nie ucina/nie traci precyzji)."""
        if t1 == t2:
            return t1
        if ogr.OFTString in (t1, t2):
            return ogr.OFTString
        pair = {t1, t2}
        if pair == {ogr.OFTInteger, ogr.OFTReal}:
            return ogr.OFTReal
        if pair == {ogr.OFTInteger, ogr.OFTInteger64}:
            return ogr.OFTInteger64
        if pair == {ogr.OFTInteger64, ogr.OFTReal}:
            return ogr.OFTReal
        return t1

    def _find_layer_in_ds(self, ds, layer_name):
        """
        Zwraca warstwe pasujaca do layer_name (dopasowanie po nazwie,
        wielkosc liter bez znaczenia). Jesli plik GML zawiera wiecej niz
        jedna wewnetrzna warstwe (np. sterownik GML tworzy osobne warstwy
        dla zagniezdzonych obiektow standalone typu BT_Identyfikator,
        BT_CyklZyciaInfo, BT_ReferencjaDoObiektu w starym schemacie), a
        zadna z nazwanych pasujaco warstw nie ma obiektow, PRZESZUKUJE
        WSZYSTKIE pozostale warstwy w pliku (nie tylko pierwsza) w
        poszukiwaniu jakiejkolwiek z danymi - wczesniejsza wersja sprawdzala
        w takim przypadku tylko warstwe o indeksie 0, co przy plikach z
        wieloma wewnetrznymi warstwami dawalo falszywy wynik "pusta", mimo
        ze dane faktycznie byly obecne w pliku (tylko pod innym indeksem).
        """
        fallback = None
        for i in range(ds.GetLayerCount()):
            lyr = ds.GetLayerByIndex(i)
            name_matches = (
                lyr.GetName().upper() == layer_name.upper()
                or lyr.GetName().upper().endswith(layer_name.upper())
            )
            n_features = lyr.GetFeatureCount()
            if name_matches and n_features > 0:
                return lyr
            if fallback is None and n_features > 0:
                fallback = lyr
        if fallback is not None:
            self.log_cb(
                f"{layer_name}: w pliku nie znaleziono warstwy o pasujacej nazwie z obiektami - uzyto "
                f"awaryjnie warstwy '{fallback.GetName()}' (pierwsza z danymi w pliku). Sprawdz wynik.",
                "warning")
        return fallback
