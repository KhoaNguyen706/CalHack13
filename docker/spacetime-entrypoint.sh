#!/bin/sh
# Start SpacetimeDB, publish the game module, and hand the publisher's token to the coordinator.
set -eu

rm -f /shared/spacetime_token
spacetime start --listen-addr 0.0.0.0:3000 &
server=$!

until spacetime server ping local >/dev/null 2>&1; do sleep 1; done
spacetime publish threekey --module-path /game/spacetimedb --server local --delete-data=on-conflict --yes

# Only the identity that published may call start_round, so the coordinator uses this token.
spacetime login show --token | grep -o 'eyJ[^ ]*' > /shared/spacetime_token
echo "game module published; coordinator token written"

wait "$server"
