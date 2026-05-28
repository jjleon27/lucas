## Inicio de sesión

Al iniciar cualquier sesión, lee en este orden:
1. `docs/SESSION_STATE.md` — si existe, léelo PRIMERO (estado exacto de la sesión anterior)
2. `docs/MASTER_PLAN.md` — sección §20 (estabilidad) y §16 (roadmap corto plazo)
3. `docs/ROADMAP.md` — tabla "Work Order Summary" al final

Esto da el estado actual del proyecto en ~2k tokens sin necesidad de resumen de conversación.

## Gestión de contexto

El hook `Stop` guarda `docs/SESSION_STATE.md` automáticamente tras cada respuesta.

Cuando detectes presión de contexto (cualquiera de estas señales):
- Ves mensajes comprimidos/sumarios en el historial (etiqueta "summary" o "compacted")
- Has hecho >40 tool calls en la sesión
- El sistema te advierte que el contexto está casi lleno

ANTES de responder al usuario, actualiza `docs/SESSION_STATE.md` añadiendo esta sección:

    ## ESTADO MANUAL (alta presión de contexto)
    Tarea en curso: [descripción exacta de lo que se estaba haciendo]
    Próximo paso inmediato: [acción específica y concreta para continuar]
    Bugs pendientes: [lista]
    Archivos clave para continuar: [rutas relevantes]

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- ALWAYS read graphify-out/GRAPH_REPORT.md before reading any source files, running grep/glob searches, or answering codebase questions. The graph is your primary map of the codebase.
- IF graphify-out/wiki/index.md EXISTS, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
