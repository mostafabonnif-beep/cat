# B-Roll API research notes

## Pexels official API

Source: [Pexels API documentation](https://www.pexels.com/api/documentation/)

The official documentation states that the API exposes photos and videos through a REST JSON API. Video search uses the current endpoint `https://api.pexels.com/v1/videos/search`, and requests authenticate through an `Authorization` header containing the user's API key. The documented request accepts a required `query` plus optional `orientation`, `page`, and `per_page` parameters; `per_page` is capped at 80.

The documentation says successful responses include rate-limit headers such as `X-Ratelimit-Limit`, `X-Ratelimit-Remaining`, and `X-Ratelimit-Reset`. It also requires a prominent link back to Pexels and recommends crediting photographers when possible. The implementation therefore keeps provider, photographer, photographer URL, source URL, and rate-limit metadata in the B-Roll plan and does not make network requests unless the user supplies a key.
