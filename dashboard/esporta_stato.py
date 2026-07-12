#!/usr/bin/env python3
"""
dashboard/esporta_stato.py
-----------------------------
Livello "contratto" dell'architettura a strati del progetto:

    dati (SQLite) -> motore (regole, report_mattina.py) -> QUESTO SCRIPT -> JSON -> interfaccia (dashboard.html)

Legge il database e produce dashboard/dati.json: un file statico che
descrive per intero lo stato attuale del sistema. dashboard.html (e
qualunque altra interfaccia si costruisca in futuro: webapp, app mobile,
un altro report) legge SOLO questo JSON, MAI il database direttamente.
Cosi' il motore (le regole in report_mattina.py) puo' evolvere senza che
l'interfaccia debba cambiare, finche' la forma del JSON resta la stessa
(schema_version la traccia).

Per non duplicare la logica del pickup, delle anomalie e della situazione
(che vive in report_mattina.py ed e' gia' testata li'), questo script la
IMPORTA direttamente invece di riscriverla. Le uniche funzioni nuove sono
in core/calcoli.py (la serie storica completa per i grafici, che
report_mattina.py non aveva bisogno di esporre) e in questo file (lettura
della griglia tariffaria 2027 e assemblaggio del JSON finale).

Oltre a dati.json, genera anche dashboard_standalone.html: la stessa
pagina di dashboard.html ma con i dati incorporati direttamente nel file,
cosi' funziona con un doppio click anche nei browser che bloccano la
lettura di file JSON locali via fetch() (restrizione CORS su file://).

Uso:
    python3 dashboard/esporta_stato.py
    python3 dashboard/esporta_stato.py --db ../vault.db --config ../config.json
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

# dashboard/ e' una sottocartella: per riusare i moduli della root (core,
# report_mattina, importa_log_disponibilita) senza duplicarli va aggiunta
# esplicitamente la cartella principale del progetto al path di ricerca.
RADICE_PROGETTO = Path(__file__).resolve().parent.parent
if str(RADICE_PROGETTO) not in sys.path:
    sys.path.insert(0, str(RADICE_PROGETTO))

import report_mattina  # noqa: E402  (import dopo la modifica di sys.path, necessario)
from core import calcoli, db  # noqa: E402
from core.config import carica_config  # noqa: E402
from core.utils import formatta_data, oggi  # noqa: E402
from importa_log_disponibilita import (  # noqa: E402
    normalizza_intestazione,
    parse_data_flessibile,
    parse_numero_flessibile,
    rileva_separatore,
)

SCHEMA_VERSION = "1.1"
NOME_FILE_JSON_DEFAULT = "dati.json"
NOME_FILE_STANDALONE_DEFAULT = "dashboard_standalone.html"
NOME_TEMPLATE_HTML = "dashboard.html"
NOME_GRIGLIA_2027_DEFAULT = "griglia_2027_tutte_tipologie.csv"
# Colonne attese nel CSV della griglia 2027 (v1.1, allineate al template
# dashboard.html "quadro del mattino"): "giorno" e' un'etichetta libera
# (es. il giorno della settimana) mostrata cosi' com'e', non validata.
COLONNE_GRIGLIA_2027 = ["data", "giorno", "codice", "tipologia", "prezzo_eur", "fascia"]

# Il template dashboard.html incorpora i dati sostituendo ESATTAMENTE questa
# stringa (marcatore vuoto) con /*__DATI_INIZIO__*/ <json> /*__DATI_FINE__*/.
MARCATORE_INIZIO = "/*__DATI_INIZIO__*/"
MARCATORE_FINE = "/*__DATI_FINE__*/"
MARCATORE_VUOTO = MARCATORE_INIZIO + " null " + MARCATORE_FINE


# ---------------------------------------------------------------------------
# meta: data di generazione, nome struttura, prossima fase stagionale
# ---------------------------------------------------------------------------

def _costruisci_meta(config, oggi_data):
    return {
        # timestamp ISO di quando e' stato generato il JSON, per capire
        # a colpo d'occhio se i dati mostrati sono aggiornati
        "generato_il": datetime.now().isoformat(timespec="seconds"),
        "nome_struttura": config["structure_name"],
        # non abbiamo un calendario di apertura/chiusura della struttura
        # (config.json non lo modella): usiamo come proxy la prossima
        # transizione di stagione tariffaria (vedi core/calcoli.py)
        "prossimo_cambio_stagione": calcoli.prossimo_cambio_stagione(config, oggi_data),
    }


# ---------------------------------------------------------------------------
# situazione: per ogni data/tipologia con almeno una rilevazione nota
# ---------------------------------------------------------------------------

def _serializza_situazione(situazione):
    voci = []
    for voce in situazione:
        if voce["disponibili"] is None:
            continue  # nessuna rilevazione ancora per questa data/tipologia
        voci.append({
            "data_target": formatta_data(voce["data"]),
            "tipologia_codice": voce["unita_code"],
            "tipologia_nome": voce["unita_name"],
            "camere_libere": voce["disponibili"],
            "camere_totali": voce["unita_totale"],
            "disponibilita_pct": voce["disponibilita_pct"],
            "prezzo_del_giorno": voce["prezzo"],
        })
    return voci


# ---------------------------------------------------------------------------
# pickup: stessa logica del report (funzione importata, non riscritta)
# ---------------------------------------------------------------------------

def _serializza_pickup(pickup_dettagliato):
    voci = []
    for riga in pickup_dettagliato:
        confronto_stly = riga["confronto_stly"]
        voci.append({
            "data_target": formatta_data(riga["data_target"]),
            "tipologia_codice": riga["unita_code"],
            "tipologia_nome": riga["unita_name"],
            "camere_totali": riga["unita_totale"],
            "ultima_rilevazione": formatta_data(riga["data_ultima_rilevazione"]),
            "n_rilevazioni": riga["n_rilevazioni"],
            "camere_libere": riga["disponibili"],
            "disponibilita_pct": riga["disponibilita_pct"],
            "pickup_ultima_rilevazione": riga["pickup_ultimo"],
            "pickup_7_giorni": riga["pickup_7gg"],
            "giorni_dallarrivo": riga["giorni_out"],
            # confronto anno precedente SEMPRE on-the-books contro
            # on-the-books allo stesso anticipo: None = storico non
            # disponibile (mai sostituito con l'occupazione consuntiva)
            "confronto_anno_precedente": None if confronto_stly is None else {
                "camere_libere": confronto_stly["disponibili"],
                "disponibilita_pct": confronto_stly["disponibilita_pct"],
                "differenza_punti_percentuali": confronto_stly["differenza_pct"],
            },
        })
    return voci


# ---------------------------------------------------------------------------
# booking_curve: serie storica completa, per i grafici della dashboard
# ---------------------------------------------------------------------------

def _costruisci_booking_curve(conn, situazione, canale, oggi_data):
    curve = []
    for voce in situazione:
        if voce["disponibili"] is None:
            continue
        serie = calcoli.serie_rilevazioni(conn, voce["unita_id"], canale, voce["data"], oggi_data)
        if len(serie) < 2:
            continue  # una sola rilevazione non fa una curva da graficare
        curve.append({
            "data_target": formatta_data(voce["data"]),
            "tipologia_codice": voce["unita_code"],
            "tipologia_nome": voce["unita_name"],
            "serie": serie,
        })
    return curve


# ---------------------------------------------------------------------------
# griglia_2027: griglia tariffaria caricata da CSV esterno, se presente
# ---------------------------------------------------------------------------

def _carica_griglia_2027(percorso):
    cammino = Path(percorso)
    if not cammino.exists():
        return {
            "disponibile": False,
            "nota": f"File '{percorso}' non trovato nella root del progetto: sezione vuota.",
            "voci": [],
        }

    testo = cammino.read_text(encoding="utf-8-sig")
    righe_grezze = testo.splitlines()
    if not righe_grezze:
        return {"disponibile": False, "nota": f"File '{percorso}' e' vuoto.", "voci": []}

    separatore = rileva_separatore(righe_grezze[0])
    lettore = csv.reader(righe_grezze, delimiter=separatore)
    intestazione = [normalizza_intestazione(c) for c in next(lettore)]
    mancanti = [c for c in COLONNE_GRIGLIA_2027 if c not in intestazione]
    if mancanti:
        return {
            "disponibile": False,
            "nota": f"Intestazione non valida in '{percorso}': mancano le colonne {', '.join(mancanti)}.",
            "voci": [],
        }

    voci = []
    scartate = 0
    for valori in lettore:
        if not valori or all(not (v or "").strip() for v in valori):
            continue
        riga = {intestazione[i]: (valori[i] if i < len(valori) else "") for i in range(len(intestazione))}
        try:
            data_riga = parse_data_flessibile(riga["data"])
            prezzo = parse_numero_flessibile(riga["prezzo_eur"])
            fascia = (riga["fascia"] or "").strip().upper()
            codice = (riga["codice"] or "").strip().upper()
            tipologia = (riga["tipologia"] or "").strip()
            if prezzo is None or not fascia or not codice or not tipologia:
                raise ValueError("campo mancante")
        except ValueError:
            scartate += 1
            continue
        # "giorno" e' solo un'etichetta descrittiva (es. giorno della
        # settimana): non e' obbligatoria riga per riga, una cella vuota
        # non fa scartare la voce.
        giorno = (riga.get("giorno") or "").strip()
        voci.append({
            "data": formatta_data(data_riga),
            "giorno": giorno,
            "codice": codice,
            "tipologia": tipologia,
            "prezzo_eur": prezzo,
            "fascia": fascia,
        })

    nota = f"{len(voci)} voci caricate da '{percorso}'."
    if scartate:
        nota += f" {scartate} righe scartate per dati mancanti o non validi."
    return {"disponibile": True, "nota": nota, "voci": voci}


# ---------------------------------------------------------------------------
# decisioni: ultime N righe della tabella decision, con motivazione a livelli
# ---------------------------------------------------------------------------

def _carica_decisioni(conn, capacity_units, limite=20):
    mappa_unita = {unita["id"]: unita for unita in capacity_units}
    righe = conn.execute(
        """
        SELECT id, created_ts, target_date, capacity_unit_id, decision_type,
               suggestion, reasoning, data_snapshot_json, outcome
        FROM decision
        ORDER BY created_ts DESC, id DESC
        LIMIT ?
        """,
        (limite,),
    ).fetchall()

    decisioni = []
    for riga in righe:
        livelli = []
        if riga["data_snapshot_json"]:
            try:
                extra = json.loads(riga["data_snapshot_json"])
                livelli = extra.get("livelli", [])
            except json.JSONDecodeError:
                livelli = []
        unita = mappa_unita.get(riga["capacity_unit_id"])
        decisioni.append({
            "id": riga["id"],
            "creato_il": riga["created_ts"],
            "data_target": riga["target_date"],
            "tipologia_codice": unita["code"] if unita else None,
            "tipologia_nome": unita["name"] if unita else None,
            "tipo": riga["decision_type"],
            "suggerimento": riga["suggestion"],
            "motivazione_sintesi": riga["reasoning"],
            "motivazione_livelli": livelli,
            "esito": riga["outcome"],
        })
    return decisioni


# ---------------------------------------------------------------------------
# alert: anomalie correnti (stessa logica del report, funzione importata)
# ---------------------------------------------------------------------------

def _serializza_alert(anomalie):
    voci = []
    for anomalia in anomalie:
        voce = anomalia["voce"]
        voci.append({
            "data_target": formatta_data(voce["data"]),
            "tipologia_codice": voce["unita_code"],
            "tipologia_nome": voce["unita_name"],
            "motivo": anomalia["motivo"],
            "dettaglio": anomalia["dettaglio"],
        })
    return voci


# ---------------------------------------------------------------------------
# assemblaggio + scrittura file
# ---------------------------------------------------------------------------

def costruisci_stato(conn, config, capacity_units, oggi_data, orizzonte_giorni, canale, percorso_griglia_2027):
    situazione = report_mattina.costruisci_situazione(conn, capacity_units, oggi_data, orizzonte_giorni)
    pickup = report_mattina.costruisci_pickup_dettagliato(conn, capacity_units, oggi_data, orizzonte_giorni, canale)
    anomalie = report_mattina.rileva_anomalie(situazione, orizzonte_giorni)

    return {
        "schema_version": SCHEMA_VERSION,
        "meta": _costruisci_meta(config, oggi_data),
        "situazione": _serializza_situazione(situazione),
        "pickup": _serializza_pickup(pickup),
        "booking_curve": _costruisci_booking_curve(conn, situazione, canale, oggi_data),
        "griglia_2027": _carica_griglia_2027(percorso_griglia_2027),
        "decisioni": _carica_decisioni(conn, capacity_units),
        "alert": _serializza_alert(anomalie),
    }


def genera_standalone(cartella_dashboard, dati, nome_output):
    """Produce dashboard_standalone.html: il template dashboard.html con i
    dati incorporati al posto del marcatore vuoto
    "/*__DATI_INIZIO__*/ null /*__DATI_FINE__*/", cosi' la pagina funziona
    con un doppio click anche senza server locale e anche nei browser che
    bloccano la lettura di file JSON esterni via fetch() da file://.

    Se il marcatore non c'e' (il template e' cambiato in modo
    incompatibile) la funzione FALLISCE esplicitamente (eccezione): non
    deve mai produrre in silenzio una pagina senza dati incorporati."""
    percorso_template = cartella_dashboard / NOME_TEMPLATE_HTML
    if not percorso_template.exists():
        raise FileNotFoundError(f"'{percorso_template}' non trovato: impossibile generare la pagina standalone.")

    template = percorso_template.read_text(encoding="utf-8")
    if MARCATORE_VUOTO not in template:
        raise ValueError(
            f"il marcatore '{MARCATORE_VUOTO}' non e' presente in '{percorso_template.name}': "
            "il template e' cambiato in modo incompatibile con questo script, non genero una pagina rotta."
        )

    blocco_dati = MARCATORE_INIZIO + " " + json.dumps(dati, ensure_ascii=False) + " " + MARCATORE_FINE
    pagina_standalone = template.replace(MARCATORE_VUOTO, blocco_dati)

    # Verifica esplicita che la sostituzione sia andata a buon fine (e non,
    # es., che sia rimasto un secondo marcatore vuoto invariato altrove).
    assert blocco_dati in pagina_standalone, "la sostituzione dei dati incorporati non e' andata a buon fine"
    assert MARCATORE_VUOTO not in pagina_standalone, "il marcatore vuoto e' ancora presente dopo la sostituzione"

    percorso_output = cartella_dashboard / nome_output
    percorso_output.write_text(pagina_standalone, encoding="utf-8")
    return percorso_output


def main(argv=None):
    cartella_dashboard = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description="Esporta lo stato del sistema in dashboard/dati.json.")
    parser.add_argument("--db", default=str(RADICE_PROGETTO / db.NOME_DB_DEFAULT), help="percorso del database (default: vault.db nella root)")
    parser.add_argument("--config", default=str(RADICE_PROGETTO / "config.json"), help="percorso di config.json (default: nella root)")
    parser.add_argument("--output", default=str(cartella_dashboard / NOME_FILE_JSON_DEFAULT), help="percorso del JSON da generare")
    parser.add_argument("--giorni", type=int, default=report_mattina.ORIZZONTE_GIORNI_DEFAULT, help="orizzonte in giorni (default: 30, come il report)")
    parser.add_argument(
        "--griglia-2027",
        default=str(RADICE_PROGETTO / NOME_GRIGLIA_2027_DEFAULT),
        help=f"percorso del CSV della griglia 2027 (default: {NOME_GRIGLIA_2027_DEFAULT} nella root, se presente)",
    )
    parser.add_argument("--no-standalone", action="store_true", help="non generare dashboard_standalone.html")
    args = parser.parse_args(argv)

    if not db.db_esiste(args.db):
        print(f"[ERRORE] il database '{args.db}' non esiste ancora. Esegui prima 'python3 init_db.py'.", file=sys.stderr)
        return 1

    config = carica_config(args.config)
    conn = db.connetti(args.db)
    capacity_units = [dict(riga) for riga in db.elenco_capacity_units(conn)]
    if not capacity_units:
        print("[ERRORE] il database non contiene ancora tipologie. Esegui prima 'python3 init_db.py'.", file=sys.stderr)
        return 1

    canale = config["channels"][0]
    oggi_data = oggi()

    print("Lettura database ed esportazione dello stato...")
    dati = costruisci_stato(conn, config, capacity_units, oggi_data, args.giorni, canale, args.griglia_2027)
    conn.close()

    percorso_output = Path(args.output)
    percorso_output.parent.mkdir(parents=True, exist_ok=True)
    percorso_output.write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"JSON generato: {percorso_output.resolve()}")
    print(f"  - situazione: {len(dati['situazione'])} voci")
    print(f"  - pickup: {len(dati['pickup'])} voci")
    print(f"  - booking_curve: {len(dati['booking_curve'])} serie")
    print(f"  - griglia_2027: {dati['griglia_2027']['nota']}")
    print(f"  - decisioni: {len(dati['decisioni'])} voci")
    print(f"  - alert: {len(dati['alert'])} voci")

    if not args.no_standalone:
        try:
            percorso_standalone = genera_standalone(cartella_dashboard, dati, NOME_FILE_STANDALONE_DEFAULT)
        except (FileNotFoundError, ValueError, AssertionError) as exc:
            # Fallimento esplicito e visibile: niente pagina standalone
            # vuota o rotta prodotta in silenzio.
            print(f"[ERRORE] generazione di '{NOME_FILE_STANDALONE_DEFAULT}' fallita: {exc}", file=sys.stderr)
            return 1
        print(f"Pagina autonoma generata: {percorso_standalone.resolve()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
