#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
VOLUME_NAME="${BOOKS_VOLUME:-book_shelter_bot_books}"
docker volume create "$VOLUME_NAME" >/dev/null
# .part files are incomplete downloads and are skipped.
tar -C "$ROOT/books" -cf - \
  --exclude='*.part' \
  --exclude='.DS_Store' \
  . | docker run --rm -i -v "$VOLUME_NAME":/books alpine tar xf - -C /books
docker run --rm -v "$VOLUME_NAME":/books alpine ls -lh /books
