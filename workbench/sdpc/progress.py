"""Session-aware scan throughput: resumed tiles never inflate the rate."""
def scan_progress(completed, total, initial_completed, elapsed):
    processed = max(0, completed - initial_completed)
    rate = processed / elapsed if elapsed > 0 else 0
    remaining = max(0, total - completed)
    return dict(stage='scan', tiles_completed=completed, tiles_planned=total,
                session_tiles=processed, session_seconds=round(elapsed, 2),
                tiles_per_second=round(rate, 3),
                estimated_remaining_seconds=round(remaining / rate, 1) if rate and processed >= 16 else None,
                estimate_scope='scan_only_excludes_merge_export_review')
