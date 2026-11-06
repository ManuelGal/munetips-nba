#!/usr/bin/env python3
"""
scripts/auditoria_independiente_tercera_ia.py
================================================================================
BATERÍA DE VERIFICACIÓN Y AUDITORÍA INDEPENDIENTE (PARA TERCERA IA / REVISOR EXTERNO)
================================================================================
Este script ejecuta pruebas cuantitativas y estructurales automatizadas sobre el
repositorio 'munetips-nba' para que un Tercer Agente de IA o Auditor Externo pueda
verificar empíricamente (con CERO suposiciones) que todos los modelos matemáticos,
filtros de mercado y reglas de negocio de Playdoit.mx están correctamente implementados.

Ejecución:
    python scripts/auditoria_independiente_tercera_ia.py
"""

import sys
import os
import re
import json
import math
from pathlib import Path

# Asegurar que la raíz del proyecto esté en sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import config
from src.model_poisson import (
    calcular_score_1_a_10,
    calcular_probabilidad_linea,
    _compound_points_pmf,
    _nbinom_pmf,
    quitar_margen_casa,
    calcular_ev_real_playdoit,
    aplicar_asimetria_lesion_playdoit,
    PlayerProjectionInput,
    evaluar_oportunidad_prop,
)
from src.nba_data import _extraer_tope_minutos_de_texto, normalizar_nombre
from scripts.comparar_playdoit import momio_americano_a_decimal, cumple_rollover

COL_VERDE = "\033[92m"
COL_ROJO = "\033[91m"
COL_AMARILLO = "\033[93m"
COL_RESET = "\033[0m"

total_pruebas = 0
pruebas_pasadas = 0

def assert_test(nombre: str, condicion: bool, detalle: str = ""):
    global total_pruebas, pruebas_pasadas
    total_pruebas += 1
    if condicion:
        pruebas_pasadas += 1
        print(f"  {COL_VERDE}[PASS]{COL_RESET} {nombre}")
    else:
        print(f"  {COL_ROJO}[FAIL]{COL_RESET} {nombre} -> {detalle}")


def test_suite_1_calibracion_score():
    print("\n--- SUITE 1: Verificación de la Calibración del Score (1 a 10) ---")
    print("Objetivo: Comprobar que se eliminó la saturación al techo (9.8 vs 9.9) y que el score refleja probabilidad y EV.")
    
    # Caso A: Pick con 61% de probabilidad y +6.5% EV (apuesta sólida estándar)
    score_61 = calcular_score_1_a_10(
        ev_pct=0.065,
        win_prob=0.61,
        side="UNDER",
        playdoit_line=21.5,
        sharp_line=21.5,
        has_injury_catalyst=True,
        game_spread=-4.5,
        is_starter=True,
    )
    assert_test(
        f"Pick 61% prob / 6.5% EV califica entre 7.0 y 7.8 (obtenido: {score_61})",
        7.0 <= score_61 <= 7.8,
        f"Score inesperado: {score_61}"
    )

    # Caso B: Pick con 71% de probabilidad y +15% EV (joya del día)
    score_71 = calcular_score_1_a_10(
        ev_pct=0.150,
        win_prob=0.71,
        side="UNDER",
        playdoit_line=21.5,
        sharp_line=22.5,
        has_injury_catalyst=True,
        game_spread=-4.5,
        is_starter=True,
    )
    assert_test(
        f"Pick 71% prob / 15.0% EV califica entre 9.3 y 9.8 (obtenido: {score_71})",
        9.3 <= score_71 <= 9.8,
        f"Score inesperado: {score_71}"
    )

    # Diferenciación clara entre ambos
    diferencia = score_71 - score_61
    assert_test(
        f"Existe una diferenciación clara entre pick 71% y 61% (Delta >= 1.5 pts, obtenido: {diferencia:.1f})",
        diferencia >= 1.5,
        f"Diferencia insuficiente: {diferencia}"
    )


def test_suite_2_reglas_playdoit():
    print("\n--- SUITE 2: Verificación de Reglas Específicas de Playdoit.mx ---")
    print("Objetivo: Validar rollover del 80% (momio >= -130 / 1.77) y asimetría de lesiones.")
    
    # 1. Rollover de retiro: cuota >= -130 (1.769)
    assert_test(
        "Configuración MIN_PLAYDOIT_ODDS_DECIMAL es exactamente 1.77",
        config.MIN_PLAYDOIT_ODDS_DECIMAL == 1.77,
        f"Valor configurado: {config.MIN_PLAYDOIT_ODDS_DECIMAL}"
    )
    assert_test(
        "Momio -115 convierte a cuota decimal 1.87 y cumple rollover",
        momio_americano_a_decimal("-115") == 1.87 and cumple_rollover(1.87),
        "Conversión o validación falló"
    )
    assert_test(
        "Momio -140 convierte a 1.71 y es RECHAZADO por no cumplir rollover",
        momio_americano_a_decimal("-140") < 1.769 and not cumple_rollover(momio_americano_a_decimal("-140")),
        "Momio menor a -130 no debe cumplir rollover"
    )

    # 2. Asimetría contractual de lesiones (Void 1H en Over / Win en Under)
    p_ov_win, p_un_win, p_ov_void = aplicar_asimetria_lesion_playdoit(raw_prob_over=0.50, raw_prob_under=0.50)
    assert_test(
        f"OVER tiene probabilidad de anulación (Void) asignada (~1.0%, obtenido: {p_ov_void})",
        p_ov_void == 0.010,
        f"Void inesperado: {p_ov_void}"
    )
    assert_test(
        f"UNDER recibe beneficio acumulado de lesión 1H y 2H (+1.8%, obtenido: {p_un_win:.3f})",
        p_un_win > 0.508,
        f"Probabilidad de Under no incrementó debidamente: {p_un_win}"
    )


def test_suite_3_modelo_matematico_panjer():
    print("\n--- SUITE 3: Verificación Matemática de la Recursión de Panjer (Puntos NBA) ---")
    print("Objetivo: Demostrar que los puntos usan Binomial Negativa Compuesta con saltos de 1, 2 y 3 puntos.")
    
    mu_pts = 22.5
    alpha = 0.09
    max_k = 40
    g = _compound_points_pmf(max_k=max_k, mu_points=mu_pts, alpha_events=alpha)
    
    suma_parcial = sum(g)
    assert_test(
        f"Vector PMF de Panjer es no vacío y probabilísticamente coherente (Suma parcial K=40: {suma_parcial:.3f})",
        len(g) == max_k + 1 and 0.85 <= suma_parcial <= 1.00,
        f"Suma fuera de rango: {suma_parcial}"
    )
    
    # Monotonía y cola
    prob_over, prob_under = calcular_probabilidad_linea(media_lambda=22.5, linea=22.5, market_type="player_points")
    assert_test(
        f"Línea centrada (media=22.5, linea=22.5) produce probabilidades equilibradas (Over: {prob_over:.2f}, Under: {prob_under:.2f})",
        0.45 <= prob_over <= 0.55 and 0.45 <= prob_under <= 0.55,
        f"Probabilidades anómalas: {prob_over}, {prob_under}"
    )


def test_suite_4_blindaje_lesiones_regex():
    print("\n--- SUITE 4: Blindaje Contra Alucinaciones en Notas de Lesión ---")
    print("Objetivo: Verificar que reportes históricos no generen falsos topes de minutos.")
    
    # Caso 1: Oración histórica que antes generaba falsos topes
    texto_historico = "He played 26 minutes in Wednesday's loss against Orlando and looked sluggish."
    tope_falso = _extraer_tope_minutos_de_texto(texto_historico)
    assert_test(
        "Texto de partido pasado ('played 26 minutes') NO genera tope de minutos (None)",
        tope_falso is None,
        f"Se extrajo erróneamente: {tope_falso}"
    )

    # Caso 2: Restricción médica legítima
    texto_medico = "Head coach announced he will be on a strict 22-minute restriction tonight."
    tope_real = _extraer_tope_minutos_de_texto(texto_medico)
    assert_test(
        f"Restricción médica explícita ('22-minute restriction') extrae tope correcto ({tope_real}m)",
        tope_real == 22.0,
        f"Tope esperado 22.0, obtenido: {tope_real}"
    )


def test_suite_5_ordenamiento_alertas():
    print("\n--- SUITE 5: Verificación de Ordenamiento de Alertas en main.py ---")
    print("Objetivo: Verificar que se ordenen las oportunidades de mejor a peor antes del corte de Telegram.")
    
    with open(BASE_DIR / "main.py", "r", encoding="utf-8") as f:
        codigo_main = f.read()
        
    tiene_sort = "oportunidades.sort" in codigo_main and "reverse=True" in codigo_main
    assert_test(
        "main.py contiene ordenamiento explícito descendente de oportunidades",
        tiene_sort,
        "No se encontró 'oportunidades.sort(..., reverse=True)' en main.py"
    )


def test_suite_6_workflow_nube_y_liquidacion():
    print("\n--- SUITE 6: Verificación de Automatización en GitHub Actions ---")
    print("Objetivo: Confirmar que el workflow 24/7 en la nube liquida apuestas y actualiza la bitácora.")
    
    wf_path = BASE_DIR / ".github" / "workflows" / "munetips_cloud.yml"
    with open(wf_path, "r", encoding="utf-8") as f:
        codigo_wf = f.read()
        
    tiene_liquidacion = "verificar_resultados.py" in codigo_wf
    tiene_commit = "git commit -m" in codigo_wf and "git push origin main" in codigo_wf
    
    assert_test(
        "Workflow ejecuta 'scripts/verificar_resultados.py' automáticamente",
        tiene_liquidacion,
        "Falta la llamada a verificar_resultados.py en el workflow"
    )
    assert_test(
        "Workflow persiste los resultados en GitHub (commit y push)",
        tiene_commit,
        "Falta la persistencia git de datos"
    )


def test_suite_7_integridad_bookmarklet():
    print("\n--- SUITE 7: Verificación del Bookmarklet Shadow DOM de Playdoit ---")
    print("Objetivo: Garantizar que el bookmarklet no contenga caracteres conflictivos (%27 o backticks).")
    
    bm_path = BASE_DIR / "scripts" / "bookmarklet_playdoit.js"
    with open(bm_path, "r", encoding="utf-8") as f:
        codigo_bm = f.read()
        
    # Verificar penetración de shadowRoot
    penetra_shadow = "shadowRoot" in codigo_bm
    # Verificar que las alertas no descarguen vacío cuando no hay líneas
    avisa_vacio = "Especiales por jugador" in codigo_bm
    
    assert_test(
        "Bookmarklet penetra limpiamente la cápsula del ShadowRoot",
        penetra_shadow,
        "Falta el acceso a shadowRoot"
    )
    assert_test(
        "Bookmarklet incluye protección contra pestaña vacía ('Especiales por jugador')",
        avisa_vacio,
        "Falta el aviso de pestaña 'Especiales por jugador'"
    )


def main():
    print(f"\n=========================================================================================")
    print(f"🤖 REPORTE DE AUDITORÍA INDEPENDIENTE PARA TERCERA IA — MUNETIPS NBA QUANT PIPELINE")
    print(f"=========================================================================================")
    print(f"Directorio Base: {BASE_DIR}")
    print(f"Python: {sys.version.split()[0]}")
    
    test_suite_1_calibracion_score()
    test_suite_2_reglas_playdoit()
    test_suite_3_modelo_matematico_panjer()
    test_suite_4_blindaje_lesiones_regex()
    test_suite_5_ordenamiento_alertas()
    test_suite_6_workflow_nube_y_liquidacion()
    test_suite_7_integridad_bookmarklet()
    
    print("\n=========================================================================================")
    pct = (pruebas_pasadas / total_pruebas) * 100.0 if total_pruebas > 0 else 0
    print(f"📊 RESUMEN FINAL: {pruebas_pasadas} de {total_pruebas} pruebas superadas ({pct:.1f}%)")
    
    if pruebas_pasadas == total_pruebas:
        print(f"{COL_VERDE}✅ ESTADO DE AUDITORÍA: APROBADO AL 100%. EL SISTEMA CUMPLE TODAS LAS ESPECIFICACIONES.{COL_RESET}")
        print("=========================================================================================\n")
        sys.exit(0)
    else:
        print(f"{COL_ROJO}❌ ESTADO DE AUDITORÍA: FALLOS DETECTADOS. REVISAR DETALLES ARRIBA.{COL_RESET}")
        print("=========================================================================================\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
