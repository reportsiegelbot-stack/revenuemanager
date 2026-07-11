"""
core/config.py
---------------
Legge il file config.json, che contiene TUTTI i parametri specifici della
struttura (nome, tipologie, canali, prezzi minimi/massimi, soglie delle
regole decisionali). Il codice del core non contiene mai numeri "magici":
li legge sempre da qui.
"""

import json
import sys
from pathlib import Path

NOME_CONFIG_DEFAULT = "config.json"

# Le chiavi che devono sempre essere presenti in config.json perche' il
# resto del sistema possa funzionare.
CHIAVI_OBBLIGATORIE = [
    "structure_name",
    "capacity_units",
    "channels",
    "price_ranges",
    "rules_thresholds",
]


def errore_e_esci(messaggio):
    """Stampa un errore comprensibile in italiano e interrompe lo script.

    Usata al posto di lasciar risalire l'eccezione originale di Python,
    che per un utente non tecnico sarebbe incomprensibile.
    """
    print(f"[ERRORE] {messaggio}", file=sys.stderr)
    sys.exit(1)


def carica_config(percorso=NOME_CONFIG_DEFAULT):
    """Carica e valida il file di configurazione. In caso di problemi
    interrompe il programma con un messaggio chiaro, invece di far
    esplodere lo script con un traceback tecnico."""
    cammino = Path(percorso)
    if not cammino.exists():
        errore_e_esci(
            f"il file di configurazione '{percorso}' non esiste. "
            "Copia/adatta il file config.json di esempio nella cartella del progetto."
        )

    try:
        testo = cammino.read_text(encoding="utf-8")
    except OSError as exc:
        errore_e_esci(f"impossibile leggere '{percorso}': {exc}")

    try:
        config = json.loads(testo)
    except json.JSONDecodeError as exc:
        errore_e_esci(
            f"'{percorso}' non e' un JSON valido (riga {exc.lineno}, colonna {exc.colno}): {exc.msg}"
        )

    mancanti = [chiave for chiave in CHIAVI_OBBLIGATORIE if chiave not in config]
    if mancanti:
        errore_e_esci(
            f"nel file '{percorso}' mancano le chiavi obbligatorie: {', '.join(mancanti)}"
        )

    if not config["capacity_units"]:
        errore_e_esci(f"'{percorso}' non definisce nessuna tipologia in 'capacity_units'.")

    for unita in config["capacity_units"]:
        for campo in ("code", "name", "total_units"):
            if campo not in unita:
                errore_e_esci(
                    f"una voce di 'capacity_units' in '{percorso}' non ha il campo '{campo}'."
                )

    # Valori di default per le sezioni facoltative, cosi' il resto del
    # codice puo' sempre assumere che esistano.
    config.setdefault("stagioni", {"alta": [6, 7, 8, 9], "media": [4, 5, 10], "bassa": [1, 2, 3, 11, 12]})
    config.setdefault(
        "generazione_demo",
        {
            "mesi_storico": 14,
            "giorni_futuro": 60,
            "anticipo_prenotazione_min_giorni": 1,
            "anticipo_prenotazione_max_giorni": 120,
            "tasso_cancellazione_pct": 12,
            "fattore_weekend": 1.35,
            "giorni_snapshot_storico": 60,
            "finestra_snapshot_giorni": 45,
        },
    )

    return config


def canale_principale(config):
    """Restituisce il primo canale della lista, considerato il canale
    "diretto" di riferimento (tipicamente il motore di prenotazione
    proprio della struttura)."""
    return config["channels"][0]
