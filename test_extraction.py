from scraper import extraction
from scraper.utils import CSV_COLUMNS

JSONLD_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context":"https://schema.org","@type":"Product",
  "name":"Burr Coffee Grinder","brand":{"@type":"Brand","name":"GrindCo"},
  "description":"Stainless steel conical burr grinder.",
  "sku":"GC-900","color":"Matte Black","material":"Stainless Steel",
  "image":["https://img/1.jpg","https://img/1.jpg","https://img/2.jpg"],
  "aggregateRating":{"@type":"AggregateRating","ratingValue":"4.6","reviewCount":"318"},
  "offers":{"@type":"Offer","price":"129.99","priceCurrency":"USD",
            "availability":"https://schema.org/InStock",
            "seller":{"@type":"Organization","name":"BestBrew"}}
}
</script>
</head><body></body></html>
"""

OG_ONLY_HTML = """
<html><head>
<meta property="og:title" content="OG Grinder"/>
<meta property="og:image" content="//cdn/og.jpg"/>
<meta property="product:price:amount" content="59.00"/>
<meta property="product:price:currency" content="GBP"/>
<title>Fallback Title</title>
</head><body></body></html>
"""

ARRAY_GRAPH_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"BreadcrumbList"},
  {"@type":"Product","name":"Graph Product","offers":[
     {"@type":"Offer","price":"20.00","priceCurrency":"USD"},
     {"@type":"Offer","price":"15.00","priceCurrency":"USD"}]}
]}
</script></head><body></body></html>
"""

INVALID_JSONLD_HTML = """
<html><head>
<script type="application/ld+json">{ this is not valid json }</script>
<meta property="og:title" content="Survived Bad JSONLD"/>
</head><body></body></html>
"""


def test_jsonld_full_extract():
    rec = extraction.extract_static(JSONLD_HTML, "https://ebay.com/itm/1")
    assert rec["product_name"] == "Burr Coffee Grinder"
    assert rec["brand"] == "GrindCo"
    assert rec["price"] == "129.99"
    assert rec["currency"] == "USD"
    assert rec["rating"] == "4.6"
    assert rec["review_count"] == "318"
    assert rec["availability"].endswith("InStock")
    assert rec["seller_name"] == "BestBrew"
    assert rec["color"] == "Matte Black"
    # duplicate image removed, order preserved
    assert rec["images"] == ["https://img/1.jpg", "https://img/2.jpg"]
    assert rec["main_image"] == "https://img/1.jpg"


def test_og_fallback_and_protocol_relative_image():
    rec = extraction.extract_static(OG_ONLY_HTML, "https://etsy.com/listing/9")
    assert rec["product_name"] == "OG Grinder"
    assert rec["main_image"] == "https://cdn/og.jpg"
    assert rec["price"] == "59.00"
    assert rec["currency"] == "GBP"


def test_graph_and_lowest_offer():
    rec = extraction.extract_static(ARRAY_GRAPH_HTML, "https://ebay.com/itm/2")
    assert rec["product_name"] == "Graph Product"
    assert rec["price"] == "15.00"  # lowest offer chosen


def test_invalid_jsonld_does_not_crash():
    rec = extraction.extract_static(INVALID_JSONLD_HTML, "https://ebay.com/itm/3")
    assert rec["product_name"] == "Survived Bad JSONLD"


def test_normalize_has_all_columns_and_asin():
    partial = extraction.extract_static(JSONLD_HTML, "https://www.amazon.com/dp/B000TEST12")
    rec = extraction.normalize_record(partial, "https://www.amazon.com/dp/B000TEST12", "amazon", "ts")
    assert set(rec.keys()) >= set(CSV_COLUMNS)
    assert rec["asin"] == "B000TEST12"
    assert rec["time_scraped"] == "ts"
    assert rec["product_url"].endswith("B000TEST12")


def test_merge_priority():
    high = {"price": "10", "brand": ""}
    low = {"price": "99", "brand": "Low"}
    merged = extraction.merge_fields(high, low)
    assert merged["price"] == "10"   # high priority wins
    assert merged["brand"] == "Low"  # blank in high, filled from low
