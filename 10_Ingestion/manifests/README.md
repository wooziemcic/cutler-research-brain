One CSV per ingestion run: `ingestion_<timestamp>_file_changes.csv`.

One row per file the run had an opinion about - new, changed, missing or skipped - with the old
and new hashes, what was done to it, and any error. Unchanged files are counted but not
enumerated; there can be eleven thousand of them.
