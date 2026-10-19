The quality response fixtures are actual Ark model outputs from the isolated completion-review experiments on 2026-10-20, not synthetic model replies.

- `quality-malformed-response.json`: an assessment of an incomplete saved portrait whose function arguments contained invalid JSON. The runtime must preserve the original response in the tool error and record no approval.
- `quality-local-edit-response.json`: a valid assessment of moving the original red circle 20 units right while preserving a blue rectangle.

Each file records its source database, task ID and the SHA-256 of the exact original function arguments. No requests are mocked by these parser tests. Real request, image delivery, review-cache and runtime completion checks are separately exercised by `scripts.evaluate_completion_review`.
# Commons metadata fixture

`commons-imageinfo.json` records an actual unauthenticated Wikimedia Commons Imageinfo/Search response fetched on 2026-10-20. The fixture includes the exact query URL, retrieval date and original response byte hash. It contains catalogue metadata rather than artwork image bytes or model replies. Tests exercise result ordering, declared attribution transport and rejection of non-image results; they do not treat a matching filename as proof of a suitable drawing pose. API documentation: https://www.mediawiki.org/wiki/API:Imageinfo.

