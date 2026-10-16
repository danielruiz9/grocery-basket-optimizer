"""Browser-only maintenance of existing No Frills product URLs; stage, never publish.

Open the selected-store popup before capturing. Only primary product markup,
public Product JSON-LD and selected-store markup are saved, not page/session data.
"""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
import sys
import webbrowser
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from src.collectors import walmart_maintenance as shared
from src.collectors.common import QUALITY_OUTPUT_COLUMNS, normalize_listing_with_quality_checks
from src.collectors.common import save_listings as _save
from src.collectors.nofrills import extract_package_details, _extract_unit_price, _parse_money

STORE = "No Frills Canada"
STORE_KEY = "nofrills"
STORE_ID = "1356"
STORE_CONTEXT = "Daniel's NOFRILLS Mississauga Eglinton"
STORE_ADDRESS = "620 Eglinton Ave W, Mississauga"
VerificationError = shared.VerificationError
EXTRA_COLUMNS = (*shared.EXTRA_COLUMNS, "store_address", "sale_expiry", "promotion_text",
                 "conditional_offer_terms", "purchase_limit", "after_limit_price")

CAPTURE_EXPRESSION = r"""(() => ({capture_version:1,
 captured_at:new Date().toISOString(), final_url:location.href,
 primary_count:document.querySelectorAll('.product-details-page-details__content__name').length,
 primary_html:document.querySelector('.product-details-page-details__content__name')?.outerHTML,
 product_number:document.querySelector('.product-number__code')?.textContent,
 store_label:document.querySelector('[data-cruller="current-location"]')?.textContent,
 store_details_html:document.querySelector('.pickup-location-details')?.outerHTML,
 products:Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
 .flatMap(n=>{try {const x=JSON.parse(n.textContent);return Array.isArray(x)?x:[x]}catch{return []}})
 .filter(x=>x['@type']==='Product').map(x=>({name:x.name,sku:x.sku,url:x.url,offers:x.offers}))
}))()"""


def product_id(url):
    parsed = urlsplit(str(url))
    match = re.fullmatch(r"/en/[^/]+/p/(\d+_(?:EA|KG))/?", parsed.path)
    if parsed.scheme != "https" or parsed.hostname not in {"www.nofrills.ca", "nofrills.ca"} or not match:
        raise VerificationError("Not an exact No Frills product URL with EA/KG suffix.")
    return match[1]


def _text(node):
    return node.get_text(" ", strip=True) if node else ""


def parse_product_capture(capture, previous, cycle, freshness_window=3):
    expected = product_id(previous["product_url"])
    if capture.get("capture_version") != 1 or capture.get("primary_count") != 1:
        raise VerificationError("Missing or ambiguous primary product area.")
    if product_id(capture.get("final_url")) != expected or capture.get("product_number", "").strip() != expected:
        raise VerificationError("Redirect or full product ID mismatch.")
    timestamp = datetime.fromisoformat(capture["captured_at"].replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise VerificationError("Capture timezone missing.")
    observed = timestamp.astimezone(ZoneInfo("America/Toronto")).date()
    if freshness_window < 1 or not cycle - timedelta(days=freshness_window - 1) <= observed <= cycle:
        raise VerificationError("Capture outside refresh window.")
    store = BeautifulSoup(capture.get("store_details_html") or "", "html.parser")
    link = store.select_one('[data-cruller="pickup-location-store-details-link"]')
    if (capture.get("store_label", "").strip() != STORE_CONTEXT
            or _text(store.select_one('.fulfillment-destination__location')) != STORE_CONTEXT
            or _text(store.select_one('.fulfillment-destination__address')) != STORE_ADDRESS
            or not link or urlsplit(link.get("href", "")).path != f"/en/store-locator/details/{STORE_ID}"):
        raise VerificationError("Selected store is not verified as Daniel's No Frills 1356, Eglinton.")
    products = capture.get("products") or []
    if len(products) != 1:
        raise VerificationError("Missing or ambiguous primary Product JSON-LD.")
    p = products[0]
    offer = p.get("offers") or {}
    if p.get("sku") != expected or product_id(p.get("url")) != expected or product_id(offer.get("url")) != expected:
        raise VerificationError("Structured product identity mismatch.")
    if offer.get("seller", {}).get("name") != "No Frills Online" or offer.get("priceCurrency") != "CAD":
        raise VerificationError("No Frills CAD seller not verified.")
    soup = BeautifulSoup(capture.get("primary_html") or "", "html.parser")
    roots = soup.select('.product-details-page-details__content__name')
    if len(roots) != 1:
        raise VerificationError("Primary product markup missing.")
    root = roots[0]
    title = _text(root.select_one('h1.product-name__item--name'))
    if not title or title != p.get("name"):
        raise VerificationError("Primary title contradicts structured product.")
    price_nodes = [n for n in root.select('.selling-price-list .price__value') if not n.find_parent('del')]
    if len(price_nodes) != 1:
        raise VerificationError("Missing or ambiguous current price.")
    price = shared._money(_parse_money(_text(price_nodes[0])))
    if abs(price - shared._money(offer.get("price"))) > 0.005:
        raise VerificationError("Visible price contradicts Product offer.")
    stock_text = _text(root).lower()
    if "currently low in stock at" in stock_text:
        availability = "low_in_stock"
    elif "currently in stock at" in stock_text:
        availability = "in_stock"
    elif "out of stock" in stock_text:
        availability = "out_of_stock"
    else:
        raise VerificationError("Store-specific availability not exposed.")
    if STORE_CONTEXT.lower() not in stock_text:
        raise VerificationError("Product availability lacks selected-store context.")
    schema_stock = str(offer.get("availability", "")).rsplit('/', 1)[-1]
    if schema_stock != ("OutOfStock" if availability == "out_of_stock" else "InStock"):
        raise VerificationError("Conflicting availability evidence.")
    promo_node = root.select_one('.product-details-deals-badge')
    if promo_node is None:
        raise VerificationError("Promotion area missing; cannot clear previous offer.")
    terms = " | ".join(_text(n) for n in promo_node.select('.product-promo')) or None
    conditional = bool(terms and re.search(r"limit|\bbuy\b|\bwhen\b|\bfor\s*\$|member|\bPC\s*Optimum", terms, re.I))
    limit = re.search(r"limit\s+(\d+),?\s*after limit\s*\$(\d+(?:\.\d+)?)", terms or "", re.I)
    multi = re.search(r"\b(\d+)\s+for\s*\$(\d+(?:\.\d+)?)", terms or "", re.I)
    regular_node = root.select_one('.selling-price-list del .price__value')
    regular = shared._money(_parse_money(_text(regular_node))) if regular_node else None
    sale = bool(promo_node.select_one('.product-badge__icon--sale') or regular is not None and regular > price)
    unit_text = ", ".join(_text(n) for n in root.select('.comparison-price-list__item')) or None
    _, unit_price, unit = _extract_unit_price(unit_text)
    variable = expected.endswith('_KG')
    if variable and unit_price is None:
        raise VerificationError("Variable-weight product has no listed unit rate.")
    package = extract_package_details(_text(root.select_one('.product-name__item--package-size')))
    row = normalize_listing_with_quality_checks({
        "store": STORE, "search_term": previous.get("search_term"), "product_title": title,
        "product_url": previous["product_url"], "product_id": expected, **package,
        "date_observed": observed.isoformat(), "captured_at": capture["captured_at"],
        "store_id": STORE_ID, "store_context": STORE_CONTEXT, "store_address": STORE_ADDRESS,
        "verification_status": "verified", "verification_error": None,
        "displayed_price": price, "regular_price": regular,
        "sale_status": "Conditional offer" if conditional else "Sale" if sale else None,
        "sale_price": price if sale else None, "sale_expiry": _text(promo_node.select_one('.product-badge__icon__expiry')) or None,
        "promotion_text": terms, "conditional_offer_terms": terms if conditional else None,
        "purchase_limit": int(limit[1]) if limit else None, "after_limit_price": float(limit[2]) if limit else None,
        "multi_buy_quantity": int(multi[1]) if multi else None,
        "multi_buy_unit_price": float(multi[2]) / int(multi[1]) if multi and int(multi[1]) else None,
        "single_item_price": None if conditional else price,
        "listed_unit_price": unit_price, "listed_unit": unit, "unit_price_text": unit_text,
        "is_variable_weight": variable, "availability": availability,
    })
    if conditional:
        row['normalization_error'] = '; '.join(filter(None, [row.get('normalization_error'),
            'Conditional promotion cannot be represented safely by current basket costing.']))
        row['price_per_standard_unit'] = None
    return row


def refresh_rows(previous_rows, folder, cycle, freshness_window=3):
    return shared.refresh_rows(previous_rows, folder, cycle, freshness_window, adapter=sys.modules[__name__])


def save_listings(rows, path):
    return _save(rows, path, (*QUALITY_OUTPUT_COLUMNS, *EXTRA_COLUMNS))


verification_summary = shared.verification_summary


def require_verified(rows):
    summary = verification_summary(rows)
    if not rows or summary['failed']:
        raise VerificationError(f"No Frills replacement refused: {summary['failed']}/{summary['total']} audit rows unverified.")


def guide_captures(previous_rows, folder, cycle, *, freshness_window=3, input_fn=input,
                   open_browser=False, opener=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / 'capture_product.js'
    script.write_text('copy(JSON.stringify(' + CAPTURE_EXPRESSION + ', null, 2));\n', encoding='utf-8')
    inventory = {r['product_url']: r for r in previous_rows}
    print(f"No Frills maintenance: {len(inventory)} unique URLs / {len(previous_rows)} audit rows.\n"
          f"Select {STORE_CONTEXT}, store {STORE_ID}, {STORE_ADDRESS}.\n"
          "Wait for the primary product and price, then open View location (Pickup details).\n"
          f"Run {script} in DevTools Console, then save the copied JSON to each filename below.\n"
          "Do not bypass blocks. If Access Denied appears, cancel and resume later; production stays unchanged.")
    for index, old in enumerate(inventory.values(), 1):
        path = folder / f"{product_id(old['product_url'])}.json"
        print(f"\n[{index}/{len(inventory)}] {old['product_url']}\nDestination: {path}")
        if open_browser:
            (opener or webbrowser.open_new_tab)(old['product_url'])
        while True:
            row = refresh_rows([old], folder, cycle, freshness_window)[0]
            if row.get('verification_status') == 'verified':
                print(f"Verified: {row['availability']}; observed {row['date_observed']}.")
                break
            print(f"Not ready: {row['verification_error']}")
            answer = input_fn('Save capture then Enter; o=open, s=skip, q=cancel: ').strip().lower()
            if answer == 'q':
                raise KeyboardInterrupt('No Frills capture cancelled')
            if answer == 's':
                break
            if answer == 'o':
                (opener or webbrowser.open_new_tab)(old['product_url'])


def stage_refresh(snapshot, captures, stage, cycle, freshness_window=3):
    import pandas as pd
    from refresh_weekly import quality_metrics
    report = shared.stage_refresh(snapshot, captures, stage, cycle, freshness_window, adapter=sys.modules[__name__])
    frame = pd.read_csv(Path(stage) / 'nofrills_prices_normalized.csv')
    unique = frame.drop_duplicates('product_url')
    verified = frame[frame.verification_status.eq('verified')]
    report.update({
        'unique_urls': len(unique),
        'unique_urls_verified': int(unique.verification_status.eq('verified').sum()),
        'unique_urls_unverified': int(unique.verification_status.ne('verified').sum()),
        'ready_for_publication': report['failed'] == 0 and report['total'] > 0,
        'quality': quality_metrics(frame),
        'verified_normalization_errors': dict(Counter(verified.normalization_error.dropna())),
        'verified_warnings': dict(Counter(verified.data_quality_warning.dropna())),
        'store_context': STORE_CONTEXT, 'store_id': STORE_ID, 'store_address': STORE_ADDRESS,
    })
    (Path(stage) / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=Path(__file__).resolve().parents[2] / 'data/nofrills_prices_normalized.csv')
    parser.add_argument('--captures', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--date', type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    report = stage_refresh(args.snapshot, args.captures, args.stage, args.date)
    print(json.dumps({k: v for k, v in report.items() if k != 'changes'}, indent=2))


if __name__ == '__main__':
    main()
