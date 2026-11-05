"""
Módulo de Notificaciones Push a Telegram para el canal 'Muñetips'.
Costo: $0.00 USD (100% gratuito e ilimitado vía Telegram Bot API).
"""
import json
import urllib.request
from typing import Optional
import config
from src.model_poisson import PropEvaluationResult
from src.odds_scanner import decimal_a_americano
from src.llm_news_filter import verificar_nota_tactica_min_tokens


def formatear_mensaje_munetips(op: PropEvaluationResult) -> str:
    """
    Construye el mensaje exacto que llega al canal 'Muñetips' en el celular,
    destacando la escala del 1 al 10 y la probabilidad real del modelo.
    """
    mercado_es = "Rebotes" if op.market_type == "player_rebounds" else "Asistencias"
    momio_am = decimal_a_americano(op.playdoit_odds)
    min_am = decimal_a_americano(op.min_acceptable_odds)

    # Semáforo visual según la calificación del 1 al 10
    if op.score_1_to_10 >= 8.5:
        etiqueta_score = "🔥 MÁXIMA CONFIANZA"
    elif op.score_1_to_10 >= 7.5:
        etiqueta_score = "✅ ALTA CONFIANZA"
    else:
        etiqueta_score = "⚡ VALOR DETECTADO"

    nota_tactica = verificar_nota_tactica_min_tokens(
        player_name=op.player_name,
        team_abbr=op.team_abbr,
        side=op.side,
        market_type=op.market_type,
        line=op.playdoit_line,
        catalyst_reason=op.catalyst_reason,
    )

    aviso_regla_playdoit = (
        "🛡️ *Ventaja extra Playdoit:* En UNDER, cualquier lesión temprana o problema de faltas nos hace ganar."
        if op.side == "UNDER"
        else "⚠️ *Regla Playdoit:* Verificar ícono `(i)` (Incluye Tiempo Extra) y NO entrar si la línea sube medio punto."
    )

    mensaje = (
        f"🚨 *ALERTA {config.CHANNEL_NAME.upper()} — NBA PROPS*\n"
        f"⭐ *CALIFICACIÓN (1 al 10):* `{op.score_1_to_10} / 10` ({etiqueta_score})\n"
        f"📈 *Probabilidad Real de Ganar:* `{op.blended_prob * 100:.1f}%` _(Ventaja EV: +{op.ev_pct * 100:.1f}%)_\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏀 *Partido:* {op.team_abbr} vs {op.opponent_abbr}\n"
        f"👤 *Jugador:* *{op.player_name}* ({op.team_abbr})\n"
        f"🎯 *Apuesta en Playdoit:* *{op.side} {op.playdoit_line} {mercado_es}*\n"
        f"💰 *Cuota Playdoit:* `{op.playdoit_odds:.2f}` (`{momio_am}`)\n"
        f"🛑 *Cuota mínima aceptable:* `{op.min_acceptable_odds:.2f}` (`{min_am}`) _(Cumple regla >= -130)_\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *Proyección Poisson:* `{op.projected_mean_lambda:.2f}` {mercado_es.lower()} en `{op.projected_minutes:.1f}` min\n"
        f"⚖️ *Ancla FanDuel/Pinnacle:* Línea en `{op.sharp_line}` (Prob. justa: `{op.sharp_fair_prob * 100:.1f}%`)\n"
        f"💵 *Monto sugerido (Banca ${config.BANKROLL_MXN:.0f}):* `${op.suggested_stake_mxn:.0f} MXN`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 *Por qué ahora:* {nota_tactica}\n"
        f"{aviso_regla_playdoit}"
    )
    return mensaje


def enviar_alerta_telegram(
    texto: str,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> bool:
    """Envía el mensaje en formato Markdown al canal 'Muñetips'."""
    token = (bot_token or config.TELEGRAM_BOT_TOKEN).strip()
    destino = (chat_id or config.TELEGRAM_CHAT_ID or "@munetipsfc").strip()
    if destino.startswith("https://t.me/"):
        destino = "@" + destino.replace("https://t.me/", "").strip("/")
    elif destino.startswith("t.me/"):
        destino = "@" + destino.replace("t.me/", "").strip("/")

    if not token or not destino:
        print(
            "[AVISO TELEGRAM] Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID. "
            "Imprimiendo alerta en consola:\n"
        )
        print(texto)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": destino,
        "text": texto,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("ok"):
                print(f"[OK] Alerta enviada exitosamente al canal '{config.CHANNEL_NAME}' ({destino}).")
                return True
            print(f"[ERROR TELEGRAM] Respuesta no OK: {res}")
            return False
    except Exception as exc:
        print(f"[ERROR TELEGRAM] Falló el envío al canal '{config.CHANNEL_NAME}': {exc}")
        return False


def descubrir_id_canal_munetips(bot_token: Optional[str] = None) -> Optional[str]:
    """
    Busca automáticamente el chat_id numérico del canal 'Muñetips' leyendo los últimos
    updates recibidos por el Bot de Telegram.
    """
    token = (bot_token or config.TELEGRAM_BOT_TOKEN).strip()
    if not token:
        print("[ERROR] Configura primero tu TELEGRAM_BOT_TOKEN para buscar el canal.")
        return None

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[ERROR] No se pudo consultar getUpdates de Telegram: {exc}")
        return None

    resultados = data.get("result", [])
    if not resultados:
        print(
            f"[INFO] Aún no hay mensajes visibles para el bot.\n"
            f"1. Abre tu canal '{config.CHANNEL_NAME}' en Telegram (teléfono {config.USER_PHONE_REFERENCE}).\n"
            f"2. Agrega tu Bot como Administrador del canal '{config.CHANNEL_NAME}'.\n"
            f"3. Escribe cualquier palabra (ej. 'Hola') dentro del canal '{config.CHANNEL_NAME}' y vuelve a correr este comando."
        )
        return None

    for item in reversed(resultados):
        for key in ("channel_post", "message", "my_chat_member"):
            post = item.get(key, {})
            chat = post.get("chat", {})
            title = (chat.get("title") or "").lower()
            chat_id = chat.get("id")
            if chat_id and ("muñetips" in title or "munetips" in title or chat.get("type") == "channel"):
                print(f"[ENCONTRADO] Canal: '{chat.get('title')}' -> TELEGRAM_CHAT_ID = {chat_id}")
                return str(chat_id)

    # Si solo hay un chat privado con el usuario, devolver ese
    ultimo_chat = resultados[-1].get("message", {}).get("chat", {})
    if ultimo_chat.get("id"):
        cid = str(ultimo_chat["id"])
        print(f"[ENCONTRADO] Chat directo detectado -> TELEGRAM_CHAT_ID = {cid}")
        return cid

    return None
