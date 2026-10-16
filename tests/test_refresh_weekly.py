"""Refresh orchestration tests: temporary snapshots and offline retailer fixtures."""

from datetime import date
import json
from pathlib import Path
import shutil

import pandas as pd
import pytest
import requests

import refresh_weekly as refresh
from src.integration import integrate_price_frames, save_integration_outputs


CYCLE = date(2026, 10, 2)
FIXTURES = Path(__file__).parent / "fixtures"


def row(key="walmart", **updates):
    result = {
        "store": refresh.STORES[key][0], "date_observed": CYCLE.isoformat(),
        "search_term": "apples", "product_url": f"https://example.test/{key}/apples",
        "product_title": "Apples", "comparison_group": "fresh_apples",
        "standardized_unit": "100g", "price_per_standard_unit": 0.50,
        "displayed_price": 5.0, "package_size": 1.0, "package_unit": "kg",
        "normalization_error": None, "data_quality_warning": None,
        "sale_status": None, "multi_buy_quantity": None, "multi_buy_unit_price": None,
        "single_item_price": None, "availability": "in_stock",
        "verification_status": "verified", "verification_error": None,
    }
    return dict(result, **updates)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Tests must never call retailer networks")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(refresh.webbrowser, "open_new_tab", forbidden)


@pytest.fixture
def project(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    frames = []
    for key, (_, module) in refresh.STORES.items():
        module.save_listings([row(key, date_observed="2026-09-20")], data / refresh.STORE_FILES[key])
        frames.append(pd.read_csv(data / refresh.STORE_FILES[key]))
    save_integration_outputs(integrate_price_frames(frames), *(data / f for f in refresh.PRODUCTION_FILES))
    return tmp_path


def contents(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def fake_collection(key, *args, **kwargs):
    return [row(key)]


def test_archive_preserves_all_eight_and_refuses_same_day(project):
    data = project / "data"
    before = contents(data)
    destination = refresh.archive_path(data, CYCLE)
    refresh.create_archive(data, destination)
    assert contents(destination) == before
    with pytest.raises(FileExistsError, match="Archive already exists"):
        refresh.create_archive(data, destination)
    assert contents(destination) == before
    retry = refresh.archive_path(data, CYCLE, "retry-1")
    refresh.create_archive(data, retry)
    assert contents(retry) == before


def test_missing_source_refuses_archive_and_unsafe_suffix(project):
    data = project / "data"
    (data / refresh.PRODUCTION_FILES[0]).unlink()
    destination = refresh.archive_path(data, CYCLE)
    with pytest.raises(FileNotFoundError):
        refresh.create_archive(data, destination)
    assert not destination.exists()
    with pytest.raises(ValueError):
        refresh.archive_path(data, CYCLE, "../../escape")


def test_freshness_current_all_stores():
    frames = {key: pd.DataFrame([row(key)]) for key in refresh.STORES}
    report = refresh.validate_freshness(frames, CYCLE)
    assert not report["errors"]
    assert set(report["latest_by_store"].values()) == {"2026-10-02"}


@pytest.mark.parametrize("observation", ["2026-09-20", "2026-10-03", "bad-date", None])
def test_stale_future_or_invalid_dates_fail(observation):
    frames = {"walmart": pd.DataFrame([row(date_observed=observation)])}
    with pytest.raises(ValueError, match="Freshness validation failed"):
        refresh.validate_freshness(frames, CYCLE)


def test_one_fresh_row_does_not_hide_old_rows_and_date_spread_warns():
    frames = {"walmart": pd.DataFrame([row(), row(date_observed="2026-09-20")])}
    report = refresh.freshness_report(frames, CYCLE)
    assert report["errors"]
    assert report["warnings"] == ["Observation dates differ by more than 3 days."]
    assert report["latest_by_store"]["walmart"] == "2026-10-02"


def test_empty_or_wrong_store_fails_freshness():
    for frame in (pd.DataFrame([row()]).iloc[:0], pd.DataFrame([row("sobeys")])):
        with pytest.raises(ValueError):
            refresh.validate_freshness({"walmart": frame}, CYCLE)


def test_weekly_changes_deduplicate_and_use_stable_urls():
    old = [row(product_url="a", displayed_price=5, sale_status="SALE", availability="in_stock"),
           row(product_url="b", displayed_price=6), row(product_url="gone")]
    new = [row(product_url="a", displayed_price=7, availability="out_of_stock"),
           row(product_url="b", product_title="Renamed Apples", displayed_price=4,
               sale_status="multi-buy", multi_buy_quantity=2, multi_buy_unit_price=3),
           row(product_url="new")]
    result = refresh.detect_changes(pd.DataFrame(old + [old[0]]), pd.DataFrame(new + [new[0]]))
    for name in ("new_products", "disappeared_products", "price_increases", "price_decreases",
                 "sales_starting", "sales_ending", "multi_buy_changes", "availability_changes"):
        assert len(result[name]) == 1, name
    assert result["price_increases"][0]["before"] == 5
    assert result["price_increases"][0]["after"] == 7
    assert result["price_decreases"][0]["product_title"] == "Renamed Apples"


def test_multi_buy_ending_and_quantity_change():
    old = pd.DataFrame([row(multi_buy_quantity=2, multi_buy_unit_price=3)])
    for updates in ({"multi_buy_quantity": 3, "multi_buy_unit_price": 3}, {}):
        result = refresh.detect_changes(old, pd.DataFrame([row(**updates)]))
        assert len(result["multi_buy_changes"]) == 1


def test_ordinary_single_item_price_change_is_not_a_multi_buy_change():
    result = refresh.detect_changes(
        pd.DataFrame([row(single_item_price=5)]),
        pd.DataFrame([row(single_item_price=6, displayed_price=6)]),
    )
    assert len(result["price_increases"]) == 1
    assert not result["multi_buy_changes"]


def test_unknown_quantity_multi_buy_promotion_change_is_reported():
    result = refresh.detect_changes(
        pd.DataFrame([row(sale_status="multi-buy", promotion_text="Special offer")]),
        pd.DataFrame([row()]),
    )
    assert len(result["multi_buy_changes"]) == 1


def test_missing_prices_not_zero_and_package_changes_not_price_inflation():
    old = pd.DataFrame([row(displayed_price=None)])
    assert not refresh.detect_changes(old, pd.DataFrame([row()]))["price_increases"]
    result = refresh.detect_changes(pd.DataFrame([row()]), pd.DataFrame([row(package_size=2, displayed_price=8)]))
    assert not result["price_increases"]
    assert len(result["package_changes"]) == 1


def test_listed_price_comparison_requires_same_unit_and_missing_identity_reported():
    old = pd.DataFrame([row(listed_unit_price=5, listed_unit="kg"), row(product_url=None)])
    new = pd.DataFrame([row(listed_unit_price=6, listed_unit="kg")])
    result = refresh.detect_changes(old, new)
    assert result["price_increases"][0]["field"] == "listed_unit_price"
    assert result["unkeyed_rows"] == {"previous": 1, "current": 0}
    new["listed_unit"] = "lb"
    assert not refresh.detect_changes(old, new)["price_increases"]


def test_quality_counts_reuse_eligibility_rules():
    frame = pd.DataFrame([
        row("sobeys", product_url="a", sale_status="sale", data_quality_warning="retain"),
        row("sobeys", product_url="b", availability="out_of_stock"),
        row("sobeys", product_url="c", comparison_group="unsupported", normalization_error="unit missing"),
        row("sobeys", product_url="d", sale_status="multi-buy", multi_buy_unit_price=3),
    ])
    metrics = refresh.quality_metrics(frame)
    assert metrics["rows"] == 4
    assert metrics["optimizer_ready"] == 2
    assert metrics["sale_promo_rows"] == 2
    assert metrics["multi_buy_rows"] == metrics["warnings"] == metrics["normalization_errors"] == 1
    assert metrics["availability"]["out_of_stock"] == 1


def test_full_refresh_stages_archives_and_rebuilds(project, monkeypatch):
    before = contents(project / "data")
    calls = []
    def collect(key, *args, **kwargs):
        calls.append(key)
        # Even the last collector sees old production/store snapshots.
        for filename in refresh.SNAPSHOT_FILES:
            assert (project / "data" / filename).read_bytes() == before[Path(filename)]
        rows = [row(key)]
        if key == "sobeys":
            rows.append(row(key, product_url="out", availability="out_of_stock"))
        return rows
    monkeypatch.setattr(refresh, "collect_store", collect)
    result = refresh.run_refresh(root=project, today=CYCLE)
    assert calls == list(refresh.STORES)
    assert result["totals"]["audit_rows"] == 7
    assert result["totals"]["optimizer_ready_rows"] == 6
    assert result["stores"]["sobeys"]["availability"]["out_of_stock"] == 1
    ready = pd.read_csv(project / "data" / "optimizer_ready_prices.csv")
    assert "out" not in set(ready.product_url)
    archive = project / "data" / "history" / CYCLE.isoformat()
    assert json.loads((archive / "refresh_summary.json").read_text())["production_rebuilt"]
    for filename in refresh.SNAPSHOT_FILES:
        assert (archive / filename).read_bytes() == before[Path(filename)]
    assert not (project / "data" / ".refresh.lock").exists()


@pytest.mark.parametrize("failure", ["collector", "stale", "empty", "ineligible", "mixed_units"])
def test_failure_keeps_all_current_csvs_unchanged(project, monkeypatch, failure):
    before = contents(project / "data")
    def collect(key, *args, **kwargs):
        if key == "sobeys":
            if failure == "collector":
                raise RuntimeError("capture failed")
            if failure == "stale":
                return [row(key, date_observed="2026-09-20")]
            if failure == "empty":
                return []
            if failure == "ineligible":
                return [row(key, comparison_group="unsupported")]
            if failure == "mixed_units":
                return [row(key, standardized_unit="1unit")]
        return fake_collection(key)
    monkeypatch.setattr(refresh, "collect_store", collect)
    with pytest.raises((RuntimeError, ValueError)):
        refresh.run_refresh(root=project, today=CYCLE)
    for filename in refresh.SNAPSHOT_FILES:
        assert (project / "data" / filename).read_bytes() == before[Path(filename)]
    assert not (project / "data" / ".refresh.lock").exists()


def test_publication_error_restores_previous_files(project, monkeypatch):
    before = contents(project / "data")
    monkeypatch.setattr(refresh, "collect_store", fake_collection)
    real_replace = refresh.os.replace
    calls = 0
    def fail_once(source, target):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated disk error")
        return real_replace(source, target)
    monkeypatch.setattr(refresh.os, "replace", fail_once)
    with pytest.raises(OSError):
        refresh.run_refresh(root=project, today=CYCLE)
    for filename in refresh.SNAPSHOT_FILES:
        assert (project / "data" / filename).read_bytes() == before[Path(filename)]


def test_cancelled_manual_prompt_never_publishes(project, monkeypatch):
    before = contents(project / "data")
    def cancelled(key, *args, **kwargs):
        if key != "walmart":
            raise KeyboardInterrupt
        return fake_collection(key)
    monkeypatch.setattr(refresh, "collect_store", cancelled)
    with pytest.raises(KeyboardInterrupt):
        refresh.run_refresh(root=project, today=CYCLE)
    for filename in refresh.SNAPSHOT_FILES:
        assert (project / "data" / filename).read_bytes() == before[Path(filename)]
    assert not (project / "data" / ".refresh.lock").exists()


@pytest.mark.parametrize("key", ["walmart", "sobeys"])
def test_single_store_never_rebuilds_implicitly(project, monkeypatch, key):
    before = contents(project / "data")
    monkeypatch.setattr(refresh, "collect_store", fake_collection)
    result = refresh.run_refresh(root=project, store=key, today=CYCLE)
    assert not result["production_rebuilt"]
    assert set(result["stores"]) == {key}
    for filename in refresh.SNAPSHOT_FILES:
        if filename != refresh.STORE_FILES[key]:
            assert (project / "data" / filename).read_bytes() == before[Path(filename)]
    assert pd.read_csv(project / "data" / refresh.STORE_FILES[key]).date_observed.iloc[0] == str(CYCLE)


def test_single_store_explicit_rebuild_refuses_stale_other_stores(project, monkeypatch):
    before = contents(project / "data")
    monkeypatch.setattr(refresh, "collect_store", fake_collection)
    with pytest.raises(ValueError, match="Freshness validation"):
        refresh.run_refresh(root=project, store="sobeys", rebuild=True, today=CYCLE)
    for filename in refresh.SNAPSHOT_FILES:
        assert (project / "data" / filename).read_bytes() == before[Path(filename)]


def test_single_store_explicit_rebuild_when_all_fresh(project, monkeypatch):
    for key, (_, module) in refresh.STORES.items():
        module.save_listings([row(key)], project / "data" / refresh.STORE_FILES[key])
    monkeypatch.setattr(refresh, "collect_store", fake_collection)
    result = refresh.run_refresh(root=project, store="sobeys", rebuild=True, today=CYCLE)
    assert result["production_rebuilt"]
    assert len(result["stores"]) == 6


def test_dry_run_walmart_no_network_prompts_or_writes(project, monkeypatch):
    before = contents(project)
    monkeypatch.setattr(refresh, "collect_store", lambda *a, **k: pytest.fail("dry Walmart must not collect"))
    with pytest.raises(ValueError, match="not ready"):
        refresh.run_refresh(root=project, store="walmart", dry=True, today=CYCLE,
                            input_fn=lambda _: pytest.fail("dry run must not prompt"))
    assert contents(project) == before


def test_full_dry_run_missing_captures_reports_all_and_never_writes(project, capsys):
    before = contents(project)
    with pytest.raises(ValueError, match="Dry-run readiness issues") as error:
        refresh.run_refresh(root=project, dry=True, today=CYCLE)
    for key in list(refresh.STORES)[1:]:
        assert key in str(error.value)
    assert "browser product maintenance" in capsys.readouterr().out
    assert contents(project) == before


def save_capture(root, key, observed_date=str(CYCLE)):
    folder = root / key
    folder.mkdir(parents=True)
    if key in {"loblaws", "sobeys"}:
        capture = json.loads((FIXTURES / f"{key}_search_results.json").read_text())
        capture["observed_date"] = observed_date  # Synthetic test date, not a real recapture.
        (folder / "capture.json").write_text(json.dumps(capture))
    else:
        (folder / "metadata.json").write_text(json.dumps({"observed_date": observed_date}))
        for term in refresh.DEFAULT_SEARCH_TERMS:
            shutil.copyfile(FIXTURES / f"{key}_search_results.html", folder / (term.replace(" ", "_") + ".html"))


@pytest.mark.parametrize("key", list(refresh.STORES)[1:])
def test_capture_adapters_use_existing_offline_parsers(tmp_path, key):
    save_capture(tmp_path, key)
    rows = refresh.collect_store(key, tmp_path, CYCLE, prompt=False)
    assert rows
    assert {row["store"] for row in rows} == {refresh.STORES[key][0]}
    assert {row["date_observed"] for row in rows} == {str(CYCLE)}
    if key == "sobeys":
        assert sum(row["availability"] == "out_of_stock" for row in rows) == 5
        paula = next(row for row in rows if row["product_title"] == "Paula Apples Red 3 lb")
        assert paula["package_unit"] == "lb"
        assert paula["normalization_error"] is None
        honeycrisp = next(row for row in rows if "Lil Snappers" in row["product_title"])
        assert honeycrisp["data_quality_warning"]


def test_stale_capture_not_relabelled(tmp_path):
    save_capture(tmp_path, "sobeys", "2026-09-20")
    with pytest.raises(ValueError, match="recapture, do not relabel"):
        refresh.collect_store("sobeys", tmp_path, CYCLE, prompt=False)


def test_dry_run_valid_capture_is_read_only(project):
    captures = project / "data" / "captures" / str(CYCLE)
    save_capture(captures, "sobeys")
    before = contents(project)
    refresh.run_refresh(root=project, store="sobeys", dry=True, today=CYCLE,
                        input_fn=lambda _: pytest.fail("no prompts"))
    assert contents(project) == before


def test_end_to_end_existing_collectors_with_only_offline_inputs(project, monkeypatch):
    captures = project / "data" / "captures" / str(CYCLE)
    for key in list(refresh.STORES)[1:]:
        save_capture(captures, key)
    capture = json.loads((FIXTURES / "walmart_product_page.json").read_text())
    capture["captured_at"] = str(CYCLE) + "T12:00:00Z"
    product = capture["next_data"]["props"]["pageProps"]["initialData"]["data"]["product"]
    walmart_row = row(product_url=capture["final_url"], product_title=product["name"])
    refresh.walmart.save_listings([walmart_row], project / "data" / refresh.STORE_FILES["walmart"])
    (captures / "walmart").mkdir()
    (captures / "walmart" / "10052944.json").write_text(json.dumps(capture))
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        return "GTA test store" if "Store name/location" in prompt else ""
    result = refresh.run_refresh(root=project, today=CYCLE, input_fn=answer)
    assert sum("Reuse without redating" in prompt for prompt in prompts) == 35
    assert result["production_rebuilt"]
    assert all(metrics["optimizer_ready"] > 0 for metrics in result["stores"].values())
    assert result["stores"]["sobeys"]["availability"]["out_of_stock"] == 5
    audit = pd.read_csv(project / "data" / refresh.PRODUCTION_FILES[0])
    ready = pd.read_csv(project / "data" / refresh.PRODUCTION_FILES[1])
    assert len(audit) == result["totals"]["audit_rows"]
    assert len(ready) == result["totals"]["optimizer_ready_rows"]
    assert result["totals"]["duplicates_removed"] > 0
    assert not ready.availability.eq("out_of_stock").any()
    assert len(ready.store.unique()) == 6


def test_missing_search_and_context_rejected(tmp_path):
    save_capture(tmp_path, "sobeys")
    path = tmp_path / "sobeys" / "capture.json"
    capture = json.loads(path.read_text())
    capture.pop("store_context")
    path.write_text(json.dumps(capture))
    with pytest.raises(ValueError, match="store_context"):
        refresh.collect_store("sobeys", tmp_path, CYCLE, prompt=False)
    capture["store_context"] = "Sobeys test store"
    capture["searches"].pop("eggs")
    path.write_text(json.dumps(capture))
    with pytest.raises(ValueError, match="missing/empty search 'eggs'"):
        refresh.collect_store("sobeys", tmp_path, CYCLE, prompt=False)


def test_existing_lock_prevents_collection(project, monkeypatch):
    (project / "data" / ".refresh.lock").write_text("another refresh")
    monkeypatch.setattr(refresh, "collect_store", lambda *a, **k: pytest.fail("locked"))
    with pytest.raises(RuntimeError, match="Refresh lock"):
        refresh.run_refresh(root=project, today=CYCLE)


def test_walmart_delegates_to_product_maintenance(monkeypatch, tmp_path):
    calls = []
    def collect(*args):
        calls.append(args)
        return [row()]
    monkeypatch.setattr(refresh.walmart_maintenance, "refresh_rows", collect)
    refresh.collect_store("walmart", tmp_path, CYCLE, prompt=False, previous_rows=[row()])
    assert calls == [([row()], tmp_path / "walmart", CYCLE, 3)]


def test_failed_walmart_verification_preserves_snapshots_and_stages_review(project, monkeypatch):
    before = contents(project / "data")
    monkeypatch.setattr(refresh, "collect_store", lambda *a, **k: [
        row(date_observed=None, displayed_price=None, price_per_standard_unit=None,
            verification_status="failed", verification_error="Wrong store",
            normalization_error="Unverified: Wrong store")])
    with pytest.raises(ValueError, match="replacement refused"):
        refresh.run_refresh(root=project, store="walmart", today=CYCLE)
    for filename in refresh.SNAPSHOT_FILES:
        assert (project / "data" / filename).read_bytes() == before[Path(filename)]
    archive = project / "data/history" / str(CYCLE)
    assert (archive / "walmart_staged_review.csv").is_file()
    assert json.loads((archive / "walmart_verification.json").read_text())["failed"] == 1


def test_walmart_dry_run_valid_capture_read_only(project, capsys):
    capture = json.loads((FIXTURES / "walmart_product_page.json").read_text())
    capture["captured_at"] = str(CYCLE) + "T12:00:00Z"
    snapshot = project / "data" / refresh.STORE_FILES["walmart"]
    refresh.walmart.save_listings([row(product_url=capture["final_url"])], snapshot)
    folder = project / "data/captures" / str(CYCLE) / "walmart"
    folder.mkdir(parents=True)
    (folder / "10052944.json").write_text(json.dumps(capture))
    before = contents(project)
    refresh.run_refresh(root=project, store="walmart", dry=True, today=CYCLE)
    assert "1/1 captures ready" in capsys.readouterr().out
    assert contents(project) == before


def test_cli_flags_and_failure_exit_code(monkeypatch):
    calls = []
    monkeypatch.setattr(refresh, "run_refresh", lambda **kwargs: calls.append(kwargs))
    assert refresh.main(["--store", "sobeys", "--dry-run", "--rebuild"]) == 0
    assert calls[0]["store"] == "sobeys"
    assert calls[0]["dry"] and calls[0]["rebuild"]
    def failed(**kwargs):
        raise RuntimeError("blocked collection")
    monkeypatch.setattr(refresh, "run_refresh", failed)
    assert refresh.main([]) == 1


@pytest.mark.parametrize("days,observed,accepted", [
    (3, "2026-09-30", True), (3, "2026-10-01", True),
    (3, "2026-09-29", False), (3, "2026-10-03", False),
    (1, "2026-10-01", False), (1, "2026-10-02", True),
    (5, "2026-09-28", True),
])
def test_configurable_window_inclusive_boundaries(days, observed, accepted):
    frames = {"walmart": pd.DataFrame([row(date_observed=observed)])}
    report = refresh.freshness_report(frames, CYCLE, days)
    assert bool(report["errors"]) is not accepted
    assert report["observation_dates"]["walmart"] == [observed]


def test_invalid_window_fails_before_writes(project):
    before = contents(project)
    with pytest.raises(ValueError, match="at least 1"):
        refresh.run_refresh(root=project, today=CYCLE, freshness_window=0)
    assert contents(project) == before


def test_single_store_rebuild_accepts_other_stores_inside_window(project, monkeypatch):
    for key, (_, module) in refresh.STORES.items():
        module.save_listings([row(key, date_observed="2026-09-30")], project / "data" / refresh.STORE_FILES[key])
    monkeypatch.setattr(refresh, "collect_store", fake_collection)
    result = refresh.run_refresh(root=project, store="sobeys", rebuild=True, today=CYCLE)
    assert result["production_rebuilt"]
    assert result["observation_dates"]["walmart"] == ["2026-09-30"]
    assert result["observation_dates"]["sobeys"] == [str(CYCLE)]


def test_urls_follow_existing_collector_parameters_for_all_searches():
    from urllib.parse import parse_qs, urlsplit
    params = {"nofrills": "search-bar", "loblaws": "search-bar", "metro": "filter",
              "foodbasics": "filter", "sobeys": "query"}
    for key, parameter in params.items():
        for term in refresh.DEFAULT_SEARCH_TERMS:
            url = urlsplit(refresh.search_url(key, term))
            assert url.scheme == "https"
            assert parse_qs(url.query)[parameter] == [term]
    assert refresh.search_url("sobeys", "chicken breast") == "https://www.sobeys.com/?query=chicken+breast&tab=products"


def guided_html(key):
    if key == "sobeys":
        return '''<html><button aria-label="Sobeys Urban Fresh Spadina Closed now">Store</button>
        <div data-id="123"><a href="/products/gala-apples" title="Gala Apples"></a>
        <p>$2.99</p><p>1 KG ($0.30 per 100g)</p><p class="card-title">Gala Apples</p>
        <span>Out of Stock</span></div></html>'''
    fixture_key = "nofrills" if key == "loblaws" else key
    return (FIXTURES / f"{fixture_key}_search_results.html").read_text()


@pytest.mark.parametrize("key", list(refresh.STORES)[1:])
def test_guided_html_all_stores_create_metadata_and_validate_each_file(tmp_path, key, capsys):
    folder = tmp_path / key
    prompts, opened = [], []
    saved = 0
    def answer(prompt):
        nonlocal saved
        prompts.append(prompt)
        assert folder.is_dir()  # User never creates the directory.
        if "Store name/location" in prompt:
            return "Toronto test store"
        assert "After saving a fresh page" in prompt
        if saved:
            manifest = json.loads((folder / "metadata.json").read_text())
            assert len(manifest["searches"]) == saved  # Validated before next search.
        filename = refresh.DEFAULT_SEARCH_TERMS[saved].replace(" ", "_") + ".html"
        (folder / filename).write_text(guided_html(key))
        saved += 1
        return ""
    rows = refresh.collect_store(key, tmp_path, CYCLE, input_fn=answer,
                                 open_browser=True, opener=lambda url: opened.append(url) or True)
    assert saved == len(opened) == 7
    assert rows
    manifest = json.loads((folder / "metadata.json").read_text())
    assert set(manifest["searches"]) == set(refresh.DEFAULT_SEARCH_TERMS)
    assert {item["date_observed"] for item in manifest["searches"].values()} == {str(CYCLE)}
    assert all(item["sha256"] for item in manifest["searches"].values())
    if key == "sobeys":
        assert not any("Store name/location" in prompt for prompt in prompts)
        assert {item["store_context"] for item in rows} == {"Sobeys Urban Fresh Spadina"}
        assert {item["availability"] for item in rows} == {"out_of_stock"}
    else:
        assert sum("Store name/location" in prompt for prompt in prompts) == 1
    output = capsys.readouterr().out
    for term in refresh.DEFAULT_SEARCH_TERMS:
        assert refresh.search_url(key, term) in output
        assert str(folder / (term.replace(" ", "_") + ".html")) in output


def test_bad_capture_retries_same_search_before_advancing(tmp_path, capsys):
    folder = tmp_path / "metro"
    attempts = 0
    def answer(prompt):
        nonlocal attempts
        if "Store name/location" in prompt:
            return "Metro Toronto"
        attempts += 1
        if attempts == 1:
            (folder / "chicken_breast.html").write_text("<html>Access denied</html>")
        elif attempts == 2:
            assert not (folder / "metadata.json").exists()
            (folder / "chicken_breast.html").write_text(guided_html("metro"))
        else:
            assert "chicken breast" in json.loads((folder / "metadata.json").read_text())["searches"]
            return "q"
        return ""
    with pytest.raises(KeyboardInterrupt):
        refresh.guide_captures("metro", folder, CYCLE, input_fn=answer)
    assert "staying on chicken breast" in capsys.readouterr().out
    assert attempts == 3


def test_reuse_preserves_actual_dates_and_manifest_hashes(tmp_path):
    save_capture(tmp_path, "sobeys", "2026-09-30")
    before = (tmp_path / "sobeys" / "capture.json").read_bytes()
    rows = refresh.collect_store("sobeys", tmp_path, CYCLE, input_fn=lambda prompt: "")
    assert {item["date_observed"] for item in rows} == {"2026-09-30"}
    assert (tmp_path / "sobeys" / "capture.json").read_bytes() == before
    manifest = json.loads((tmp_path / "sobeys" / "metadata.json").read_text())
    assert {item["date_observed"] for item in manifest["searches"].values()} == {"2026-09-30"}
    assert len(manifest["searches"]) == 7


def test_unmodified_stale_file_cannot_be_automatically_redated(tmp_path, capsys):
    save_capture(tmp_path, "metro", "2026-09-20")
    before = contents(tmp_path)
    answers = iter(["", "q"])
    with pytest.raises(KeyboardInterrupt):
        refresh.guide_captures("metro", tmp_path / "metro", CYCLE, input_fn=lambda _: next(answers))
    assert "old data cannot be redated" in capsys.readouterr().out
    assert contents(tmp_path) == before


def test_hash_mismatch_requires_reverification(tmp_path):
    save_capture(tmp_path, "sobeys")
    refresh.collect_store("sobeys", tmp_path, CYCLE, input_fn=lambda _: "")
    path = tmp_path / "sobeys" / "capture.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(refresh.CaptureNotReady, match="changed since verification"):
        refresh.collect_store("sobeys", tmp_path, CYCLE, prompt=False)


def test_browser_failure_allows_manual_open_and_never_advances_without_capture(tmp_path, capsys):
    answers = iter(["o", "q"])
    opened = []
    with pytest.raises(KeyboardInterrupt):
        refresh.guide_captures("metro", tmp_path / "metro", CYCLE,
                               input_fn=lambda _: next(answers),
                               opener=lambda url: opened.append(url) or False)
    assert opened == [refresh.search_url("metro", "chicken breast")]
    assert "Open the printed URL manually" in capsys.readouterr().out


def test_dry_run_missing_captures_is_friendly_and_browser_flag_has_no_effect(project, capsys):
    before = contents(project)
    with pytest.raises(refresh.CaptureNotReady, match="NOT READY"):
        refresh.run_refresh(root=project, dry=True, today=CYCLE, open_browser=True,
                            input_fn=lambda _: pytest.fail("dry run must not ask"),
                            opener=lambda _: pytest.fail("dry run must not open browsers"))
    output = capsys.readouterr().out
    assert "Not ready:" in output
    assert "Errno" not in output
    assert "Traceback" not in output
    assert contents(project) == before


def test_cli_propagates_new_options(monkeypatch):
    calls = []
    monkeypatch.setattr(refresh, "run_refresh", lambda **kwargs: calls.append(kwargs))
    assert refresh.main(["--freshness-window", "5", "--open-browser"]) == 0
    assert calls[0]["freshness_window"] == 5
    assert calls[0]["open_browser"] is True
