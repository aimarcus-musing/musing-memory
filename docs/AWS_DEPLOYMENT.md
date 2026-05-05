# musing-memory AWS Deployment

This document tracks the AWS deployment story for musing-memory as the
service evolves. Each milestone PR appends/updates the relevant section.
The runbook will be replaced by Terraform/IaC once the architecture
stabilizes; treat this as the authoritative manual reference until then.

> **Status**: Pre-deployment. The service is not yet running in production.
> The first deployable milestone is the v1 service skeleton (Phase 1 of the
> plan in `~/.claude/plans/playful-painting-rainbow.md`). This document
> currently describes the deployment story for the schema-extension PR
> (emotion + recall metadata columns + memory_edges table) — additive,
> backward-compatible, no-data-required.

---

## Schema-extension PR — additive ORM changes

### What this PR ships

- New mixin classes (`EmotionContextMixin`, `RecallMetadataMixin`) applied to
  `EpisodicEvent`, `SemanticMemoryItem`, `ProceduralMemoryItem`,
  `ResourceMemoryItem`, `Block`. Adds nullable columns: `vad`,
  `primary_emotion`, `scenarios`, `linguistic_cues`, `voice_emotion` on all
  five; `g_0`, `consolidation_g_n`, `last_recalled_at`, `recall_count` on
  the three decaying buckets (Episodic, Semantic, Procedural).
- New `memory_edges` table for typed connections between memory rows.
- New enums `MemoryBucket` and `EdgeType`.
- 23 schema-level unit tests; no DB required.

## Bucket consolidation PR — Procedural folded into Semantic

### What this PR ships

- `SemanticMemoryItem` gains two columns:
  - `entry_type VARCHAR(32) NOT NULL DEFAULT 'fact'` — discriminator across
    `'fact' | 'concept' | 'entity' | 'procedure'`.
  - `structured_data JSONB NULL` — holds procedure steps (or future typed
    payloads). Shape for procedures: `{"steps": [{"order": int, "action": str, ...}, ...]}`.
- New pg index `ix_semantic_memory_org_entry_type` on `(organization_id, entry_type)`
  for filtering by entry kind.
- Pydantic schema (`SemanticMemoryItemBase`, `SemanticMemoryItemUpdate`) gains
  the same two fields with a `Literal` validator on `entry_type`.
- `insert_semantic_item` plumbs `entry_type` and `structured_data` through to
  the row.
- Meta-router prompt + Semantic Memory Manager prompt updated: procedure
  content now routes to Semantic with `entry_type='procedure'`. The
  `'procedural'` choice is still legal in `trigger_memory_update`'s tool
  signature but is no longer offered to the LLM in the meta-router prompt.
- `ProceduralMemoryItem` ORM class carries a docstring deprecation banner —
  the class and its table remain so legacy rows keep loading; no new writes
  should target it.
- 7 new schema-level tests guarding the entry_type taxonomy.

### Deploy order

Same envelope as the previous PR: schema-only, additive, safe defaults. On a
greenfield deployment the new columns appear via `Base.metadata.create_all()`
on FastAPI startup. No data migration is needed: existing semantic rows pick
up `entry_type='fact'` via the column default; legacy procedural rows keep
living in `procedural_memory` until a future migration backfills them into
`semantic_memory` with `entry_type='procedure'`.

### Rollback

```sql
-- Drop the bucket-consolidation columns + index
DROP INDEX IF EXISTS ix_semantic_memory_org_entry_type;
ALTER TABLE semantic_memory
    DROP COLUMN IF EXISTS entry_type,
    DROP COLUMN IF EXISTS structured_data;
```

The legacy `procedural_memory` table is untouched by this PR, so reverting
prompts is enough to fully restore the prior bucket layout — no DDL needed
on the procedural side.

### Deploy order

This PR is **schema-only**. There is no service running musing-memory in
production yet, so there's nothing to deploy alongside it. When the v1
service ships, this PR's schema is part of the initial DDL — no separate
migration needed.

When/if a Postgres database with prior MIRIX schema already exists, the
columns are added via `Base.metadata.create_all()` on startup (existing
behavior — see `mirix/server/server.py::ensure_tables_created`). All new
columns are nullable with safe defaults, so the change is non-breaking
for any existing rows.

### Rollback

Manual `ALTER TABLE ... DROP COLUMN ...` for each new column, plus
`DROP TABLE memory_edges`. SQL is included at the bottom of this section.

---

## v1 Service Deployment (planned — not yet shipping)

> This section will be filled in when the v1 service ships. Outline below
> reflects the plan; concrete commands will be added incrementally.

### Infrastructure

| Component | AWS Resource | Sizing (start) | Notes |
|---|---|---|---|
| Application | ECS Fargate or EC2 | 1 vCPU / 2 GB | Runs the FastAPI server |
| Primary DB | RDS PostgreSQL 16 + pgvector | `db.t4g.medium` | `shared_preload_libraries=pgvector` parameter group |
| Cache | ElastiCache Redis | `cache.t4g.micro` | Embedding cache, retrieval rerank cache |
| Secrets | AWS Secrets Manager | — | DB creds, internal API key, Vertex SA JSON |
| Networking | VPC + private subnets | — | Memory svc, RDS, Redis all in private subnets |
| Logs/Metrics | CloudWatch | — | App logs + RDS Performance Insights |

### One-time setup steps (will be Terraform)

1. **VPC & networking**
   - Reuse the musing-sms VPC if same region; otherwise create a new VPC
     with peering. Memory service lives in private subnets; only the
     musing-sms EC2 SG should be allowed inbound on the memory svc SG.

2. **RDS Postgres with pgvector**
   - Create a parameter group based on `default.postgres16` with
     `shared_preload_libraries = pgvector` (or use the AWS-provided
     `postgres16-pgvector` family if available).
   - Create the RDS instance. Enable encryption at rest, automated
     backups, multi-AZ if production.
   - Connect once and run: `CREATE EXTENSION IF NOT EXISTS vector;`
   - SG: allow inbound 5432 from the memory service SG only.

3. **ElastiCache Redis**
   - Single-node `cache.t4g.micro` is fine to start. Encryption in transit.
   - SG: allow inbound 6379 from the memory service SG only.

4. **Secrets Manager**
   - `musing-memory/db-uri` — full Postgres URI
   - `musing-memory/redis-uri`
   - `musing-memory/internal-api-key` — used by musing-sms client
   - `musing-memory/vertex-sa-json` — Google Cloud service account
     credentials for Vertex AI

5. **IAM**
   - Task role (Fargate) or instance role (EC2) with permissions to:
     - Read those four Secrets Manager entries
     - Write logs to CloudWatch
     - (No direct AWS Vertex equivalent — Vertex auth is via the SA JSON
       loaded from Secrets Manager into `GOOGLE_APPLICATION_CREDENTIALS`)

6. **DNS (optional)**
   - `memory.internal.themusing.ai` → service load balancer (private)

### Application deployment

- Build: `docker build -t musing-memory:<sha> .` — Dockerfile already
  in the fork.
- Push to ECR.
- ECS service or EC2 systemd unit pulls and runs.
- Env vars (loaded from Secrets Manager at startup):
  - `MIRIX_PG_URI`
  - `MIRIX_REDIS_HOST` / `MIRIX_REDIS_PORT` / `MIRIX_REDIS_ENABLED=true`
  - `MIRIX_INTERNAL_API_KEY`
  - `GOOGLE_APPLICATION_CREDENTIALS=/secrets/vertex-sa.json` (mounted
     from Secrets Manager at boot)
  - `MIRIX_QUEUE_TYPE=memory` to start; can switch to Kafka later for
     high throughput

### Schema bootstrap

The current MIRIX fork uses `Base.metadata.create_all()` on FastAPI lifespan
startup (no Alembic). This is fine for greenfield deployment. For ongoing
schema changes once the service is in production, **adopt Alembic before
the first prod deployment** — production schema changes via DDL-on-startup
are not safe for any non-trivial migration.

→ **Tech debt**: Add Alembic during v1 service deployment milestone.

### Observability

- CloudWatch log group `/aws/ecs/musing-memory` (or `/aws/ec2/musing-memory`)
- Custom metrics: `memory.ingest.latency_ms`, `memory.query.latency_ms`,
  `memory.embedding_cache.hit_rate`, `memory.bucket_count.<bucket>`
- Dashboards: ingest p50/p95, query p50/p95, recall hits per bucket

### Rollback

- ECS: revert task definition revision; previous image stays in ECR
- RDS schema: see PR-specific rollback SQL below
- Redis: nothing to roll back (cache only)

### Cost estimate (rough, single-region, low traffic)

- RDS `db.t4g.medium` Multi-AZ: ~$120/mo
- ECS Fargate 1 vCPU / 2 GB, 24/7: ~$30/mo
- ElastiCache `cache.t4g.micro`: ~$15/mo
- Vertex AI multimodal embedding: depends on volume; cache hit ratio is
  the main lever
- Total floor: ~$165/mo before traffic-driven Vertex spend

---

## Rollback SQL (this PR)

If we deploy these schema changes and need to revert:

```sql
-- Drop the new memory_edges table
DROP TABLE IF EXISTS memory_edges;

-- Drop emotion + recall columns from each memory table
-- Run inside a transaction; verify counts before committing.
BEGIN;

ALTER TABLE episodic_memory
    DROP COLUMN IF EXISTS vad,
    DROP COLUMN IF EXISTS primary_emotion,
    DROP COLUMN IF EXISTS scenarios,
    DROP COLUMN IF EXISTS linguistic_cues,
    DROP COLUMN IF EXISTS voice_emotion,
    DROP COLUMN IF EXISTS g_0,
    DROP COLUMN IF EXISTS consolidation_g_n,
    DROP COLUMN IF EXISTS last_recalled_at,
    DROP COLUMN IF EXISTS recall_count;

ALTER TABLE semantic_memory
    DROP COLUMN IF EXISTS vad,
    DROP COLUMN IF EXISTS primary_emotion,
    DROP COLUMN IF EXISTS scenarios,
    DROP COLUMN IF EXISTS linguistic_cues,
    DROP COLUMN IF EXISTS voice_emotion,
    DROP COLUMN IF EXISTS g_0,
    DROP COLUMN IF EXISTS consolidation_g_n,
    DROP COLUMN IF EXISTS last_recalled_at,
    DROP COLUMN IF EXISTS recall_count;

ALTER TABLE procedural_memory
    DROP COLUMN IF EXISTS vad,
    DROP COLUMN IF EXISTS primary_emotion,
    DROP COLUMN IF EXISTS scenarios,
    DROP COLUMN IF EXISTS linguistic_cues,
    DROP COLUMN IF EXISTS voice_emotion,
    DROP COLUMN IF EXISTS g_0,
    DROP COLUMN IF EXISTS consolidation_g_n,
    DROP COLUMN IF EXISTS last_recalled_at,
    DROP COLUMN IF EXISTS recall_count;

ALTER TABLE resource_memory
    DROP COLUMN IF EXISTS vad,
    DROP COLUMN IF EXISTS primary_emotion,
    DROP COLUMN IF EXISTS scenarios,
    DROP COLUMN IF EXISTS linguistic_cues,
    DROP COLUMN IF EXISTS voice_emotion;

ALTER TABLE block
    DROP COLUMN IF EXISTS vad,
    DROP COLUMN IF EXISTS primary_emotion,
    DROP COLUMN IF EXISTS scenarios,
    DROP COLUMN IF EXISTS linguistic_cues,
    DROP COLUMN IF EXISTS voice_emotion;

COMMIT;
```

For SQLite (dev), columns can't be dropped easily — easier to wipe the
DB file (`rm musing_memory.db`) since dev data is disposable.
