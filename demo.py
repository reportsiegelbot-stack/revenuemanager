#!/usr/bin/env python3
"""
demo.py
--------
Un solo comando per vedere tutto il sistema funzionante: crea il
database, lo riempie di dati demo realistici e genera il report del
mattino.

Uso:
    python3 demo.py
"""

import sys

import genera_demo
import init_db
import report_mattina


def main(argv=None):
    print("=== 1/3: creazione del database ===")
    esito = init_db.main([])
    if esito != 0:
        return esito

    print()
    print("=== 2/3: generazione dati demo ===")
    esito = genera_demo.main(["--si"])
    if esito != 0:
        return esito

    print()
    print("=== 3/3: generazione report del mattino ===")
    esito = report_mattina.main([])
    if esito != 0:
        return esito

    print()
    print("Demo completata. Apri report/report_oggi.html nel browser per vedere il risultato.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
