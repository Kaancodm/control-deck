#!/usr/bin/env bash
set -euo pipefail

# Run test clients inside the same disposable CI service as PostgreSQL itself.
: "${POSTGRES_TEST_CONTAINER:?CI PostgreSQL service identifier is required}"
: "${RUNNER_TEMP:?CI temporary directory is required}"
: "${GITHUB_ENV:?CI environment file is required}"
if [[ ! "$POSTGRES_TEST_CONTAINER" =~ ^[a-f0-9]{12,64}$ ]]; then
    exit 2
fi
client_dir=$(mktemp -d "$RUNNER_TEMP/deck-pg-clients.XXXXXX")
for client in pg_dump pg_restore; do
    printf '#!/usr/bin/env bash\nset -euo pipefail\nexec docker exec -i --env PGHOST=localhost --env PGPORT=5432 --env PGUSER --env PGDATABASE --env PGPASSWORD %q %q "$@"\n' \
        "$POSTGRES_TEST_CONTAINER" "$client" > "$client_dir/$client"
    chmod 700 "$client_dir/$client"
done
printf 'DECK_TEST_PG_DUMP=%s/pg_dump\nDECK_TEST_PG_RESTORE=%s/pg_restore\n' \
    "$client_dir" "$client_dir" >> "$GITHUB_ENV"
"$client_dir/pg_dump" --version
"$client_dir/pg_restore" --version
