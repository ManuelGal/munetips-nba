"""
Motor Matemático Cuantitativo para NBA Player Props (Rebotes y Asistencias).
Implementa:
1. Proyección de minutos con redistribución por bajas (WOWY) y descuento bimodal por Blowout (Spread).
2. Distribución de Poisson Sobredispersa (Binomial Negativa) en Python puro (0 dependencias pesadas).
3. Eliminación de margen (De-vigging) del ancla afilada (FanDuel + Pinnacle).
4. Cálculo del Score de Confianza (1.0 a 10.0) calibrado para las reglas de Playdoit.mx.
"""
import math
from dataclasses import dataclass
from typing import Optional
import config


@dataclass
class PlayerProjectionInput:
    player_name: str
    team_abbr: str
    opponent_abbr: str
    market_type: str               # "player_rebounds" | "player_assists"
    is_starter: bool
    base_minutes: float            # Promedio ponderado de minutos recientes
    rate_per_minute: float         # Rebotes o asistencias por minuto en cancha
    team_pace: float
    opp_pace: float
    game_spread: float             # Spread del partido (ej. -13.5 o +4.0)
    teammate_out_name: Optional[str] = None
    teammate_out_minutes: float = 0.0
    usage_boost_factor: float = 1.0  # Ej. 1.14 si el base titular es baja y este jugador sube el balón
    minutes_cap_news: Optional[float] = None  # Si un beat writer reporta límite de minutos
    is_two_way_contract: bool = False


@dataclass
class PropEvaluationResult:
    player_name: str
    team_abbr: str
    opponent_abbr: str
    market_type: str
    side: str                      # "OVER" | "UNDER"
    playdoit_line: float           # Ej. 4.5
    playdoit_odds: float           # Ej. 1.87
    sharp_line: float              # Línea en FanDuel / Pinnacle (ej. 5.5)
    sharp_fair_prob: float         # Probabilidad sin vig de FanDuel/Pinnacle para la línea de Playdoit
    projected_minutes: float
    projected_mean_lambda: float   # Media proyectada (λ) de rebotes o asistencias
    model_prob: float              # Probabilidad real calculada por Binomial Negativa / Poisson
    blended_prob: float            # Probabilidad combinada (Modelo + Ancla Sharp FanDuel)
    ev_pct: float                  # Valor Esperado porcentual (ej. +0.084 = +8.4%)
    score_1_to_10: float           # Calificación final del 1 al 10 para el canal Muñetips
    suggested_stake_mxn: float     # Monto redondeado ($20, $30 MXN) para camuflaje en Altenar
    min_acceptable_odds: float     # Cuota mínima a la que sigue siendo rentable entrar
    catalyst_reason: str           # Explicación clara del motivo de la ventaja


def proyectar_minutos_y_media(inp: PlayerProjectionInput) -> tuple[float, float, list[str]]:
    """
    Calcula E[Minutos] y E[Estadística] (λ) incorporando:
    - Redistribución de minutos por compañero titular baja (OUT)
    - Descuento o bono por riesgo de paliza (Blowout según Spread)
    - Tope de minutos por noticia de última hora (Minutes Restriction)
    - Ajuste por ritmo de posesiones (Pace)
    """
    notas = []
    minutos = inp.base_minutes

    # 1. Redistribución por baja de compañero clave (WOWY aproximado)
    if inp.teammate_out_name and inp.teammate_out_minutes >= 22.0:
        if not inp.is_starter:
            # Suplente directo absorbe entre 22% y 30% de los minutos liberados
            extra_min = min(9.5, inp.teammate_out_minutes * 0.26)
            minutos += extra_min
            notas.append(
                f"Baja de {inp.teammate_out_name} ({inp.teammate_out_minutes:.0f} mpg) libera +{extra_min:.1f} min"
            )
        else:
            # Titular absorbe menos minutos extra (~8%), pero sube su tasa por minuto
            extra_min = min(3.0, inp.teammate_out_minutes * 0.08)
            minutos += extra_min
            notas.append(
                f"Sin {inp.teammate_out_name}, sube uso ofensivo (+{(inp.usage_boost_factor - 1)*100:.0f}%)"
            )

    # 2. Ajuste bimodal por riesgo de paliza (Blowout según Spread)
    abs_spread = abs(inp.game_spread)
    if abs_spread > config.BLOWOUT_SPREAD_THRESHOLD:
        exceso_spread = abs_spread - config.BLOWOUT_SPREAD_THRESHOLD
        if inp.is_starter:
            penalizacion = exceso_spread * config.BLOWOUT_MINUTES_PENALTY_PER_PT
            minutos = max(18.0, minutos - penalizacion)
            notas.append(f"Riesgo de paliza (Spread {inp.game_spread:+.1f}) recorta -{penalizacion:.1f} min al titular")
        else:
            bono_basura = exceso_spread * (config.BLOWOUT_MINUTES_PENALTY_PER_PT * 0.65)
            minutos = min(34.0, minutos + bono_basura)
            notas.append(f"Riesgo de paliza (Spread {inp.game_spread:+.1f}) suma +{bono_basura:.1f} min de rotación")

    # 3. Restricción explícita de minutos reportada por periodista (Minutes Cap)
    if inp.minutes_cap_news is not None and inp.minutes_cap_news > 0:
        if minutos > inp.minutes_cap_news:
            notas.append(f"Restricción médica reportada: tope de {inp.minutes_cap_news:.0f} min")
            minutos = inp.minutes_cap_news

    minutos = max(8.0, min(40.0, minutos))

    # 4. Ajuste de tasa por minuto según el Pace esperado del partido
    expected_game_pace = (inp.team_pace + inp.opp_pace) / 2.0
    pace_multiplier = expected_game_pace / config.LEAGUE_AVG_PACE

    tasa_ajustada = inp.rate_per_minute * inp.usage_boost_factor * pace_multiplier
    media_lambda = minutos * tasa_ajustada

    return round(minutos, 2), round(media_lambda, 3), notas


def _nbinom_pmf(k: int, mu: float, alpha: float) -> float:
    """
    Función de masa de probabilidad P(X = k) para una Binomial Negativa (Poisson sobredispersa)
    con media `mu` y varianza `mu + alpha * mu^2`. Si alpha <= 1e-6, usa Poisson exacta.
    """
    if mu <= 0:
        return 1.0 if k == 0 else 0.0

    if alpha <= 1e-6:
        # Poisson estándar: e^(-mu) * mu^k / k!
        return math.exp(-mu + k * math.log(mu) - math.lgamma(k + 1))

    r = 1.0 / alpha
    p = r / (r + mu)
    log_prob = (
        math.lgamma(k + r)
        - math.lgamma(k + 1)
        - math.lgamma(r)
        + r * math.log(p)
        + k * math.log(1.0 - p)
    )
    return math.exp(log_prob)


def calcular_probabilidad_linea(
    media_lambda: float,
    linea: float,
    market_type: str,
) -> tuple[float, float]:
    """
    Devuelve (prob_over, prob_under) condicionado a que no haya Push (para líneas .5 es directo).
    Usa Binomial Negativa para capturar la sobredispersión real de minutos en la NBA.
    """
    alpha = (
        config.OVERDISPERSION_ALPHA_REB
        if market_type == "player_rebounds"
        else config.OVERDISPERSION_ALPHA_AST
    )

    # Si la línea es 4.5: Under gana con k <= 4, Over gana con k >= 5
    es_linea_entera = abs(linea - round(linea)) < 1e-4
    limite_under = int(round(linea)) - 1 if es_linea_entera else int(math.floor(linea))

    prob_under = sum(_nbinom_pmf(k, media_lambda, alpha) for k in range(0, limite_under + 1))
    prob_push = _nbinom_pmf(int(round(linea)), media_lambda, alpha) if es_linea_entera else 0.0
    prob_over = max(0.0, 1.0 - prob_under - prob_push)

    # Normalizar si hay push (reembolso)
    denom = prob_over + prob_under
    if denom > 0 and es_linea_entera:
        prob_over /= denom
        prob_under /= denom

    return min(0.99, max(0.01, prob_over)), min(0.99, max(0.01, prob_under))


def quitar_margen_casa(cuota_over: float, cuota_under: float) -> tuple[float, float]:
    """
    Elimina el overround (vig) de FanDuel/Pinnacle usando el método de potencia/multiplicativo
    para obtener la probabilidad verdadera implícita del mercado eficiente.
    """
    imp_over = 1.0 / cuota_over
    imp_under = 1.0 / cuota_under
    total = imp_over + imp_under
    if total <= 0:
        return 0.5, 0.5
    return imp_over / total, imp_under / total


def ajustar_prob_sharp_por_diferencia_de_linea(
    sharp_prob_over_en_su_linea: float,
    sharp_line: float,
    playdoit_line: float,
    market_type: str,
) -> tuple[float, float]:
    """
    Si FanDuel ya movió la línea a 5.5 (con prob justa 50% Over) pero Playdoit sigue dormida en 4.5,
    traduce la línea de FanDuel a la probabilidad equivalente en la línea de Playdoit (4.5).
    """
    if abs(sharp_line - playdoit_line) < 0.05:
        return sharp_prob_over_en_su_linea, 1.0 - sharp_prob_over_en_su_linea

    # Buscamos la lambda implícita de FanDuel por bisección rápida (15 iteraciones)
    low, high = 0.3, 25.0
    for _ in range(18):
        mid = (low + high) / 2.0
        p_ov, _ = calcular_probabilidad_linea(mid, sharp_line, market_type)
        if p_ov < sharp_prob_over_en_su_linea:
            low = mid
        else:
            high = mid
    sharp_implied_lambda = (low + high) / 2.0

    # Evaluamos esa lambda implícita de FanDuel contra la línea atrasada de Playdoit
    return calcular_probabilidad_linea(sharp_implied_lambda, playdoit_line, market_type)


def calcular_score_1_a_10(
    ev_pct: float,
    side: str,
    playdoit_line: float,
    sharp_line: float,
    has_injury_catalyst: bool,
    game_spread: float,
    is_starter: bool,
) -> float:
    """
    Construye el Score de 1.0 a 10.0 para el canal 'Muñetips':
    - 1 a 4 pts: Magnitud del Valor Esperado (+EV %)
    - 0 a 3 pts: Desfase de línea en puntos frente a FanDuel/Pinnacle (Stale Line)
    - 0 a 3 pts: Catalizador confirmado (Lesión NBA) + Ventaja de reglas en Playdoit
    """
    # A. Puntos por EV (máx 4.5 pts)
    if ev_pct < 0.04:
        pts_ev = max(1.0, (ev_pct / 0.04) * 2.0)
    elif ev_pct < 0.065:
        pts_ev = 2.5 + ((ev_pct - 0.04) / 0.025) * 0.8
    elif ev_pct < 0.10:
        pts_ev = 3.3 + ((ev_pct - 0.065) / 0.035) * 0.8
    else:
        pts_ev = min(4.5, 4.1 + (ev_pct - 0.10) * 4.0)

    # B. Puntos por desfase de línea respecto a FanDuel (máx 3.0 pts)
    # Si apostamos OVER y Playdoit tiene una línea MENOR que FanDuel (ej. 4.5 vs 5.5) -> Joya
    # Si apostamos UNDER y Playdoit tiene una línea MAYOR que FanDuel (ej. 6.5 vs 5.5) -> Joya
    ventaja_puntos = (sharp_line - playdoit_line) if side == "OVER" else (playdoit_line - sharp_line)
    if ventaja_puntos >= 1.0:
        pts_linea = 3.0
    elif ventaja_puntos >= 0.5:
        pts_linea = 2.3
    elif abs(ventaja_puntos) < 0.1:
        pts_linea = 0.8
    else:
        pts_linea = 0.0  # Nunca premiar ir contra una línea peor que FanDuel

    # C. Puntos por Catalizador y Reglas de Playdoit (máx 2.5 pts)
    pts_contexto = 0.0
    if has_injury_catalyst:
        pts_contexto += 1.5

    # En Playdoit no hay Prop Protect: los UNDERS tienen ventaja estructural por lesiones/faltas
    if side == "UNDER":
        pts_contexto += 0.6
        if is_starter and abs(game_spread) >= 11.0:
            pts_contexto += 0.4  # Titular en partido con alto riesgo de paliza
    else:
        if not is_starter and has_injury_catalyst:
            pts_contexto += 0.5  # Suplente que absorbe minutos garantizados por baja

    raw_score = pts_ev + pts_linea + min(2.5, pts_contexto)
    return round(min(9.9, max(1.0, raw_score)), 1)


def calcular_stake_playdoit(prob_ganar: float, cuota_decimal: float) -> float:
    """
    Calcula el monto a apostar con 1/4 de Kelly, respetando el mínimo de $20 MXN
    y redondeando a múltiplos cerrados de $10 MXN para no disparar alertas de bot en Altenar.
    """
    b = cuota_decimal - 1.0
    if b <= 0:
        return 0.0
    kelly_puro = (prob_ganar * b - (1.0 - prob_ganar)) / b
    if kelly_puro <= 0:
        return 0.0

    fraccion = min(config.MAX_STAKE_PCT, kelly_puro * config.KELLY_FRACTION)
    monto_bruto = config.BANKROLL_MXN * fraccion

    # Redondeo a múltiplos humanos ($20, $30, $40 MXN)
    monto_redondeado = round(monto_bruto / config.STAKE_ROUNDING_BASE) * config.STAKE_ROUNDING_BASE
    return max(config.MIN_STAKE_MXN, monto_redondeado)


def evaluar_oportunidad_prop(
    inp: PlayerProjectionInput,
    playdoit_line: float,
    playdoit_over_odds: Optional[float],
    playdoit_under_odds: Optional[float],
    sharp_line: float,
    sharp_over_odds: float,
    sharp_under_odds: float,
) -> Optional[PropEvaluationResult]:
    """
    Evalúa tanto el OVER como el UNDER de un jugador en Playdoit contra el modelo de Poisson
    y contra la línea sin margen de FanDuel/Pinnacle. Devuelve la mejor oportunidad si supera los filtros.
    """
    # Regla Jontay Porter: si solo ofrecen Over sin Under (jugador Two-Way), descartar por margen oculto
    if inp.is_two_way_contract or playdoit_over_odds is None or playdoit_under_odds is None:
        return None

    proj_min, proj_lambda, notas = proyectar_minutos_y_media(inp)
    model_p_over, model_p_under = calcular_probabilidad_linea(
        proj_lambda, playdoit_line, inp.market_type
    )

    sharp_base_over, _ = quitar_margen_casa(sharp_over_odds, sharp_under_odds)
    sharp_adj_over, sharp_adj_under = ajustar_prob_sharp_por_diferencia_de_linea(
        sharp_base_over, sharp_line, playdoit_line, inp.market_type
    )

    # Mezcla Bayesiana conservadora: 55% Ancla Sharp (FanDuel) + 45% Modelo Poisson con Noticias
    # (Si hay catalizador de lesión recién salido, damos 60% de peso al modelo porque el mercado aún ajusta)
    peso_modelo = 0.60 if (inp.teammate_out_name or inp.minutes_cap_news) else 0.40
    blended_over = (peso_modelo * model_p_over) + ((1.0 - peso_modelo) * sharp_adj_over)
    blended_under = (peso_modelo * model_p_under) + ((1.0 - peso_modelo) * sharp_adj_under)

    candidatos = [
        ("OVER", playdoit_over_odds, model_p_over, sharp_adj_over, blended_over, config.MIN_EV_OVER),
        ("UNDER", playdoit_under_odds, model_p_under, sharp_adj_under, blended_under, config.MIN_EV_UNDER),
    ]

    mejor_resultado: Optional[PropEvaluationResult] = None

    for side, odds, m_prob, s_prob, b_prob, min_ev_req in candidatos:
        # Filtro 1: Regla del 80% de retiro de Playdoit (cuota >= 1.77 / -130)
        if odds < config.MIN_PLAYDOIT_ODDS_DECIMAL or odds > config.MAX_PLAYDOIT_ODDS_DECIMAL:
            continue

        # Filtro 2: Nunca comprar una línea de puntos peor que la de FanDuel
        if side == "OVER" and playdoit_line > sharp_line:
            continue
        if side == "UNDER" and playdoit_line < sharp_line:
            continue

        ev = (b_prob * odds) - 1.0
        if ev < min_ev_req:
            continue

        has_catalyst = bool(inp.teammate_out_name or inp.minutes_cap_news)
        score = calcular_score_1_a_10(
            ev_pct=ev,
            side=side,
            playdoit_line=playdoit_line,
            sharp_line=sharp_line,
            has_injury_catalyst=has_catalyst,
            game_spread=inp.game_spread,
            is_starter=inp.is_starter,
        )

        # Cuota mínima aceptable (donde EV sigue siendo >= +3.5%)
        min_odds = max(config.MIN_PLAYDOIT_ODDS_DECIMAL, round(1.035 / b_prob, 2))
        stake = calcular_stake_playdoit(b_prob, odds)

        if abs(sharp_line - playdoit_line) >= 0.5:
            notas_alerta = [f"Línea atrasada en Playdoit ({playdoit_line}) vs FanDuel ({sharp_line})"] + notas
        else:
            notas_alerta = [f"Cuota desfasada en Playdoit ({odds:.2f}) frente al consenso Sharp"] + notas

        res = PropEvaluationResult(
            player_name=inp.player_name,
            team_abbr=inp.team_abbr,
            opponent_abbr=inp.opponent_abbr,
            market_type=inp.market_type,
            side=side,
            playdoit_line=playdoit_line,
            playdoit_odds=odds,
            sharp_line=sharp_line,
            sharp_fair_prob=round(s_prob, 4),
            projected_minutes=proj_min,
            projected_mean_lambda=proj_lambda,
            model_prob=round(m_prob, 4),
            blended_prob=round(b_prob, 4),
            ev_pct=round(ev, 4),
            score_1_to_10=score,
            suggested_stake_mxn=stake,
            min_acceptable_odds=min_odds,
            catalyst_reason=" | ".join(notas_alerta),
        )

        if mejor_resultado is None or res.score_1_to_10 > mejor_resultado.score_1_to_10:
            mejor_resultado = res

    return mejor_resultado
