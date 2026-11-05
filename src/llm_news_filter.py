"""
Filtro de Noticias de Ultra-Bajo Consumo de Tokens (Opcional, Tier Gratuito de Gemini Flash).
REGLA DE ARQUITECTURA:
- Este módulo NUNCA se ejecuta para escanear el mercado (eso lo hace Python con 0 tokens).
- Solo se invoca cuando una apuesta YA superó el filtro matemático (Score >= 7.0),
  consumiendo ~120 tokens para validar restricciones de minutos y pulir la nota táctica.
- Si no hay GEMINI_API_KEY configurada, opera al 100% con reglas deterministas (0 tokens).
"""
import json
import urllib.request
import config


def verificar_nota_tactica_min_tokens(
    player_name: str,
    team_abbr: str,
    side: str,
    market_type: str,
    line: float,
    catalyst_reason: str,
) -> str:
    """
    Si GEMINI_API_KEY está activa, usa ~120 tokens de Gemini 2.5 Flash (gratis) para generar
    una síntesis táctica de 1 línea para el canal 'Muñetips'. Si no, devuelve la razón determinista.
    """
    if not config.USE_LLM_NEWS_CHECK or not config.GEMINI_API_KEY:
        return catalyst_reason

    if market_type == "player_points":
        mercado_es = "Puntos"
    elif market_type == "player_rebounds":
        mercado_es = "Rebotes"
    else:
        mercado_es = "Asistencias"

    prompt = (
        f"Eres analista cuantitativo NBA. Resume en 1 sola frase directa (máx 20 palabras, español) "
        f"por qué {side} {line} {mercado_es} de {player_name} ({team_abbr}) tiene ventaja hoy: "
        f"{catalyst_reason}"
    )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={config.GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 45,
            "temperature": 0.1,
        },
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            candidates = result.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts and "text" in parts[0]:
                    return parts[0]["text"].strip().replace("\n", " ")
    except Exception:
        pass

    return catalyst_reason
