import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.request import Request

import pytest

from openstack_perf.artifacts import deserialize_run, serialize_run
from openstack_perf.product_http import (
    MAX_PAGE_DELIVERY_BYTES,
    MAX_PAGE_RESOURCES,
    MAX_RESPONSE_BYTES,
    ProductValidationError,
    _SameOriginRedirectHandler,
    _add_delivery_bytes,
    _extract_page_resources,
    _fetch,
    observe_corporate_web_application,
    observe_page_delivery,
    observe_service_http_endpoints,
)
from openstack_perf.results import (
    CleanSnapshotStatus,
    EnvironmentFingerprint,
    FunctionalVerdict,
    RegressionRunResult,
    RunMetadata,
    RunRole,
)


class _Response:
    def __init__(self, url, body, status=200, content_type="text/html"):
        self.url = url
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size):
        return self.body[:size]

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url


class _SizedBody:
    def __init__(self, size):
        self.size = size

    def __len__(self):
        return self.size

    def __getitem__(self, _item):
        return self


class _RoutingOpener:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        value = self.routes[request.full_url]
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            status, body, content_type = (
                (*value, "text/html") if len(value) == 2 else value
            )
        else:
            status, body, content_type = 200, value, "text/html"
        if "rest_route=" in request.full_url and not isinstance(value, tuple):
            content_type = "application/json"
        return _Response(request.full_url, body, status, content_type)


class _CrossOriginOpener:
    def open(self, request, timeout):
        return _Response("http://outside.test/", b"Active connections:")


class _FinalUrlOpener(_RoutingOpener):
    def __init__(self, routes, final_urls):
        super().__init__(routes)
        self.final_urls = final_urls

    def open(self, request, timeout):
        response = super().open(request, timeout)
        final_url = self.final_urls.get(request.full_url, request.full_url)
        if isinstance(final_url, list):
            final_url = final_url.pop(0)
        response.url = final_url
        return response


def _web_routes(release_title="Release notes 1.0"):
    posts = json.dumps([{"title": {"rendered": release_title}}]).encode()
    return {
        "http://product.test/": b"Corp Intranet",
        "http://product.test/?s=release": release_title.encode(),
        "http://product.test/?rest_route=/wp/v2/posts": posts,
        "http://product.test/wp-login.php": b'<form id="loginform">',
        "http://product.test/site/": (
            b'Home <a href="/site/">Home</a>'
            b'<a href="/site/about.html">About</a>'
            b'<a href="/site/products.html">Products</a>'
            b'<a href="/site/team.html">Team</a>'
            b'<a href="/site/contact.html">Contact</a>'
            b'<a href="https://outside.test/">Outside</a>'
        ),
        "http://product.test/site/about.html": b"About",
        "http://product.test/site/products.html": b"Products",
        "http://product.test/site/team.html": b"Team",
        "http://product.test/site/contact.html": b"Contact",
    }


def _service_routes():
    return {
        "http://product.test/status": b"Active connections: 1",
        "http://product.test:8080/": b"Corp Tomcat",
        "http://product.test:8080/examples/": b"Apache Tomcat Examples",
        "http://product.test:8080/examples/servlets/servlet/HelloWorldExample": b"Hello World!",
    }


def _page_routes():
    return {
        "http://product.test/": (
            b'<link rel="stylesheet" href="/style.css">'
            b'<script src="/app.js"></script>'
            b'<img src="/image.png">'
            b'<a href="/ignored">Ignored</a>'
        ),
        "http://product.test/app.js": b"script",
        "http://product.test/image.png": b"image",
        "http://product.test/style.css": b"style",
    }


def _clock(count=1000):
    values = iter(float(value) for value in range(count))
    return lambda: next(values)


def _run(observations):
    return RegressionRunResult(
        metadata=RunMetadata(
            "run-1",
            RunRole.CANDIDATE,
            "2026-09-04T10:00:00Z",
            "2026-09-04T10:01:00Z",
            CleanSnapshotStatus.CLEAN,
        ),
        environment=EnvironmentFingerprint(
            "test", "RegionOne", "release", "main", "application-1"
        ),
        observations=observations,
    )


def _load_bdd_module():
    path = (
        Path(__file__).parents[1]
        / "integration"
        / "test_product_regression_bdd.py"
    )
    spec = importlib.util.spec_from_file_location(
        "product_regression_bdd_for_unit_tests", path
    )
    module = importlib.util.module_from_spec(spec)
    with patch("pytest_bdd.scenarios"):
        spec.loader.exec_module(module)
    return module


def test_web_application_checks_all_exact_targets_and_navigation():
    opener = _RoutingOpener(_web_routes())

    observations = observe_corporate_web_application(
        "http://product.test",
        expected_release_title="Release notes 1.0",
        sample_count=1,
        opener=opener,
        clock=_clock(),
    )

    assert [item.target_id for item in observations] == [
        "wordpress.home",
        "wordpress.search.release",
        "wordpress.rest.posts",
        "wordpress.login",
        "static.home",
        "static.about",
        "static.products",
        "static.team",
        "static.contact",
    ]
    assert all(item.functional_verdict is FunctionalVerdict.PASS for item in observations)
    assert len(opener.calls) == 18
    assert {call[0].method for call in opener.calls} == {"GET"}
    assert {call[1] for call in opener.calls} == {10.0}
    assert "https://outside.test/" not in {call[0].full_url for call in opener.calls}


def test_default_web_sampling_has_one_warmup_and_ten_timed_requests_per_target():
    opener = _RoutingOpener(_web_routes())

    observations = observe_corporate_web_application(
        "http://product.test/",
        expected_release_title="Release notes 1.0",
        opener=opener,
        clock=_clock(),
    )

    assert all(len(item.samples) == 10 for item in observations)
    assert all(item.statistics.sample_count == 10 for item in observations)
    assert len(opener.calls) == 9 * 11


def test_page_resource_extraction_is_same_origin_deduplicated_and_sorted():
    body = (
        b'<img src="images/a.png#first">'
        b'<script src="/z.js?version=1#fragment"></script>'
        b'<link REL="stylesheet preload" href="http://product.test/a.css">'
        b'<img src="images/a.png#second">'
        b'<a href="/not-an-asset">ignored</a>'
        b'<link rel="icon" href="/favicon.ico">'
    )

    assert _extract_page_resources(body, "http://product.test/path/") == (
        "http://product.test/a.css",
        "http://product.test/path/images/a.png",
        "http://product.test/z.js?version=1",
    )


@pytest.mark.parametrize(
    "reference,match",
    [
        ("http://outside.test/a.js", "same-origin"),
        ("http://user:password@product.test/a.js", "credentials"),
    ],
)
def test_page_resource_extraction_rejects_unsafe_urls(reference, match):
    with pytest.raises(ProductValidationError, match=match):
        _extract_page_resources(
            f'<script src="{reference}"></script>'.encode(),
            "http://product.test/",
        )


def test_empty_page_resource_manifest_is_allowed():
    assert _extract_page_resources(b"<p>no assets</p>", "http://product.test/") == ()


def test_page_resource_manifest_allows_32_and_rejects_33():
    allowed = b"".join(
        f'<img src="/{index}.png">'.encode() for index in range(MAX_PAGE_RESOURCES)
    )
    excessive = allowed + b'<img src="/extra.png">'

    assert len(_extract_page_resources(allowed, "http://product.test/")) == 32
    with pytest.raises(ProductValidationError, match="exceeded 32"):
        _extract_page_resources(excessive, "http://product.test/")


def test_page_delivery_uses_one_warmup_and_ten_timed_full_deliveries():
    opener = _RoutingOpener(_page_routes())

    observation = observe_page_delivery(
        "http://product.test", opener=opener, clock=_clock()
    )

    assert observation.functional_verdict is FunctionalVerdict.PASS
    assert len(observation.samples) == 10
    assert observation.statistics.sample_count == 10
    assert len(opener.calls) == 4 * 11
    assert {call[0].method for call in opener.calls} == {"GET"}


def test_page_delivery_duration_sums_only_network_body_read_intervals():
    observation = observe_page_delivery(
        "http://product.test",
        sample_count=1,
        opener=_RoutingOpener(_page_routes()),
        clock=_clock(),
    )

    assert observation.samples[0].duration_seconds == 4.0


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "text/html; charset=UTF-8", " Text/HTML ; Charset=UTF-8"],
)
def test_page_delivery_accepts_normalized_html_content_type(content_type):
    routes = _page_routes()
    routes["http://product.test/"] = (200, routes["http://product.test/"], content_type)

    observation = observe_page_delivery(
        "http://product.test", sample_count=1,
        opener=_RoutingOpener(routes), clock=_clock()
    )

    assert observation.functional_verdict is FunctionalVerdict.PASS


@pytest.mark.parametrize(
    "content_type", [None, "application/json", "text/plain", "image/png"]
)
def test_page_delivery_rejects_non_html_primary_content_type(content_type):
    routes = _page_routes()
    routes["http://product.test/"] = (
        200, routes["http://product.test/"], content_type
    )

    observation = observe_page_delivery(
        "http://product.test", sample_count=1,
        opener=_RoutingOpener(routes), clock=_clock()
    )

    assert observation.samples == ()
    assert observation.error_message.endswith("expected HTML content type")


def test_page_delivery_timed_primary_must_remain_html():
    routes = _page_routes()
    body = routes["http://product.test/"]
    routes["http://product.test/"] = [
        (200, body, "text/html"),
        (200, body, "application/json"),
    ]

    observation = observe_page_delivery(
        "http://product.test", sample_count=1,
        opener=_RoutingOpener(routes), clock=_clock()
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert observation.samples[0].successful is False
    assert "expected HTML content type" in observation.error_message


def test_page_delivery_reuses_the_frozen_warmup_manifest():
    routes = _page_routes()
    routes["http://product.test/"] = [
        b'<img src="/image.png">',
        b'<img src="/different.png">',
    ]
    opener = _RoutingOpener(routes)

    observation = observe_page_delivery(
        "http://product.test", sample_count=1, opener=opener, clock=_clock()
    )

    assert observation.functional_verdict is FunctionalVerdict.PASS
    assert [call[0].full_url for call in opener.calls] == [
        "http://product.test/",
        "http://product.test/image.png",
        "http://product.test/",
        "http://product.test/image.png",
    ]


def test_page_delivery_primary_cannot_finish_at_approved_asset():
    page = "http://product.test/"
    asset = "http://product.test/image.png"
    opener = _FinalUrlOpener(
        {page: b'<img src="/image.png">', asset: b"image"},
        {page: [page, asset]},
    )

    observation = observe_page_delivery(
        page, sample_count=1, opener=opener, clock=_clock()
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert "destination was not approved" in observation.error_message


@pytest.mark.parametrize(
    "unexpected_final",
    ["http://product.test/b.js", "http://product.test/"],
)
def test_page_delivery_asset_must_finish_at_its_exact_url(unexpected_final):
    page = "http://product.test/"
    asset_a = "http://product.test/a.js"
    asset_b = "http://product.test/b.js"
    opener = _FinalUrlOpener(
        {
            page: b'<script src="/a.js"></script><script src="/b.js"></script>',
            asset_a: b"a",
            asset_b: b"b",
        },
        {asset_a: unexpected_final},
    )

    observation = observe_page_delivery(
        page, sample_count=1, opener=opener, clock=_clock()
    )

    assert observation.samples == ()
    assert "destination was not approved" in observation.error_message


def test_page_delivery_redirect_handler_rejects_another_manifest_asset():
    handler = _SameOriginRedirectHandler(("http://product.test/a.js",))
    parent = MagicMock()
    handler.add_parent(parent)

    with pytest.raises(ProductValidationError):
        handler.http_error_302(
            Request("http://product.test/a.js"),
            MagicMock(),
            302,
            "Found",
            {"location": "/b.js"},
        )

    parent.open.assert_not_called()


def test_page_delivery_asset_failure_during_warmup_prevents_samples():
    routes = _page_routes()
    routes["http://product.test/image.png"] = URLError("token=private")
    opener = _RoutingOpener(routes)

    observation = observe_page_delivery(
        "http://product.test", sample_count=2, opener=opener, clock=_clock()
    )

    assert observation.samples == ()
    assert observation.statistics is None
    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert observation.error_message.endswith("URLError")
    assert "private" not in repr(observation)


def test_page_delivery_rejects_non_200_asset():
    routes = _page_routes()
    routes["http://product.test/app.js"] = (503, b"private")

    observation = observe_page_delivery(
        "http://product.test", sample_count=1,
        opener=_RoutingOpener(routes), clock=_clock()
    )

    assert observation.samples == ()
    assert "expected HTTP status 200" in observation.error_message
    assert "private" not in repr(observation)


def test_page_delivery_accepts_asset_at_response_limit():
    routes = {
        "http://product.test/": b'<img src="/image.png">',
        "http://product.test/image.png": b"x" * MAX_RESPONSE_BYTES,
    }

    observation = observe_page_delivery(
        "http://product.test", sample_count=1,
        opener=_RoutingOpener(routes), clock=_clock()
    )

    assert observation.functional_verdict is FunctionalVerdict.PASS


def test_page_delivery_rejects_asset_above_response_limit():
    routes = {
        "http://product.test/": b'<img src="/image.png">',
        "http://product.test/image.png": b"x" * (MAX_RESPONSE_BYTES + 1),
    }

    observation = observe_page_delivery(
        "http://product.test", sample_count=1,
        opener=_RoutingOpener(routes), clock=_clock()
    )

    assert observation.samples == ()
    assert "2 MiB limit" in observation.error_message


def test_page_delivery_failed_sample_retains_successful_sample_statistics():
    routes = {
        "http://product.test/": b'<img src="/image.png">',
        "http://product.test/image.png": [
            b"warm-up", URLError("private"), b"success"
        ],
    }

    observation = observe_page_delivery(
        "http://product.test", sample_count=2,
        opener=_RoutingOpener(routes), clock=_clock()
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert [sample.successful for sample in observation.samples] == [False, True]
    assert observation.statistics.sample_count == 1
    assert observation.statistics.p50_seconds == observation.samples[1].duration_seconds
    assert "private" not in repr(observation)


def test_page_delivery_aggregate_byte_limit_accepts_exact_and_rejects_above():
    assert _add_delivery_bytes(MAX_PAGE_DELIVERY_BYTES - 1, 1) == (
        MAX_PAGE_DELIVERY_BYTES
    )
    with pytest.raises(ProductValidationError, match="16 MiB"):
        _add_delivery_bytes(MAX_PAGE_DELIVERY_BYTES, 1)


@pytest.mark.parametrize("extra_byte,passes", [(0, True), (1, False)])
def test_page_delivery_execution_enforces_aggregate_body_limit(
    extra_byte, passes
):
    page = "http://product.test/"
    asset_urls = tuple(
        f"http://product.test/{index}.bin" for index in range(8)
    )
    body = b"".join(
        f'<img src="/{index}.bin">'.encode() for index in range(8)
    )
    remaining = MAX_PAGE_DELIVERY_BYTES - len(body)
    routes = {page: body}
    for asset_url in asset_urls[:-1]:
        routes[asset_url] = _SizedBody(MAX_RESPONSE_BYTES)
        remaining -= MAX_RESPONSE_BYTES
    routes[asset_urls[-1]] = _SizedBody(remaining + extra_byte)

    observation = observe_page_delivery(
        page, sample_count=1, opener=_RoutingOpener(routes), clock=_clock(100)
    )

    if passes:
        assert observation.functional_verdict is FunctionalVerdict.PASS
        assert observation.samples[0].successful is True
    else:
        assert observation.samples == ()
        assert "16 MiB aggregate limit" in observation.error_message


def test_page_delivery_observation_round_trips_through_artifact_schema():
    observation = observe_page_delivery(
        "http://product.test", sample_count=2,
        opener=_RoutingOpener(_page_routes()), clock=_clock()
    )

    restored = deserialize_run(serialize_run(_run((observation,))))

    assert restored.observations == (observation,)


def test_timing_stops_after_body_read_and_before_content_validation():
    opener = _RoutingOpener(_service_routes())
    clock = _clock()

    observations = observe_service_http_endpoints(
        "http://product.test",
        "http://product.test:8080",
        sample_count=1,
        opener=opener,
        clock=clock,
    )

    assert [item.samples[0].duration_seconds for item in observations] == [1.0] * 4


def test_request_and_response_metadata_are_outside_timing_boundary():
    events = []

    class Headers(dict):
        def get(self, key, default=None):
            events.append("content-type")
            return super().get(key, default)

    class Response(_Response):
        headers = Headers({"Content-Type": "text/html"})

        def __init__(self):
            self.url = "http://product.test/status"
            self.body = b"Active connections:"
            self.status = 200

        def read(self, size):
            events.append("read")
            return super().read(size)

        def getcode(self):
            events.append("status")
            return super().getcode()

        def geturl(self):
            events.append("url")
            return super().geturl()

    class Opener:
        def open(self, request, timeout):
            assert isinstance(request, Request)
            events.append("open")
            return Response()

    clock_values = iter((10.0, 12.0))

    def clock():
        events.append("clock")
        return next(clock_values)

    with patch(
        "openstack_perf.product_http.Request",
        side_effect=lambda *args, **kwargs: (
            events.append("request") or Request(*args, **kwargs)
        ),
    ):
        result = _fetch(
            Opener(),
            "http://product.test/status",
            10.0,
            MAX_RESPONSE_BYTES,
            clock,
        )

    assert events == [
        "request",
        "clock",
        "open",
        "read",
        "clock",
        "status",
        "url",
        "content-type",
    ]
    assert result[-1] == 2.0


def test_marker_failure_retains_timing_and_fails_target():
    routes = _service_routes()
    routes["http://product.test/status"] = [
        b"Active connections: 1",
        b"wrong content",
    ]
    opener = _RoutingOpener(routes)

    observation = observe_service_http_endpoints(
        "http://product.test",
        "http://product.test:8080",
        sample_count=1,
        opener=opener,
        clock=_clock(),
    )[0]

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert observation.samples[0].duration_seconds == 1.0
    assert observation.samples[0].successful is False
    assert "wrong content" not in repr(observation)


def test_timed_network_failure_preserves_statistics_from_successful_samples():
    routes = _service_routes()
    routes["http://product.test/status"] = [
        b"Active connections: warm-up",
        URLError("private network detail"),
        b"Active connections: success",
    ]

    observation = observe_service_http_endpoints(
        "http://product.test",
        "http://product.test:8080",
        sample_count=2,
        opener=_RoutingOpener(routes),
        clock=_clock(),
    )[0]

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert [sample.successful for sample in observation.samples] == [False, True]
    assert observation.statistics.sample_count == 1
    assert observation.statistics.minimum_seconds == observation.samples[1].duration_seconds
    assert "private network detail" not in repr(observation)


def test_warmup_failure_prevents_timed_requests():
    routes = _service_routes()
    routes["http://product.test/status"] = URLError("token=secret")
    opener = _RoutingOpener(routes)

    observation = observe_service_http_endpoints(
        "http://product.test",
        "http://product.test:8080",
        sample_count=2,
        opener=opener,
        clock=_clock(),
    )[0]

    assert observation.samples == ()
    assert observation.statistics is None
    assert observation.error_message == "nginx.status warm-up failed: URLError"
    assert "secret" not in repr(observation)


def test_non_200_response_is_a_controlled_failure():
    routes = _service_routes()
    routes["http://product.test/status"] = (503, b"private response")

    observation = observe_service_http_endpoints(
        "http://product.test",
        "http://product.test:8080",
        sample_count=1,
        opener=_RoutingOpener(routes),
        clock=_clock(),
    )[0]

    assert observation.error_message == (
        "nginx.status warm-up failed: expected HTTP status 200"
    )
    assert "private response" not in repr(observation)


def test_response_larger_than_limit_is_rejected_without_retaining_body():
    routes = _service_routes()
    routes["http://product.test/status"] = b"x" * (MAX_RESPONSE_BYTES + 1)

    observation = observe_service_http_endpoints(
        "http://product.test",
        "http://product.test:8080",
        sample_count=1,
        opener=_RoutingOpener(routes),
        clock=_clock(),
    )[0]

    assert "2 MiB limit" in observation.error_message
    assert "xxx" not in repr(observation)


def test_response_exactly_at_limit_is_accepted():
    routes = _service_routes()
    marker = b"Active connections:"
    routes["http://product.test/status"] = marker + b"x" * (
        MAX_RESPONSE_BYTES - len(marker)
    )

    observation = observe_service_http_endpoints(
        "http://product.test",
        "http://product.test:8080",
        sample_count=1,
        opener=_RoutingOpener(routes),
        clock=_clock(),
    )[0]

    assert observation.functional_verdict is FunctionalVerdict.PASS


def test_cross_origin_final_response_is_rejected():
    observation = observe_service_http_endpoints(
        "http://product.test",
        "http://product.test:8080",
        sample_count=1,
        opener=_CrossOriginOpener(),
        clock=_clock(),
    )[0]

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert "response destination was not approved" in observation.error_message


def _redirect(handler, source, destination):
    parent = MagicMock()
    handler.add_parent(parent)
    response = MagicMock()
    request = Request(source)
    request.timeout = 10.0
    handler.http_error_302(
        request,
        response,
        302,
        "Found",
        {"location": destination},
    )
    return parent


def test_redirect_handler_rejects_cross_origin_before_following():
    handler = _SameOriginRedirectHandler(("http://product.test/site/",))
    parent = MagicMock()
    handler.add_parent(parent)

    with pytest.raises(ProductValidationError):
        handler.http_error_302(
            Request("http://product.test/site/"),
            MagicMock(),
            302,
            "Found",
            {"location": "http://outside.test/site/"},
        )

    parent.open.assert_not_called()


def test_redirect_handler_rejects_unapproved_same_origin_path():
    handler = _SameOriginRedirectHandler(("http://product.test/site/",))
    parent = MagicMock()
    handler.add_parent(parent)

    with pytest.raises(ProductValidationError):
        handler.http_error_302(
            Request("http://product.test/site/"),
            MagicMock(),
            302,
            "Found",
            {"location": "/unapproved"},
        )

    parent.open.assert_not_called()


def test_redirect_handler_requires_exact_approved_query():
    handler = _SameOriginRedirectHandler(
        ("http://product.test/?s=release",)
    )
    parent = MagicMock()
    handler.add_parent(parent)

    with pytest.raises(ProductValidationError):
        handler.http_error_302(
            Request("http://product.test/?s=release"),
            MagicMock(),
            302,
            "Found",
            {"location": "/?s=other"},
        )

    parent.open.assert_not_called()


def test_redirect_handler_rejects_embedded_credentials():
    handler = _SameOriginRedirectHandler(("http://product.test/site/",))
    parent = MagicMock()
    handler.add_parent(parent)

    with pytest.raises(ProductValidationError):
        handler.http_error_302(
            Request("http://product.test/site/"),
            MagicMock(),
            302,
            "Found",
            {"location": "http://user:password@product.test/site/"},
        )

    parent.open.assert_not_called()


def test_redirect_handler_never_follows_manager_status():
    handler = _SameOriginRedirectHandler(("http://product.test:8080/",))
    parent = MagicMock()
    handler.add_parent(parent)

    with pytest.raises(ProductValidationError):
        handler.http_error_302(
            Request("http://product.test:8080/"),
            MagicMock(),
            302,
            "Found",
            {"location": "/manager/status"},
        )

    parent.open.assert_not_called()


def test_redirect_handler_allows_an_explicitly_approved_destination():
    handler = _SameOriginRedirectHandler(
        (
            "http://product.test/site/",
            "http://product.test/site/about.html",
        )
    )

    parent = _redirect(
        handler,
        "http://product.test/site/",
        "/site/about.html",
    )

    assert parent.open.call_args.args[0].full_url == (
        "http://product.test/site/about.html"
    )


def test_redirect_chain_checks_every_hop_before_following():
    handler = _SameOriginRedirectHandler(
        (
            "http://product.test/site/",
            "http://product.test/site/about.html",
        )
    )
    parent = MagicMock()
    handler.add_parent(parent)

    def second_hop(request, timeout=None):
        return handler.http_error_302(
            request,
            MagicMock(),
            302,
            "Found",
            {"location": "/manager/status"},
        )

    parent.open.side_effect = second_hop
    request = Request("http://product.test/site/")
    request.timeout = 10.0
    with pytest.raises(ProductValidationError):
        handler.http_error_302(
            request,
            MagicMock(),
            302,
            "Found",
            {"location": "/site/about.html"},
        )

    assert parent.open.call_count == 1
    assert parent.open.call_args.args[0].full_url.endswith("/site/about.html")


def test_malformed_json_is_sanitized():
    routes = _web_routes()
    routes["http://product.test/?rest_route=/wp/v2/posts"] = b"not JSON secret"

    observation = observe_corporate_web_application(
        "http://product.test",
        expected_release_title="Release notes 1.0",
        sample_count=1,
        opener=_RoutingOpener(routes),
        clock=_clock(),
    )[2]

    assert observation.error_message.endswith("JSONDecodeError")
    assert "not JSON secret" not in repr(observation)


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/json; charset=UTF-8",
        " Application/JSON ; Charset=UTF-8",
    ],
)
def test_rest_accepts_json_content_type(content_type):
    routes = _web_routes()
    body = routes["http://product.test/?rest_route=/wp/v2/posts"]
    routes["http://product.test/?rest_route=/wp/v2/posts"] = (
        200,
        body,
        content_type,
    )

    observation = observe_corporate_web_application(
        "http://product.test",
        expected_release_title="Release notes 1.0",
        sample_count=1,
        opener=_RoutingOpener(routes),
        clock=_clock(),
    )[2]

    assert observation.functional_verdict is FunctionalVerdict.PASS


@pytest.mark.parametrize("content_type", [None, "text/html"])
def test_rest_rejects_missing_or_incorrect_content_type(content_type):
    routes = _web_routes()
    body = routes["http://product.test/?rest_route=/wp/v2/posts"]
    routes["http://product.test/?rest_route=/wp/v2/posts"] = (
        200,
        body,
        content_type,
    )

    observation = observe_corporate_web_application(
        "http://product.test",
        expected_release_title="Release notes 1.0",
        sample_count=1,
        opener=_RoutingOpener(routes),
        clock=_clock(),
    )[2]

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert "content type" in observation.error_message


def test_posts_require_top_level_list_and_exact_rendered_title():
    routes = _web_routes("Release notes 110")

    observation = observe_corporate_web_application(
        "http://product.test",
        expected_release_title="Release notes 1.0",
        sample_count=1,
        opener=_RoutingOpener(routes),
        clock=_clock(),
    )[2]

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert "Release notes 110" not in repr(observation)


@pytest.mark.parametrize(
    "document",
    [
        {"title": {"rendered": "Release notes 1.0"}},
        [{}],
        [{"title": None}],
        [{"title": {}}],
        [{"title": {"rendered": "different title"}}],
    ],
)
def test_posts_reject_invalid_structure_or_missing_expected_title(document):
    routes = _web_routes()
    routes["http://product.test/?rest_route=/wp/v2/posts"] = (
        200,
        json.dumps(document).encode(),
        "application/json",
    )

    observation = observe_corporate_web_application(
        "http://product.test",
        expected_release_title="Release notes 1.0",
        sample_count=1,
        opener=_RoutingOpener(routes),
        clock=_clock(),
    )[2]

    assert observation.functional_verdict is FunctionalVerdict.FAILURE


def test_posts_accept_expected_rendered_title():
    observation = observe_corporate_web_application(
        "http://product.test",
        expected_release_title="Release notes 1.0",
        sample_count=1,
        opener=_RoutingOpener(_web_routes()),
        clock=_clock(),
    )[2]

    assert observation.functional_verdict is FunctionalVerdict.PASS


def test_static_home_requires_every_approved_navigation_path():
    routes = _web_routes()
    routes["http://product.test/site/"] = b"Home"

    observation = observe_corporate_web_application(
        "http://product.test",
        expected_release_title="Release notes 1.0",
        sample_count=1,
        opener=_RoutingOpener(routes),
        clock=_clock(),
    )[4]

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert "navigation" in observation.error_message


def test_product_observations_round_trip_through_artifact_schema():
    observations = observe_service_http_endpoints(
        "http://product.test",
        "http://product.test:8080",
        sample_count=2,
        opener=_RoutingOpener(_service_routes()),
        clock=_clock(),
    )

    restored = deserialize_run(serialize_run(_run(observations)))

    assert restored.observations == observations


@pytest.mark.parametrize("sample_count", [True, 0, 1.5])
def test_invalid_sample_count_is_rejected(sample_count):
    with pytest.raises((TypeError, ValueError)):
        observe_service_http_endpoints(
            "http://product.test",
            "http://product.test:8080",
            sample_count=sample_count,
            opener=_RoutingOpener(_service_routes()),
        )


@pytest.mark.parametrize("timeout", [True, 0, 10.1, float("inf")])
def test_http_timeout_must_be_positive_and_at_most_ten_seconds(timeout):
    with pytest.raises((TypeError, ValueError)):
        observe_service_http_endpoints(
            "http://product.test",
            "http://product.test:8080",
            sample_count=1,
            timeout_seconds=timeout,
            opener=_RoutingOpener(_service_routes()),
        )


@pytest.mark.parametrize(
    "url",
    ["", "ftp://product.test", "http://user:password@product.test", "http://product.test/#fragment"],
)
def test_unsafe_or_invalid_base_urls_are_rejected(url):
    with pytest.raises(ValueError):
        observe_corporate_web_application(
            url,
            expected_release_title="Release notes 1.0",
            sample_count=1,
            opener=SimpleNamespace(),
        )


def test_live_guard_runs_before_any_http_or_ssh_operation(monkeypatch):
    module = _load_bdd_module()
    web_check = MagicMock()
    service_check = MagicMock()
    backend_check = MagicMock()
    page_delivery = MagicMock()
    monkeypatch.setattr(module, "observe_corporate_web_application", web_check)
    monkeypatch.setattr(module, "observe_service_http_endpoints", service_check)
    monkeypatch.setattr(module, "observe_backend_reachability", backend_check)
    monkeypatch.setattr(module, "observe_page_delivery", page_delivery)
    monkeypatch.delenv("OPENSTACK_PERF_RUN_LIVE", raising=False)

    with pytest.raises(pytest.skip.Exception):
        module.configured_web_application()
    with pytest.raises(pytest.skip.Exception):
        module.configured_application_services()

    web_check.assert_not_called()
    service_check.assert_not_called()
    backend_check.assert_not_called()
    page_delivery.assert_not_called()


def test_application_service_fixture_rejects_wrong_bastion(monkeypatch):
    module = _load_bdd_module()
    settings = {
        "OPENSTACK_PERF_RUN_LIVE": "1",
        "OPENSTACK_PERF_PRODUCT_BASE_URL": "http://product.test",
        "OPENSTACK_PERF_TOMCAT_BASE_URL": "http://product.test:8080",
        "OPENSTACK_PERF_APPLICATION_RELEASE": "release-1",
        "OPENSTACK_PERF_PRODUCT_BASTION": "wiki@unapproved",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="approved bastion"):
        module.configured_application_services()
