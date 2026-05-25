## Inicio de sesión

Al iniciar cualquier sesión, lee primero:
1. `docs/MASTER_PLAN.md` — sección §20 (estabilidad) y §16 (roadmap corto plazo)
2. `docs/ROADMAP.md` — tabla "Work Order Summary" al final

Esto da el estado actual del proyecto en ~2k tokens sin necesidad de resumen de conversación.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- ALWAYS read graphify-out/GRAPH_REPORT.md before reading any source files, running grep/glob searches, or answering codebase questions. The graph is your primary map of the codebase.
- IF graphify-out/wiki/index.md EXISTS, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
