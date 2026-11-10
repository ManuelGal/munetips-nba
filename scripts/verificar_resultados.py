#!/usr/bin/env python3
"""
scripts/verificar_resultados.py
--------------------------------
Auditor y Calificador Post-Partido de Apuestas Playdoit.mx
Consulta los Boxscores oficiales de ESPN para los partidos terminados y califica
automáticamente las alertas de `data/munetips_historial_clv.csv`:
- Regla Playdoit Lesión 1H: OVER es Reembolso (VOID), UNDER es Ganador (WIN).
- Normal: OVER / UNDER vs línea final real.
- Calcula Unidades Ganadas/Perdidas y Balance Neto en MXN.

Uso:
    python scripts/verificar_resultados.py [--fecha YYYY-MM-DD]
"""

import sys
import os
import json
import csv
import argparse
import urllib.request
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

def normalizar(texto: str) -> str:
    nfkd = unicodedata.normalize('NFKD', texto)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()

def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def obtener_stats_partidos_espn(fecha_str: str) -> dict:
    """
    Descarga el scoreboard y los boxscores de ESPN para una fecha (formato YYYYMMDD).
    Devuelve un diccionario {nombre_normalizado_jugador: {pts, reb, ast, min, expulsado_o_lesionado}}
    """
    fecha_espn = fecha_str.replace("-", "")
    url_sb = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={fecha_espn}"
    
    stats_jugadores = {}
    try:
        sb_data = get_json(url_sb)
    except Exception as e:
        print(f"[ERROR] Error al consultar ESPN Scoreboard ({url_sb}): {e}")
        return stats_jugadores

    eventos = sb_data.get("events", [])
    for ev in eventos:
        status = ev.get("status", {}).get("type", {})
        if not status.get("completed", False):
            continue  # El partido sigue en juego o no ha empezado
            
        ev_id = ev.get("id")
        url_summary = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={ev_id}"
        try:
            summary = get_json(url_summary)
            boxscore = summary.get("boxscore", {})
            players_blocks = boxscore.get("players", [])
            for block in players_blocks:
                team_abbr = block.get("team", {}).get("abbreviation", "")
                statistics = block.get("statistics", [])
                if not statistics:
                    continue
                stat_headers = statistics[0].get("names", [])
                # Hallar índices de MIN, PTS, REB, AST
                idx_min = stat_headers.index("MIN") if "MIN" in stat_headers else -1
                idx_pts = stat_headers.index("PTS") if "PTS" in stat_headers else -1
                idx_reb = stat_headers.index("REB") if "REB" in stat_headers else -1
                idx_ast = stat_headers.index("AST") if "AST" in stat_headers else -1
                
                athletes = statistics[0].get("athletes", [])
                for ath in athletes:
                    athlete_data = ath.get("athlete", {})
                    name = athlete_data.get("displayName", "")
                    stats_vals = ath.get("stats", [])
                    dnp = ath.get("didNotPlay", False)
                    
                    if dnp or not stats_vals:
                        stats_jugadores[normalizar(name)] = {
                            "name": name, "team": team_abbr, "min": 0, "pts": 0, "reb": 0, "ast": 0, "dnp": True
                        }
                        continue
                        
                    try:
                        m_str = stats_vals[idx_min] if idx_min >= 0 else "0"
                        m_val = float(m_str.split(":")[0]) if ":" in m_str else float(m_str or 0)
                        pts_val = float(stats_vals[idx_pts]) if idx_pts >= 0 else 0.0
                        reb_val = float(stats_vals[idx_reb]) if idx_reb >= 0 else 0.0
                        ast_val = float(stats_vals[idx_ast]) if idx_ast >= 0 else 0.0
                    except Exception:
                        continue
                        
                    stats_jugadores[normalizar(name)] = {
                        "name": name,
                        "team": team_abbr,
                        "min": m_val,
                        "pts": pts_val,
                        "reb": reb_val,
                        "ast": ast_val,
                        "dnp": False
                    }
        except Exception as e:
            print(f"[WARN] Error procesando boxscore {ev_id}: {e}")

    return stats_jugadores

def calificar_historial(fecha_str: str = None):
    base_dir = Path(__file__).resolve().parent.parent
    csv_path = base_dir / "data" / "munetips_historial_clv.csv"
    
    if not csv_path.exists():
        print(f"ℹ️ No existe el archivo {csv_path}. Aún no se han emitido alertas para calificar.")
        return

    ahora_utc = datetime.now(timezone.utc)
    fechas_objetivo = (
        [fecha_str]
        if fecha_str
        else [
            (ahora_utc - timedelta(days=1)).strftime("%Y-%m-%d"),
            ahora_utc.strftime("%Y-%m-%d"),
        ]
    )

    print(f"\n=========================================================================================")
    print(f"🏆 AUDITORÍA DE RESULTADOS Y SETTLEMENT POST-PARTIDO ({', '.join(fechas_objetivo)})")
    print(f"=========================================================================================")
    
    stats_por_fecha: dict[str, dict] = {}
    for f_obj in fechas_objetivo:
        st_dia = obtener_stats_partidos_espn(f_obj)
        if st_dia:
            stats_por_fecha[f_obj] = st_dia

    if not stats_por_fecha:
        print(f"ℹ️ No se encontraron partidos finalizados para {fechas_objetivo} en ESPN.")
        return

    filas = []
    modificado = False
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        campos = list(reader.fieldnames or [])
        for extra in ["resultado", "stat_real", "profit_mxn"]:
            if extra not in campos:
                campos.append(extra)
        for r in reader:
            filas.append(r)

    ganadas = 0
    perdidas = 0
    voids = 0
    profit_total = 0.0

    for r in filas:
        f_alerta = r.get("fecha_utc", "").split(" ")[0]
        if f_alerta not in fechas_objetivo:
            continue
        # Si ya fue calificada previamente y no se forzó una fecha específica, respetarla
        if r.get("resultado") and not fecha_str:
            continue

        # Buscar primero en la fecha UTC de la alerta; si la alerta salió tras las 00:00 UTC
        # (6:00 PM CDMX), el partido en ESPN pertenece al día calendario anterior en EE.UU./México.
        candidatos_stats = []
        if f_alerta in stats_por_fecha:
            candidatos_stats.append(stats_por_fecha[f_alerta])
        try:
            dt_prev = (datetime.strptime(f_alerta, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            if dt_prev in stats_por_fecha and stats_por_fecha[dt_prev] not in candidatos_stats:
                candidatos_stats.append(stats_por_fecha[dt_prev])
        except Exception:
            pass

        if not candidatos_stats:
            continue
            
        jugador = r.get("jugador", "")
        mercado = r.get("mercado", "")
        lado = r.get("lado", "").upper()
        linea = float(r.get("linea_playdoit", 0.0))
        cuota = float(r.get("cuota_playdoit", 1.87))
        stake = float(r.get("stake_sugerido_mxn", 20.0))
        
        j_norm = normalizar(jugador)
        st = None
        for mapa_stats in candidatos_stats:
            for k, v in mapa_stats.items():
                if k in j_norm or j_norm in k:
                    st = v
                    break
            if st:
                break
                
        if not st:
            continue

        if st["dnp"]:
            res = "VOID (DNP)"
            stat_real = 0.0
            profit = 0.0
            voids += 1
        else:
            # Seleccionar stat según mercado
            if "point" in mercado:
                stat_real = st["pts"]
            elif "rebound" in mercado:
                stat_real = st["reb"]
            elif "assist" in mercado:
                stat_real = st["ast"]
            else:
                stat_real = st["pts"]

            # Regla Playdoit: Lesión en 1H (< 24 min jugados y no volvió)
            # En nuestro modelo, si jugó menos de 18 min por lesión:
            if "UNDER" in lado:
                if stat_real < linea:
                    res = "WIN"
                    profit = stake * (cuota - 1.0)
                    ganadas += 1
                elif stat_real > linea:
                    res = "LOSS"
                    profit = -stake
                    perdidas += 1
                else:
                    res = "PUSH"
                    profit = 0.0
            else: # OVER
                if stat_real > linea:
                    res = "WIN"
                    profit = stake * (cuota - 1.0)
                    ganadas += 1
                elif 0 < st["min"] <= 14.0:
                    # Regla contractual Playdoit: si el jugador sale lesionado en 1ra mitad (<=14 min) sin cubrir el Over, es Reembolso (VOID)
                    res = "VOID (Lesión 1H)"
                    profit = 0.0
                    voids += 1
                elif stat_real < linea:
                    res = "LOSS"
                    profit = -stake
                    perdidas += 1
                else:
                    res = "PUSH"
                    profit = 0.0

        r["resultado"] = res
        r["stat_real"] = str(stat_real)
        r["profit_mxn"] = f"{profit:+.2f}"
        profit_total += profit
        modificado = True

        print(f"🏀 {jugador} ({st['team']}): {lado} {linea} {mercado}")
        print(f"   • Minutos: {st['min']} | Stat Real: {stat_real} -> {res} ({profit:+.2f} MXN)")

    if modificado:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=campos)
            writer.writeheader()
            writer.writerows(filas)
        print("-" * 95)
        total_jug = ganadas + perdidas
        wr = (ganadas / total_jug * 100) if total_jug > 0 else 0
        print(f"📊 RESUMEN: {ganadas}W - {perdidas}L - {voids}V | WinRate: {wr:.1f}% | Balance: {profit_total:+.2f} MXN")
        print("✅ Historial CSV actualizado con éxito.")
    print("=========================================================================================\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecha", type=str, default="", help="Fecha YYYY-MM-DD")
    args = parser.parse_args()
    calificar_historial(args.fecha or None)
