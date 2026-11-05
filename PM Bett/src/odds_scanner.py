"""
Escáner de Cuotas NBA Player Props con Protector de Créditos (The Odds API + Feed B2B Altenar/Playdoit).
- Primero filtra partidos usando `nba_data.py` (0 créditos consumidos).
- Solo consulta cuotas de Player Props en partidos con catalizador de lesión o desfase activo.
- Compara el Ancla Sharp (FanDuel + Pinnacle) contra la línea blanda (Playdoit / Altenar B2B).
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
    evaluar_oportunidad_prop,
)


SHARP_BOOKS = ("fanduel", "pinnacle", "draftkings")
# Proveedores que comparten el mismo feed secundario B2B de Player Props que Altenar (Playdoit)
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
    Consulta The Odds API únicamente para los partidos del día con catalizador de minutos
    o riesgo de blowout, protegiendo los 500 créditos mensuales gratuitos.
    """
    if not config.ODDS_API_KEY:
        print("[INFO] Sin ODDS_API_KEY configurada. Usa --demo para probar alertas reales en Telegram.")
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

    for ctx in partidos:
        # Ahorro extremo de créditos: solo escanear partidos donde hay al menos 1 titular OUT
        # o un spread de paliza (>= 10.5 pts)
        if not ctx.newly_out_starters and abs(ctx.home_spread) < 10.5:
            continue

        # Emparejar evento con The Odds API
        ev_id = None
        for ev in eventos_api:
            ev_id = ev.get("id")
            break  # Se refina por coincidencia de equipos abajo

        for ev in eventos_api:
            h_name = (ev.get("home_team") or "").upper()
            a_name = (ev.get("away_team") or "").upper()
            if ctx.home_team in h_name or ctx.away_team in a_name or len(eventos_api) <= 15:
                ev_id = ev.get("id")
                break

        if not ev_id:
            continue

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

    # Estructura: {(market_key, player_norm): {"sharp": (line, over, under), "soft": (line, over, under)}}
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
                # Priorizar FanDuel como ancla sharp #1
                if es_sharp and ("sharp" not in mapa_cuotas[par_key] or bm_key == "fanduel"):
                    mapa_cuotas[par_key]["sharp"] = (info["line"], info["over"], info["under"])
                elif es_soft and "soft" not in mapa_cuotas[par_key]:
                    mapa_cuotas[par_key]["soft"] = (info["line"], info["over"], info["under"])

    resultados: list[PropEvaluationResult] = []
    titular_baja = ctx.newly_out_starters[0] if ctx.newly_out_starters else None

    for (m_key, p_norm), libros in mapa_cuotas.items():
        if "sharp" not in libros or "soft" not in libros:
            continue

        sharp_line, sharp_ov, sharp_un = libros["sharp"]
        soft_line, soft_ov, soft_un = libros["soft"]

        perfil = ctx.active_players.get(p_norm)
        es_titular = perfil.is_starter if perfil else False
        base_min = perfil.avg_minutes if perfil else (soft_line * 4.8)
        tasa_min = (
            (perfil.reb_per_min if m_key == "player_rebounds" else perfil.ast_per_min)
            if perfil
            else (sharp_line / max(18.0, base_min))
        )

        mismo_equipo_baja = (
            titular_baja is not None
            and perfil is not None
            and titular_baja.team_abbr == perfil.team_abbr
        )

        inp = PlayerProjectionInput(
            player_name=libros["raw_name"],
            team_abbr=perfil.team_abbr if perfil else ctx.home_team,
            opponent_abbr=ctx.away_team if (perfil and perfil.team_abbr == ctx.home_team) else ctx.home_team,
            market_type=m_key,
            is_starter=es_titular,
            base_minutes=base_min,
            rate_per_minute=tasa_min,
            team_pace=ctx.estimated_pace,
            opp_pace=ctx.estimated_pace,
            game_spread=ctx.home_spread,
            teammate_out_name=titular_baja.player_name if mismo_equipo_baja else None,
            teammate_out_minutes=titular_baja.avg_minutes if mismo_equipo_baja else 0.0,
            usage_boost_factor=1.12 if mismo_equipo_baja else 1.0,
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
