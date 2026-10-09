// Tier 0 TypeScript type-check (P09; ADR 0017). The platform's pinned TypeScript compiler reads the
// project's files as data and reports diagnostics. Nothing from the project is executed or
// imported: no compiler plugins or transformers, no project-installed TypeScript, no automatic
// @types, and every file access is confined to the checked folder plus the compiler's own
// standard library. A tsconfig.json in the folder is read as data; options that could load code,
// reach outside the folder or write files are switched off.
//
// Usage: node typecheck.mjs <root> <files.json> <report.json>
// Exit: 0 = report written; anything else = runner failure.
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const [root, listFile, reportFile] = process.argv.slice(2);
if (!root || !listFile || !reportFile) {
  process.stderr.write("usage: typecheck.mjs <root> <files.json> <report.json>\n");
  process.exit(2);
}
const base = fs.realpathSync(root);
const libDir = path.dirname(ts.getDefaultLibFilePath({}));
const files = JSON.parse(fs.readFileSync(listFile, "utf8")).map((f) => path.join(base, f));

function inside(file) {
  const resolved = path.resolve(file);
  return resolved === base || resolved.startsWith(base + path.sep) || resolved.startsWith(libDir + path.sep);
}

const notes = [];
let options = {};
const configPath = path.join(base, "tsconfig.json");
if (fs.existsSync(configPath)) {
  const read = ts.readConfigFile(configPath, (p) => (inside(p) ? fs.readFileSync(p, "utf8") : ""));
  if (read.error) {
    notes.push("tsconfig.json could not be read, so default options were used.");
  } else {
    const host = {
      useCaseSensitiveFileNames: true,
      readDirectory: () => [],
      fileExists: (p) => inside(p) && fs.existsSync(p),
      readFile: (p) => (inside(p) && fs.existsSync(p) ? fs.readFileSync(p, "utf8") : undefined),
    };
    const parsed = ts.parseJsonConfigFileContent(read.config, host, base);
    options = parsed.options;
    if (read.config.extends) {
      notes.push("tsconfig.json extends other configuration; only files inside the upload were read.");
    }
    if (read.config.compilerOptions && read.config.compilerOptions.plugins) {
      notes.push("Compiler plugins listed in tsconfig.json were not loaded.");
    }
  }
}
for (const key of ["outDir", "outFile", "rootDir", "tsBuildInfoFile", "declarationDir"]) {
  delete options[key];
}
options = {
  ...options,
  noEmit: true,
  skipLibCheck: true,
  types: [],
  typeRoots: [],
  plugins: [],
  incremental: false,
  composite: false,
  declaration: false,
  sourceMap: false,
  allowJs: false,
  checkJs: false,
};

const real = ts.createCompilerHost(options, true);
const host = {
  ...real,
  fileExists: (p) => inside(p) && real.fileExists(p),
  readFile: (p) => (inside(p) ? real.readFile(p) : undefined),
  directoryExists: (p) => inside(p) && (real.directoryExists ? real.directoryExists(p) : true),
  getDirectories: (p) => (inside(p) && real.getDirectories ? real.getDirectories(p) : []),
  realpath: (p) => p,
  getSourceFile: (p, language, onError, create) =>
    inside(p) ? real.getSourceFile(p, language, onError, create) : undefined,
  writeFile: () => {},
};

// Errors caused by packages that are not installed (uploads never contain node_modules): counted,
// not reported as problems of the project.
const DEPENDENCY = new Set([2305, 2307, 2688, 2792, 7016, 7026]);
const program = ts.createProgram({ rootNames: files, options, host });
const diagnostics = [...program.getSyntacticDiagnostics(), ...program.getSemanticDiagnostics()];
const out = [];
let unresolved = 0;
for (const d of diagnostics) {
  if (d.category !== ts.DiagnosticCategory.Error) continue;
  if (DEPENDENCY.has(d.code)) {
    unresolved += 1;
    continue;
  }
  if (!d.file || !inside(d.file.fileName) || d.file.fileName.startsWith(libDir + path.sep)) continue;
  const { line, character } = d.file.getLineAndCharacterOfPosition(d.start ?? 0);
  out.push({
    path: path.relative(base, d.file.fileName).split(path.sep).join("/"),
    line: line + 1,
    column: character + 1,
    code: `TS${d.code}`,
    message: ts.flattenDiagnosticMessageText(d.messageText, " ").slice(0, 500),
  });
}
fs.writeFileSync(
  reportFile,
  JSON.stringify({ typescriptVersion: ts.version, diagnostics: out, unresolvedImports: unresolved, notes }),
);
