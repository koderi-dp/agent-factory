import type { FeatureImplementation } from "@headless-tree/core";

/**
 * Replaces headless-tree's `updateDomFocus`, which calls `element.focus()`
 * without `preventScroll` (the browser then scrolls the row on its own, outside
 * the app's reveal logic) and, if the row is not mounted within 500 ms, moves
 * focus to the first row — jumping the list to the top. Register it last.
 */
export const preventScrollFocusFeature: FeatureImplementation = {
  key: "prevent-scroll-focus",
  treeInstance: {
    updateDomFocus: ({ tree }) => {
      requestAnimationFrame(() => {
        tree.getFocusedItem()?.getElement()?.focus({ preventScroll: true });
      });
    },
  },
};
