#!/usr/bin/env bash

NAME="ghcr.io/duobitx/yass-agent-sleep"
docker build -t ${NAME} . && docker push ${NAME}