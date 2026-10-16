/** The same settings Pylon uses, so code moves between the two without reformatting. */
/** @type {import("prettier").Config} */
const config = {
  semi: true,
  singleQuote: false,
  trailingComma: 'all',
  printWidth: 100,
  overrides: [
    {
      files: ['*.js', '*.mjs', '*.cjs'],
      options: {
        semi: false,
        singleQuote: true,
        tabWidth: 2,
        printWidth: 80,
        trailingComma: 'es5',
      },
    },
  ],
}

export default config
