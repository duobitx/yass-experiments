#!/usr/bin/env bash

# Setup IPFS for YASS

set -e
sleep 1 # avoid startup noises

set -x

ipfs bootstrap rm all

# bunch of settings to switch off things which don't work OOTB with a private swarm:

# AutoConf (needs custom URL and static dns/ip)
ipfs config --json AutoConf.Enabled 'false'

# ERROR p2pnode libp2p/transport.go:46 libp2p.ShareTCPListener() is not supported in private networks, please disable Swarm.Transports.Network.Websocket or run with LIBP2P_TCP_MUX=false to make this message go away
ipfs config --json Swarm.Transports.Network.Websocket 'false'

# ERROR cmd/ipfs kubo/daemon.go:446 private networking (swarm.key / LIBP2P_FORCE_PNET) is not compatible with AutoTLS. Set AutoTLS.Enabled=false in config to remove this message.
ipfs config --json AutoTLS.Enabled 'false'

# ERROR cmd/ipfs kubo/daemon.go:432 Private networking (swarm.key / LIBP2P_FORCE_PNET) does not work with public HTTP IPNIs enabled by Routing.Type=auto. Kubo will use Routing.Type=dht instead. Update config to remove this message.
ipfs config --json Routing.Type '"dhtserver"'

# ERROR autoconf config/autoconf_client.go:124 DNS.Resolvers contains 'auto' but AutoConf.Enabled=false
# use only the default OS resolver, no DoH:
ipfs config --json DNS.Resolvers '{}'

# ERROR autoconf config/autoconf_client.go:124 Routing.DelegatedRouters contains 'auto' but AutoConf.Enabled=false
ipfs config --json Routing.DelegatedRouters '[]'

# ERROR autoconf config/autoconf_client.go:124 Ipns.DelegatedPublishers contains 'auto' but AutoConf.Enabled=false
ipfs config --json Ipns.DelegatedPublishers '[]'


##### OPTIONAL / DEBUG settings for reference

## [ -f $IPFS_PATH/config ] || ipfs init --profile flatfs,server,announce-off,autoconf-off
##
## # Storage limit
## ipfs config Datastore.StorageMax 120GB
##
## # optionally append external address to announce
## # ipfs config Addresses.AppendAnnounce --json '["/dns4/a.example.com/tcp/4001", "/dns4/b.example.com/tcp/4002"]'
##
## # Disable MDNS
## ipfs config Discovery.MDNS.Enabled --json false
##
## # Do not filter private addresses in swarm announce
## ipfs config Swarm.AddrFilters --json null
##
## # Do not punch holes in NATs
## ipfs config Swarm.DisableNatPortMap --json true
## ipfs config Swarm.EnableHolePunching --json false
##
## # Disable relay to autoconfigured relays and provide static relays instead
## # ipfs config Swarm.RelayClient.Enabled --json false
## # ipfs config Swarm.RelayClient.StaticRelays --json '["/maddr1", "/maddr2"]'
##
## # Disable AutoNAT
## ipfs config AutoNAT.ServiceMode disabled
##
## # No Peering?
## ipfs config Peering.Peers --json null
##
## # AutoConf?
## ipfs config AutoConf.Enabled --json false
## ipfs config AutoConf.URL "https://example.com/autoconf.json"
##
## # Enable Kubo provide and reprovide systems
## ipfs config Provide.Enabled --json true
##
## # DHT settings
## ipfs config Provide.DHT.Interval --json '"22h"'
## ipfs config Provide.DHT.MaxWorkers --json 3
## ipfs config Provide.DHT.SweepEnabled --json true
## ipfs config Provide.DHT.DedicatedPeriodicWorkers --json 1

