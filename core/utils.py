"""
core/utils.py
--------------
Piccole funzioni di utilita' condivise da tutti gli script: gestione delle
date, calcolo della stagione tariffaria, formattazione di numeri e testi.
Anche qui: nessuna parola legata al settore alberghiero.
"""

import datetime

FORMATO_DATA_ISO = "%Y-%m-%d"

NOMI_GIORNI_IT = [
    "lunedi",
    "martedi",
    "mercoledi",
    "giovedi",
    "venerdi",
    "sabato",
    "domenica",
]

NOMI_MESI_IT = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]


def oggi():
    """Restituisce la data odierna (funzione separata cosi' i test/demo
    possono, volendo, sovrascriverla facilmente)."""
    return datetime.date.today()


def parse_data(testo, formato=FORMATO_DATA_ISO):
    """Converte una stringa in una data, con un messaggio d'errore chiaro
    se il formato non e' quello atteso. Solleva ValueError."""
    testo = (testo or "").strip()
    if not testo:
        raise ValueError("la data e' vuota")
    try:
        return datetime.datetime.strptime(testo, formato).date()
    except ValueError:
        raise ValueError(
            f"'{testo}' non e' una data valida nel formato atteso ({formato})"
        )


def formatta_data(data):
    """Converte una data Python nel formato canonico usato nel database
    (YYYY-MM-DD, ordinabile come testo)."""
    return data.strftime(FORMATO_DATA_ISO)


def formatta_data_estesa(data):
    """Es: 'venerdi 11 luglio 2026', per i testi mostrati nel report."""
    nome_giorno = NOMI_GIORNI_IT[data.weekday()]
    nome_mese = NOMI_MESI_IT[data.month - 1]
    return f"{nome_giorno} {data.day} {nome_mese} {data.year}"


def aggiungi_mesi(data, numero_mesi):
    """Aggiunge (o toglie, se negativo) un certo numero di mesi a una data,
    usando solo la libreria standard (non esiste un equivalente diretto di
    dateutil.relativedelta in stdlib)."""
    mese_totale = data.month - 1 + numero_mesi
    anno = data.year + mese_totale // 12
    mese = mese_totale % 12 + 1
    ultimo_giorno_mese = _ultimo_giorno_del_mese(anno, mese)
    giorno = min(data.day, ultimo_giorno_mese)
    return datetime.date(anno, mese, giorno)


def _ultimo_giorno_del_mese(anno, mese):
    if mese == 12:
        primo_mese_successivo = datetime.date(anno + 1, 1, 1)
    else:
        primo_mese_successivo = datetime.date(anno, mese + 1, 1)
    return (primo_mese_successivo - datetime.timedelta(days=1)).day


def is_weekend(data):
    """Considera 'giorni forti' venerdi' e sabato (notti di maggior
    richiesta), coerente con la classica curva di domanda leisure."""
    return data.weekday() in (4, 5)


def stagione_di(data, config):
    """Restituisce 'alta', 'media' o 'bassa' in base al mese della data,
    leggendo l'assegnazione mesi->stagione da config['stagioni']."""
    mese = data.month
    stagioni = config.get("stagioni", {})
    for nome_stagione, mesi in stagioni.items():
        if nome_stagione.startswith("_"):
            continue
        if mese in mesi:
            return nome_stagione
    return "media"


def intervallo_date(data_inizio, data_fine):
    """Generatore che restituisce tutte le date da data_inizio a data_fine
    (estremi inclusi)."""
    giorno = data_inizio
    while giorno <= data_fine:
        yield giorno
        giorno += datetime.timedelta(days=1)


def numero_o_nd(valore, decimali=0, suffisso=""):
    """Formatta un numero per la visualizzazione, oppure 'N/D' se il
    valore e' None (dato non disponibile)."""
    if valore is None:
        return "N/D"
    return f"{valore:.{decimali}f}{suffisso}"
