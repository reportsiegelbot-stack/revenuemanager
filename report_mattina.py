#!/usr/bin/env python3
"""
report_mattina.py
--------------------
Il cuore del sistema. Legge il database e produce un report HTML
(report/report_oggi.html) pensato per essere aperto ogni mattina e letto
in pochi minuti da chi si occupa dei prezzi e della disponibilita'.

Contiene:
  a) la situazione dei prossimi 30 giorni (disponibilita', prezzo, pickup)
  b) il confronto con lo stesso periodo dell'anno precedente
  c) le date "anomale" da controllare
  d) suggerimenti generati da un motore a regole parametriche (le soglie
     sono tutte lette da config.json, non ce n'e' nessuna scritta nel
     codice), ognuno salvato anche nella tabella "decision"
  e) il "playbook" del revenue manager: come si e' comportato in passato
     l'autore "RM" quando ha cambiato i prezzi

Uso:
    python3 report_mattina.py
    python3 report_mattina.py --db vault.db --config config.json --output report/report_oggi.html
"""

import argparse
import html
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from core import db
from core.config import carica_config
from core.utils import formatta_data, formatta_data_estesa, oggi

ORIZZONTE_GIORNI_DEFAULT = 30
PICKUP_GIORNI = 7
ORDINE_URGENZA = {"alta": 0, "media": 1, "bassa": 2}


# ---------------------------------------------------------------------------
# a) Situazione dei prossimi N giorni
# ---------------------------------------------------------------------------

def costruisci_situazione(conn, capacity_units, oggi_data, orizzonte_giorni):
    """Per ogni combinazione (data futura, tipologia) recupera l'ultimo
    snapshot conosciuto (disponibilita' e prezzo) e calcola il "pickup"
    (variazione di disponibilita') rispetto a circa 7 giorni prima."""
    righe = []
    oggi_str = formatta_data(oggi_data)

    for offset in range(orizzonte_giorni):
        data_target = oggi_data + timedelta(days=offset)
        data_target_str = formatta_data(data_target)

        for unita in capacity_units:
            ultimo = conn.execute(
                """
                SELECT units_available, price_published, snapshot_date
                FROM inventory_snapshot
                WHERE target_date = ? AND capacity_unit_id = ? AND snapshot_date <= ?
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                (data_target_str, unita["id"], oggi_str),
            ).fetchone()

            voce = {
                "data": data_target,
                "giorni_al_target": offset,
                "unita_id": unita["id"],
                "unita_code": unita["code"],
                "unita_name": unita["name"],
                "unita_totale": unita["total_units"],
                "disponibili": None,
                "disponibilita_pct": None,
                "prezzo": None,
                "pickup": None,
            }

            if ultimo is not None:
                disponibili = ultimo["units_available"]
                voce["disponibili"] = disponibili
                voce["disponibilita_pct"] = round(disponibili / unita["total_units"] * 100, 1)
                voce["prezzo"] = ultimo["price_published"]

                data_riferimento = datetime.strptime(ultimo["snapshot_date"], "%Y-%m-%d").date()
                data_confronto_str = formatta_data(data_riferimento - timedelta(days=PICKUP_GIORNI))
                precedente = conn.execute(
                    """
                    SELECT units_available
                    FROM inventory_snapshot
                    WHERE target_date = ? AND capacity_unit_id = ? AND snapshot_date <= ?
                    ORDER BY snapshot_date DESC
                    LIMIT 1
                    """,
                    (data_target_str, unita["id"], data_confronto_str),
                ).fetchone()
                if precedente is not None:
                    voce["pickup"] = precedente["units_available"] - disponibili

            righe.append(voce)

    return righe


# ---------------------------------------------------------------------------
# b) Confronto con lo stesso periodo dell'anno precedente
# ---------------------------------------------------------------------------

def _statistiche_periodo(conn, data_inizio, data_fine, capacity_unit_id=None, creato_entro_il=None):
    """Statistiche sulle prenotazioni confermate con arrivo nel periodo
    indicato. Se 'creato_entro_il' e' impostato, conta solo le prenotazioni
    gia' fatte a quella data: serve per confrontare "lo stesso punto della
    curva di prenotazione" tra un anno e l'altro (STLY, same-time-last-year),
    invece di confrontare un periodo futuro (ancora in costruzione) con uno
    passato (ormai definitivo), confronto che sarebbe fuorviante."""
    parametri = [formatta_data(data_inizio), formatta_data(data_fine)]
    filtro_unita = ""
    if capacity_unit_id is not None:
        filtro_unita = "AND capacity_unit_id = ?"
        parametri.append(capacity_unit_id)
    filtro_creazione = ""
    if creato_entro_il is not None:
        filtro_creazione = "AND created_date <= ?"
        parametri.append(formatta_data(creato_entro_il))

    riga = conn.execute(
        f"""
        SELECT COUNT(*) AS n_prenotazioni,
               COALESCE(SUM(nights), 0) AS notti,
               COALESCE(SUM(total_price), 0.0) AS ricavo
        FROM booking
        WHERE status = 'confirmed' AND arrival_date BETWEEN ? AND ? {filtro_unita} {filtro_creazione}
        """,
        parametri,
    ).fetchone()

    notti = riga["notti"]
    prezzo_medio = riga["ricavo"] / notti if notti else None
    return {
        "n_prenotazioni": riga["n_prenotazioni"],
        "notti": notti,
        "ricavo": round(riga["ricavo"], 2),
        "prezzo_medio_notte": round(prezzo_medio, 2) if prezzo_medio is not None else None,
    }


def confronto_anno_precedente(conn, capacity_units, oggi_data, orizzonte_giorni):
    """Confronta il periodo corrente con lo stesso periodo di un anno fa,
    usando pero' lo stesso punto della "curva di prenotazione": per l'anno
    corrente si contano le prenotazioni fatte fino ad oggi, per l'anno
    precedente quelle fatte fino allo stesso numero di giorni prima
    dell'arrivo. Cosi' il confronto e' equo (altrimenti il periodo corrente,
    ancora in costruzione, sembrerebbe sempre molto peggiore di quello
    passato, che invece e' ormai completo)."""
    fine_periodo = oggi_data + timedelta(days=orizzonte_giorni - 1)
    inizio_anno_scorso = oggi_data - timedelta(days=365)
    fine_anno_scorso = fine_periodo - timedelta(days=365)

    totale_corrente = _statistiche_periodo(conn, oggi_data, fine_periodo, creato_entro_il=oggi_data)
    totale_precedente = _statistiche_periodo(conn, inizio_anno_scorso, fine_anno_scorso, creato_entro_il=inizio_anno_scorso)

    per_unita = []
    for unita in capacity_units:
        corrente = _statistiche_periodo(conn, oggi_data, fine_periodo, unita["id"], creato_entro_il=oggi_data)
        precedente = _statistiche_periodo(conn, inizio_anno_scorso, fine_anno_scorso, unita["id"], creato_entro_il=inizio_anno_scorso)
        per_unita.append({"unita": unita, "corrente": corrente, "precedente": precedente})

    return {
        "periodo_corrente": (oggi_data, fine_periodo),
        "periodo_precedente": (inizio_anno_scorso, fine_anno_scorso),
        "totale_corrente": totale_corrente,
        "totale_precedente": totale_precedente,
        "per_unita": per_unita,
    }


def variazione_pct(valore_nuovo, valore_vecchio):
    if not valore_vecchio:
        return None
    return round((valore_nuovo - valore_vecchio) / valore_vecchio * 100, 1)


# ---------------------------------------------------------------------------
# c) Anomalie
# ---------------------------------------------------------------------------

def rileva_anomalie(situazione, orizzonte_giorni):
    # "Troppo presto" e' definito relativamente all'orizzonte del report:
    # se manca piu' dei due terzi del periodo osservato ed e' gia' esaurito,
    # vale la pena controllare.
    soglia_giorni_anticipo = round(orizzonte_giorni * 0.66)
    anomalie = []

    for voce in situazione:
        if voce["disponibilita_pct"] is None:
            continue

        if voce["disponibilita_pct"] <= 5 and voce["giorni_al_target"] > soglia_giorni_anticipo:
            anomalie.append({
                "voce": voce,
                "motivo": "Esaurimento troppo anticipato",
                "dettaglio": (
                    f"Disponibilita' gia' al {voce['disponibilita_pct']:.0f}% a ben "
                    f"{voce['giorni_al_target']} giorni dalla data: verificare che non sia un errore "
                    "di inserimento o una chiusura canale dimenticata."
                ),
            })

        if voce["giorni_al_target"] <= 3 and voce["disponibilita_pct"] > 60:
            anomalie.append({
                "voce": voce,
                "motivo": "Disponibilita' ancora alta a ridosso della data",
                "dettaglio": (
                    f"Mancano solo {voce['giorni_al_target']} giorni ma e' ancora disponibile il "
                    f"{voce['disponibilita_pct']:.0f}% delle unita': valutare un'azione commerciale."
                ),
            })

    return anomalie


# ---------------------------------------------------------------------------
# d) Motore di suggerimenti a regole parametriche
# ---------------------------------------------------------------------------

def registra_decisione(conn, oggi_data, voce, decision_type, suggestion, reasoning, urgenza, dati_extra=None):
    dati = {
        "disponibilita_pct": voce.get("disponibilita_pct"),
        "disponibili": voce.get("disponibili"),
        "prezzo": voce.get("prezzo"),
        "pickup": voce.get("pickup"),
        "giorni_al_target": voce.get("giorni_al_target"),
        "urgenza": urgenza,
    }
    if dati_extra:
        dati.update(dati_extra)

    conn.execute(
        """
        INSERT INTO decision (created_ts, target_date, capacity_unit_id, decision_type, suggestion, reasoning, data_snapshot_json, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            formatta_data(voce["data"]),
            voce.get("unita_id"),
            decision_type,
            suggestion,
            reasoning,
            json.dumps(dati, ensure_ascii=False),
        ),
    )


def genera_suggerimenti(conn, situazione, config, oggi_data, orizzonte_giorni):
    soglie = config["rules_thresholds"]
    suggerimenti = []

    for voce in situazione:
        if voce["disponibilita_pct"] is None:
            continue

        s = soglie["aumento_prezzo"]
        if (
            voce["disponibili"] > 0
            and voce["disponibilita_pct"] < s["disponibilita_max_pct"]
            and voce["giorni_al_target"] > s["giorni_minimi"]
        ):
            urgenza = "alta" if voce["disponibilita_pct"] < s["disponibilita_max_pct"] / 2 else "media"
            testo = f"Aumentare il prezzo del {s['aumento_pct']}%"
            motivo = (
                f"Per {voce['unita_name']} il {formatta_data_estesa(voce['data'])} restano solo "
                f"{voce['disponibili']} unita' su {voce['unita_totale']} ({voce['disponibilita_pct']:.0f}%), "
                f"con ancora {voce['giorni_al_target']} giorni di anticipo: la domanda e' sostenuta, "
                "c'e' margine per vendere le unita' rimaste a un prezzo piu' alto."
            )
            suggerimenti.append({"voce": voce, "tipo": "aumento_prezzo", "azione": testo, "motivo": motivo, "urgenza": urgenza})
            registra_decisione(conn, oggi_data, voce, "aumento_prezzo", testo, motivo, urgenza)

        s = soglie["ribasso_o_promo"]
        if (
            voce["pickup"] is not None
            and voce["pickup"] <= 0
            and voce["disponibilita_pct"] > s["disponibilita_min_pct"]
            and 0 <= voce["giorni_al_target"] < s["giorni_massimi"]
        ):
            urgenza = "alta" if voce["giorni_al_target"] <= 3 else "media"
            testo = "Valutare un ribasso di prezzo o una promozione mirata"
            motivo = (
                f"Per {voce['unita_name']} il {formatta_data_estesa(voce['data'])} il pickup degli ultimi "
                f"{PICKUP_GIORNI} giorni e' fermo (variazione: {voce['pickup']} unita'), con disponibilita' "
                f"ancora al {voce['disponibilita_pct']:.0f}% e solo {voce['giorni_al_target']} giorni alla data: "
                "rischio concreto di rimanere con invenduto."
            )
            suggerimenti.append({"voce": voce, "tipo": "ribasso_promo", "azione": testo, "motivo": motivo, "urgenza": urgenza})
            registra_decisione(conn, oggi_data, voce, "ribasso_promo", testo, motivo, urgenza)

        s = soglie["chiusura_ota"]
        if voce["disponibilita_pct"] < s["disponibilita_critica_pct"]:
            urgenza = "alta"
            testo = "Chiudere i canali OTA e vendere solo sul canale diretto"
            motivo = (
                f"Per {voce['unita_name']} il {formatta_data_estesa(voce['data'])} la disponibilita' e' "
                f"critica ({voce['disponibilita_pct']:.0f}%, solo {voce['disponibili']} unita' rimaste): "
                "conviene evitare le commissioni delle OTA e vendere le ultime unita' direttamente."
            )
            suggerimenti.append({"voce": voce, "tipo": "chiusura_ota", "azione": testo, "motivo": motivo, "urgenza": urgenza})
            registra_decisione(conn, oggi_data, voce, "chiusura_ota", testo, motivo, urgenza)

    oggi_str = formatta_data(oggi_data)
    fine_str = formatta_data(oggi_data + timedelta(days=orizzonte_giorni - 1))
    segnali = conn.execute(
        """
        SELECT signal_date, signal_type, description, impact_score
        FROM external_signal
        WHERE signal_date BETWEEN ? AND ? AND impact_score > 0
        ORDER BY signal_date
        """,
        (oggi_str, fine_str),
    ).fetchall()

    for segnale in segnali:
        data_segnale = datetime.strptime(segnale["signal_date"], "%Y-%m-%d").date()
        impatto = segnale["impact_score"]
        urgenza = "alta" if impatto >= 8 else "media" if impatto >= 5 else "bassa"
        testo = "Opportunita' da evento: valutare aumento prezzi / riduzione disponibilita' su canali scontati"
        motivo = (
            f"Il {formatta_data_estesa(data_segnale)} e' segnalato '{segnale['description']}' "
            f"({segnale['signal_type']}, impatto stimato {impatto:.0f}/10 sulla domanda)."
        )
        voce_finta = {
            "data": data_segnale, "giorni_al_target": (data_segnale - oggi_data).days,
            "unita_id": None, "unita_name": "tutte le tipologie", "unita_totale": None,
            "disponibilita_pct": None, "disponibili": None, "prezzo": None, "pickup": None,
        }
        suggerimenti.append({"voce": voce_finta, "tipo": "opportunita_evento", "azione": testo, "motivo": motivo, "urgenza": urgenza})
        registra_decisione(
            conn, oggi_data, voce_finta, "opportunita_evento", testo, motivo, urgenza,
            dati_extra={"evento": segnale["description"], "impact_score": impatto},
        )

    conn.commit()
    suggerimenti.sort(key=lambda s: (ORDINE_URGENZA.get(s["urgenza"], 9), s["voce"]["data"]))
    return suggerimenti


# ---------------------------------------------------------------------------
# e) Playbook del revenue manager (analisi dei rate_event dell'autore RM)
# ---------------------------------------------------------------------------

def analizza_playbook_rm(conn, autore="RM"):
    righe = conn.execute(
        """
        SELECT event_ts, target_date, price_before, price_after, channel
        FROM rate_event
        WHERE author = ?
        ORDER BY event_ts
        """,
        (autore,),
    ).fetchall()

    if not righe:
        return None

    aumenti, ribassi = [], []
    canali = Counter()

    for riga in righe:
        prima, dopo = riga["price_before"], riga["price_after"]
        canali[riga["channel"]] += 1
        if not prima:
            continue
        variazione = (dopo - prima) / prima * 100
        data_evento = datetime.strptime(riga["event_ts"][:10], "%Y-%m-%d").date()
        data_target = datetime.strptime(riga["target_date"], "%Y-%m-%d").date()
        anticipo = (data_target - data_evento).days
        if variazione > 0:
            aumenti.append((variazione, anticipo))
        elif variazione < 0:
            ribassi.append((variazione, anticipo))

    def _media(valori):
        return sum(valori) / len(valori) if valori else None

    return {
        "totale_eventi": len(righe),
        "n_aumenti": len(aumenti),
        "n_ribassi": len(ribassi),
        "aumento_medio_pct": _media([v for v, _ in aumenti]),
        "ribasso_medio_pct": _media([v for v, _ in ribassi]),
        "anticipo_medio_aumenti": _media([a for _, a in aumenti]),
        "anticipo_medio_ribassi": _media([a for _, a in ribassi]),
        "canale_piu_frequente": canali.most_common(1)[0][0] if canali else None,
    }


# ---------------------------------------------------------------------------
# Generazione HTML
# ---------------------------------------------------------------------------

def _classe_disponibilita(pct):
    if pct is None:
        return ""
    if pct < 15:
        return "critico"
    if pct < 40:
        return "attenzione"
    return "ok"


def _badge_urgenza(urgenza):
    return f'<span class="badge badge-{html.escape(urgenza)}">{html.escape(urgenza.upper())}</span>'


def _num(valore, decimali=0, suffisso=""):
    if valore is None:
        return "N/D"
    return f"{valore:,.{decimali}f}{suffisso}".replace(",", "X").replace(".", ",").replace("X", ".")


def costruisci_html(config, oggi_data, situazione, confronto, anomalie, suggerimenti, playbook, orizzonte_giorni):
    nome_struttura = html.escape(config["structure_name"])
    generato_il = datetime.now().strftime("%d/%m/%Y alle %H:%M")

    # --- sezione a: situazione prossimi giorni -----------------------------
    righe_situazione = ""
    for voce in situazione:
        classe = _classe_disponibilita(voce["disponibilita_pct"])
        pickup_testo = "N/D" if voce["pickup"] is None else f"{voce['pickup']:+d}"
        if voce["disponibili"] is None:
            disponibili_testo = "N/D"
        else:
            disponibili_testo = f"{voce['disponibili']}/{voce['unita_totale']}"
        prezzo_testo = "N/D" if voce["prezzo"] is None else _num(voce["prezzo"], 0, " EUR")
        righe_situazione += (
            "<tr>"
            f"<td>{formatta_data(voce['data'])}</td>"
            f"<td>{html.escape(voce['unita_name'])} ({html.escape(voce['unita_code'])})</td>"
            f"<td>{disponibili_testo}</td>"
            f"<td class='{classe}'>{_num(voce['disponibilita_pct'], 0, '%')}</td>"
            f"<td>{prezzo_testo}</td>"
            f"<td>{pickup_testo}</td>"
            "</tr>\n"
        )

    # --- sezione b: confronto anno precedente ------------------------------
    tc, tp = confronto["totale_corrente"], confronto["totale_precedente"]
    var_prenotazioni = variazione_pct(tc["n_prenotazioni"], tp["n_prenotazioni"])
    var_notti = variazione_pct(tc["notti"], tp["notti"])
    var_ricavo = variazione_pct(tc["ricavo"], tp["ricavo"])
    var_prezzo = variazione_pct(tc["prezzo_medio_notte"] or 0, tp["prezzo_medio_notte"] or 0)

    inizio_c, fine_c = confronto["periodo_corrente"]
    inizio_p, fine_p = confronto["periodo_precedente"]

    righe_unita_confronto = ""
    for voce in confronto["per_unita"]:
        u, c, p = voce["unita"], voce["corrente"], voce["precedente"]
        righe_unita_confronto += (
            "<tr>"
            f"<td>{html.escape(u['name'])} ({html.escape(u['code'])})</td>"
            f"<td>{c['n_prenotazioni']}</td><td>{p['n_prenotazioni']}</td>"
            f"<td>{c['notti']}</td><td>{p['notti']}</td>"
            f"<td>{_num(c['ricavo'], 0, ' EUR')}</td><td>{_num(p['ricavo'], 0, ' EUR')}</td>"
            "</tr>\n"
        )

    # --- sezione c: anomalie ------------------------------------------------
    if anomalie:
        righe_anomalie = ""
        for a in anomalie:
            v = a["voce"]
            righe_anomalie += (
                "<div class='riquadro-anomalia'>"
                f"<strong>{formatta_data(v['data'])} - {html.escape(v['unita_name'])}</strong>: "
                f"{html.escape(a['motivo'])}<br><span class='dettaglio'>{html.escape(a['dettaglio'])}</span>"
                "</div>\n"
            )
    else:
        righe_anomalie = "<p>Nessuna anomalia rilevata nei prossimi giorni.</p>"

    # --- sezione d: suggerimenti --------------------------------------------
    if suggerimenti:
        righe_suggerimenti = ""
        for s in suggerimenti:
            v = s["voce"]
            righe_suggerimenti += (
                "<div class='riquadro-suggerimento'>"
                f"{_badge_urgenza(s['urgenza'])} "
                f"<strong>{formatta_data(v['data'])} - {html.escape(v['unita_name'])}</strong>: "
                f"{html.escape(s['azione'])}"
                f"<div class='dettaglio'>{html.escape(s['motivo'])}</div>"
                "</div>\n"
            )
    else:
        righe_suggerimenti = "<p>Nessun suggerimento generato: la situazione risulta sotto controllo secondo le regole attuali.</p>"

    n_alta = sum(1 for s in suggerimenti if s["urgenza"] == "alta")

    # --- sezione e: playbook RM ---------------------------------------------
    if playbook is None:
        blocco_playbook = "<p>Non ci sono ancora variazioni di prezzo registrate dall'autore 'RM'.</p>"
    else:
        blocco_playbook = f"""
        <ul>
          <li>Variazioni di prezzo totali registrate: <strong>{playbook['totale_eventi']}</strong></li>
          <li>Aumenti: <strong>{playbook['n_aumenti']}</strong>
              (variazione media: {_num(playbook['aumento_medio_pct'], 1, '%')},
               con anticipo medio di {_num(playbook['anticipo_medio_aumenti'], 0, ' giorni')})</li>
          <li>Ribassi: <strong>{playbook['n_ribassi']}</strong>
              (variazione media: {_num(playbook['ribasso_medio_pct'], 1, '%')},
               con anticipo medio di {_num(playbook['anticipo_medio_ribassi'], 0, ' giorni')})</li>
          <li>Canale piu' spesso interessato dalle modifiche:
              <strong>{html.escape(playbook['canale_piu_frequente'] or 'N/D')}</strong></li>
        </ul>
        <p class='dettaglio'>
          In sintesi: quando alza i prezzi, "RM" lo fa tipicamente con
          {_num(playbook['anticipo_medio_aumenti'], 0)} giorni di anticipo sulla data e di circa
          {_num(playbook['aumento_medio_pct'], 1)}%; quando li abbassa lo fa piu' a ridosso della data
          (circa {_num(playbook['anticipo_medio_ribassi'], 0)} giorni prima), di circa
          {_num(abs(playbook['ribasso_medio_pct']) if playbook['ribasso_medio_pct'] else None, 1)}%.
        </p>
        """

    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>Report mattina - {nome_struttura}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0; padding: 0; background: #f4f5f7; color: #1f2933; }}
  .contenitore {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
  header.intestazione {{ background: #10334f; color: #fff; padding: 24px; }}
  header.intestazione h1 {{ margin: 0 0 6px 0; font-size: 22px; }}
  header.intestazione p {{ margin: 0; opacity: 0.85; font-size: 14px; }}
  h2 {{ border-bottom: 2px solid #10334f; padding-bottom: 6px; margin-top: 36px; font-size: 18px; color: #10334f; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 12px; font-size: 13px; background: #fff; }}
  th, td {{ border: 1px solid #e0e3e8; padding: 6px 10px; text-align: left; }}
  th {{ background: #eef1f5; }}
  tr:nth-child(even) {{ background: #fafbfc; }}
  td.ok {{ color: #1a7f37; font-weight: bold; }}
  td.attenzione {{ color: #9a6700; font-weight: bold; }}
  td.critico {{ color: #cf222e; font-weight: bold; }}
  .riepilogo {{ background: #fff; border-left: 4px solid #10334f; padding: 12px 16px; margin-top: 16px; }}
  .riquadro-anomalia {{ background: #fff8e6; border-left: 4px solid #9a6700; padding: 10px 14px; margin-bottom: 8px; }}
  .riquadro-suggerimento {{ background: #fff; border: 1px solid #e0e3e8; border-left: 4px solid #10334f; padding: 10px 14px; margin-bottom: 8px; }}
  .dettaglio {{ color: #57606a; font-size: 13px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: bold; color: #fff; }}
  .badge-alta {{ background: #cf222e; }}
  .badge-media {{ background: #9a6700; }}
  .badge-bassa {{ background: #57606a; }}
  footer {{ margin-top: 40px; color: #8a94a1; font-size: 12px; text-align: center; padding: 20px; }}
</style>
</head>
<body>
<header class="intestazione">
  <h1>Report del mattino - {nome_struttura}</h1>
  <p>Generato il {generato_il} &middot; orizzonte {orizzonte_giorni} giorni a partire da {formatta_data_estesa(oggi_data)}</p>
</header>
<div class="contenitore">

  <div class="riepilogo">
    <strong>In sintesi:</strong> {len(suggerimenti)} suggerimenti generati ({n_alta} a priorita' alta),
    {len(anomalie)} date da controllare come anomale.
  </div>

  <h2>a) Situazione prossimi {orizzonte_giorni} giorni</h2>
  <table>
    <tr><th>Data</th><th>Tipologia</th><th>Disponibili</th><th>Disp. %</th><th>Prezzo pubblicato</th><th>Pickup ~{PICKUP_GIORNI}gg</th></tr>
    {righe_situazione}
  </table>

  <h2>b) Confronto con lo stesso periodo dell'anno precedente</h2>
  <p class="dettaglio">
    Periodo corrente: {formatta_data(inizio_c)} &rarr; {formatta_data(fine_c)}
    (prenotazioni fatte finora).
    Periodo di confronto (anno precedente): {formatta_data(inizio_p)} &rarr; {formatta_data(fine_p)}
    (prenotazioni fatte entro lo stesso numero di giorni prima dell'arrivo, per un confronto equo).
  </p>
  <table>
    <tr><th></th><th>Anno corrente</th><th>Anno precedente</th><th>Variazione</th></tr>
    <tr><td>Prenotazioni confermate</td><td>{tc['n_prenotazioni']}</td><td>{tp['n_prenotazioni']}</td><td>{_num(var_prenotazioni, 1, '%')}</td></tr>
    <tr><td>Notti vendute</td><td>{tc['notti']}</td><td>{tp['notti']}</td><td>{_num(var_notti, 1, '%')}</td></tr>
    <tr><td>Ricavo totale</td><td>{_num(tc['ricavo'], 0, ' EUR')}</td><td>{_num(tp['ricavo'], 0, ' EUR')}</td><td>{_num(var_ricavo, 1, '%')}</td></tr>
    <tr><td>Prezzo medio a notte</td><td>{_num(tc['prezzo_medio_notte'], 0, ' EUR')}</td><td>{_num(tp['prezzo_medio_notte'], 0, ' EUR')}</td><td>{_num(var_prezzo, 1, '%')}</td></tr>
  </table>
  <table>
    <tr><th>Tipologia</th><th>Prenotazioni (corrente)</th><th>Prenotazioni (anno prec.)</th><th>Notti (corrente)</th><th>Notti (anno prec.)</th><th>Ricavo (corrente)</th><th>Ricavo (anno prec.)</th></tr>
    {righe_unita_confronto}
  </table>

  <h2>c) Date anomale da controllare</h2>
  {righe_anomalie}

  <h2>d) Suggerimenti</h2>
  {righe_suggerimenti}

  <h2>e) Playbook del revenue manager (storico variazioni prezzo di "RM")</h2>
  {blocco_playbook}

</div>
<footer>revenue-vault &middot; report generato in locale, nessun dato inviato all'esterno</footer>
</body>
</html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Genera il report mattutino in report/report_oggi.html.")
    parser.add_argument("--db", default=db.NOME_DB_DEFAULT, help="percorso del file database (default: vault.db)")
    parser.add_argument("--config", default="config.json", help="percorso del file di configurazione (default: config.json)")
    parser.add_argument("--output", default="report/report_oggi.html", help="percorso del file HTML da generare")
    parser.add_argument("--giorni", type=int, default=ORIZZONTE_GIORNI_DEFAULT, help="orizzonte in giorni del report (default: 30)")
    args = parser.parse_args(argv)

    if not db.db_esiste(args.db):
        print(f"[ERRORE] il database '{args.db}' non esiste ancora. Esegui prima 'python3 init_db.py'.", file=sys.stderr)
        return 1

    config = carica_config(args.config)
    conn = db.connetti(args.db)
    capacity_units = [dict(riga) for riga in db.elenco_capacity_units(conn)]
    if not capacity_units:
        print("[ERRORE] il database non contiene ancora tipologie. Esegui prima 'python3 init_db.py'.", file=sys.stderr)
        return 1

    oggi_data = oggi()

    print("Analisi della situazione in corso...")
    situazione = costruisci_situazione(conn, capacity_units, oggi_data, args.giorni)
    confronto = confronto_anno_precedente(conn, capacity_units, oggi_data, args.giorni)
    anomalie = rileva_anomalie(situazione, args.giorni)
    suggerimenti = genera_suggerimenti(conn, situazione, config, oggi_data, args.giorni)
    playbook = analizza_playbook_rm(conn)

    html_report = costruisci_html(config, oggi_data, situazione, confronto, anomalie, suggerimenti, playbook, args.giorni)

    percorso_output = Path(args.output)
    percorso_output.parent.mkdir(parents=True, exist_ok=True)
    percorso_output.write_text(html_report, encoding="utf-8")

    conn.close()

    print()
    print(f"Report generato: {percorso_output.resolve()}")
    print(f"Suggerimenti: {len(suggerimenti)} (di cui {sum(1 for s in suggerimenti if s['urgenza'] == 'alta')} a priorita' alta)")
    print(f"Anomalie rilevate: {len(anomalie)}")
    print("Apri il file con un browser per consultarlo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
