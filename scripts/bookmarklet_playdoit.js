/**
 * Bookmarklet Capturador Definitivo de Cuotas Reales de Playdoit.mx (Motor Altenar Shadow DOM)
 * Diseñado sin comillas simples (') ni backticks (`) para evitar que Chrome
 * las convierta en %27 o %60 al guardar el marcador.
 */
javascript:(function(){
  try {
    var sr = Array.from(document.querySelectorAll("*")).find(function(el){ return el.shadowRoot; });
    var root = sr ? sr.shadowRoot : null;
    if (!root) {
      alert("❌ No se detectó la ventana de Playdoit. Asegúrate de estar dentro de un partido de NBA en Playdoit.mx.");
      return;
    }
    
    var fullText = root.firstElementChild ? root.firstElementChild.innerText : root.textContent;
    if (!fullText) {
      alert("❌ No se pudo leer el contenido de Playdoit.");
      return;
    }

    var regex = /([A-Za-zÀ-ÿ\s\.\-]{3,45})\n+(\d{1,2}\.5)\n+([+-]\d{3,4})\n+([+-]\d{3,4})/g;
    var resultados = [];
    var m;
    while ((m = regex.exec(fullText)) !== null) {
      var lineas = m[1].split("\n").map(function(s){ return s.trim(); }).filter(Boolean);
      var nombre = lineas.length > 0 ? lineas[lineas.length - 1] : "";
      nombre = nombre.replace(/^(a|Más de|Menos de|Línea|Line|SGP)\s+/i, "").trim();
      if (nombre.length >= 3 && !nombre.includes("prórroga") && !nombre.includes("Totales")) {
        resultados.push({
          jugador: nombre,
          linea: parseFloat(m[2]),
          momio_mas_de: m[3].trim(),
          momio_menos_de: m[4].trim()
        });
      }
    }

    if (resultados.length === 0) {
      alert("⚠️ Se detectaron 0 líneas de jugadores.\n\n👉 Haz clic en la pestaña 'Especiales por jugador' (y en 'Puntos') en Playdoit para que se muestren las tablas de los jugadores, y vuelve a presionar este botón.");
      return;
    }

    var payload = {
      capturedAt: new Date().toISOString(),
      url: window.location.href,
      totalLineasCapturadas: resultados.length,
      mercados: resultados
    };

    var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    var a = document.createElement("a");
    var fName = "playdoit_nba_" + new Date().toISOString().slice(0, 10) + "_" + Date.now().toString().slice(-4) + ".json";
    a.href = URL.createObjectURL(blob);
    a.download = fName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    var toast = document.createElement("div");
    toast.style.cssText = "position:fixed;top:25px;right:25px;background:#15803d;color:#fff;padding:14px 22px;border-radius:10px;box-shadow:0 6px 16px rgba(0,0,0,0.35);z-index:999999;font-family:sans-serif;font-size:14px;font-weight:bold";
    toast.innerText = "✅ Playdoit NBA: " + resultados.length + " líneas capturadas (" + fName + ")";
    document.body.appendChild(toast);
    setTimeout(function(){ toast.remove(); }, 4500);

  } catch(err) {
    alert("Error al extraer cuotas: " + err.message);
  }
})();
