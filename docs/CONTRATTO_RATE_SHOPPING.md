Fonte di verità dal 13/07/2026 (commit di introduzione in questo repo); la copia in project knowledge è storica.

# CONTRATTO DATI — RATE SHOPPING (R11)

Definito: 12/07/2026, chat "ripresa post stato v2".
Stato: SCHEMA DEFINITO, nessun codice ancora scritto (per scelta: niente codice
prima dei dati). Destinazione: progetto dev; da portare nel repo come
`docs/CONTRATTO_RATE_SHOPPING.md` nella sessione Claude Code n.2 — da quel
momento il repo è la fonte di verità e questa copia va considerata storica.
NON condividere col Consulente Mattina finché non esistono dati reali.

## Principi di progettazione

- Stesso stile del log disponibilità: colonne in italiano, parser tollerante
  (date IT/ISO, numeri con virgola o punto), una riga = una osservazione.
- Agnostico rispetto al raccoglitore: oggi manuale, domani browser agentico
  (Comet) o tool dedicato — lo schema non cambia.
- Due file: anagrafica separata dalle osservazioni (l'anagrafica si compila
  una volta sola).

## File 1 — competitor_anagrafica.csv (una tantum, aggiornamenti rari)

| colonna | descrizione | esempio |
|---|---|---|
| codice | identificativo breve, stabile nel tempo | CET01 |
| nome | nome struttura | Hotel Marincanto |
| localita | comune | Positano |
| stelle | categoria | 4 |
| unita_stimate | numero camere se noto, altrimenti vuoto | 30 |
| tipologia_riferimento | tipologia di quella struttura equivalente alla nostra Classic | Camera Superior Vista Mare |
| canale_rilevazione | dove si guarda il prezzo | sito / Booking.com |
| note | qualunque cosa utile | ristorante stellato, apre ad aprile |

## File 2 — rilievi_competitor.csv (una riga per prezzo osservato)

| colonna | descrizione | esempio |
|---|---|---|
| data_rilevazione | quando si è guardato | 2026-07-15 |
| codice_competitor | dal file anagrafica | CET01 |
| data_target | notte a cui si riferisce il prezzo | 2026-08-14 |
| trattamento | RO / BB / HB — OBBLIGATORIO, mai vuoto | BB |
| occupazione | numero ospiti della quotazione | 2 |
| notti | lunghezza soggiorno quotata | 1 |
| prezzo_eur | prezzo totale mostrato, tasse incluse | 512 |
| tariffa_tipo | flessibile / non_rimborsabile | flessibile |
| disponibilita | disponibile / esaurito / chiuso | disponibile |
| canale | sito / booking / expedia | booking |
| note | libere | ultima camera |

## Regole del contratto

1. TRATTAMENTO SEMPRE DICHIARATO. Lezione già pagata sui nostri dati
   (2025=RO vs 2026=BB): mai confrontare prezzi senza trattamento esplicito.
   Riga senza trattamento = scartata dal parser con motivo dichiarato.
2. STANDARD DI QUOTAZIONE FISSO: 2 adulti, 1 notte, tariffa flessibile, su
   prezzi PUBBLICI (rate shopping legale in EU su prezzi pubblici — fonte:
   estratto ricerca Perplexity). Deviazioni (minimo notti imposto, solo non
   rimborsabile) si registrano in tariffa_tipo/note: il dato resta, il
   confronto lo pesa diversamente.
3. ESAURITO È UN DATO, NON UN BUCO: disponibilita=esaurito con prezzo vuoto
   è una riga valida e preziosa (competitor sold out = pressione di domanda).
   Il parser NON la scarta.
4. POCHE DATE COSTANTI > TANTE UNA TANTUM (Pareto): stesse 18 date chiave già
   monitorate nel log disponibilità, rilevazione 1-2 volte a settimana,
   4-6 competitor. Manuale ≈ 10 minuti con browser agentico.
5. PERIMETRO COMPETITOR: scelta delle strutture a cura di Chicco (conosce il
   competitive set reale di Cetara/Costiera). 4-6 strutture, non di più.
   Per ciascuna va indicata la tipologia equivalente alla Classic.

## Attivazione (quando si vorrà)

1. Chicco indica 4-6 competitor + tipologia equivalente → compilazione
   anagrafica (10 minuti).
2. Scelta del raccoglitore: Google Sheet gemello del log disponibilità,
   oppure prompt per browser agentico (da preparare al momento).
3. Parser: gemello di importa_log_disponibilita.py con le stesse verifiche
   P6 (coerenza, warning, audit). Si scrive in una sessione Claude Code
   futura SOLO quando esistono i primi dati reali.

## Integrazione futura nel motore

- I rilievi alimenteranno una regola R11 nel motore (posizionamento prezzo
  vs competitive set come livello aggiuntivo di motivazione, non come
  automatismo): coerente con "copilot prima di autopilot".
- Slot già previsto nell'architettura a strati (connettori → motore →
  report): il rate shopping è un connettore in ingresso come il log
  disponibilità, nessuna modifica strutturale richiesta.
