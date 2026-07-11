#!/usr/bin/env python3
"""
importa_csv.py
----------------
Importa prenotazioni reali da un file CSV esterno, usando le regole di
corrispondenza definite in mapping.json (nome colonna del CSV -> campo
della tabella "booking"). Lo script non conosce in anticipo il formato
del CSV: legge SOLO quello che mapping.json gli dice di leggere.

Uso:
    python3 importa_csv.py prenotazioni.csv
    python3 importa_csv.py prenotazioni.csv --mapping mapping.json --db vault.db

Alla fine stampa un riepilogo con quante righe sono state importate e
quante scartate, con il motivo dello scarto.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from core import db
from core.utils import parse_data, formatta_data

NOME_MAPPING_DEFAULT = "mapping.json"

CAMPI_OBBLIGATORI = ["created_date", "arrival_date", "nights", "capacity_unit_code", "channel", "total_price", "status"]


def carica_mapping(percorso):
    cammino = Path(percorso)
    if not cammino.exists():
        print(f"[ERRORE] il file di mapping '{percorso}' non esiste.", file=sys.stderr)
        sys.exit(1)
    try:
        mapping = json.loads(cammino.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[ERRORE] '{percorso}' non e' un JSON valido: {exc.msg} (riga {exc.lineno})", file=sys.stderr)
        sys.exit(1)

    for chiave in ("separatore", "formato_data", "colonne"):
        if chiave not in mapping:
            print(f"[ERRORE] al file di mapping '{percorso}' manca la chiave '{chiave}'.", file=sys.stderr)
            sys.exit(1)

    colonne = mapping["colonne"]
    campi_mappati = set(colonne.values())
    mancanti = [c for c in CAMPI_OBBLIGATORI if c not in campi_mappati]
    if mancanti:
        print(
            f"[ERRORE] il mapping non copre tutti i campi obbligatori. Mancano: {', '.join(mancanti)}.",
            file=sys.stderr,
        )
        sys.exit(1)

    mapping.setdefault("valori_stato", {})
    return mapping


def valida_e_converti_riga(riga_csv, numero_riga, mapping, mappa_capacity_units):
    """Converte una riga del CSV (dizionario colonna->valore) in una tupla
    pronta per l'inserimento nella tabella booking, oppure solleva
    ValueError con un motivo di scarto comprensibile."""
    colonne = mapping["colonne"]

    valori = {}
    for colonna_sorgente, campo_destinazione in colonne.items():
        if colonna_sorgente not in riga_csv:
            raise ValueError(f"colonna '{colonna_sorgente}' non trovata nel CSV")
        valori[campo_destinazione] = (riga_csv[colonna_sorgente] or "").strip()

    try:
        data_creazione = parse_data(valori["created_date"], mapping["formato_data"])
    except ValueError as exc:
        raise ValueError(f"data prenotazione non valida: {exc}")

    try:
        data_arrivo = parse_data(valori["arrival_date"], mapping["formato_data"])
    except ValueError as exc:
        raise ValueError(f"data arrivo non valida: {exc}")

    try:
        notti = int(valori["nights"])
        if notti <= 0:
            raise ValueError
    except ValueError:
        raise ValueError(f"numero di notti non valido: '{valori['nights']}'")

    codice_unita = valori["capacity_unit_code"].upper()
    if codice_unita not in mappa_capacity_units:
        raise ValueError(f"tipologia sconosciuta: '{valori['capacity_unit_code']}'")

    canale = valori["channel"]
    if not canale:
        raise ValueError("canale mancante")

    try:
        prezzo = float(valori["total_price"].replace(",", "."))
        if prezzo < 0:
            raise ValueError
    except ValueError:
        raise ValueError(f"prezzo totale non valido: '{valori['total_price']}'")

    stato_originale = valori["status"].strip().lower()
    stato = mapping["valori_stato"].get(stato_originale) or mapping["valori_stato"].get(valori["status"].strip())
    if stato not in ("confirmed", "cancelled"):
        raise ValueError(f"stato non riconosciuto: '{valori['status']}' (controlla 'valori_stato' in mapping.json)")

    return (
        formatta_data(data_creazione),
        formatta_data(data_arrivo),
        notti,
        mappa_capacity_units[codice_unita],
        canale,
        round(prezzo, 2),
        stato,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Importa un CSV di prenotazioni usando mapping.json.")
    parser.add_argument("csv_path", help="percorso del file CSV da importare")
    parser.add_argument("--mapping", default=NOME_MAPPING_DEFAULT, help="percorso del file di mapping (default: mapping.json)")
    parser.add_argument("--db", default=db.NOME_DB_DEFAULT, help="percorso del file database (default: vault.db)")
    args = parser.parse_args(argv)

    percorso_csv = Path(args.csv_path)
    if not percorso_csv.exists():
        print(f"[ERRORE] il file CSV '{args.csv_path}' non esiste.", file=sys.stderr)
        return 1

    if not db.db_esiste(args.db):
        print(f"[ERRORE] il database '{args.db}' non esiste ancora. Esegui prima 'python3 init_db.py'.", file=sys.stderr)
        return 1

    mapping = carica_mapping(args.mapping)
    conn = db.connetti(args.db)
    mappa_capacity_units = {k.upper(): v for k, v in db.mappa_capacity_units(conn).items()}
    if not mappa_capacity_units:
        print("[ERRORE] il database non contiene ancora tipologie. Esegui prima 'python3 init_db.py'.", file=sys.stderr)
        return 1

    righe_valide = []
    scartate = []

    try:
        with percorso_csv.open("r", encoding="utf-8-sig", newline="") as f:
            lettore = csv.DictReader(f, delimiter=mapping["separatore"])
            if not lettore.fieldnames:
                print("[ERRORE] il CSV sembra vuoto o senza intestazione.", file=sys.stderr)
                return 1
            for numero_riga, riga_csv in enumerate(lettore, start=2):  # riga 1 = intestazione
                try:
                    tupla = valida_e_converti_riga(riga_csv, numero_riga, mapping, mappa_capacity_units)
                    righe_valide.append(tupla)
                except ValueError as exc:
                    scartate.append((numero_riga, str(exc)))
    except OSError as exc:
        print(f"[ERRORE] impossibile leggere il file CSV: {exc}", file=sys.stderr)
        return 1
    except csv.Error as exc:
        print(f"[ERRORE] il file CSV e' malformato: {exc}", file=sys.stderr)
        return 1

    if righe_valide:
        conn.executemany(
            """
            INSERT INTO booking (created_date, arrival_date, nights, capacity_unit_id, channel, total_price, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            righe_valide,
        )
        conn.commit()
    conn.close()

    print()
    print("=== Riepilogo importazione ===")
    print(f"File: {args.csv_path}")
    print(f"Righe importate correttamente: {len(righe_valide)}")
    print(f"Righe scartate: {len(scartate)}")
    if scartate:
        print()
        print("Dettaglio righe scartate (riga del CSV, motivo):")
        for numero_riga, motivo in scartate[:30]:
            print(f"  - riga {numero_riga}: {motivo}")
        if len(scartate) > 30:
            print(f"  ... e altre {len(scartate) - 30} righe scartate.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
