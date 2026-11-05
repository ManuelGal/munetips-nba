"""
Escáner de Cuotas NBA Player Props con Protector de Créditos (The Odds API + Feed B2B Altenar/Playdoit).
- Ancla la tasa base por minuto de cada jugador a su rendimiento real implícito en el mercado Sharp
  y aplica encima los shocks cuantitativos (bajas confirmadas OUT, riesgo de Blowout por Spread y desfase de línea).
"""
import json
import urllib.parse
import urllib.request
from typing import Optional
import config
from src.nba_data import NBAGameContext, normalizar_nombre
from src.model_poisson import (
    PlayerProjectionInput,
    PropEvaluationResult,
    calcular_lambda_implicita_sharp,
    evaluar_oportunidad_prop,
    quitar_margen_casa,
)


SHARP_BOOKS = ("fanduel", "pinnacle", "draftkings")
SOFT_ALTENAR_PROXY_BOOKS = ("betonlineag", "bovada", "mybookieag", "betmgm", "williamhill_us")


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
    am = int(round(-100.0 / (cuota_dec - 1.0)))
    return f"{am}"


def escanear_mercados_reales(
    partidos: list[NBAGameContext],
) -> list[PropEvaluationResult]:
    """
    Consulta The Odds API para los partidos de NBA de hoy y evalúa desfases entre
    el Ancla Sharp (FanDuel/Pinnacle) y el feed blando (Playdoit / Altenar B2B).
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

    # Mapear partidos por equipos para no repetir el mismo evento de The Odds API
    eventos_visitados: set[str] = set()

    for ctx in partidos:
        if not ctx.newly_out_starters and abs(ctx.home_spread) < 9.5 and len(partidos) > 4:
            continue

        ev_id = None
        for ev in eventos_api:
            eid = ev.get("id")
            if not eid or eid in eventos_visitados:
                continue
            h_name = (ev.get("home_team") or "").upper()
            a_name = (ev.get("away_team") or "").upper()
            if ctx.home_team in h_name or ctx.away_team in a_name or len(eventos_api) <= 12:
                ev_id = eid
                break

        if not ev_id:
            continue

        eventos_visitados.add(ev_id)
        url_props = (
            f"https://api.the-odds-api.com/v4/sports/basketball_nba/events/{ev_id}/odds"
            f"?apiKey={urllib.parse.quote(config.ODDS_API_KEY)}"
            f"&regions=us,eu&markets={mercados_str}&oddsFormat=decimal"
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
    """Cruza las cuotas de FanDuel/Pinnacle contra el feed blando (Altenar/Playdoit) y corre Poisson."""
    bookmakers = prop_data.get("bookmakers", [])
    if not bookmakers:
        return []

    mapa_cuotas: dict[tuple[str, str], dict] = {}

    for bm in bookmakers:
        bm_key = bm.get("key", "")
        es_sharp = bm_key in SHARP_BOOKS
        es_soft = bm_key in SOFT_ALTENAR_PROXY_BOOKS
        if not (es_sharp or es_soft):
            continue

        for mkt in bm.get("markets", []):
            m_key = mkt.get("key", "")
            por_jugador: dict[str, dict] = {}
            for outc in mkt.get("outcomes", []):
                p_name = outc.get("description", "")
                p_norm = normalizar_nombre(p_name)
                side = (outc.get("name") or "").upper()
                point = outc.get("point")
                price = outc.get("price")
                if not p_norm or point is None or price is None:
                    continue
                por_jugador.setdefault(p_norm, {"raw_name": p_name, "line": float(point)})
                if side == "OVER":
                    por_jugador[p_norm]["over"] = float(price)
                elif side == "UNDER":
                    por_jugador[p_norm]["under"] = float(price)

            for p_norm, info in por_jugador.items():
                if "over" not in info or "under" not in info:
                    continue
                par_key = (m_key, p_norm)
                mapa_cuotas.setdefault(par_key, {"raw_name": info["raw_name"]})
                if es_sharp and ("sharp" not in mapa_cuotas[par_key] or bm_key == "fanduel"):
                    mapa_cuotas[par_key]["sharp"] = (info["line"], info["over"], info["under"])
                elif es_soft and "soft" not in mapa_cuotas[par_key]:
                    mapa_cuotas[par_key]["soft"] = (info["line"], info["over"], info["under"])

    resultados: list[PropEvaluationResult] = []
    titulares_baja_por_equipo = {b.team_abbr: b for b in ctx.newly_out_starters}

    for (m_key, p_norm), libros in mapa_cuotas.items():
        if "sharp" not in libros or "soft" not in libros:
            continue

        sharp_line, sharp_ov, sharp_un = libros["sharp"]
        soft_line, soft_ov, soft_un = libros["soft"]

        perfil = ctx.active_players.get(p_norm)
        es_titular = perfil.is_starter if perfil else (sharp_line >= 5.5)
        base_min = perfil.avg_minutes if perfil else (31.0 if es_titular else 22.0)

        # Anclar la tasa base por minuto a la media implícita sin vig del mercado Sharp
        # para evitar sesgos de posición en jugadores atípicos (ej. point-forwards)
        sharp_p_over, _ = quitar_margen_casa(sharp_ov, sharp_un)
        sharp_lambda_base = calcular_lambda_implicita_sharp(sharp_p_over, sharp_line, m_key)
        tasa_min_real = sharp_lambda_base / max(15.0, base_min)

        equipo_jug = perfil.team_abbr if perfil else ctx.home_team
        rival_jug = ctx.away_team if equipo_jug == ctx.home_team else ctx.home_team
        baja_equipo = titulares_baja_por_equipo.get(equipo_jug)

        inp = PlayerProjectionInput(
            player_name=libros["raw_name"],
            team_abbr=equipo_jug,
            opponent_abbr=rival_jug,
            market_type=m_key,
            is_starter=es_titular,
            base_minutes=base_min,
            rate_per_minute=tasa_min_real,
            team_pace=ctx.estimated_pace,
            opp_pace=ctx.estimated_pace,
            game_spread=ctx.home_spread,
            teammate_out_name=baja_equipo.player_name if baja_equipo else None,
            teammate_out_minutes=baja_equipo.avg_minutes if baja_equipo else 0.0,
            usage_boost_factor=1.06 if baja_equipo else 1.0,
            is_two_way_contract=perfil.is_two_way if perfil else False,
        )

        evaluacion = evaluar_oportunidad_prop(
            inp=inp,
            playdoit_line=soft_line,
            playdoit_over_odds=soft_ov,
            playdoit_under_odds=soft_un,
            sharp_line=sharp_line,
            sharp_over_odds=sharp_ov,
            sharp_under_odds=sharp_un,
        )

        if evaluacion and evaluacion.score_1_to_10 >= config.MIN_SCORE_FOR_ALERT:
            resultados.append(evaluacion)

    return resultados
