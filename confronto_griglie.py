#!/usr/bin/env python3
"""
confronto_griglie.py
-----------------------
Implementa la regola P2-R12 di docs/SPECIFICA_MOTORE.md: "confronto_griglie
| date allineate, per tipologia: nostra griglia vs griglia RM 2027 -> delta
per data/fascia/mese, sintesi per la decisione di dicembre. Modulo
autonomo, input = due CSV con stesso schema."

In pratica la griglia del revenue manager arriva quasi sempre come
trascrizione manuale (un foglio con solo data, tipologia e prezzo), non
con lo schema C2 completo: questo script quindi tratta il PRIMO file come
la nostra griglia con lo schema C2 completo (vedi docs/SPECIFICA_MOTORE.md,
sezione C2: data, giorno, tipologia, codice, trattamento, prezzo_eur,
fascia, unita_totali, unita_scarsa) e il SECONDO in modo tollerante:
bastano data, prezzo_eur e un modo qualunque di identificare la tipologia
(tipologia o codice, anche solo uno dei due).

Non e' un importatore: non scrive nel database. E' un modulo autonomo che
legge due CSV e produce due output:
  (a) un CSV di delta, una riga per ogni data+tipologia presente in
      entrambe le griglie (prezzo_nostro, prezzo_rm, delta_eur, delta_pct,
      fascia);
  (b) un report HTML con la sintesi per mese e per tipologia, le 15
      divergenze maggiori in valore assoluto, e i conteggi "noi piu'
      alti / piu' bassi / uguali" per fascia.

Uso:
    python3 confronto_griglie.py nostra_griglia.csv griglia_rm.csv
    python3 confronto_griglie.py nostra_griglia.csv griglia_rm.csv \
        --output-csv delta.csv --output-html confronto.html
"""

import argparse
import csv
import html
import sys
from collections import defaultdict
from pathlib import Path

from importa_log_disponibilita import (
    normalizza_intestazione,
    parse_data_flessibile,
    parse_numero_flessibile,
    rileva_separatore,
)

# Le 9 colonne dello schema C2 (docs/SPECIFICA_MOTORE.md). Nessuna e'
# obbligatoria in senso stretto per QUESTO script (che deve restare
# tollerante anche sul primo file): quello che serve davvero per fare un
# confronto e' avere una data, un prezzo e un modo di identificare la
# tipologia. Le altre colonne, se presenti, arricchiscono l'output.
COLONNE_C2 = ["data", "giorno", "tipologia", "codice", "trattamento", "prezzo_eur", "fascia", "unita_totali", "unita_scarsa"]
COLONNE_MINIME = ["data", "prezzo_eur"]

EPSILON_UGUAGLIANZA_EUR = 0.005  # sotto questa soglia due prezzi si considerano uguali (arrotondamento)


# ---------------------------------------------------------------------------
# Caricamento CSV (tollerante: usato sia per la nostra griglia sia per
# quella del RM, che tipicamente ha molte meno colonne)
# ---------------------------------------------------------------------------

def carica_griglia(percorso):
    """Legge un CSV di griglia tariffaria in modo tollerante: separatore
    rilevato automaticamente, date in formato italiano o ISO, numeri con
    virgola o punto. Richiede solo 'data' e 'prezzo_eur'; tutte le altre
    colonne dello schema C2 sono lette se presenti, altrimenti restano
    None. Restituisce (righe_valide, righe_scartate) dove ogni riga
    scartata e' (numero_riga, motivo)."""
    cammino = Path(percorso)
    if not cammino.exists():
        raise FileNotFoundError(f"il file '{percorso}' non esiste")

    testo = cammino.read_text(encoding="utf-8-sig")
    righe_grezze = testo.splitlines()
    if not righe_grezze:
        raise ValueError(f"il file '{percorso}' e' vuoto")

    separatore = rileva_separatore(righe_grezze[0])
    lettore = csv.reader(righe_grezze, delimiter=separatore)
    intestazione = [normalizza_intestazione(c) for c in next(lettore)]

    mancanti = [c for c in COLONNE_MINIME if c not in intestazione]
    if mancanti:
        raise ValueError(
            f"al file '{percorso}' mancano le colonne minime {', '.join(mancanti)} "
            f"(intestazione trovata: {', '.join(intestazione)})"
        )
    if "tipologia" not in intestazione and "codice" not in intestazione:
        raise ValueError(
            f"il file '{percorso}' non ha ne' 'tipologia' ne' 'codice': "
            "serve almeno una delle due colonne per identificare la tipologia"
        )

    righe_valide = []
    righe_scartate = []
    numero_riga = 1  # riga 1 = intestazione
    for valori in lettore:
        numero_riga += 1
        if not valori or all(not (v or "").strip() for v in valori):
            continue  # riga vuota, ignorata silenziosamente (non e' un errore)
        riga = {intestazione[i]: (valori[i] if i < len(valori) else "") for i in range(len(intestazione))}

        # una eventuale intestazione ripetuta in mezzo al file (es. file
        # incollati insieme) va riconosciuta e ignorata, non scartata come errore
        if normalizza_intestazione(riga.get("data", "")) == "data":
            continue

        try:
            data_riga = parse_data_flessibile(riga.get("data"))
        except ValueError as exc:
            righe_scartate.append((numero_riga, f"data non valida: {exc}"))
            continue

        try:
            prezzo = parse_numero_flessibile(riga.get("prezzo_eur"))
        except ValueError as exc:
            righe_scartate.append((numero_riga, str(exc)))
            continue
        if prezzo is None:
            righe_scartate.append((numero_riga, "prezzo_eur mancante"))
            continue

        tipologia = (riga.get("tipologia") or "").strip()
        codice = (riga.get("codice") or "").strip().upper()
        if not tipologia and not codice:
            righe_scartate.append((numero_riga, "ne' tipologia ne' codice presenti in questa riga"))
            continue

        righe_valide.append({
            "data": data_riga,
            "giorno": (riga.get("giorno") or "").strip() or None,
            "tipologia": tipologia or None,
            "codice": codice or None,
            "trattamento": (riga.get("trattamento") or "").strip() or None,
            "prezzo_eur": prezzo,
            "fascia": (riga.get("fascia") or "").strip().upper() or None,
            "unita_totali": (riga.get("unita_totali") or "").strip() or None,
            "unita_scarsa": (riga.get("unita_scarsa") or "").strip() or None,
            "_riga_originale": numero_riga,
        })

    return righe_valide, righe_scartate


# ---------------------------------------------------------------------------
# Allineamento delle due griglie (per data + tipologia)
# ---------------------------------------------------------------------------

def costruisci_mappa_tipologie(righe_nostra):
    """La nostra griglia (schema C2) ha sia il nome per esteso sia il
    codice: costruisce da li' una mappa nome->codice, cosi' che se la
    griglia del RM identifica le tipologie solo per nome si possa comunque
    allineare alla stessa chiave della nostra."""
    mappa = {}
    for riga in righe_nostra:
        if riga["tipologia"] and riga["codice"]:
            mappa[riga["tipologia"].strip().upper()] = riga["codice"]
    return mappa


def chiave_tipologia(riga, mappa_nome_a_codice):
    """Restituisce una chiave univoca per la tipologia di una riga,
    preferendo il codice quando c'e'; se la riga ha solo il nome, cerca il
    codice corrispondente nella mappa costruita dalla nostra griglia."""
    if riga["codice"]:
        return riga["codice"]
    if riga["tipologia"]:
        nome_norm = riga["tipologia"].strip().upper()
        return mappa_nome_a_codice.get(nome_norm, nome_norm)
    return None


def allinea_griglie(righe_nostra, righe_rm):
    """Abbina le righe delle due griglie per (data, tipologia). Restituisce
    (righe_delta, solo_nostra, solo_rm) dove righe_delta contiene solo le
    combinazioni presenti in entrambi i file."""
    mappa_nomi = costruisci_mappa_tipologie(righe_nostra)

    indice_nostra = {}
    for riga in righe_nostra:
        chiave = chiave_tipologia(riga, mappa_nomi)
        if chiave is None:
            continue
        indice_nostra[(riga["data"], chiave)] = riga

    indice_rm = {}
    for riga in righe_rm:
        chiave = chiave_tipologia(riga, mappa_nomi)
        if chiave is None:
            continue
        indice_rm[(riga["data"], chiave)] = riga

    chiavi_comuni = sorted(set(indice_nostra) & set(indice_rm))
    righe_delta = []
    for data_riga, chiave in chiavi_comuni:
        nostra = indice_nostra[(data_riga, chiave)]
        rm = indice_rm[(data_riga, chiave)]
        prezzo_nostro = nostra["prezzo_eur"]
        prezzo_rm = rm["prezzo_eur"]
        delta_eur = round(prezzo_nostro - prezzo_rm, 2)
        delta_pct = round(delta_eur / prezzo_rm * 100, 1) if prezzo_rm else None
        righe_delta.append({
            "data": data_riga,
            "tipologia": nostra["tipologia"] or chiave,
            "codice": chiave,
            "prezzo_nostro": prezzo_nostro,
            "prezzo_rm": prezzo_rm,
            "delta_eur": delta_eur,
            "delta_pct": delta_pct,
            "fascia": nostra["fascia"],
            "trattamento_nostro": nostra["trattamento"],
            "trattamento_rm": rm["trattamento"],
        })

    solo_nostra = sorted(set(indice_nostra) - set(indice_rm))
    solo_rm = sorted(set(indice_rm) - set(indice_nostra))
    return righe_delta, solo_nostra, solo_rm


# ---------------------------------------------------------------------------
# Output (a): CSV di delta
# ---------------------------------------------------------------------------

def scrivi_csv_delta(percorso, righe_delta):
    with open(percorso, "w", newline="", encoding="utf-8") as f:
        scrittore = csv.writer(f)
        scrittore.writerow(["data", "tipologia", "codice", "prezzo_nostro", "prezzo_rm", "delta_eur", "delta_pct", "fascia"])
        for r in sorted(righe_delta, key=lambda r: (r["data"], r["codice"])):
            scrittore.writerow([
                r["data"].strftime("%Y-%m-%d"),
                r["tipologia"] or "",
                r["codice"] or "",
                f"{r['prezzo_nostro']:.2f}",
                f"{r['prezzo_rm']:.2f}",
                f"{r['delta_eur']:.2f}",
                "" if r["delta_pct"] is None else f"{r['delta_pct']:.1f}",
                r["fascia"] or "",
            ])


# ---------------------------------------------------------------------------
# Sintesi statistiche per il report HTML
# ---------------------------------------------------------------------------

def _media(valori):
    return sum(valori) / len(valori) if valori else None


def sintesi_per_gruppo(righe_delta, chiave_gruppo):
    gruppi = defaultdict(list)
    for r in righe_delta:
        gruppi[chiave_gruppo(r)].append(r)

    risultato = []
    for chiave in sorted(gruppi):
        righe = gruppi[chiave]
        nostri = [r["prezzo_nostro"] for r in righe]
        rm = [r["prezzo_rm"] for r in righe]
        delta = [r["delta_eur"] for r in righe]
        risultato.append({
            "chiave": chiave,
            "n": len(righe),
            "media_nostro": _media(nostri),
            "media_rm": _media(rm),
            "media_delta_eur": _media(delta),
            "totale_nostro": sum(nostri),
            "totale_rm": sum(rm),
        })
    return risultato


def conteggi_per_fascia(righe_delta):
    conteggi = defaultdict(lambda: {"piu_alti": 0, "piu_bassi": 0, "uguali": 0})
    for r in righe_delta:
        fascia = r["fascia"] or "N/D"
        if r["delta_eur"] > EPSILON_UGUAGLIANZA_EUR:
            conteggi[fascia]["piu_alti"] += 1
        elif r["delta_eur"] < -EPSILON_UGUAGLIANZA_EUR:
            conteggi[fascia]["piu_bassi"] += 1
        else:
            conteggi[fascia]["uguali"] += 1
    return dict(sorted(conteggi.items()))


def analizza_trattamenti(righe_delta):
    """Confronta la colonna 'trattamento' tra le due griglie, se
    disponibile in entrambe. Restituisce un messaggio pronto per il report."""
    con_trattamento_rm = [r for r in righe_delta if r["trattamento_rm"] is not None]
    if not con_trattamento_rm:
        return "Il file della griglia RM non include (o non valorizza) la colonna 'trattamento': confronto non possibile."

    divergenti = [r for r in con_trattamento_rm if (r["trattamento_nostro"] or "").strip().upper() != (r["trattamento_rm"] or "").strip().upper()]
    if not divergenti:
        return f"Trattamento confrontabile su {len(con_trattamento_rm)} righe: nessuna differenza trovata."

    esempi = "; ".join(
        f"{r['data'].strftime('%d/%m/%Y')} {r['codice']} (noi: {r['trattamento_nostro'] or 'N/D'}, RM: {r['trattamento_rm']})"
        for r in divergenti[:5]
    )
    altre = f" (+altre {len(divergenti) - 5})" if len(divergenti) > 5 else ""
    return f"Trattamento DIVERSO su {len(divergenti)} righe su {len(con_trattamento_rm)} confrontabili: {esempi}{altre}"


# ---------------------------------------------------------------------------
# Output (b): report HTML
# ---------------------------------------------------------------------------

def _num(valore, decimali=2, suffisso=""):
    if valore is None:
        return "N/D"
    return f"{valore:,.{decimali}f}{suffisso}".replace(",", "X").replace(".", ",").replace("X", ".")


def costruisci_report_html(righe_delta, solo_nostra, solo_rm, nota_trattamento, percorso_nostra, percorso_rm):
    n_totale = len(righe_delta)
    if n_totale == 0:
        corpo_vuoto = "<p>Nessuna combinazione data+tipologia e' presente in entrambe le griglie: impossibile calcolare un confronto.</p>"
    else:
        corpo_vuoto = ""

    per_mese = sintesi_per_gruppo(righe_delta, lambda r: r["data"].strftime("%Y-%m"))
    per_tipologia = sintesi_per_gruppo(righe_delta, lambda r: r["codice"])
    top15 = sorted(righe_delta, key=lambda r: abs(r["delta_eur"]), reverse=True)[:15]
    conteggi_fascia = conteggi_per_fascia(righe_delta)

    def righe_tabella_gruppo(gruppi):
        out = ""
        for g in gruppi:
            out += (
                "<tr>"
                f"<td>{html.escape(str(g['chiave']))}</td>"
                f"<td>{g['n']}</td>"
                f"<td>{_num(g['media_nostro'], 0, ' EUR')}</td>"
                f"<td>{_num(g['media_rm'], 0, ' EUR')}</td>"
                f"<td>{_num(g['media_delta_eur'], 1, ' EUR')}</td>"
                f"<td>{_num(g['totale_nostro'], 0, ' EUR')}</td>"
                f"<td>{_num(g['totale_rm'], 0, ' EUR')}</td>"
                "</tr>\n"
            )
        return out

    righe_top15 = ""
    for r in top15:
        segno = "+" if r["delta_eur"] > 0 else ""
        righe_top15 += (
            "<tr>"
            f"<td>{r['data'].strftime('%d/%m/%Y')}</td>"
            f"<td>{html.escape(r['tipologia'] or r['codice'])}</td>"
            f"<td>{_num(r['prezzo_nostro'], 0, ' EUR')}</td>"
            f"<td>{_num(r['prezzo_rm'], 0, ' EUR')}</td>"
            f"<td>{segno}{_num(r['delta_eur'], 2, ' EUR')}</td>"
            f"<td>{'' if r['delta_pct'] is None else segno + _num(r['delta_pct'], 1, '%')}</td>"
            f"<td>{html.escape(r['fascia'] or 'N/D')}</td>"
            "</tr>\n"
        )

    righe_fascia = ""
    for fascia, c in conteggi_fascia.items():
        righe_fascia += (
            "<tr>"
            f"<td>{html.escape(fascia)}</td>"
            f"<td>{c['piu_alti']}</td>"
            f"<td>{c['piu_bassi']}</td>"
            f"<td>{c['uguali']}</td>"
            "</tr>\n"
        )

    nota_copertura = (
        f"<p class='dettaglio'>Confrontate {n_totale} combinazioni data+tipologia presenti in entrambi i file. "
        f"Solo nella nostra griglia: {len(solo_nostra)}. Solo nella griglia RM: {len(solo_rm)}.</p>"
    )

    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>Confronto griglie tariffarie</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0; padding: 0; background: #f4f5f7; color: #1f2933; }}
  .contenitore {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
  header {{ background: #10334f; color: #fff; padding: 20px 24px; }}
  header p {{ margin: 4px 0 0 0; font-size: 13px; opacity: .85; }}
  h2 {{ border-bottom: 2px solid #10334f; padding-bottom: 6px; margin-top: 32px; font-size: 17px; color: #10334f; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 10px; font-size: 13px; background: #fff; }}
  th, td {{ border: 1px solid #e0e3e8; padding: 6px 10px; text-align: left; }}
  th {{ background: #eef1f5; }}
  tr:nth-child(even) {{ background: #fafbfc; }}
  .dettaglio {{ color: #57606a; font-size: 13px; }}
  .nota {{ background: #fff8e6; border-left: 4px solid #9a6700; padding: 10px 14px; margin-top: 10px; font-size: 13.5px; }}
  footer {{ margin-top: 40px; color: #8a94a1; font-size: 12px; text-align: center; padding: 20px; }}
</style>
</head>
<body>
<header>
  <h1>Confronto griglie tariffarie</h1>
  <p>Nostra griglia: {html.escape(str(percorso_nostra))} &middot; Griglia RM: {html.escape(str(percorso_rm))}</p>
</header>
<div class="contenitore">
  {nota_copertura}
  {corpo_vuoto}

  <h2>Sintesi per mese</h2>
  <table>
    <tr><th>Mese</th><th>Righe</th><th>Media nostra</th><th>Media RM</th><th>Delta medio</th><th>Totale nostro</th><th>Totale RM</th></tr>
    {righe_tabella_gruppo(per_mese)}
  </table>

  <h2>Sintesi per tipologia</h2>
  <table>
    <tr><th>Tipologia</th><th>Righe</th><th>Media nostra</th><th>Media RM</th><th>Delta medio</th><th>Totale nostro</th><th>Totale RM</th></tr>
    {righe_tabella_gruppo(per_tipologia)}
  </table>

  <h2>Le 15 divergenze maggiori (valore assoluto)</h2>
  <table>
    <tr><th>Data</th><th>Tipologia</th><th>Prezzo nostro</th><th>Prezzo RM</th><th>Delta</th><th>Delta %</th><th>Fascia</th></tr>
    {righe_top15}
  </table>

  <h2>Chi e' piu' caro, per fascia</h2>
  <table>
    <tr><th>Fascia</th><th>Noi piu' alti</th><th>Noi piu' bassi</th><th>Uguali</th></tr>
    {righe_fascia}
  </table>

  <h2>Trattamento</h2>
  <div class="nota">{html.escape(nota_trattamento)}</div>

</div>
<footer>confronto_griglie.py &middot; regola P2-R12 di docs/SPECIFICA_MOTORE.md &middot; nessun dato scritto nel database</footer>
</body>
</html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Confronta la nostra griglia tariffaria con quella del revenue manager (regola R12).")
    parser.add_argument("nostra_griglia", help="CSV della nostra griglia (schema C2)")
    parser.add_argument("griglia_rm", help="CSV della griglia del RM (tollerante: bastano data, tipologia/codice, prezzo_eur)")
    parser.add_argument("--output-csv", default="confronto_griglie_delta.csv", help="percorso del CSV di delta da generare")
    parser.add_argument("--output-html", default="confronto_griglie.html", help="percorso del report HTML da generare")
    args = parser.parse_args(argv)

    try:
        righe_nostra, scartate_nostra = carica_griglia(args.nostra_griglia)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERRORE] impossibile leggere la nostra griglia: {exc}", file=sys.stderr)
        return 1

    try:
        righe_rm, scartate_rm = carica_griglia(args.griglia_rm)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERRORE] impossibile leggere la griglia RM: {exc}", file=sys.stderr)
        return 1

    print(f"Nostra griglia: {len(righe_nostra)} righe valide, {len(scartate_nostra)} scartate.")
    for numero, motivo in scartate_nostra[:15]:
        print(f"  - riga {numero}: {motivo}")
    print(f"Griglia RM: {len(righe_rm)} righe valide, {len(scartate_rm)} scartate.")
    for numero, motivo in scartate_rm[:15]:
        print(f"  - riga {numero}: {motivo}")

    righe_delta, solo_nostra, solo_rm = allinea_griglie(righe_nostra, righe_rm)
    if not righe_delta:
        print("[ERRORE] nessuna combinazione data+tipologia comune tra le due griglie: nulla da confrontare.", file=sys.stderr)
        return 1

    scrivi_csv_delta(args.output_csv, righe_delta)
    nota_trattamento = analizza_trattamenti(righe_delta)
    html_report = costruisci_report_html(righe_delta, solo_nostra, solo_rm, nota_trattamento, args.nostra_griglia, args.griglia_rm)
    Path(args.output_html).write_text(html_report, encoding="utf-8")

    print()
    print(f"Confrontate {len(righe_delta)} combinazioni data+tipologia.")
    print(f"Solo nella nostra griglia: {len(solo_nostra)}. Solo nella griglia RM: {len(solo_rm)}.")
    print(f"CSV di delta: {Path(args.output_csv).resolve()}")
    print(f"Report HTML: {Path(args.output_html).resolve()}")
    print(nota_trattamento)

    return 0


if __name__ == "__main__":
    sys.exit(main())
