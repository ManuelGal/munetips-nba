"""
Extractor de datos NBA, Depth Charts reales y Monitor de Bajas / Lesiones 100% compatible con la nube.
Usa `/depthcharts` y `/roster` de ESPN Site API para identificar titulares reales (no por orden alfabético)
y detectar bajas confirmadas (OUT / DOUBTFUL) con consumo de 0 tokens.
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
    home_team_full: str
    away_team_full: str
    start_time_utc: str
    home_spread: float
    estimated_pace: float
    newly_out_starters: list[PlayerStatsProfile]
    active_players: dict[str, PlayerStatsProfile]


def _http_get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalizar_nombre(nombre: str) -> str:
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


def _obtener_mapa_depth_chart(team_id: str) -> tuple[set[str], set[str]]:
    """
    Consulta el Depth Chart oficial del equipo para saber quiénes son los 5 titulares reales (rank 0)
    y quiénes son la segunda unidad directa (rank 1), evitando el orden alfabético del roster.
    """
    starters_norm: set[str] = set()
    second_unit_norm: set[str] = set()
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/depthcharts"
    try:
        data = _http_get_json(url)
        charts = data.get("depthchart", [])
        if charts:
            positions = charts[0].get("positions", {})
            for pos_obj in positions.values():
                aths = pos_obj.get("athletes", [])
                if len(aths) >= 1:
                    n0 = aths[0].get("displayName") or aths[0].get("fullName", "")
                    if n0:
                        starters_norm.add(normalizar_nombre(n0))
                if len(aths) >= 2:
                    n1 = aths[1].get("displayName") or aths[1].get("fullName", "")
                    if n1:
                        second_unit_norm.add(normalizar_nombre(n1))
    except Exception:
        pass
    return starters_norm, second_unit_norm


def obtener_roster_y_bajas_equipo(team_id: str, team_abbr: str) -> list[PlayerStatsProfile]:
    """
    Descarga el roster activo, cruza con el Depth Chart real y extrae el estatus de lesión.
    """
    starters_norm, second_unit_norm = _obtener_mapa_depth_chart(team_id)
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/roster"
    try:
        data = _http_get_json(url)
    except Exception:
        return []

    perfiles: list[PlayerStatsProfile] = []
    athletes = data.get("athletes", [])

    for ath in athletes:
        nombre = ath.get("fullName") or ath.get("displayName", "")
        if not nombre:
            continue
        norm = normalizar_nombre(nombre)

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

        contract_info = str(ath.get("contract", "")).lower()
        experience = ath.get("experience", {}).get("years", 1)
        salary = ath.get("salary", 2000000)
        is_two_way = ("two-way" in contract_info) or (experience == 0 and salary < 600000)

        es_titular = norm in starters_norm
        es_segunda_unidad = norm in second_unit_norm
        if es_titular:
            avg_min = 32.0
        elif es_segunda_unidad:
            avg_min = 22.0
        else:
            avg_min = 14.0

        pos_abbr = (ath.get("position", {}).get("abbreviation") or "G").upper()
        if pos_abbr in ("C", "PF", "F-C"):
            reb_rate, ast_rate = 0.27, 0.08
        elif pos_abbr in ("PG", "G"):
            reb_rate, ast_rate = 0.12, 0.21
        else:
            reb_rate, ast_rate = 0.17, 0.12

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
    url_scoreboard = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    try:
        sb = _http_get_json(url_scoreboard)
    except Exception as exc:
        print(f"[WARN] No se pudo consultar el scoreboard de NBA: {exc}")
        return []

    cache_previa = cargar_cache_lesiones()
    primer_escaneo = len(cache_previa) == 0
    nuevo_estado_lesiones = dict(cache_previa)
    partidos_contexto: list[NBAGameContext] = []

    for ev in sb.get("events", []):
        game_id = str(ev.get("id", ""))
        start_time = ev.get("date", datetime.utcnow().isoformat())
        comps = ev.get("competitions", [])
        if not comps:
            continue
        comp = comps[0]

        home_spread = 0.0
        odds_list = comp.get("odds", [])
        if odds_list:
            details = odds_list[0].get("details", "")
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
        home_team_full = ""
        away_team_full = ""
        newly_out_starters: list[PlayerStatsProfile] = []
        active_players: dict[str, PlayerStatsProfile] = {}

        for team_entry in competitors:
            team_obj = team_entry.get("team", {})
            t_id = str(team_obj.get("id", ""))
            t_abbr = team_obj.get("abbreviation", "NBA")
            t_full = team_obj.get("displayName", t_abbr)
            if team_entry.get("homeAway") == "home":
                home_team_abbr = t_abbr
                home_team_full = t_full
            else:
                away_team_abbr = t_abbr
                away_team_full = t_full

            perfiles = obtener_roster_y_bajas_equipo(t_id, t_abbr)
            for p in perfiles:
                clave_norm = normalizar_nombre(p.player_name)
                prev_status = cache_previa.get(clave_norm, p.injury_status if primer_escaneo else "ACTIVE")
                nuevo_estado_lesiones[clave_norm] = p.injury_status

                # Solo considerar bajas de titulares o rotación principal (>= 22 min reales en Depth Chart)
                if p.injury_status in ("OUT", "DOUBTFUL") and p.avg_minutes >= 22.0:
                    if not primer_escaneo and prev_status not in ("OUT", "DOUBTFUL"):
                        p.injury_detail = f"RECIÉN DESCARTADO ({p.injury_status}) — " + p.injury_detail
                    newly_out_starters.append(p)
                elif p.injury_status == "ACTIVE":
                    active_players[clave_norm] = p

        partidos_contexto.append(
            NBAGameContext(
                game_id=game_id,
                home_team=home_team_abbr,
                away_team=away_team_abbr,
                home_team_full=home_team_full,
                away_team_full=away_team_full,
                start_time_utc=start_time,
                home_spread=home_spread,
                estimated_pace=config.LEAGUE_AVG_PACE,
                newly_out_starters=newly_out_starters,
                active_players=active_players,
            )
        )

    guardar_cache_lesiones(nuevo_estado_lesiones)
    return partidos_contexto
