# revenue-vault

Sistema locale di **revenue management** per una struttura ricettiva
(pensato per un piccolo hotel, ma il "motore" interno non conosce parole
come "camera" o "hotel": lavora con concetti astratti che potrebbero
valere per qualsiasi attivita' che vende unita' di capacita' per data).

Gira interamente sul tuo computer: nessun server, nessun account, nessun
dato che esce dal tuo PC. Serve solo Python 3 (nessuna libreria da
installare: usa solo cio' che c'e' gia' dentro Python).

## Cosa fa ogni file

| File | A cosa serve |
|---|---|
| `config.json` | Tutti i dati della tua struttura: nome, tipologie (con quante unita' per tipo), canali di vendita, fasce di prezzo per stagione, soglie delle regole che generano i suggerimenti. **E' l'unico file da modificare per personalizzare il sistema.** |
| `mapping.json` | Dice allo script di importazione come leggere un file CSV di prenotazioni reali: quale colonna del tuo CSV corrisponde a quale campo del sistema. |
| `init_db.py` | Crea il database `vault.db` e vi carica le tipologie da `config.json`. Si puo' rilanciare quante volte si vuole. |
| `genera_demo.py` | Riempie il database con dati **finti ma realistici** (14 mesi di storico + i prossimi ~60 giorni), utile per provare subito il sistema senza avere ancora dati veri. |
| `importa_csv.py` | Importa prenotazioni reali da un file CSV esterno, usando `mapping.json`. Alla fine mostra quante righe sono state importate e quali scartate (e perche'). |
| `snapshot_oggi.py` | Da lanciare ogni mattina: chiede in modo rapido (basta premere INVIO per confermare) la disponibilita' e il prezzo pubblicato per le prossime date, cosi' il sistema puo' calcolare il "pickup" (quanto si e' venduto). |
| `report_mattina.py` | **Il cuore del sistema.** Legge tutti i dati e genera `report/report_oggi.html`: la situazione dei prossimi 30 giorni, il confronto con l'anno scorso, le date da controllare e i suggerimenti su prezzi/canali. |
| `demo.py` | Esegue in un colpo solo `init_db.py` + `genera_demo.py` + `report_mattina.py`, per vedere tutto il sistema funzionante subito. |

## I 3 comandi essenziali

```bash
# 1. La prima volta (o per rivedere una demo completa con dati finti):
python3 demo.py

# 2. Ogni mattina, per aggiornare disponibilita' e prezzi:
python3 snapshot_oggi.py

# 3. Per generare il report aggiornato:
python3 report_mattina.py
```

Dopo il comando 3, apri il file `report/report_oggi.html` con un doppio
click (si apre nel browser): non serve nessun programma speciale.

## Come sostituire i dati demo con i dati veri della tua struttura

I dati generati da `genera_demo.py` servono solo a farti vedere subito il
sistema funzionante. Per usarlo con i tuoi dati veri:

### Passo 1 - Personalizza `config.json`

Apri `config.json` con un editor di testo qualsiasi e modifica:

- `structure_name`: il nome della tua struttura
- `capacity_units`: le tue tipologie, con il codice, il nome e il numero
  di unita' di ciascuna (es. quante camere di quel tipo hai)
- `channels`: i canali di vendita che usi davvero (il primo della lista
  e' considerato il canale diretto/principale)
- `price_ranges`: per ogni tipologia, il prezzo minimo e massimo in
  stagione bassa e in stagione alta
- `rules_thresholds`: le soglie che fanno scattare i suggerimenti
  (quando alzare i prezzi, quando proporre uno sconto, quando chiudere
  le OTA). Puoi lasciarle come sono all'inizio e aggiustarle in seguito
  in base a come si comporta il report.
- `stagioni`: quali mesi consideri alta/media/bassa stagione

### Passo 2 - Ricrea il database vuoto

```bash
rm vault.db          # cancella il database con i dati demo
python3 init_db.py   # ne crea uno nuovo, vuoto, con le tue tipologie
```

### Passo 3 - Importa le tue prenotazioni reali da CSV

Apri `mapping.json` e adatta i nomi delle colonne di sinistra ("colonne")
a come si chiamano davvero le colonne nel tuo file CSV (esporta un CSV
dal tuo gestionale/PMS). Controlla anche `formato_data` (es. `%d/%m/%Y`
per il formato giorno/mese/anno) e `valori_stato` (come viene scritto
"confermata" o "cancellata" nel tuo file).

Poi lancia:

```bash
python3 importa_csv.py il_tuo_file.csv
```

Lo script ti dira' quante righe sono state importate correttamente e
quante scartate, con il motivo (es. data non valida, tipologia
sconosciuta, prezzo mancante).

Nella cartella `esempi/` trovi `esempio_prenotazioni.csv`, un file di
prova per capire il formato atteso (compatibile con il `mapping.json` di
default).

### Passo 4 - Inserisci la disponibilita' di oggi

```bash
python3 snapshot_oggi.py
```

### Passo 5 - Genera il report

```bash
python3 report_mattina.py
```

Da qui in poi basta ripetere i passi 4 e 5 ogni giorno (idealmente ogni
mattina), per avere sempre un report aggiornato. Con il tempo, piu'
snapshot registri e piu' lo storico prenotazioni cresce, piu' il report
(pickup, confronto anno su anno, suggerimenti) diventa preciso.

## Domande frequenti

**Devo installare qualcosa?**
No. Serve solo Python 3 (versione 3.8 o superiore), gia' presente sulla
maggior parte dei computer Mac e Linux; su Windows si scarica gratis dal
Microsoft Store o da python.org. Nessun `pip install`.

**I miei dati vengono inviati da qualche parte?**
No. Tutto resta nel file `vault.db` sul tuo computer. Non c'e' nessuna
connessione a internet in nessuno script.

**Posso rilanciare `python3 demo.py` piu' volte?**
Si': ripulisce e rigenera solo i dati demo/di prova, non tocca la tua
configurazione.

**Cosa succede se sbaglio a scrivere un dato in `snapshot_oggi.py`?**
Lo script segnala l'errore e richiede di nuovo il valore; puoi anche
scrivere `fine` in qualsiasi momento per interrompere e salvare solo
quello che hai gia' inserito.

**Come faccio il backup dei dati?**
Basta copiare il file `vault.db`: contiene tutto lo storico.
