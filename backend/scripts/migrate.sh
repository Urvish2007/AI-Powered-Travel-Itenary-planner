#!/bin/sh
# Apply pending Beanie data migrations, then exit.
#
# Run this BEFORE the new API version starts (compose does it via the `migrate`
# service; CI/CD does it as a deploy step). Beanie records which migrations have
# run, so re-running is a no-op.
#
# Environment:
#   MONGODB_URI              required
#   MONGODB_DB_NAME          default: itinerary_planner
#   MIGRATE_USE_TRANSACTION  default: true. Set to "false" ONLY for a standalone
#                            mongod; replica sets and Atlas support transactions.
set -eu

: "${MONGODB_URI:?MONGODB_URI is required}"
DB_NAME="${MONGODB_DB_NAME:-itinerary_planner}"

if [ "${MIGRATE_USE_TRANSACTION:-true}" = "true" ]; then
  TX_FLAG="--use-transaction"
else
  TX_FLAG="--no-use-transaction"
fi

echo "Applying migrations to database '${DB_NAME}' (${TX_FLAG})"
# Migrations import the app package relative to the working directory.
cd "$(dirname "$0")/.."
exec beanie migrate "${TX_FLAG}" -uri "${MONGODB_URI}" -db "${DB_NAME}" -p migrations
