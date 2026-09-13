# Android steps view

The steps detail has one page title and localized English/German visible labels, including ranges,
loading/empty states, calendar bucket labels and units. A dedicated chart supports immediate finger
selection and horizontal scrubbing. While held, other bars dim and a dashed guide extends above the
selected bar. Date and value remain above the plot in a larger readout. The zero-based side axis has
5,000-step ticks and rounds the maximum upward to the next occupied 5,000-step block (minimum 5,000).
Daily values are printed above each bar for one- and two-week windows. Existing density buckets and
calendar filtering are preserved.

The Key Metrics editor offers a new **30-day step average** tile. It is absent from the default order,
so fresh installs and existing layouts keep it off until explicitly enabled. Its arithmetic mean uses
the selected day and preceding 29 calendar days. Only recorded days enter the divisor; missing days
are not converted to zero. A recorded zero is included. Coverage is shown as N of 30 days. The tile
uses the same per-day source precedence as the detail (strap, imported, estimated) and opens that detail.

Validation: final staging APK build is performed from an integration worktree preserving the existing
main/translation integration. No emulator or physical-device touch interaction has been exercised;
finger tracking, long holds and readability remain manual device acceptance items.
