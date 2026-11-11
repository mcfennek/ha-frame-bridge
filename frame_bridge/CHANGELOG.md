# Changelog

## 0.4.0

- ChatGPT-Anbindung: eigener GPT mit Aktion. Neues Schema unter `/gpt/openapi.json` (ohne Token abrufbar, nur die für den Chat nötigen Funktionen, kein Löschen).
- `POST /api/images/import` übernimmt Bilder per Download-Link, wie ChatGPT sie als `openaiFileIdRefs` sendet – auch selbst erzeugte Bilder. Geladen wird nur von öffentlichen https-Adressen, nie aus dem Heimnetz.
- Neue Option `public_url` für die öffentliche Adresse der API.
- Anleitung: `docs/chatgpt.md`.

## 0.3.0

- Passepartout auch für Bilder in voller 4K-Auflösung: Der Frame lehnt dort Passepartouts ab (Fehler −7). Das Add-on lädt dann automatisch eine Version in 1920×1080 mit Passepartout hoch; bei „Ohne“ kommt wieder die 4K-Version. Das Original bleibt in voller Auflösung gespeichert.
- Die vorherige Version bleibt auf dem Fernseher, bis du ihr Löschen ausdrücklich bestätigst (nur in der Weboberfläche, nie über die externe API, nur eigene Bilder).
- Verständliche Meldung bei 4K-Bildern, die nur auf dem Fernseher liegen.

## 0.2.2

- Bilder auf dem Fernseher werden nicht mehr doppelt angezeigt (der Fernseher führt jedes eigene Bild in zwei Kategorien).
- Programmlogik und Gestaltung sind direkt in die Seite eingebettet – nach einem Update kann der Browser keine veraltete Version mehr verwenden.
- Versionsnummer unten auf der Seite.

## 0.2.1

- Nach einem Update lädt die Oberfläche immer die neuen Programmdateien (vorher konnte der Browser die alte Version weiterverwenden – der Passepartout-Knopf reagierte dann nicht).
- Eigene Bilder auf dem Fernseher werden an ihrer Kennung (MY_…) erkannt statt an der Kategorie, die der 2025er Frame nicht immer gleich meldet.

## 0.2.0

- Passepartout pro Bild wählbar (Stil und Farbe aus der Liste des Fernsehers, oder „Ohne“) – für gespeicherte Bilder, für Bilder, die nur auf dem Fernseher liegen, und schon beim Upload.
- Die Änderung wird am Fernseher nachgeprüft; übernimmt er ein Passepartout nicht, kommt eine klare Meldung.
- Die Galerie zeigt das echte Passepartout: randlos ohne, farbig mit.

## 0.1.2

- „Zufälliges Bild anzeigen“ wählt jetzt aus allen eigenen Bildern auf dem Fernseher und in der Galerie, nie das gerade angezeigte. Gibt es keine Alternative, kommt eine klare Meldung.

## 0.1.1

- iPhone- und Kamerafotos (JPEG mit Zusatzbild, „MPO“) werden jetzt angenommen; verwendet wird das Hauptbild.

## 0.1.0

- Erste Version: Weboberfläche über Ingress (Status, Upload, Galerie lokal und auf dem Fernseher, Einstellungen mit Kopplung und Diagnose)
- Bildvorbereitung auf 3840×2160 mit „Einpassen“ oder „Zuschneiden“
- REST-API mit Bearer-Token, standardmäßig ausgeschaltet
- Samsung-Art-Kanal über `samsungframetv` 4.1.4 mit eigenem Client „FrameBridge“
