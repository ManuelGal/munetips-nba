"""
Escáner de Cuotas NBA Player Props con Protector de Créditos (The Odds API `regions=us`).
- Exclusivo para Playdoit.mx (motor Altenar).
- Utiliza el Consenso Sharp Mundial (`fanduel`, `pinnacle`, `draftkings`, `betonlineag`, `lowvig`)
  como ancla de referencia para fijar la línea real de mercado y su probabilidad limpia.
- Alimenta el Modelo Estadístico Independiente (`model_poisson.py`) con los promedios y tasas
  por minuto reales de la temporada actual (ESPN Overview API), las bajas recientes con sinergia
  posicional, y la asimetría contractual de Playdoit.mx (Void 1H en OVER / Win en UNDER).
- Filtra partidos que ya comenzaron para proteger los créditos restantes de la API.
"""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from statistics import median
from typing import Optional
import config
from src.nba_data import NBAGameContext, PlayerStatsProfile, normalizar_nombre
from src.model_poisson import (
    PlayerProjectionInput,
    PropEvaluationResult,
    calcular_lambda_implicita_sharp,
    evaluar_oportunidad_prop,
    quitar_margen_casa,
)


SHARP_PRIORITY_BOOKS = ("fanduel", "pinnacle", "draftkings", "betonlineag", "lowvig")
ALL_US_TRACKED_BOOKS = SHARP_PRIORITY_BOOKS


def _get_json(url: str) -> tuple[object, dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        headers = {k.lower(): v for k, v in resp.headers.items()}
        data = json.loads(resp.read().decode("utf-8"))
        return data, headers


def decimal_a_americano(cuota_dec: float) -> str:
    if cuota_dec >= 2.0:
        am = int(round((cuota_dec - 1.0) * 100))
        return f"+{am}"
    if cuota_dec <= 1.01:
        return "-10000"
    am = int(round(-100.0 / (cuota_dec - 1.0)))
    return f"{am}"


def _cumple_umbral_catalogo_playdoit(m_key: str, linea: float) -> bool:
    """
    Playdoit (Altenar) solo abre mercados 'Más de/Menos de' desbloqueados (sin candado 🔒)
    para titulares y estrellas con líneas principales por encima de estos umbrales:
    """
    if m_key == "player_points" and linea < 13.5:
        return False
    if m_key == "player_rebounds" and linea < 5.5:
        return False
    if m_key == "player_assists" and linea < 4.5:
        return False
    return True


def escanear_mercados_reales(
    partidos: list[NBAGameContext],
) -> list[PropEvaluationResult]:
    """
    Consulta The Odds API (`regions=us` = 3 créditos por partido en vez de 6) para los partidos
    NBA de hoy y evalúa ventajas reales entre nuestro Modelo Estadístico Independiente + Reglas Playdoit,
    el Consenso Sharp y las líneas rezagadas en casas recreativas.
    """
    if not config.ODDS_API_KEY:
        print("[INFO] Sin ODDS_API_KEY configurada.")
        return []

    url_events = (
        f"https://api.the-odds-api.com/v4/sports/basketball_nba/events"
        f"?apiKey={urllib.parse.quote(config.ODDS_API_KEY)}"
    )
    try:
        eventos_api, hdrs = _get_json(url_events)
        restantes = hdrs.get("x-requests-remaining", "?")
        print(f"[ODDS API] Eventos NBA leídos. Créditos gratuitos restantes este mes: {restantes}")
    except Exception as exc:
        print(f"[ERROR] No se pudo conectar a The Odds API: {exc}")
        return []

    if not isinstance(eventos_api, list):
        return []

    oportunidades: list[PropEvaluationResult] = []
    mercados_str = ",".join(config.TARGET_MARKETS)
    eventos_visitados: set[str] = set()
    now_utc = datetime.now(timezone.utc)

    for ctx in partidos:
        # Protector de créditos: Omitir partidos que ya comenzaron
        if ctx.start_time_utc:
            try:
                t_ctx = datetime.fromisoformat(ctx.start_time_utc.replace("Z", "+00:00"))
                if t_ctx <= now_utc:
                    continue
            except Exception:
                pass

        if not ctx.newly_out_starters and abs(ctx.home_spread) < 9.5 and len(partidos) > 4:
            continue

        ev_id = None
        h_full_target = ctx.home_team_full.lower().strip()
        a_full_target = ctx.away_team_full.lower().strip()
        for ev in eventos_api:
            eid = ev.get("id")
            if not eid or eid in eventos_visitados:
                continue

            commence_str = ev.get("commence_time")
            if commence_str:
                try:
                    t_comm = datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
                    if t_comm <= now_utc:
                        continue
                except Exception:
                    pass

            h_name = (ev.get("home_team") or "").lower().strip()
            a_name = (ev.get("away_team") or "").lower().strip()
            if (
                h_full_target == h_name
                or a_full_target == a_name
                or (h_full_target and h_full_target.split()[-1] in h_name)
                or (a_full_target and a_full_target.split()[-1] in a_name)
            ):
                ev_id = eid
                break

        if not ev_id:
            continue

        eventos_visitados.add(ev_id)
        url_props = (
            f"https://api.the-odds-api.com/v4/sports/basketball_nba/events/{ev_id}/odds"
            f"?apiKey={urllib.parse.quote(config.ODDS_API_KEY)}"
            f"&regions=us&markets={mercados_str}&oddsFormat=decimal"
        )
        try:
            prop_data, _ = _get_json(url_props)
        except Exception:
            continue

        oportunidades.extend(_procesar_libro_evento(ctx, prop_data))

    oportunidades.sort(key=lambda x: x.score_1_to_10, reverse=True)
    return oportunidades


def _procesar_libro_evento(
    ctx: NBAGameContext,
    prop_data: dict,
) -> list[PropEvaluationResult]:
    """
    Cruza el Ancla Sharp (`fanduel`/`pinnacle`/`draftkings`) contra nuestro Modelo Estadístico
    Independiente (datos reales ESPN) y contra líneas rezagadas en casas recreativas (`betmgm`,
    `williamhill_us`, `espnbet`, `betrivers`).
    """
    bookmakers = prop_data.get("bookmakers", [])
    if not bookmakers:
        return []

    mapa_cuotas: dict[tuple[str, str], dict] = {}

    for bm in bookmakers:
        bm_key = bm.get("key", "")
        if bm_key not in ALL_US_TRACKED_BOOKS:
            continue

        for mkt in bm.get("markets", []):
            m_key = mkt.get("key", "")
            por_jugador_y_linea: dict[tuple[str, float], dict] = {}
            for outc in mkt.get("outcomes", []):
                p_name = outc.get("description", "")
                p_norm = normalizar_nombre(p_name)
                side = (outc.get("name") or "").upper()
                point = outc.get("point")
                price = outc.get("price")
                if not p_norm or point is None or price is None:
                    continue
                pt_val = float(point)
                clave_jl = (p_norm, pt_val)
                por_jugador_y_linea.setdefault(
                    clave_jl, {"raw_name": p_name, "p_norm": p_norm, "line": pt_val}
                )
                if side == "OVER":
                    por_jugador_y_linea[clave_jl]["over"] = float(price)
                elif side == "UNDER":
                    por_jugador_y_linea[clave_jl]["under"] = float(price)

            for (_, _), info in por_jugador_y_linea.items():
                if "over" not in info or "under" not in info:
                    continue
                if not (1.50 <= info["over"] <= 2.50 and 1.50 <= info["under"] <= 2.50):
                    continue
                p_norm = info["p_norm"]
                par_key = (m_key, p_norm)
                registro = mapa_cuotas.setdefault(
                    par_key, {"raw_name": info["raw_name"], "books": {}}
                )
                prev = registro["books"].get(bm_key)
                if prev is None or abs(info["over"] - info["under"]) < abs(prev[1] - prev[2]):
                    registro["books"][bm_key] = (info["line"], info["over"], info["under"])

    resultados: list[PropEvaluationResult] = []

    bajas_por_equipo: dict[str, list[PlayerStatsProfile]] = {}
    for b in ctx.newly_out_starters:
        bajas_por_equipo.setdefault(b.team_abbr, []).append(b)

    benchmark_cap = getattr(config, "PLAYDOIT_STANDARD_BENCHMARK_ODDS", 1.87)

    for (m_key, p_norm), datos_jug in mapa_cuotas.items():
        books_dict: dict[str, tuple[float, float, float]] = datos_jug.get("books", {})
        if not books_dict:
            continue

        # 1. Determinar Ancla Sharp (exclusivamente de casas Sharp: FanDuel -> Pinnacle -> DraftKings)
        sharp_tuple = None
        for sk in SHARP_PRIORITY_BOOKS:
            if sk in books_dict:
                sharp_tuple = books_dict[sk]
                break
        if sharp_tuple is None:
            continue

        sharp_line, sharp_ov, sharp_un = sharp_tuple

        if not _cumple_umbral_catalogo_playdoit(m_key, sharp_line):
            continue

        # 2. Construir la línea objetivo de Playdoit:
        #    Se toma la línea principal del Consenso Sharp con cuota estándar de referencia
        #    en Playdoit (Altenar), topada en el benchmark (1.87 / -115) y respetando
        #    la regla de retiro de Playdoit (momio >= -130 / 1.77).
        misma_linea_sharp = [
            vals for vals in books_dict.values() if abs(vals[0] - sharp_line) < 0.05
        ]
        med_ov = float(median([v[1] for v in misma_linea_sharp])) if misma_linea_sharp else sharp_ov
        med_un = float(median([v[2] for v in misma_linea_sharp])) if misma_linea_sharp else sharp_un

        lineas_candidatas: dict[float, tuple[float, float]] = {
            sharp_line: (round(min(med_ov, benchmark_cap), 2), round(min(med_un, benchmark_cap), 2))
        }

        # 3. Obtener estadísticas reales de temporada del jugador desde ESPN (independientes de las casas)
        perfil = ctx.active_players.get(p_norm)
        if perfil and perfil.has_real_stats:
            es_titular = perfil.is_starter
            base_min = perfil.avg_minutes
            pos_jug = perfil.position
            if m_key == "player_points":
                tasa_min_real = perfil.pts_per_min
            elif m_key == "player_rebounds":
                tasa_min_real = perfil.reb_per_min
            else:
                tasa_min_real = perfil.ast_per_min
        else:
            es_titular = perfil.is_starter if perfil else True
            base_min = perfil.avg_minutes if perfil else 31.5
            pos_jug = perfil.position if perfil else "G"
            sharp_p_over, _ = quitar_margen_casa(sharp_ov, sharp_un)
            sharp_lambda_base = calcular_lambda_implicita_sharp(sharp_p_over, sharp_line, m_key)
            tasa_min_real = sharp_lambda_base / max(15.0, base_min)

        equipo_jug = perfil.team_abbr if perfil else ctx.home_team
        rival_jug = ctx.away_team if equipo_jug == ctx.home_team else ctx.home_team
        spread_equipo = ctx.home_spread if equipo_jug == ctx.home_team else -ctx.home_spread

        bajas_equipo = bajas_por_equipo.get(equipo_jug, [])
        es_baja_reciente = any(b.is_newly_out for b in bajas_equipo)

        inp = PlayerProjectionInput(
            player_name=datos_jug["raw_name"],
            team_abbr=equipo_jug,
            opponent_abbr=rival_jug,
            market_type=m_key,
            is_starter=es_titular,
            base_minutes=base_min,
            rate_per_minute=tasa_min_real,
            team_pace=ctx.estimated_pace,
            opp_pace=ctx.estimated_pace,
            game_spread=spread_equipo,
            position=pos_jug,
            teammates_out=bajas_equipo,
            is_breaking_injury=es_baja_reciente,
            minutes_cap_news=perfil.minutes_cap_news if perfil else None,
            is_two_way_contract=perfil.is_two_way if perfil else False,
        )

        mejor_eval_jugador: Optional[PropEvaluationResult] = None
        for cand_line, (cand_ov, cand_un) in lineas_candidatas.items():
            evaluacion = evaluar_oportunidad_prop(
                inp=inp,
                playdoit_line=cand_line,
                playdoit_over_odds=cand_ov,
                playdoit_under_odds=cand_un,
                sharp_line=sharp_line,
                sharp_over_odds=sharp_ov,
                sharp_under_odds=sharp_un,
            )
            if evaluacion and evaluacion.playdoit_odds >= config.MIN_PLAYDOIT_ODDS_DECIMAL and (
                mejor_eval_jugador is None
                or evaluacion.score_1_to_10 > mejor_eval_jugador.score_1_to_10
                or (
                    evaluacion.score_1_to_10 == mejor_eval_jugador.score_1_to_10
                    and evaluacion.ev_pct > mejor_eval_jugador.ev_pct
                )
            ):
                mejor_eval_jugador = evaluacion

        if mejor_eval_jugador and mejor_eval_jugador.score_1_to_10 >= config.MIN_SCORE_FOR_ALERT:
            resultados.append(mejor_eval_jugador)

    return resultados
