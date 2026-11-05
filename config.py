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
# 2. API KEYS DE DATOS Y LLM (OPCIONAL, TIER GRATUITO)
# ==============================================================================
# The Odds API (Plan gratuito: 500 créditos/mes -> https://the-odds-api.com)
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

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
