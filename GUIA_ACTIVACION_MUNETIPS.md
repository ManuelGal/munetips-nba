# Guía de Activación en 5 Minutos — Canal **Muñetips** (24/7 con la PC Apagada)

Todo el código ya está creado en esta carpeta (`c:\Users\djell\OneDrive\Documents\PM Bett`).
Está diseñado para correr **100% gratis en los servidores de GitHub Actions** (tu computadora puede estar completamente apagada) y con **consumo cero de tokens** en el escaneo.

---

## Paso 1: Crear tu Canal **`Muñetips`** y tu Bot en Telegram (2 minutos desde tu celular `+52 55 3993 6844`)

*(Por seguridad de Telegram, ningún sistema externo puede crear canales a nombre de tu número telefónico sin tu app; se hace una sola vez desde tu teléfono en 4 toques)*:

1. **Crea el canal `Muñetips`:**
   - Abre Telegram en tu celular (`+52 55 3993 6844`) $\rightarrow$ botón de nuevo mensaje $\rightarrow$ **Nuevo Canal**.
   - Ponle de nombre: **`Muñetips`**.
   - Si lo pones **Público** con un enlace como `t.me/munetips_nba`, tu `TELEGRAM_CHAT_ID` será simplemente `@munetips_nba`. Si lo pones **Privado** (recomendado para que solo tú veas tus alertas), el propio sistema detectará el ID numérico en el Paso 3.

2. **Crea el Bot mensajero (Gratis e instantáneo):**
   - En el buscador de Telegram escribe **`@BotFather`** (tiene la palomita azul oficial) y toca **Iniciar**.
   - Escríbele `/newbot`
   - Te pedirá un nombre visible: escribe `Muñetips Quant Bot`
   - Te pedirá un usuario terminado en `bot`: escribe por ejemplo `munetips_nba_bot`
   - `@BotFather` te responderá con un código largo (**Token**), parecido a `7123456789:AAHxyz...`. **Cópialo.**

3. **Mete al Bot a tu canal `Muñetips`:**
   - Entra a tu canal **`Muñetips`** $\rightarrow$ toca el nombre arriba $\rightarrow$ **Administradores** $\rightarrow$ **Añadir administrador**.
   - Busca a tu bot (`munetips_nba_bot`) y dale permiso de **Publicar mensajes**.
   - Escribe cualquier mensaje corto dentro del canal **`Muñetips`** (por ejemplo: `Prueba`).

---

## Paso 2: Obtener tu llave gratuita de cuotas NBA (1 minuto)

1. Entra a [https://the-odds-api.com](https://the-odds-api.com) y dale al botón **Get API Key (Free)**.
2. Pon tu correo y te llegará al instante tu llave gratuita (incluye **500 créditos gratis cada mes**; nuestro código tiene un filtro protector que solo gasta créditos cuando hay un titular lesionado en la NBA).

---

## Paso 3: Subirlo a la nube para que corra con tu PC apagada (2 minutos)

1. Entra a [https://github.com/new](https://github.com/new) (crea cuenta gratis si no tienes) y crea un repositorio llamado `munetips-nba` (puedes elegir **Private** o **Public**).
2. Haz clic en **"uploading an existing file"** y arrastra todos los archivos y carpetas de esta carpeta (`PM Bett`), incluyendo la carpeta `.github` y `src`.
3. En tu repositorio de GitHub, ve a **Settings** (arriba) $\rightarrow$ **Secrets and variables** (menú izquierdo) $\rightarrow$ **Actions** $\rightarrow$ botón verde **New repository secret** y agrega estos 3 secretos:
   - `TELEGRAM_BOT_TOKEN`: *(pega el token que te dio `@BotFather`)*
   - `ODDS_API_KEY`: *(pega la llave que te llegó por correo de The Odds API)*
   - `TELEGRAM_CHAT_ID`: *(si tu canal es privado y aún no sabes el ID numérico, déjalo vacío un segundo y sigue el punto 4)*

4. **Probar la alerta en tu celular ahora mismo (sin prender la PC):**
   - En tu repositorio de GitHub, toca la pestaña **Actions** $\rightarrow$ selecciona **Muñetips NBA Cloud Scanner (24/7 Sin PC)** $\rightarrow$ botón **Run workflow**.
   - Si tu canal **`Muñetips`** es privado, primero elige el modo `find-channel` y dale a **Run**: en los logs verás escrito `[ENCONTRADO] Canal: 'Muñetips' -> TELEGRAM_CHAT_ID = -100...`. Copia ese número `-100...` y guárdalo en el secreto `TELEGRAM_CHAT_ID`.
   - Ahora vuelve a darle a **Run workflow**, elige el modo **`demo`** y en **3 segundos te sonará el celular con la primera alerta completa en el canal `Muñetips`**.
finalmente el sistema quedará corriendo solo todos los días en las ventanas de la NBA (`9:35 AM`, `12:35 PM`, `2:35 PM`, `3:35 PM`, `4:05 PM`, `4:35 PM`, `5:20 PM`, `6:20 PM` y `8:20 PM` hora CDMX).
