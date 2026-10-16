Copied from `srt-gateway/web` at commit `8c386bc12cd7fd784a5b1e6632526140b096b8b8`.

Local changes in session 3.6: the destructive button uses the existing background
text token and keeps its fill on hover. Axe measured white on the copied red fill
at 4.17:1, below WCAG AA's 4.5:1. Dark text provides sufficient contrast without
introducing a new colour.
