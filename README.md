# SkiAlp Abruzzo — feed meteorologico pubblico

Il feed `latest.json` viene generato dal NAS tramite `bridge/export_feed.py` e pubblicato con `bridge/publish_github.py`. **Nessuna porta del NAS o di Home Assistant deve essere esposta a Internet.**

## Pagina pubblica
Dopo aver abilitato **Settings > Pages > Deploy from a branch > main > /(root)**:
https://francescofilipponi85.github.io/skialp-abruzzo-feed/

L'APK usa:
https://francescofilipponi85.github.io/skialp-abruzzo-feed/latest.json

Il JSON non è ancora disponibile finché il NAS non effettua la prima pubblicazione. I dati sono pubblici e non devono contenere credenziali o informazioni private.

## Installazione NAS (container Home Assistant)
Scaricare i due file in `/config/skialp_abruzzo/collector/`, poi provare:
```sh
python3 /config/skialp_abruzzo/collector/export_feed.py
```

Creare un **fine-grained personal access token** su GitHub limitato a questo repository con solo **Contents: Read and write**, salvarlo nel NAS come `/config/skialp_abruzzo/secrets/github_feed_token`, con permessi 600. **Non incollarlo in chat e non aggiungerlo a GitHub.**

Pubblicare manualmente, soltanto dopo aver controllato il JSON:
```sh
python3 /config/skialp_abruzzo/collector/publish_github.py
```

Dopo il primo invio riuscito, programmare la pubblicazione poco dopo i dati raster (:55 ogni ora), con esclusione di sovrapposizioni e gestione degli errori.

## Sicurezza e qualità dati
Feed in sola lettura, senza ID WhatsApp, database, IP o segreti. Il raster termico è riferito a circa un'ora prima della pubblicazione; i dati possono diventare obsoleti. Nessuna valutazione sostituisce bollettini valanghe ufficiali e osservazione sul terreno.
