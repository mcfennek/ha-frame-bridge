# The Frame Bridge an Claude oder ChatGPT anbinden

Ziel: „Erstelle ein Herbstbild mit dem Schriftzug Happy Birthday Astrid und zeige es auf meinem Samsung The Frame.“

Dafür braucht der KI-Dienst zwei Dinge: eine erzeugte **Bilddatei** und einen Weg, sie per **echtem Datei-Upload** an `POST /api/images` zu schicken. Beides ist unten beschrieben.

## 1. REST-API einschalten

1. Add-on-Konfiguration: `api_enabled: true`.
2. Netzwerk: Host-Port für `8080/tcp` eintragen, z. B. `8787`. Neu starten.
3. Token aus der Weboberfläche (Einstellungen → Externe REST-API) kopieren.

Test im Heimnetz:

```bash
TOKEN=...   # aus der Weboberfläche
BASE=http://homeassistant.local:8787

curl -s $BASE/health
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/frame/status

# Bild hochladen und sofort anzeigen
curl -s -H "Authorization: Bearer $TOKEN" \
  -F "file=@herbst.png" -F "title=Happy Birthday Astrid" \
  -F "mode=crop" -F "display=true" \
  $BASE/api/images
```

## 2. Wichtige Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/health` | Lebenszeichen (ohne Token) |
| GET | `/api/frame/status` | Verbindung, Art Mode, aktuelles Bild |
| GET | `/api/images` | lokale Galerie |
| POST | `/api/images` | Upload (`multipart/form-data`: `file`, `title`, `mode`=`fit`/`crop`, `display`=`true`/`false`) |
| GET | `/api/images/{id}` | Metadaten |
| DELETE | `/api/images/{id}` | nur lokale Kopie löschen |
| POST | `/api/images/{id}/display` | auf dem Fernseher anzeigen |
| POST | `/api/frame/random` | zufälliges Bild anzeigen |
| GET | `/api/frame/remote-images` | eigene Bilder auf dem Fernseher (`supported: false`, wenn nicht abrufbar) |

Vollständig: [openapi.json](openapi.json) (OpenAPI 3.1).

## 3. Sicherer Zugriff von außen (HTTPS)

ChatGPT-Aktionen und Cloud-Dienste brauchen eine öffentlich erreichbare HTTPS-Adresse. **Den API-Port nie ungeschützt per Portfreigabe im Router öffnen.**

Empfohlene Wege:

- **Cloudflare Tunnel** (z. B. als Home-Assistant-Add-on): eine eigene Subdomain wie `frame.deine-domain.de` auf `http://<HA-IP>:8787` zeigen lassen. Kein offener Port im Router, TLS übernimmt Cloudflare. Zusätzlich lässt sich Cloudflare Access vorschalten.
- **Reverse Proxy** (z. B. NGINX Proxy Manager) mit Let's-Encrypt-Zertifikat auf denselben Port.

Hinweis: Der Nabu-Casa-Fernzugriff leitet nur Home Assistant selbst weiter, nicht die Ports von Add-ons.

Das Bearer-Token schützt jeden Aufruf. Bei Verdacht auf Missbrauch in der Weboberfläche **Neues Token erzeugen** – das alte ist sofort ungültig.

## 4. Claude

### Claude Desktop / Claude Code (lokal, MCP)

Ein kleiner MCP-Server übersetzt Werkzeugaufrufe in API-Aufrufe. Er läuft auf deinem Rechner und braucht nur Zugriff auf die API im Heimnetz – kein HTTPS nach außen nötig.

Datei [`mcp_server.py`](mcp_server.py), Einrichtung in Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "frame-bridge": {
      "command": "uvx",
      "args": ["--with", "mcp", "--with", "httpx", "python", "/Pfad/zu/mcp_server.py"],
      "env": {
        "FRAME_BRIDGE_URL": "http://homeassistant.local:8787",
        "FRAME_BRIDGE_TOKEN": "<Token>"
      }
    }
  }
}
```

Werkzeuge: `frame_status`, `list_images`, `upload_image` (Pfad zu einer lokalen Datei), `show_image`, `show_random_image`.

Claude erzeugt keine Fotos wie ein Bildgenerator, kann aber Grafiken per Code erstellen (z. B. ein Herbstmotiv mit Schriftzug als PNG) und die Datei dann mit `upload_image` übergeben. Für fotorealistische Motive ein Bild aus einem Bildgenerator speichern und den Dateipfad übergeben.

### claude.ai (Cloud)

Ein Remote-MCP-Connector braucht eine öffentliche HTTPS-Adresse (Abschnitt 3) und einen MCP-Server, der dort läuft. Das ist ein möglicher nächster Ausbauschritt; die REST-API ist dafür vorbereitet.

## 5. ChatGPT

Über **eigene GPTs mit Aktionen**:

1. Öffentliche HTTPS-Adresse einrichten (Abschnitt 3).
2. GPT bearbeiten → Aktionen → Schema importieren: `https://frame.deine-domain.de/openapi.json` (oder den Inhalt von [openapi.json](openapi.json) mit angepasster `servers`-URL).
3. Authentifizierung: **API-Schlüssel**, Typ **Bearer**, Token eintragen.

Für den Datei-Upload müssen GPT-Aktionen das erzeugte Bild als Datei übergeben können. Ob und wie das mit von ChatGPT erzeugten Bildern klappt, hängt vom aktuellen Funktionsumfang der Aktionen ab und sollte vor dem Einsatz getestet werden. Funktioniert es nicht direkt, kann das Bild heruntergeladen und über die Weboberfläche hochgeladen werden.

## 6. Übergabe generierter Bilddateien

- Format: JPG oder PNG, bis `max_upload_mb` (Standard 25 MB).
- Ideal: 3840×2160 oder größer im Seitenverhältnis 16:9. Andere Formate werden vorbereitet:
  - `mode=fit` – ganzes Bild sichtbar, Rand unscharf aufgefüllt
  - `mode=crop` – füllt den Bildschirm, Ränder werden abgeschnitten
- Schrift im Bild nicht zu nah an den Rand setzen, damit sie bei `crop` erhalten bleibt.
- `display=true` zeigt das Bild sofort im Art Mode. Klappt das nicht (Fernseher aus), bleibt das Bild gespeichert und die Antwort enthält `display_error`.
