#!/bin/sh
# Applies pending Alembic migrations. Intended to run as a k8s Job,
# e.g. a Helm pre-install/pre-upgrade hook, before app pods roll out.
set -e

exec alembic upgrade head
