"""Fail-closed maintenance of existing Walmart URLs from browser PDP captures.

No HTTP client or discovery search is used. Capture only the public product
portion of __NEXT_DATA__, not session data or recommended products.
"""

import argparse
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
from urllib.parse import urlsplit
import webbrowser
from zoneinfo import ZoneInfo

import pandas as pd

from src.collectors.common import QUALITY_OUTPUT_COLUMNS, normalize_listing_with_quality_checks
from src.collectors.common import save_listings as _save_listings
from src.collectors.walmart import extract_package_details, _extract_unit_price


STORE_ID = "1004"
STORE_CONTEXT = "Toronto Stockyards"
EXTRA_COLUMNS = ("availability", "product_id", "store_id", "store_context",
                 "verification_status", "verification_error", "captured_at",
                 "multi_buy_quantity", "multi_buy_unit_price", "single_item_price")

# Can be evaluated read-only in a browser, or wrapped in copy(JSON.stringify(...))
# in DevTools by the operator. No network, cookies, storage, or session inspection.
CAPTURE_EXPRESSION = r"""(() => {
  const node = document.querySelector('script#__NEXT_DATA__');
  const data = node ? JSON.parse(node.textContent).props?.pageProps?.initialData?.data : null;
  const source = data?.product;
  const keys = ['usItemId', 'name', 'canonicalUrl', 'sellerDisplayName', 'sellerId',
    'sellerType', 'offerType', 'availabilityStatus', 'priceInfo', 'fulfillmentSummary'];
  const product = source ? Object.fromEntries(keys.filter(k => k in source).map(k => [k, source[k]])) : null;
  if (product) product.fulfillmentSummary = (source.fulfillmentSummary || []).map(
    f => ({fulfillment: f.fulfillment, storeId: f.storeId}));
  if (product && source.location) product.location = {
    storeIds: source.location.storeIds,
    pickupLocation: {storeId: source.location.pickupLocation?.storeId}
  };
  if (product) product.fulfillmentLabel = (source.fulfillmentLabel || [])
    .filter(f => f.fulfillmentMethod === 'PICKUP')
    .map(f => ({fulfillmentMethod: f.fulfillmentMethod, message: f.message}));
  return {capture_version: 1, captured_at: new Date().toISOString(), final_url: location.href,
    store_label: Array.from(document.querySelectorAll('button')).map(b => b.getAttribute('aria-label') || b.textContent)
      .find(t => /STOCKYARDS.*change store/i.test(t || '')) || null,
    next_data: {props: {pageProps: {initialData: {data: {
      product, idml: {specifications: data?.idml?.specifications || []}
    }}}}}};
})()"""


class VerificationError(ValueError):
    """The current offer cannot safely replace the previous observation."""


def product_id(url):
    parsed = urlsplit(str(url))
    if parsed.scheme != "https" or parsed.hostname not in {"walmart.ca", "www.walmart.ca"}:
        raise VerificationError("Not an HTTPS Walmart Canada product URL.")
    if not parsed.path.startswith("/en/ip/"):
        raise VerificationError("Not a Walmart product page (possibly blocked/redirected).")
    identifier = parsed.path.rstrip("/").split("/")[-1]
    if not identifier.isalnum():
        raise VerificationError("Invalid product identifier.")
    return identifier


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VerificationError("Missing or invalid current price.")
    if not math.isfinite(value) or value < 0:
        raise VerificationError("Missing or invalid current price.")
    return float(value)


def parse_product_capture(capture, previous, cycle, freshness_window=3):
    """Verify identity/context/offer first, then use existing normalization."""
    expected = product_id(previous["product_url"])
    if capture.get("capture_version") != 1:
        raise VerificationError("Missing browser capture envelope.")
    if product_id(capture.get("final_url")) != expected:
        raise VerificationError("Redirected to a different product.")
    try:
        timestamp = datetime.fromisoformat(capture["captured_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("timezone missing")
        observed = timestamp.astimezone(ZoneInfo("America/Toronto")).date()
        if freshness_window < 1 or not cycle - timedelta(days=freshness_window - 1) <= observed <= cycle:
            raise ValueError("outside refresh window")
        data = capture["next_data"]["props"]["pageProps"]["initialData"]["data"]
        p = data["product"]
        if not isinstance(p, dict):
            raise ValueError("missing primary product")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise VerificationError("Missing primary __NEXT_DATA__ product or invalid capture date.") from exc
    if str(p.get("usItemId")) != expected:
        raise VerificationError("Primary product identity does not match requested URL.")
    canonical = p.get("canonicalUrl")
    if canonical and product_id("https://www.walmart.ca" + canonical if canonical.startswith("/") else canonical) != expected:
        raise VerificationError("Canonical URL identifies another product.")
    if not (p.get("sellerDisplayName") == "Walmart" and str(p.get("sellerId")) == "0"
            and p.get("sellerType") == "INTERNAL" and p.get("offerType") == "1P"):
        raise VerificationError("Walmart first-party seller not verified.")
    availability = {"IN_STOCK": "in_stock", "OUT_OF_STOCK": "out_of_stock"}.get(p.get("availabilityStatus"))
    if availability is None:
        raise VerificationError("Availability is unknown.")
    store_ids = {str(f.get("storeId")) for f in p.get("fulfillmentSummary", [])
                 if f.get("fulfillment") in {"PICKUP", "DELIVERY"}}
    # Out-of-stock pages omit fulfillment options but still expose the selected
    # location and a product-specific "Pickup not available at ..." message.
    location = p.get("location") or {}
    location_ids = {str(s) for s in location.get("storeIds", [])}
    pickup_id = (location.get("pickupLocation") or {}).get("storeId")
    if pickup_id is not None:
        location_ids.add(str(pickup_id))
    if location_ids and location_ids != {STORE_ID}:
        raise VerificationError("Product location contradicts Stockyards store 1004.")
    unavailable_at_stockyards = any(
        f.get("fulfillmentMethod") == "PICKUP"
        and "PICKUP NOT AVAILABLE AT TORONTO STOCKYARDS" in str(f.get("message", "")).upper()
        for f in p.get("fulfillmentLabel", [])
    )
    oos_context = availability == "out_of_stock" and not store_ids and location_ids == {STORE_ID} and unavailable_at_stockyards
    if oos_context:
        store_ids = location_ids
    if store_ids != {STORE_ID}:
        raise VerificationError(f"Store context must be Stockyards {STORE_ID}; found {sorted(store_ids)}.")
    if "TORONTO STOCKYARDS" not in str(capture.get("store_label", "")).upper() and not oos_context:
        raise VerificationError("Visible Stockyards store context is missing.")
    info = p.get("priceInfo") or {}
    current = info.get("currentPrice") or {}
    if current.get("currencyUnit") != "CAD":
        raise VerificationError("Current CAD offer price is missing.")
    price = _money(current.get("price"))
    codes = info.get("priceDisplayCodes") or {}
    # Missing flags are not evidence that an old promotion ended.
    required = {"rollback", "clearance", "reducedPrice", "finalCostByWeight"}
    if not required.issubset(codes) or not isinstance(info.get("isPriceReduced"), bool) or "wasPrice" not in info:
        raise VerificationError("Promotion/weight status is incomplete; cannot clear previous promotion.")
    if info.get("volumePriceTiers") or info.get("subscriptionPrice"):
        raise VerificationError("Conditional offer structure requires review; not an unconditional price.")
    sale = ("Rollback" if codes["rollback"] else "Clearance" if codes["clearance"]
            else "Sale" if info["isPriceReduced"] or codes["reducedPrice"] else None)
    was = info.get("wasPrice")
    regular = _money(was["price"]) if isinstance(was, dict) and was.get("price") is not None else None
    if regular is not None and regular > price and sale is None:
        raise VerificationError("Previous price contradicts explicit non-sale status.")
    title = p.get("name")
    if not isinstance(title, str) or not title.strip():
        raise VerificationError("Product title is missing.")
    package = extract_package_details(title)
    # Only explicit net-content specifications, never ingredients/shipping weight.
    for spec in (data.get("idml") or {}).get("specifications", []):
        if not package["package_size_text"] and spec.get("name", "").lower() in {
            "net weight", "net content", "net quantity", "pack size", "package size"
        }:
            package = extract_package_details(str(spec.get("value", "")))
    unit_text = (info.get("unitPrice") or {}).get("priceString")
    unit_price, unit = _extract_unit_price(unit_text)
    variable = codes["finalCostByWeight"] is True or codes.get("priceDisplayCondition") == "avg price"
    if variable and (unit_price is None or unit is None):
        raise VerificationError("Variable-weight unit price is missing; average package price is insufficient.")
    row = normalize_listing_with_quality_checks({
        "date_observed": observed.isoformat(), "search_term": previous.get("search_term"),
        "store": "Walmart Canada", "product_title": title,
        "product_url": previous["product_url"], **package,
        "displayed_price": price, "unit_price_text": unit_text,
        "listed_unit_price": unit_price, "listed_unit": unit, "is_variable_weight": variable,
        "sale_status": sale, "sale_price": price if sale else None, "regular_price": regular,
        "availability": availability, "product_id": expected, "store_id": STORE_ID,
        "store_context": STORE_CONTEXT, "captured_at": capture["captured_at"],
        "verification_status": "verified", "verification_error": None,
    })
    old_package = previous.get("package_size_text")
    if isinstance(old_package, str) and old_package.strip() and not package["package_size_text"]:
        warning = "Current product page exposes no package size; old package metadata was not carried forward."
        row["data_quality_warning"] = "; ".join(filter(None, [row.get("data_quality_warning"), warning]))
    return row


def refresh_rows(previous_rows, folder, cycle, freshness_window=3):
    """Keep one staged audit row per input, including explicit failed rows."""
    rows = []
    for previous in previous_rows:
        try:
            path = Path(folder) / f"{product_id(previous['product_url'])}.json"
            if not path.is_file():
                raise VerificationError("Browser capture not ready.")
            capture = json.loads(path.read_text(encoding="utf-8"))
            row = parse_product_capture(capture, previous, cycle, freshness_window)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # Do NOT relabel last-known data as current. History holds old prices.
            row = {"store": "Walmart Canada", "product_url": previous.get("product_url"),
                   "product_title": previous.get("product_title"), "search_term": previous.get("search_term"),
                   "date_observed": None, "verification_status": "failed",
                   "verification_error": str(exc), "normalization_error": f"Unverified: {exc}",
                   "availability": "unknown", "comparison_group": "unsupported"}
        rows.append(row)
    return rows


def save_listings(rows, path):
    return _save_listings(rows, path, (*QUALITY_OUTPUT_COLUMNS, *EXTRA_COLUMNS))


def verification_summary(rows):
    failures = [{"product_url": r["product_url"], "reason": r["verification_error"]}
                for r in rows if r.get("verification_status") != "verified"]
    return {"total": len(rows), "verified": len(rows) - len(failures), "failed": len(failures),
            "out_of_stock": sum(r.get("availability") == "out_of_stock" for r in rows),
            "failures": failures}


def require_verified(rows):
    summary = verification_summary(rows)
    if not rows or summary["failed"]:
        raise VerificationError(f"Walmart replacement refused: {summary['failed']}/{summary['total']} URLs unverified.")


def guide_captures(previous_rows, folder, cycle, *, freshness_window=3, input_fn=input,
                   open_browser=False, opener=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "capture_product.js"
    script.write_text("copy(JSON.stringify(" + CAPTURE_EXPRESSION + ", null, 2));\n", encoding="utf-8")
    print(f"Walmart maintenance: {len(previous_rows)} existing rows; select Toronto Stockyards (1004).\n"
          f"For each URL, use DevTools Console to run {script}, paste the copied JSON into the printed file.\n"
          "This reads public page data only. Never bypass a challenge. Enter s to record failure, q to cancel.")
    for index, old in enumerate(previous_rows, 1):
        path = folder / f"{product_id(old['product_url'])}.json"
        print(f"\n[{index}/{len(previous_rows)}] {old['product_url']}\nDestination: {path}")
        if open_browser:
            (opener or webbrowser.open_new_tab)(old["product_url"])
        while True:
            result = refresh_rows([old], folder, cycle, freshness_window)[0]
            if result.get("verification_status") == "verified":
                print(f"Verified: {result['availability']}, observed {result['date_observed']}.")
                break
            print(f"Not ready: {result['verification_error']}")
            answer = input_fn("Save current product capture, then Enter; o=open, s=skip, q=cancel: ").strip().lower()
            if answer == "q":
                raise KeyboardInterrupt("Walmart capture cancelled")
            if answer == "s":
                break
            if answer == "o":
                (opener or webbrowser.open_new_tab)(old["product_url"])


def stage_refresh(snapshot, captures, stage, cycle, freshness_window=3):
    """Persistent review-only staging. Never replaces any production file."""
    from refresh_weekly import detect_changes
    previous = pd.read_csv(snapshot)
    rows = refresh_rows(previous.astype(object).where(pd.notna(previous), None).to_dict("records"), captures, cycle, freshness_window)
    stage = Path(stage)
    stage.mkdir(parents=True, exist_ok=False)
    path = save_listings(rows, stage / "walmart_prices_normalized.csv")
    verified = pd.read_csv(path).query("verification_status == 'verified'")
    old_verified = previous[previous.product_url.isin(verified.product_url)]
    changes = detect_changes(old_verified, verified)
    report = {**verification_summary(rows), "changes": changes,
              "change_counts": {name: len({r["product_url"] for r in records})
                                for name, records in changes.items() if isinstance(records, list)},
              "publication": "review only; production unchanged",
              "baseline_context": "Previous snapshot location may be unverified; differences are not proven same-store inflation."}
    (stage / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--snapshot", type=Path, default=root / "data/walmart_prices_normalized.csv")
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True, help="new review directory; never overwritten")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    report = stage_refresh(args.snapshot, args.captures, args.stage, args.date)
    print(json.dumps({key: value for key, value in report.items() if key != "changes"}, indent=2))
    print(f"Detailed changes: {args.stage / 'report.json'}")


if __name__ == "__main__":
    main()
