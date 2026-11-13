# PostgreSQL Operations & Data Processing Runbook
**Created By: Zeferino Araiza-Flores**

---

## 1. Access & Connection Strings

### Command Line (`psql`)
For batch jobs or index creation:
```
sudo -u postgres psql
```
and to specify a database to use with psql just append this to the command abouve:
```
-d your_database_name
```

### Connection URIs
For Python ingestion scripts or application connections:
`postgresql://[user]:[password]@[host]:[port]/[database_name]`

Example: `postgresql://postgres:MySecurePass@127.0.0.1:5432/vtti`

---

## 2. Core Configuration Files

Global database settings are managed through configuration files typically located in `/etc/postgresql/18/main/`.

*   **`postgresql.conf`:** The primary configuration file. Use this to establish static baselines for parameters like `shared_buffers` and `max_connections`. 
*   **`pg_hba.conf`:** The host-based authentication file. Modify this to configure client authentication and whitelist external IP addresses (e.g., `host all all 0.0.0.0/0 scram-sha-256`).

---

## 3. Resource Delegation (CPU & RAM)

**Memory Boundary:** Maintain a 10GB–15GB unallocated RAM buffer for the host OS to prevent the Linux Out-Of-Memory (OOM) Killer from terminating the PostgreSQL process.

### Persistent System Changes (`ALTER SYSTEM`)
To establish global baselines across all sessions, apply these changes and reload the daemon:
```sql
ALTER SYSTEM SET max_parallel_workers_per_gather = 24;
ALTER SYSTEM SET work_mem = '2GB';
ALTER SYSTEM SET maintenance_work_mem = '40GB';
```

### Session-Level Overrides (`SET`)
For isolated, high-intensity operations (e.g., a massive index build), apply overrides dynamically within the current `psql` session:
```sql
SET maintenance_work_mem = '40GB';
```

---

## 4. Indexing Strategy

Creating composite B-Tree indexes on massive tables is critical for aligning disk structures with analytical window functions **(C5 and C6 completed with this)**. 

### Why and When to Index
*   **Window Function Alignment:** When queries rely heavily on `OVER (PARTITION BY X ORDER BY Y)`, building a composite index on `(X, Y)` allows PostgreSQL to read the index sequentially. This bypasses the need for CPU workers to execute expensive in-memory sorts on raw data.
*   **Targeted Lookups:** For fast UI retrieval or spot-checking specific slices (e.g., retrieving a single `release_key` or `driver_id`), the leading column in the composite index acts as the primary access path.

**Syntax Example:**
```sql
CREATE INDEX idx_c5_release_time ON c5_table_name (release_key, t_rel_sec);
```

---

## 5. Live Progress Monitoring

Use these internal views in a secondary `psql` session to track execution metrics.

**Monitor Active Queries & CPU Wait Events:**
```sql
SELECT pid, now() - query_start AS running_time, wait_event_type, wait_event, state 
FROM pg_stat_activity 
WHERE state = 'active' AND query ILIKE '%catalog%'\watch 60
```

**Monitor Index Build Progress:**
```sql
SELECT phase, blocks_done, blocks_total, 
       ROUND((blocks_done::numeric / NULLIF(blocks_total, 0)) * 100, 2) AS blocks_pct,
       tuples_done, tuples_total,
       ROUND((tuples_done::numeric / NULLIF(tuples_total, 0)) * 100, 2) AS tuples_pct
FROM pg_stat_progress_create_index\watch 10
```

---

## 6. Daemon Management & Troubleshooting

System-level parameter changes written to `postgresql.conf` or via `ALTER SYSTEM` require daemon interaction to take effect.

### Reload vs. Restart
*   **Reload (Non-Disruptive):** Applies changes to parameters like `work_mem` or `max_parallel_workers_per_gather` without dropping active connections.
    ```bash
    sudo systemctl reload postgresql
    ```
*   **Restart (Disruptive):** Required for core architectural changes (e.g., `shared_buffers`). This will sever all active connections.
    ```bash
    sudo systemctl restart postgresql
    ```

### Troubleshooting
If the daemon fails to start or hangs (often due to misconfigured resource allocations or mount issues), output the tail of the service logs to identify the specific error:
```bash
sudo journalctl -u postgresql -n 50 --no-pager
```

---

## 7. pgAdmin Usage

PgAdmin helps with the following administrative and diagnostic tasks:
*   **Visual Query Plans (EXPLAIN):** Generate execution trees for complex `JOIN` or `WINDOW` operations to verify index utilization before executing them in `psql`.
*   **Connection Tracking:** Monitor the dashboard to track idle connections and ensure Python ingestion modules are gracefully closing sessions.
