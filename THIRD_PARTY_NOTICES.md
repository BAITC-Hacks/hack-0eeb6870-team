# Third-party design sources

## Streamline SVG icons

The 19 icons bundled in `moneygraph/static/icons.js` were created by **[Streamline](https://streamlinehq.com)** and are licensed under **[Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)**. They are an intentionally small subset (fewer than 50 icons), not a redistributed complete asset library.

- Original publisher and license declaration: [Streamline free vectors](https://github.com/webalys-hq/streamline-vectors).
- Distribution and license metadata: [Iconify Streamline collection](https://icon-sets.iconify.design/streamline/) and [Iconify icon-set source](https://github.com/iconify/icon-sets/blob/master/json/streamline.json).
- Exact selected-asset distribution request: [Streamline SVG bodies from the Iconify API](https://api.iconify.design/streamline.json?icons=hierarchy-2,dashboard-3,layers-1,open-book,star-2,upload-box-1,download-box-1,magnifying-glass,interface-arrows-corner-up-right-keyboard-top-arrow-right-up,interface-arrows-button-right-arrow-right-keyboard,shield-check,interface-validation-check-circle-checkmark-addition-circle-success-check-validation-add-form,user-multiple-group,wave-signal,wallet,interface-arrows-button-down-arrow-down-keyboard,add-1,subtract-1,expand-window-2).
- Retrieved: September 23, 2026. API metadata: `lastModified: 1748325452`.

The SVG bodies are copied unchanged from this distribution. Iconify normalizes monochrome colors to `currentColor`; this application adds an SVG wrapper, display sizing, accessibility attributes, and local aliases. No external icon runtime, API token, or network request is needed to render them.

The interface should retain its visible **[Icons by Streamline](https://streamlinehq.com)** credit. Keep this notice and the license link with redistributed copies. Credit does not imply endorsement by Streamline.

### Local aliases

| UI alias | Streamline / Iconify source name |
| --- | --- |
| `network` | `hierarchy-2` |
| `grid` | `dashboard-3` |
| `layers` | `layers-1` |
| `book` | `open-book` |
| `sparkle` | `star-2` |
| `upload` | `upload-box-1` |
| `download` | `download-box-1` |
| `search` | `magnifying-glass` |
| `arrow-up-right` | `interface-arrows-corner-up-right-keyboard-top-arrow-right-up` |
| `chevron` | `interface-arrows-button-right-arrow-right-keyboard` |
| `shield` | `shield-check` |
| `circle-check` | `interface-validation-check-circle-checkmark-addition-circle-success-check-validation-add-form` |
| `users` | `user-multiple-group` |
| `activity` | `wave-signal` |
| `wallet` | `wallet` |
| `chevron-down` | `interface-arrows-button-down-arrow-down-keyboard` |
| `plus` | `add-1` |
| `minus` | `subtract-1` |
| `expand` | `expand-window-2` |

## 21st.dev design references

Public 21st.dev material was consulted as design inspiration for navigation and assistant UI patterns:

- [Shadcn sidebar collection](https://21st.dev/community/components/explore/shadcn-sidebar).
- [Sidebar patterns and states](https://docs.21st.dev/blog/react-sidebar-component-examples).
- [Agent Elements send button](https://21st.dev/community/components/21st.dev/send-button).

The authorized 21st MCP search also returned these composition references on September 23, 2026:

- [Dashboard Sidebar — Arun Dass](https://21st.dev/@arunjdass/components/dashboard-sidebar).
- [Advanced Stats — UI Layout](https://21st.dev/@uilayout.contact/components/advanced-stats).
- [InlineAnalyticsTable — Ruixen](https://21st.dev/@ruixen.ui/components/inline-analytics-table).

The application layout and its vanilla HTML/CSS/JavaScript implementation are authored for MoneyGraph. No 21st.dev component source code, marketplace preview media, or paid template is bundled. MCP search was used; paid code retrieval and hosted UI generation were not used. The authorized Streamline MCP search also confirmed free Network icons from the Core Line, Cyber Line, and Flex Line sets. The actual bundled SVG sources are listed above.
