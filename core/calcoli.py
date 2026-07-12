"""
core/calcoli.py
-----------------
Piccole funzioni di calcolo condivise tra i consumer del database che non
sono report_mattina.py (oggi: dashboard/esporta_stato.py). Modulo NUOVO:
non tocca ne' report_mattina.py ne' gli altri file di core/.

La logica del pickup, dei suggerimenti e della loro motivazione a livelli
resta unicamente in report_mattina.py e viene IMPORTATA da chi la riusa
(vedi dashboard/esporta_stato.py), non duplicata qui: cosi' le due regole
non possono disallinearsi nel tempo. Qui vivono solo calcoli "atomici" che
report_mattina.py non espone gia' come funzione a se stante.
"""

from datetime import timedelta

from core.utils import formatta_data, stagione_di


def serie_rilevazioni(conn, capacity_unit_id, channel, target_date, entro_il):
    """Restituisce l'intera serie storica delle rilevazioni note per una
    data futura (data_rilevazione -> camere disponibili), in ordine
    cronologico. Usata per disegnare la "booking curve" nella dashboard:
    a differenza del pickup (che guarda solo l'ultima rilevazione e quella
    precedente) qui serve la serie completa da graficare."""
    righe = conn.execute(
        """
        SELECT snapshot_date, units_available
        FROM inventory_snapshot
        WHERE target_date = ? AND capacity_unit_id = ? AND channel = ? AND snapshot_date <= ?
        ORDER BY snapshot_date
        """,
        (formatta_data(target_date), capacity_unit_id, channel, formatta_data(entro_il)),
    ).fetchall()
    return [{"data_rilevazione": riga["snapshot_date"], "camere_libere": riga["units_available"]} for riga in righe]


def prossimo_cambio_stagione(config, oggi_data, orizzonte_massimo_giorni=400):
    """Trova la prossima data in cui cambia la stagione tariffaria (letta
    da config['stagioni']). Il sistema oggi non modella un vero calendario
    di apertura/chiusura della struttura (non e' un dato presente in
    config.json), quindi questo e' il miglior proxy disponibile onesto per
    "quanto manca alla prossima fase": es. se si sta uscendo dall'alta
    stagione, e' un'approssimazione di "quanto manca alla bassa stagione"."""
    stagione_oggi = stagione_di(oggi_data, config)
    for offset in range(1, orizzonte_massimo_giorni):
        giorno = oggi_data + timedelta(days=offset)
        stagione_giorno = stagione_di(giorno, config)
        if stagione_giorno != stagione_oggi:
            return {
                "data": formatta_data(giorno),
                "da_stagione": stagione_oggi,
                "a_stagione": stagione_giorno,
                "giorni_mancanti": offset,
            }
    return None
