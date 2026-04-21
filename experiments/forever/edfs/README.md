# forever-experiment-edfs

Create forever experiment with the EDFS engine:

```
k apply -k experiments/forever/edfs
```

List boostrap EDFS nodes:

``` console
$ k exec -it -n forever-experiment-edfs estrack-cebreros -c edfs-engine-node -- ipfs bootstrap list
/dns/estrack-kiruna/tcp/4001/ipfs/12D3KooWLJFr7nesNtEQrQ9PqyjT8kvxbdUqfF4xwZZKEzJHnEhT
```

List EDFS cluster peers:

``` console
$ k exec -it -n forever-experiment-edfs estrack-cebreros -c edfs-engine-node -- ipfs swarm peers
/ip4/10.244.0.157/tcp/4001/p2p/12D3KooWLdgf5Sweepz3moZGzqzNSQihDroMFZemVAptj5iPLDx1
/ip4/10.244.0.158/tcp/4001/p2p/12D3KooWLSd6gAJW9LEe3qQq8EtsRvKYHCNwCDBH6Fc8ZuGT3MZQ
/ip4/10.244.0.160/tcp/4001/p2p/12D3KooWHQy53etgNJPBcUqMs8ouGEN8eh35uQE7DgZqMEYLaVbh
/ip4/10.244.0.161/tcp/4001/p2p/12D3KooWLUFRHSLRFmtDK1Q2g8pzDfAxP3Re8d7au8GVUmUtCQPJ
/ip4/10.244.0.162/tcp/4001/p2p/12D3KooWJF49BNib1pKwqbyjbSMiyK2mi3zsgjgdXFASS6FNCwDa
/ip4/10.244.0.163/tcp/4001/p2p/12D3KooWLJFr7nesNtEQrQ9PqyjT8kvxbdUqfF4xwZZKEzJHnEhT
/ip4/10.244.0.163/tcp/4001/p2p/12D3KooWLJFr7nesNtEQrQ9PqyjT8kvxbdUqfF4xwZZKEzJHnEhT
/ip4/10.244.0.164/tcp/4001/p2p/12D3KooWFH5NxL6ZVr76ViZjQW2Cv56dthZidd19jd8VJA2BPQ2F
/ip4/10.244.0.165/tcp/4001/p2p/12D3KooWEunkN9vEsTvwoN2JbRN29Ex6AH8HuuLGZBrjvHKRCLNh
/ip4/10.244.0.166/tcp/4001/p2p/12D3KooW9vZ4LL8sNh458s1Za5T1hg4LyJSi5ckW83gGNxKcQVya
```

Simulate that the `estrack-cebreros` satellite took two photos:

```
k exec -it -n forever-experiment-edfs estrack-cebreros -c agent -- touch /mnt/transfer/estrack-cebreros-photo-01.txt
```

```
k exec -it -n forever-experiment-edfs estrack-cebreros -c agent -- touch /mnt/transfer/estrack-cebreros-photo-01.txt
```

List photos taken by satellites:

``` console
$ k exec -it -n forever-experiment-edfs estrack-cebreros -c agent -- ls -lh /mnt/transfer/
total 0
-rw-r--r-- 1 root root 0 Apr 21 16:14 estrack-cebreros-photo-01.txt
-rw-r--r-- 1 root root 0 Apr 21 16:14 estrack-cebreros-photo-02.txt
```

Add photos to the EDFS cluster:

``` console
$ k exec -it -n forever-experiment-edfs estrack-cebreros -c edfs-engine-proxy -- \
  ipfs-cluster-ctl add --replication-min 2 --replication-max 3 /mnt/transfer/estrack-cebreros-photo-01.txt
added QmbFMke1KXqnYyBBWxB74N4c5SBnJMVAiMNRcGu6x1AwQH estrack-cebreros-photo-01.txt
```

``` console
$ k exec -it -n forever-experiment-edfs estrack-cebreros -c edfs-engine-proxy -- \
  ipfs-cluster-ctl add --replication-min 2 --replication-max 3 /mnt/transfer/estrack-cebreros-photo-02.txt
added QmbFMke1KXqnYyBBWxB74N4c5SBnJMVAiMNRcGu6x1AwQH estrack-cebreros-photo-02.txt
```

List photos stored in the EDFS cluster:

``` console
k exec -it -n forever-experiment-edfs estrack-cebreros -c edfs-engine-proxy -- \
  ipfs-cluster-ctl pin ls
QmbFMke1KXqnYyBBWxB74N4c5SBnJMVAiMNRcGu6x1AwQH |  | PIN | Repl. Factor: 2--3 | Allocations: [12D3KooWEHYghCaMtpEMdsaJKYRydsbKiYxcscyqRv7CTWS7V5AQ 12D3KooWJ6tiGs1n68a7Kr3UdBzi3uHDizfxZ9BTTKCDx4qzkpc6 12D3KooWQkR6Ttx8MLSTSW953ZnFbtV9cnmLh1KFL25fD6vuGi72] | Recursive | Metadata: no | Exp: ∞ | Added: 2026-04-21 16:41:22
```

Get photo on a different node than the one that created it:

```
k exec -it -n forever-experiment-edfs yaogan-25c -c edfs-engine-node -- ipfs get QmbFMke1KXqnYyBBWxB74N4c5SBnJMVAiMNRcGu6x1AwQH -o /tmp/estrack-cebreros-photo-02.txt
```

```
k delete -k experiments/forever/edfs
```

``` mermaid

```

## Troubleshooting

```
sudo sysctl -w fs.inotify.max_user_watches=2099999999
sudo sysctl -w fs.inotify.max_user_instances=2099999999
sudo sysctl -w fs.inotify.max_queued_events=2099999999
```
