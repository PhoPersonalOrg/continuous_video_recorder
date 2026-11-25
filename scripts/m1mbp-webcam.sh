#!/bin/sh
cd "$(dirname "$0")/.." || exit
uv run m1mbp-webcam "$@"
