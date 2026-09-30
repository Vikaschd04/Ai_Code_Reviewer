/**
 * Reviewer-facing vocabulary. Screens show these plain-language names; internal identifiers
 * (engine ids, rule ids, versions, hashes) appear only inside "Technical details" sections.
 * See docs/UI_SPEC.md "Reviewer-first presentation".
 */

export interface CheckInfo {
  name: string;
  description: string;
  /** The analyzer behind the check, shown only in technical details. */
  tool: string;
}

export const CHECKS: Record<string, CheckInfo> = {
  structure: {
    name: "Code structure",
    description: "Reads every supported file and finds classes, functions and imports.",
    tool: "Tree-sitter",
  },
  graph: {
    name: "Architecture map",
    description: "Maps modules, files and how they depend on each other.",
    tool: "refactorX graph",
  },
  pmd: {
    name: "Java quality",
    description: "Bugs, error-prone patterns and risky code in Java.",
    tool: "PMD",
  },
  eslint: {
    name: "JavaScript & TypeScript quality",
    description: "Bugs and unsafe patterns in JavaScript and TypeScript.",
    tool: "ESLint",
  },
  opengrep: {
    name: "Security patterns",
    description: "Injection, weak cryptography, unsafe HTML and hard-coded secrets in code.",
    tool: "Opengrep",
  },
  trivy: {
    name: "Dependencies & secrets",
    description: "Known vulnerabilities in dependencies and leaked credentials in files.",
    tool: "Trivy",
  },
};

export const CHECK_ORDER = ["structure", "graph", "pmd", "eslint", "opengrep", "trivy"];
export const FINDING_CHECKS = ["pmd", "eslint", "opengrep", "trivy"];

export function checkName(engine: string): string {
  return CHECKS[engine]?.name ?? engine;
}

export const CATEGORY_LABELS: Record<string, string> = {
  security: "Security",
  dependencies: "Vulnerable dependencies",
  correctness: "Bugs",
  reliability: "Reliability",
  performance: "Performance",
  maintainability: "Maintainability",
  coding_standards: "Coding standards",
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category.replaceAll("_", " ");
}

/** Plain explanation of why a file was not reviewed (snapshot dispositions and reasons). */
export function fileReason(reason: string | null | undefined): string {
  if (!reason) return "";
  const known: Record<string, string> = {
    secret_candidate: "Looks like a secrets file; not stored",
    dependency_vendor: "Downloaded dependency code",
    build_output: "Generated build output",
    generated_minified: "Generated or minified code",
    vcs_metadata: "Version-control data",
    ide_metadata: "Editor settings",
    tool_cache: "Tool cache",
    tooling_cache: "Tool cache",
    binary: "Binary file",
    binary_content: "Binary file",
    nested_archive: "Archive inside the upload",
    text_too_large: "Too large to review",
  };
  return known[reason] ?? reason.replaceAll("_", " ");
}

const LANGUAGE_LABELS: Record<string, string> = {
  java: "Java",
  javascript: "JavaScript",
  typescript: "TypeScript",
  json: "JSON",
  xml: "XML",
  yaml: "YAML",
  markdown: "Markdown",
  html: "HTML",
  css: "CSS",
  scss: "SCSS",
  sql: "SQL",
  shell: "Shell",
  properties: "Properties",
  groovy: "Groovy",
  kotlin: "Kotlin",
  python: "Python",
  apex: "Apex",
  text: "Text",
};

export function languageLabel(language: string | null | undefined): string {
  if (!language) return "—";
  return LANGUAGE_LABELS[language] ?? language.charAt(0).toUpperCase() + language.slice(1);
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}
