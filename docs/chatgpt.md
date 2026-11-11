# The Frame Bridge mit ChatGPT oder Claude verbinden (MCP)

Das Add-on bringt einen MCP-Server mit Anmeldung (OAuth) mit:

```
https://frame.fenner.berlin/mcp
```

Danach genügt im Chat ein Satz wie: *„Zeig ein zufälliges Bild auf dem Frame“* oder *„Zeig dieses Foto mit Rahmen auf dem Frame“*.

## 1. Voraussetzungen (einmalig)

1. **Öffentliche Adresse über den Cloudflare-Tunnel:** Cloudflare-Dashboard → **Networking → Tunnels** → Tunnel → **Add route → Published application** → Hostname `frame.fenner.berlin`, Service URL `http://fe001382-frame-bridge:8080`.
   Test: `https://frame.fenner.berlin/health` → `{"status":"ok",…}`.
2. **Add-on-Konfiguration:** `api_enabled` an, `public_url` = `https://frame.fenner.berlin`.
3. **API-Token** bereithalten: Weboberfläche → Einstellungen → Externe REST-API → Token anzeigen. Du brauchst es einmal auf der Anmeldeseite.

## 2. ChatGPT (Plugin mit eigenem MCP-Server)

OpenAI ersetzt eigene GPTs durch Plugins; externe Dienste werden dort als MCP-Server eingebunden. Die genauen Menübezeichnungen ändern sich gerade – sinngemäß:

1. In ChatGPT (Web) die Plugins öffnen (`chatgpt.com/plugins`). Bei Plus/Pro ggf. zuerst in den Einstellungen den **Entwicklermodus** einschalten.
2. **Hinzufügen → eigenen MCP-Server erstellen.**
3. URL: `https://frame.fenner.berlin/mcp`, Anmeldung: **OAuth**. Client-ID und -Secret leer lassen – ChatGPT registriert sich selbst.
4. Es öffnet sich die Seite **„Zugriff auf The Frame erlauben?“** von deinem Add-on. API-Token eingeben → **Zugriff erlauben**.
5. Im Chat das Plugin auswählen bzw. erwähnen und loslegen.

**Hinweis zu ChatGPT Pro:** Je nach aktuellem Stand erlaubt Pro bei eigenen MCP-Servern nur lesende Werkzeuge. Dann funktionieren „Was läuft gerade?“ und „Welche Bilder habe ich?“, aber nicht „Zeig …“. Werkzeuge, die etwas ändern, sind korrekt als solche gekennzeichnet; ChatGPT fragt vor dem Ausführen nach.

## 3. Claude

Einstellungen → **Connectors** → **Benutzerdefinierten Connector hinzufügen**:

- Name: The Frame
- URL: `https://frame.fenner.berlin/mcp`
- OAuth-Felder leer lassen

Dann **Verbinden** → Anmeldeseite des Add-ons → Token eingeben → **Zugriff erlauben**.

Claude Code / Desktop-Konfiguration mit festem Token geht auch:

```bash
claude mcp add --transport http the-frame https://frame.fenner.berlin/mcp \
  --header "Authorization: Bearer <API-Token>"
```

## 4. Werkzeuge

| Werkzeug | Zweck | ändert etwas |
|---|---|---|
| `get_frame_status` | erreichbar, Art Mode, aktuelles Bild | nein |
| `list_images` | Bilder der Galerie | nein |
| `list_tv_images` | eigene Bilder auf dem Fernseher | nein |
| `list_mattes` | verfügbare Passepartouts | nein |
| `import_image` | Bild aus dem Chat (Datei oder https-Adresse) speichern und anzeigen; `passepartout` true/false | ja |
| `show_image` | gespeichertes Bild anzeigen | ja |
| `show_random_image` | zufälliges Bild, nie das aktuelle | ja |
| `set_image_matte` | mit/ohne Rahmen bzw. bestimmter Stil | ja |
| `set_art_mode` | Art Mode an/aus | ja |

Löschen auf dem Fernseher gibt es bewusst nicht – nur in der Weboberfläche nach Bestätigung.

„Mit Rahmen“ verwendet die Option `ai_matte` (Standard **Modern schmal, Warm**, `modernthin_warm`).

## 5. Bilder aus dem Chat

`import_image` nimmt Dateien so an, wie ChatGPT sie über `openai/fileParams` übergibt (Download-Link), oder eine öffentliche https-Adresse (`image_url`). Ob ChatGPT auch **selbst erzeugte** Bilder an ein MCP-Werkzeug übergibt, ist bei OpenAI noch nicht verbindlich dokumentiert – hochgeladene Fotos funktionieren laut Dokumentation. Klappt es mit einem erzeugten Bild nicht, das Bild speichern und in der Weboberfläche hochladen.

Geladen wird nur von öffentlichen https-Adressen, nie aus dem Heimnetz.

## 6. Sicherheit

- Ohne Anmeldung antwortet `/mcp` mit 401. Freigeben kann nur, wer das API-Token kennt; nach 5 Fehlversuchen ist die Anmeldeseite 10 Minuten gesperrt.
- Zugriffstoken gelten 1 Stunde, Refresh-Token 90 Tage und werden bei jeder Erneuerung ausgetauscht. Gespeichert werden nur Prüfsummen.
- Einstellungen → **Alle Assistenten abmelden** beendet jeden Zugriff. **Neues Token erzeugen** tut das ebenfalls.

## Älter: GPT-Aktion

Für eigene GPTs gibt es weiterhin das Aktionsschema unter `https://frame.fenner.berlin/gpt/openapi.json` (Bearer-Token). OpenAI stellt eigene GPTs jedoch ein; neue Einrichtungen bitte über MCP (Abschnitt 2).
