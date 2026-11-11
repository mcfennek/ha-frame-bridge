# The Frame Bridge mit ChatGPT verbinden (eigener GPT mit Aktion)

Danach genügt in ChatGPT ein Satz wie: *„Erstelle ein Herbstbild mit dem Schriftzug Happy Birthday Astrid und zeige es auf meinem Frame.“*

## 1. Öffentliche Adresse über den Cloudflare-Tunnel

Cloudflare-Dashboard → **Zero Trust → Networks → Tunnels** → deinen Home-Assistant-Tunnel → **Public Hostname → Add a public hostname**:

| Feld | Wert |
|---|---|
| Subdomain | `frame` |
| Domain | deine Domain, z. B. `fenner.berlin` |
| Service Type | `HTTP` |
| URL | `fe001382-frame-bridge:8080` |

Der Tunnel erreicht das Add-on intern; im Router und in Home Assistant wird kein Port geöffnet.

Test im Browser: `https://frame.fenner.berlin/health` → `{"status":"ok",…}`. Alles unter `/api` antwortet ohne Token mit 401.

## 2. Add-on einstellen

Konfiguration von The Frame Bridge:

- `api_enabled`: an
- `public_url`: `https://frame.fenner.berlin`

Neu starten. In der Weboberfläche unter **Einstellungen → Externe REST-API → Token anzeigen** das Token kopieren.

## 3. GPT anlegen

ChatGPT → **GPTs → Erstellen → Konfigurieren**:

- **Name:** The Frame
- **Beschreibung:** Zeigt Bilder auf meinem Samsung The Frame.
- **Fähigkeiten:** *Bildgenerierung* einschalten.
- **Anweisungen:** den Text aus Abschnitt 4 einfügen.

**Aktionen → Neue Aktion erstellen:**

1. **Authentifizierung:** API-Schlüssel → Typ **Bearer** → Token einfügen.
2. **Schema → Von URL importieren:** `https://frame.fenner.berlin/gpt/openapi.json`
3. **Datenschutzrichtlinie:** eine erreichbare Seite angeben, falls ChatGPT eine verlangt (für einen privaten GPT reicht z. B. die Repository-Adresse).

Den GPT nur für dich speichern („Nur ich“).

Beim ersten Aufruf fragt ChatGPT, ob es `frame.fenner.berlin` aufrufen darf – mit **Immer erlauben** entfällt die Frage künftig.

## 4. Anweisungen für den GPT

```
Du steuerst den Samsung The Frame im Wohnzimmer über die Aktion "The Frame Bridge".

- Soll ein Bild erstellt und gezeigt werden: Erzeuge es im Querformat (möglichst breit, 16:9)
  und übergib es danach mit importImages (display=true, passender kurzer title).
  Schrift nicht zu nah an den Rand setzen.
- Hat der Nutzer ein Bild hochgeladen und will es zeigen: importImages mit diesem Bild.
- "Zeig ein anderes / zufälliges Bild": showRandomImage.
- Gespeicherte Bilder nennen oder eines davon zeigen: listImages, dann showImage.
- Passepartout ("mit Rahmen" / "ohne Rahmen"): passepartout=true bzw. false bei importImages,
  für ein schon gespeichertes Bild setImageMatte mit passepartout=true/false.
  Sagt der Nutzer nichts dazu, passepartout weglassen.
  Nur wenn er einen bestimmten Stil oder eine Farbe nennt: listMattes, dann matte angeben.
- "Was läuft gerade?": getFrameStatus.
- Antworte kurz auf Deutsch und sag, was auf dem Fernseher passiert ist.
  Meldet die Aktion einen Fehler, gib die Meldung verständlich weiter.
```

## Passepartout

„Mit Rahmen“ verwendet das Passepartout aus der Add-on-Option `ai_matte` – Standard **Modern schmal, Warm** (`modernthin_warm`).

## Schema aktualisieren

Nach einem Add-on-Update mit geänderten Aktionen im GPT unter **Aktionen** das Schema erneut **von URL importieren** und den GPT speichern.

## Sicherheit

- Jeder Aufruf unter `/api` braucht das Token; ohne Token nur `/health` und das Schema.
- Bei Verdacht auf Missbrauch: Einstellungen → **Neues Token erzeugen** und im GPT eintragen.
- ChatGPT kann keine Bilder auf dem Fernseher löschen – das geht nur in der Weboberfläche nach Bestätigung.
- Bilder werden nur von öffentlichen https-Adressen geladen, nie aus dem Heimnetz.
