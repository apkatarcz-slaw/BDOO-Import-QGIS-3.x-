# Import BDOO (QGIS 3.x) - wtyczka QGIS

Wtyczka do importu i scalania wojewodzkich baz **BDOO** (Baza Danych
Ogolnogeograficznych, dane GML w skali 1:250 000, zgodne ze schematem
`BDOT10k_BDOO.xsd`) z jednego lub wielu wojewodztw do jednej lub kilku
formatow wynikowych (GeoPackage, GML, ESRI File Geodatabase, SQLite/
SpatiaLite - dowolna kombinacja).

BDOO to produkt GUGiK/PZGiK bedacy zgeneralizowana pochodna bazy
BDOT10k (stad wspolny schemat `BDOT10k_BDOO.xsd`), dystrybuowany w
paczkach `.zip` **per wojewodztwo** (nie per powiat).

**Ta wersja jest przeznaczona wylacznie dla QGIS 3.x** (PyQt5, importy
Qt przez `qgis.PyQt`). Dla QGIS 4.x istnieje osobna, niezalezna wtyczka
"Import BDOO (QGIS 4.x)" - obie sa calkowicie odrebnymi pluginami (bez
wspoldzielonego kodu), instaluje sie tylko jedna z nich, zalezna od
posiadanej wersji QGIS.

## Wymagania

- QGIS 3.34 - 3.99 (PyQt5).
- Standardowa instalacja QGIS / OSGeo4W - wtyczka korzysta wylacznie z
  bibliotek juz dostepnych w tym środowisku: `osgeo.ogr`, `osgeo.osr`,
  `osgeo.gdal` (GDAL/OGR) oraz `qgis.PyQt`. Nie wymaga dodatkowych
  pakietow pip.

## Wbudowane schematy XSD

Rzeczywiste paczki BDOO pobrane z geoportalu **nie zawieraja** katalogu
`XSD/`, mimo ze naglowek kazdego pliku GML odwoluje sie do schematu
poprzez `xsi:schemaLocation` (sciezka wzgledna `../XSD/BDOT10k_BDOO.xsd`).
Wtyczka ma oba wymagane pliki (`BDOT10k_BDOO.xsd`, `KARTO.xsd`) zaszyte
na stale w `resources/xsd/` i przy rozpakowywaniu kazdej paczki
automatycznie dogrywa je we wlasciwym miejscu wzgledem wykrytych plikow
`.gml`/`.xml` - uzytkownik nigdy nie musi samodzielnie dostarczac XSD.

## Formaty eksportu

Baza robocza uzywana do scalania wojewodztw, docinania szerokosci pol
tekstowych i deduplikacji jest **zawsze** SQLite/SpatiaLite, budowana w
katalogu tymczasowym (niewidoczna dla uzytkownika). Widoczne w
interfejsie sa 4 niezalezne checkboxy eksportu, kazdy budowany z juz
gotowej, scalonej i zdeduplikowanej bazy roboczej:

| Format | Domyslnie | Uwagi |
|---|---|---|
| **GeoPackage (.gpkg)** | zaznaczony | jeden plik ze wszystkimi warstwami |
| **GML/XML** | odznaczony | jeden plik na kazdy typ warstwy, zapisywany do podkatalogu `BDOO_Polska` wewnatrz katalogu wyjsciowego, z nazwa poprzedzona stalym prefiksem `PL.PZGiK.201.22__` |
| **ESRI File Geodatabase (.gdb)** | odznaczony | wymaga sterownika OpenFileGDB z obsluga zapisu (GDAL >= 3.6) - checkbox jest automatycznie wyszarzany, jesli niedostepny w danej instalacji |
| **SQLite/SpatiaLite (.sqlite)** | odznaczony | kopia bazy roboczej trafia do katalogu wyjsciowego zamiast zostac skasowana po imporcie |

Wymagany jest **co najmniej jeden** zaznaczony format - start importu
jest blokowany, jesli zadnego nie wybrano.

## Architektura importu (konwersja posrednia + cache)

Zamiast wielokrotnie parsowac te same pliki GML w petli Pythona, kazdy
plik zrodlowy jest **raz** przetwarzany na lekki format posredni
(GeoPackage) przez `gdal.VectorTranslate`. Filtr (pominiecie zamknietego
cyklu zycia) i reprojekcja do EPSG:2180 dzieja sie w tym samym kroku,
wykonywane w skompilowanym kodzie GDAL/C++, nie w interpretowanym
Pythonie.

Wynik konwersji jest **cache'owany na dysku**, w podkatalogu
`.bdoo_cache` obok katalogu wejsciowego, kluczowany nazwa wojewodztwa +
nazwa warstwy + data modyfikacji zrodlowego pliku `.zip`. Kolejne
uruchomienia importu na tych samych danych zrodlowych (np. zmiana
wyboru warstw/formatow eksportu) **nie parsuja GML ponownie** -
korzystaja z gotowego pliku posredniego.

**Sprzatanie cache:**
- Automatyczne, na starcie kazdego importu: pliki posrednie odnoszace
  sie do wojewodztwa, ktorego `.zip` zostal zmieniony (inna data
  modyfikacji) lub usuniety z katalogu wejsciowego, sa kasowane.
- Reczne: przycisk "Wyczysc cache teraz" w interfejsie usuwa caly
  cache dla biezacego katalogu wejsciowego.

Dalsze etapy pipeline'u dzialaja juz na przekonwertowanych, znacznie
szybszych do odczytu plikach GeoPackage, nie na oryginalnym GML:

1. **Szerokosci pol tekstowych** - zapytanie SQL `MAX(LENGTH(...))`
   wykonywane przez silnik bazy, nie petla w Pythonie.
2. **Scalanie wojewodztw** do wspolnej warstwy w bazie roboczej -
   `gdal.VectorTranslate` w trybie `append`, opakowane w jedna
   transakcje na warstwe.
3. **Deduplikacja** po polu `lokalnyId` - pojedyncze zapytanie
   `DELETE ... WHERE rowid NOT IN (SELECT MIN(rowid) ... GROUP BY ...)`
   wykonywane przez SQLite.
4. **Eksporty rownolegle** (GeoPackage / GML / .gdb / SQLite) - kazdy
   czyta **z juz gotowej bazy roboczej** (scalonej, przefiltrowanej,
   zdeduplikowanej), a nie z surowych plikow XML ani z siebie nawzajem.

GeoParquet zostal z wtyczki usuniety (swiadoma decyzja) - dedup przez
SQL `DELETE` nie ma prostego odpowiednika dla tego formatu.

## Automatyczne rozpoznawanie starego schematu (sprzed 2024)

Do konca 2023 r. dane BDOT10k/BDOO byly prowadzone wg starszego
rozporzadzenia z 2011 r. (namespace GML z sufiksem `:1.0`), od 2024 r.
obowiazuje nowy, uproszczony model wg rozporzadzenia z 2021 r. (sufiks
`:2.0`). Wtyczka rozpoznaje wersje schematu automatycznie, wprost z
namespace w naglowku kazdego pliku GML - nie trzeba nic wybierac
recznie.

Dane w starym schemacie **nie sa mapowane** na pola nowego schematu -
sa importowane pod wlasnymi, natywnymi nazwami pol, do **calkowicie
osobnego podkatalogu** `bdoo_wojewodztwa_stary_schemat` wewnatrz
katalogu wyjsciowego (nie trafiaja do glownej, scalonej bazy z nowym
schematem - zestawy pol sie nie pokrywaja). W tym podkatalogu powstaje
**jedna wspolna baza na kazdy zaznaczony format** (GeoPackage/SQLite/
ESRI geobaza) - dokladnie tak samo jak dla nowego schematu, tylko w
osobnym zestawie plikow - z wieloma warstwami w srodku, kazda nazwana
samym kodem warstwy (np. `OT_PTWP_A`), bez zadnego sufiksu wersji
schematu - informacja o tym, ze to dane archiwalne, jest tylko w
nazwie podkatalogu/pliku (np. `BDOO_wojewodztwa.gpkg` wewnatrz
`bdoo_wojewodztwa_stary_schemat/`). Dla starego schematu **nie
generuje sie plikow GML** (eksport GML jest przeznaczony wylacznie pod
wtyczke wizualizacyjna "BDOO IMPORT GML", ktora zaklada nowy model
danych) - zamiast tego w podkatalogu zapisywany jest jednorazowo plik
tekstowy `UWAGA_brak_plikow_GML.txt` z wyjasnieniem. Komunikat koncowy
o wtyczce wizualizacyjnej pojawia sie tylko wtedy, gdy w danym
przebiegu faktycznie przetworzono choc jedna warstwe w nowym schemacie.

Jesli w danym przebiegu nie przetworzono **zadnych** danych w nowym
schemacie (np. katalog wejsciowy zawieral wylacznie dane archiwalne),
glowna baza wynikowa (poza podkatalogiem starego schematu) w ogole nie
zostaje zapisana - zamiast zostawiac mylacy, technicznie pusty plik,
ktory niektore programy (w tym QGIS) potrafia zglosic jako
nierozpoznane/uszkodzone zrodlo danych, wtyczka informuje o tym wprost
w logu.

Identyfikator obiektu (`lokalnyId`) i pole cyklu zycia
(`koniecWersjiObiektu`) - w starym schemacie zagniezdzone w
podelementach `idIIP`/`x_cyklZycia` - sa wyszukiwane dynamicznie po
sufiksie nazwy kolumny (nie po sztywnej, dokladnej nazwie), wiec
deduplikacja i filtr cyklu zycia dzialaja poprawnie dla obu wersji
schematu bez dodatkowej konfiguracji.

Wymagane schematy starego modelu (`OT_BDOT10k_BDOO.xsd`,
`BT_ModelPodstawowy.xsd`, `MZ_MapaZasadnicza.xsd`,
`OT_BDOT10k_Slowniki.xsd`) sa rowniez wbudowane w zasoby wtyczki -
uzytkownik nie musi ich dostarczac.

## Zalozenia dot. danych wejsciowych

- Katalog wejsciowy zawiera bezposrednio pliki `.zip`, po jednym na
  wojewodztwo (np. `PL.PZGiK.201.02.zip`), rozpakowywane automatycznie
  "w locie" do katalogu tymczasowego.
- Kazdy plik wewnatrz archiwum ma nazwe
  `<PREFIKS_PZGiK>__<NAZWA_WARSTWY>.gml` (obslugiwane jest tez
  rozszerzenie `.xml`). Prefiks pliku musi byc identyczny z nazwa
  archiwum `.zip` - w przeciwnym razie plik jest pomijany z wpisem w logu.
- Uklad wspolrzednych danych wejsciowych: **EPSG:2180 (PL-1992)**.
- Katalog `XSD/` **nie musi** byc obecny w paczce - wtyczka podklada
  wlasne, wbudowane kopie schematow. Pliki XSD **nie sa importowane
  jako warstwy**.

## Obsluga bledow

- Brak pliku wejsciowego dla danej warstwy w danym wojewodztwie:
  warstwa z tego wojewodztwa jest pomijana, przetwarzanie kontynuowane.
- Niezgodna nazwa prefiksu pliku wzgledem archiwum: plik pomijany.
- Niezgodny/nierozpoznany CRS: plik pomijany bez reprojekcji.
- Pusty katalog wejsciowy / brak plikow `.zip`: komunikat i przerwanie.
- Uszkodzone archiwum `.zip`: pomijane, wpis w logu.
- Blad konwersji posredniej: warstwa/wojewodztwo pomijane, reszta
  przetwarzana dalej.

## Log i komunikat koncowy

Panel logu w oknie wtyczki pokazuje wszystkie komunikaty na biezaco
(kolorowane wg poziomu). Przycisk "Eksportuj log..." zapisuje caly
biezacy log do pliku tekstowego. Po zakonczeniu kazdego importu
wyswietlane jest okno z komunikatem "W celu wizualizacji
kartograficznej, uzyj wtyczki BDOO IMPORT GML" (znika po kliknieciu OK).

## Historia zmian

- **2.0.0** (2026-08-10) - wydzielenie osobnej wtyczki dla QGIS 3.x;
  format wyjsciowy zmieniony z rozwijanej listy na 4 niezalezne
  checkboxy eksportu (GeoPackage/GML/ESRI geobaza/SQLite), z baza
  robocza zawsze w SQLite budowanym wewnetrznie.
- **1.4.0** (2026-08-10) - dostosowanie do rzeczywistych paczek BDOO
  (wojewodztwa zamiast powiatow, rozszerzenie `.gml`/`.xml`, pole
  `lokalnyId`), wbudowane schematy XSD, domyslny sufiks `_BDOO`,
  przycisk "Eksportuj log", eksport GML do `BDOO_Polska` z prefiksem
  `PL.PZGiK.201.22__`, komunikat koncowy.
- **1.3.0** (2026-08-07) - eksport do ESRI File Geodatabase (.gdb).
- **1.2.0** (2026-08-07) - nowa architektura: konwersja posrednia +
  cache, scalanie i dedup przez SQL/VectorTranslate.
- **1.0.0** (2026-08-07) - pierwsze wydanie.
