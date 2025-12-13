#!/bin/sh

if [ -z "${SLEEP_TIME}" ]; then
  echo "Warn: Env SLEEP_TIME is not defined" >&2
  SLEEP_TIME="12h"
fi

echo "Sleeping for ${SLEEP_TIME}..."
/bin/sleep "${SLEEP_TIME}"
echo "Wake up."
