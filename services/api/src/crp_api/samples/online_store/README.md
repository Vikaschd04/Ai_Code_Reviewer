# Online store (refactorX sample project)

A small web shop used to try refactorX: a Java order/payment backend and a TypeScript web
client. It contains **deliberate** problems so every review step has something to show:

- security flaws (SQL built from user input, command execution, weak hashing, a hard-coded
  encryption key, HTML injection, `eval`),
- bugs and reliability issues (string comparison with `==`, swallowed exceptions, unclosed
  resources, duplicate object keys, `NaN` comparison),
- vulnerable dependencies (log4j-core 2.14.1, lodash 4.17.15),
- a leaked (fake, never valid) access token in `web/src/config.js`.

Do not use this code in production.
