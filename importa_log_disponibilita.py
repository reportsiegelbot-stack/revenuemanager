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

        try:
            data_rilevazione, data_target, capacity_unit_id, camere_libere, prezzo = elabora_riga(
                riga, numero_riga, mappa_per_codice, mappa_per_nome, unita_per_id
            )
        except ValueError as exc:
            scartate.append((numero_riga, str(exc)))
            continue

        esito = upsert_snapshot(
            conn,
            formatta_data(data_rilevazione),
            formatta_data(data_target),
            capacity_unit_id,
            canale,
            camere_libere,
            prezzo,
        )
        if esito == "importata":
            importate += 1
        else:
            aggiornate += 1

        chiave_rilevazione = formatta_data(data_rilevazione)
        somma_camere_per_rilevazione[chiave_rilevazione] += camere_libere
        conteggio_per_rilevazione[chiave_rilevazione] += 1

    conn.commit()
    conn.close()

    print()
    print("=== Riepilogo importazione log disponibilita' ===")
    print(f"File: {args.csv_path}  (separatore rilevato: '{separatore}', canale: {canale})")
    print(f"Righe importate (nuove): {importate}")
    print(f"Righe aggiornate (gia' presenti, sovrascritte): {aggiornate}")
    print(f"Righe scartate: {len(scartate)}")
    if righe_vuote_ignorate:
        print(f"Righe vuote ignorate: {righe_vuote_ignorate}")
    if intestazioni_ripetute_ignorate:
        print(f"Intestazioni ripetute ignorate: {intestazioni_ripetute_ignorate}")

    if scartate:
        print()
        print("Dettaglio righe scartate (riga del CSV, motivo):")
        for numero, motivo in scartate[:30]:
            print(f"  - riga {numero}: {motivo}")
        if len(scartate) > 30:
            print(f"  ... e altre {len(scartate) - 30} righe scartate.")

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
