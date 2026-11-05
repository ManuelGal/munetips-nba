"""
Motor Matemático Cuantitativo para NBA Player Props (Rebotes y Asistencias).
Implementa:
1. Proyección sin doble conteo: parte de la media implícita limpia del mercado Sharp (FanDuel/Pinnacle)
   y SOLO suma minutos extra si la baja acaba de ocurrir en esta ventana (Recién Descartado).
2. Asimetría contractual de Playdoit.mx (Sin Prop Protect):
   - Penaliza la probabilidad real de todos los OVERS en -1.8% por riesgo de lesión en 1ra mitad / faltas.
   - Bonifica la probabilidad real de todos los UNDERS en +1.8% + descuento bimodal por Blowout (Spread).
3. Distribución Binomial Negativa (Poisson sobredispersa) en Python puro.
"""
import math
from dataclasses import dataclass
from typing import Optional
import config

# Probabilidad histórica NBA de que un jugador salga antes de tiempo por lesión/faltas/expulsión (~1.8%)
PLAYDOIT_EARLY_EXIT_PROB = 0.018


@dataclass
class PlayerProjectionInput:
    player_name: str
    team_abbr: str
    opponent_abbr: str
    market_type: str               # "player_rebounds" | "player_assists"
    is_starter: bool
    base_minutes: float
    rate_per_minute: float
    team_pace: float
    opp_pace: float
    game_spread: float
    teammate_out_name: Optional[str] = None
    teammate_out_minutes: float = 0.0
    is_breaking_injury: bool = False  # True SOLO si acaba de cambiar a OUT en los últimos 30 min
    usage_boost_factor: float = 1.0
    minutes_cap_news: Optional[float] = None
    is_two_way_contract: bool = False


@dataclass
class PropEvaluationResult:
    player_name: str
    team_abbr: str
    opponent_abbr: str
    market_type: str
    side: str                      # "OVER" | "UNDER"
    playdoit_line: float
    playdoit_odds: float
    sharp_line: float
    sharp_fair_prob: float
    projected_minutes: float
    projected_mean_lambda: float
    model_prob: float
    blended_prob: float
    ev_pct: float
    score_1_to_10: float
    suggested_stake_mxn: float
    min_acceptable_odds: float
    catalyst_reason: str


def proyectar_minutos_y_media(inp: PlayerProjectionInput) -> tuple[float, float, list[str]]:
    """
    Calcula E[Minutos] y E[Estadística] (λ).
    IMPORTANTE: Si la baja de un compañero ya era conocida desde ayer (is_breaking_injury=False),
    NO vuelve a sumar minutos encima de la línea de FanDuel (evita el doble conteo que inflaba los OVERS).
    """
    notas = []
    minutos = inp.base_minutes
    factor_uso = 1.0

    # 1. SOLO sumar shock de minutos si la baja acaba de ocurrir en esta ventana (Recién Descartado)
    if inp.is_breaking_injury and inp.teammate_out_name and inp.teammate_out_minutes >= 22.0:
        if not inp.is_starter:
            extra_min = min(7.5, inp.teammate_out_minutes * 0.22)
            minutos += extra_min
            notas.append(
                f"Baja de última hora de {inp.teammate_out_name} libera +{extra_min:.1f} min aún no ajustados"
            )
        else:
            extra_min = min(2.5, inp.teammate_out_minutes * 0.07)
            minutos += extra_min
            factor_uso = inp.usage_boost_factor
            notas.append(
                f"Baja recién confirmada de {inp.teammate_out_name} sube uso (+{(factor_uso - 1)*100:.0f}%)"
            )

    # 2. Descuento bimodal por riesgo de paliza (Blowout según Spread) -> Favorece UNDERS de titulares
    abs_spread = abs(inp.game_spread)
    if abs_spread >= config.BLOWOUT_SPREAD_THRESHOLD:
        exceso_spread = abs_spread - config.BLOWOUT_SPREAD_THRESHOLD + 1.5
        if inp.is_starter:
            penalizacion = exceso_spread * config.BLOWOUT_MINUTES_PENALTY_PER_PT
            minutos = max(18.0, minutos - penalizacion)
            notas.append(f"Riesgo de Blowout (Spread {inp.game_spread:+.1f}) recorta -{penalizacion:.1f} min")

    # 3. Restricción explícita de minutos reportada por periodista (Minutes Cap) -> Favorece UNDER
    if inp.minutes_cap_news is not None and inp.minutes_cap_news > 0:
        if minutos > inp.minutes_cap_news:
            notas.append(f"Restricción médica: tope de {inp.minutes_cap_news:.0f} min")
            minutos = inp.minutes_cap_news

    minutos = max(8.0, min(40.0, minutos))
    media_lambda = minutos * inp.rate_per_minute * factor_uso

    return round(minutos, 2), round(media_lambda, 3), notas


def _nbinom_pmf(k: int, mu: float, alpha: float) -> float:
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    if alpha <= 1e-6:
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
    alpha = (
        config.OVERDISPERSION_ALPHA_REB
        if market_type == "player_rebounds"
        else config.OVERDISPERSION_ALPHA_AST
    )

    es_linea_entera = abs(linea - round(linea)) < 1e-4
    limite_under = int(round(linea)) - 1 if es_linea_entera else int(math.floor(linea))

    prob_under = sum(_nbinom_pmf(k, media_lambda, alpha) for k in range(0, limite_under + 1))
    prob_push = _nbinom_pmf(int(round(linea)), media_lambda, alpha) if es_linea_entera else 0.0
    prob_over = max(0.0, 1.0 - prob_under - prob_push)

    denom = prob_over + prob_under
    if denom > 0 and es_linea_entera:
        prob_over /= denom
        prob_under /= denom

    return min(0.99, max(0.01, prob_over)), min(0.99, max(0.01, prob_under))


def quitar_margen_casa(cuota_over: float, cuota_under: float) -> tuple[float, float]:
    imp_over = 1.0 / cuota_over
    imp_under = 1.0 / cuota_under
    total = imp_over + imp_under
    if total <= 0:
        return 0.5, 0.5
    return imp_over / total, imp_under / total


def calcular_lambda_implicita_sharp(
    sharp_prob_over_en_su_linea: float,
    sharp_line: float,
    market_type: str,
) -> float:
    low, high = 0.3, 25.0
    for _ in range(18):
        mid = (low + high) / 2.0
        p_ov, _ = calcular_probabilidad_linea(mid, sharp_line, market_type)
        if p_ov < sharp_prob_over_en_su_linea:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def ajustar_prob_sharp_por_diferencia_de_linea(
    sharp_prob_over_en_su_linea: float,
    sharp_line: float,
    playdoit_line: float,
    market_type: str,
) -> tuple[float, float]:
    if abs(sharp_line - playdoit_line) < 0.05:
        return sharp_prob_over_en_su_linea, 1.0 - sharp_prob_over_en_su_linea

    sharp_implied_lambda = calcular_lambda_implicita_sharp(
        sharp_prob_over_en_su_linea, sharp_line, market_type
    )
    return calcular_probabilidad_linea(sharp_implied_lambda, playdoit_line, market_type)


def aplicar_asimetria_lesion_playdoit(prob_over: float, prob_under: float) -> tuple[float, float]:
    """
    En Playdoit NO existe 'Prop Protect': si el jugador se lesiona o se llena de faltas en el 1er tiempo,
    el OVER pierde automáticamente y el UNDER gana automáticamente (~1.8% de los partidos).
    """
    real_over = prob_over * (1.0 - PLAYDOIT_EARLY_EXIT_PROB)
    real_under = prob_under + (prob_over * PLAYDOIT_EARLY_EXIT_PROB)
    return min(0.99, max(0.01, real_over)), min(0.99, max(0.01, real_under))


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
    Escala calibrada 1 a 10 para el canal Muñetips:
    Toda jugada que ya superó el umbral estricto de EV (+4.2% en UNDER o +6.0% en OVER)
    arranca en base 5.5 y suma puntos por magnitud de EV, diferencia de línea y reglas de Playdoit.
    """
    # Base para apuestas con EV neto positivo comprobado sobre la línea sin vig de FanDuel
    if ev_pct < 0.015:
        pts_ev = 4.5 + (ev_pct / 0.015) * 1.0
    elif ev_pct < 0.035:
        pts_ev = 5.8 + ((ev_pct - 0.015) / 0.020) * 1.1
    elif ev_pct < 0.065:
        pts_ev = 6.9 + ((ev_pct - 0.035) / 0.030) * 1.0
    else:
        pts_ev = min(8.5, 7.9 + (ev_pct - 0.065) * 5.0)

    ventaja_puntos = (sharp_line - playdoit_line) if side == "OVER" else (playdoit_line - sharp_line)
    if ventaja_puntos >= 1.0:
        pts_linea = 1.5
    elif ventaja_puntos >= 0.5:
        pts_linea = 1.1
    elif abs(ventaja_puntos) < 0.1:
        pts_linea = 0.4
    else:
        pts_linea = 0.0

    pts_contexto = 0.0
    if has_injury_catalyst:
        pts_contexto += 0.7

    if side == "UNDER":
        pts_contexto += 0.6  # Prioridad estructural a UNDERS en Playdoit por regla de lesión/faltas
        if is_starter and abs(game_spread) >= 9.5:
            pts_contexto += 0.4
    else:
        if not is_starter and has_injury_catalyst:
            pts_contexto += 0.4

    raw_score = pts_ev + pts_linea + pts_contexto
    return round(min(9.9, max(1.0, raw_score)), 1)


def calcular_stake_playdoit(prob_ganar: float, cuota_decimal: float) -> float:
    b = cuota_decimal - 1.0
    if b <= 0:
        return 0.0
    kelly_puro = (prob_ganar * b - (1.0 - prob_ganar)) / b
    if kelly_puro <= 0:
        return 0.0

    fraccion = min(config.MAX_STAKE_PCT, kelly_puro * config.KELLY_FRACTION)
    monto_bruto = config.BANKROLL_MXN * fraccion
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
    if inp.is_two_way_contract or playdoit_over_odds is None or playdoit_under_odds is None:
        return None

    proj_min, proj_lambda, notas = proyectar_minutos_y_media(inp)
    raw_model_over, raw_model_under = calcular_probabilidad_linea(
        proj_lambda, playdoit_line, inp.market_type
    )

    sharp_base_over, _ = quitar_margen_casa(sharp_over_odds, sharp_under_odds)
    raw_sharp_over, raw_sharp_under = ajustar_prob_sharp_por_diferencia_de_linea(
        sharp_base_over, sharp_line, playdoit_line, inp.market_type
    )

    # Aplicar la asimetría real de reglas de Playdoit (+1.8% al UNDER, -1.8% al OVER por lesión en vivo)
    model_p_over, model_p_under = aplicar_asimetria_lesion_playdoit(raw_model_over, raw_model_under)
    sharp_adj_over, sharp_adj_under = aplicar_asimetria_lesion_playdoit(raw_sharp_over, raw_sharp_under)

    peso_modelo = 0.55 if (inp.is_breaking_injury or inp.minutes_cap_news) else 0.45
    blended_over = (peso_modelo * model_p_over) + ((1.0 - peso_modelo) * sharp_adj_over)
    blended_under = (peso_modelo * model_p_under) + ((1.0 - peso_modelo) * sharp_adj_under)

    candidatos = [
        ("UNDER", playdoit_under_odds, model_p_under, sharp_adj_under, blended_under, config.MIN_EV_UNDER),
        ("OVER", playdoit_over_odds, model_p_over, sharp_adj_over, blended_over, config.MIN_EV_OVER),
    ]

    mejor_resultado: Optional[PropEvaluationResult] = None

    for side, odds, m_prob, s_prob, b_prob, min_ev_req in candidatos:
        if odds < config.MIN_PLAYDOIT_ODDS_DECIMAL or odds > config.MAX_PLAYDOIT_ODDS_DECIMAL:
            continue

        if side == "OVER" and playdoit_line > sharp_line:
            continue
        if side == "UNDER" and playdoit_line < sharp_line:
            continue

        ev = (b_prob * odds) - 1.0
        if ev < min_ev_req:
            continue

        has_catalyst = bool(inp.is_breaking_injury or inp.minutes_cap_news)
        score = calcular_score_1_a_10(
            ev_pct=ev,
            side=side,
            playdoit_line=playdoit_line,
            sharp_line=sharp_line,
            has_injury_catalyst=has_catalyst,
            game_spread=inp.game_spread,
            is_starter=inp.is_starter,
        )

        min_odds = max(config.MIN_PLAYDOIT_ODDS_DECIMAL, round(1.035 / b_prob, 2))
        stake = calcular_stake_playdoit(b_prob, odds)

        notas_alerta = list(notas)
        if abs(sharp_line - playdoit_line) >= 0.5:
            notas_alerta.insert(0, f"Línea atrasada en Playdoit ({playdoit_line}) vs FanDuel ({sharp_line})")
        elif side == "UNDER":
            notas_alerta.insert(0, f"Valor en UNDER ({odds:.2f}) + bono de +1.8% por regla de lesión Playdoit")
        else:
            notas_alerta.insert(0, f"Cuota desfasada en Playdoit ({odds:.2f}) vs consenso Sharp")

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
