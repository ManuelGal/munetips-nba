"""
Configuración central del Agente Cuantitativo NBA Player Props -> Canal 'Muñetips'
Optimizado para Playdoit.mx (motor Altenar) y consumo CERO/MÍNIMO de tokens.
"""
import os

# ==============================================================================
# 1. IDENTIDAD Y CANAL DE TELEGRAM ("Muñetips")
# ==============================================================================
CHANNEL_NAME = "Muñetips"
USER_PHONE_REFERENCE = "+525539936844"

# Se leen desde variables de entorno (GitHub Secrets en la nube) o archivo .env local
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "@munetipsfc").strip()  # Canal: https://t.me/munetipsfc

# ==============================================================================
# 2. API KEYS DE DATOS CON ROTACIÓN AUTOMÁTICA Y BLINDAJE DE CRÉDITOS
# ==============================================================================
# The Odds API (Plan gratuito: 500 créditos/mes por cada API Key -> https://the-odds-api.com)
# Soporta rotación automática entre múltiples llaves (separadas por coma en ODDS_API_KEYS,
# variables numeradas ODDS_API_KEY_1..5, ODDS_API_KEY y el pool de respaldo).
DEFAULT_ODDS_API_KEYS_POOL = [
    "ddfb2ab97e7c6a3d0e04d20c38206b3b",
]


def obtener_pool_odds_api_keys() -> list[str]:
    """
    Construye la lista ordenada y sin duplicados de API Keys disponibles para The Odds API.
    Permite rotar automáticamente cuando una llave baja de 3 créditos o devuelve HTTP 401/429.
    """
    candidatas: list[str] = []
    # 1. Lista explícita separada por comas en ODDS_API_KEYS
    multi_env = os.getenv("ODDS_API_KEYS", "").strip()
    if multi_env:
        for k in multi_env.split(","):
            if k.strip():
                candidatas.append(k.strip())

    # 2. Llaves numeradas ODDS_API_KEY_1 .. ODDS_API_KEY_5
    for idx in range(1, 6):
        k_idx = os.getenv(f"ODDS_API_KEY_{idx}", "").strip()
        if k_idx:
            candidatas.append(k_idx)

    # 3. Pool activo integrado
    for k_def in DEFAULT_ODDS_API_KEYS_POOL:
        if k_def.strip():
            candidatas.append(k_def.strip())

    # 4. Variable clásica ODDS_API_KEY (admite también varias separadas por coma)
    single_env = os.getenv("ODDS_API_KEY", "").strip()
    if single_env:
        for k in single_env.split(","):
            if k.strip():
                candidatas.append(k.strip())

    # Deduplicar preservando orden
    pool_unico: list[str] = []
    for k in candidatas:
        if k not in pool_unico:
            pool_unico.append(k)
    return pool_unico


ODDS_API_KEYS_POOL = obtener_pool_odds_api_keys()
ODDS_API_KEY = ODDS_API_KEYS_POOL[0] if ODDS_API_KEYS_POOL else ""
MIN_CREDITS_PER_GAME_QUERY = 3        # 1 región (us) x 3 mercados = 3 créditos por partido
LOW_CREDITS_ALERT_THRESHOLD = 18      # Avisar cuando el pool total baje de 18 créditos

# Gemini API (Opcional, Tier Gratuito de Google AI Studio -> 0 costo)
# Solo se invoca si una apuesta YA superó el filtro matemático (máx ~150 tokens/alerta)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
USE_LLM_NEWS_CHECK = bool(GEMINI_API_KEY)

# ==============================================================================
# 3. REGLAS ESPECÍFICAS DE PLAYDOIT.MX (MOTOR ALTENAR)
# ==============================================================================
# Regla de retiro de Playdoit: 80% del depósito debe apostarse a momio >= -130 (1.769 decimal)
MIN_PLAYDOIT_ODDS_DECIMAL = 1.77
MAX_PLAYDOIT_ODDS_DECIMAL = 2.45

# Cuota estándar de referencia en Playdoit (Altenar) para líneas principales equilibradas (-115 = 1.87)
PLAYDOIT_STANDARD_BENCHMARK_ODDS = 1.87

# Asimetría real de reglas pre-partido en Playdoit.mx:
# - Si el jugador sale lesionado en la 1ra mitad y no regresa (sin haber superado la línea):
#   el OVER se ANULA/REEMBOLSA (Void = ~1.0%), mientras que el UNDER GANA.
# - Si el jugador sale lesionado en la 2da mitad o por faltas (~0.8%):
#   el OVER PIERDE y el UNDER GANA.
PLAYDOIT_FIRST_HALF_VOID_PROB = 0.010
PLAYDOIT_SECOND_HALF_LOSS_PROB = 0.008

# Filtro estricto de ventaja matemática (EV neto mínimo requerido):
MIN_EV_OVER = 0.055   # +5.5% Valor Esperado neto mínimo para OVERS
MIN_EV_UNDER = 0.042  # +4.2% Valor Esperado neto mínimo para UNDERS

# Filtro de alerta al celular: solo notificar en "Muñetips" si el Score (1 a 10) >= 7.0
MIN_SCORE_FOR_ALERT = 7.0

# ==============================================================================
# 4. GESTIÓN DE BANCA Y CAMUFLAJE ANTI-LIMITACIÓN (ALTENAR)
# ==============================================================================
BANKROLL_MXN = float(os.getenv("BANKROLL_MXN", "200.0"))
MIN_STAKE_MXN = 20.0          # Apuesta mínima operativa
MAX_STAKE_PCT = 0.12          # Con banca micro ($200 MXN), tope de $20-$30 MXN por jugada
KELLY_FRACTION = 0.25         # 1/4 de Kelly para proteger contra varianza
STAKE_ROUNDING_BASE = 10.0    # Redondear siempre a múltiplos de $10 MXN ($20, $30, $40)

# ==============================================================================
# 5. PARÁMETROS DEL MODELO CUANTITATIVO (POISSON / NEGATIVE BINOMIAL COMPUESTA)
# ==============================================================================
LEAGUE_AVG_PACE = 99.5
BLOWOUT_SPREAD_THRESHOLD = 9.5        # A partir de -9.5 pts de spread, sube el riesgo de banca en 4to cuarto
BLOWOUT_MINUTES_PENALTY_PER_PT = 0.35 # Minutos descontados al titular por cada punto de spread >= 9.5
OVERDISPERSION_ALPHA_PTS = 0.09       # Sobredispersión de eventos de anotación (puntos compuestos 1, 2 y 3 pts)
OVERDISPERSION_ALPHA_REB = 0.08       # Factor de sobredispersión para Rebotes (Binomial Negativa)
OVERDISPERSION_ALPHA_AST = 0.06       # Factor de sobredispersión para Asistencias

# Mercados objetivo que SÍ abre Playdoit bajo las pestañas "... de Jugador Más de/Menos de (incl. prórroga)"
TARGET_MARKETS = [
    "player_points",
    "player_rebounds",
    "player_assists",
]

# Rutas de almacenamiento de estado y bitácora CLV
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STATE_CACHE_FILE = os.path.join(DATA_DIR, "injury_state_cache.json")
PLAYER_STATS_CACHE_FILE = os.path.join(DATA_DIR, "player_stats_cache.json")
PICKS_LOG_CSV = os.path.join(DATA_DIR, "munetips_historial_clv.csv")
