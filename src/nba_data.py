"""
Extractor de datos NBA, Estadísticas Reales de Temporada (ESPN Overview API),
Depth Charts reales y Monitor de Bajas / Lesiones 100% compatible con la nube.
- Consulta `/depthcharts`, `/roster` y `/athletes/{id}/overview` de ESPN API para obtener
  minutos promedio reales (`MIN`) y tasas empíricas por minuto (`PTS/min`, `REB/min`, `AST/min`)
  sin bloqueos de IP y con consumo de 0 tokens LLM.
"""
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
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
    position: str               # "PG" | "SG" | "SF" | "PF" | "C" | "G" | "F"
    is_starter: bool
    avg_minutes: float
    pts_per_min: float
    reb_per_min: float
    ast_per_min: float
    injury_status: str          # "ACTIVE" | "OUT" | "DOUBTFUL" | "QUESTIONABLE"
    injury_detail: str = ""
    is_newly_out: bool = False  # True si acaba de pasar a OUT/DOUBTFUL en esta ventana
    minutes_cap_news: Optional[float] = None
    has_real_stats: bool = False
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


def _http_get_json(url: str, timeout: int = 12) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        limpio = str(val).replace(",", "").strip()
        if not limpio or limpio == "--":
            return default
        return float(limpio)
    except Exception:
        return default


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


def _cargar_cache_stats() -> dict[str, dict]:
    if not os.path.exists(config.PLAYER_STATS_CACHE_FILE):
        return {}
    try:
        with open(config.PLAYER_STATS_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Sanitizar minutos_cap heredados de la regex antigua para evitar falsos topes
            for v in data.values():
                if isinstance(v, dict):
                    v["minutes_cap"] = None
            return data
    except Exception:
        return {}


def _guardar_cache_stats(cache: dict[str, dict]) -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.PLAYER_STATS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def _extraer_tope_minutos_de_texto(texto: str) -> Optional[float]:
    if not texto:
        return None
    t_low = texto.lower()
    if not any(k in t_low for k in ("restriction", "limit", "cap", "restricted")):
        return None
    # Solo buscar frases explícitas de restricción médica, e.g.:
    # "on a 24-minute restriction", "minute limit of 20", "capped at 25 minutes"
    # NUNCA coincidir con oraciones que solo relatan minutos de partidos anteriores ("played 26 minutes")
    patrones = [
        r"(?:restriction|limit|capped|restricted)\s+(?:of|to|around|at)?\s*(\d{2})\s*(?:-\s*(\d{2}))?\s*min",
        r"(\d{2})\s*(?:-\s*(\d{2}))?\s*minute\s*(?:restriction|limit|cap)",
    ]
    for pat in patrones:
        m = re.search(pat, t_low)
        if m:
            val = _safe_float(m.group(2) or m.group(1), 0.0)
            if 14.0 <= val <= 32.0:
                return val
    return None


def obtener_estadisticas_reales_jugador(
    player_id: str,
    pos_abbr: str,
    es_titular: bool,
    es_segunda_unidad: bool,
    cache_stats: dict[str, dict],
) -> tuple[float, float, float, float, Optional[float], bool]:
    """
    Consulta el endpoint público `athletes/{id}/overview` de ESPN para obtener los promedios
    reales de la temporada actual (MIN, PTS, REB, AST) y calcular tasas empíricas por minuto.
    Usa caché en disco por día para evitar llamadas redundantes.
    """
    hoy_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cached = cache_stats.get(player_id)
    if cached and cached.get("date") == hoy_str and cached.get("has_real_stats"):
        return (
            _safe_float(cached.get("avg_minutes"), 28.0),
            _safe_float(cached.get("pts_per_min"), 0.45),
            _safe_float(cached.get("reb_per_min"), 0.18),
            _safe_float(cached.get("ast_per_min"), 0.12),
            cached.get("minutes_cap"),
            True,
        )

    # Valores de respaldo por posición solo si el jugador es novato sin partidos o falla la red
    fallback_min = 31.5 if es_titular else (22.0 if es_segunda_unidad else 14.0)
    if pos_abbr in ("C", "PF", "F-C"):
        fb_pts, fb_reb, fb_ast = 0.46, 0.27, 0.08
    elif pos_abbr in ("PG", "G"):
        fb_pts, fb_reb, fb_ast = 0.48, 0.12, 0.21
    else:
        fb_pts, fb_reb, fb_ast = 0.45, 0.17, 0.11

    if not player_id:
        return fallback_min, fb_pts, fb_reb, fb_ast, None, False

    url = f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{player_id}/overview"
    try:
        data = _http_get_json(url, timeout=8)
        stats_block = data.get("statistics", {})
        names = stats_block.get("names", [])
        splits = stats_block.get("splits", [])

        # Soportar estructura anidada `categories` si ESPN la devuelve así para ciertos perfiles
        if (not names or not splits) and isinstance(stats_block.get("categories"), list):
            cats = stats_block["categories"]
            if cats:
                names = cats[0].get("names", names)
                splits = cats[0].get("splits", splits)

        elegida = None
        for sp in splits:
            if "regular" in (sp.get("displayName") or "").lower():
                elegida = sp.get("stats", [])
                break
        if not elegida and splits:
            elegida = splits[0].get("stats", [])

        min_cap = None
        rotowire = data.get("rotowire", {})
        if isinstance(rotowire, dict):
            story = (rotowire.get("story") or "") + " " + (rotowire.get("headline") or "")
            min_cap = _extraer_tope_minutos_de_texto(story)

        if names and elegida and len(names) == len(elegida):
            mapa = {str(k).upper(): v for k, v in zip(names, elegida)}
            labels = stats_block.get("labels", [])
            if labels and len(labels) == len(elegida):
                for lk, lv in zip(labels, elegida):
                    mapa[str(lk).upper()] = lv

            gp = _safe_float(mapa.get("GP") or mapa.get("GAMESPLAYED"), 0.0)
            avg_min = _safe_float(mapa.get("MIN") or mapa.get("AVGMINUTES"), 0.0)
            avg_pts = _safe_float(mapa.get("PTS") or mapa.get("AVGPOINTS"), 0.0)
            avg_reb = _safe_float(mapa.get("REB") or mapa.get("AVGREBOUNDS"), 0.0)
            avg_ast = _safe_float(mapa.get("AST") or mapa.get("AVGASSISTS"), 0.0)

            if gp >= 1 and avg_min >= 8.0:
                pts_pm = round(avg_pts / avg_min, 4)
                reb_pm = round(avg_reb / avg_min, 4)
                ast_pm = round(avg_ast / avg_min, 4)
                cache_stats[player_id] = {
                    "date": hoy_str,
                    "avg_minutes": round(avg_min, 2),
                    "pts_per_min": pts_pm,
                    "reb_per_min": reb_pm,
                    "ast_per_min": ast_pm,
                    "minutes_cap": min_cap,
                    "has_real_stats": True,
                }
                return round(avg_min, 2), pts_pm, reb_pm, ast_pm, min_cap, True
    except Exception:
        pass

    return fallback_min, fb_pts, fb_reb, fb_ast, None, False


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


def obtener_roster_y_bajas_equipo(
    team_id: str,
    team_abbr: str,
    cache_stats: dict[str, dict],
) -> list[PlayerStatsProfile]:
    """
    Descarga el roster activo, cruza con el Depth Chart real y con las estadísticas reales de temporada
    de ESPN (`athletes/{id}/overview`) para los jugadores de rotación.
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
        pid = str(ath.get("id", ""))

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
        experience = _safe_float(ath.get("experience", {}).get("years", 1), 1.0)
        salary = _safe_float(ath.get("salary", 2000000), 2000000.0)
        is_two_way = ("two-way" in contract_info) or (experience == 0 and salary < 600000)

        es_titular = norm in starters_norm
        es_segunda_unidad = norm in second_unit_norm
        pos_abbr = (ath.get("position", {}).get("abbreviation") or "G").upper()

        # Consultar estadísticas reales de temporada para todos los jugadores que no sean two-way
        if not is_two_way:
            avg_min, pts_rate, reb_rate, ast_rate, min_cap, has_real = obtener_estadisticas_reales_jugador(
                player_id=pid,
                pos_abbr=pos_abbr,
                es_titular=es_titular,
                es_segunda_unidad=es_segunda_unidad,
                cache_stats=cache_stats,
            )
        else:
            avg_min = 12.0
            pts_rate, reb_rate, ast_rate = 0.40, 0.15, 0.10
            min_cap = None
            has_real = False

        if min_cap is None and detail:
            min_cap = _extraer_tope_minutos_de_texto(detail)

        perfiles.append(
            PlayerStatsProfile(
                player_id=pid,
                player_name=nombre,
                team_abbr=team_abbr,
                position=pos_abbr,
                is_starter=es_titular or (avg_min >= 28.0),
                avg_minutes=avg_min,
                pts_per_min=pts_rate,
                reb_per_min=reb_rate,
                ast_per_min=ast_rate,
                injury_status=status,
                injury_detail=detail,
                is_newly_out=False,
                minutes_cap_news=min_cap,
                has_real_stats=has_real,
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
    cache_stats = _cargar_cache_stats()
    primer_escaneo = len(cache_previa) == 0
    nuevo_estado_lesiones = dict(cache_previa)
    partidos_contexto: list[NBAGameContext] = []

    total_activos_reales = 0
    total_activos_fallback = 0

    for ev in sb.get("events", []):
        game_id = str(ev.get("id", ""))
        start_time = ev.get("date", datetime.now(timezone.utc).isoformat())
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
                    home_spread = _safe_float(partes[1], 0.0)
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

            perfiles = obtener_roster_y_bajas_equipo(t_id, t_abbr, cache_stats)
            for p in perfiles:
                clave_norm = normalizar_nombre(p.player_name)
                prev_status = cache_previa.get(clave_norm)
                nuevo_estado_lesiones[clave_norm] = p.injury_status

                if p.injury_status in ("OUT", "DOUBTFUL") and p.avg_minutes >= 20.0:
                    # Solo es "RECIÉN DESCARTADO" si estaba en caché previa Y su estado previo era ACTIVE o QUESTIONABLE
                    if prev_status and prev_status in ("ACTIVE", "QUESTIONABLE"):
                        p.is_newly_out = True
                        p.injury_detail = f"RECIÉN DESCARTADO ({p.injury_status}) — " + p.injury_detail
                    newly_out_starters.append(p)
                elif p.injury_status in ("ACTIVE", "QUESTIONABLE"):
                    active_players[clave_norm] = p
                    if p.has_real_stats:
                        total_activos_reales += 1
                    else:
                        total_activos_fallback += 1

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

    print(
        f"[NBA DATA] Estadísticas reales ESPN cargadas para jugadores activos: "
        f"{total_activos_reales} reales / {total_activos_fallback} fallback"
    )
    guardar_cache_lesiones(nuevo_estado_lesiones)
    _guardar_cache_stats(cache_stats)
    return partidos_contexto
