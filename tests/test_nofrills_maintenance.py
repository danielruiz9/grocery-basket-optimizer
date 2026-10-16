"""Offline regression tests using primary PDP capture, not live requests."""
from datetime import date
import json
from pathlib import Path

import pandas as pd
import pytest

from src.collectors import nofrills_maintenance as m
from src.collectors import walmart_maintenance as shared

CYCLE = date(2026, 10, 16)


@pytest.fixture
def capture():
    return json.loads((Path(__file__).parent / 'fixtures/nofrills_product_capture.json').read_text())


def previous(capture):
    return {'product_url': capture['final_url'], 'search_term': 'eggs', 'sale_status': 'Sale'}


def test_verified_sale(capture):
    row = m.parse_product_capture(capture, previous(capture), CYCLE)
    assert row['store_id'] == '1356'
    assert row['store_address'] == m.STORE_ADDRESS
    assert row['displayed_price'] == 3.33
    assert row['regular_price'] == 3.93
    assert row['sale_expiry'] == 'Exp. 10/21'
    assert row['availability'] == 'in_stock'
    assert row['package_size'] == 12


@pytest.mark.parametrize('field,value', [
    ('store_label', 'Wrong store'), ('store_details_html', ''),
    ('product_number', '20812144001_KG'), ('primary_count', 2),
    ('products', []), ('primary_html', ''),
    ('captured_at', '2026-09-01T12:00:00Z'),
    ('final_url', 'https://www.nofrills.ca/en/other/p/999_EA'),
])
def test_verification_failures(capture, field, value):
    old = previous(capture)
    capture[field] = value
    with pytest.raises(ValueError):
        m.parse_product_capture(capture, old, CYCLE)


def test_wrong_store_id(capture):
    capture['store_details_html'] = capture['store_details_html'].replace('/1356?', '/9999?')
    with pytest.raises(ValueError, match='Selected store'):
        m.parse_product_capture(capture, previous(capture), CYCLE)


@pytest.mark.parametrize('change', ['price', 'seller', 'sku', 'availability'])
def test_jsonld_contradictions(capture, change):
    p = capture['products'][0]
    if change == 'sku':
        p['sku'] = '20812144001_KG'
    else:
        p['offers'][change] = {'price': 9, 'seller': {'name': 'Other'}, 'availability': 'https://schema.org/OutOfStock'}[change]
    with pytest.raises(ValueError):
        m.parse_product_capture(capture, previous(capture), CYCLE)


def test_sale_ends_only_with_loaded_promo_area(capture):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(capture['primary_html'], 'html.parser')
    for node in soup.select('del, .product-promo'):
        node.decompose()
    capture['primary_html'] = str(soup)
    row = m.parse_product_capture(capture, previous(capture), CYCLE)
    assert row['sale_status'] is None and row['regular_price'] is None
    soup.select_one('.product-details-deals-badge').decompose()
    capture['primary_html'] = str(soup)
    with pytest.raises(ValueError, match='Promotion area'):
        m.parse_product_capture(capture, previous(capture), CYCLE)


@pytest.mark.parametrize('phrase,expected', [('low in stock', 'low_in_stock'), ('out of stock', 'out_of_stock')])
def test_stock(capture, phrase, expected):
    capture['primary_html'] = capture['primary_html'].replace('Currently in stock', 'Currently ' + phrase)
    if expected == 'out_of_stock':
        capture['products'][0]['offers']['availability'] = 'https://schema.org/OutOfStock'
    assert m.parse_product_capture(capture, previous(capture), CYCLE)['availability'] == expected


def test_conditional_price_is_not_unlimited(capture):
    capture['primary_html'] = capture['primary_html'].replace('SAVE $0.60', 'Limit 6, after limit $2.00')
    row = m.parse_product_capture(capture, previous(capture), CYCLE)
    assert row['purchase_limit'] == 6
    assert row['after_limit_price'] == 2
    assert row['single_item_price'] is None
    assert row['price_per_standard_unit'] is None
    assert 'Conditional promotion' in row['normalization_error']


def test_duplicate_audit_rows_and_failure_do_not_reuse_price(capture, tmp_path):
    old = previous(capture)
    (tmp_path / '20812144001_EA.json').write_text(json.dumps(capture))
    rows = m.refresh_rows([old, {**old, 'search_term': 'bread'}], tmp_path, CYCLE)
    assert len(rows) == 2
    assert [r['search_term'] for r in rows] == ['eggs', 'bread']
    failed = m.refresh_rows([{**old, 'product_url': 'https://www.nofrills.ca/en/missing/p/999_EA', 'displayed_price': 10}], tmp_path, CYCLE)
    assert failed[0]['date_observed'] is None
    assert 'displayed_price' not in failed[0]
    with pytest.raises(ValueError):
        shared.require_verified(failed)


def test_stage_never_publishes_or_overwrites(capture, tmp_path):
    snapshot = tmp_path / 'nofrills_prices_normalized.csv'
    pd.DataFrame([{**previous(capture), 'store': m.STORE, 'displayed_price': 4,
                   'date_observed': '2026-09-19', 'product_title': 'Large Size Eggs 12 Pack'}]).to_csv(snapshot, index=False)
    original = snapshot.read_bytes()
    (tmp_path / '20812144001_EA.json').write_text(json.dumps(capture))
    report = m.stage_refresh(snapshot, tmp_path, tmp_path / 'review', CYCLE)
    assert report['verified'] == 1
    assert snapshot.read_bytes() == original
    with pytest.raises(FileExistsError):
        m.stage_refresh(snapshot, tmp_path, tmp_path / 'review', CYCLE)


def reshape(capture, title, package, price, unit, variable=False):
    """Synthetic edge cases applied to an actual primary-area fixture."""
    from bs4 import BeautifulSoup
    p = capture['products'][0]
    p['name'] = title
    p['offers']['price'] = price
    if variable:
        for obj, field in [(capture, 'final_url'), (capture, 'product_number'), (p, 'url'), (p, 'sku'), (p['offers'], 'url')]:
            obj[field] = obj[field].replace('_EA', '_KG')
    soup = BeautifulSoup(capture['primary_html'], 'html.parser')
    soup.select_one('h1').string = title
    soup.select_one('.product-name__item--package-size').string = package
    soup.select_one('.selling-price-list .price__value').string = f'${price}'
    soup.select_one('.comparison-price-list__item').string = unit
    for node in soup.select('del, .product-promo'):
        node.decompose()
    capture['primary_html'] = str(soup)
    return capture


def test_cellentani_warning_and_authoritative_package_cost(capture):
    reshape(capture, 'Cellentani Pasta', '340 g', 2.50, '$7.35/100g')
    row = m.parse_product_capture(capture, previous(capture), CYCLE)
    assert row['price_per_standard_unit'] == pytest.approx(2.50 / 3.4)
    assert row['data_quality_warning']


def test_ambiguous_chicken_not_guessed(capture):
    reshape(capture, 'Boneless Skinless Chicken Breasts', '1 ea', 21, '$21/1ea')
    row = m.parse_product_capture(capture, previous(capture), CYCLE)
    assert row['normalization_error']
    assert row['price_per_standard_unit'] is None


def test_variable_rate_not_package_average(capture):
    import refresh_weekly
    reshape(capture, 'Chicken Breast Boneless & Skinless, Club Pack', '1.226 kg', 13.19, '$10.76/kg', variable=True)
    before = m.parse_product_capture(capture, previous(capture), CYCLE)
    reshape(capture, 'Chicken Breast Boneless & Skinless, Club Pack', '1.5 kg', 16.14, '$10.76/kg')
    after = m.parse_product_capture(capture, previous(capture), CYCLE)
    assert after['price_per_standard_unit'] == before['price_per_standard_unit'] == pytest.approx(1.076)
    changes = refresh_weekly.detect_changes(pd.DataFrame([before]), pd.DataFrame([after]))
    assert not changes['price_increases'] and not changes['price_decreases']
    assert len(changes['average_package_changes']) == 1
    assert len(changes['unit_average_discrepancies']) == 1


def test_primary_area_ignores_recommendations(capture):
    capture['primary_html'] += '<div><h1>Sponsored Item</h1><div class="selling-price-list"><span class="price__value">$99</span></div></div>'
    assert m.parse_product_capture(capture, previous(capture), CYCLE)['displayed_price'] == 3.33


def test_guided_unique_inventory_and_weekly_delegation(capture, tmp_path):
    import refresh_weekly
    old = previous(capture)
    folder = tmp_path / 'nofrills'
    calls = []
    def save(prompt):
        calls.append(prompt)
        assert folder.is_dir()
        (folder / '20812144001_EA.json').write_text(json.dumps(capture))
        return ''
    rows = refresh_weekly.collect_store('nofrills', tmp_path, CYCLE, previous_rows=[old, old], input_fn=save)
    assert len(calls) == 1
    assert len(rows) == 2
    assert all(r['verification_status'] == 'verified' for r in rows)
    assert (folder / 'capture_product.js').is_file()


def test_multi_buy_preserved_and_excluded(capture):
    capture['primary_html'] = capture['primary_html'].replace('SAVE $0.60', '2 for $5.00')
    row = m.parse_product_capture(capture, previous(capture), CYCLE)
    assert row['multi_buy_quantity'] == 2
    assert row['multi_buy_unit_price'] == 2.50
    assert row['normalization_error']
    assert row['price_per_standard_unit'] is None


def test_conditional_change_not_reported_as_unlimited_price_change(capture):
    from refresh_weekly import detect_changes
    before = m.parse_product_capture(capture, previous(capture), CYCLE)
    after = {**before, 'displayed_price': 1, 'conditional_offer_terms': 'Limit 6, after limit $2.00',
             'purchase_limit': 6, 'after_limit_price': 2}
    changes = detect_changes(pd.DataFrame([before]), pd.DataFrame([after]))
    assert len(changes['conditional_offer_changes']) == 1
    assert not changes['price_decreases']


def test_out_of_stock_audit_kept_but_not_eligible(capture):
    from src.integration import integrate_price_frames
    capture['primary_html'] = capture['primary_html'].replace('Currently in stock', 'Currently out of stock')
    capture['products'][0]['offers']['availability'] = 'https://schema.org/OutOfStock'
    row = m.parse_product_capture(capture, previous(capture), CYCLE)
    result = integrate_price_frames([pd.DataFrame([row])])
    assert len(result.combined) == 1
    assert result.optimizer_ready.empty


def test_missing_current_price_not_taken_from_recommendations(capture):
    capture['primary_html'] = capture['primary_html'].replace('$3.33', 'unavailable')
    capture['primary_html'] += '<div class="selling-price-list"><span class="price__value">$3.33</span></div>'
    with pytest.raises(ValueError, match='price'):
        m.parse_product_capture(capture, previous(capture), CYCLE)
