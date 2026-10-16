"""Guide a staged, manual-assisted weekly refresh; never commit or deploy."""

import argparse
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlencode
import webbrowser

import pandas as pd
from bs4 import BeautifulSoup

from src.collectors import foodbasics, loblaws, metro, nofrills, sobeys, walmart
from src.collectors import walmart_maintenance, nofrills_maintenance

MAINTENANCE = {"walmart": walmart_maintenance, "nofrills": nofrills_maintenance}
from src.collectors.common import DEFAULT_SEARCH_TERMS
from src.integration import integrate_price_frames, save_integration_outputs


ROOT = Path(__file__).resolve().parent
STORES = {
    "walmart": ("Walmart Canada", walmart),
    "nofrills": ("No Frills Canada", nofrills),
    "foodbasics": ("Food Basics Canada", foodbasics),
    "metro": ("Metro Canada", metro),
    "loblaws": ("Loblaws Canada", loblaws),
    "sobeys": ("Sobeys Canada", sobeys),
}
PRODUCTION_FILES = ("all_stores_prices_normalized.csv", "optimizer_ready_prices.csv")
STORE_FILES = {key: f"{key}_prices_normalized.csv" for key in STORES}
SNAPSHOT_FILES = (*STORE_FILES.values(), *PRODUCTION_FILES)


def window_start(cycle, freshness_window):
    """N calendar dates including the refresh date, never future observations."""
    if freshness_window < 1:
        raise ValueError("--freshness-window must be at least 1 day.")
    return cycle - timedelta(days=freshness_window - 1)


def archive_path(data_dir, cycle, suffix=None):
    if suffix is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", suffix):
        raise ValueError("Archive suffix must contain only letters, digits, '_' or '-'.")
    name = cycle.isoformat() + (f"-{suffix}" if suffix else "")
    return data_dir / "history" / name


def check_archive(data_dir, destination):
    if destination.exists():
        raise FileExistsError(
            f"Archive already exists: {destination}. Nothing overwritten. "
            "For a deliberate retry, use a new --archive-suffix (e.g. retry-1)."
        )
    for filename in SNAPSHOT_FILES:
        if not (data_dir / filename).is_file():
            raise FileNotFoundError(f"Cannot archive missing snapshot: {data_dir / filename}")


def create_archive(data_dir, destination):
    """Copy every current snapshot before collection, with exclusive creation."""
    check_archive(data_dir, destination)
    destination.mkdir(parents=True, exist_ok=False)
    for filename in SNAPSHOT_FILES:
        shutil.copy2(data_dir / filename, destination / filename)
    return destination


def freshness_report(frames, cycle, freshness_window=3):
    """Require all observations inside the cycle window; preserve actual dates."""
    start = window_start(cycle, freshness_window)
    latest, observations, errors, warnings = {}, {}, [], []
    dates_seen = []
    for key, frame in frames.items():
        dates = pd.to_datetime(frame["date_observed"], errors="coerce", utc=True)
        valid = dates.dropna().dt.date
        latest[key] = max(valid).isoformat() if not valid.empty else None
        observations[key] = sorted({day.isoformat() for day in valid})
        dates_seen.extend(valid.tolist())
        if frame.empty or dates.isna().any() or not valid.between(start, cycle).all():
            errors.append(
                f"{STORES[key][0]} is stale, empty, or has invalid/mixed dates "
                f"(latest: {latest[key]}); all observations must be within {start} through {cycle}."
            )
        if set(frame["store"].dropna()) != {STORES[key][0]} or frame["store"].isna().any():
            errors.append(f"{key}: unexpected or missing store identity.")
    if dates_seen and (max(dates_seen) - min(dates_seen)).days > 3:
        warnings.append("Observation dates differ by more than 3 days.")
    return {"latest_by_store": latest, "observation_dates": observations,
            "errors": errors, "warnings": warnings}


def validate_freshness(frames, cycle, freshness_window=3):
    report = freshness_report(frames, cycle, freshness_window)
    for key, newest in report["latest_by_store"].items():
        print(f"  {STORES[key][0]} newest observation: {newest or 'missing'}; "
              f"observed: {', '.join(report['observation_dates'][key]) or 'missing'}")
    for warning in report["warnings"]:
        print(f"WARNING: {warning}")
    if report["errors"]:
        raise ValueError("Freshness validation failed:\n" + "\n".join(report["errors"]))
    return report


def _values(frame, column):
    return frame.get(column, pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()


def _promotion_mask(frame):
    status = _values(frame, "sale_status").str.lower()
    return ~status.isin({"", "none", "false", "regular", "nan", "0"}) | _values(
        frame, "promotion_text"
    ).ne("")


def _multi_buy_mask(frame):
    return (
        _values(frame, "multi_buy_quantity").ne("")
        | _values(frame, "multi_buy_unit_price").ne("")
        | _values(frame, "sale_status").str.lower().str.contains(r"multi[- ]?buy", regex=True)
    )


def quality_metrics(frame):
    integrated = integrate_price_frames([frame])
    availability = _values(frame, "availability").replace("", "unknown")
    return {
        "rows": len(frame),
        "optimizer_ready": len(integrated.optimizer_ready),
        "latest_observation": str(frame["date_observed"].max()) if len(frame) else None,
        "sale_promo_rows": int(_promotion_mask(frame).sum()),
        "multi_buy_rows": int(_multi_buy_mask(frame).sum()),
        "normalization_errors": int(_values(frame, "normalization_error").ne("").sum()),
        "warnings": int(_values(frame, "data_quality_warning").ne("").sum()),
        "availability": {str(k): int(v) for k, v in availability.value_counts().items()},
    }


def _product_index(frame):
    """Compare stable identities, collapsing cross-search duplicates, not titles."""
    indexed, unkeyed = {}, 0
    for row in frame.fillna("").sort_values(
        ["date_observed", "product_title"], kind="stable"
    ).to_dict("records"):
        identity = str(row.get("product_url", "")).strip()
        kind = "url"
        if not identity:
            identity, kind = str(row.get("product_id", "")).strip(), "id"
        if not identity:
            unkeyed += 1
            continue
        indexed[(row["store"], kind, identity)] = row
    return indexed, unkeyed


def detect_changes(previous, current):
    """Snapshot differences, not claims about catalogue additions or inflation."""
    old, old_unkeyed = _product_index(previous)
    new, new_unkeyed = _product_index(current)
    report = {name: [] for name in (
        "new_products", "disappeared_products", "price_increases", "price_decreases",
        "sales_starting", "sales_ending", "multi_buy_changes", "availability_changes",
        "package_changes",
        "average_package_changes", "unit_average_discrepancies", "conditional_offer_changes",
    )}

    def details(row):
        return {key: row.get(key, "") for key in (
            "store", "product_title", "product_url", "product_id"
        )}

    report["new_products"] = [details(new[key]) for key in sorted(new.keys() - old.keys())]
    report["disappeared_products"] = [details(old[key]) for key in sorted(old.keys() - new.keys())]
    for key in sorted(old.keys() & new.keys()):
        before, after = old[key], new[key]
        info = details(after)
        package_fields = ("package_size", "package_unit", "package_size_text", "is_variable_weight")
        same_package = all(before.get(f, "") == after.get(f, "") for f in package_fields)
        if not same_package:
            report["package_changes"].append(info)
        # Compare like-for-like exposed prices only. Missing/new values are not zero.
        variable = any(str(r.get("is_variable_weight", "")).lower() == "true" for r in (before, after))
        if variable:
            a, b = pd.to_numeric([before.get("displayed_price"), after.get("displayed_price")], errors="coerce")
            if pd.notna(a) and pd.notna(b) and a != b:
                change = {**info, "before": float(a), "after": float(b)}
                report["average_package_changes"].append(change)
                if (before.get("listed_unit") == after.get("listed_unit")
                        and before.get("listed_unit_price") == after.get("listed_unit_price")
                        and after.get("listed_unit_price") not in (None, "")):
                    report["unit_average_discrepancies"].append(change)
        for price_field, compatible in (
            ("displayed_price", same_package and not variable),
            ("listed_unit_price", before.get("listed_unit", "") == after.get("listed_unit", "")
             and bool(after.get("listed_unit", ""))),
        ):
            a, b = pd.to_numeric([before.get(price_field), after.get(price_field)], errors="coerce")
            conditional = any(r.get("conditional_offer_terms") not in (None, "") for r in (before, after))
            if compatible and not conditional and pd.notna(a) and pd.notna(b) and a != b:
                report["price_increases" if b > a else "price_decreases"].append(
                    {**info, "field": price_field, "before": float(a), "after": float(b)}
                )
        was_sale = bool(_promotion_mask(pd.DataFrame([before])).iloc[0])
        is_sale = bool(_promotion_mask(pd.DataFrame([after])).iloc[0])
        if was_sale != is_sale:
            report["sales_starting" if is_sale else "sales_ending"].append(info)
        has_multi_buy = bool(_multi_buy_mask(pd.DataFrame([before, after])).any())
        for name, fields in (
            ("multi_buy_changes", ("multi_buy_quantity", "multi_buy_unit_price", "single_item_price",
                                   "sale_status", "promotion_text")),
            ("availability_changes", ("availability",)),
            ("conditional_offer_changes", ("conditional_offer_terms", "purchase_limit", "after_limit_price")),
        ):
            if name == "multi_buy_changes" and not has_multi_buy:
                continue
            a = {f: before.get(f, "") for f in fields}
            b = {f: after.get(f, "") for f in fields}
            if a != b:
                report[name].append({**info, "before": a, "after": b})
    report["unkeyed_rows"] = {"previous": old_unkeyed, "current": new_unkeyed}
    return report


class CaptureNotReady(ValueError):
    """An expected, actionable capture-readiness problem (not a filesystem dump)."""


def search_url(key, term):
    module = STORES[key][1]
    base = getattr(module, f"{key.upper()}_SEARCH_URL")
    if key == "sobeys":
        params = {"query": term, "tab": "products"}
    elif key in {"nofrills", "loblaws"}:
        params = {"search-bar": term}
    else:
        params = {"filter": term}
    return base + "?" + urlencode(params)


def _read_json(path):
    if not path.is_file():
        raise CaptureNotReady(f"{path.name} has not been created yet; run the guided refresh.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise CaptureNotReady(f"{path.name} is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise CaptureNotReady(f"{path.name} must contain a JSON object.")
    return value


def _capture_date(value, cycle, freshness_window):
    start = window_start(cycle, freshness_window)
    try:
        observed = date.fromisoformat(str(value))
    except ValueError as exc:
        raise CaptureNotReady("Capture has no valid observation date; recapture with the guide.") from exc
    if not start <= observed <= cycle:
        raise CaptureNotReady(
            f"Observed {observed}, outside {start} through {cycle}; recapture, do not relabel old data."
        )
    return observed.isoformat()


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_capture(key, folder, term, cycle, freshness_window=3):
    """Read per-search manifests or legacy HTML/JSON without writing or redating."""
    manifest = folder / "metadata.json"
    metadata = _read_json(manifest) if manifest.is_file() else {}
    if "searches" in metadata:
        entry = metadata["searches"].get(term)
        if not entry:
            raise CaptureNotReady(f"{term}: no verified capture yet.")
        filename = entry["filename"]
        if Path(filename).name != filename:
            raise CaptureNotReady("Capture filename must stay inside its store directory.")
        path = folder / filename
    elif key in {"loblaws", "sobeys"} and (folder / "capture.json").is_file():
        path = folder / "capture.json"
        entry = _read_json(path)
    else:
        path = folder / (term.replace(" ", "_") + ".html")
        entry = metadata
    if not path.is_file():
        raise CaptureNotReady(f"{path.name} has not been saved yet.")
    observed = _capture_date(entry.get("date_observed", entry.get("observed_date")), cycle, freshness_window)
    digest = _hash(path)
    if entry.get("sha256") and digest != entry["sha256"]:
        raise CaptureNotReady(f"{path.name} changed since verification; verify it again with the guide.")
    context = entry.get("store_context") or metadata.get("store_context")
    if path.suffix == ".json":
        capture = _read_json(path)
        if not capture.get("searches", {}).get(term):
            raise CaptureNotReady(f"{path.name}: missing/empty search {term!r}.")
        context = context or capture.get("store_context")
        content = json.dumps({"records": sobeys.capture_records(capture, term)}) if key == "sobeys" else json.dumps(
            {"productTiles": capture["searches"][term]}
        )
    else:
        content = path.read_text(encoding="utf-8")
    return {"content": content, "date_observed": observed, "store_context": context,
            "filename": path.name, "sha256": digest, "source_url": search_url(key, term)}


def validate_capture(key, term, capture):
    kwargs = {"observed_date": capture["date_observed"]}
    if key == "sobeys":
        kwargs["store_context"] = capture.get("store_context")
    rows = STORES[key][1].parse_search_results(capture["content"], term, **kwargs)
    if not rows or not any(row.get("product_title") for row in rows):
        raise CaptureNotReady(
            "No search product records found. The page may be blocked, empty, or not fully rendered. "
            "Wait for product cards and copy the rendered HTML using the instructions; do not bypass blocks."
        )
    return len(rows)


def _infer_context(key, content):
    # Only a known, exposed selected-store control, never a product-title guess.
    if key == "sobeys" and content.lstrip().startswith("<"):
        labels = {re.split(r"\s+(?:Open|Closed|Opens|Closes)\b", node["aria-label"], maxsplit=1)[0].strip()
                  for node in BeautifulSoup(content, "html.parser").select("button[aria-label]")
                  if re.match(r"^Sobeys .+\s+(?:Open|Closed|Opens|Closes)\b", node["aria-label"])}
        if len(labels) == 1:
            return labels.pop()
    return None


def capture_instructions(key, folder):
    print(f"\nRefreshing {STORES[key][0]}\nCapture folder: {folder} (created automatically)")
    print("Use the same GTA store for all seven searches. Wait for product names AND prices.\n"
          "Keep the default results, including unsupported/out-of-stock products. Do not bypass blocks.\n"
          "Capture in Chrome/Edge (no console code or extension needed):\n"
          "  1. Right-click the page > Inspect. In Elements, select the top <html> node.\n"
          "  2. Right-click that node > Copy > Copy outerHTML. This copies the rendered page.\n"
          "  3. Paste into a plain-text editor (TextEdit: Format > Make Plain Text) and save\n"
          "     as UTF-8 to the exact .html filename below, not .html.txt.\n"
          "Do not use View Source, PDF, or a screenshot. Keep captures local; full HTML can contain session details.")
    requirements = {
        "nofrills": "embedded script#__NEXT_DATA__ search productTiles",
        "loblaws": "embedded script#__NEXT_DATA__ search productTiles",
        "foodbasics": "rendered product tiles with their data attributes",
        "metro": ".searchOnlineResults grid and its product tiles",
        "sobeys": "rendered div[data-id] product cards, prices, availability and selected-store header",
    }
    print(f"This retailer's existing parser needs: {requirements[key]}.")


def _open_search(url, opener):
    try:
        if opener(url):
            return
    except (OSError, webbrowser.Error):
        pass
    print("Could not open a browser automatically. Open the printed URL manually.")


def guide_captures(key, folder, cycle, *, freshness_window=3, input_fn=input,
                   open_browser=False, opener=None):
    """Validate each search before advancing and journal its actual capture date."""
    opener = opener or webbrowser.open_new_tab
    folder.mkdir(parents=True, exist_ok=True)
    capture_instructions(key, folder)
    # Cache legacy descriptors before upgrading the manifest, preserving their dates.
    saved = {}
    for term in DEFAULT_SEARCH_TERMS:
        try:
            saved[term] = read_capture(key, folder, term, cycle, freshness_window)
        except (CaptureNotReady, OSError, ValueError, KeyError, TypeError):
            pass
    entries, context = {}, None
    for index, term in enumerate(DEFAULT_SEARCH_TERMS, 1):
        url = search_url(key, term)
        target = folder / (term.replace(" ", "_") + ".html")
        print(f"\n[{index}/7] {term}\nOpen: {url}\nSave as: {target}")
        capture = saved.get(term)
        if capture:
            try:
                validate_capture(key, term, capture)
                answer = str(input_fn(f"Verified capture from {capture['date_observed']} exists. Reuse without redating? [Y/n/q]: ") or "").lower()
                if answer == "q":
                    raise KeyboardInterrupt
                if answer not in {"", "y", "yes"}:
                    capture = None
            except (CaptureNotReady, ValueError, KeyError, TypeError):
                capture = None
        if capture is None:
            if open_browser:
                _open_search(url, opener)
            original = (target.stat().st_mtime_ns, _hash(target)) if target.is_file() else None
            while True:
                answer = str(input_fn("After saving a fresh page: Enter=validate, o=open URL, q=cancel: ") or "").strip().lower()
                if answer == "q":
                    raise KeyboardInterrupt
                if answer == "o":
                    _open_search(url, opener)
                    continue
                try:
                    if not target.is_file():
                        raise CaptureNotReady(f"{target.name} has not been saved yet. Use the exact path above.")
                    if original == (target.stat().st_mtime_ns, _hash(target)):
                        raise CaptureNotReady("File is unchanged from before this capture step. Save a newly captured page; old data cannot be redated.")
                    capture = {"content": target.read_text(encoding="utf-8"), "date_observed": cycle.isoformat(),
                               "filename": target.name, "sha256": _hash(target), "source_url": url}
                    validate_capture(key, term, capture)
                    break
                except (CaptureNotReady, OSError, ValueError, KeyError, TypeError) as exc:
                    print(f"Not ready: {exc}\nPlease correct this capture; staying on {term}.")
        inferred = _infer_context(key, capture["content"]) or capture.get("store_context")
        if inferred and context and inferred != context:
            raise CaptureNotReady(f"Store context changed from {context!r} to {inferred!r}; use one store and retry.")
        context = inferred or context
        if not context:
            while not context:
                context = str(input_fn("Store name/location shown in your browser (not inferable from this file; q=cancel): ") or "").strip()
                if context.lower() == "q":
                    raise KeyboardInterrupt
        capture["store_context"] = context
        count = validate_capture(key, term, capture)
        entries[term] = {field: value for field, value in capture.items() if field != "content"}
        # Keep unvisited legacy captures with their original dates, not the newest date.
        pending = {term: {field: value for field, value in item.items() if field != "content"}
                   for term, item in saved.items() if term not in entries}
        manifest = {"store_context": context, "searches": {**pending, **entries}}
        temporary = folder / "metadata.json.tmp"
        temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, folder / "metadata.json")
        print(f"OK: {count} product records; observed {capture['date_observed']}. Metadata saved.")


def collect_store(key, capture_root, cycle, *, prompt=True, input_fn=input,
                  freshness_window=3, open_browser=False, opener=None, previous_rows=None):
    """Delegate pricing/classification unchanged; browser stores never use HTTP."""
    if key in MAINTENANCE:
        maintenance = MAINTENANCE[key]
        if previous_rows is None:
            raise ValueError(f"{key} maintenance requires the existing snapshot rows.")
        folder = capture_root / key
        if prompt:
            maintenance.guide_captures(
                previous_rows, folder, cycle, freshness_window=freshness_window,
                input_fn=input_fn, open_browser=open_browser, opener=opener)
        return maintenance.refresh_rows(previous_rows, folder, cycle, freshness_window)
    collector = getattr(STORES[key][1], f"collect_{key}_listings")
    folder = capture_root / key
    if prompt:
        guide_captures(key, folder, cycle, freshness_window=freshness_window,
                       input_fn=input_fn, open_browser=open_browser, opener=opener)
    rows = []
    for term in DEFAULT_SEARCH_TERMS:
        capture = read_capture(key, folder, term, cycle, freshness_window)
        validate_capture(key, term, capture)
        kwargs = {}
        if key == "sobeys":
            if not capture.get("store_context"):
                raise CaptureNotReady("Sobeys capture has no store_context; run the guided refresh.")
            kwargs["store_context"] = capture["store_context"]
        rows.extend(collector(search_terms=[term], observed_date=capture["date_observed"],
                              delay_seconds=0, fetcher=lambda _: capture["content"], **kwargs))
    return rows


def _print_summary(summary):
    print("\nQuality summary (raw store row counts; ready counts are deduplicated):")
    for key, metrics in summary["stores"].items():
        print(f"  {STORES[key][0]}: {json.dumps(metrics, sort_keys=True)}")
    print("Totals: " + json.dumps(summary["totals"], sort_keys=True))
    print("Snapshot changes (search churn is informational; prices count changed fields):")
    for key, changes in summary["changes"].items():
        counts = {name: len(rows) for name, rows in changes.items() if isinstance(rows, list)}
        print(f"  {STORES[key][0]}: {json.dumps(counts, sort_keys=True)}")


def dry_run(data_dir, capture_root, cycle, selected, rebuild, destination, freshness_window=3):
    """Offline readiness check: no prompts, requests, archives, or CSV writes."""
    print(f"DRY RUN: would archive all eight CSVs to {destination}")
    print(f"Would refresh {', '.join(selected)}; rebuild production: {rebuild}.")
    check_archive(data_dir, destination)
    frames = {key: pd.read_csv(data_dir / filename) for key, filename in STORE_FILES.items()}
    for key, metrics in ((key, quality_metrics(frames[key])) for key in selected):
        print(f"Current {STORES[key][0]}: {json.dumps(metrics, sort_keys=True)}")
    print("Current snapshot dates (not a prediction of successful collection):")
    current = freshness_report(frames, cycle, freshness_window)
    for key, newest in current["latest_by_store"].items():
        print(f"  {STORES[key][0]}: latest {newest}; observations "
              f"{', '.join(current['observation_dates'][key])}; "
              f"{'within window' if not freshness_report({key: frames[key]}, cycle, freshness_window)['errors'] else 'needs refresh'}")
    for warning in current["warnings"]:
        print(f"WARNING: {warning}")
    errors = []
    for key in selected:
        if key in MAINTENANCE:
            maintenance = MAINTENANCE[key]
            rows = maintenance.refresh_rows(
                frames[key].astype(object).where(pd.notna(frames[key]), None).to_dict("records"),
                capture_root / key, cycle, freshness_window)
            summary = maintenance.verification_summary(rows)
            print(f"{key}: browser product maintenance, {maintenance.STORE_CONTEXT} {maintenance.STORE_ID}; {summary['verified']}/{summary['total']} audit rows ready.")
            for failure in summary["failures"]:
                print(f"  Not ready: {failure['product_url']}: {failure['reason']}")
            if summary["failed"]:
                errors.append(f"{key}: not ready ({summary['failed']} unverified product captures).")
            else:
                frames[key] = pd.DataFrame(rows)
            continue
        folder = capture_root / key
        print(f"\n{STORES[key][0]} captures (the normal run creates folders and metadata):")
        not_ready = []
        for term in DEFAULT_SEARCH_TERMS:
            print(f"  {term}: {search_url(key, term)}")
            print(f"    Destination: {folder / (term.replace(' ', '_') + '.html')}")
            try:
                capture = read_capture(key, folder, term, cycle, freshness_window)
                count = validate_capture(key, term, capture)
                print(f"    Ready: {count} records, observed {capture['date_observed']}")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                reason = f"Cannot read {getattr(exc, 'filename', 'capture')}" if isinstance(exc, OSError) else str(exc)
                print(f"    Not ready: {reason}")
                not_ready.append(term)
        if not_ready:
            errors.append(f"{key}: not ready ({len(not_ready)}/7 captures need attention). Run the guided refresh.")
            continue
        try:
            frames[key] = pd.DataFrame(collect_store(key, capture_root, cycle, prompt=False,
                                                    freshness_window=freshness_window))
            validate_freshness({key: frames[key]}, cycle, freshness_window)
            metrics = quality_metrics(frames[key])
            print(f"Capture quality: {json.dumps(metrics, sort_keys=True)}")
            if metrics["optimizer_ready"] == 0:
                raise ValueError("No optimizer-ready products; publication would be refused.")
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            errors.append(f"{key}: {exc}")
    if rebuild:
        # Unselected stores cannot become fresh as a side effect of a single-store refresh.
        errors.extend(freshness_report(
            {key: frame for key, frame in frames.items() if key not in selected}, cycle, freshness_window
        )["errors"])
        integrate_price_frames(list(frames.values()))
    if errors:
        raise CaptureNotReady("Dry-run readiness issues — NOT READY (nothing written):\n" + "\n".join(errors))
    print("Offline checks passed. Freshness/collection will be revalidated before publication.")


def _publish(staged_paths, data_dir, archive):
    """Replace only validated files; restore replaced files on a handled failure."""
    replaced = []
    try:
        for staged in staged_paths:
            target = data_dir / staged.name
            # Include the target before replacement so interrupts also restore it.
            replaced.append(target)
            os.replace(staged, target)
    except BaseException:
        for target in replaced:
            restore = staged_paths[0].parent / ("restore-" + target.name)
            shutil.copy2(archive / target.name, restore)
            os.replace(restore, target)
        raise


def run_refresh(*, root=ROOT, store=None, rebuild=False, dry=False,
                capture_root=None, archive_suffix=None, input_fn=input, today=None,
                freshness_window=3, open_browser=False, opener=None):
    cycle = today or date.today()
    start = window_start(cycle, freshness_window)
    data_dir = Path(root) / "data"
    capture_root = Path(capture_root) if capture_root else data_dir / "captures" / cycle.isoformat()
    selected = [store] if store else list(STORES)
    rebuild = rebuild or store is None
    destination = archive_path(data_dir, cycle, archive_suffix)
    print(f"Refresh cycle: {cycle}. Accepted observation dates: {start} through {cycle} (inclusive).\n"
          f"Capture root: {capture_root}")
    lock = data_dir / ".refresh.lock"
    if lock.exists():
        raise RuntimeError(f"Refresh lock exists: {lock}. Check for a running/interrupted refresh before retrying.")
    if dry:
        return dry_run(data_dir, capture_root, cycle, selected, rebuild, destination, freshness_window)
    check_archive(data_dir, destination)
    # Prevent overlapping runs even when different archive suffixes are requested.
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(f"pid={os.getpid()} archive={destination}\n")
    try:
        create_archive(data_dir, destination)
        print(f"Archived all eight snapshots: {destination}")
        if open_browser is None:
            open_browser = str(input_fn("Open each product/search URL in your default browser automatically? [y/N]: ") or "").lower() in {"y", "yes"}
        previous = {key: pd.read_csv(destination / filename) for key, filename in STORE_FILES.items()}
        with tempfile.TemporaryDirectory(prefix=".refresh-stage-", dir=data_dir) as temporary:
            stage = Path(temporary)
            frames, paths = dict(previous), []
            for key in selected:
                print(f"\nCollecting {STORES[key][0]}...")
                listings = collect_store(key, capture_root, cycle, input_fn=input_fn,
                                         freshness_window=freshness_window, open_browser=open_browser,
                                         opener=opener, previous_rows=previous[key].astype(object).where(pd.notna(previous[key]), None).to_dict("records"))
                path = stage / STORE_FILES[key]
                saver = MAINTENANCE.get(key, STORES[key][1])
                saver.save_listings(listings, path)
                frames[key] = pd.read_csv(path)
                if key in MAINTENANCE:
                    verification = saver.verification_summary(listings)
                    print(f"{key} staged verification: " + json.dumps(verification))
                    shutil.copy2(path, destination / f"{key}_staged_review.csv")
                    (destination / f"{key}_verification.json").write_text(
                        json.dumps(verification, indent=2) + "\n", encoding="utf-8")
                    saver.require_verified(listings)
                validate_freshness({key: frames[key]}, cycle, freshness_window)
                metrics = quality_metrics(frames[key])
                if metrics["optimizer_ready"] == 0:
                    raise ValueError(f"{key}: no optimizer-ready products; refusing publication.")
                paths.append(path)
            relevant = frames if rebuild else {key: frames[key] for key in selected}
            freshness = validate_freshness(relevant, cycle, freshness_window)
            result = integrate_price_frames(list(relevant.values()))
            if rebuild:
                paths.extend(save_integration_outputs(result, *(stage / name for name in PRODUCTION_FILES)))
            summary = {
                "cycle": cycle.isoformat(), "production_rebuilt": rebuild,
                "freshness_window_days": freshness_window,
                "observation_dates": freshness["observation_dates"],
                "stores": {key: quality_metrics(frame) for key, frame in relevant.items()},
                "totals": {
                    "scope": "all six stores" if rebuild else "selected store only",
                    "audit_rows": len(result.combined),
                    "optimizer_ready_rows": len(result.optimizer_ready),
                    "duplicates_removed": result.duplicates_removed,
                    "newest_observation": max(freshness["latest_by_store"].values()),
                },
                "changes": {key: detect_changes(previous[key], frames[key]) for key in selected},
            }
            # Persist detailed changes before publishing; a failed report cannot publish data.
            (destination / "refresh_summary.json").write_text(
                json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
            )
            _print_summary(summary)
            _publish(paths, data_dir, destination)
            print(f"Published. Detailed changes: {destination / 'refresh_summary.json'}")
            if not rebuild:
                print("Six-store production files were NOT rebuilt. Use a full refresh, or --rebuild "
                      "only when all six store snapshots are within the refresh window.")
            return summary
    finally:
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="offline checks; no requests, prompts, or writes")
    parser.add_argument("--store", choices=STORES, help="refresh only this store; do not rebuild production by default")
    parser.add_argument("--rebuild", action="store_true", help="also rebuild production in single-store mode; all stores must be fresh")
    parser.add_argument("--capture-root", type=Path, help="default: data/captures/YYYY-MM-DD")
    parser.add_argument("--archive-suffix", help="explicit unique suffix for a same-day retry (never overwrites an archive)")
    parser.add_argument("--freshness-window", type=int, default=3,
                        help="accepted calendar dates including today (default 3; use 1 for today only)")
    parser.add_argument("--open-browser", action="store_true", default=None,
                        help="open new search URLs in the default browser; otherwise ask (never in dry run)")
    args = parser.parse_args(argv)
    try:
        run_refresh(store=args.store, rebuild=args.rebuild, dry=args.dry_run,
                    capture_root=args.capture_root, archive_suffix=args.archive_suffix,
                    freshness_window=args.freshness_window, open_browser=args.open_browser)
    except CaptureNotReady as exc:
        print(f"\nNot ready: {exc}")
        return 1
    except (Exception, KeyboardInterrupt) as exc:
        print(f"\nRefresh stopped: {exc or 'cancelled'}. Collection/validation failures leave "
              "current CSVs unchanged. If publication itself was interrupted, check the archive.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
