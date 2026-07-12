# Divergenze tra il codice attuale e docs/SPECIFICA_MOTORE.md (v1.0)

Data verifica: 2026-07-12. Verifica di sola lettura: **nessuna riga di
codice e' stata modificata** per produrre questo documento, come
richiesto. Ogni voce riporta file e riga esatti nel codice cosi' com'e'
oggi, cosa dice la specifica, e cosa fa davvero il codice. Le decisioni su
cosa allineare (e quando) restano da prendere.

**Aggiornamento 2026-07-12 (stesso giorno, dopo le correzioni mirate):**
le voci R1, R2, R3, R4, R6, P5, C2, E1 sono state **risolte** con
correzioni applicate una alla volta e verificate con `demo.py` dopo
ciascuna (nessuna regressione). P2 e' stata **chiarita** in
`docs/SPECIFICA_MOTORE.md` (v1.1), non nel codice. R7, R8, R9 restano
**deferite** (nessun generatore di griglia annuale: fuori scope di questo
giro). P6 resta **by design** (controllo informativo, non un difetto da
correggere). Il dettaglio dello stato e' riportato in fondo a ciascuna
voce sotto.

Legenda gravita' uso personale in questo documento (non e' nella
specifica, solo per aiutare a prioritizzare): 🔴 comportamento diverso da
quanto promesso all'utente/RM, 🟡 gap di soglia/bordo, ⚪ funzionalita'
descritta in specifica ma non ancora costruita.

---

## R1 — aumento_prezzo

**Specifica**: "disp **≤** 25% E **≥** 14 gg out → proporre +10%"

**Codice**: `report_mattina.py:552-553`
```python
and voce["disponibilita_pct"] < s["disponibilita_max_pct"]
and voce["giorni_al_target"] > s["giorni_minimi"]
```
🟡 Usa `<` e `>` (stretti) invece di `≤` e `≥`. Ai valori di confine esatti
(disponibilita' esattamente 25%, o esattamente 14 giorni all'arrivo) la
regola **non scatta**, mentre per la specifica dovrebbe scattare. I valori
in `config.json` (`disponibilita_max_pct: 25`, `giorni_minimi: 14`,
`aumento_pct: 10`) corrispondono esattamente alla specifica: diverge solo
l'operatore di confronto, non le soglie.

**Stato: ✅ risolto.** Operatori corretti in `<=`/`>=` in
`report_mattina.py`, coerenti con la specifica.

---

## R2 — ribasso_o_promo

**Specifica**: "disp **≥** 50% E **≤** 10 gg out → proporre promo mirata
(long-stay/canale), **NON taglio griglia**"; nota: "il taglio listino e'
ultima scelta".

**Codice**: `report_mattina.py:569-577`
```python
s = soglie["ribasso_o_promo"]
if (
    voce["pickup"] is not None
    and voce["pickup"] <= 0                                   # riga 572
    and voce["disponibilita_pct"] > s["disponibilita_min_pct"] # riga 573
    and 0 <= voce["giorni_al_target"] < s["giorni_massimi"]    # riga 574
):
    ...
    testo = "Valutare un ribasso di prezzo o una promozione mirata"  # riga 577
```
Tre divergenze distinte in questo blocco:
1. 🟡 Operatori: `>` invece di `≥` (riga 573), `<` invece di `≤` (riga
   574) — stesso tipo di gap di confine di R1.
2. 🟡 Condizione aggiuntiva non presente in specifica: riga 572 richiede
   anche `pickup <= 0` (pickup fermo/negativo). La specifica R2 attiva la
   regola solo su disponibilita'+giorni, senza menzionare il pickup come
   condizione necessaria. Con questa condizione in piu' il codice e' oggi
   **piu' restrittivo** della specifica (propone la promo in meno casi).
3. 🔴 Il testo proposto (riga 577) suggerisce esplicitamente un "ribasso
   di prezzo" come prima opzione alla pari di "promozione mirata". La
   specifica dice il contrario: la leva di prezzo e' **l'ultima scelta**,
   si preferiscono leve non-prezzo (min LOS, early-bird, condizioni per
   canale). Il testo attuale non riflette questa gerarchia.

**Stato:**
- Punto 1 (operatori `>`/`<`): **non toccato**, fuori dall'elenco delle
  correzioni mirate di questo giro (solo R1/R3/R4 erano in scope per gli
  operatori). Resta `>`/`<` in `report_mattina.py`.
- Punto 2 (condizione `pickup <= 0`): **✅ risolto**. Rimossa: non era
  una decisione presa, era un'aggiunta autonoma non prevista dalla
  specifica. La regola ora si attiva solo su disponibilita'+giorni, come
  da specifica.
- Punto 3 (testo "ribasso di prezzo" alla pari): **✅ risolto**. Il
  ribasso di listino non e' piu' proposto come opzione alla pari: il
  testo ora propone "una promozione mirata (minimo soggiorno,
  early-booking, condizioni per canale) - non un ribasso di listino", e
  la motivazione chiarisce che il ribasso resta l'ultima opzione.

---

## R3 — chiusura_ota

**Specifica**: "disp **≤** 10% → proporre chiusura canali commissionati"

**Codice**: `report_mattina.py:591`
```python
if voce["disponibilita_pct"] < s["disponibilita_critica_pct"]:
```
🟡 Stesso pattern di R1/R2: `<` invece di `≤`. Il valore config
(`disponibilita_critica_pct: 10`) e' corretto, diverge solo l'operatore.

**Stato: ✅ risolto.** Operatore corretto in `<=` in `report_mattina.py`,
coerente con la specifica.

---

## R4 — unita_scarse

**Specifica**: "totale ≤ 3 unita' (soglia_totale_unita) | ... resta 1
unita' e **≥** X gg out → +10%; in griglia di apertura: MAI sconto, +12%
(non +10%) su date esaurite"

**Codice**: `report_mattina.py:526-528` (soglia tipologia, corretta):
```python
tipologia_scarsa = (
    voce["unita_totale"] is not None and voce["unita_totale"] <= soglia_totale_unita_scarse
)
```
Questo confronto e' gia' `<=`, coerente con la specifica.

`report_mattina.py:535`:
```python
if 0 < voce["disponibili"] <= soglia_unita_assolute_scarse and voce["giorni_al_target"] > s["giorni_minimi"]:
```
🟡 `giorni_al_target > s["giorni_minimi"]`: di nuovo `>` invece di `≥`.

🔴⚪ La parte "in griglia di apertura: MAI sconto, +12% (non +10%) su date
esaurite" riguarda il generatore della griglia annuale (R7, vedi sotto):
**non esiste nel codice nessun generatore di griglia**, quindi questo
+12% differenziato non e' implementato da nessuna parte (ne' giusto ne'
sbagliato: assente).

**Stato:**
- Operatore `giorni_al_target > s["giorni_minimi"]`: **✅ risolto**,
  ora `>=` in `report_mattina.py`.
- Parte "griglia di apertura +12%": **⚪ deferita**, dipende da R7 (non
  implementata), esplicitamente esclusa da questo giro di correzioni.

---

## R5 — pickup

**Verifica**: `report_mattina.py:127-224` (`_otb_stly`,
`costruisci_pickup_dettagliato`). ✅ **Nessuna divergenza trovata.**
Corrispondenza esatta STLY (offset -364, T1+T2), "storico non disponibile"
esplicito quando la rilevazione precisa non esiste (mai sostituito con
l'occupazione consuntiva, coerente con P5). Soglia "≥2 rilevazioni"
rispettata (`len(rilevazioni) < 2: continue`).

---

## R6 — eventi

**Specifica**: "evento con impatto **alto** su data → decision
opportunita_evento"

**Codice**: `report_mattina.py:604-617`
```python
WHERE signal_date BETWEEN ? AND ? AND impact_score > 0     # riga 608
...
urgenza = "alta" if impatto >= 8 else "media" if impatto >= 5 else "bassa"  # riga 617
```
Due divergenze:
1. 🟡 La query (riga 608) genera una decision per **qualunque**
   `impact_score > 0`, non solo per impatto "alto": un evento con
   impatto 1/10 genera oggi lo stesso tipo di suggerimento di uno con
   impatto 9/10 (cambia solo l'etichetta di urgenza).
2. 🟡 Le soglie 8 e 5 (riga 617) che decidono l'urgenza sono scritte nel
   codice, non lette da `config.json` (non esiste una sezione
   `rules_thresholds.eventi`). Diverge dal criterio generale "nessuna
   soglia scritta nel codice" seguito per le altre regole.

Inoltre, la decision `opportunita_evento` (come `chiusura_ota`, vedi P2
sotto) non ha una ricetta a 5 livelli.

**Stato: ✅ risolto.** Nuova chiave `rules_thresholds.eventi.impatto_minimo_alert`
in `config.json` (default 7). La query ora filtra `impact_score >= ?`
usando questa soglia letta da config: un evento sotto soglia non genera
piu' nessuna decision `opportunita_evento`. Verificato che il valore
viene davvero letto da config (test con soglia alzata a 9: 0 decision
generate sui due eventi demo, entrambi impatto 7-8). L'urgenza, non
avendo piu' senso graduarla sotto una soglia minima unica, e' ora sempre
"alta" per le decisioni che superano la soglia.
(Nota sulla ricetta a 5 livelli mancante per `opportunita_evento`: vedi
P2 sotto, chiarito come comportamento by-design, non un difetto.)

---

## R7 — griglia_apertura ⚪ NON IMPLEMENTATA

**Specifica**: genera una griglia di prezzi per fasce di occupazione
(A ≥97% → +10%/+12%, B 90-97% → +5%, C 75-90% → +2,5%, D <75% → 0%/-5%
con eccezioni).

**Codice**: nessuna funzione genera una griglia. Quello che esiste
(`dashboard/esporta_stato.py:171` `_carica_griglia_2027`) e'
esclusivamente un **caricatore/visualizzatore** di un CSV gia' pronto
fornito da fuori (`griglia_2027_tutte_tipologie.csv`): non applica
nessuna delle regole di fascia della specifica, si limita a leggerlo e a
passarlo alla dashboard cosi' com'e'.

**Stato: ⚪ deferita.** Esplicitamente fuori scope in questo giro di
correzioni (non tra i punti richiesti). Nessuna modifica.

---

## R8 — micro_stagioni ⚪ NON IMPLEMENTATA

**Specifica**: corridoio floor/ceiling (min×0,95 / max×1,15) sul mese
sorgente per ogni prezzo di griglia.

**Codice**: nessuna traccia. Non essendoci un generatore di griglia (R7),
non c'e' nemmeno il corridoio che dovrebbe vincolarlo.

Nota per non confondere: il "Livello 5 - Limiti min/max" della ricetta a
5 livelli dei suggerimenti quotidiani (`report_mattina.py:462-475`) fa
un clamp, ma e' un meccanismo **diverso**: usa il range
`price_ranges` di `config.json` per la stagione tariffaria della singola
data, non il corridoio min×0,95/max×1,15 del mese sorgente descritto da
R8 (che si applicherebbe alla costruzione della griglia annuale, non ai
suggerimenti giorno per giorno).

**Stato: ⚪ deferita.** Esplicitamente fuori scope in questo giro di
correzioni (non tra i punti richiesti). Nessuna modifica.

---

## R9 — differenziali_tipologie ⚪ NON IMPLEMENTATA

**Specifica**: prezzo di ogni tipologia = prezzo Classic × rapporto
moltiplicativo calibrato sul punto di riferimento piu' recente.

**Codice**: `config.json` (`price_ranges`) definisce per ogni tipologia
una fascia di prezzo **assoluta e indipendente** (es. `CLA: {bassa:
[110,180], alta:[300,449]}`, `SUI: {bassa:[200,300], alta:[480,704]}`),
non un rapporto rispetto a Classic. Non c'e' nel codice nessun calcolo di
tipo "prezzo Classic × ratio".

**Stato: ⚪ deferita.** Esplicitamente fuori scope in questo giro di
correzioni (non tra i punti richiesti). Nessuna modifica.

---

## P1 — Copilot prima di autopilot

✅ **Nessuna divergenza trovata.** Il motore scrive solo nella tabella
`decision` (`outcome='pending'`); non ho trovato nessuna scrittura
automatica verso `channel_event` (la tabella esiste in `core/db.py:77`
ma non viene mai scritta da `report_mattina.py`, solo letta/creata come
schema) ne' verso PMS/canali esterni.

---

## P2 — Spiegabilita' (ricetta a 5 livelli)

**Specifica**: "ogni suggerimento di prezzo espone la motivazione in 5
livelli etichettati ... Un suggerimento senza ricetta e' un bug."

**Codice**: `costruisci_livelli_prezzo()` (`report_mattina.py:385-475`)
produce correttamente L1→L5 nell'ordine della specifica, usata da:
- `aumento_prezzo` (riga 563)
- `unita_scarse_aumento` (riga 545)
- `ribasso_promo` (riga 584)

🔴 **Non la usano** (niente campo `"livelli"`, solo un `motivo` testuale):
- `chiusura_ota` — `report_mattina.py:599` (`suggerimenti.append` senza
  `"livelli"`)
- `opportunita_evento` — `report_mattina.py:628` (idem)

La tabella delle "Regole attive" della specifica assegna comunque un
livello anche a R3 (`chiusura_ota | L2`) e R6 (`eventi | L3`), quindi non
e' del tutto chiaro se la specifica si aspetti una ricetta a 5 livelli
anche per queste due, o solo che quella regola *contribuisca* al livello
indicato quando genera un'altra decision di prezzo. Segnalo l'ambiguita'
oltre alla divergenza: e' una delle cose su cui serve una decisione
esplicita.

**Stato: 🔵 chiarito (non un bug del codice).** `docs/SPECIFICA_MOTORE.md`
aggiornata a v1.1: P2 ora dichiara esplicitamente che la ricetta a 5
livelli si applica solo alle decisioni che modificano un prezzo di
griglia (`aumento_prezzo`, `unita_scarse_aumento`, `ribasso_promo`).
`chiusura_ota` e `opportunita_evento` non toccano un prezzo di griglia e
restano motivazioni testuali per design. Il codice non e' stato toccato:
l'ambiguita' era nella specifica v1.0, non un difetto di
`report_mattina.py`.

---

## P3 — Core agnostico dal settore

✅ **Nessuna violazione sostanziale in `report_mattina.py` o `core/*.py`.**
Le uniche occorrenze di "hotel"/"camera" sono in commenti di
`core/db.py:5-6,20` che *dichiarano* l'assenza del concetto (es. "non
conosce il concetto di hotel o camera"), non un uso reale nella logica.

---

## P4 — Solo stdlib

✅ **Nessuna divergenza.** Nessun modulo di terze parti importato in
nessun file del progetto (verificato: solo `sqlite3`, `csv`, `json`,
`datetime`, `html`, `argparse`, `sys`, `pathlib`, `collections` — tutta
libreria standard).

---

## P5 — Onesta' sui dati

✅ Confermato per il pickup (R5, vedi sopra): "storico non disponibile"
esplicito quando serve.

🟡 **Zona grigia non coperta allo stesso modo**: `_statistiche_periodo()`
(`report_mattina.py:230-255`, in particolare le `COALESCE(..., 0)` alle
righe 250-251) usata dal confronto con l'anno precedente (sezione "c" del
report, non il pickup) restituisce sempre `0` prenotazioni/notti/ricavo
quando non trova righe, **senza distinguere** "quel periodo ha
davvero avuto zero prenotazioni" da "non abbiamo dati per quel periodo
storico" (es. lo storico non arriva cosi' indietro). A differenza del
pickup, qui non compare mai la dicitura "storico non disponibile": uno
zero e' sempre mostrato come se fosse un dato reale.

**Stato: ✅ risolto.** Nuova funzione `_storico_copre()` in
`report_mattina.py` verifica se esiste storico di prenotazioni confermate
prima/alla data di riferimento dell'anno precedente. Quando lo storico
non copre quel periodo, la sezione (c) del report scrive esplicitamente
"storico non disponibile" (e "N/D" nelle celle numeriche collegate)
invece di uno zero indistinguibile da un dato reale. Verificato forzando
l'assenza di storico e controllando che il testo compaia nel report
generato.

---

## P6 — Checksum sui numeri trascritti

**Specifica**: "ogni dato trascritto ha un checksum; checksum fallito =
righe sospette elencate, dato usato come indicativo, mai come esatto."

**Codice**: `importa_log_disponibilita.py:328-334` stampa una somma di
`camere_libere` per data di rilevazione, ma e' **solo informativa** ("da
confrontare a occhio col foglio", riga 330): non c'e' nessun valore di
riferimento con cui confrontarla automaticamente, nessuna soglia di
scarto, nessun elenco di "righe sospette", nessun flag che marchi i dati
come "indicativi" quando il confronto fallisce. Il meccanismo di
validazione attivo descritto dalla specifica non esiste: quello che c'e'
oggi e' un aiuto per il controllo manuale, non un controllo automatico.

**Stato: ⚪ by design, nessuna modifica.** Come da istruzione esplicita
del task di correzione: P6 e' informativa per scelta, non un difetto da
correggere in questo giro.

---

## C2 — Schema CSV griglia

**Specifica**: "colonne data, giorno, tipologia, codice, **trattamento**,
prezzo_eur, fascia, **unita_totali**, **unita_scarsa**" (9 colonne).

**Codice**: `dashboard/esporta_stato.py:67`
```python
COLONNE_GRIGLIA_2027 = ["data", "giorno", "codice", "tipologia", "prezzo_eur", "fascia"]
```
🔴 Solo 6 delle 9 colonne dello schema C2: mancano **`trattamento`**,
**`unita_totali`**, **`unita_scarsa`**. Il caricatore (`_carica_griglia_2027`,
righe 171-219) non le legge ne' le espone nel JSON: chi produce una
griglia con lo schema C2 completo vedrebbe silenziosamente ignorate
queste tre colonne. (`confronto_griglie.py`, aggiunto in questo stesso
lavoro per R12, usa invece lo schema C2 completo a 9 colonne per il primo
file — vedi il modulo stesso per il dettaglio della tolleranza sul
secondo file.)

**Stato: ✅ risolto.** `COLONNE_GRIGLIA_2027` e `_carica_griglia_2027()`
in `dashboard/esporta_stato.py` leggono ora tutte e 9 le colonne dello
schema C2, incluse `trattamento`, `unita_totali`, `unita_scarsa`.
`schema_version` del contratto JSON portata a `1.2`
(`docs/ARCHITETTURA_DASHBOARD.md` aggiornato). Il calendario in
`dashboard.html` mostra un simbolo "●" sulle celle con `unita_scarsa=1`
(con legenda dedicata), e il dettaglio-giorno mostra trattamento e
unita' totali per ogni tipologia.

---

## E1 — Multi-struttura, niente Cetus hardcoded

✅ **`report_mattina.py`, `core/*.py`, `dashboard/esporta_stato.py`**:
nessun valore "Cetus"/"Cetara" hardcoded (verificato via ricerca testuale
su tutto il codice del motore).

🟡 **`dashboard/dashboard.html:45`**:
```html
<title>Cetus — Quadro del mattino</title>
```
Il tag `<title>` e' statico e non viene mai aggiornato da JavaScript
(nessuna occorrenza di `document.title` in tutto il file): aprendo la
dashboard per una struttura diversa da Cetus, il titolo mostrato nella
scheda del browser resterebbe comunque "Cetus — Quadro del mattino".

**Stato: ✅ risolto.** `avvia(dati)` in `dashboard.html` imposta ora
`document.title` da `meta.nome_struttura` quando presente. Verificato
generando lo standalone dai dati demo e leggendo il `<title>` renderizzato
via Chromium headless: risulta "Hotel Cetus — Quadro del mattino" invece
del valore statico.

🟡 **`dashboard/dashboard.html:187`**:
```html
<h1 id="nome-hotel">Hotel Cetus <span class="quadro">— quadro del mattino</span></h1>
```
Testo segnaposto hardcoded. Viene sovrascritto a runtime se
`meta.nome_struttura` e' presente nel JSON (`dashboard.html:222`), quindi
nell'uso normale non e' visibile; resta pero' visibile per un istante
prima che il JS giri, o in modo permanente se il caricamento dei dati
fallisce (vedi il banner di errore) — in quel caso mostrerebbe "Hotel
Cetus" anche per una struttura diversa.

**Stato: non toccato.** Il punto E1 richiesto in questo giro riguardava
solo `document.title` (vedi sopra); questo segnaposto nell'`<h1>` resta
com'era, nessuna modifica.

(`genera_demo.py` non e' incluso in questa voce: e' esplicitamente lo
script di generazione dati demo *per Cetus*, il suo scopo dichiarato fin
dalla richiesta originale del progetto e' proprio quello di essere
Cetus-specifico.)

---

## Voci della specifica non toccate da questa verifica

- **T3** (mapping date tra anni per le griglie, festivita' mobili):
  dipende da R7 (non implementata), quindi non verificabile nel codice
  attuale — non c'e' nulla da confrontare.
- **P1-R10, P1-R11, P2-R13, P3-R14** (regole pianificate): per
  definizione non ancora implementate, nessuna divergenza da segnalare
  (non c'e' codice che le riguardi, ne' dovrebbe essercene).
- **C1, C3, C4**: verificate, nessuna divergenza (C1: `schema_version`
  gestita correttamente, vedi `docs/ARCHITETTURA_DASHBOARD.md`; C3: le
  colonne di `importa_log_disponibilita.py` corrispondono esattamente;
  C4: `external_signal` resta un canale generico, nessun accesso a Lybra
  nel codice).
