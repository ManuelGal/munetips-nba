import argparse
import csv
import os
from datetime import datetime, timezone
import config


def cargar_env_local() -> None:
    ruta_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(ruta_env):
        return
    with open(ruta_env, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v and not os.getenv(k):
                os.environ[k] = v

    config.TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", config.TELEGRAM_BOT_TOKEN).strip()
    config.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", config.TELEGRAM_CHAT_ID).strip()
    config.ODDS_API_KEY = os.getenv("ODDS_API_KEY", config.ODDS_API_KEY).strip()
    config.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", config.GEMINI_API_KEY).strip()
    config.USE_LLM_NEWS_CHECK = bool(config.GEMINI_API_KEY)


from src.model_poisson import (
    PlayerProjectionInput,
    PropEvaluationResult,
    evaluar_oportunidad_prop,
)
from src.nba_data import obtener_contexto_partidos_hoy
from src.odds_scanner import escanear_mercados_reales
from src.telegram_notifier import (
    descubrir_id_canal_munetips,
    enviar_alerta_telegram,
    formatear_mensaje_munetips,
)


def registrar_pick_csv(op: PropEvaluationResult) -> bool:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    fecha_hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    clave_unica = f"{fecha_hoy}|{op.player_name}|{op.market_type}|{op.side}|{op.playdoit_line}"

    ya_existe = False
    if os.path.exists(config.PICKS_LOG_CSV):
        with open(config.PICKS_LOG_CSV, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("id_unico") == clave_unica:
                    ya_existe = True
                    break

    if ya_existe:
        return False

    escribir_cabecera = not os.path.exists(config.PICKS_LOG_CSV)
    with open(config.PICKS_LOG_CSV, "a", encoding="utf-8", newline="") as f:
        campos = [
            "id_unico",
            "fecha_utc",
            "jugador",
            "equipo",
            "rival",
            "mercado",
            "lado",
            "linea_playdoit",
            "cuota_playdoit",
            "linea_sharp_fanduel",
            "prob_justa_sharp",
            "lambda_poisson",
            "prob_modelo_final",
            "ev_pct",
            "score_1_a_10",
            "stake_sugerido_mxn",
            "motivo",
        ]
        writer = csv.DictWriter(f, fieldnames=campos)
        if escribir_cabecera:
            writer.writeheader()
        writer.writerow(
            {
                "id_unico": clave_unica,
                "fecha_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "jugador": op.player_name,
                "equipo": op.team_abbr,
                "rival": op.opponent_abbr,
                "mercado": op.market_type,
                "lado": op.side,
                "linea_playdoit": op.playdoit_line,
                "cuota_playdoit": op.playdoit_odds,
                "linea_sharp_fanduel": op.sharp_line,
                "prob_justa_sharp": op.sharp_fair_prob,
                "lambda_poisson": op.projected_mean_lambda,
                "prob_modelo_final": op.blended_prob,
                "ev_pct": op.ev_pct,
                "score_1_a_10": op.score_1_to_10,
                "stake_sugerido_mxn": op.suggested_stake_mxn,
                "motivo": op.catalyst_reason,
            }
        )
    return True


def ejecutar_simulacion_demo() -> None:
    inp_ejemplo = PlayerProjectionInput(
        player_name="Tre Mann",
        team_abbr="CHA",
        opponent_abbr="IND",
        market_type="player_assists",
        is_starter=False,
        base_minutes=23.5,
        rate_per_minute=0.185,
        team_pace=101.8,
        opp_pace=103.2,
        game_spread=-4.5,
        teammate_out_name="LaMelo Ball",
        teammate_out_minutes=33.5,
        usage_boost_factor=1.15,
    )

    op = evaluar_oportunidad_prop(
        inp=inp_ejemplo,
        playdoit_line=4.5,
        playdoit_over_odds=1.87,
        playdoit_under_odds=1.83,
        sharp_line=5.5,
        sharp_over_odds=1.88,
        sharp_under_odds=1.92,
    )

    if op:
        msg = formatear_mensaje_munetips(op)
        enviar_alerta_telegram(msg)


def ejecutar_escaneo_vivo() -> None:
    ahora_str = datetime.now().strftime("%H:%M UTC")
    print(f"[{ahora_str}] Iniciando escaneo NBA -> Canal '{config.CHANNEL_NAME}'...")
    partidos = obtener_contexto_partidos_hoy()
    print(f"[NBA DATA] Partidos encontrados hoy: {len(partidos)}")

    bajas_lista = []
    for p in partidos:
        for b in p.newly_out_starters:
            bajas_lista.append(f"{b.player_name} ({b.team_abbr})")

    oportunidades = escanear_mercados_reales(partidos)
    print(f"[MODELO POISSON] Oportunidades que superan Score >= {config.MIN_SCORE_FOR_ALERT}/10: {len(oportunidades)}")

    enviadas = 0
    for op in oportunidades[:4]:
        if registrar_pick_csv(op):
            mensaje = formatear_mensaje_munetips(op)
            if enviar_alerta_telegram(mensaje):
                enviadas += 1

    if enviadas == 0:
        resumen_partidos = (
            ", ".join(f"{p.away_team}@{p.home_team}" for p in partidos[:8])
            if partidos
            else "Sin partidos programados hoy"
        )
        resumen_bajas = (
            ", ".join(bajas_lista[:8])
            if bajas_lista
            else "Ningún titular confirmado OUT todavía"
        )
        msg_estado = (
            f"📡 *REPORTE DE VIGILANCIA — {config.CHANNEL_NAME.upper()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏀 *Partidos NBA hoy ({len(partidos)}):* {resumen_partidos}\n"
            f"🚑 *Bajas clave detectadas ({len(bajas_lista)}):* {resumen_bajas}\n"
            f"🎯 *Apuestas con Score >= {config.MIN_SCORE_FOR_ALERT}/10:* `0`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ _Ninguna línea de Playdoit tiene todavía ventaja matemática suficiente. "
            f"Cuidando banca (${config.BANKROLL_MXN:.0f} MXN) hasta el próximo corte de lesiones._"
        )
        enviar_alerta_telegram(msg_estado)


def main() -> None:
    cargar_env_local()
    parser = argparse.ArgumentParser(description="Agente Cuantitativo NBA -> Canal Muñetips (Playdoit)")
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--find-channel", action="store_true")
    args = parser.parse_args()

    if args.find_channel:
        descubrir_id_canal_munetips()
    elif args.demo:
        ejecutar_simulacion_demo()
    else:
        ejecutar_escaneo_vivo()


if __name__ == "__main__":
    main()
