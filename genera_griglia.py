#!/usr/bin/env python3
"""
genera_griglia.py
--------------------
Ossatura R7-R9 di docs/SPECIFICA_MOTORE.md: genera automaticamente la
griglia tariffaria dell'anno target (es. 2027) a partire dai dati
sorgente dell'anno di riferimento (es. 2025: occupazione e prezzo
osservati per la tipologia Classic), rendendo ripetibile via comando il
processo oggi manuale con cui sono state costruite le griglie v2
(griglia_2027_*_v2.csv). Obiettivo dichiarato: la ricalibrazione di
settembre-ottobre 2026 (griglia 2027 rifatta sui consuntivi 2026) deve
poter essere un rilancio di comando, non un lavoro manuale.

Pipeline a fasi separate e componibili (ogni fase e' una funzione pura,
testabile da sola, elencata nell'ordine in cui il main le applica):
  1. mappa_calendario / determina_data_sorgente - allinea sorgente<->target
  2. classifica_fascia            - A/B/C/D dall'occupazione sorgente
  3. decidi_delta_fascia          - delta% base di prezzo per la fascia
                                     (+ regola D/mediana del mese sorgente)
  4. applica_correzioni_evento    - correzioni per eventi/festivita' mobili
  5. applica_floor_ceiling        - corridoio min/max del mese sorgente
  6. estendi_tipologia            - rapporto moltiplicativo dal riferimento Classic
  7. applica_regola_scarsita      - niente sconto e maggiorazione dedicata
                                     sulle date critiche per le tipologie scarse
  8. applica_override             - ultima fase: eccezioni puntuali da file esplicito

Tutti i parametri (soglie fasce, delta per fascia, rapporti tipologie,
correzioni evento, corridoio floor/ceiling, regola scarsita', regola di
arrotondamento) sono letti da un file di configurazione dedicato
(default: griglia_config.json), MAI scritti nel codice: ogni chiave ha un
campo "_nota" che dichiara se e' un dato di partenza fornito a mano o un
valore derivato e verificato contro le griglie v2 esistenti (dettaglio
completo della verifica in docs/DIVERGENZE_SPECIFICA.md).

Input atteso (CSV, tollerante come importa_log_disponibilita.py:
separatore rilevato automaticamente, date IT/ISO, numeri con virgola o
punto): una riga per data TARGET (non sorgente) della tipologia di
riferimento (default: Classic), gia' con la mappatura calendario decisa
(colonne: data, occupazione_pct, prezzo_eur). Questo e' esattamente lo
stesso formato delle colonne data/occ_2025_pct/prezzo_2025_eur di
griglia_2027_classic_roomonly_v2.csv, dove la mappatura calendario e' gia'
stata fatta una volta. La fase 1 (mappa_calendario/determina_data_sorgente)
e' comunque disponibile come funzione pura autonoma per chi deve ancora
decidere la mappatura partendo da un CSV sorgente non allineato (es. un
export grezzo di occupazione 2026): vedi le funzioni omonime piu' sotto.

Output: CSV nello schema C2 (9 colonne: data, giorno, tipologia, codice,
trattamento, prezzo_eur, fascia, unita_totali, unita_scarsa), lo stesso
delle griglie v2 esistenti e di docs/SPECIFICA_MOTORE.md.

Uso:
    python3 genera_griglia.py sorgente_classic_2025.csv --output griglia_2027.csv
    python3 genera_griglia.py sorgente_classic_2025.csv --output griglia_2027.csv \
        --griglia-config griglia_config.json --override griglia_override.csv
"""

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from core.config import carica_config
from importa_log_disponibilita import (
    normalizza_intestazione,
    parse_data_flessibile,
    parse_numero_flessibile,
    rileva_separatore,
)

NOMI_GIORNI_BREVI_IT = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]

COLONNE_OUTPUT_C2 = ["data", "giorno", "tipologia", "codice", "trattamento", "prezzo_eur", "fascia", "unita_totali", "unita_scarsa"]

COLONNE_SORGENTE_OBBLIGATORIE = ["data", "occupazione_pct", "prezzo_eur"]
COLONNA_SORGENTE_DATA_ORIGINALE = "data_sorgente"  # facoltativa, vedi carica_sorgente

COLONNE_OVERRIDE_OBBLIGATORIE = ["data", "tipologia", "prezzo_forzato", "motivo"]


def carica_griglia_config(percorso):
    testo = Path(percorso).read_text(encoding="utf-8")
    import json
    return json.loads(testo)


# ---------------------------------------------------------------------------
# Fase 1: mapping calendario sorgente <-> target (T3 di docs/SPECIFICA_MOTORE.md)
# ---------------------------------------------------------------------------

def mappa_calendario(data_sorgente, offset_giorni):
    """Data target = data sorgente + offset_giorni (MAI -365: l'offset e'
    un numero esplicito di giorni, dato di partenza in griglia_config.json,
    verificato contro le griglie v2 esistenti). Funzione pura, un solo
    verso (sorgente -> target)."""
    return data_sorgente + timedelta(days=offset_giorni)


def determina_data_sorgente(data_target, offset_giorni, date_sorgente_disponibili):
    """Verso inverso di mappa_calendario, usato quando si parte da un
    elenco di date TARGET necessarie e si deve trovare per ciascuna la
    rilevazione sorgente da usare. Se la data sorgente "naturale"
    (data_target - offset_giorni) non e' tra quelle disponibili (es. il
    CSV sorgente non arriva cosi' indietro), usa un proxy: la data
    disponibile piu' vicina con lo stesso giorno della settimana, e lo
    segnala (e_proxy=True) cosi' il chiamante puo' applicare
    proxy_data_sorgente.correzione_pct e documentarlo in motivazione.
    Restituisce (data_sorgente_da_usare, e_proxy) oppure (None, False) se
    non esiste nessuna data con lo stesso giorno della settimana."""
    naturale = data_target - timedelta(days=offset_giorni)
    if naturale in date_sorgente_disponibili:
        return naturale, False

    candidate = [d for d in date_sorgente_disponibili if d.weekday() == naturale.weekday()]
    if not candidate:
        return None, False
    piu_vicina = min(candidate, key=lambda d: abs((d - naturale).days))
    return piu_vicina, True


# ---------------------------------------------------------------------------
# Fase 2: classificazione in fascia A/B/C/D dall'occupazione sorgente
# ---------------------------------------------------------------------------

def classifica_fascia(occupazione_pct, soglie_pct):
    if occupazione_pct >= soglie_pct["A"]:
        return "A"
    if occupazione_pct >= soglie_pct["B"]:
        return "B"
    if occupazione_pct >= soglie_pct["C"]:
        return "C"
    return "D"


# ---------------------------------------------------------------------------
# Fase 3: delta% di prezzo base per la fascia (+ regola D/mediana del mese)
# ---------------------------------------------------------------------------

def decidi_delta_fascia(fascia, prezzo_sorgente, mediana_mese_sorgente, delta_config):
    """Delta% BASE per la fascia, valido per una tipologia NON scarsa
    (la fase 7 la corregge per le tipologie scarse). In fascia D il prezzo
    resta invariato (0%) a meno che il prezzo sorgente non sia sopra la
    mediana di TUTTI i prezzi sorgente del mese sorgente (qualunque
    fascia): in quel caso scatta lo sconto D_sopra_mediana_mese (-5% di
    default). Restituisce (delta_pct, etichetta) per la motivazione."""
    if fascia == "D":
        if mediana_mese_sorgente is not None and prezzo_sorgente > mediana_mese_sorgente:
            return delta_config["D_sopra_mediana_mese"], "debole sovraprezzata"
        return delta_config["D"], "debole"
    if fascia == "A":
        return delta_config["A"], "esaurita"
    if fascia == "B":
        return delta_config["B"], "forte"
    return delta_config["C"], "sana"


# ---------------------------------------------------------------------------
# Fase 4: correzioni evento / festivita' mobili
# ---------------------------------------------------------------------------

def applica_correzioni_evento(data_target_iso, prezzo, correzioni_eventi):
    """Applica, se la data target rientra in una regola, la correzione
    percentuale MOLTIPLICATIVAMENTE sopra il prezzo gia' calcolato dalle
    fasi precedenti (verificato: e' cosi' che si combina con l'eventuale
    sconto 'sopra mediana' di fascia D nelle griglie v2 esistenti, non
    additivamente). Restituisce (prezzo_corretto, descrizione_o_None)."""
    for regola in correzioni_eventi:
        if data_target_iso in regola["date_target"]:
            return prezzo * (1 + regola["correzione_pct"] / 100), regola["descrizione"]
    return prezzo, None


# ---------------------------------------------------------------------------
# Fase 5: corridoio floor/ceiling sul mese sorgente (R8)
# ---------------------------------------------------------------------------

def applica_floor_ceiling(prezzo, prezzi_sorgente_mese, floor_moltiplicatore, ceiling_moltiplicatore):
    """Nessun prezzo di griglia fuori dal corridoio [min*floor, max*ceiling]
    dei prezzi sorgente del MESE SORGENTE (non del mese target). Se la
    lista dei prezzi del mese e' vuota (dato mancante) non applica nessun
    corridoio, per non inventare un limite (P5)."""
    if not prezzi_sorgente_mese:
        return prezzo, False
    floor = min(prezzi_sorgente_mese) * floor_moltiplicatore
    ceiling = max(prezzi_sorgente_mese) * ceiling_moltiplicatore
    if prezzo < floor:
        return floor, True
    if prezzo > ceiling:
        return ceiling, True
    return prezzo, False


# ---------------------------------------------------------------------------
# Fase 6: estensione alle altre tipologie (rapporto moltiplicativo da Classic)
# ---------------------------------------------------------------------------

def estendi_tipologia(prezzo_sorgente_riferimento, rapporto):
    """Prezzo sorgente equivalente per una tipologia diversa dal
    riferimento (Classic): SEMPRE un rapporto moltiplicativo, mai un
    supplemento fisso in euro (docs/SPECIFICA_MOTORE.md, R9)."""
    return prezzo_sorgente_riferimento * rapporto


# ---------------------------------------------------------------------------
# Fase 7: regola scarsita' (tipologie con unita' totali <= soglia)
# ---------------------------------------------------------------------------

def applica_regola_scarsita(fascia, delta_base_pct, tipologia_scarsa, delta_config, scarsita_config):
    """Corregge il delta% BASE (fase 3) per le tipologie scarse: mai uno
    sconto in fascia D (il prezzo resta al livello 'debole' anche se
    sarebbe sopra mediana), maggiorazione dedicata (di norma piu' alta di
    quella base) in fascia A. Nessuna correzione in fascia B/C: verificato
    che le tipologie scarse seguono li' lo stesso delta delle altre."""
    if not tipologia_scarsa:
        return delta_base_pct
    if fascia == "A":
        return scarsita_config["maggiorazione_fascia_A_pct"]
    if fascia == "D" and scarsita_config.get("mai_sconto_fascia_D") and delta_base_pct < 0:
        return delta_config["D"]
    return delta_base_pct


# ---------------------------------------------------------------------------
# Arrotondamento finale (parametro scoperto in fase di verifica, vedi
# griglia_config.json:arrotondamento)
# ---------------------------------------------------------------------------

def arrotonda_prezzo(prezzo_grezzo):
    k = round((prezzo_grezzo - 4) / 5)
    return int(k * 5 + 4)


# ---------------------------------------------------------------------------
# Fase 8 (ultima): override esplicito da file
# ---------------------------------------------------------------------------

def carica_override(percorso):
    """Legge il CSV di override (data, tipologia, prezzo_forzato, motivo),
    tollerante come gli altri importer. Restituisce un dizionario
    {(data_iso, codice_tipologia): (prezzo_forzato, motivo)}. Se il file
    non esiste restituisce un dizionario vuoto (l'override e' facoltativo:
    P5, non inventare un file che non c'e')."""
    cammino = Path(percorso)
    if not cammino.exists():
        return {}

    testo = cammino.read_text(encoding="utf-8-sig")
    righe_grezze = testo.splitlines()
    if not righe_grezze:
        return {}

    separatore = rileva_separatore(righe_grezze[0])
    lettore = csv.reader(righe_grezze, delimiter=separatore)
    intestazione = [normalizza_intestazione(c) for c in next(lettore)]
    mancanti = [c for c in COLONNE_OVERRIDE_OBBLIGATORIE if c not in intestazione]
    if mancanti:
        raise ValueError(f"al file di override '{percorso}' mancano le colonne: {', '.join(mancanti)}")

    risultato = {}
    for valori in lettore:
        if not valori or all(not (v or "").strip() for v in valori):
            continue
        riga = {intestazione[i]: (valori[i] if i < len(valori) else "") for i in range(len(intestazione))}
        if normalizza_intestazione(riga.get("data", "")) == "data":
            continue
        data_riga = parse_data_flessibile(riga["data"])
        codice = riga["tipologia"].strip().upper()
        prezzo = parse_numero_flessibile(riga["prezzo_forzato"])
        motivo = riga.get("motivo", "").strip()
        risultato[(data_riga.strftime("%Y-%m-%d"), codice)] = (prezzo, motivo)
    return risultato


def applica_override(data_iso, codice, prezzo, override):
    """Se esiste un override per questa combinazione (data, tipologia),
    sovrascrive il prezzo e restituisce anche il motivo dichiarato, per
    poterlo mostrare in motivazione invece di nasconderlo."""
    chiave = (data_iso, codice)
    if chiave in override:
        prezzo_forzato, motivo = override[chiave]
        return int(prezzo_forzato), motivo
    return prezzo, None


# ---------------------------------------------------------------------------
# Caricamento del CSV sorgente (tollerante)
# ---------------------------------------------------------------------------

def carica_sorgente(percorso):
    """Legge il CSV sorgente: 'data' (TARGET, gia' mappata), occupazione_pct,
    prezzo_eur. Se e' presente anche una colonna 'data_sorgente' (FACOLTATIVA:
    la data di osservazione originale, anno di riferimento), viene letta e
    usata solo per decidere se questa riga ha avuto una mappatura calendario
    "naturale" (data - offset_giorni) o non-naturale (proxy per range
    esaurito, o correzione evento su una data curata a mano come Pasqua):
    serve alla fase 4/proxy per non applicare due correzioni sulla stessa
    riga per errore. Se la colonna non c'e', nessun controllo di questo
    tipo viene fatto (si assume che la mappatura sia gia' stata decisa)."""
    cammino = Path(percorso)
    if not cammino.exists():
        raise FileNotFoundError(f"il file '{percorso}' non esiste")

    testo = cammino.read_text(encoding="utf-8-sig")
    righe_grezze = testo.splitlines()
    if not righe_grezze:
        raise ValueError(f"il file '{percorso}' e' vuoto")

    separatore = rileva_separatore(righe_grezze[0])
    lettore = csv.reader(righe_grezze, delimiter=separatore)
    intestazione = [normalizza_intestazione(c) for c in next(lettore)]
    mancanti = [c for c in COLONNE_SORGENTE_OBBLIGATORIE if c not in intestazione]
    if mancanti:
        raise ValueError(
            f"al file sorgente '{percorso}' mancano le colonne obbligatorie: {', '.join(mancanti)} "
            f"(intestazione trovata: {', '.join(intestazione)})"
        )
    ha_data_sorgente = COLONNA_SORGENTE_DATA_ORIGINALE in intestazione

    righe_valide, righe_scartate = [], []
    numero_riga = 1
    for valori in lettore:
        numero_riga += 1
        if not valori or all(not (v or "").strip() for v in valori):
            continue
        riga = {intestazione[i]: (valori[i] if i < len(valori) else "") for i in range(len(intestazione))}
        if normalizza_intestazione(riga.get("data", "")) == "data":
            continue
        try:
            data_riga = parse_data_flessibile(riga["data"])
            occ = parse_numero_flessibile(riga["occupazione_pct"])
            prezzo = parse_numero_flessibile(riga["prezzo_eur"])
            if occ is None or prezzo is None:
                raise ValueError("occupazione_pct o prezzo_eur mancante")
            data_sorgente_riga = None
            if ha_data_sorgente and (riga.get(COLONNA_SORGENTE_DATA_ORIGINALE) or "").strip():
                data_sorgente_riga = parse_data_flessibile(riga[COLONNA_SORGENTE_DATA_ORIGINALE])
        except ValueError as exc:
            righe_scartate.append((numero_riga, str(exc)))
            continue
        righe_valide.append({
            "data": data_riga, "occupazione_pct": occ, "prezzo_eur": prezzo, "data_sorgente": data_sorgente_riga,
        })

    return righe_valide, righe_scartate


# ---------------------------------------------------------------------------
# Pipeline completa per una riga (una data, una tipologia)
# ---------------------------------------------------------------------------

def calcola_prezzo_tipologia(data_target, occupazione_pct, prezzo_sorgente_riferimento, mediana_mese_riferimento,
                              prezzi_sorgente_mese_riferimento, codice_tipologia, rapporto, unita_totali,
                              griglia_config, override, e_proxy=False):
    """Esegue le fasi 2-8 per UNA tipologia in UNA data, a partire dai
    dati sorgente della tipologia di RIFERIMENTO (di norma Classic).
    Restituisce un dizionario pronto per una riga di output C2, con
    'motivazione' come traccia leggibile di ogni fase applicata."""
    soglia_scarsa = griglia_config["scarsita"]["soglia_unita_totali"]
    tipologia_scarsa = unita_totali is not None and unita_totali <= soglia_scarsa

    fascia = classifica_fascia(occupazione_pct, griglia_config["fasce_occupazione"]["soglie_pct"])

    # fase 3: delta base sul prezzo sorgente DI RIFERIMENTO (Classic), non
    # su quello gia' esteso alla tipologia: la mediana del mese e' la
    # stessa curva per tutte le tipologie (proporzionale), verificato
    # equivalente applicarla prima o dopo il rapporto.
    delta_base_pct, etichetta = decidi_delta_fascia(
        fascia, prezzo_sorgente_riferimento, mediana_mese_riferimento, griglia_config["delta_prezzo_per_fascia_pct"]
    )

    # fase 7: correzione scarsita' (agisce sul delta, prima di estendere)
    delta_pct = applica_regola_scarsita(
        fascia, delta_base_pct, tipologia_scarsa, griglia_config["delta_prezzo_per_fascia_pct"], griglia_config["scarsita"]
    )
    if tipologia_scarsa and delta_pct != delta_base_pct:
        etichetta = "esaurita, tipologia scarsa" if fascia == "A" else etichetta + " (tipologia scarsa: nessuno sconto)"

    prezzo_con_delta_riferimento = prezzo_sorgente_riferimento * (1 + delta_pct / 100)

    # fase 6: estensione alla tipologia (rapporto moltiplicativo)
    prezzo = estendi_tipologia(prezzo_con_delta_riferimento, rapporto)

    # fase 4: correzioni evento (moltiplicativa, sopra il prezzo gia' esteso)
    data_iso = data_target.strftime("%Y-%m-%d")
    prezzo, descrizione_evento = applica_correzioni_evento(data_iso, prezzo, griglia_config["correzioni_eventi"])

    # fase 1 (coda): correzione per data sorgente "proxy" (mappatura non
    # naturale, es. range dei dati sorgente esaurito). Non si applica se la
    # data e' gia' coperta da una correzione evento (es. Pasqua, che e' una
    # mappatura curata a mano, non un proxy generico): evita di sommare le
    # due correzioni sulla stessa riga.
    if e_proxy and descrizione_evento is None:
        prezzo *= (1 + griglia_config["proxy_data_sorgente"]["correzione_pct"] / 100)
        descrizione_evento = "data sorgente proxy (range dati sorgente esaurito)"

    # fase 5: floor/ceiling sul corridoio del mese sorgente, scalato con lo
    # stesso rapporto della tipologia (equivalente al corridoio di Classic)
    prezzi_mese_scalati = [p * rapporto for p in prezzi_sorgente_mese_riferimento]
    fc = griglia_config["micro_stagioni_floor_ceiling"]
    prezzo, clampato = applica_floor_ceiling(prezzo, prezzi_mese_scalati, fc["floor_moltiplicatore"], fc["ceiling_moltiplicatore"])

    prezzo_finale = arrotonda_prezzo(prezzo)

    motivazione = f"occ {occupazione_pct:.1f}% -> fascia {fascia} ({etichetta}, {delta_pct:+.1f}%)"
    if descrizione_evento:
        motivazione += f"; evento: {descrizione_evento}"
    if clampato:
        motivazione += "; limitato dal corridoio floor/ceiling del mese sorgente"

    # fase 8: override, ultima parola
    prezzo_finale, motivo_override = applica_override(data_iso, codice_tipologia, prezzo_finale, override)
    if motivo_override:
        motivazione += f"; OVERRIDE: {motivo_override}"

    return {
        "fascia": fascia,
        "prezzo_eur": prezzo_finale,
        "motivazione": motivazione,
        "unita_scarsa": tipologia_scarsa,
    }


# ---------------------------------------------------------------------------
# Generazione della griglia completa
# ---------------------------------------------------------------------------

def genera_griglia(righe_sorgente, config_struttura, griglia_config, override, tipologia_riferimento="CLA", trattamento=None):
    """righe_sorgente: lista di {'data', 'occupazione_pct', 'prezzo_eur'}
    della tipologia di riferimento (gia' con la mappatura calendario
    fatta: 'data' e' la data TARGET). Restituisce la lista di righe C2
    (una per data x tipologia) e la lista dei motivi/motivazioni."""
    trattamento = trattamento or griglia_config.get("trattamento_default", "Room Only")
    capacity_units = {u["code"]: u for u in config_struttura["capacity_units"]}
    if tipologia_riferimento not in capacity_units:
        raise ValueError(f"tipologia di riferimento '{tipologia_riferimento}' non presente in config.json capacity_units")

    rapporti = griglia_config["rapporti_tipologie"]
    offset_giorni = griglia_config["mapping_calendario"]["offset_giorni"]
    date_sorgente_note = [r["data_sorgente"] for r in righe_sorgente if r.get("data_sorgente") is not None]
    prima_data_sorgente = min(date_sorgente_note) if date_sorgente_note else None

    prezzi_per_mese = defaultdict(list)
    for r in righe_sorgente:
        mese = r["data"].strftime("%Y-%m")
        prezzi_per_mese[mese].append(r["prezzo_eur"])
    mediane_mese = {m: _mediana(v) for m, v in prezzi_per_mese.items()}

    righe_output = []
    for r in righe_sorgente:
        mese = r["data"].strftime("%Y-%m")
        giorno = NOMI_GIORNI_BREVI_IT[r["data"].weekday()]

        # e_proxy si applica SOLO quando la data sorgente naturale (target -
        # offset) cade prima dell'inizio dei dati sorgente disponibili: e'
        # la condizione verificabile, oggettiva, che nella griglia v2
        # esistente distingue un vero proxy (03-24, 03-25: naturale prima
        # dell'1/4) da un semplice riuso di dati vicini per fine mese
        # mancante (10-30, 10-31: naturale disponibile, solo che il
        # preparatore ha scelto una data leggermente diversa senza bisogno
        # di nessuna correzione aggiuntiva).
        e_proxy = False
        if r.get("data_sorgente") is not None and prima_data_sorgente is not None:
            naturale = r["data"] - timedelta(days=offset_giorni)
            e_proxy = naturale < prima_data_sorgente

        for codice, unita in capacity_units.items():
            if codice not in rapporti:
                continue  # nessun rapporto configurato per questa tipologia: non estesa
            rapporto = rapporti[codice]

            esito = calcola_prezzo_tipologia(
                data_target=r["data"],
                occupazione_pct=r["occupazione_pct"],
                prezzo_sorgente_riferimento=r["prezzo_eur"],
                mediana_mese_riferimento=mediane_mese.get(mese),
                prezzi_sorgente_mese_riferimento=prezzi_per_mese.get(mese, []),
                codice_tipologia=codice,
                rapporto=rapporto,
                unita_totali=unita["total_units"],
                griglia_config=griglia_config,
                override=override,
                e_proxy=e_proxy,
            )

            righe_output.append({
                "data": r["data"].strftime("%Y-%m-%d"),
                "giorno": giorno,
                "tipologia": unita["name"],
                "codice": codice,
                "trattamento": trattamento,
                "prezzo_eur": esito["prezzo_eur"],
                "fascia": esito["fascia"],
                "unita_totali": unita["total_units"],
                "unita_scarsa": 1 if esito["unita_scarsa"] else 0,
                "motivazione": esito["motivazione"],
            })

    return righe_output


def _mediana(valori):
    valori_ordinati = sorted(valori)
    n = len(valori_ordinati)
    meta = n // 2
    if n % 2 == 1:
        return valori_ordinati[meta]
    return (valori_ordinati[meta - 1] + valori_ordinati[meta]) / 2


def scrivi_csv_output(percorso, righe, con_motivazione=True):
    colonne = COLONNE_OUTPUT_C2 + (["motivazione"] if con_motivazione else [])
    with open(percorso, "w", newline="", encoding="utf-8") as f:
        scrittore = csv.writer(f)
        scrittore.writerow(colonne)
        for r in sorted(righe, key=lambda r: (r["data"], r["codice"])):
            riga = [r[c] for c in colonne]
            scrittore.writerow(riga)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Genera automaticamente la griglia tariffaria dell'anno target (ossatura R7-R9).")
    parser.add_argument("sorgente", help="CSV sorgente della tipologia di riferimento: data (target), occupazione_pct, prezzo_eur")
    parser.add_argument("--config", default="config.json", help="config.json della struttura (default: config.json)")
    parser.add_argument("--griglia-config", default="griglia_config.json", help="parametri della griglia (default: griglia_config.json)")
    parser.add_argument("--override", default=None, help="CSV di override (default: file_override_default di griglia_config.json, se esiste)")
    parser.add_argument("--tipologia-riferimento", default="CLA", help="codice della tipologia di riferimento nel CSV sorgente (default: CLA)")
    parser.add_argument("--output", default="griglia_generata.csv", help="CSV di output, schema C2 (default: griglia_generata.csv)")
    parser.add_argument("--senza-motivazione", action="store_true", help="non includere la colonna 'motivazione' nell'output (schema C2 stretto)")
    args = parser.parse_args(argv)

    config_struttura = carica_config(args.config)
    try:
        griglia_config = carica_griglia_config(args.griglia_config)
    except OSError as exc:
        print(f"[ERRORE] impossibile leggere '{args.griglia_config}': {exc}", file=sys.stderr)
        return 1

    try:
        righe_sorgente, scartate = carica_sorgente(args.sorgente)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERRORE] {exc}", file=sys.stderr)
        return 1

    print(f"Righe sorgente valide: {len(righe_sorgente)}, scartate: {len(scartate)}")
    for numero, motivo in scartate[:15]:
        print(f"  - riga {numero}: {motivo}")

    percorso_override = args.override or griglia_config.get("file_override_default")
    override = carica_override(percorso_override) if percorso_override else {}
    if override:
        print(f"Override caricato: {len(override)} eccezioni da '{percorso_override}'")

    righe_output = genera_griglia(righe_sorgente, config_struttura, griglia_config, override, args.tipologia_riferimento)
    scrivi_csv_output(args.output, righe_output, con_motivazione=not args.senza_motivazione)

    print(f"Griglia generata: {len(righe_output)} righe ({len(righe_sorgente)} date x tipologie) -> {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
