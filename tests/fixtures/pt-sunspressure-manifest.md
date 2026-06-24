# Pathway Manifest — pt-sunspressure (pressure-test fixture)

Tiny map used by the SunSponge pressure test lane. Locations are crafted so each
pathway resolves to a real example.com URL via the SunSponge UI-view mapping.

## Summary Counts

| Category | Count |
|---|---|
| **Total pathways** | 3 |
| **WIRED** | 2 |
| **UNWIRED** | 1 |

## 1. Pathways Table

| id | location | trigger | declared_intent | handler | downstream_call | expected_side_effect | evidence | status |
|---|---|---|---|---|---|---|---|---|
| `home-light` | RestedCaptureView.jsx:10 | `load` | Land on home view | `render` | `GET /` | Home page shown | RestedCaptureView.jsx:10 | WIRED |
| `about-page` | CatalogView.jsx:20 | `click` About | Open About page | `navigate` | `GET /about` | About page shown | CatalogView.jsx:20 | WIRED |
| `missing-route` | WorkbenchView.jsx:30 | `fetch` | Fetch route that doesn't exist | `fetch` | `GET /api/nope` | 404 expected | WorkbenchView.jsx:30 | UNWIRED |
