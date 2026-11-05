"""
Escáner de Cuotas NBA Player Props con Protector de Créditos (The Odds API `regions=us`).
- Calcula el Consenso Sharp sin vig (FanDuel / DraftKings) mediante Power Devigging.
- Alimenta el Modelo Estadístico Independiente (`model_poisson.py`) con los promedios y tasas
  por minuto reales de la temporada actual (ESPN Overview API), la lista completa de bajas por
  equipo con sinergia posicional, y la asimetría contractual de Playdoit.mx.
- Emite la Línea Objetivo y la Cuota Mínima Aceptable (>= 1.77 / -130) que el usuario debe
  verificar en la pestaña 'Más de/Menos de' de Playdoit, sin inventar cuotas falsas de otras casas.
"""
import json
import urllib.parse
import urllib.request
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


SHARP_PRIORITY_BOOKS = ("fanduel", "draftkings", "pinnacle")
CONSENSUS_US_BOOKS = ("fanduel", "draftkings", "betmgm", "williamhill_us", "espnbet", "betrivers")


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


def escanear_mercados_reales(
    partidos: list[NBAGameContext],
) -> list[PropEvaluationResult]:
    """
    Consulta The Odds API (`regions=us` = 3 créditos por partido en vez de 6) para los partidos
    NBA de hoy y evalúa ventajas reales entre nuestro Modelo Estadístico Independiente + Reglas Playdoit
    y el Consenso Sharp del mercado.
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

    for ctx in partidos:
        if not ctx.newly_out_starters and abs(ctx.home_spread) < 9.5 and len(partidos) > 4:
            continue

        ev_id = None
        h_full_target = ctx.home_team_full.lower().strip()
        a_full_target = ctx.away_team_full.lower().strip()
        for ev in eventos_api:
            eid = ev.get("id")
            if not eid or eid in eventos_visitados:
                continue
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
        # Optimización de créditos: solo `regions=us` (consume 3 créditos por partido en vez de 6)
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
    Construye el consenso Sharp de línea y probabilidad justa para cada jugador y lo compara
    contra el modelo estadístico independiente alimentado con datos reales de temporada de ESPN.
    """
    bookmakers = prop_data.get("bookmakers", [])
    if not bookmakers:
        return []

    mapa_cuotas: dict[tuple[str, str], dict] = {}

    for bm in bookmakers:
        bm_key = bm.get("key", "")
        if bm_key not in CONSENSUS_US_BOOKS and bm_key not in SHARP_PRIORITY_BOOKS:
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
                # Si una casa tiene varias líneas alternas, conservar la más equilibrada (menor |over - under|)
                prev = registro["books"].get(bm_key)
                if prev is None or abs(info["over"] - info["under"]) < abs(prev[1] - prev[2]):
                    registro["books"][bm_key] = (info["line"], info["over"], info["under"])

    resultados: list[PropEvaluationResult] = []

    # Agrupar TODAS las bajas del equipo como lista (sin sobrescribir cuando hay 2+ titulares fuera)
    bajas_por_equipo: dict[str, list[PlayerStatsProfile]] = {}
    for b in ctx.newly_out_starters:
        bajas_por_equipo.setdefault(b.team_abbr, []).append(b)

    for (m_key, p_norm), datos_jug in mapa_cuotas.items():
        books_dict: dict[str, tuple[float, float, float]] = datos_jug.get("books", {})
        if not books_dict:
            continue

        # 1. Determinar Ancla Sharp (prioridad: FanDuel -> Pinnacle -> DraftKings -> mediana US)
        sharp_tuple = None
        for sk in SHARP_PRIORITY_BOOKS:
            if sk in books_dict:
                sharp_tuple = books_dict[sk]
                break
        if sharp_tuple is None:
            sharp_tuple = next(iter(books_dict.values()))

        sharp_line, sharp_ov, sharp_un = sharp_tuple

        # Filtro estricto de Catálogo Playdoit (Altenar):
        # Playdoit solo abre pestañas "Más de/Menos de" desbloqueadas para titulares/estrellas con líneas altas:
        if m_key == "player_points" and sharp_line < 13.5:
            continue
        if m_key == "player_rebounds" and sharp_line < 5.5:
            continue
        if m_key == "player_assists" and sharp_line < 4.5:
            continue

        # 2. Línea objetivo de consenso y cuota de referencia conservadora para Playdoit
        lineas_en_linea_sharp = [
            vals for vals in books_dict.values() if abs(vals[0] - sharp_line) < 0.05
        ]
        if lineas_en_linea_sharp:
            med_ov = float(median([v[1] for v in lineas_en_linea_sharp]))
            med_un = float(median([v[2] for v in lineas_en_linea_sharp]))
        else:
            med_ov, med_un = sharp_ov, sharp_un

        # No inflar artificialmente la cuota estimada: tope en el estándar de Playdoit (1.87 / -115)
        benchmark_cap = getattr(config, "PLAYDOIT_STANDARD_BENCHMARK_ODDS", 1.87)
        ref_playdoit_ov = round(min(med_ov, benchmark_cap), 2)
        ref_playdoit_un = round(min(med_un, benchmark_cap), 2)

        # 3. Obtener estadísticas reales de temporada del jugador desde ESPN (independientes de FanDuel)
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
            # Respaldo solo si ESPN no devolvió ficha individual para ese nombre
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

        evaluacion = evaluar_oportunidad_prop(
            inp=inp,
            playdoit_line=sharp_line,
            playdoit_over_odds=ref_playdoit_ov,
            playdoit_under_odds=ref_playdoit_un,
            sharp_line=sharp_line,
            sharp_over_odds=sharp_ov,
            sharp_under_odds=sharp_un,
        )

        if evaluacion and evaluacion.score_1_to_10 >= config.MIN_SCORE_FOR_ALERT:
            resultados.append(evaluacion)

    return resultados
