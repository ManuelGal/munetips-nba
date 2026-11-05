"""
Extractor de datos NBA y Monitor de Bajas / Lesiones 100% compatible con ejecución en la nube (GitHub Actions).
Usa endpoints públicos CDN (ESPN Site API + NBA Live CDN) que NO bloquean IPs de la nube y consumen 0 tokens.
"""
import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import config


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


@dataclass
class PlayerStatsProfile:
    player_id: str
    player_name: str
    team_abbr: str
    is_starter: bool
    avg_minutes: float
    reb_per_min: float
    ast_per_min: float
    injury_status: str          # "ACTIVE" | "OUT" | "DOUBTFUL" | "QUESTIONABLE"
    injury_detail: str = ""
    is_two_way: bool = False


@dataclass
class NBAGameContext:
    game_id: str
    home_team: str
    away_team: str
    start_time_utc: str
    home_spread: float
    estimated_pace: float
    newly_out_starters: list[PlayerStatsProfile]
    active_players: dict[str, PlayerStatsProfile]  # Clave: nombre normalizado


def _http_get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalizar_nombre(nombre: str) -> str:
    """Normaliza nombres de jugadores para cruzar entre NBA/ESPN y las casas de apuestas."""
    reemplazos = {
        ".": "",
        "'": "",
        "-": " ",
        " jr": "",
        " sr": "",
        " iii": "",
        " ii": "",
        " iv": "",
    }
    limpio = nombre.lower().strip()
    for k, v in reemplazos.items():
        limpio = limpio.replace(k, v)
    return " ".join(limpio.split())


def cargar_cache_lesiones() -> dict[str, str]:
    if not os.path.exists(config.STATE_CACHE_FILE):
        return {}
    try:
        with open(config.STATE_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def guardar_cache_lesiones(estado: dict[str, str]) -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.STATE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2, ensure_ascii=False)


def obtener_roster_y_bajas_equipo(team_id: str, team_abbr: str) -> list[PlayerStatsProfile]:
    """
    Descarga el roster activo, estatus de lesión en tiempo real y estadísticas base del equipo.
    Costo: 0 tokens, sin bloqueo de IP en GitHub Actions.
    """
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/roster"
    try:
        data = _http_get_json(url)
    except Exception:
        return []

    perfiles: list[PlayerStatsProfile] = []
    athletes = data.get("athletes", [])

    for idx, ath in enumerate(athletes):
        nombre = ath.get("fullName") or ath.get("displayName", "")
        if not nombre:
            continue

        # Estatus de lesión actual
        injuries = ath.get("injuries", [])
        status = "ACTIVE"
        detail = ""
        if injuries:
            raw_status = (injuries[0].get("status") or "").upper()
            detail = injuries[0].get("shortComment") or injuries[0].get("longComment") or ""
            if "OUT" in raw_status or "IR" in raw_status:
                status = "OUT"
            elif "DOUBT" in raw_status:
                status = "DOUBTFUL"
            elif "QUEST" in raw_status or "DAY" in raw_status or "GTD" in raw_status:
                status = "QUESTIONABLE"

        # Detectar contrato Two-Way (Regla Jontay Porter)
        contract_info = str(ath.get("contract", "")).lower()
        experience = ath.get("experience", {}).get("years", 1)
        salary = ath.get("salary", 2000000)
        is_two_way = ("two-way" in contract_info) or (experience == 0 and salary < 600000)

        # Valores base de rotación (se enriquecen con el endpoint de estadísticas del equipo)
        es_titular = idx < 5
        avg_min = 31.5 if es_titular else (21.0 if idx < 9 else 12.0)
        pos_abbr = (ath.get("position", {}).get("abbreviation") or "G").upper()

        # Tasas base por posición si aún no hay muestra suficiente en la temporada
        if pos_abbr in ("C", "PF", "F-C"):
            reb_rate = 0.27
            ast_rate = 0.08
        elif pos_abbr in ("PG", "G"):
            reb_rate = 0.12
            ast_rate = 0.21
        else:
            reb_rate = 0.17
            ast_rate = 0.12

        perfiles.append(
            PlayerStatsProfile(
                player_id=str(ath.get("id", "")),
                player_name=nombre,
                team_abbr=team_abbr,
                is_starter=es_titular,
                avg_minutes=avg_min,
                reb_per_min=reb_rate,
                ast_per_min=ast_rate,
                injury_status=status,
                injury_detail=detail,
                is_two_way=is_two_way,
            )
        )

    return perfiles


def obtener_contexto_partidos_hoy() -> list[NBAGameContext]:
    """
    Obtiene todos los partidos de NBA del día, sus spreads, bajas confirmadas (OUT)
    y detecta qué titulares acaban de cambiar a OUT desde la última revisión.
    """
    url_scoreboard = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    try:
        sb = _http_get_json(url_scoreboard)
    except Exception as exc:
        print(f"[WARN] No se pudo consultar el scoreboard de NBA: {exc}")
        return []

    cache_previa = cargar_cache_lesiones()
    nuevo_estado_lesiones = dict(cache_previa)
    partidos_contexto: list[NBAGameContext] = []

    for ev in sb.get("events", []):
        game_id = str(ev.get("id", ""))
        start_time = ev.get("date", datetime.utcnow().isoformat())
        comps = ev.get("competitions", [])
        if not comps:
            continue
        comp = comps[0]

        # Extraer Spread del partido para el ajuste de Blowout
        home_spread = 0.0
        odds_list = comp.get("odds", [])
        if odds_list:
            details = odds_list[0].get("details", "")  # Ej: "BOS -11.5"
            try:
                partes = details.split()
                if len(partes) == 2:
                    home_spread = float(partes[1])
            except Exception:
                home_spread = 0.0

        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        home_team_abbr = ""
        away_team_abbr = ""
        newly_out_starters: list[PlayerStatsProfile] = []
        active_players: dict[str, PlayerStatsProfile] = {}

        for team_entry in competitors:
            team_obj = team_entry.get("team", {})
            t_id = str(team_obj.get("id", ""))
            t_abbr = team_obj.get("abbreviation", "NBA")
            if team_entry.get("homeAway") == "home":
                home_team_abbr = t_abbr
            else:
                away_team_abbr = t_abbr

            perfiles = obtener_roster_y_bajas_equipo(t_id, t_abbr)
            for p in perfiles:
                clave_norm = normalizar_nombre(p.player_name)
                prev_status = cache_previa.get(clave_norm, "ACTIVE")
                nuevo_estado_lesiones[clave_norm] = p.injury_status

                if p.injury_status in ("OUT", "DOUBTFUL") and p.avg_minutes >= 22.0:
                    # Titular o jugador de rotación importante descartado
                    newly_out_starters.append(p)
                    if prev_status not in ("OUT", "DOUBTFUL"):
                        p.injury_detail = f"RECIÉN DESCARTADO ({p.injury_status}) — " + p.injury_detail
                elif p.injury_status == "ACTIVE":
                    active_players[clave_norm] = p

        partidos_contexto.append(
            NBAGameContext(
                game_id=game_id,
                home_team=home_team_abbr,
                away_team=away_team_abbr,
                start_time_utc=start_time,
                home_spread=home_spread,
                estimated_pace=config.LEAGUE_AVG_PACE,
                newly_out_starters=newly_out_starters,
                active_players=active_players,
            )
        )

    guardar_cache_lesiones(nuevo_estado_lesiones)
    return partidos_contexto
