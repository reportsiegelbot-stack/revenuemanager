# SPECIFICA MOTORE DECISIONALE — Revenue Cetus / piattaforma
Versione specifica: 1.3 — 13/07/2026 (v1.3: R7-R9 da pianificate a ossatura attiva, vedi note)
Questo documento è la FONTE DI VERITÀ delle regole del motore. Ogni modifica al
comportamento passa da qui: lesson → aggiornamento specifica (nuova versione) →
config.json → codice. Nessuna regola vive solo nel codice.

## Principi non negoziabili
P1. Copilot prima di autopilot: il motore suggerisce con motivazione scritta,
    non scrive mai in PMS/canali finché il valore non è misurato.
P2. Spiegabilità: ogni suggerimento di prezzo espone la motivazione in 5 livelli
    etichettati (L1 prezzo base griglia → L2 fattore occupazione/pickup →
    L3 giorno-settimana/eventi → L4 regola unità scarse → L5 limiti min/max)
    più una riga di sintesi. Un suggerimento senza ricetta è un bug.
    Ambito (v1.1): la ricetta a 5 livelli si applica SOLO alle decisioni che
    modificano un prezzo (aumento_prezzo, unita_scarse_aumento, ribasso_promo).
    chiusura_ota e opportunita_evento non toccano un prezzo di griglia: restano
    motivazioni testuali per design, non ricetta a 5 livelli. In v1.0 questo non
    era esplicito: non era un bug del codice, era un'ambiguità della specifica.
P3. Core agnostico dal settore: il motore ragiona su capacity_unit, snapshot,
    decision; "camera", "hotel", "retta" esistono solo in config e interfaccia.
P4. Solo stdlib Python; nessuna dipendenza esterna senza decisione esplicita.
P5. Onestà sui dati: mai inventare uno storico. Se il confronto richiesto non
    esiste (es. OTB anno precedente), si scrive "storico non disponibile".
P6. Numeri: ogni dato trascritto ha un checksum; checksum fallito = righe
    sospette elencate, dato usato come indicativo, mai come esatto.
    Attivo (v1.2): `importa_log_disponibilita.py` calcola un hash SHA-256 di
    ogni file importato e verifica automaticamente la coerenza interna
    (date, duplicati) e temporale (salti di disponibilità/prezzo anomali)
    di ogni riga, registrando tutto in `import_audit`. I controlli
    generano solo WARNING, mai un blocco: decide sempre l'umano.

## Convenzioni temporali
T1. Confronti anno-su-anno: offset -364 giorni (52 settimane esatte), mai -365.
T2. Pace: OTB oggi vs OTB stesso numero di giorni-dall'arrivo anno precedente.
T3. Mapping tra anni per griglie: stessa data ±k giorni per allineare il
    giorno-settimana (2025→2027: +2); festività mobili (Pasqua) mappate
    festività-su-festività con correzione stagionale dichiarata.

## Regole attive (implementate)
Formato: ID | nome | input | soglie (config.rules_thresholds) | output | livello ricetta

R1 | aumento_prezzo | disponibilità % e giorni-out per data/tipologia |
   disp ≤ 25% E ≥ 14 gg out → proporre +10% | decision tipo aumento_prezzo | L2
R2 | ribasso_o_promo | idem | disp ≥ 50% E ≤ 10 gg out → proporre promo mirata
   (long-stay/canale), NON taglio griglia | decision ribasso_promo | L2
   Nota: preferire leve non-prezzo su spalle (min LOS, early-bird), il taglio
   listino è ultima scelta (ricerca: su shoulder si difende l'occupazione con
   valore, sui picchi si massimizza ADR).
R3 | chiusura_ota | disponibilità critica | disp ≤ 10% → proporre chiusura
   canali commissionati | decision chiusura_ota | L2
R4 | unita_scarse | tipologie con totale ≤ 3 unità (soglia_totale_unita) |
   ragionare in unità residue assolute, mai in %; resta 1 unità e ≥ X gg out →
   +10%; in griglia di apertura: MAI sconto, +12% (non +10%) su date esaurite |
   decision unita_scarse_aumento | L4
R5 | pickup | log disponibilità (≥2 rilevazioni per data target) | variazione
   camere libere vs rilevazione precedente e vs -7 gg; confronto YoY solo
   OTB-vs-OTB (T1+T2), altrimenti "storico non disponibile" | sezione pickup
   del report + input a R1/R2 | L2
R6 | eventi | calendario eventi locali con impatto stimato 1-10 | evento con
   impatto alto su data → decision opportunita_evento (valutare rialzo/chiusura
   canali scontati) | L3
R7 | griglia_apertura | prezzi anno-2 + occupazione finale anno-2 per data |
   fasce per occupazione: A ≥97% → +10% (+12% scarse); B 90-97% → +5%;
   C 75-90% → +2,5%; D <75% → 0%, e -5% solo se prezzo sopra mediana del mese
   (mai -5% sulle tipologie scarse) | griglia CSV con fascia e motivazione riga
   per riga | L1
R8 | micro_stagioni | min/max mese sorgente | floor = min×0,95, ceiling =
   max×1,15: nessun prezzo di griglia fuori dal corridoio del suo mese | L5
R9 | differenziali_tipologie | rapporti moltiplicativi dal punto di riferimento
   più recente per tipologia (oggi: picco 2026 BB) | prezzo tipologia = prezzo
   Classic × ratio; da ricalibrare quando esisterà storico per tipologia | L1

R7-R9 — ossatura attiva (v1.3): `genera_griglia.py` implementa le tre regole
   sopra come pipeline di 8 fasi pure e componibili (mapping calendario,
   classificazione fascia, delta base, correzioni evento, floor/ceiling,
   estensione tipologie, regola scarsità, override esplicito), parametri in
   `griglia_config.json` (mai numeri cablati nel codice). Verificata
   riproducendo esattamente `griglia_2027_classic_roomonly_v2.csv` e
   `griglia_2027_tutte_tipologie_v2.csv` (griglie costruite a mano, non nel
   repo): 1773/1776 celle identiche (99,83%) senza override; con un
   override di 3 righe per l'unica data con una divergenza residua
   documentata (2027-10-11: il prezzo sorgente supera la mediana del mese
   ma la griglia originale non ha applicato lo sconto "sopra mediana" —
   non è stato un parametro sbagliato, è un'eccezione puntuale della
   griglia originale), il diff è zero. Dettaglio completo della verifica
   in `docs/DIVERGENZE_SPECIFICA.md`. Resta "ossatura" perché la mappatura
   calendario generale (proxy per date fuori dal range dei dati sorgente,
   festività mobili come Pasqua) richiede ancora una preparazione/curazione
   dei dati sorgente in ingresso: lo script non inventa da solo quali date
   sono "Pasqua" o quali eccezioni puntuali servono, per quello c'è il file
   di override.
R13 | esiti_decisioni | decisioni di prezzo tracciate in `decision_outcome`
   (aumento_prezzo, unita_scarse_aumento, ribasso_promo) con almeno N giorni
   di anzianità | soglie in `rules_thresholds.esiti`
   (giorni_minimi_misurazione, tolleranza_pct_seguita) | aggiorna
   `decision_outcome.status`: seguita / parziale / non_seguita / non
   misurabile, confrontando la rilevazione più recente successiva alla
   decisione sullo STESSO canale su cui era nata; se manca, "storico non
   disponibile" (P5). Per ribasso_promo la misura è dichiaratamente
   indiretta (variazione di disponibilità, non verifica dell'azione: le
   unità si vendono anche senza promo) e non ha un prezzo suggerito, quindi
   nessun esito "parziale" per questo tipo. Script dedicato:
   `esiti_decisioni.py`. Nota: chiusura_ota e opportunità_evento non sono
   tracciate da R13 in questa fase (nessun prezzo suggerito da misurare);
   un'eventuale misura di impatto per queste due (es. sul pickup) è Fase 2,
   non ancora decisa | nessun livello ricetta proprio (misura l'esito di
   una decisione già tracciata, non genera un nuovo suggerimento)

## Regole pianificate (non ancora implementate)
Pnn = priorità (1 alta). Implementare SOLO passando da questa specifica.

P1-R10 | pace_alert | quando il log avrà ≥ 2 settimane di storico: pace che
   diverge oltre ±20% vs riferimento (prima stagione: vs settimana precedente;
   dal 2027: vs STLY OTB) → alert dedicato con data e direzione. Fonte: ricerca
   (griglia = ipotesi da ricalibrare presto).
P1-R11 | prezzo_pubblico_vs_griglia | quando entrerà il rate shopping (o
   rilevazione manuale da Booking): scrivere in external_signal
   {data, canale, prezzo_pubblico}; il report mostra delta griglia vs pubblico
   e sospende R1/R2 sulle date dove Lybra sta già lavorando (pubblico ≠ griglia).
P2-R12 | confronto_griglie | date allineate, per tipologia: nostra griglia vs
   griglia RM 2027 → delta per data/fascia/mese, sintesi per la decisione di
   dicembre. Modulo autonomo, input = due CSV con stesso schema.
P3-R14 | net_revpar_canale | richiede commissioni per canale (dato mancante):
   ricavo netto per canale, input a R3.
P?-R15 | esiti_evento (Fase 2, non prioritizzata) | misurare l'esito delle
   decisioni chiusura_ota/opportunità_evento (R13 oggi le esclude: non hanno
   un prezzo suggerito, servirebbe una metrica diversa, es. sul pickup) →
   da definire quando servirà davvero.

## Contratti tra strati (non rompere mai)
C1. dati.json schema_version: ogni cambio di campi = bump di versione +
    aggiornamento docs/ARCHITETTURA_DASHBOARD.md. La UI legge solo il JSON.
C2. griglia CSV: colonne data, giorno, tipologia, codice, trattamento,
    prezzo_eur, fascia, unita_totali, unita_scarsa. Chi produce griglie
    (2027, future) produce QUESTO schema.
C3. log disponibilità: data_rilevazione, data_target, tipologia, camere_libere,
    prezzo_del_giorno, note. L'importatore è tollerante sui formati ma lo
    schema logico è questo.
C4. external_signal: slot unico per segnali esterni (eventi, prezzi pubblici,
    futuro adattatore Lybra). Il motore legge, non conosce la fonte.

## Evoluzione prodotto (per non costruirsi muri)
E1. Multi-struttura = un config.json per struttura: nomi, tipologie, soglie,
    stagionalità. Il codice non contiene MAI valori di Cetus hardcoded: se ne
    trovi uno, è un bug da spostare in config.
E2. Nuove interfacce (webapp, mobile, prodotto) = nuovi consumer di dati.json.
E3. Nuovi settori = nuovo vocabolario in config + connettori; core invariato.
