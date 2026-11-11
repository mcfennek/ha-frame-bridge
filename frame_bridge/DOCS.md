# The Frame Bridge

Bildergalerie, Upload und REST-API für den Samsung The Frame – vollständig über Home Assistant bedienbar.

## Wie es zusammenarbeitet

- **Home Assistant (Integration „Samsung Smart TV“)** bleibt für Ein/Aus, Quelle und Lautstärke zuständig. Das Add-on liest daraus Gerät, Entitäten und Zustand.
- **Art-Funktionen** (Art Mode, Upload, Bildliste, Auswahl, Vorschaubilder) bietet die Integration nicht. Dafür spricht das Add-on den Art-Kanal des Fernsehers direkt an, als eigener Client „FrameBridge“ mit eigenem Token. Kopplung und Betrieb der HA-Integration werden dadurch nicht verändert.

## Einrichtung

1. Konfiguration öffnen und eintragen:
   - `tv_host`: IP-Adresse des Fernsehers, z. B. `192.168.0.150`
   - `media_player_entity`: z. B. `media_player.wohnzimmer_the_frame_65`
2. Speichern und das Add-on starten.
3. In der Seitenleiste **The Frame** öffnen → **Einstellungen** → **Mit Fernseher koppeln**.
   Auf dem Fernseher erscheint eine Abfrage – mit der Fernbedienung erlauben (60 Sekunden Zeit).
4. **Verbindung testen** zeigt Art-Mode-Status und Bildliste.

Der Fernseher muss dabei eingeschaltet oder im Art Mode sein und im selben Netz wie Home Assistant liegen.

## Optionen

| Option | Bedeutung |
|---|---|
| `tv_host` | IP-Adresse des Fernsehers |
| `media_player_entity` | Media Player der Samsung-Integration (Statusanzeige) |
| `api_enabled` | externe REST-API auf Port 8080 starten (Standard: aus) |
| `max_upload_mb` | maximale Dateigröße beim Upload |
| `matte` | Standard-Passepartout für neue Uploads, z. B. `none`, `shadowbox_polar`; pro Bild in der Galerie änderbar |
| `log_level` | `debug`, `info`, `warning`, `error` |

## Daten und Sicherung

Alles liegt im Add-on-Datenverzeichnis und ist Teil der Home-Assistant-Sicherungen:
Bilder (3840×2160 JPEG), Vorschaubilder, Datenbank, Fernseher-Token und API-Token.

Löschen in der Galerie entfernt nur die lokale Kopie. **Bilder auf dem Fernseher werden vom Add-on nie gelöscht.**

## Externe REST-API

Standardmäßig aus. Zum Einschalten:

1. `api_enabled: true` setzen.
2. Unter **Netzwerk** einen Host-Port für `8080/tcp` eintragen, z. B. `8787`.
3. Neu starten. Das Token steht in der Weboberfläche unter **Einstellungen → Externe REST-API**.

Jeder Aufruf unter `/api` braucht `Authorization: Bearer <Token>`. Spezifikation: `http://<home-assistant>:8787/openapi.json`, interaktiv unter `/docs`.

Die Weboberfläche ist nur über Home Assistant Ingress erreichbar; die externe API bietet keine Einstellungen, keine Kopplung und keine Token-Verwaltung.

Anbindung an Claude oder ChatGPT: siehe `docs/ki-anbindung.md` im Repository.

## Fehlerbehebung

- **„Noch nicht gekoppelt“** – Einstellungen → Mit Fernseher koppeln.
- **„Art-Kanal antwortet nicht“** – Fernseher einmal kurz aus- und einschalten, danach erneut koppeln.
- **Mehr Details** – `log_level: debug` setzen und das Protokoll des Add-ons ansehen.
