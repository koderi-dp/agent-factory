Supplemental upstream licenses for npm archives missing root license text:

- `headless-tree.txt`: @headless-tree/core and @headless-tree/react 1.7.0,
  https://github.com/lukasbach/headless-tree/blob/11b0bb0859c4a0e6bc7c41f74aa3a5096c1a69df/LICENSE
  (npm release gitHead).
- `react-remove-scroll-bar.txt`: react-remove-scroll-bar 2.3.8 (MIT in package metadata),
  https://github.com/theKashey/react-remove-scroll-bar/blob/8ca9ba5ea52de03308fe8ced94f7b159a44d28ff/LICENSE.
  The npm gitHead is unavailable upstream; this is the repository's current license.
- `victory-vendor.txt`: victory-vendor 36.9.2,
  https://github.com/FormidableLabs/victory/blob/v36.9.2/LICENSE.txt.
  The generator also includes the nested vendored d3 licenses from the npm archive.

The generator includes every installed production dependency from package-lock.json,
including unused dependencies, and fails if a dependency has no license text. The
version-specific supplements must be reviewed when those packages are upgraded.
The copied theme and UI license is preserved in src/ui/LICENSE.

Copied shadcn primitives license: https://raw.githubusercontent.com/shadcn-ui/ui/d2fba50ee5b9113008b5bb908563527b7efd2f27/LICENSE.md.
