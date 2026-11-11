# The Frame Bridge – Home Assistant Add-on

Eigene Bilder auf den Samsung The Frame bringen: hochladen, für 16:9 vorbereiten, in einer Galerie verwalten und im Art Mode anzeigen. Mit REST-API für spätere Anbindung an Claude oder ChatGPT.

## Installation

1. Home Assistant → **Einstellungen → Apps** (bzw. Add-ons) → **App Store**.
2. Menü oben rechts → **Repositories** → `https://github.com/mcfennek/ha-frame-bridge` hinzufügen.
3. **The Frame Bridge** auswählen → **Installieren**.
4. Unter **Konfiguration** `tv_host` und `media_player_entity` eintragen → Speichern.
5. **Starten**, optional „Beim Systemstart starten“ und „Watchdog“ aktivieren.
6. **The Frame** in der Seitenleiste öffnen → Einstellungen → **Mit Fernseher koppeln**.

Details: [frame_bridge/DOCS.md](frame_bridge/DOCS.md) · KI-Anbindung: [docs/ki-anbindung.md](docs/ki-anbindung.md) · API: [docs/openapi.json](docs/openapi.json)

## Aufbau

```
frame_bridge/
  config.yaml        Add-on-Definition (Ingress, Optionen, Watchdog)
  Dockerfile
  app/
    main.py          startet Ingress-Server (8099) und optional externe API (8080)
    web.py           FastAPI-Routen, Ingress-Schutz, Bearer-Token
    service.py       Upload, Anzeige, Zufallsbild, Status
    frame.py         Samsung-Art-Kanal (samsungframetv), eigener Client „FrameBridge“
    ha.py            Home Assistant Core API über den Supervisor
    imaging.py       Vorbereitung auf 3840×2160 (Einpassen/Zuschneiden)
    storage.py       SQLite + Dateien in /data
    static/          Weboberfläche
  tests/             pytest mit simuliertem Fernseher
```

## Entwicklung

```bash
cd frame_bridge
pip install -r requirements.txt pytest httpx
python -m pytest tests
```

## Lizenz

MIT. Nutzt [samsungframetv](https://github.com/NickWaterton/samsung-tv-ws-api) (LGPL-3.0) als unveränderte Abhängigkeit.
