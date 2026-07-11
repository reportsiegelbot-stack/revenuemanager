#!/usr/bin/env python3
"""
snapshot_oggi.py
------------------
Script interattivo da usare ogni mattina (o quando serve) per registrare
"a mano" la disponibilita' residua e il prezzo pubblicato per le prossime
date chiave. E' pensato per essere veloce: per ogni domanda basta premere
INVIO per confermare il valore proposto tra parentesi quadre, che di
default e' uguale al giorno precedente (di solito la situazione cambia
poco da un giorno all'altro).

Uso:
    python3 snapshot_oggi.py                (chiede 21 giorni, come da default)
    python3 snapshot_oggi.py --giorni 14

In qualsiasi momento si puo' scrivere "fine" invece di un numero per
interrompere e salvare solo cio' che e' stato inserito fino a quel punto.
"""

import argparse
import sys
from datetime import timedelta

from core import db
from core.config import carica_config
from core.utils import formatta_data, formatta_data_estesa, oggi

COMANDO_INTERRUZIONE = "fine"


def chiedi_intero(messaggio, valore_default, minimo, massimo):
    """Chiede un numero intero, riproponendo la domanda se l'input non e'
    valido. Premere solo INVIO conferma il valore proposto. Restituisce
    None se l'utente vuole interrompere lo script."""
    while True:
        testo = input(f"{messaggio} [{valore_default}]: ").strip()
        if testo == "":
            return valore_default
        if testo.lower() == COMANDO_INTERRUZIONE:
            return None
        try:
            valore = int(testo)
        except ValueError:
            print(f"    Non ho capito: scrivi un numero intero tra {minimo} e {massimo} (oppure INVIO per {valore_default}).")
            continue
        if not (minimo <= valore <= massimo):
            print(f"    Il valore deve essere tra {minimo} e {massimo}.")
            continue
        return valore


def chiedi_prezzo(messaggio, valore_default):
    """Chiede un prezzo (numero anche decimale). Restituisce None se
    l'utente vuole interrompere lo script."""
    while True:
        testo = input(f"{messaggio} [{valore_default}]: ").strip()
        if testo == "":
            return valore_default
        if testo.lower() == COMANDO_INTERRUZIONE:
            return None
        try:
            valore = float(testo.replace(",", "."))
            if valore < 0:
                raise ValueError
        except ValueError:
            print(f"    Non ho capito: scrivi un prezzo valido (es. 180 oppure 180.50), oppure INVIO per {valore_default}.")
            continue
        return valore


def prezzo_iniziale(config, codice_unita):
    """Valore proposto la primissima volta, prima che l'utente abbia
    inserito qualcosa: la media della fascia di prezzo 'media stagione'."""
    fasce = config["price_ranges"].get(codice_unita)
    if not fasce:
        return 100.0
    bassa_media = sum(fasce["bassa"]) / 2
    alta_media = sum(fasce["alta"]) / 2
    return round((bassa_media + alta_media) / 2)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inserimento rapido e interattivo di disponibilita' e prezzi.")
    parser.add_argument("--giorni", type=int, default=21, help="quante date future compilare (default: 21)")
    parser.add_argument("--canale", default=None, help="canale a cui si riferisce lo snapshot (default: il primo canale in config.json)")
    parser.add_argument("--db", default=db.NOME_DB_DEFAULT, help="percorso del file database (default: vault.db)")
    parser.add_argument("--config", default="config.json", help="percorso del file di configurazione (default: config.json)")
    args = parser.parse_args(argv)

    if not db.db_esiste(args.db):
        print(f"[ERRORE] il database '{args.db}' non esiste ancora. Esegui prima 'python3 init_db.py'.", file=sys.stderr)
        return 1

    config = carica_config(args.config)
    canale = args.canale or config["channels"][0]

    conn = db.connetti(args.db)
    unita_elenco = [dict(riga) for riga in db.elenco_capacity_units(conn)]
    if not unita_elenco:
        print("[ERRORE] il database non contiene ancora tipologie. Esegui prima 'python3 init_db.py'.", file=sys.stderr)
        return 1

    oggi_data = oggi()
    date_da_compilare = [oggi_data + timedelta(days=i) for i in range(args.giorni)]

    print(f"=== Snapshot disponibilita'/prezzi del {formatta_data_estesa(oggi_data)} ===")
    print(f"Canale di riferimento: {canale}")
    print(f"Date da compilare: {args.giorni} (da oggi a {formatta_data(date_da_compilare[-1])})")
    print("Premi solo INVIO per confermare il valore proposto tra parentesi.")
    print(f"Scrivi '{COMANDO_INTERRUZIONE}' in qualsiasi momento per interrompere e salvare quanto fatto finora.")
    print()

    ultima_disponibilita = {u["code"]: u["total_units"] for u in unita_elenco}
    ultimo_prezzo = {u["code"]: prezzo_iniziale(config, u["code"]) for u in unita_elenco}

    righe_salvate = 0
    interrotto = False

    try:
        for data_target in date_da_compilare:
            print(f"--- {formatta_data_estesa(data_target)} ---")
            righe_giorno = []
            for unita in unita_elenco:
                codice = unita["code"]
                etichetta = f"{unita['name']} ({codice}, max {unita['total_units']})"

                disponibili = chiedi_intero(
                    f"  {etichetta} - unita' disponibili", ultima_disponibilita[codice], 0, unita["total_units"]
                )
                if disponibili is None:
                    interrotto = True
                    break

                prezzo = chiedi_prezzo(f"  {etichetta} - prezzo pubblicato", ultimo_prezzo[codice])
                if prezzo is None:
                    interrotto = True
                    break

                ultima_disponibilita[codice] = disponibili
                ultimo_prezzo[codice] = prezzo
                righe_giorno.append((formatta_data(oggi_data), formatta_data(data_target), unita["id"], disponibili, prezzo, canale))

            if righe_giorno:
                conn.executemany(
                    """
                    INSERT INTO inventory_snapshot (snapshot_date, target_date, capacity_unit_id, units_available, price_published, channel)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    righe_giorno,
                )
                conn.commit()
                righe_salvate += len(righe_giorno)

            if interrotto:
                break
    except (EOFError, KeyboardInterrupt):
        print()
        interrotto = True

    conn.close()

    print()
    if interrotto:
        print(f"Interrotto su richiesta. Righe salvate finora: {righe_salvate}.")
    else:
        print(f"Completato. Righe salvate: {righe_salvate}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
