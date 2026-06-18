# JamaClient reference (read-side)

A compact map of the `py_jama_rest_client.client.JamaClient` methods that come up when building trace matrices. Write-side methods (`post_*`, `put_*`, `patch_*`, `delete_*`) are omitted — they're not used in this workflow.

Load this file when you need the signature or return shape of a specific endpoint and don't want to scroll through 1,500 lines of client source.

## Construction

```python
JamaClient(
    host_domain,                                       # "https://org.jamacloud.com"
    credentials=("username|clientID", "password|secret"),
    api_version="/rest/v1/",                           # also: "/rest/latest/", "/rest/labs/"
    oauth=False,
    verify=True,
    allowed_results_per_page=20,                        # 1..50
)
```

Raises `APIException` if construction fails (usually an OAuth token fetch error). The default `allowed_results_per_page=20` is a common footgun — set it to 50 for any read-heavy work.

## Projects & structure

| Method | Returns | Notes |
|---|---|---|
| `get_projects()` | `list[dict]` | All projects the credentialed user can see. |
| `get_current_user()` | `dict` | Sanity-check the auth worked. |
| `get_users()` | `list[dict]` | All active users. |
| `get_available_endpoints()` | `list` | The API's root listing — handy for discovery. |

## Items

| Method | Returns | Notes |
|---|---|---|
| `get_items(project_id)` | `list[dict]` | Every item in a project. Heavy call. |
| `get_item(item_id)` | `dict` | One item. |
| `get_abstract_item(item_id)` | `dict` | Works for items, test plans, test cycles, test runs, attachments — anything with an ID. Prefer this over `get_item` when the ID's type is not known in advance. |
| `get_abstract_items(project=None, item_type=None, document_key=None, release=None, created_date=None, modified_date=None, last_activity_date=None, contains=None, sort_by=None)` | `list[dict]` | **The filtered query endpoint.** All list-valued params accept `list[int]` or `list[str]`. Use this for subset-by-type resolution. |
| `get_item_children(item_id)` | `list[dict]` | Direct children only, not recursive. |
| `get_item_versions(item_id)` | `list[dict]` | Version history. |
| `get_item_version(item_id, version_num)` | `dict` | One historical version. |

## Item types

| Method | Returns | Notes |
|---|---|---|
| `get_item_types()` | `list[dict]` | All item types across the instance. Each has `id`, `typeKey` (short code like `"REQ"`), `display` (human name). |
| `get_item_type(type_id)` | `dict` | Single item type by ID. |

Fetch once per process and cache. Item types rarely change.

## Relationships

| Method | Returns | Notes |
|---|---|---|
| `get_relationships(project_id)` | `list[dict]` | **The core endpoint for this skill.** Every relationship in the project. Paginates internally. |
| `get_relationship(relationship_id)` | `dict` | One relationship. Avoid in loops — use the bulk endpoint. |
| `get_relationship_types()` | `list[dict]` | All relationship types across the instance. |
| `get_relationship_type(type_id)` | `dict` | One relationship type by ID. |
| `get_items_upstream_relationships(item_id)` | `list[dict]` | Relationships where this item is the `toItem` (i.e. something upstream points at it). |
| `get_items_downstream_relationships(item_id)` | `list[dict]` | Relationships where this item is the `fromItem`. |
| `get_items_upstream_related(item_id)` | `list[dict]` | The *items* upstream of this one, not the relationships. |
| `get_items_downstream_related(item_id)` | `list[dict]` | The *items* downstream of this one. |

### Relationship dict shape

```python
{
    "id": 1234,
    "fromItem": 1001,
    "toItem": 2001,
    "relationshipType": 42,
    "suspect": False,
    "createdDate": "2024-...",
    "modifiedDate": "2024-...",
    "createdBy": 7,
    "modifiedBy": 7,
}
```

### Upstream/downstream semantics

- Relationship direction in Jama is `from → to`, where `from` is conceptually upstream.
- For a classic Requirements-verify-TestCases matrix, the relationship is `from=Requirement, to=TestCase`.
- "Upstream relationships" for a test case = relationships where the test case is the `toItem`.

## Baselines

| Method | Returns | Notes |
|---|---|---|
| `get_baselines(project_id)` | `list[dict]` | All baselines in a project. |
| `get_baseline(baseline_id)` | `dict` | One baseline. |
| `get_baselines_versioneditems(baseline_id)` | `list[dict]` | **Versioned items** in a baseline. Each entry's underlying item ID lives in the `item` field, not `id`. |
| *(not wrapped)* `GET /baselines/{id}/reviewlink` | `dict` or `None` | The only Review Center surface the REST API exposes. Returns `reviewKey`, `reviewName`, `url`, `revisionId`, `readAccess`, `deleted`. py-jama-rest-client does not wrap it; use `scripts/subset_resolvers.py::get_baseline_review_link`, which drops into the private `Core` object. See the Review Center section below. |

## Picklists, filters, tags

| Method | Returns | Notes |
|---|---|---|
| `get_pick_lists()` | `list[dict]` | All picklists. |
| `get_pick_list(pl_id)` | `dict` | One picklist. |
| `get_pick_list_options(pl_id)` | `list[dict]` | Options for a picklist. |
| `get_filter_results(filter_id, project_id=None)` | `list[dict]` | Results of a saved filter. Pass `project_id` only for filters with `projectScope=CURRENT`. |
| `get_tags(project)` | `list[dict]` | All tags in a project. |
| `get_tagged_items(tag_id)` | `list[dict]` | Items with a given tag. |

## Test management

| Method | Returns | Notes |
|---|---|---|
| `get_test_cycle(cycle_id)` | `dict` | One test cycle. |
| `get_testruns(test_cycle_id)` | `list[dict]` | Test runs in a cycle. |

## Exceptions

Class hierarchy — all subclass `APIException`:

| Exception | HTTP | Thrown when |
|---|---|---|
| `UnauthorizedException` | 401 | Bad creds or expired token. |
| `ResourceNotFoundException` | 404 | Bad ID or wrong host. |
| `AlreadyExistsException` | 400 (specific) | Duplicate on create — not relevant for reads. |
| `APIClientException` | 4xx (other) | Generic 4xx. |
| `TooManyRequestsException` | 429 | Rate limited. Back off and retry. |
| `APIServerException` | 5xx | Jama-side error. Retry may help. |
| `APIException` | — | Base class and catch-all. |

Every one carries `status_code` and `reason` attributes alongside the message, so generic handlers can introspect without parsing the message.

## Review Center

Review Center data is not exposed as a queryable REST resource. There is no `/reviews` top-level endpoint in `v1`, `latest`, or `labs`. You cannot list reviews, fetch review items, pull comments or decisions, or read participant status through the public API.

The one exception is baseline-linked review metadata:

```
GET /baselines/{baselineId}/reviewlink
→ { data: { reviewKey, reviewName, url, revisionId, readAccess, deleted } }
```

This returns a pointer to the review attached to a baseline — enough to surface a deep link back into the Jama UI, not enough to drive reporting. If a baseline has no attached review, the response's `data` carries `deleted: true` (or is empty); the wrapper helper returns `None` in that case.

`scripts/subset_resolvers.py::get_baseline_review_link` wraps this endpoint. It reaches into the client's private `Core` object (see below) because py-jama-rest-client itself does not expose a method for `/reviewlink`.

When users ask for review data that goes beyond the link pointer, the real-world answers are: scrape the Review Center UI (brittle, breaks on UI changes, hard under SSO), query the Jama database directly (self-hosted only and unsupported), or wait on the open feature request in the Jama Software User Community. Flag this before they build a dependency.

## Hidden core

The client keeps a private `Core` object (`client._JamaClient__core` after Python name-mangling) exposing `get`, `post`, `put`, `patch`, `delete` against arbitrary resource paths. Use it as an escape hatch for endpoints the client doesn't wrap (Review Center, activities, occupied roles, etc.) — but prefer opening an issue on `py-jama-rest-client` or using `requests` directly for anything non-trivial. The name-mangled access is fragile and will break if the maintainer renames the attribute.

```python
# Escape hatch — works but ugly. Prefer a separate requests call.
raw = client._JamaClient__core.get("reviews")
reviews = raw.json()["data"]
```

## Pagination internals

`JamaClient.__get_all` loops over pages until `startIndex + pageSize >= totalResults`. There's no way to stream results; everything is materialized in memory. For projects with hundreds of thousands of relationships, this can cost real memory. If that's a concern, copy the pattern from `__get_page` and iterate manually.
