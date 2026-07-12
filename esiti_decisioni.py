#!/usr/bin/env python3
"""
esiti_decisioni.py
--------------------
R13: misura l'esito delle decisioni di prezzo generate da report_mattina.py
(aumento_prezzo, unita_scarse_aumento, ribasso_promo), registrate nella
tabella decision_outcome. Per ogni decisione con almeno N giorni di
anzianita' (config.json: rules_thresholds.esiti.giorni_minimi_misurazione,
default 3), cerca la rilevazione PIU' RECENTE in inventory_snapshot,
successiva alla decisione, sullo STESSO canale su cui la decisione era
nata, e classifica l'esito:

  SEGUITA       il prezzo pubblicato ha raggiunto il livello suggerito
                (aumento_prezzo/unita_scarse_aumento), o la disponibilita'
                e' scesa dopo la decisione (ribasso_promo, misura indiretta)
  PARZIALE      il prezzo e' aumentato ma non fino al livello suggerito
                (solo per le decisioni con un prezzo suggerito numerico)
  NON SEGUITA   nessun movimento nella direzione attesa
  NON MISURABILE  nessuna rilevazione successiva sullo stesso canale, o
                dati di partenza mancanti: mai un valore indovinato (P5)

La misurazione e' riproducibile e monotona: una decisione gia' misurata
viene ri-misurata solo se e' comparsa una rilevazione piu' recente di
quella usata l'ultima volta. Rilanciare lo script piu' volte non cambia
nulla se non ci sono nuove rilevazioni.

Per ribasso_promo la misura e' dichiaratamente INDIRETTA: una variazione
di disponibilita' non dimostra che la promozione sia stata applicata (le
unita' si vendono anche senza), quindi il dettaglio dell'esito lo dice
sempre esplicitamente in apertura.

Uso:
    python3 esiti_decisioni.py
    python3 esiti_decisioni.py --db vault.db --config config.json --giorni-minimi 3
"""

import argparse
import sys
from datetime import timedelta

from core import db
from core.config import carica_config
from core.utils import formatta_data, oggi

PREFISSO_RIBASSO = "misura indiretta (variazione disponibilita'), non verifica dell'azione: "

DECISIONI_CON_PREZZO_NUMERICO = ("aumento_prezzo", "unita_scarse_aumento")


def _trova_rilevazione_successiva(conn, target_date, capacity_unit_id, channel, generated_date):
    """Rilevazione PIU' RECENTE in inventory_snapshot per (target_date,
    tipologia, canale) con snapshot_date successiva al giorno della
    decisione. Solo lo stesso canale su cui la decisione era nata: un
    prezzo di un altro canale non dice nulla su questa decisione."""
    return conn.execute(
        """
        SELECT snapshot_date, units_available, price_published
        FROM inventory_snapshot
        WHERE target_date = ? AND capacity_unit_id = ? AND channel = ? AND snapshot_date > ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        (target_date, capacity_unit_id, channel, generated_date),
    ).fetchone()


def _misura_decisione_prezzo(riga, rilevazione, tolleranza_pct):
    """Classifica aumento_prezzo / unita_scarse_aumento in base al prezzo
    pubblicato nella rilevazione trovata."""
    intestazione = f"Rilevazione usata: {rilevazione['snapshot_date']} (canale {riga['channel']}). "

    if riga["current_price"] is None or riga["suggested_price"] is None:
        return "not_measurable", (
            intestazione + "prezzo di partenza o prezzo suggerito non disponibile al momento "
            "della decisione: impossibile determinare la direzione, storico non disponibile."
        )

    prezzo_dopo = rilevazione["price_published"]
    if prezzo_dopo is None:
        return "not_measurable", (
            intestazione + "la rilevazione trovata non ha un prezzo pubblicato: storico non disponibile."
        )

    soglia_seguita = riga["suggested_price"] * (1 - tolleranza_pct / 100)
    if prezzo_dopo >= soglia_seguita:
        return "followed", (
            intestazione + f"prezzo pubblicato {prezzo_dopo:g} EUR, ha raggiunto il livello suggerito "
            f"({riga['suggested_price']:.0f} EUR, tolleranza {tolleranza_pct}%): il rialzo e' stato applicato."
        )
    if prezzo_dopo <= riga["current_price"]:
        return "not_followed", (
            intestazione + f"prezzo pubblicato {prezzo_dopo:g} EUR, invariato o sceso rispetto al momento "
            f"della decisione ({riga['current_price']:.0f} EUR): il rialzo non risulta applicato."
        )
    return "partial", (
        intestazione + f"prezzo pubblicato {prezzo_dopo:g} EUR, aumentato rispetto al momento della decisione "
        f"({riga['current_price']:.0f} EUR) ma non fino al livello suggerito ({riga['suggested_price']:.0f} EUR)."
    )


def _misura_ribasso_promo(riga, rilevazione):
    """Classifica ribasso_promo in base alla disponibilita': misura
    indiretta, dichiarata sempre in apertura del dettaglio (nessun prezzo
    suggerito numerico da confrontare per questo tipo di decisione)."""
    intestazione = PREFISSO_RIBASSO + f"rilevazione usata: {rilevazione['snapshot_date']} (canale {riga['channel']}). "

    if riga["current_units"] is None:
        return "not_measurable", intestazione + "disponibilita' al momento della decisione non nota, storico non disponibile."

    disponibili_dopo = rilevazione["units_available"]
    if disponibili_dopo < riga["current_units"]:
        return "followed", (
            intestazione + f"disponibilita' scesa da {riga['current_units']} a {disponibili_dopo} unita' "
            "dopo la decisione."
        )
    return "not_followed", (
        intestazione + f"disponibilita' invariata o aumentata ({riga['current_units']} -> {disponibili_dopo} "
        "unita') dopo la decisione."
    )


def _messaggio_nessuna_rilevazione(decision_type, channel):
    dettaglio = f"nessuna rilevazione successiva sullo stesso canale ({channel}), storico non disponibile."
    if decision_type == "ribasso_promo":
        return PREFISSO_RIBASSO + dettaglio
    return dettaglio


def misura_esiti(conn, oggi_data, giorni_minimi, tolleranza_pct):
    """Esamina le decisioni abbastanza vecchie e aggiorna decision_outcome.
    Restituisce un riepilogo per il messaggio finale."""
    soglia_data = formatta_data(oggi_data - timedelta(days=giorni_minimi))

    righe = conn.execute(
        "SELECT * FROM decision_outcome WHERE generated_date <= ? ORDER BY id",
        (soglia_data,),
    ).fetchall()

    esaminate = 0
    aggiornate = {"followed": 0, "not_followed": 0, "partial": 0, "not_measurable": 0}
    invariate = 0
    dettagli_aggiornate = []

    for riga in righe:
        esaminate += 1
        rilevazione = _trova_rilevazione_successiva(
            conn, riga["target_date"], riga["capacity_unit_id"], riga["channel"], riga["generated_date"]
        )

        if rilevazione is None:
            # Nessuna rilevazione utile trovata. Se non ne avevamo mai
            # trovata una prima d'ora (measured_snapshot_date NULL) e lo
            # stato non e' gia' 'not_measurable', lo segnaliamo una volta;
            # altrimenti non c'e' nulla di nuovo da scrivere (monotono).
            if riga["measured_snapshot_date"] is None and riga["status"] == "pending":
                dettaglio = _messaggio_nessuna_rilevazione(riga["decision_type"], riga["channel"])
                conn.execute(
                    "UPDATE decision_outcome SET status = 'not_measurable', measured_ts = ?, measurement_detail = ? WHERE id = ?",
                    (formatta_data(oggi_data), dettaglio, riga["id"]),
                )
                aggiornate["not_measurable"] += 1
                dettagli_aggiornate.append((riga, "not_measurable", dettaglio))
            else:
                invariate += 1
            continue

        # Monotonia: se avevamo gia' misurato con una rilevazione uguale o
        # piu' recente di questa, non c'e' nulla di nuovo da fare.
        if riga["measured_snapshot_date"] is not None and rilevazione["snapshot_date"] <= riga["measured_snapshot_date"]:
            invariate += 1
            continue

        if riga["decision_type"] in DECISIONI_CON_PREZZO_NUMERICO:
            stato, dettaglio = _misura_decisione_prezzo(riga, rilevazione, tolleranza_pct)
        else:
            stato, dettaglio = _misura_ribasso_promo(riga, rilevazione)

        conn.execute(
            """
            UPDATE decision_outcome
            SET status = ?, measured_ts = ?, measured_snapshot_date = ?, measurement_detail = ?
            WHERE id = ?
            """,
            (stato, formatta_data(oggi_data), rilevazione["snapshot_date"], dettaglio, riga["id"]),
        )
        aggiornate[stato] += 1
        dettagli_aggiornate.append((riga, stato, dettaglio))

    conn.commit()
    return {
        "esaminate": esaminate,
        "aggiornate": aggiornate,
        "invariate": invariate,
        "dettagli": dettagli_aggiornate,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Misura l'esito delle decisioni di prezzo (R13).")
    parser.add_argument("--db", default=db.NOME_DB_DEFAULT, help="percorso del file database (default: vault.db)")
    parser.add_argument("--config", default="config.json", help="percorso del file di configurazione (default: config.json)")
    parser.add_argument("--giorni-minimi", type=int, default=None, help="giorni minimi di anzianita' della decisione prima di provare a misurarla (default: da config.json)")
    args = parser.parse_args(argv)

    if not db.db_esiste(args.db):
        print(f"[ERRORE] il database '{args.db}' non esiste ancora. Esegui prima 'python3 init_db.py'.", file=sys.stderr)
        return 1

    config = carica_config(args.config)
    soglie_esiti = config["rules_thresholds"].get("esiti", {})
    giorni_minimi = args.giorni_minimi if args.giorni_minimi is not None else soglie_esiti.get("giorni_minimi_misurazione", 3)
    tolleranza_pct = soglie_esiti.get("tolleranza_pct_seguita", 2)

    conn = db.connetti(args.db)
    oggi_data = oggi()

    print("Misurazione esiti delle decisioni di prezzo in corso...")
    risultato = misura_esiti(conn, oggi_data, giorni_minimi, tolleranza_pct)
    conn.close()

    print()
    print("=== Riepilogo esiti decisioni (R13) ===")
    print(f"Decisioni con almeno {giorni_minimi} giorni di anzianita' esaminate: {risultato['esaminate']}")
    print(f"Invariate (nessuna rilevazione nuova rispetto all'ultima misurazione): {risultato['invariate']}")
    print("Aggiornate ora:")
    print(f"  - seguita: {risultato['aggiornate']['followed']}")
    print(f"  - parziale: {risultato['aggiornate']['partial']}")
    print(f"  - non seguita: {risultato['aggiornate']['not_followed']}")
    print(f"  - non misurabile: {risultato['aggiornate']['not_measurable']}")

    if risultato["dettagli"]:
        print()
        print("Dettaglio decisioni aggiornate in questa esecuzione:")
        for riga, stato, dettaglio in risultato["dettagli"]:
            print(
                f"  - #{riga['id']} {riga['target_date']} {riga['decision_type']} "
                f"(generata il {riga['generated_date']}) -> {stato}: {dettaglio}"
            )

    print()
    print("Apri report_mattina.py per vedere il riepilogo nella sezione g) Esiti delle decisioni.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
