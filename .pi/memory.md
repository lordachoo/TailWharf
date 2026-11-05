# Working preferences (TailWharf)

- NEVER `git push` — commit only; the user pushes manually.
- Production instance runs on v100-ai (tailnet); laptop runs dev instances only.
- User pushes, then pulls/restarts on v100: `git pull && sudo systemctl restart tailnet-dash`.
- cpgw (:8081 v100) is a gateway/health endpoint, NOT a model server —
  model-endpoint UI (⚙ Harness config) must stay gated on real evidence
  (/v1/models answering 200 with models).
- Secrets live only in .env (Crawl4AI key, MCP_TOKEN). Never commit or echo.
