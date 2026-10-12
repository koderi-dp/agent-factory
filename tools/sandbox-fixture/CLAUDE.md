# Todo app

A single-page todo list in plain HTML, CSS and JavaScript (ES modules, no build step, no dependencies). Todos are kept in `localStorage`.

## Paths

- `index.html`: the page.
- `src/todos.js`: pure functions over the todo list (add, toggle, remove). No DOM access. Every list change goes through here.
- `src/app.js`: renders the list and wires up the events. Reads and writes `localStorage` under the key `todos`.
- `src/style.css`: all styles. Colours, spacing and radii are CSS custom properties on `:root`; use them, don't hard-code values.
- `test/`: `node:test` tests for `src/todos.js`.

## Setup

Nothing to install. Needs Node 20+ and Python 3.

## Dev server

```bash
python -m http.server <port> --bind 127.0.0.1
```

from the repo root, then open `http://127.0.0.1:<port>/`. The default port is 8000. There is no backend.

## Validation

```bash
node --test
```

All tests must pass. There is no linter; match the existing formatting (2-space indent, no semicolons, single quotes).

## Conventions

- Logic goes in `src/todos.js` with a test in `test/todos.test.js`; `src/app.js` only renders and wires events.
- Buttons are real `<button>` elements with visible text.
- Commit messages are short, imperative, one line.
