"""
Módulo de Notificaciones Push a Telegram para el canal 'Muñetips'.
Formato minimalista y directo de 7 líneas para lectura rápida en el celular.
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
    Formato limpio y directo de 7 renglones para el canal 'Muñetips'.
    Traduce OVER/UNDER a 'MÁS DE' / 'MENOS DE' igual que en los botones de Playdoit.
    """
    if op.market_type == "player_points":
        mercado_es = "Puntos"
        pestana_playdoit = "Puntos de Jugador Más de/Menos de"
    elif op.market_type == "player_rebounds":
        mercado_es = "Rebotes"
        pestana_playdoit = "Rebotes de Jugador Más de/Menos de"
    else:
        mercado_es = "Asistencias"
        pestana_playdoit = "Asistencias de Jugador Más de/Menos de"

    lado_es = "MENOS DE" if op.side == "UNDER" else "MÁS DE"
    momio_am = decimal_a_americano(op.playdoit_odds)
    min_am = decimal_a_americano(op.min_acceptable_odds)

    nota_tactica = verificar_nota_tactica_min_tokens(
        player_name=op.player_name,
        team_abbr=op.team_abbr,
        side=op.side,
        market_type=op.market_type,
        line=op.playdoit_line,
        catalyst_reason=op.catalyst_reason,
    )

    mensaje = (
        f"🎯 *{config.CHANNEL_NAME.upper()}*  |  ⭐ *{op.score_1_to_10} / 10* _(Prob: {op.blended_prob * 100:.0f}%)_\n\n"
        f"🏀 *{op.team_abbr} vs {op.opponent_abbr}*\n"
        f"👤 *{op.player_name}* — *{lado_es} {op.playdoit_line} {mercado_es}*\n"
        f"📂 _{pestana_playdoit}_\n\n"
        f"💰 *Momio:* `{momio_am}` (`{op.playdoit_odds:.2f}`)  |  *Mín:* `{min_am}` (`{op.min_acceptable_odds:.2f}`)\n"
        f"💵 *Apostar:* `${op.suggested_stake_mxn:.0f} MXN`\n"
        f"⚡ _{nota_tactica}_"
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
                print(f"[OK] Alerta enviada a '{config.CHANNEL_NAME}' ({destino}).")
                return True
            return False
    except Exception as exc:
        print(f"[ERROR TELEGRAM] {exc}")
        return False


def descubrir_id_canal_munetips(bot_token: Optional[str] = None) -> Optional[str]:
    token = (bot_token or config.TELEGRAM_BOT_TOKEN).strip()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    for item in reversed(data.get("result", [])):
        for key in ("channel_post", "message", "my_chat_member"):
            chat = item.get(key, {}).get("chat", {})
            if chat.get("id"):
                return str(chat["id"])
    return None
