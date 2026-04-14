# Bluebell DB — Technical Overview

Bluebell DB is a fictional embedded key-value store designed for edge devices with
limited RAM. It was created in 2023 by the fictional Meadow Systems research lab.

## Core features

- **Write-ahead log (WAL):** Every write is recorded in an append-only log before
  being applied to the main store. This guarantees durability: if the process
  crashes mid-write, the WAL can replay the missing operations on restart.

- **Adaptive bloom filters:** Bluebell DB uses per-segment bloom filters to skip
  disk reads for keys that definitely do not exist. The filter size adjusts
  automatically based on the observed false-positive rate.

- **Zero-copy reads:** Callers receive a memory-mapped slice of the underlying
  storage file rather than a copied buffer. This eliminates one allocation per
  read and is the main reason Bluebell DB outperforms competing stores on
  read-heavy workloads.

## Limitations

Bluebell DB does not support transactions spanning multiple keys. Each write is
atomic, but there is no multi-key compare-and-swap. Applications that need
multi-key atomicity must implement their own locking layer on top.

The maximum value size is 64 KB. Larger values must be split by the caller.

## Benchmark results

On a Raspberry Pi 4 with a USB SSD:
- Sequential reads:  180,000 ops/sec
- Random reads:       42,000 ops/sec
- Sequential writes:  95,000 ops/sec

Competing store "PebbleKV" achieved 31,000 random reads/sec on the same hardware,
making Bluebell DB approximately 35% faster for random read workloads.

## Configuration

Bluebell DB is configured via a single TOML file. The most important knobs are:

```toml
[storage]
path = "/var/lib/bluebell"
max_value_bytes = 65536

[wal]
sync_interval_ms = 100   # flush WAL to disk every 100 ms
max_segment_bytes = 8388608  # 8 MB per WAL segment

[bloom]
target_false_positive_rate = 0.01
```

## Getting started

```bash
cargo add bluebell-db   # add to Cargo.toml (fictional Rust crate)
```

```rust
let db = BluebellDb::open("/var/lib/bluebell")?;
db.put(b"hello", b"world")?;
let val = db.get(b"hello")?;  // returns Some(b"world")
```
