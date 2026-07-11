#!/usr/bin/env python3
"""
init_db.py
----------
Crea (o aggiorna) il database SQLite "vault.db" e vi carica le tipologie
di unita' definite in config.json.

Uso:
    python3 init_db.py
    python3 init_db.py --db un_altro_nome.db --config config.json

Puo' essere rilanciato piu' volte senza problemi: non duplica dati, si
limita ad allineare le tabelle al contenuto di config.json.
"""

import argparse
import sys

from core import db
from core.config import carica_config


def main(argv=None):
    parser = argparse.ArgumentParser(description="Crea/aggiorna il database di revenue-vault.")
    parser.add_argument("--db", default=db.NOME_DB_DEFAULT, help="percorso del file database (default: vault.db)")
    parser.add_argument("--config", default="config.json", help="percorso del file di configurazione (default: config.json)")
    args = parser.parse_args(argv)

    print(f"Lettura configurazione da '{args.config}'...")
    config = carica_config(args.config)

    print(f"Creazione/apertura database '{args.db}'...")
    conn = db.connetti(args.db)
    db.crea_schema(conn)

    print("Sincronizzazione tipologie (capacity_unit) con la configurazione...")
    db.sincronizza_capacity_units(conn, config["capacity_units"])

    unita = db.elenco_capacity_units(conn)
    totale_unita = sum(riga["total_units"] for riga in unita)

    print()
    print(f"Struttura: {config['structure_name']}")
    print(f"Tipologie caricate: {len(unita)} (totale {totale_unita} unita')")
    for riga in unita:
        print(f"  - {riga['code']:6s} {riga['name']:15s} {riga['total_units']:3d} unita'")
    print(f"Canali configurati: {', '.join(config['channels'])}")
    print()
    print(f"Database pronto: '{args.db}'.")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
