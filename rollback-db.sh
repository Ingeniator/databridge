#!/bin/sh
# Reverts the last applied Alembic migration (or to a given revision).
# Intended to run as a k8s Job, e.g. a Helm post-rollback hook, when a
# release rollback needs the schema reverted alongside the app version.
set -e

REVISION="${1:--1}"

exec alembic downgrade "$REVISION"
