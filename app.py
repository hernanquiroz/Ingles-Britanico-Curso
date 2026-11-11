import json
import re
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

from gsheets_sync import clear_progress, load_progress, save_result as sync_save

st.set_page_config(page_title="British English · MCER A1–C2", page_icon="🇬🇧", layout="centered")

# ------------------------------------------------------------------ #
# Configuración                                                      #
# ------------------------------------------------------------------ #
LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
LEVEL_NAMES = {
    "A1": "Principiante", "A2": "Elemental", "B1": "Intermedio",
    "B2": "Intermedio alto", "C1": "Avanzado", "C2": "Maestría",
}
CEFR_DESC = {
    "A1": "Entiende y usa expresiones cotidianas básicas; se presenta y responde preguntas simples.",
    "A2": "Se comunica en tareas simples y rutinarias; describe su entorno y necesidades inmediatas.",
    "B1": "Se desenvuelve en viajes, describe experiencias y da opiniones sencillas.",
    "B2": "Interactúa con fluidez y espontaneidad; argumenta ventajas e inconvenientes.",
    "C1": "Uso flexible y eficaz del idioma con fines sociales, académicos y profesionales.",
    "C2": "Comprende prácticamente todo con facilidad y se expresa con precisión y matices.",
}
BRIT_TIPS = {
    "A1": "flat = piso · autumn = otoño · biscuit = galleta",
    "A2": "lift = ascensor · holiday = vacaciones · chemist's = farmacia · queue = fila",
    "B1": "lorry = camión · postcode = código postal · rubbish = basura · petrol = gasolina",
    "B2": "'have got' es muy británico: *I've got a car* · at the weekend (no 'on')",
    "C1": "Registro: *shall we?*, *fancy a cuppa?*, *brilliant*, *cheers* = gracias (informal)",
    "C2": "Matices: *quite good* en británico puede significar 'regular', no 'muy bueno'.",
}
SKILLS = [
    ("vocab", "📝 Vocabulario"),
    ("grammar", "✏️ Gramática"),
    ("reading", "📖 Lectura"),
    ("listening", "🎧 Escucha"),
    ("writing", "🖊️ Escritura"),
]
SKILL_LABEL = dict(SKILLS)
PROVIDERS = {
    "Gemini (gratis)": {
        "kind": "gemini",
        "model": "gemini-2.0-flash",
        "help": "Clave gratuita en https://aistudio.google.com/app/apikey",
    },
    "Groq (gratis)": {
        "kind": "openai",
        "base": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "help": "Clave gratuita en https://console.groq.com/keys",
    },
    "OpenAI": {
        "kind": "openai",
        "base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "help": "Clave de pago en https://platform.openai.com/api-keys",
    },
}

# ------------------------------------------------------------------ #
# Progreso persistente (Google Sheets si hay credenciales; si no,    #
# fichero local progress.json)                                        #
# ------------------------------------------------------------------ #
def save_result(level, skill, score, total):
    entry = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "level": level,
        "skill": skill,
        "score": score,
        "total": total,
        "percent": round(100 * score / total, 1),
    }
    st.session_state.history.append(entry)
    if sync_save(entry):
        st.toast("☁️ Sincronizado con Google Sheets")


def grade(pct):
    if pct >= 90:
        return "A — Sobresaliente 🏆"
    if pct >= 75:
        return "B — Notable 💪"
    if pct >= 60:
        return "C — Aprobado ✅"
    return "D — A repasar 📚"


def next_level_advice(df):
    for i, lv in enumerate(LEVELS[:-1]):
        sub = df[df["level"] == lv].tail(3)
        if len(sub) >= 3 and sub["percent"].mean() >= 85:
            nxt = LEVELS[i + 1]
            return (f"Media de {sub['percent'].mean():.0f} % en tus últimos 3 intentos "
                    f"de **{lv}** → listo para subir a **{nxt}** 🎯")
    if (df["level"] == "C2").any() and df[df["level"] == "C2"]["percent"].mean() >= 85:
        return "Dominas C2: nivel de maestría. ¡Enhorabuena! 🏆"
    return "Sigue practicando: con una media del 85 % en tus últimos 3 intentos de un nivel, te recomendaré subir."

# ------------------------------------------------------------------ #
# Llamadas a la API                                                  #
# ------------------------------------------------------------------ #
def call_llm(prompt, provider, model, api_key):
    cfg = PROVIDERS[provider]
    if cfg["kind"] == "gemini":
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
              f"{model}:generateContent?key={api_key}")
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.8,
                                "responseMimeType": "application/json"},
        }
        r = requests.post(url, json=body, timeout=90)
    else:
        url = f"{cfg['base']}/chat/completions"
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Eres un generador de ejercicios. "
                                              "Respondes únicamente con JSON válido."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.8,
            "response_format": {"type": "json_object"},
        }
        r = requests.post(url, headers={"Authorization": f"Bearer {api_key}"},
                          json=body, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"La API respondió {r.status_code}: {r.text[:400]}")
    data = r.json()
    if cfg["kind"] == "gemini":
        return data["candidates"][0]["content"]["parts"][0]["text"]
    return data["choices"][0]["message"]["content"]


def parse_json(text):
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
    t = re.sub(r"\n?```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("La respuesta no contiene JSON.")
    return json.loads(t[start:end + 1])


def build_prompt(level, skill, topic):
    base = (
        f"Eres un profesor de INGLÉS BRITÁNICO que crea ejercicios según el Marco Común "
        f"Europeo de Referencia (MCER). Nivel: {level} ({LEVEL_NAMES[level]}). "
        f"Tema: {topic or 'variado, vida cotidiana en el Reino Unido'}. "
        f"Usa vocabulario y ortografía británicos (flat, autumn, colour, organise...), "
        f"nunca americanismos. Las preguntas y textos van en inglés; "
        f"las explicaciones e instrucciones, en español. "
        f"Dificultad estrictamente acorde al nivel {level}.\n"
    )
    esquemas = {
        "vocab": ('Crea 5 preguntas de opción múltiple de vocabulario británico '
                  '(incluye diferencias británico/americano cuando sea útil). '
                  'JSON: {"questions": [{"q": "...", "options": ["a","b","c"], '
                  '"answer": 0, "explain": "..."}]}'),
        "grammar": ('Crea 5 preguntas de opción múltiple de gramática. '
                    'JSON: {"questions": [{"q": "...", "options": ["a","b","c"], '
                    '"answer": 0, "explain": "..."}]}'),
        "reading": ('Escribe un texto de lectura de 120-200 palabras y 3 preguntas de '
                    'comprensión. JSON: {"text": "...", "questions": [{"q": "...", '
                    '"options": ["a","b","c"], "answer": 0, "explain": "..."}]}'),
        "listening": ('Escribe una transcripción en primera persona de 80-130 palabras '
                      '(se leerá en voz alta) y 3 preguntas de comprensión auditiva. '
                      'JSON: {"transcript": "...", "questions": [{"q": "...", '
                      '"options": ["a","b","c"], "answer": 0, "explain": "..."}]}'),
        "writing": ('Crea una tarea de escritura adecuada al nivel, un ejemplo de '
                    'respuesta modelo y una lista de 4 criterios de autoevaluación. '
                    'JSON: {"prompt": "...", "model": "...", '
                    '"checklist": ["...", "...", "...", "..."]}'),
    }
    return base + esquemas[skill] + "\nResponde SOLO con el JSON, sin markdown ni texto extra."


def build_correction_prompt(level, task, text):
    return (
        f"Eres examinador de inglés británico (MCER), nivel objetivo {level}.\n"
        f'Tarea: "{task}"\nTexto del estudiante: """{text}"""\n'
        'Evalúa y responde SOLO con JSON válido:\n'
        '{"score": 0, "level_estimate": "A1|A2|B1|B2|C1|C2", '
        '"corrections": [{"original": "...", "corrected": "...", '
        '"why": "explicación en español"}], '
        '"improved": "versión mejorada en inglés británico", '
        '"comments": "comentario breve en español"}\n'
        "score: entero de 0 a 100. Sé constructivo y exige según el nivel."
    )


def speak(text):
    """Lee el texto con la voz en-GB del navegador (gratis, sin API)."""
    components.html(
        f"""<script>
        const u = new SpeechSynthesisUtterance({json.dumps(text)});
        u.lang = "en-GB"; u.rate = 0.95;
        const vs = speechSynthesis.getVoices();
        const v = vs.find(x => x.lang === "en-GB") || vs.find(x => x.lang.startsWith("en"));
        if (v) u.voice = v;
        speechSynthesis.cancel(); speechSynthesis.speak(u);
        </script>""",
        height=0,
    )

# ------------------------------------------------------------------ #
# Estado                                                              #
# ------------------------------------------------------------------ #
st.session_state.setdefault("bank", {})      # {(level, skill): ejercicios}
st.session_state.setdefault("versions", {})  # {(level, skill): nº de generación}
st.session_state.setdefault("history", load_progress())

# ------------------------------------------------------------------ #
# Barra lateral                                                      #
# ------------------------------------------------------------------ #
with st.sidebar:
    st.header("⚙️ Configuración")
    provider = st.selectbox("Proveedor de IA", list(PROVIDERS.keys()))
    model = st.text_input("Modelo", value=PROVIDERS[provider]["model"])
    api_key = st.text_input("Clave API", type="password",
                            value=st.secrets.get("API_KEY", ""),
                            help=PROVIDERS[provider]["help"])
    st.divider()
    level = st.selectbox("Nivel MCER", LEVELS,
                        format_func=lambda x: f"{x} — {LEVEL_NAMES[x]}")
    skill_label = st.radio("Actividad", [s[1] for s in SKILLS])
    skill = dict((lbl, key) for key, lbl in SKILLS)[skill_label]
    topic = st.text_input("Tema (opcional)", placeholder="viajes, trabajo, comida…")
    generate = st.button("✨ Generar ejercicios", type="primary",
                        use_container_width=True)
    if st.session_state.history:
        st.divider()
        df_side = pd.DataFrame(st.session_state.history)
        st.subheader("📊 Resumen")
        st.metric("Media global", f"{df_side['percent'].mean():.0f} %")
        st.caption(f"{len(df_side)} intentos registrados · "
                  f"detalle en la pestaña «Mi progreso»")

# ------------------------------------------------------------------ #
# Cuerpo                                                              #
# ------------------------------------------------------------------ #
st.title("🇬🇧 British English Trainer")
st.caption("Ejercicios generados con IA · Marco Común Europeo · A1 a C2 · con calificación y progreso")

tab_practice, tab_progress = st.tabs(["🎯 Practicar", "📊 Mi progreso"])

# ================================================================== #
# PESTAÑA 1 · PRACTICAR                                              #
# ================================================================== #
with tab_practice:
    with st.expander(f"📏 Nivel {level} — {LEVEL_NAMES[level]}"):
        st.write(CEFR_DESC[level])
        st.info(f"🇬🇧 {BRIT_TIPS[level]}")

    key = (level, skill)
    version = st.session_state.versions.get(key, 0)

    if generate:
        if not api_key:
            st.error("Introduce tu clave API en la barra lateral "
                    "(Gemini y Groq la dan gratis).")
        else:
            with st.spinner("Generando ejercicios con IA…"):
                try:
                    raw = call_llm(build_prompt(level, skill, topic),
                                  provider, model, api_key)
                    st.session_state.bank[key] = parse_json(raw)
                    st.session_state.versions[key] = version + 1
                    st.rerun()
                except Exception as e:
                    st.error(f"No se pudo generar: {e}")

    data = st.session_state.bank.get(key)
    if not data:
        st.info("Pulsa **✨ Generar ejercicios** para empezar. "
                "Cada pulsación crea un set nuevo, así que nunca repetirás.")
    else:
        prefix = f"{level}_{skill}_{st.session_state.versions.get(key, 0)}"

        def render_quiz(questions):
            with st.form(f"form_{prefix}"):
                chosen = []
                for i, item in enumerate(questions):
                    st.markdown(f"**{i + 1}. {item['q']}**")
                    chosen.append(st.radio("Elige:", item["options"],
                                          key=f"{prefix}_q{i}",
                                          label_visibility="collapsed"))
                submitted = st.form_submit_button("Corregir ✅", type="primary")
            if submitted:
                score = 0
                for i, item in enumerate(questions):
                    ok = item["options"].index(chosen[i]) == item["answer"]
                    score += ok
                    if ok:
                        st.success(f"{i + 1}. ✅ Correcto — {item['explain']}")
                    else:
                        st.error(f"{i + 1}. ❌ Correcta: **{item['options'][item['answer']]}**"
                                f" — {item['explain']}")
                total = len(questions)
                pct = round(100 * score / total)
                save_result(level, skill, score, total)
                c1, c2 = st.columns(2)
                c1.metric("Resultado", f"{score}/{total} · {pct} %")
                c2.metric("Calificación", grade(pct))
                if pct == 100:
                    st.balloons()
                st.caption("Guardado en tu historial · Genera un set nuevo para seguir practicando.")

        if skill in ("vocab", "grammar"):
            render_quiz(data["questions"])

        elif skill == "reading":
            st.markdown("### 📖 Lee el texto")
            st.write(data["text"])
            st.divider()
            render_quiz(data["questions"])

        elif skill == "listening":
            st.markdown("### 🎧 Escucha con atención")
            st.caption("Usa la voz británica (en-GB) de tu navegador. "
                      "Puedes repetirla las veces que quieras.")
            if st.button("🔊 Escuchar / repetir"):
                speak(data["transcript"])
            with st.expander("Ver transcripción (solo si te rindes 😄)"):
                st.write(data["transcript"])
            st.divider()
            render_quiz(data["questions"])

        elif skill == "writing":
            st.markdown("### 🖊️ Tarea de escritura")
            st.write(data["prompt"])
            text = st.text_area("Escribe aquí tu texto en inglés:", height=180,
                                key=f"{prefix}_essay")
            with st.expander("Ver ejemplo de respuesta y criterios"):
                st.write(data["model"])
                for c in data["checklist"]:
                    st.write(f"- {c}")
            if st.button("🤖 Corregir con IA", type="primary"):
                if not api_key:
                    st.error("Introduce tu clave API en la barra lateral.")
                elif len(text.strip()) < 10:
                    st.warning("Escribe un poco más antes de corregir.")
                else:
                    with st.spinner("Corrigiendo…"):
                        try:
                            raw = call_llm(build_correction_prompt(level, data["prompt"], text),
                                          provider, model, api_key)
                            fb = parse_json(raw)
                        except Exception as e:
                            st.error(f"No se pudo corregir: {e}")
                            st.stop()
                    score = int(fb.get("score", 0))
                    save_result(level, skill, score, 100)
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Puntuación", f"{score}/100")
                    c2.metric("Calificación", grade(score))
                    c3.metric("Nivel estimado", fb.get("level_estimate", "—"))
                    for corr in fb.get("corrections", []):
                        st.error(f"❌ {corr['original']} → ✅ **{corr['corrected']}**"
                                f" — {corr['why']}")
                    st.success(f"✨ Versión mejorada: {fb.get('improved', '')}")
                    st.info(f"💬 {fb.get('comments', '')}")
                    st.caption("Guardado en tu historial.")

# ================================================================== #
# PESTAÑA 2 · MI PROGRESO                                            #
# ================================================================== #
with tab_progress:
    hist = st.session_state.history
    if not hist:
        st.info("Aún no hay resultados. Completa un ejercicio en la pestaña "
                "«Practicar» y tu progreso aparecerá aquí.")
    else:
        df = pd.DataFrame(hist)
        df["date"] = pd.to_datetime(df["date"])

        # --- Métricas globales ---
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Intentos", len(df))
        c2.metric("Media global", f"{df['percent'].mean():.0f} %")
        c3.metric("Mejor resultado", f"{df['percent'].max():.0f} %")
        c4.metric("Días practicados", df["date"].dt.date.nunique())

        st.success(f"🧭 **Recomendación:** {next_level_advice(df)}")

        # Nombres legibles para mostrar
        df["skill"] = df["skill"].map(lambda s: SKILL_LABEL.get(s, s))

        # --- Filtros ---
        f1, f2 = st.columns(2)
        lv_sel = f1.multiselect("Nivel", LEVELS,
                                default=sorted(df["level"].unique(), key=LEVELS.index))
        sk_sel = f2.multiselect("Actividad", [s[1] for s in SKILLS],
                                default=sorted(df["skill"].unique()))
        f = df[df["level"].isin(lv_sel) & df["skill"].isin(sk_sel)]

        if f.empty:
            st.warning("No hay datos con esos filtros.")
        else:
            # --- Gráfica de evolución ---
            st.subheader("📈 Evolución de tu puntuación")
            pivot = f.pivot_table(index="date", columns="level",
                                  values="percent", aggfunc="mean")
            st.line_chart(pivot)

            # --- Tabla resumen con calificaciones ---
            st.subheader("🏅 Calificaciones por nivel y actividad")
            resumen = (f.groupby(["level", "skill"])
                        .agg(Intentos=("percent", "size"),
                            Media=("percent", "mean"),
                            Mejor=("percent", "max"),
                            Último=("percent", "last"))
                        .round(1)
                        .reset_index())
            resumen["Calificación"] = resumen["Media"].map(grade)
            resumen.columns = ["Nivel", "Actividad", "Intentos", "Media %",
                              "Mejor %", "Último %", "Calificación"]
            st.dataframe(resumen, use_container_width=True, hide_index=True)

            # --- Historial reciente ---
            with st.expander("🕒 Historial reciente"):
                rec = f.sort_values("date", ascending=False).head(20).copy()
                rec["date"] = rec["date"].dt.strftime("%Y-%m-%d %H:%M")
                rec["Calificación"] = rec["percent"].map(grade)
                st.dataframe(rec[["date", "level", "skill", "score",
                                  "total", "percent", "Calificación"]],
                            use_container_width=True, hide_index=True)

            # --- Exportar / borrar ---
            st.download_button("⬇️ Descargar historial (CSV)",
                              df.to_csv(index=False).encode("utf-8"),
                              "progreso_ingles.csv", "text/csv")
            if st.checkbox("Quiero borrar todo el historial"):
                if st.button("🗑️ Borrar definitivamente"):
                    clear_progress()
                    st.session_state.history = []
                    st.rerun()

