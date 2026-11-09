from scraper import utils


def test_detect_marketplace():
    assert utils.detect_marketplace("https://www.amazon.com/dp/B000") == "amazon"
    assert utils.detect_marketplace("https://www.ebay.co.uk/itm/123") == "ebay"
    assert utils.detect_marketplace("https://www.etsy.com/listing/1") == "etsy"
    assert utils.detect_marketplace("https://example.com/x") is None


def test_validate_url_rejects_non_marketplace():
    import pytest
    with pytest.raises(ValueError):
        utils.validate_and_normalize_url("https://example.com", resolve_dns=False)
    with pytest.raises(ValueError):
        utils.validate_and_normalize_url("ftp://amazon.com", resolve_dns=False)
    # valid one passes (skip DNS in test)
    out = utils.validate_and_normalize_url("https://www.ebay.com/itm/1", resolve_dns=False)
    assert out.startswith("https://www.ebay.com")


def test_extract_asin():
    assert utils.extract_asin("https://www.amazon.com/dp/B08N5WRWNW") == "B08N5WRWNW"
    assert utils.extract_asin("https://www.amazon.com/gp/product/B07XYZ1234/ref=x") == "B07XYZ1234"
    assert utils.extract_asin("https://www.amazon.com/s?k=coffee") == ""


def test_build_search_url():
    assert "k=coffee+grinder" in utils.build_search_url("amazon", "coffee grinder")
    assert "_nkw=coffee+grinder" in utils.build_search_url("ebay", "coffee grinder")
    assert "q=coffee+grinder" in utils.build_search_url("etsy", "coffee grinder")


def test_parse_price():
    assert utils.parse_price("$1,299.99") == ("1299.99", "USD")
    assert utils.parse_price("£49.50")[0] == "49.50"
    assert utils.parse_price("1.234,56 EUR")[1] == "EUR"
    assert utils.parse_price("") == ("", "")


def test_dedupe_preserve_order():
    assert utils.dedupe_preserve_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_to_csv_escaping():
    rows = [{
        "time_scraped": "2026-01-01", "product_url": "https://x",
        "product_name": 'Weird, "quoted"\nname', "images": ["u1", "u2"],
        "features": ["f1", "f2"],
    }]
    csv_text = utils.to_csv(rows)
    lines = csv_text.splitlines()
    # header has all 25 columns
    assert lines[0].split(",")[0] == "time_scraped"
    assert len(lines[0].split(",")) == 25
    # quoting handled: the name field is wrapped in quotes and newline preserved
    assert '"Weird, ""quoted""' in csv_text
    # list fields joined with pipe
    assert "u1 | u2" in csv_text
    assert "f1 | f2" in csv_text


def test_is_private_host_blocks_localhost():
    assert not utils.is_allowed_url("http://localhost/x", resolve_dns=True)
    assert not utils.is_allowed_url("http://127.0.0.1/x", resolve_dns=True)
