#!/usr/bin/env python3
"""
genera_demo.py
---------------
Riempie il database con dati sintetici ma realistici, cosi' da poter
vedere subito il sistema funzionante senza avere ancora dati veri.

Simula una struttura di 6 tipologie sulla Costiera Amalfitana (i valori
di default sono in config.json) con:
  - 14 mesi di storico prenotazioni + i prossimi ~60 giorni
  - forte stagionalita' (alta: giu-set, bassa: nov-mar)
  - weekend (venerdi'/sabato) piu' pieni
  - anticipo di prenotazione variabile (1-120 giorni)
  - una quota di cancellazioni
  - mix di canali di vendita
  - snapshot giornalieri di disponibilita'/prezzo degli ultimi 60 giorni
    (la cosiddetta "curva di prenotazione")
  - alcune variazioni di prezzo storiche fatte dal revenue manager ("RM")
  - alcuni segnali esterni (eventi/festivita') plausibili

Uso:
    python3 genera_demo.py            (chiede conferma prima di ripulire i dati demo)
    python3 genera_demo.py --si       (non chiede conferma, utile per demo.py)
"""

import argparse
import bisect
import random
import sys
from datetime import date, datetime, timedelta

from core import db
from core.config import carica_config
from core.utils import aggiungi_mesi, formatta_data, intervallo_date, is_weekend, oggi, stagione_di

SEME_CASUALE_DEFAULT = 42


# ---------------------------------------------------------------------------
# Funzioni di supporto per generare valori plausibili
# ---------------------------------------------------------------------------

def prezzo_stagionale(config, codice_unita, data_riferimento):
    """Sceglie un prezzo casuale plausibile per la data indicata, in base
    alla stagione tariffaria e alla fascia min/max definita in config.json."""
    stagione = stagione_di(data_riferimento, config)
    fasce = config["price_ranges"][codice_unita]
    if stagione == "alta":
        minimo, massimo = fasce["alta"]
    elif stagione == "bassa":
        minimo, massimo = fasce["bassa"]
    else:
        minimo = (fasce["bassa"][0] + fasce["alta"][0]) / 2
        massimo = (fasce["bassa"][1] + fasce["alta"][1]) / 2
    return random.uniform(minimo, massimo)


def occupazione_target(config, data_riferimento):
    """Restituisce la percentuale di riempimento "obiettivo" per una data,
    combinando stagione e giorno della settimana. Il boost del weekend e'
    additivo (si avvicina al 100% senza mai raggiungerlo con certezza),
    cosi' non tutte le tipologie risultano sempre sold-out lo stesso giorno."""
    livello_base = {"alta": 0.72, "media": 0.50, "bassa": 0.25}
    stagione = stagione_di(data_riferimento, config)
    valore = livello_base.get(stagione, 0.5)
    fattore_weekend = config["generazione_demo"].get("fattore_weekend", 1.35)
    if is_weekend(data_riferimento):
        valore += (1 - valore) * (fattore_weekend - 1)
    valore *= random.uniform(0.88, 1.10)
    return min(valore, 0.95)


def anticipo_casuale(min_giorni, max_giorni):
    """Estrae un numero di giorni di anticipo con cui una prenotazione
    viene fatta rispetto alla data di arrivo. La distribuzione favorisce
    gli anticipi brevi/medi ma lascia una coda di prenotazioni fatte con
    molto anticipo, come avviene nella realta'."""
    max_giorni = max(max_giorni, min_giorni)
    confini = sorted(set([
        max(min_giorni, round(max_giorni * 0.06)),
        max(min_giorni, round(max_giorni * 0.12)),
        max(min_giorni, round(max_giorni * 0.25)),
        max(min_giorni, round(max_giorni * 0.50)),
        max(min_giorni, round(max_giorni * 0.75)),
        max_giorni,
    ]))
    pesi = [30, 20, 20, 15, 10, 5][: len(confini)]
    indice = random.choices(range(len(confini)), weights=pesi, k=1)[0]
    limite_sup = confini[indice]
    limite_inf = confini[indice - 1] + 1 if indice > 0 else min_giorni
    if limite_inf > limite_sup:
        limite_inf = limite_sup
    return random.randint(limite_inf, limite_sup)


OPZIONI_NOTTI = [1, 2, 3, 4, 5, 6, 7]
PESI_NOTTI = [10, 25, 25, 20, 10, 5, 5]
# Durata media del soggiorno, calcolata dalla distribuzione qui sopra
# (serve per stimare quanti NUOVI arrivi al giorno servono per raggiungere
# una certa occupazione: occupazione = arrivi_al_giorno * notti_medie).
NOTTI_MEDIE = sum(o * p for o, p in zip(OPZIONI_NOTTI, PESI_NOTTI)) / sum(PESI_NOTTI)


def arrotonda_probabilistico(valore):
    """Arrotonda un valore atteso non intero in modo probabilistico invece
    che sempre per difetto: un valore atteso di 0.2 genera un evento circa
    2 volte su 10, non zero volte su dieci. Senza questo accorgimento le
    tipologie con pochissime unita' (es. 1 sola) non riceverebbero MAI una
    prenotazione (0.x si arrotonderebbe sempre a 0), restando sempre
    "piene" in modo irrealistico per tutto il periodo generato."""
    intero = int(valore)
    frazione = valore - intero
    if random.random() < frazione:
        intero += 1
    return intero


def notti_casuali():
    """Numero di notti della prenotazione: la maggior parte dei soggiorni
    e' breve (2-4 notti), pochi molto lunghi."""
    return random.choices(OPZIONI_NOTTI, weights=PESI_NOTTI, k=1)[0]


def pesi_canali(canali):
    """Distribuisce il volume di vendite tra i canali: il canale diretto
    (il primo in elenco) vende di piu' degli altri."""
    n = len(canali)
    if n == 1:
        return [1.0]
    peso_diretto = 0.40
    resto = (1 - peso_diretto) / (n - 1)
    return [peso_diretto] + [resto] * (n - 1)


def fattore_prezzo_canale(indice, n_canali):
    """Piccola variazione di prezzo/commissione a seconda del canale:
    il canale diretto (indice 0) non ha ricarico, l'ultimo canale (tipicamente
    un canale all'ingrosso) ha prezzo piu' basso, i canali intermedi (OTA)
    hanno un piccolo ricarico."""
    if indice == 0:
        return 1.0
    if indice == n_canali - 1 and n_canali >= 3:
        return random.uniform(0.85, 0.92)
    return random.uniform(1.0, 1.06)


# ---------------------------------------------------------------------------
# Passo 1: prenotazioni (booking)
# ---------------------------------------------------------------------------

def genera_prenotazioni(conn, config, capacity_units, oggi_data):
    parametri = config["generazione_demo"]
    inizio = aggiungi_mesi(oggi_data, -parametri["mesi_storico"])
    fine = oggi_data + timedelta(days=parametri["giorni_futuro"])
    min_anticipo_cfg = parametri["anticipo_prenotazione_min_giorni"]
    max_anticipo_cfg = parametri["anticipo_prenotazione_max_giorni"]
    tasso_cancellazione = parametri["tasso_cancellazione_pct"] / 100
    canali = config["channels"]
    pesi = pesi_canali(canali)

    righe = []
    confermate = 0
    cancellate = 0

    for data_arrivo in intervallo_date(inizio, fine):
        for unita in capacity_units:
            target = occupazione_target(config, data_arrivo)
            # Non tutte le unita' occupate in un giorno sono NUOVI arrivi:
            # chi resta piu' notti "occupa" anche i giorni successivi.
            # Per non vendere piu' unita' di quante ce ne siano (legge di
            # Little: occupazione = arrivi_al_giorno * notti_medie) dividiamo
            # per la durata media del soggiorno.
            n_prenotazioni = arrotonda_probabilistico(unita["total_units"] * target / NOTTI_MEDIE)
            if n_prenotazioni <= 0:
                continue

            giorni_al_arrivo = (data_arrivo - oggi_data).days
            min_anticipo_effettivo = max(min_anticipo_cfg, giorni_al_arrivo) if giorni_al_arrivo > 0 else min_anticipo_cfg
            if min_anticipo_effettivo > max_anticipo_cfg:
                # Troppo lontano nel tempo: nessuno prenota ancora cosi' in anticipo.
                continue

            for _ in range(n_prenotazioni):
                anticipo = anticipo_casuale(min_anticipo_cfg, max_anticipo_cfg)
                if anticipo < min_anticipo_effettivo:
                    # Non e' ancora "successo": scartiamo questo tentativo,
                    # cosi' le date lontane risultano naturalmente meno piene.
                    continue
                data_creazione = data_arrivo - timedelta(days=anticipo)

                indice_canale = random.choices(range(len(canali)), weights=pesi, k=1)[0]
                canale = canali[indice_canale]

                notti = notti_casuali()
                stato = "cancelled" if random.random() < tasso_cancellazione else "confirmed"

                prezzo_notte = prezzo_stagionale(config, unita["code"], data_arrivo)
                prezzo_notte *= fattore_prezzo_canale(indice_canale, len(canali))
                prezzo_notte *= random.uniform(0.96, 1.04)
                prezzo_totale = round(prezzo_notte * notti, 2)

                righe.append((
                    formatta_data(data_creazione),
                    formatta_data(data_arrivo),
                    notti,
                    unita["id"],
                    canale,
                    prezzo_totale,
                    stato,
                ))
                if stato == "confirmed":
                    confermate += 1
                else:
                    cancellate += 1

    conn.executemany(
        """
        INSERT INTO booking (created_date, arrival_date, nights, capacity_unit_id, channel, total_price, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        righe,
    )
    conn.commit()
    return confermate, cancellate, inizio, fine


# ---------------------------------------------------------------------------
# Passo 2: inventory_snapshot (curva di prenotazione ricostruita)
# ---------------------------------------------------------------------------

def genera_snapshot(conn, config, capacity_units, oggi_data):
    parametri = config["generazione_demo"]
    giorni_storico = parametri["giorni_snapshot_storico"]
    finestra = parametri["finestra_snapshot_giorni"]
    canale_riferimento = config["channels"][0]

    date_snapshot = [oggi_data - timedelta(days=g) for g in range(giorni_storico - 1, -1, -1)]
    data_min_target = min(date_snapshot)
    data_max_target = max(date_snapshot) + timedelta(days=finestra - 1)
    date_target = list(intervallo_date(data_min_target, data_max_target))

    # Carichiamo tutte le prenotazioni confermate una sola volta, per non
    # dover interrogare il database migliaia di volte.
    prenotazioni = conn.execute(
        """
        SELECT capacity_unit_id, created_date, arrival_date, nights
        FROM booking
        WHERE status = 'confirmed'
        """
    ).fetchall()

    righe = []
    for unita in capacity_units:
        prenotazioni_unita = []
        for p in prenotazioni:
            if p["capacity_unit_id"] != unita["id"]:
                continue
            arrivo = datetime.strptime(p["arrival_date"], "%Y-%m-%d").date()
            fine_soggiorno = arrivo + timedelta(days=p["nights"])
            creazione = datetime.strptime(p["created_date"], "%Y-%m-%d").date()
            prenotazioni_unita.append((arrivo, fine_soggiorno, creazione))

        # Per ogni data target, elenco (ordinato) delle date di creazione
        # delle prenotazioni che "coprono" quella data.
        creazioni_per_target = {}
        for data_target in date_target:
            creazioni = sorted(
                creazione
                for (arrivo, fine_soggiorno, creazione) in prenotazioni_unita
                if arrivo <= data_target < fine_soggiorno
            )
            creazioni_per_target[data_target] = creazioni

        # Prezzo pubblicato: calcolato una volta per (unita', data target),
        # cosi' resta stabile tra uno snapshot e l'altro (come nella realta').
        prezzo_per_target = {}
        for data_target in date_target:
            prezzo = prezzo_stagionale(config, unita["code"], data_target)
            if is_weekend(data_target):
                prezzo *= 1.08
            prezzo_per_target[data_target] = round(prezzo)

        for data_snapshot in date_snapshot:
            for offset in range(finestra):
                data_target = data_snapshot + timedelta(days=offset)
                creazioni = creazioni_per_target[data_target]
                venduti = bisect.bisect_right(creazioni, data_snapshot)
                disponibili = max(0, unita["total_units"] - venduti)
                righe.append((
                    formatta_data(data_snapshot),
                    formatta_data(data_target),
                    unita["id"],
                    disponibili,
                    prezzo_per_target[data_target],
                    canale_riferimento,
                ))

    conn.executemany(
        """
        INSERT INTO inventory_snapshot (snapshot_date, target_date, capacity_unit_id, units_available, price_published, channel)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        righe,
    )
    conn.commit()
    return len(righe), len(date_snapshot)


# ---------------------------------------------------------------------------
# Passo 3: rate_event (storico variazioni di prezzo fatte da "RM")
# ---------------------------------------------------------------------------

def genera_rate_events(conn, config, capacity_units, oggi_data, numero_eventi=45):
    canali = config["channels"]
    righe = []
    for _ in range(numero_eventi):
        unita = random.choice(capacity_units)
        canale = random.choice(canali)

        giorno_decisione = oggi_data - timedelta(days=random.randint(0, 90))
        data_target = giorno_decisione + timedelta(days=random.randint(1, 60))

        prezzo_prima = round(prezzo_stagionale(config, unita["code"], data_target), 2)
        percentuale = random.uniform(5, 15)
        aumenta = random.random() < 0.6
        if aumenta:
            prezzo_dopo = round(prezzo_prima * (1 + percentuale / 100), 2)
        else:
            prezzo_dopo = round(prezzo_prima * (1 - percentuale / 100), 2)

        ora = random.randint(8, 20)
        minuto = random.choice([0, 15, 30, 45])
        event_ts = datetime.combine(giorno_decisione, datetime.min.time()).replace(hour=ora, minute=minuto)

        righe.append((
            event_ts.strftime("%Y-%m-%d %H:%M"),
            formatta_data(data_target),
            unita["id"],
            canale,
            prezzo_prima,
            prezzo_dopo,
            "RM",
        ))

    conn.executemany(
        """
        INSERT INTO rate_event (event_ts, target_date, capacity_unit_id, channel, price_before, price_after, author)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        righe,
    )
    conn.commit()
    return len(righe)


# ---------------------------------------------------------------------------
# Passo 4: external_signal (eventi/festivita' plausibili per una struttura
# sulla Costiera Amalfitana)
# ---------------------------------------------------------------------------

# Festivita' nazionali italiane e "ponti", con data fissa (mese, giorno).
MODELLI_SEGNALI_FISSI = [
    (1, 1, "festivita", "Capodanno", 5),
    (1, 6, "festivita", "Epifania", 3),
    (4, 25, "ponte", "Festa della Liberazione (25 Aprile)", 5),
    (5, 1, "ponte", "Festa dei Lavoratori (1 Maggio)", 5),
    (6, 2, "ponte", "Festa della Repubblica (2 Giugno)", 5),
    (8, 15, "festivita", "Ferragosto", 9),
    (11, 1, "festivita", "Ognissanti", 3),
    (12, 8, "ponte", "Immacolata Concezione", 5),
    (12, 25, "festivita", "Natale", 3),
    (12, 26, "festivita", "Santo Stefano", 3),
    (12, 31, "festivita", "San Silvestro / vigilia di Capodanno", 6),
    # Eventi locali della Costiera Amalfitana con data pressoche' fissa.
    (7, 20, "evento_locale", "Concerti del Ravello Festival in Costiera", 6),
    (9, 12, "evento_locale", "Regata storica delle Repubbliche Marinare", 7),
]


def _data_valida(anno, mese, giorno):
    """True se (anno, mese, giorno) e' una data valida (es. non 29 febbraio
    in un anno non bisestile)."""
    try:
        date(anno, mese, giorno)
        return True
    except ValueError:
        return False


def _pasqua(anno):
    """Calcola la domenica di Pasqua (calendario gregoriano) con
    l'algoritmo di Gauss. E' un algoritmo puramente aritmetico, non serve
    nessuna libreria esterna."""
    a = anno % 19
    b = anno // 100
    c = anno % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mese = (h + l - 7 * m + 114) // 31
    giorno = (h + l - 7 * m + 114) % 31 + 1
    return date(anno, mese, giorno)


def _primo_sabato_del_mese(anno, mese):
    """Trova il primo sabato di un dato mese: comodo per eventi locali che
    tradizionalmente si tengono "il primo sabato di luglio/agosto"."""
    primo_giorno = date(anno, mese, 1)
    giorni_da_aggiungere = (5 - primo_giorno.weekday()) % 7  # 5 = sabato
    return primo_giorno + timedelta(days=giorni_da_aggiungere)


# Eventi calcolati per ogni anno (data mobile), tipicamente le feste
# cristiane e gli eventi locali "il primo sabato di un certo mese".
MODELLI_SEGNALI_CALCOLATI = [
    (_pasqua, "festivita", "Pasqua", 5),
    (lambda anno: _pasqua(anno) + timedelta(days=1), "ponte", "Pasquetta (Lunedi' dell'Angelo)", 5),
    (lambda anno: _primo_sabato_del_mese(anno, 7), "evento_locale", "Notte delle Lampare a Cetara", 10),
    (lambda anno: _primo_sabato_del_mese(anno, 8), "evento_locale", "Sagra del Tonno e delle Alici di Cetara", 9),
]


def genera_segnali_esterni(conn, oggi_data, inizio_storico, fine_futuro):
    righe = []
    for anno in range(inizio_storico.year, fine_futuro.year + 1):
        eventi_anno = [
            (date(anno, mese, giorno), tipo, descrizione, impatto_base)
            for mese, giorno, tipo, descrizione, impatto_base in MODELLI_SEGNALI_FISSI
            if _data_valida(anno, mese, giorno)
        ]
        eventi_anno += [
            (funzione_data(anno), tipo, descrizione, impatto_base)
            for funzione_data, tipo, descrizione, impatto_base in MODELLI_SEGNALI_CALCOLATI
        ]

        for data_evento, tipo, descrizione, impatto_base in eventi_anno:
            if not (inizio_storico <= data_evento <= fine_futuro):
                continue
            impatto = max(1, min(10, impatto_base + random.randint(-1, 1)))
            righe.append((
                formatta_data(data_evento),
                tipo,
                descrizione,
                impatto,
            ))

    conn.executemany(
        """
        INSERT INTO external_signal (signal_date, signal_type, description, impact_score)
        VALUES (?, ?, ?, ?)
        """,
        righe,
    )
    conn.commit()
    return len(righe)


# ---------------------------------------------------------------------------
# Pulizia dati demo esistenti (per rendere lo script ripetibile)
# ---------------------------------------------------------------------------

def ripulisci_dati_demo(conn):
    for tabella in ("booking", "inventory_snapshot", "rate_event", "channel_event", "decision", "external_signal"):
        conn.execute(f"DELETE FROM {tabella}")
    conn.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Genera dati demo sintetici per revenue-vault.")
    parser.add_argument("--db", default=db.NOME_DB_DEFAULT, help="percorso del file database (default: vault.db)")
    parser.add_argument("--config", default="config.json", help="percorso del file di configurazione (default: config.json)")
    parser.add_argument("--si", action="store_true", help="non chiedere conferma prima di ripulire i dati demo esistenti")
    parser.add_argument("--seed", type=int, default=SEME_CASUALE_DEFAULT, help="seme casuale, per ottenere sempre gli stessi dati demo")
    args = parser.parse_args(argv)

    config = carica_config(args.config)
    random.seed(args.seed)

    conn = db.connetti(args.db)
    db.crea_schema(conn)
    db.sincronizza_capacity_units(conn, config["capacity_units"])
    capacity_units = [dict(riga) for riga in db.elenco_capacity_units(conn)]

    righe_esistenti = conn.execute("SELECT COUNT(*) AS n FROM booking").fetchone()["n"]
    if righe_esistenti > 0:
        if not args.si:
            if sys.stdin.isatty():
                risposta = input(
                    f"Nel database sono gia' presenti {righe_esistenti} prenotazioni demo/test. "
                    "Rigenerandole verranno CANCELLATE e ricreate da zero. Continuare? [s/N]: "
                )
                if risposta.strip().lower() not in ("s", "si", "sì", "y", "yes"):
                    print("Operazione annullata: nessun dato modificato.")
                    return 1
            else:
                print(
                    "[ERRORE] Nel database ci sono gia' dati demo e lo script non e' interattivo. "
                    "Rilancia con l'opzione --si per confermare la rigenerazione."
                )
                return 1
        print("Rimozione dei dati demo/test precedenti...")
        ripulisci_dati_demo(conn)

    oggi_data = oggi()
    print(f"Generazione dati sintetici (seme casuale {args.seed})...")

    confermate, cancellate, inizio, fine = genera_prenotazioni(conn, config, capacity_units, oggi_data)
    print(f"  - prenotazioni generate: {confermate + cancellate} (confermate: {confermate}, cancellate: {cancellate})")
    print(f"    periodo date di arrivo coperto: {formatta_data(inizio)} -> {formatta_data(fine)}")

    n_snapshot, n_giorni_snapshot = genera_snapshot(conn, config, capacity_units, oggi_data)
    print(f"  - inventory_snapshot generati: {n_snapshot} righe ({n_giorni_snapshot} giorni di rilevazione storica)")

    n_rate = genera_rate_events(conn, config, capacity_units, oggi_data)
    print(f"  - rate_event generati: {n_rate} (autore 'RM')")

    n_segnali = genera_segnali_esterni(conn, oggi_data, inizio, fine)
    print(f"  - external_signal generati: {n_segnali}")

    conn.close()
    print()
    print("Dati demo pronti. Esegui 'python3 report_mattina.py' per generare il report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
