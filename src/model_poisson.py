"""
Motor Matemático Cuantitativo para NBA Player Props (Puntos, Rebotes y Asistencias).
Implementa:
1. Modelo Estadístico Independiente basado en tasas reales de temporada (ESPN Overview API),
   separado de la media implícita Sharp (evita el doble conteo de spread/lesiones).
2. Sinergia Posicional Múltiple: distribuye minutos y uso según posición (Guard / Wing / Big)
   y tipo de mercado cuando hay 1 o más compañeros titulares/rotación fuera (OUT/DOUBTFUL).
3. Devigging por Método de Potencia (Power Method) para eliminar el vig de las casas Sharp
   penalizando correctamente el sesgo de cuotas largas (longshot bias).
4. Distribución Binomial Negativa Compuesta (recursión exacta de Panjer con saltos de 1, 2 y 3 pts)
   para Puntos de Jugador, y Binomial Negativa para conteos unitarios (Rebotes y Asistencias).
5. Regla Contractual Exacta de Playdoit.mx en Pre-Partido:
   - Lesión en 1ra Mitad (~1.0%): el OVER se ANULA/REEMBOLSA (Void = 0), el UNDER GANA (+1).
   - Lesión en 2da Mitad / Faltas (~0.8%): el OVER PIERDE (-1), el UNDER GANA (+1).
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import config


@dataclass
class TeammateOutInfo:
    player_name: str
    position: str
    avg_minutes: float
    pts_per_min: float = 0.45
    reb_per_min: float = 0.18
    ast_per_min: float = 0.12
    is_newly_out: bool = False


@dataclass
class PlayerProjectionInput:
    player_name: str
    team_abbr: str
    opponent_abbr: str
    market_type: str               # "player_points" | "player_rebounds" | "player_assists"
    is_starter: bool
    base_minutes: float
    rate_per_minute: float
    team_pace: float
    opp_pace: float
    game_spread: float
    position: str = "G"
    teammates_out: list[Any] = field(default_factory=list)
    teammate_out_name: Optional[str] = None
    teammate_out_minutes: float = 0.0
    is_breaking_injury: bool = False
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


def _grupo_posicional(pos: str) -> str:
    p = (pos or "G").upper().strip()
    if p in ("PG", "SG", "G"):
        return "GUARD"
    if p in ("C", "PF", "F-C", "C-F"):
        return "BIG"
    return "WING"


def _calcular_sinergia_posicional(
    pos_jugador: str,
    es_titular: bool,
    market_type: str,
    baja_pos: str,
    baja_min: float,
    baja_reb_pm: float,
    baja_ast_pm: float,
) -> tuple[float, float]:
    """
    Calcula cuántos minutos extra y qué porcentaje de impulso de tasa por minuto (usage/rate boost)
    recibe un jugador activo cuando sale un compañero de rotación, según su sinergia posicional.
    """
    g_jug = _grupo_posicional(pos_jugador)
    g_baja = _grupo_posicional(baja_pos)

    if g_jug == g_baja:
        afinidad_min = 1.0
    elif "WING" in (g_jug, g_baja):
        afinidad_min = 0.60
    else:
        afinidad_min = 0.25

    if not es_titular:
        extra_min = min(4.0, baja_min * 0.12 * afinidad_min)
    else:
        extra_min = min(2.0, baja_min * 0.05 * afinidad_min)

    boost_tasa = 0.0
    if market_type == "player_assists":
        es_creador = (g_baja == "GUARD") or (baja_ast_pm >= 0.16)
        if es_creador:
            if g_jug == "GUARD":
                boost_tasa = 0.04
            elif g_jug == "WING":
                boost_tasa = 0.025
            else:
                boost_tasa = 0.015
        else:
            boost_tasa = 0.01
    elif market_type == "player_rebounds":
        # En rebotes la tasa por minuto (TRB%) es estacionaria; el volumen sube solo por minutos extra
        boost_tasa = 0.0
    else:  # player_points
        if g_jug == g_baja:
            boost_tasa = 0.04
        elif "WING" in (g_jug, g_baja):
            boost_tasa = 0.025
        else:
            boost_tasa = 0.015

    return extra_min, boost_tasa


def proyectar_minutos_y_media(inp: PlayerProjectionInput) -> tuple[float, float, list[str]]:
    """
    Modelo estadístico independiente de E[Minutos] y E[Estadística] (λ).
    Usa los promedios reales de temporada del jugador, aplica sinergia posicional para
    todas las bajas del equipo, ritmo (Pace) y descuento por riesgo de Blowout (Spread).
    """
    notas: list[str] = []
    minutos = inp.base_minutes
    factor_uso = 1.0

    # Normalizar lista de bajas (soporta lista completa de perfiles o campos simples de demo)
    lista_bajas = list(inp.teammates_out) if inp.teammates_out else []
    if not lista_bajas and inp.teammate_out_name and inp.teammate_out_minutes >= 20.0:
        lista_bajas.append(
            TeammateOutInfo(
                player_name=inp.teammate_out_name,
                position=inp.position,
                avg_minutes=inp.teammate_out_minutes,
                is_newly_out=inp.is_breaking_injury,
            )
        )

    total_extra_min = 0.0
    total_boost_tasa = 0.0
    nombres_bajas_recientes: list[str] = []
    nombres_bajas_previas: list[str] = []

    for b in lista_bajas:
        b_min = float(getattr(b, "avg_minutes", 0.0) or 0.0)
        if b_min < 20.0:
            continue
        b_name = getattr(b, "player_name", "Titular")
        b_pos = getattr(b, "position", inp.position)
        b_reb = float(getattr(b, "reb_per_min", 0.18) or 0.18)
        b_ast = float(getattr(b, "ast_per_min", 0.12) or 0.12)
        b_new = bool(getattr(b, "is_newly_out", False))

        ex_m, ex_boost = _calcular_sinergia_posicional(
            pos_jugador=inp.position,
            es_titular=inp.is_starter,
            market_type=inp.market_type,
            baja_pos=b_pos,
            baja_min=b_min,
            baja_reb_pm=b_reb,
            baja_ast_pm=b_ast,
        )
        # IMPORTANTE: Como `base_minutes` y `rate_per_minute` provienen de los promedios reales de
        # la temporada actual (ESPN Overview API), las bajas antiguas (`b_new == False`) YA están
        # absorbidas en los minutos promedio del jugador. Solo aplicamos el 100% del shock posicional
        # cuando la baja es RECIENTE (`b_new == True`), evitando inflar artificialmente los OVERS.
        if b_new:
            total_extra_min += ex_m
            total_boost_tasa += ex_boost
            nombres_bajas_recientes.append(b_name)
        else:
            nombres_bajas_previas.append(b_name)

    tope_extra_min = 4.5 if not inp.is_starter else 2.5
    total_extra_min = min(tope_extra_min, total_extra_min)
    total_boost_tasa = min(0.08, total_boost_tasa)

    if total_extra_min > 0.2 or total_boost_tasa > 0.01:
        minutos += total_extra_min
        factor_uso += total_boost_tasa
        if inp.usage_boost_factor > factor_uso and not inp.teammates_out:
            factor_uso = inp.usage_boost_factor

        if nombres_bajas_recientes:
            bajas_str = ", ".join(nombres_bajas_recientes[:2])
            notas.append(
                f"Baja de última hora ({bajas_str}) libera +{total_extra_min:.1f} min y +{(factor_uso - 1)*100:.0f}% uso"
            )
        elif nombres_bajas_previas:
            bajas_str = ", ".join(nombres_bajas_previas[:2])
            notas.append(
                f"Rotación sin {bajas_str} (+{total_extra_min:.1f} min proyectados)"
            )

    # 2. Descuento bimodal por riesgo de paliza (Blowout según Spread) en el modelo estadístico
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

    # 4. Ajuste por ritmo de juego (Pace)
    ritmo_esperado = (inp.team_pace + inp.opp_pace) / 2.0
    factor_ritmo = ritmo_esperado / max(85.0, config.LEAGUE_AVG_PACE)

    minutos = max(8.0, min(38.5, minutos))
    media_lambda = minutos * inp.rate_per_minute * factor_uso * factor_ritmo

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


def _compound_points_pmf(max_k: int, mu_points: float, alpha_events: float) -> list[float]:
    """
    Distribución Binomial Negativa Compuesta para Puntos de Jugador (recursión exacta de Panjer).
    En la NBA, un jugador no anota de 1 en 1 como en rebotes: cada posesión anotadora suma
    1 punto (tiro libre, p1=0.22), 2 puntos (doble, p2=0.52) o 3 puntos (triple, p3=0.26),
    con salto medio E[Y] = 2.04 puntos por evento anotador.
    Esto captura exactamente la mayor varianza de Puntos (Fano ~ 2.27 + alpha * mu).
    """
    if mu_points <= 0:
        res = [0.0] * (max_k + 1)
        res[0] = 1.0
        return res

    f1, f2, f3 = 0.22, 0.52, 0.26
    mean_jump = (1.0 * f1) + (2.0 * f2) + (3.0 * f3)  # 2.04 puntos por evento anotador
    mu_events = mu_points / mean_jump

    r = 1.0 / max(1e-4, alpha_events)
    p = r / (r + mu_events)
    q = 1.0 - p

    # Panjer recursion for Compound Negative Binomial:
    # g_0 = p^r
    # g_k = (1 / k) * q * sum_{j=1}^{min(k,3)} (r * j + k - j) * f_j * g_{k - j}
    g = [0.0] * (max_k + 1)
    g[0] = math.exp(r * math.log(p))
    jumps = ((1, f1), (2, f2), (3, f3))

    for k in range(1, max_k + 1):
        acc = 0.0
        for j, fj in jumps:
            if k >= j:
                acc += (r * j + (k - j)) * fj * g[k - j]
        g[k] = (q / k) * acc

    return g


def calcular_probabilidad_linea(
    media_lambda: float,
    linea: float,
    market_type: str,
) -> tuple[float, float]:
    es_linea_entera = abs(linea - round(linea)) < 1e-4
    limite_under = int(round(linea)) - 1 if es_linea_entera else int(math.floor(linea))
    max_k = max(limite_under + 2, int(round(linea)) + 1)

    if market_type == "player_points":
        alpha_pts = getattr(config, "OVERDISPERSION_ALPHA_PTS", 0.09)
        pmf_vec = _compound_points_pmf(max_k, media_lambda, alpha_pts)
        prob_under = sum(pmf_vec[k] for k in range(0, limite_under + 1))
        prob_push = pmf_vec[int(round(linea))] if es_linea_entera else 0.0
    else:
        alpha = (
            config.OVERDISPERSION_ALPHA_REB
            if market_type == "player_rebounds"
            else config.OVERDISPERSION_ALPHA_AST
        )
        prob_under = sum(_nbinom_pmf(k, media_lambda, alpha) for k in range(0, limite_under + 1))
        prob_push = _nbinom_pmf(int(round(linea)), media_lambda, alpha) if es_linea_entera else 0.0

    prob_over = max(0.0, 1.0 - prob_under - prob_push)

    denom = prob_over + prob_under
    if denom > 0 and es_linea_entera:
        prob_over /= denom
        prob_under /= denom

    return min(0.99, max(0.01, prob_over)), min(0.99, max(0.01, prob_under))


def quitar_margen_casa(cuota_over: float, cuota_under: float) -> tuple[float, float]:
    """
    Elimina el margen (overround/vig) usando el Método de Potencia (Power Method):
    encuentra el exponente k > 0 tal que (1/O_over)^k + (1/O_under)^k = 1.
    Corrige el sesgo de cuotas largas (longshot bias) de las casas de apuestas.
    """
    if cuota_over <= 1.01 or cuota_under <= 1.01:
        return 0.5, 0.5
    q_over = 1.0 / cuota_over
    q_under = 1.0 / cuota_under
    if q_over + q_under <= 0:
        return 0.5, 0.5

    low_k, high_k = 0.5, 4.0
    for _ in range(25):
        mid_k = (low_k + high_k) / 2.0
        s = (q_over ** mid_k) + (q_under ** mid_k)
        if s > 1.0:
            low_k = mid_k
        else:
            high_k = mid_k
    k_opt = (low_k + high_k) / 2.0
    p_over = q_over ** k_opt
    p_under = 1.0 - p_over
    return p_over, p_under


def calcular_lambda_implicita_sharp(
    sharp_prob_over_en_su_linea: float,
    sharp_line: float,
    market_type: str,
) -> float:
    low, high = 0.3, 52.0
    for _ in range(22):
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


def aplicar_asimetria_lesion_playdoit(
    prob_over_sano: float = 0.5,
    prob_under_sano: float = 0.5,
    raw_prob_over: Optional[float] = None,
    raw_prob_under: Optional[float] = None,
) -> tuple[float, float, float]:
    """
    Modela la regla contractual real de Playdoit.mx (Altenar) en pre-partido:
    - Si el jugador sale lesionado en la 1ra mitad y no regresa (~1.0% = p_void_1h):
      * El OVER se ANULA / REEMBOLSA (Void, retorno neto = 0).
      * El UNDER GANA (+1).
    - Si el jugador sale lesionado en la 2da mitad o por faltas (~0.8% = p_loss_2h):
      * El OVER PIERDE (-1).
      * El UNDER GANA (+1).
    Devuelve: (prob_win_over, prob_win_under, prob_void_over)
    """
    p_ov = raw_prob_over if raw_prob_over is not None else prob_over_sano
    p_un = raw_prob_under if raw_prob_under is not None else prob_under_sano

    p_void_1h = getattr(config, "PLAYDOIT_FIRST_HALF_VOID_PROB", 0.010)
    p_loss_2h = getattr(config, "PLAYDOIT_SECOND_HALF_LOSS_PROB", 0.008)

    prob_void_over = p_void_1h
    prob_win_over = p_ov * (1.0 - p_void_1h - p_loss_2h)
    prob_win_under = p_un + ((1.0 - p_un) * (p_void_1h + p_loss_2h))

    return (
        min(0.99, max(0.01, prob_win_over)),
        min(0.99, max(0.01, prob_win_under)),
        min(0.05, max(0.0, prob_void_over)),
    )


def calcular_ev_real_playdoit(
    side: str,
    prob_win: float,
    prob_void: float,
    cuota_decimal: float,
) -> float:
    """
    Calcula el Valor Esperado (EV) exacto considerando la probabilidad de reembolso (Void) en 1ra mitad para OVERS:
    EV = P(Win) * (Cuota - 1) - P(Loss) * 1 + P(Void) * 0
    """
    if side == "OVER":
        prob_loss = max(0.0, 1.0 - prob_win - prob_void)
        return (prob_win * (cuota_decimal - 1.0)) - prob_loss
    prob_loss_under = max(0.0, 1.0 - prob_win)
    return (prob_win * (cuota_decimal - 1.0)) - prob_loss_under


def calcular_score_1_a_10(
    ev_pct: float,
    win_prob: float,
    side: str,
    playdoit_line: float,
    sharp_line: float,
    has_injury_catalyst: bool,
    game_spread: float,
    is_starter: bool,
) -> float:
    """
    Escala calibrada 1 a 10 para el canal Muñetips.
    Combina equilibradamente:
    1. Valor Esperado (EV %): Rentabilidad matemática neta a largo plazo (4.0 a 7.5 pts base).
    2. Probabilidad de Acierto (Win Prob): Confianza de acierto y protección de drawdown (0.0 a 1.6 pts).
    3. Desfase de Línea Real vs Sharp: Ventaja directa sobre Pinnacle/FanDuel (0.0 a 0.5 pts).
    4. Catalizadores Contextuales: Lesión confirmada y ventaja estructural Playdoit (0.0 a 0.4 pts).
    
    Esto evita que picks de 60% se inflen artificialmente al techo (9.8), reservando
    las notas 9.3 a 9.9 exclusivamente para selecciones con alta probabilidad Y alto EV.
    """
    # 1. Base por EV neto (Cero inflación si EV <= 0)
    if ev_pct <= 0.0:
        return 1.0
    if ev_pct < 0.040:
        pts_ev = 1.0 + (ev_pct / 0.040) * 4.1        # 1.0 a 5.1
    elif ev_pct < 0.080:
        pts_ev = 5.1 + ((ev_pct - 0.040) / 0.040) * 1.2  # 5.1 a 6.3
    elif ev_pct < 0.140:
        pts_ev = 6.3 + ((ev_pct - 0.080) / 0.060) * 1.0  # 6.3 a 7.3
    else:
        pts_ev = min(7.8, 7.3 + (ev_pct - 0.140) * 2.0)  # 7.3 a 7.8

    # 2. Componente de Probabilidad de Acierto (Previa penalización / premio)
    # Por debajo de 54% no suma; de 58% a 65% da soporte sólido; >68% impulsa a élite
    if win_prob < 0.54:
        pts_prob = 0.0
    elif win_prob < 0.60:
        pts_prob = 0.4 + ((win_prob - 0.54) / 0.06) * 0.4  # 0.4 a 0.8
    elif win_prob < 0.68:
        pts_prob = 0.8 + ((win_prob - 0.60) / 0.08) * 0.5  # 0.8 a 1.3
    else:
        pts_prob = min(1.6, 1.3 + ((win_prob - 0.68) / 0.10) * 0.3)  # 1.3 a 1.6

    # 3. Ventaja de Línea continua sobre el Consenso Sharp (sin escalones artificiales)
    ventaja_puntos = (sharp_line - playdoit_line) if side == "OVER" else (playdoit_line - sharp_line)
    pts_linea = min(0.6, max(-0.6, ventaja_puntos * 0.4))

    # 4. Catalizador Contextual (sin doble conteo del bono genérico de Under)
    pts_contexto = 0.0
    if has_injury_catalyst:
        pts_contexto += 0.35

    if side == "UNDER" and is_starter and abs(game_spread) >= 9.5:
        pts_contexto += 0.15  # Riesgo real de blowout en 4to cuarto

    raw_score = pts_ev + pts_prob + pts_linea + pts_contexto
    return round(min(9.9, max(1.0, raw_score)), 1)


def calcular_stake_playdoit(
    prob_win: float,
    prob_void: float,
    cuota_decimal: float,
) -> float:
    b = cuota_decimal - 1.0
    if b <= 0:
        return 0.0
    prob_loss = max(0.0, 1.0 - prob_win - prob_void)
    denom_resuelta = max(0.01, 1.0 - prob_void)
    p_cond = prob_win / denom_resuelta
    q_cond = prob_loss / denom_resuelta

    kelly_puro = (p_cond * b - q_cond) / b
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

    # 1. Proyección estadística independiente (basada en tasas reales de temporada + sinergia posicional + spread)
    proj_min, raw_proj_lambda, notas = proyectar_minutos_y_media(inp)

    # 2. Probabilidad justa del consenso Sharp sin vig (Power Method) ajustada a la línea objetivo
    sharp_base_over, _ = quitar_margen_casa(sharp_over_odds, sharp_under_odds)
    sharp_implied_lambda = calcular_lambda_implicita_sharp(
        sharp_base_over, sharp_line, inp.market_type
    )

    # 2.1 Empirical Bayes Shrinkage: anclar la base empírica (40%) a la media implícita Sharp (60%)
    #     y aplicar únicamente sobre esa base anclada el multiplicador neto del catalizador de hoy (M_cat)
    base_emp_lambda = max(0.5, inp.base_minutes * inp.rate_per_minute)
    m_cat = raw_proj_lambda / base_emp_lambda
    proj_lambda = round(((0.60 * sharp_implied_lambda) + (0.40 * base_emp_lambda)) * m_cat, 3)

    raw_model_over, raw_model_under = calcular_probabilidad_linea(
        proj_lambda, playdoit_line, inp.market_type
    )
    raw_sharp_over, raw_sharp_under = ajustar_prob_sharp_por_diferencia_de_linea(
        sharp_base_over, sharp_line, playdoit_line, inp.market_type
    )

    # 3. Aplicar la regla contractual exacta de Playdoit (1ra mitad Void en OVER, ganancia en UNDER)
    model_win_over, model_win_under, model_void_over = aplicar_asimetria_lesion_playdoit(
        raw_model_over, raw_model_under
    )
    sharp_win_over, sharp_win_under, sharp_void_over = aplicar_asimetria_lesion_playdoit(
        raw_sharp_over, raw_sharp_under
    )

    tiene_baja_reciente = bool(
        any(getattr(b, "is_newly_out", False) for b in (inp.teammates_out or []))
        or (not inp.teammates_out and inp.is_breaking_injury)
    )
    peso_modelo = 0.55 if (tiene_baja_reciente or inp.minutes_cap_news) else 0.45

    blended_win_over = (peso_modelo * model_win_over) + ((1.0 - peso_modelo) * sharp_win_over)
    blended_void_over = (peso_modelo * model_void_over) + ((1.0 - peso_modelo) * sharp_void_over)
    blended_win_under = (peso_modelo * model_win_under) + ((1.0 - peso_modelo) * sharp_win_under)

    # Regla de Horario (Mañana vs. Tarde):
    # - En la Mañana (7:00 AM a 11:59 AM CDMX = 13:00 a 17:59 UTC): se cazan aperturas con valor.
    # - En la Tarde (después de las 12:00 PM CDMX): el mercado ya ajustó las aperturas; SOLO se dispara
    #   si hay una baja recién confirmada (tiene_baja_reciente), tope de minutos nuevo, o línea atrasada >= 0.5 pts.
    hora_utc = datetime.now(timezone.utc).hour
    es_ventana_manana = 13 <= hora_utc <= 17
    hay_desfase_de_linea = abs(sharp_line - playdoit_line) >= 0.5
    hay_catalizador_nuevo = bool(tiene_baja_reciente or inp.minutes_cap_news)

    if not es_ventana_manana and not (hay_desfase_de_linea or hay_catalizador_nuevo):
        return None

    candidatos = [
        (
            "UNDER",
            playdoit_under_odds,
            model_win_under,
            sharp_win_under,
            blended_win_under,
            0.0,
            config.MIN_EV_UNDER,
        ),
        (
            "OVER",
            playdoit_over_odds,
            model_win_over,
            sharp_win_over,
            blended_win_over,
            blended_void_over,
            config.MIN_EV_OVER,
        ),
    ]

    mejor_resultado: Optional[PropEvaluationResult] = None

    for side, odds, m_prob, s_prob, b_win_prob, b_void_prob, min_ev_req in candidatos:
        if odds < config.MIN_PLAYDOIT_ODDS_DECIMAL or odds > config.MAX_PLAYDOIT_ODDS_DECIMAL:
            continue

        if side == "OVER":
            if playdoit_line > sharp_line:
                continue
            # Guardrail anti-sobreproyección en OVER cuando no hay ventaja de línea vs Sharp
            if abs(playdoit_line - sharp_line) < 0.05 and (
                sharp_base_over < 0.485 or proj_lambda > 1.22 * sharp_line
            ):
                continue
        if side == "UNDER" and playdoit_line < sharp_line:
            continue

        ev = calcular_ev_real_playdoit(side, b_win_prob, b_void_prob, odds)
        if ev < min_ev_req:
            continue

        has_catalyst = bool(
            tiene_baja_reciente
            or inp.minutes_cap_news
            or (inp.teammates_out and len(inp.teammates_out) > 0)
            or inp.teammate_out_name
        )
        prob_efectiva_mostrar = b_win_prob / max(0.01, 1.0 - b_void_prob)
        score = calcular_score_1_a_10(
            ev_pct=ev,
            win_prob=prob_efectiva_mostrar,
            side=side,
            playdoit_line=playdoit_line,
            sharp_line=sharp_line,
            has_injury_catalyst=has_catalyst,
            game_spread=inp.game_spread,
            is_starter=inp.is_starter,
        )

        # Cuota mínima aceptable en Playdoit para mantener al menos +3.5% de EV real (y >= 1.77 / -130)
        prob_loss = max(0.0, 1.0 - b_win_prob - b_void_prob)
        cuota_min_ev = 1.0 + ((prob_loss + 0.035) / max(0.05, b_win_prob))
        min_odds = max(config.MIN_PLAYDOIT_ODDS_DECIMAL, round(cuota_min_ev, 2))

        stake = calcular_stake_playdoit(b_win_prob, b_void_prob, odds)

        notas_alerta = list(notas)
        if abs(sharp_line - playdoit_line) >= 0.5:
            notas_alerta.insert(0, f"Línea objetivo {playdoit_line} con ventaja vs consenso Sharp ({sharp_line})")
        elif side == "UNDER":
            notas_alerta.insert(
                0,
                f"Proyección {proj_lambda:.1f} por debajo de {playdoit_line} + ventaja por regla de lesión Playdoit",
            )
        else:
            notas_alerta.insert(
                0,
                f"Proyección {proj_lambda:.1f} supera línea {playdoit_line} (reembolso si sale en 1ra mitad)",
            )

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
            blended_prob=round(prob_efectiva_mostrar, 4),
            ev_pct=round(ev, 4),
            score_1_to_10=score,
            suggested_stake_mxn=stake,
            min_acceptable_odds=min_odds,
            catalyst_reason=" | ".join(notas_alerta),
        )

        if mejor_resultado is None or res.score_1_to_10 > mejor_resultado.score_1_to_10:
            mejor_resultado = res

    return mejor_resultado
