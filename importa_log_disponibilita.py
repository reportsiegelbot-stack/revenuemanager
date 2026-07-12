#!/usr/bin/env python3
"""
importa_log_disponibilita.py
------------------------------
Importa il log di disponibilita' compilato a mano su un foglio Google
(esportato periodicamente in CSV) nella tabella inventory_snapshot.

Colonne attese nel CSV (una riga per rilevazione):
    data_rilevazione, data_target, tipologia, camere_libere,
    prezzo_del_giorno, note

- Le date possono essere in formato italiano (GG/MM/AAAA) o ISO (AAAA-MM-GG).
- I numeri possono usare la virgola o il punto come separatore decimale.
- Il file puo' contenere righe vuote, intestazioni ripetute (es. se e' stato
  incollato piu' volte lo stesso blocco) e refusi: queste vengono segnalate,
  non scartate in silenzio.
- La colonna "note" viene letta ma non salvata: lo schema del database
  (tabella inventory_snapshot) non prevede un campo apposito e questo script
  non puo' modificarlo.

Chiave di un rilevamento: (data_rilevazione, data_target, tipologia). Se nel
file (o in un'importazione successiva dello stesso export) compare piu' di
una volta la stessa chiave, l'ultima letta sovrascrive la precedente
(aggiorna la riga in inventory_snapshot invece di duplicarla).

Uso:
    python3 importa_log_disponibilita.py log_disponibilita.csv
    python3 importa_log_disponibilita.py log_disponibilita.csv --canale booking_engine
"""

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from core import db
from core.config import carica_config
from core.utils import formatta_data

COLONNE_OBBLIGATORIE = ["data_rilevazione", "data_target", "tipologia", "camere_libere"]
COLONNE_TUTTE = COLONNE_OBBLIGATORIE + ["prezzo_del_giorno", "note"]

FORMATI_DATA = ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"]

# Soglie di default per la coerenza temporale (P6), usate solo se
# 'import_checks' non e' presente in config.json (compatibilita' con
# config.json piu' vecchi, senza dover forzare l'aggiornamento).
SALTO_CAMERE_LIBERE_MAX_DEFAULT = 2
VARIAZIONE_PREZZO_PCT_MAX_DEFAULT = 30


def normalizza_intestazione(testo):
    """Uniforma il nome di una colonna: minuscolo, senza spazi ai lati,
    spazi interni trasformati in underscore (cosi' 'Data Rilevazione' e
    'data_rilevazione' sono la stessa cosa)."""
    return testo.strip().lower().replace(" ", "_")


def rileva_separatore(riga_intestazione):
    """Indovina il separatore del CSV contando i candidati piu' comuni
    nella riga di intestazione: i fogli Google in locale italiano spesso
    esportano con il punto e virgola invece della virgola."""
    candidati = [",", ";", "\t"]
    conteggi = {c: riga_intestazione.count(c) for c in candidati}
    migliore = max(conteggi, key=conteggi.get)
    return migliore if conteggi[migliore] > 0 else ","


def parse_data_flessibile(testo):
    """Converte una data in formato italiano o ISO. Solleva ValueError con
    un messaggio comprensibile se non riconosce il formato."""
    testo = (testo or "").strip()
    if not testo:
        raise ValueError("data mancante")
    # scarta un eventuale orario incollato insieme alla data (es. "11/07/2026 10:30")
    testo = testo.split()[0]
    for formato in FORMATI_DATA:
        try:
            return datetime.strptime(testo, formato).date()
        except ValueError:
            continue
    raise ValueError(f"formato data non riconosciuto: '{testo}'")


def parse_numero_flessibile(testo):
    """Converte un numero che puo' usare la virgola o il punto come
    separatore decimale (ed eventualmente l'altro come separatore delle
    migliaia). Restituisce None se il campo e' vuoto (numero opzionale)."""
    testo = (testo or "").strip()
    if not testo:
        return None
    testo = testo.replace(" ", "")
    if "," in testo and "." in testo:
        # entrambi presenti: formato italiano, punto = migliaia, virgola = decimali
        testo = testo.replace(".", "").replace(",", ".")
    elif "," in testo:
        # solo la virgola: e' il separatore decimale in stile italiano
        testo = testo.replace(",", ".")
    # altrimenti (solo punto, o nessun separatore): gia' in formato valido
    try:
        return float(testo)
    except ValueError:
        raise ValueError(f"numero non valido: '{testo}'")


def trova_capacity_unit(testo, mappa_per_codice, mappa_per_nome):
    """Cerca la tipologia sia per codice (es. 'CLA') sia per nome per
    esteso (es. 'Classic'), senza distinguere maiuscole/minuscole: il
    foglio compilato a mano potrebbe usare l'uno o l'altro."""
    chiave = (testo or "").strip()
    if not chiave:
        return None
    return mappa_per_codice.get(chiave.upper()) or mappa_per_nome.get(chiave.lower())


def e_riga_vuota(riga):
    return all(not (valore or "").strip() for valore in riga.values())


def e_intestazione_ripetuta(riga):
    """Rileva un blocco di intestazioni incollato di nuovo in mezzo ai
    dati: succede se il file e' stato costruito incollando piu' export
    uno dopo l'altro."""
    valore = normalizza_intestazione(riga.get("data_rilevazione") or "")
    return valore == "data_rilevazione"


def calcola_hash_file(percorso):
    """Calcola l'hash SHA-256 del contenuto grezzo del file (bytes), per
    riconoscere un re-import identico a uno gia' registrato in
    import_audit (P6: parte del checksum sui dati trascritti)."""
    return hashlib.sha256(percorso.read_bytes()).hexdigest()


def verifica_coerenza_temporale(conn, target_date_str, capacity_unit_id, channel, data_rilevazione_str, camere_libere, prezzo, soglia_salto_camere, soglia_variazione_prezzo_pct):
    """Confronta la rilevazione appena letta con quella immediatamente
    precedente (stessa data_target, tipologia, canale) gia' presente nel
    database, e segnala eventuali variazioni anomale. Non blocca mai
    l'importazione (P6: 'checksum fallito = righe sospette elencate, dato
    usato come indicativo, mai come esatto'): decide sempre l'umano.
    Va chiamata PRIMA dell'upsert della riga corrente, altrimenti la
    'precedente' sarebbe la riga stessa appena scritta."""
    precedente = conn.execute(
        """
        SELECT snapshot_date, units_available, price_published
        FROM inventory_snapshot
        WHERE target_date = ? AND capacity_unit_id = ? AND channel = ? AND snapshot_date < ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        (target_date_str, capacity_unit_id, channel, data_rilevazione_str),
    ).fetchone()

    if precedente is None:
        return []

    avvisi = []

    delta_camere = camere_libere - precedente["units_available"]
    if delta_camere > soglia_salto_camere:
        avvisi.append(
            f"camere_libere aumentata da {precedente['units_available']} a {camere_libere} "
            f"(+{delta_camere}) rispetto alla rilevazione del {precedente['snapshot_date']}: "
            "possibile cancellazione o errore di battitura, verificare"
        )

    prezzo_precedente = precedente["price_published"]
    if prezzo is not None and prezzo_precedente:
        variazione_pct = abs(prezzo - prezzo_precedente) / prezzo_precedente * 100
        if variazione_pct > soglia_variazione_prezzo_pct:
            avvisi.append(
                f"prezzo variato da {prezzo_precedente:g} a {prezzo:g} EUR "
                f"({variazione_pct:.0f}%) rispetto alla rilevazione del {precedente['snapshot_date']}: "
                "verificare che non sia un errore di battitura"
            )

    return avvisi


def upsert_snapshot(conn, snapshot_date, target_date, capacity_unit_id, channel, units_available, price_published):
    """Inserisce una nuova rilevazione, o aggiorna quella gia' presente per
    la stessa chiave (data_rilevazione, data_target, tipologia, canale).
    Restituisce 'importata' o 'aggiornata'."""
    riga_esistente = conn.execute(
        """
        SELECT id FROM inventory_snapshot
        WHERE snapshot_date = ? AND target_date = ? AND capacity_unit_id = ? AND channel = ?
        """,
        (snapshot_date, target_date, capacity_unit_id, channel),
    ).fetchone()

    if riga_esistente is not None:
        conn.execute(
            "UPDATE inventory_snapshot SET units_available = ?, price_published = ? WHERE id = ?",
            (units_available, price_published, riga_esistente["id"]),
        )
        return "aggiornata"

    conn.execute(
        """
        INSERT INTO inventory_snapshot
            (snapshot_date, target_date, capacity_unit_id, units_available, price_published, channel)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (snapshot_date, target_date, capacity_unit_id, units_available, price_published, channel),
    )
    return "importata"


def elabora_riga(riga, numero_riga, mappa_per_codice, mappa_per_nome, unita_per_id):
    """Valida e converte una riga del CSV. Restituisce una tupla pronta per
    upsert_snapshot (senza canale), oppure solleva ValueError con il
    motivo dello scarto."""
    try:
        data_rilevazione = parse_data_flessibile(riga.get("data_rilevazione"))
    except ValueError as exc:
        raise ValueError(f"data_rilevazione non valida: {exc}")

    try:
        data_target = parse_data_flessibile(riga.get("data_target"))
    except ValueError as exc:
        raise ValueError(f"data_target non valida: {exc}")

    if data_target < data_rilevazione:
        raise ValueError(
            f"data_target ({formatta_data(data_target)}) precedente a data_rilevazione "
            f"({formatta_data(data_rilevazione)}): non ha senso rilevare oggi la disponibilita' di una data gia' passata"
        )

    unita = trova_capacity_unit(riga.get("tipologia"), mappa_per_codice, mappa_per_nome)
    if unita is None:
        raise ValueError(f"tipologia sconosciuta: '{riga.get('tipologia')}'")

    testo_camere = riga.get("camere_libere")
    try:
        camere_libere = parse_numero_flessibile(testo_camere)
    except ValueError as exc:
        raise ValueError(str(exc))
    if camere_libere is None:
        raise ValueError("camere_libere mancante")
    if camere_libere != int(camere_libere):
        raise ValueError(f"camere_libere non e' un numero intero: '{testo_camere}'")
    camere_libere = int(camere_libere)
    if camere_libere < 0:
        raise ValueError(f"camere_libere negativo: {camere_libere}")
    if camere_libere > unita["total_units"]:
        raise ValueError(
            f"camere_libere ({camere_libere}) superiore alle unita' totali della tipologia "
            f"'{unita['code']}' ({unita['total_units']})"
        )

    try:
        prezzo = parse_numero_flessibile(riga.get("prezzo_del_giorno"))
    except ValueError as exc:
        raise ValueError(str(exc))
    if prezzo is not None and prezzo < 0:
        raise ValueError(f"prezzo_del_giorno negativo: {prezzo}")

    return data_rilevazione, data_target, unita["id"], camere_libere, prezzo


def main(argv=None):
    parser = argparse.ArgumentParser(description="Importa il log manuale di disponibilita' (CSV) in inventory_snapshot.")
    parser.add_argument("csv_path", help="percorso del CSV esportato dal foglio Google")
    parser.add_argument("--db", default=db.NOME_DB_DEFAULT, help="percorso del file database (default: vault.db)")
    parser.add_argument("--config", default="config.json", help="percorso del file di configurazione (default: config.json)")
    parser.add_argument("--canale", default=None, help="canale a cui associare le rilevazioni (default: il primo canale in config.json)")
    args = parser.parse_args(argv)

    percorso_csv = Path(args.csv_path)
    if not percorso_csv.exists():
        print(f"[ERRORE] il file CSV '{args.csv_path}' non esiste.", file=sys.stderr)
        return 1

    if not db.db_esiste(args.db):
        print(f"[ERRORE] il database '{args.db}' non esiste ancora. Esegui prima 'python3 init_db.py'.", file=sys.stderr)
        return 1

    config = carica_config(args.config)
    canale = args.canale or config["channels"][0]
    soglie_import = config.get("import_checks", {})
    soglia_salto_camere = soglie_import.get("salto_camere_libere_max", SALTO_CAMERE_LIBERE_MAX_DEFAULT)
    soglia_variazione_prezzo_pct = soglie_import.get("variazione_prezzo_pct_max", VARIAZIONE_PREZZO_PCT_MAX_DEFAULT)

    conn = db.connetti(args.db)
    unita_elenco = db.elenco_capacity_units(conn)
    if not unita_elenco:
        print("[ERRORE] il database non contiene ancora tipologie. Esegui prima 'python3 init_db.py'.", file=sys.stderr)
        return 1

    mappa_per_codice = {riga["code"].upper(): dict(riga) for riga in unita_elenco}
    mappa_per_nome = {riga["name"].strip().lower(): dict(riga) for riga in unita_elenco}
    unita_per_id = {riga["id"]: dict(riga) for riga in unita_elenco}

    try:
        testo_grezzo = percorso_csv.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"[ERRORE] impossibile leggere il file CSV: {exc}", file=sys.stderr)
        return 1

    # P6: hash del file per riconoscere un re-import identico a uno gia'
    # registrato in import_audit (non blocca l'importazione: l'upsert e'
    # comunque idempotente, e' solo un'informazione utile).
    hash_file = calcola_hash_file(percorso_csv)
    import_precedente_identico = conn.execute(
        "SELECT import_ts FROM import_audit WHERE file_sha256 = ? ORDER BY import_ts DESC LIMIT 1",
        (hash_file,),
    ).fetchone()

    righe_grezze = testo_grezzo.splitlines()
    if not righe_grezze:
        print("[ERRORE] il CSV e' vuoto.", file=sys.stderr)
        return 1

    separatore = rileva_separatore(righe_grezze[0])
    lettore = csv.reader(righe_grezze, delimiter=separatore)
    intestazione_grezza = next(lettore)
    intestazione = [normalizza_intestazione(c) for c in intestazione_grezza]

    mancanti = [c for c in COLONNE_OBBLIGATORIE if c not in intestazione]
    if mancanti:
        print(
            f"[ERRORE] al CSV mancano le colonne obbligatorie: {', '.join(mancanti)} "
            f"(intestazione trovata: {', '.join(intestazione)})",
            file=sys.stderr,
        )
        return 1

    importate = 0
    aggiornate = 0
    scartate = []
    righe_vuote_ignorate = 0
    intestazioni_ripetute_ignorate = 0
    somma_camere_per_rilevazione = defaultdict(int)
    conteggio_per_rilevazione = defaultdict(int)
    chiavi_viste = {}  # (data_rilevazione, data_target, capacity_unit_id) -> numero_riga della prima occorrenza
    duplicati = []  # (numero_riga, numero_riga_precedente, descrizione_chiave)
    warning = []  # (numero_riga, motivo)

    righe_lette = 0
    numero_riga = 1  # la riga 1 e' l'intestazione
    for valori in lettore:
        numero_riga += 1
        if not valori:
            righe_vuote_ignorate += 1
            continue
        # allinea i valori alle colonne note; celle mancanti/extra non
        # devono far esplodere lo script
        riga = {intestazione[i]: (valori[i] if i < len(valori) else "") for i in range(len(intestazione))}

        if e_riga_vuota(riga):
            righe_vuote_ignorate += 1
            continue
        if e_intestazione_ripetuta(riga):
            intestazioni_ripetute_ignorate += 1
            continue

        righe_lette += 1
        try:
            data_rilevazione, data_target, capacity_unit_id, camere_libere, prezzo = elabora_riga(
                riga, numero_riga, mappa_per_codice, mappa_per_nome, unita_per_id
            )
        except ValueError as exc:
            scartate.append((numero_riga, str(exc)))
            continue

        data_rilevazione_str = formatta_data(data_rilevazione)
        data_target_str = formatta_data(data_target)

        # P6 (a): coerenza interna - duplicati della stessa chiave dentro il
        # file. Non e' uno scarto: l'upsert esistente gestisce comunque la
        # chiave (l'ultima riga letta vince), qui la segnaliamo soltanto.
        chiave = (data_rilevazione_str, data_target_str, capacity_unit_id)
        if chiave in chiavi_viste:
            unita_codice = unita_per_id[capacity_unit_id]["code"]
            duplicati.append((
                numero_riga, chiavi_viste[chiave],
                f"data_rilevazione={data_rilevazione_str}, data_target={data_target_str}, tipologia={unita_codice}",
            ))
        chiavi_viste[chiave] = numero_riga

        # P6 (b): coerenza temporale - va calcolata PRIMA di scrivere la
        # riga corrente, altrimenti confronteremmo il dato con se stesso.
        avvisi_riga = verifica_coerenza_temporale(
            conn, data_target_str, capacity_unit_id, canale, data_rilevazione_str,
            camere_libere, prezzo, soglia_salto_camere, soglia_variazione_prezzo_pct,
        )
        if avvisi_riga:
            for avviso in avvisi_riga:
                warning.append((numero_riga, avviso))

        esito = upsert_snapshot(
            conn,
            data_rilevazione_str,
            data_target_str,
            capacity_unit_id,
            canale,
            camere_libere,
            prezzo,
        )
        if esito == "importata":
            importate += 1
        else:
            aggiornate += 1

        somma_camere_per_rilevazione[data_rilevazione_str] += camere_libere
        conteggio_per_rilevazione[data_rilevazione_str] += 1

    # P6 (c): registro di controllo dell'importazione.
    dettaglio_audit = {
        "scartate": [{"riga": n, "motivo": m} for n, m in scartate],
        "warning": [{"riga": n, "motivo": m} for n, m in warning],
        "duplicati": [
            {"riga": n, "prima_occorrenza_riga": n_prima, "chiave": descr}
            for n, n_prima, descr in duplicati
        ],
    }
    conn.execute(
        """
        INSERT INTO import_audit
            (import_ts, source_file, file_sha256, rows_read, rows_accepted, rows_rejected, rows_warning, detail_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            args.csv_path,
            hash_file,
            righe_lette,
            importate + aggiornate,
            len(scartate),
            len({n for n, _ in warning}),
            json.dumps(dettaglio_audit, ensure_ascii=False),
        ),
    )

    conn.commit()
    conn.close()

    print()
    print("=== Riepilogo importazione log disponibilita' ===")
    print(f"File: {args.csv_path}  (separatore rilevato: '{separatore}', canale: {canale})")
    if import_precedente_identico is not None:
        print(
            f"[NOTA] questo file e' identico (stesso contenuto) a un'importazione gia' registrata "
            f"il {import_precedente_identico['import_ts']}: nessun problema, l'importazione e' idempotente, "
            "e' solo un'informazione utile se non te lo aspettavi."
        )
    print(f"Righe lette: {righe_lette}")
    print(f"Righe importate (nuove): {importate}")
    print(f"Righe aggiornate (gia' presenti, sovrascritte): {aggiornate}")
    print(f"Righe scartate: {len(scartate)}")
    if righe_vuote_ignorate:
        print(f"Righe vuote ignorate: {righe_vuote_ignorate}")
    if intestazioni_ripetute_ignorate:
        print(f"Intestazioni ripetute ignorate: {intestazioni_ripetute_ignorate}")
    if duplicati:
        print(f"Righe duplicate nello stesso file (stessa data_rilevazione+data_target+tipologia): {len(duplicati)}")

    if scartate:
        print()
        print("Dettaglio righe scartate (riga del CSV, motivo):")
        for numero, motivo in scartate[:30]:
            print(f"  - riga {numero}: {motivo}")
        if len(scartate) > 30:
            print(f"  ... e altre {len(scartate) - 30} righe scartate.")

    if duplicati:
        print()
        print("Dettaglio righe duplicate (la piu' recente sovrascrive la precedente, come sempre):")
        for numero, numero_prima, descr in duplicati[:30]:
            print(f"  - riga {numero}: stessa chiave della riga {numero_prima} ({descr})")
        if len(duplicati) > 30:
            print(f"  ... e altre {len(duplicati) - 30} righe duplicate.")

    if warning:
        print()
        print(f"=== ATTENZIONE: {len({n for n, _ in warning})} righe con variazioni anomale (WARNING, non bloccanti) ===")
        print("Queste righe sono state importate comunque: verificale a occhio, potrebbero essere errori di battitura.")
        for numero, motivo in warning[:30]:
            print(f"  - riga {numero}: {motivo}")
        if len(warning) > 30:
            print(f"  ... e altri {len(warning) - 30} warning.")

    if somma_camere_per_rilevazione:
        print()
        print("Mini-checksum per data di rilevazione (confronta a occhio col foglio):")
        for data_rilevazione in sorted(somma_camere_per_rilevazione):
            somma = somma_camere_per_rilevazione[data_rilevazione]
            n = conteggio_per_rilevazione[data_rilevazione]
            print(f"  - {data_rilevazione}: somma camere_libere = {somma}  (su {n} rilevazioni)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
