# ci-scribe

> Fleet ops para la flota de `llm-dev-core`: audit (lints), evidencia (§10.1) y triaje LLM de regresiones.

**Semana:** 7 · **Core:** `llm-dev-core` 0.7.0

## Problema

La semana 7 nace con 6 consumidores y una promesa de `ARCHITECTURE.md` §10.1: la evidencia
debe ser un **URL**, no un README que la promete. Pero nadie audita los invariantes de §3
(anti-imports, anti-secretos, anti-prompts-inline) ni triajea los `eval-smoke` que se rompen.

## Demo

```bash
# audita repos consumidores con los lints de ci-pack (§3)
uv run ci-scribe audit --repos ../commit-cli ../release-scribe ../sec-check

# regenera la página de evidencia (§10.1) leyendo manifiestos y spans
uv run ci-scribe evidence --repos ../bench-runner ../commit-cli ../release-scribe \
                          ../sec-check ../eval-api --spans ..

# eval del propio prompt (record/replay, test-kit)
uv run ci-scribe eval --replay cassettes/
```

Por HTTP (FastAPI sobre `web-api-base`):

```bash
uv run uvicorn ci_scribe.main:app      # app = create_app(provider=OpenCodeCLI("opencode/big-pickle"))
curl -s localhost:8000/health
curl -s -X POST localhost:8000/fleet/audit  -H 'content-type: application/json' \
     -d '{"repos": ["../commit-cli", "../eval-api"]}'
curl -s -X POST localhost:8000/fleet/triage -H 'content-type: application/json' \
     -d '{"repo": "commit-cli", "report": "{\"total\": 3, \"passed\": 2, \"threshold_ok\": false}"}'
```

## Cómo funciona

- **Audit**: `ci_pack.run_lints` verifica las tres reglas ejecutables de §3 sobre el código
  de cada consumidor y devuelve violaciones; HTTP busca un panel central.
- **Triage**: el prompt `eval-triage-generator` (`/prompts`) recibe el reporte crudo de un
  `eval-smoke` y propone diagnóstico, severidad, acciones y si toca re-baseline — salida
  validada contra `eval-triage-v1` por `schema-validate` (la regla 3 es el gate).
- **Evidencia**: `ci_pack.collect`/`render` convierten `core-consumer.yml`, `prompts/` y los
  spans de `llm-client` en la página `docs/metrics/evidence.html`.

## Arquitectura

```
ci-scribe
├── src/ci_scribe/
│   ├── main.py       # CLI + create_app sobre web-api-base (/fleet/audit, /fleet/triage)
│   └── models.py     # schema eval-triage-v1 (EvalTriage)
├── prompts/eval-triage-generator.md
├── evals/eval-triage-generator.jsonl   # 3 casos congelados (baseline ronda 1)
├── cassettes/                          # tape real grabada con OpenCodeCLI
└── tests/                              # app (stub) + replay determinista (D4)
```

## Recicla de

| Módulo | Para qué |
|---|---|
| `llm-client` | toda llamada (triage) pasa por `client.complete`; span en cada llamada |
| `schema-validate` | la salida del triaje se valida contra `eval-triage-v1` antes de responder |
| `web-api-base` | `/health`, `/llm` y contrato de errores vienen de la base |
| `test-kit` | el prompt nace evaluado: dataset + replay determinista; el `eval-smoke` está en CI |
| `ci-pack` | lints de §3, `collect`/`render` de evidencia y el generador del workflow `eval-smoke` |

## Limitaciones

- El `eval-smoke` en CI es determinista por cassette (sin LLM, D4); el triaje real de una
  regresión requiere correr con proveedor vivo (record) o re-grabar tras un re-baseline.
- `fleet/audit` toma rutas locales: quien lo sirva en red debe apuntar a un checkout de la flota.
- La evidencia agrega costo por repo; la observabilidad fina (por-token, utilidad) es de
  `cost-obs` (sem. 39).

## Roadmap

- [x] Audit de §3 con lints de `ci-pack` (CLI + HTTP)
- [x] Triaje LLM `eval-triage-generator` evaluado (3 casos, baseline congelado)
- [x] Página de evidencia regenerada y servida; `docs/metrics/evidence.html`
- [ ] Re-baseline integrado como operación de la CLI (`--rebaseline` invoca el runbook)
- [x] El `eval-smoke` de todos los consumidores generado por `ci-pack.jobs` (sem. 7, dogfood)