#!/usr/bin/env python3
"""
scripts/comparar_playdoit.py
-----------------------------
Módulo de Comparación Empírica y Aprendizaje de Playdoit.mx
Compara las líneas y cuotas reales capturadas directamente de Playdoit
contra las predicciones y alertas registradas en `data/munetips_historial_clv.csv`.

Uso:
    python scripts/comparar_playdoit.py [ruta_al_json]
"""

import sys
import os
import json
import csv
from datetime import datetime
from pathlib import Path

def momio_americano_a_decimal(momio_str: str) -> float:
    try:
        val = float(str(momio_str).replace('+', '').strip())
        if val > 0:
            return round(1.0 + (val / 100.0), 3)
        elif val < 0:
            return round(1.0 + (100.0 / abs(val)), 3)
        return 1.0
    except Exception:
        return 1.0

def cumple_rollover(decimal: float) -> bool:
    """Regla Playdoit: cuota >= -130 americano equivale a decimal >= 1.769"""
    return decimal >= 1.769

def normalizar(texto: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize('NFKD', texto)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()

def buscar_archivo_mas_reciente() -> Path:
    base_dir = Path(__file__).resolve().parent.parent
    candidatos = []
    
    # 1. data/playdoit_capturas/
    carpeta_data = base_dir / "data" / "playdoit_capturas"
    if carpeta_data.exists():
        candidatos.extend(list(carpeta_data.glob("playdoit_*.json")))
        
    # 2. Carpeta Downloads del usuario
    descargas = Path(os.environ.get("USERPROFILE", "")) / "Downloads"
    if descargas.exists():
        candidatos.extend(list(descargas.glob("playdoit_*.json")))
        
    # 3. Raíz del workspace
    candidatos.extend(list(base_dir.glob("playdoit_*.json")))

    if not candidatos:
        return None
        
    candidatos.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidatos[0]

def analizar_captura(ruta_json: Path):
    print(f"\n=========================================================================================")
    print(f"📊 AUDITORÍA EMPÍRICA PLAYDOIT — APRENDIZAJE CUANTITATIVO")
    print(f"=========================================================================================")
    print(f"📁 Archivo: {ruta_json.name}")
    print(f"🕒 Fecha modif: {datetime.fromtimestamp(ruta_json.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
    
    with open(ruta_json, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    mercados = data.get("mercados", [])
    total = len(mercados)
    print(f"🎯 Total de líneas analizadas: {total}\n")
    
    if total == 0:
        print("⚠️ No hay líneas registradas en este JSON. Revisa la captura.")
        return
        
    # Indexar mercados por jugador normalizado
    mercados_dict = {}
    for m in mercados:
        j_norm = normalizar(m.get("jugador", ""))
        mercados_dict[j_norm] = m

    # 1. Tabla de Cuotas y Cumplimiento de Rollover
    print(f"{'JUGADOR':<25} {'LÍNEA':<7} {'MÁS DE':<15} {'MENOS DE':<15} {'ROLLOVER OVER':<15} {'ROLLOVER UNDER'}")
    print("-" * 95)
    
    cumplen_over = 0
    cumplen_under = 0
    
    for m in mercados:
        jugador = m.get("jugador", "Desconocido")[:24]
        linea = m.get("linea", 0.0)
        mas_de = str(m.get("momio_mas_de", ""))
        menos_de = str(m.get("momio_menos_de", ""))
        
        dec_mas = momio_americano_a_decimal(mas_de)
        dec_menos = momio_americano_a_decimal(menos_de)
        
        roll_mas = "✅ SÍ" if cumple_rollover(dec_mas) else "❌ NO (< -130)"
        roll_menos = "✅ SÍ" if cumple_rollover(dec_menos) else "❌ NO (< -130)"
        
        if cumple_rollover(dec_mas):
            cumplen_over += 1
        if cumple_rollover(dec_menos):
            cumplen_under += 1
            
        print(f"{jugador:<25} {linea:<7.1f} {mas_de:>5} ({dec_mas:.2f})    {menos_de:>5} ({dec_menos:.2f})    {roll_mas:<15} {roll_menos}")
        
    print("-" * 95)
    print(f"📈 Resumen de Rollover Playdoit (Cuotas >= -130 / 1.77):")
    print(f"   • Opciones Más de apostables: {cumplen_over}/{total} ({cumplen_over/total*100:.1f}%)")
    print(f"   • Opciones Menos de apostables: {cumplen_under}/{total} ({cumplen_under/total*100:.1f}%)")

    # 2. Cruce con alertas de Telegram (munetips_historial_clv.csv) si existe
    csv_historial = Path(__file__).resolve().parent.parent / "data" / "munetips_historial_clv.csv"
    if csv_historial.exists():
        print(f"\n🔍 CRUCE CON HISTORIAL DE ALERTAS TELEGRAM (munetips_historial_clv.csv):")
        print("-" * 95)
        with open(csv_historial, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            alertas = list(reader)
            
        if not alertas:
            print("No hay alertas registradas en el CSV.")
        else:
            for al in alertas:
                j_al = al.get("jugador", "")
                j_al_norm = normalizar(j_al)
                linea_al = float(al.get("linea_playdoit", 0))
                lado_al = al.get("lado", "").upper()
                
                # Buscar en la captura
                encontrado = None
                for k, v in mercados_dict.items():
                    if k in j_al_norm or j_al_norm in k:
                        encontrado = v
                        break
                        
                if encontrado:
                    cuota_real_str = encontrado.get("momio_menos_de" if "UNDER" in lado_al else "momio_mas_de")
                    cuota_real_dec = momio_americano_a_decimal(cuota_real_str)
                    roll_ok = "✅ CUMPLE ROLLOVER" if cumple_rollover(cuota_real_dec) else "❌ NO CUMPLE"
                    diff_linea = encontrado.get("linea", 0) - linea_al
                    
                    print(f"🎯 ALERTA: {j_al} {lado_al} {linea_al}")
                    print(f"   • Línea Real Playdoit: {encontrado.get('linea')} (Dif: {diff_linea:+.1f})")
                    print(f"   • Momio Real Playdoit: {cuota_real_str} ({cuota_real_dec:.2f}) -> {roll_ok}")
                else:
                    print(f"ℹ️ ALERTA: {j_al} {lado_al} {linea_al} no figuraba en la pestaña capturada de este partido.")
    print("=========================================================================================\n")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        archivo = Path(sys.argv[1])
    else:
        archivo = buscar_archivo_mas_reciente()
        
    if not archivo or not archivo.exists():
        print("❌ No se encontró ningún archivo playdoit_*.json en data/playdoit_capturas, Descargas o raíz.")
        print("Copia tu archivo descargado a 'data/playdoit_capturas/' y vuelve a ejecutar.")
        sys.exit(1)
        
    analizar_captura(archivo)
