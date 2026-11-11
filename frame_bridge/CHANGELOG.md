# Changelog

## 0.1.2

- „Zufälliges Bild anzeigen“ wählt jetzt aus allen eigenen Bildern auf dem Fernseher und in der Galerie, nie das gerade angezeigte. Gibt es keine Alternative, kommt eine klare Meldung.

## 0.1.1

- iPhone- und Kamerafotos (JPEG mit Zusatzbild, „MPO“) werden jetzt angenommen; verwendet wird das Hauptbild.

## 0.1.0

- Erste Version: Weboberfläche über Ingress (Status, Upload, Galerie lokal und auf dem Fernseher, Einstellungen mit Kopplung und Diagnose)
- Bildvorbereitung auf 3840×2160 mit „Einpassen“ oder „Zuschneiden“
- REST-API mit Bearer-Token, standardmäßig ausgeschaltet
- Samsung-Art-Kanal über `samsungframetv` 4.1.4 mit eigenem Client „FrameBridge“
