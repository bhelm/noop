# Steps view (Android and iOS)

The steps detail has one page title and localized English/German visible labels, including ranges,
loading/empty states, calendar bucket labels and units. The shared BarChart supports immediate finger
selection and horizontal scrubbing for all selectable callers. While held, other bars dim and a dashed guide extends above the
selected bar. Date and value remain above the plot in a larger readout. The zero-based side axis has
5,000-step ticks and rounds the maximum upward to the next occupied 5,000-step block (minimum 5,000).
Daily values are printed above each bar for one- and two-week windows. Existing density buckets and
calendar filtering are preserved. Axis step, above-bar values and a larger two-line readout are optional
shared-widget parameters enabled by the steps detail; other callers retain their default sizing/scale.

The Your Cards editor offers a new **30-day step average** card. It is absent from the default order,
so fresh installs and existing layouts keep it off until explicitly enabled. Its arithmetic mean uses
the selected day and preceding 29 calendar days. Only recorded days enter the divisor; missing days
are not converted to zero. A recorded zero is included. Coverage is shown as N of 30 days. The tile
uses the same per-day source precedence as the detail (strap, imported, estimated) and opens that detail.

The Your Cards editor uses its complete registry to offer the default-off average card.
Regression tests cover its discoverability and saved selection. The general Key Metrics editor
retains its original opening and reset behavior; the separate picker correction has been removed.

Validation: no emulator or physical-device touch interaction has been exercised;
finger tracking, long holds and readability remain manual device acceptance items.

## iOS parity

The iOS metric detail configures the shared `TrendChart` with the same 5,000-step axis,
weekly/two-weekly value labels and a larger persistent selected date/value readout. A zero-distance
drag selects immediately and scrubs horizontally; holding dims the other bars and draws a vertical
dashed position guide that remains on the selected bar after release. Bars are rectangular with a subtle 2-point corner radius. Gray horizontal
dashed grid lines appear only at interior 5,000-step ticks (not zero or the upper axis limit).
Other trend callers retain their existing scale/readout defaults. Steps no longer repeats category
and metric headers below the navigation title; the chart is labelled Historical trend.

Classic and Liquid Today both offer the optional `stepsAverage30` tile through the existing editor,
excluded from defaults. Its selected-day task queries the exact 30-calendar-day window, using the
same daily resolver as its explicitly combined steps detail (strap, phone, estimate). Existing
WHOOP, Apple, Xiaomi and estimate catalog entries remain source-specific. Coverage counts recorded
zeroes, excludes missing/invalid readings and changes
when the selected day or repository refresh changes. The tile opens the steps detail.

iOS validation on this Linux host is limited to source/diff checks and localization JSON checks.
XCTest cases cover calendar boundaries, zero/missing observations, opt-in persistence and axis
rounding, but cannot be executed here: Swift/Xcode and the iOS SDK are unavailable. An Xcode build
and simulator/device touch acceptance are still required; no iOS binary was produced.
