#!/usr/bin/env bash
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
set -Eeuo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SRC/connect-web.py" "$@"
