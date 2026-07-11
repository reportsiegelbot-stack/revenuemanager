# Verifica repository esterni (segnalati da ricerca Perplexity)

Data verifica: 2026-07-11
Metodo: `git ls-remote` + `git clone --depth 1` in cartella temporanea fuori
dal repository, ispezione diretta dei file (README, LICENSE, codice,
notebook), più lettura delle pagine GitHub pubbliche per stelle/fork.
Le cartelle temporanee sono state cancellate al termine della verifica
(vedi ultima sezione).

Nessun file di questo repository e' stato modificato: questo documento e'
l'unico output prodotto.

## Tabella riassuntiva

| # | Repo | Esiste | Licenza | Riusabilita' (0-5) | Cosa prendere |
|---|------|--------|---------|---------------------|----------------|
| 1 | [edubu2/hotel-revman-system](https://github.com/edubu2/hotel-revman-system) | Si' | **Assente** (nessun file LICENSE) → riuso codice non permesso | 2/5 | Solo l'*idea* (non il codice): pickup a T-5/T-15/T-30 e allineamento STLY sul giorno della settimana, da riscrivere da zero |
| 2 | [sgino209/hotels_rms](https://github.com/sgino209/hotels_rms) | Si' | **Apache 2.0** (presente) | 1/5 | Nulla di concreto: tutto basato su ARIMA/Prophet/TensorFlow/XGBoost, nessuna logica pickup/STLY da estrarre |
| 3 | [robert-robison/hotel-booking-demand](https://github.com/robert-robison/hotel-booking-demand) | Si' | **Assente** (nessun file LICENSE) → riuso codice non permesso | 3/5 | Solo l'*idea* (non il codice): le 4 feature "stesso giorno/settimana/2 settimane anno scorso + giorno settimana" e il baseline "media mese anno scorso", da riscrivere in stdlib |
| 4 | [nomanjaffar1/-Forecasting-Future-Bookings-and-Dynamics-Hotel-Price-Optimization](https://github.com/nomanjaffar1/-Forecasting-Future-Bookings-and-Dynamics-Hotel-Price-Optimization) | Si' (nome repo con trattino iniziale) | **Assente** | 1/5 | Nulla di concreto: codice minimale, con un bug di naming (ADR/lead time), demo Q-learning con dati simulati casuali (non reali) |
| 5 | Dataset "Hotel Booking Demand" (Antonio, de Almeida, Nunes) | Si', tuttora scaricabile | **CC BY 4.0** (attribuzione richiesta) | — (dataset, non codice) | Eventualmente utile per tarare i parametri realistici di `genera_demo.py` (distribuzioni di anticipo, tasso di cancellazione), citando la fonte |

**Legenda riusabilita':** 0 = nulla di utile: 5 = codice direttamente
riusabile. Con licenza assente il punteggio non supera mai 3/5, perche'
al massimo si puo' portare via l'*idea* (algoritmi/formule non sono
coperti da copyright, l'espressione concreta del codice si'), mai il
codice stesso.

---

## 1. github.com/edubu2/hotel-revman-system

**(a) Esistenza e attivita'**
- Esiste, pubblico. Clonato con successo (`git clone --depth 1`).
- Stelle: 13 — Fork: 6 (da pagina GitHub).
- Ultimo commit sul branch principale: **2021-04-15** (`dadda36` - "Add
  preds variable to fix dem_mod_an_h1").
- Linguaggio dichiarato: Jupyter Notebook 97% (il resto sono script `.py`
  di supporto in `code/`).
- Autore: Elliot Wilens, ex revenue manager Marriott, progetto finale di
  un bootcamp di data science (Metis).

**(b) Licenza**
- **Nessun file LICENSE** nella root ne' altrove nel repo.
- Conseguenza: il codice resta sotto copyright pieno dell'autore per
  default (GitHub "no license" = tutti i diritti riservati). **Non e'
  permesso copiare, ridistribuire o adattare il codice**, nemmeno per uso
  interno in un progetto diverso, senza chiedere permesso all'autore.

**(c) Ispezione del codice**
Struttura in `code/`: `agg.py`, `agg_utils.py`, `dbds.py`, `sim.py`,
`demand.py`, `model_cancellations.py`, `model_tools.py`, oltre a vari
notebook Jupyter per esplorazione/selezione modello.

E' il repo piu' ricco dei quattro sul fronte "pickup / booking curve":
- **Pickup a finestre fisse (T-30/T-15/T-5)**: `code/agg.py`, funzione
  `add_tminus` (righe ~188-214), calcola per ciascuna combinazione
  data-soggiorno/data-osservazione le differenze `RoomsOTB - TM{n}_RoomsOTB`
  (camere sul groupware ora meno camere sul groupware n giorni prima) per
  n=5,15,30, sia sul totale che per segmento di canale (`TRN_`, `TRNP_`).
- **STLY (Same-Time-Last-Year) allineato al giorno della settimana**:
  `code/dbds.py` riga ~295-298 e `code/sim.py` righe ~92-97:
  ```python
  stly_lambda = lambda x: pd.to_datetime(x) + relativedelta(
      years=-1, weekday=pd.to_datetime(x).weekday()
  )
  df["STLY_Date"] = df.index.map(stly_lambda)
  ```
  Usa `dateutil.relativedelta(years=-1, weekday=...)` per trovare, un anno
  prima, la data piu' vicina che cade sullo **stesso giorno della
  settimana** (non semplicemente "-365 giorni"). E' un dettaglio
  importante: -365 giorni sposta il giorno della settimana di 1 (o 2 negli
  anni bisestili), il che falsa il confronto per un'attivita' con forte
  stagionalita' settimanale (fine settimana vs feriali).
- **Confronto "pace"**: `code/agg_utils.py` (`gap_tuples`) e
  `code/agg.py` `add_gaps` (riga ~215-220): calcola lo scarto tra il
  venduto-alla-stessa-data-di-osservazione dell'anno scorso (STLY) e
  l'attuale sul groupware (`LYA - TY_OTB`), cioe' "siamo avanti o indietro
  rispetto a un anno fa, misurato allo stesso punto della curva di
  prenotazione".
- Le vere e proprie **previsioni di domanda/cancellazione sono modelli
  ML** (`RandomForestRegressor` in `demand.py`, `XGBClassifier` in
  `model_cancellations.py`): non portabili in un progetto solo-stdlib.

**(d) Giudizio di riusabilita' per il nostro progetto**
- Il codice concreto **non e' riusabile** (nessuna licenza).
- Le **idee** sono pero' di alta qualita' e direttamente applicabili al
  nostro `report_mattina.py`, riscritte da zero in Python stdlib:
  1. **STLY allineato al giorno della settimana** invece di un semplice
     `-365 giorni`: miglioria concreta rispetto alla nostra
     `confronto_anno_precedente()` attuale, che oggi usa `-365 giorni`
     fissi (`report_mattina.py`, funzione `confronto_anno_precedente`).
  2. **Pickup a piu' finestre** (non solo 7 giorni come facciamo ora, ma
     anche a 15/30 giorni) per distinguere un rallentamento recente da un
     trend piu' strutturale.

**(e) Pseudocodice portabile (stdlib, non copiato)**
```python
# STLY allineato al giorno della settimana, solo con datetime stdlib:
def data_stly_allineata(data):
    un_anno_fa = data.replace(year=data.year - 1)
    # sposta indietro fino a trovare lo stesso giorno della settimana
    delta = (un_anno_fa.weekday() - data.weekday()) % 7
    return un_anno_fa - timedelta(days=delta)
```
```
# Pickup multi-finestra (generalizzazione della funzione pickup gia'
# presente in report_mattina.py, oggi fissa a 7 giorni):
per ogni finestra in (7, 15, 30):
    snapshot_vecchio = ultimo_snapshot(target_date, snapshot_date <= oggi - finestra)
    pickup[finestra] = snapshot_vecchio.disponibili - snapshot_attuale.disponibili
```

---

## 2. github.com/sgino209/hotels_rms

**(a) Esistenza e attivita'**
- Esiste, pubblico. Clonato con successo.
- Stelle: 1 — Fork: 0.
- Ultimo commit: **2022-11-22** (merge PR dependabot su TensorFlow 2.9.3).
- Linguaggio dichiarato: Jupyter Notebook 100% (3 notebook, uno dei quali
  da 50 MB).

**(b) Licenza**
- **Apache License 2.0**, file `LICENSE` presente nella root (verificato
  aprendo il file: intestazione "Apache License, Version 2.0"). Il riuso
  del codice **e' permesso**, con obbligo di conservare l'avviso di
  copyright e la licenza nelle copie/derivati.

**(c) Ispezione del codice**
- Tre notebook (`nb0_arr_analysis.ipynb`, `nb0_arr_analysis_hotels_sweep.ipynb`,
  `nb0_ramblas_analysis.ipynb`) + `requirements.txt`.
- `requirements.txt` elenca: `tensorflow`, `xgboost`, `scikit-learn`,
  `statsmodels`, `pmdarima` (ARIMA), `fbprophet`, `shap`, `holidays`,
  `korean-lunar-calendar`/`LunarCalendar` (calendari festivi non
  gregoriani) — uno stack interamente orientato a modelli di serie
  storiche/ML pesanti.
- Nei notebook compaiono `ARIMA`, `prophet`, `day_of_week` come feature,
  ma **nessuna logica di pickup/OTB/STLY**: l'approccio e' prevedere
  direttamente gli arrivi con modelli statistici/ML, non costruire un
  motore a regole sul booking pace.

**(d) Giudizio di riusabilita'**
- Licenza permissiva, ma **contenuto non applicabile**: e' pensato per un
  ambiente con TensorFlow/Prophet/XGBoost, l'opposto del vincolo "solo
  libreria standard" del nostro progetto. Non c'e' una singola funzione
  isolabile e riscrivibile in poche righe di stdlib.
- L'unica idea generica (non codice, non specifica di questo repo) e'
  "usare un calendario di festivita' come segnale di domanda": concetto
  che il nostro progetto **implementa gia'** tramite la tabella
  `external_signal` e `genera_demo.py::MODELLI_SEGNALI_FISSI/CALCOLATI`.

**(e) Snippet portabili**
Nessuno: non c'e' nulla di specifico a un pattern pickup/STLY da estrarre
da questo repo.

---

## 3. github.com/robert-robison/hotel-booking-demand

**(a) Esistenza e attivita'**
- Esiste, pubblico. Clonato con successo.
- Stelle: 0 — Fork: 0.
- Ultimo commit: **2022-08-23** ("update link").
- Linguaggio dichiarato: Jupyter Notebook 100% (2 notebook:
  `hotel_cleaning.ipynb`, `hotel_modeling.ipynb`).
- Contesto: piccolo "datathon" interno di 3 ore presso Elder Research, sul
  dataset pubblico Hotel Booking Demand (vedi punto 5).

**(b) Licenza**
- **Nessun file LICENSE**. Riuso del codice **non permesso**.

**(c) Ispezione del codice**
Questo e' il repo **piu' pertinente** alla richiesta specifica del task
(feature "stesso giorno/settimana/2 settimane anno precedente" +
giorno-settimana), descritto chiaramente anche nel `README.md`:

> Feature Engineering: [...] 1. Vacancies last year, same day.
> 2. Vacancies last year, week centered on same day.
> 3. Vacancies last year, 2-week period centered on same day.
> 4. Day of week.

Nel notebook `notebooks/hotel_modeling.ipynb`:
- **Cella 9** (baseline "Method 1: The Mean"): predice ogni giorno di
  agosto 2017 con la **media semplice** dei valori dello stesso mese
  (agosto 2016) un anno prima — nessun modello, solo una media.
- **Cella 12**: calcola medie mobili trailing a 7 e 15 giorni con
  `df_[col].rolling(window).mean()`.
- **Cella 14**: crea le feature "anno scorso" traslando (`shift`) le
  medie mobili di 365 (valore secco), 362 (finestra 7 → 365-3, per
  centrare la finestra di 7 giorni sul giorno di un anno fa) e 358
  (finestra 15 → 365-7, per centrare la finestra di 15 giorni).
- **Cella 15**: il target modellato e' la "vacancy" (capienza massima
  meno venduto), non il venduto diretto — stessa logica di
  `units_available` che usiamo gia' noi.
- Il modello finale (LightGBM, non stdlib) e' usato solo per confrontare
  le 4 feature con un modello piu' sofisticato del semplice baseline
  "media"; il README stesso nota che il baseline "media stesso periodo
  anno scorso" e' "a hard baseline to improve upon" (difficile da
  battere) — conferma indiretta che per un hotel piccolo un approccio
  basato su regole/medie e' gia' competitivo, senza bisogno di ML.

**(d) Giudizio di riusabilita'**
- Codice non riusabile (nessuna licenza), ma le **formule sono semplici,
  chiare e riscrivibili in poche righe di stdlib puro** (nessuna necessita'
  di pandas/numpy: bastano liste e `datetime`). E' il repo con il miglior
  rapporto valore/complessita' per il nostro caso d'uso.
- Il baseline "media dello stesso periodo dell'anno scorso" e' concettualmente
  vicino a quanto gia' facciamo in `report_mattina.py::confronto_anno_precedente`,
  ma le 3 varianti (giorno esatto / settimana centrata / due settimane
  centrate) sarebbero un arricchimento naturale per rendere il confronto
  meno sensibile al rumore di un singolo giorno.

**(e) Pseudocodice portabile (stdlib, non copiato — formule riscritte
da zero a partire dalla descrizione concettuale del README/notebook)**
```python
# Le 4 feature del repo #3, riscritte con solo datetime/statistics stdlib.
# "valore" e' una funzione che restituisce il dato storico (es. unita' vendute)
# per una data esatta.

from datetime import timedelta
from statistics import mean

def stesso_giorno_anno_scorso(valore, data):
    return valore(data - timedelta(days=365))

def media_settimana_centrata_anno_scorso(valore, data):
    centro = data - timedelta(days=365)
    giorni = [centro + timedelta(days=d) for d in range(-3, 4)]  # 7 giorni
    return mean(v for v in (valore(g) for g in giorni) if v is not None)

def media_2settimane_centrata_anno_scorso(valore, data):
    centro = data - timedelta(days=365)
    giorni = [centro + timedelta(days=d) for d in range(-7, 8)]  # 15 giorni
    return mean(v for v in (valore(g) for g in giorni) if v is not None)

def giorno_settimana(data):
    return data.weekday()  # 0=lunedi ... 6=domenica
```
```
# Baseline "media dello stesso mese/periodo dell'anno scorso" (Method 1
# del repo, il piu' semplice dei due approcci descritti):
previsione(ogni giorno futuro) = media(valori_reali(stesso_mese, anno_scorso))
```

---

## 4. github.com/nomanjaffar1/-Forecasting-Future-Bookings-and-Dynamics-Hotel-Price-Optimization

**(a) Esistenza e attivita'**
- Esiste, pubblico. Nome repo con **trattino iniziale** nel path (`/-Forecasting...`),
  come segnalato nel task come possibile imprecisione — il nome corretto
  completo e' `nomanjaffar1/-Forecasting-Future-Bookings-and-Dynamics-Hotel-Price-Optimization`.
  Clonato con successo.
- Stelle: 2 — Fork: 0.
- Ultimo commit: **2024-02-23** ("Create README.md").
- Linguaggio dichiarato: Python 100% (4 script `.py`, nessun notebook,
  nessun dataset CSV effettivamente incluso nel repo nonostante il README
  lo citi).

**(b) Licenza**
- **Nessun file LICENSE**. Riuso del codice **non permesso**.

**(c) Ispezione del codice**
4 file, 404 righe totali:
- `predicting_booking.py`: `RandomForestRegressor` / `GradientBoostingRegressor`
  di scikit-learn per prevedere prenotazioni e ricavi, con
  `train_test_split` **casuale** (riga 49) invece che basato sul tempo —
  errore metodologico per dati di serie storiche (rischio di "leakage"
  dal futuro al passato in fase di addestramento).
- `BookingRevenue.py`: LSTM con TensorFlow/Keras per lo stesso scopo.
- `Dynamic_PriceOptimization.py`: un agente Q-Learning giocattolo
  (tabellare, `numpy` puro) con 3 azioni discrete — **alza prezzo del
  10%, abbassa del 10%, lascia invariato** — ma allenato su una domanda
  **simulata casualmente** (`np.random.randint(1, 11)`, riga 40), non su
  dati reali: e' uno scheletro didattico, non un modello funzionante.
- `Performance_Analysis.py`: calcolo di KPI (ADR, lead time). **Bug
  rilevato**: la colonna `LengthOfStay` e' calcolata come
  `(StayDate - BookingDate).days` (riga 24), che e' in realta' il
  **lead time/anticipo di prenotazione**, non la durata del soggiorno;
  l'ADR calcolato di conseguenza (`Price / LengthOfStay`, riga 25) e'
  quindi concettualmente errato.

**(d) Giudizio di riusabilita'**
- Codice non riusabile (nessuna licenza) e comunque di qualita' bassa
  (bug di naming, validazione train/test scorretta per serie storiche,
  simulazione RL con dati casuali non reali).
- L'unico elemento di interesse concettuale (non codice) e' la scelta di
  **3 azioni discrete di prezzo** (+10% / -10% / invariato), che
  **coincide** con l'approccio che il nostro motore a regole gia' adotta
  in `report_mattina.py` (regola `aumento_prezzo` con `aumento_pct` da
  config) — conferma indiretta che uno schema di decisione a passi
  discreti e' una scelta di design comune e ragionevole, non serve
  Reinforcement Learning per ottenerlo.

**(e) Snippet portabili**
Nessuno di valore diretto: il codice non aggiunge nulla che il nostro
motore a regole non gia' faccia in modo piu' semplice e corretto.

---

## 5. Dataset "Hotel Booking Demand" (Antonio, de Almeida, Nunes)

**Fonte originale**: N. Antonio, A. de Almeida, L. Nunes, "Hotel booking
demand datasets", *Data in Brief*, Vol. 22, 2019, pp. 41-49,
DOI: `10.1016/j.dib.2018.11.126` (rivista open-access, articolo tipo
"data article"). Due dataset (H1 = resort hotel, H2 = city hotel), stessa
struttura a 31 colonne: 40.060 osservazioni H1 + 79.330 osservazioni H2 =
**119.390 righe totali**, prenotazioni con arrivo tra il 1 luglio 2015 e
il 31 agosto 2017, incluse sia le prenotazioni arrivate sia quelle
cancellate.

**Dove si scarica oggi**:
- Mirror piu' diffuso: Kaggle, dataset "Hotel booking demand" caricato da
  Jesse Mostipak (`kaggle.com/datasets/jessemostipak/hotel-booking-demand`) —
  pagina non accessibile direttamente dal nostro ambiente (risposta
  HTTP 403 alla verifica automatica, tipico comportamento anti-bot di
  Kaggle), confermata pero' raggiungibile e attiva tramite ricerca web.
- Esistono inoltre numerosi mirror/fork su GitHub (es.
  `aaqibqadeer/Hotel-booking-demand`) che ridistribuiscono lo stesso CSV.
- Il deposito dati originale collegato all'articolo e' su Mendeley Data.

**Licenza**: **CC BY 4.0** (Attribution 4.0 International) — sia il
Kaggle mirror sia l'articolo originale su *Data in Brief* risultano
etichettati con questa licenza secondo le fonti incrociate consultate.
CC BY 4.0 **permette il riuso, anche commerciale, con il solo obbligo di
citare la fonte** (autori, articolo, DOI).

> Nota metodologica: le pagine dirette di Kaggle, ScienceDirect e PMC
> hanno risposto HTTP 403 ai tentativi di lettura automatica in questo
> ambiente (blocco anti-bot), quindi la licenza e' stata confermata
> incrociando due ricerche web indipendenti concordanti anziche' leggendo
> il testo integrale della pagina. Prima di un uso concreto (es. citarlo
> pubblicamente, ridistribuire il CSV) si consiglia una verifica visiva
> diretta della pagina Kaggle/Mendeley.

**Rilevanza per il nostro progetto**: non lo importeremmo cosi' com'e'
(e' un CSV enorme con nomi di colonna e concetti specifici del dominio
hotel booking, mentre il nostro `genera_demo.py` genera dati sintetici
parametrici da `config.json`). E' pero' potenzialmente utile come
**riferimento per tarare i parametri di `generazione_demo` in
`config.json`** (es. la vera distribuzione dell'anticipo di prenotazione,
il vero tasso di cancellazione aggregato), citando la fonte se questi
numeri venissero effettivamente presi da li'. Al momento i parametri di
`genera_demo.py` sono stime plausibili non derivate da questo dataset.

---

## Verdetto finale

**Da portare nel nostro motore** (solo idee/formule riscritte in stdlib,
mai codice copiato — nessuno dei 4 repo ha una licenza che permetterebbe
comunque di copiare porzioni sostanziali con attribuzione minima, tranne
il repo #2 che pero' non ha nulla di pertinente):

1. **STLY allineato al giorno della settimana** (dal repo #1): sostituire
   il confronto anno-su-anno oggi basato su `-365 giorni` fissi in
   `report_mattina.py::confronto_anno_precedente` con un calcolo che trova
   la data di un anno fa **con lo stesso giorno della settimana**. Per un
   hotel dove il weekend si comporta diversamente dai feriali, questo
   riduce un bias sistematico nel confronto.
2. **Pickup su piu' finestre temporali** (dal repo #1): affiancare al
   pickup a 7 giorni gia' presente anche un pickup a 15 e 30 giorni, per
   distinguere rumore di breve periodo da un trend piu' strutturale.
3. **Feature "stesso giorno / settimana centrata / due settimane
   centrate" dell'anno scorso** (dal repo #3): utile soprattutto se in
   futuro si volesse rendere il confronto con l'anno precedente piu'
   robusto a singole date anomale (es. un giorno di maltempo che ha
   azzerato le prenotazioni un anno fa) usando una media locale invece del
   singolo giorno.
4. **Nessun modello di Machine Learning** dai repo esaminati e'
   applicabile: sono tutti basati su scikit-learn, XGBoost, TensorFlow,
   LightGBM, Prophet o ARIMA, in contrasto diretto con il vincolo "solo
   libreria standard" del progetto. Le uniche parti riusabili sono quelle
   a monte del modello (feature engineering su pickup/date), non i
   modelli stessi.

**Da ignorare**:
- Repo #2 (`sgino209/hotels_rms`): nonostante la licenza permissiva
  (Apache 2.0), non contiene nessuna logica pickup/STLY/booking-curve,
  solo modelli di serie storiche pesanti non applicabili.
- Repo #4 (`nomanjaffar1/...`): codice di qualita' bassa (bug, validazione
  train/test scorretta, simulazione RL su dati casuali non reali) e senza
  licenza; nessuno spunto concreto oltre alla conferma che uno schema a
  3 azioni di prezzo discrete (gia' presente nel nostro motore) e'
  ragionevole.
- Il dataset (#5) come importazione diretta: troppo specifico e
  ingombrante per il nostro generatore parametrico; eventualmente utile
  solo come riferimento numerico per tarare `config.json`, con citazione
  della fonte (CC BY 4.0 lo permetterebbe).

## Pulizia

Tutte le cartelle temporanee dei cloni (`test_clone_1` .. `test_clone_4`,
create sotto una directory temporanea **esterna a questo repository**)
sono state cancellate al termine della verifica. Nessun file di
`revenuemanager` e' stato toccato all'infuori della creazione di questo
stesso documento.
