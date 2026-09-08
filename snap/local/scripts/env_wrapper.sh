#!/bin/bash

# this whole thing is here to export the ARCH variable
# originally from checkbox
# variables exported here are accessible everywhere inside bugit-v2
# specifying them in the pipx version is not necessary
case "$SNAP_ARCH" in
"amd64")
	export ARCH='x86_64-linux-gnu'
	;;
"i386")
	export ARCH='i386-linux-gnu'
	;;
"arm64")
	export ARCH='aarch64-linux-gnu'
	;;
"armhf")
	export ARCH='arm-linux-gnueabihf'
	;;
*)
	echo "Unsupported architecture: $SNAP_ARCH"
	;;
esac


# lets 'snap set bugit jira-server=<your_url>' override the default
# JIRA_SERVER, see snap/hooks/configure
if [ -f "$SNAP_DATA/jira-server-url" ]; then
	export JIRA_SERVER="$(cat "$SNAP_DATA/jira-server-url")"
fi

# lets 'snap set bugit ai-api-key/ai-base-url/ai-model=...' configure the
# (hidden-by-default) AI Log Collector, see snap/hooks/configure and
# src/bugit_v2/utils/ai_config.py
if [ -f "$SNAP_DATA/ai-api-key" ]; then
	export AI_API_KEY="$(cat "$SNAP_DATA/ai-api-key")"
fi
if [ -f "$SNAP_DATA/ai-base-url" ]; then
	export AI_BASE_URL="$(cat "$SNAP_DATA/ai-base-url")"
fi
if [ -f "$SNAP_DATA/ai-model" ]; then
	export AI_MODEL="$(cat "$SNAP_DATA/ai-model")"
fi

# https://github.com/snapcrafters/get-iplayer/blob/candidate/snap/local/scripts/launcher
exec "$@"
