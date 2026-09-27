// Usage: node run.mjs <snapshot-root> <file-list.json> <output.json>
// Lints exactly the listed files (no globbing, no project config lookup, no inline config)
// and writes a normalized JSON report. Exit 0 = report written; 2 = runner failure.
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { ESLint } from "eslint";

const here = path.dirname(fileURLToPath(import.meta.url));

async function main() {
  const [root, listFile, outFile] = process.argv.slice(2);
  if (!root || !listFile || !outFile) {
    throw new Error("usage: run.mjs <root> <file-list.json> <output.json>");
  }
  const files = JSON.parse(await readFile(listFile, "utf8"));
  if (!Array.isArray(files)) throw new Error("file list must be a JSON array");
  const eslint = new ESLint({
    cwd: root,
    overrideConfigFile: path.join(here, "trusted.config.mjs"),
    allowInlineConfig: false,
    warnIgnored: true,
    errorOnUnmatchedPattern: false,
    globInputPaths: false,
    cache: false,
  });
  const absolute = files.map((file) => path.join(root, file));
  const results = await eslint.lintFiles(absolute);
  const report = {
    eslintVersion: ESLint.version,
    results: results.map((result) => ({
      path: path.relative(root, result.filePath).split(path.sep).join("/"),
      fatalErrorCount: result.fatalErrorCount,
      messages: result.messages.map((m) => ({
        ruleId: m.ruleId,
        severity: m.severity,
        fatal: m.fatal === true,
        message: m.message,
        line: m.line,
        column: m.column,
        endLine: m.endLine ?? null,
        endColumn: m.endColumn ?? null,
      })),
    })),
  };
  await writeFile(outFile, JSON.stringify(report));
}

main().catch((error) => {
  process.stderr.write(`eslint-runner: ${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(2);
});
