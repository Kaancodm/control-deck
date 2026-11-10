# Connectors

## How tool references work

This plugin is configured for one setup: a self-hosted PostgreSQL database, with work tracked in GitHub issues and pull requests. Skills refer to these tools by name.

## Connectors for this plugin

| Category | Tool in use | Connector | Notes |
|----------|-------------|-----------|-------|
| Data warehouse | PostgreSQL 17 (self-hosted, pgvector) | None configured | No hosted connector reaches a private database. Work from a test database, repository schema sources, or a schema dump. Never the production database. |
| Notebook | Not used | None | Analyses run as plain SQL and Python. |
| Product analytics | Not used | None | Metrics come from PostgreSQL. |
| Project tracker | GitHub issues and pull requests | GitHub | Configured in `.mcp.json`. |
