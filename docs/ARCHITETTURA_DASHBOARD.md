# Architettura della dashboard

Data: 2026-07-12.

Questo documento descrive la "fondazione dashboard" aggiunta al progetto:
come e' organizzata a strati, cosa contiene esattamente il contratto JSON
che li separa, e come e' pensata per evolvere senza dover riscrivere il
motore ogni volta che cambia l'interfaccia (o viceversa).

## I quattro strati

```
  dati (SQLite)  -->  motore (regole)  -->  contratto JSON  -->  interfaccia (HTML statico)
   vault.db          report_mattina.py      dashboard/dati.json    dashboard/dashboard.html
```

**1. Dati — `vault.db` (SQLite)**
Lo strato piu' basso: i fatti grezzi (prenotazioni, rilevazioni di
disponibilita', variazioni di prezzo, segnali esterni, decisioni passate).
Nessuna interpretazione, solo fatti. Schema descritto in `core/db.py`,
invariato da questo lavoro.

**2. Motore — `report_mattina.py` (+ `core/`)**
Legge i dati grezzi e applica le regole: calcola disponibilita' e pickup,
confronta con l'anno precedente allo stesso anticipo (OTB-vs-OTB),
individua le anomalie, genera i suggerimenti con la motivazione a
livelli, salva le decisioni. E' l'unico posto dove vive la logica di
revenue management. Non e' stato toccato da questo lavoro.

**3. Contratto — `dashboard/dati.json`**
Il livello nuovo, centrale in questa fondazione: uno snapshot JSON
completo e autosufficiente dello stato del sistema, generato da
`dashboard/esporta_stato.py`. E' un "contratto" nel senso che la sua
struttura (i nomi dei campi, cosa significano, che tipo di dato sono) e'
quello che qualunque interfaccia puo' dare per scontato, indipendentemente
da come i dati sono stati calcolati. Ha un `schema_version` esplicito:
se in futuro cambia la struttura in modo incompatibile, la versione
cambia e chi legge il JSON puo' accorgersene.

**4. Interfaccia — `dashboard/dashboard.html`**
Legge **solo** `dati.json`, mai il database. Oggi e' una pagina statica
con CSS/JS inline, senza dipendenze esterne. Domani potrebbe essere
sostituita o affiancata da qualunque altro consumer dello stesso JSON,
senza che il motore debba saperlo.

Esistono due modi di consultarla, entrambi generati da
`esporta_stato.py`:
- `dashboard.html` + `dati.json` accanto: la pagina fa `fetch("dati.json")`
  a runtime. Funziona servita da un server locale (`python3 -m http.server`);
  molti browser bloccano `fetch()` di un file locale aperto con doppio
  click, e in quel caso la pagina mostra un banner con le istruzioni.
- `dashboard_standalone.html`: stesso template, ma con i dati incorporati
  al posto del marcatore `/*__DATI_INIZIO__*/ null /*__DATI_FINE__*/`
  (dentro `<script id="dati-incorporati">window.DATI = ...;</script>`).
  Funziona con un doppio click, senza server e senza leggere nessun altro
  file. Se il marcatore non e' presente nel template (es. e' stato
  modificato in un modo incompatibile), `esporta_stato.py` **fallisce
  esplicitamente** (eccezione + uscita con errore) invece di generare una
  pagina senza dati: e' una scelta deliberata, per non lasciare in giro
  una pagina che sembra funzionare ma e' vuota.

### Perche' separare cosi'

Il vincolo "l'interfaccia legge solo il JSON" e' la parte importante:
significa che il motore (le regole di revenue management, che sono la
parte piu' delicata e testata del progetto) puo' restare in Python puro
lato server/locale, mentre l'interfaccia puo' evolvere liberamente (nuovo
stile, nuova pagina, perfino un linguaggio diverso) senza mai dover
reimplementare le regole. E viceversa: si puo' cambiare la logica del
motore (es. aggiungere una nuova regola di pricing) senza dover toccare
l'HTML, purche' la forma del JSON resti compatibile.

### Come non duplicare le regole

`dashboard/esporta_stato.py` non riscrive la logica del pickup, delle
anomalie o della situazione: **importa le funzioni direttamente da
`report_mattina.py`** (`costruisci_situazione`, `costruisci_pickup_dettagliato`,
`rileva_anomalie`). Cosi' le due strade (report HTML mattutino e
dashboard) restano garantite allineate: se una regola cambia in
`report_mattina.py`, la dashboard la eredita automaticamente al prossimo
export, senza bisogno di modificare due posti.

Le uniche funzioni nuove sono in `core/calcoli.py` (nuovo modulo, non
tocca gli altri file di `core/`): cose che `report_mattina.py` non aveva
bisogno di esporre per se stesso, come la serie storica completa delle
rilevazioni (serve per disegnare un grafico, il report mostra solo
l'ultimo valore) o il calcolo del prossimo cambio di stagione tariffaria.

## Il contratto JSON, campo per campo

File: `dashboard/dati.json`. Oggetto radice con questi campi:

### `schema_version` (stringa)
Attualmente `"1.2"`. Va incrementata (parte intera per cambi incompatibili,
es. rinominare un campo; si puo' usare la parte decimale per aggiunte
retrocompatibili) ogni volta che la struttura del JSON cambia in un modo
che un consumer esistente potrebbe non aspettarsi.

**Storico delle versioni:**
- `1.0` — prima versione del contratto (sezione "Fondazione dashboard").
- `1.1` — rinominati i campi di `griglia_2027.voci` per allinearli al
  template `dashboard.html` "quadro del mattino" (`prezzo` -> `prezzo_eur`,
  aggiunti `codice` e `giorno`): vedi sezione `griglia_2027` piu' sotto.
  Nessun altro campo del contratto e' cambiato.
- `1.2` — `griglia_2027.voci` legge ora tutte le 9 colonne dello schema C2
  di `docs/SPECIFICA_MOTORE.md` (aggiunti `trattamento`, `unita_totali`,
  `unita_scarsa`; prima ne mancavano 3 su 9, vedi
  `docs/DIVERGENZE_SPECIFICA.md`). La dashboard mostra un indicatore visivo
  nel calendario quando `unita_scarsa` e' vero, e trattamento/unita' totali
  nel dettaglio del giorno.

### `meta` (oggetto)
| Campo | Tipo | Significato |
|---|---|---|
| `generato_il` | stringa ISO 8601 | Timestamp di generazione del JSON. |
| `nome_struttura` | stringa | Da `config.json` (`structure_name`). |
| `prossimo_cambio_stagione` | oggetto o `null` | Prossima transizione tra stagioni tariffarie (`da_stagione`, `a_stagione`, `data`, `giorni_mancanti`). E' un **proxy**, non un vero calendario di apertura/chiusura della struttura: quel dato oggi non esiste da nessuna parte nel sistema (vedi sezione "Cosa NON c'e' ancora" piu' sotto). |

### `situazione` (lista)
Una voce per ogni combinazione data futura / tipologia che ha **almeno
una** rilevazione nota in `inventory_snapshot` (demo, snapshot manuali o
log importato: la tabella non distingue la provenienza, quindi nemmeno il
JSON lo fa). Campi per voce: `data_target`, `tipologia_codice`,
`tipologia_nome`, `camere_libere`, `camere_totali`, `disponibilita_pct`,
`prezzo_del_giorno` (puo' essere `null` se non ancora registrato).

### `pickup` (lista)
Una voce per ogni combinazione data/tipologia con **almeno 2** rilevazioni
(serve un "prima" e un "dopo" per calcolare una variazione). Stessa
logica esatta della sezione "Pickup dettagliato" del report mattutino
(funzione importata, non duplicata). Campi aggiuntivi rispetto a
`situazione`: `ultima_rilevazione`, `n_rilevazioni`, `pickup_ultima_rilevazione`
(variazione vs. la rilevazione immediatamente precedente),
`pickup_7_giorni` (variazione vs. ~7 giorni prima, `null` se quella
rilevazione non esiste), `giorni_dallarrivo`, e `confronto_anno_precedente`.

`confronto_anno_precedente` e' `null` oppure un oggetto con
`camere_libere`, `disponibilita_pct`, `differenza_punti_percentuali`
dell'anno scorso **allo stesso numero di giorni prima dell'arrivo**
(offset -364, allineato al giorno della settimana). **Regola non
negoziabile ereditata dal report**: se quella rilevazione precisa non
esiste nello storico, il campo e' `null` (l'interfaccia deve mostrare
"storico non disponibile" o equivalente) — non viene MAI sostituito con
l'occupazione finale consuntiva, che misurerebbe una cosa diversa e
darebbe un confronto fuorviante.

### `booking_curve` (lista)
Una voce per ogni combinazione data/tipologia con almeno 2 rilevazioni
(stessa popolazione di `pickup`, ma qui interessa la serie completa, non
solo l'ultima variazione). Campi: `data_target`, `tipologia_codice`,
`tipologia_nome`, `serie` (lista ordinata cronologicamente di
`{data_rilevazione, camere_libere}`). E' pensata per essere disegnata
cosi' com'e', un punto per elemento della serie.

### `griglia_2027` (oggetto)
| Campo | Tipo | Significato |
|---|---|---|
| `disponibile` | booleano | `false` se il file `griglia_2027_tutte_tipologie.csv` non e' stato trovato (o non e' valido) nella root del progetto. |
| `nota` | stringa | Messaggio leggibile: quante voci caricate, quante scartate, o perche' la sezione e' vuota. |
| `voci` | lista | Schema C2 completo (9 colonne, dalla v1.2): `{data, giorno, codice, tipologia, trattamento, prezzo_eur, fascia, unita_totali, unita_scarsa}`. |

Campi di ogni voce: `data` (ISO `YYYY-MM-DD`), `giorno` (etichetta libera,
es. il giorno della settimana — puramente descrittiva, non validata),
`codice` (codice tipologia, es. `CLA`, sempre maiuscolo), `tipologia`
(nome per esteso, es. `Classic`), `trattamento` (stringa o `null`, es.
`BB`), `prezzo_eur` (numero), `fascia` (lettera, tipicamente A-D, usata
dall'interfaccia per colorare il calendario), `unita_totali` (intero o
`null`), `unita_scarsa` (booleano: se vero, la dashboard mostra un
indicatore "●" nella cella del calendario).

Cronologia dei campi: v1.0 `{data, tipologia, prezzo, fascia}` -> v1.1
rinominati/aggiunti `{data, giorno, codice, tipologia, prezzo_eur, fascia}`
-> v1.2 schema C2 completo (9 campi, questa versione).

Non e' un dato del database: e' un file CSV esterno opzionale, pensato per
la griglia tariffaria dell'anno successivo definita a tavolino (non
ancora "in produzione" nel motore delle regole). Le colonne attese nel CSV
hanno esattamente questi nomi (`data`, `giorno`, `codice`, `tipologia`,
`prezzo_eur`, `fascia`); date e numeri sono letti con lo stesso parsing
tollerante (formato italiano o ISO, virgola o punto decimale) usato da
`importa_log_disponibilita.py`, le cui funzioni sono importate anziche'
duplicate.

### `decisioni` (lista, ultime 20)
Storico delle proposte del motore (tabella `decision`), piu' recenti
prima. Campi: `id`, `creato_il`, `data_target`, `tipologia_codice`,
`tipologia_nome` (`null` se il suggerimento non riguarda una tipologia
specifica, es. un'opportunita' da evento), `tipo` (`decision_type`),
`suggerimento`, `motivazione_sintesi`, `motivazione_livelli` (lista di
stringhe, vuota se il suggerimento non e' di quelli con motivazione a
livelli — vedi `report_mattina.py`), `esito` (`pending`/`accepted`/`ignored`).

### `alert` (lista)
Le anomalie rilevate nell'orizzonte del report (stessa funzione
`rileva_anomalie` del motore, importata). Campi: `data_target`,
`tipologia_codice`, `tipologia_nome`, `motivo`, `dettaglio`.

## Come si evolve

### Una futura webapp e' semplicemente un nuovo consumer dello stesso JSON

Oggi l'unico consumer e' `dashboard.html` (pagina statica). Se in futuro
si volesse costruire un prodotto vero (webapp con backend, app mobile,
integrazione in un altro sistema), la strada e':

1. Il motore (`report_mattina.py` + eventuali nuove regole) resta
   com'e', o si arricchisce senza rompere la forma del JSON.
2. Si continua a generare (o si automatizza la generazione di) `dati.json`
   con `esporta_stato.py` — eventualmente con piu' frequenza, o esposto
   via un piccolo servizio invece che come file statico.
3. La nuova interfaccia legge **lo stesso contratto**, non il database.

Se una nuova interfaccia avesse bisogno di dati che il JSON oggi non
contiene, la modifica corretta e' **arricchire il contratto** (aggiungere
un campo, alzando se necessario `schema_version`), non far leggere il
database direttamente a un'altra interfaccia: altrimenti si perde il
punto di avere un contratto stabile.

### Un futuro adattatore Lybra: solo lo slot, nessun accesso oggi

Il progetto menziona Lybra Assistant come RMS del revenue manager (vedi
`config.json`, campo `pms_note`). Questa fondazione **non si connette a
Lybra in nessun modo**: non ci sono credenziali, chiamate API o import da
Lybra da nessuna parte nel codice. Lo slot pero' e' gia' chiaro
nell'architettura esistente (non aggiunto da questo lavoro, solo
confermato): la tabella `external_signal` (`signal_date`, `signal_type`,
`description`, `impact_score`) e' pensata esattamente per ricevere segnali
da fonti esterne come un RMS terzo.

Il percorso futuro, quando/se si vorra' collegare Lybra davvero, sarebbe:

1. Un nuovo script `importa_lybra.py` (o simile) scrive righe in
   `external_signal` — cosi' come oggi `importa_log_disponibilita.py`
   scrive in `inventory_snapshot`. Nessuna nuova tabella necessaria.
2. Il motore (`report_mattina.py`, regola "opportunita' da evento") legge
   gia' `external_signal` cosi' com'e': non richiede modifiche per
   accorgersi dei nuovi segnali.
3. Il JSON (`alert`, ed eventualmente una nuova sezione `segnali_esterni`
   se servisse mostrarli esplicitamente in dashboard) li espone
   all'interfaccia con lo stesso meccanismo di export gia' esistente.

Nessuna di queste tre parti esiste oggi: e' descritta qui solo perche' lo
strato dati/motore e' gia' pronto ad accoglierla senza modifiche
strutturali, quando servira'.

## Cosa NON c'e' ancora (limiti onesti di questa fondazione)

- **Nessun calendario di apertura/chiusura stagionale della struttura**:
  `config.json` modella solo le fasce tariffarie (alta/media/bassa), non
  se la struttura e' fisicamente aperta o chiusa. `meta.prossimo_cambio_stagione`
  e' il miglior proxy disponibile con i dati che esistono oggi, non un
  vero countdown all'apertura/chiusura.
- **`dati.json` non si aggiorna da solo**: va rigenerato lanciando
  `python3 dashboard/esporta_stato.py` (es. dopo ogni
  `importa_log_disponibilita.py`, o come parte di una routine giornaliera
  a scelta di chi usa il sistema). Non c'e' nessun meccanismo automatico
  in questa fondazione.
- **Nessun accesso a Lybra o ad altri sistemi esterni**: vedi sopra.
- **La griglia 2027 e' statica**: viene letta da un CSV esterno opzionale,
  non fa ancora parte del flusso "ufficiale" del motore (non alimenta le
  regole di pricing, e' solo visualizzata).
