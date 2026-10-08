/** Source editor and comparison views (CodeMirror 6, MIT). Loaded only on workspace and compare
 * pages. Code is always inert text: nothing in a file is executed or rendered as HTML. Colours
 * come from the design tokens, so both themes work. */
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import {
  bracketMatching,
  HighlightStyle,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import { MergeView, unifiedMergeView } from "@codemirror/merge";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import {
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { useEffect, useRef } from "react";

const highlight = HighlightStyle.define([
  { tag: [tags.keyword, tags.modifier, tags.operatorKeyword], color: "var(--code-keyword)" },
  { tag: [tags.string, tags.special(tags.string), tags.regexp], color: "var(--code-string)" },
  { tag: [tags.comment, tags.meta], color: "var(--code-comment)", fontStyle: "italic" },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: "var(--code-number)" },
  { tag: [tags.typeName, tags.className, tags.namespace], color: "var(--code-type)" },
  { tag: [tags.tagName, tags.attributeName], color: "var(--code-keyword)" },
  {
    tag: [tags.function(tags.variableName), tags.definition(tags.variableName)],
    color: "var(--code-function)",
  },
  { tag: tags.invalid, color: "var(--bad)" },
]);

const theme = EditorView.theme({
  "&": {
    backgroundColor: "var(--surface-2)",
    color: "var(--text)",
    fontSize: "var(--text-sm)",
    borderRadius: "var(--radius-md)",
    border: "1px solid var(--border)",
  },
  "&.cm-focused": { outline: "none", boxShadow: "var(--focus-ring)" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "var(--leading-sm)" },
  ".cm-content": { caretColor: "var(--accent)" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)" },
  ".cm-gutters": {
    backgroundColor: "var(--surface-3)",
    color: "var(--text-muted)",
    border: "none",
    borderRight: "1px solid var(--border)",
  },
  ".cm-activeLine": { backgroundColor: "var(--accent-subtle)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--accent-subtle)", color: "var(--text)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
    backgroundColor: "var(--accent-border)",
  },
  ".cm-searchMatch": { backgroundColor: "var(--warn-bg)", outline: "1px solid var(--warn)" },
  ".cm-panels": { backgroundColor: "var(--surface)", color: "var(--text)" },
  ".cm-panels.cm-panels-bottom": { borderTop: "1px solid var(--border)" },
  ".cm-textfield, .cm-button": { fontSize: "var(--text-xs)" },
  ".cm-changedLine": { backgroundColor: "color-mix(in srgb, var(--ok) 12%, transparent)" },
  ".cm-changedText": { background: "color-mix(in srgb, var(--ok) 30%, transparent)" },
  ".cm-deletedChunk": { backgroundColor: "color-mix(in srgb, var(--bad) 12%, transparent)" },
  ".cm-deletedChunk .cm-deletedText, del": {
    background: "color-mix(in srgb, var(--bad) 30%, transparent)",
  },
  ".cm-collapsedLines": {
    backgroundColor: "var(--surface-3)",
    color: "var(--text-muted)",
    fontFamily: "var(--font-sans)",
    fontSize: "var(--text-xs)",
    padding: "var(--space-1) var(--space-3)",
  },
  ".cm-changeGutter": { width: "3px" },
  ".cm-changedLineGutter": { backgroundColor: "var(--ok)" },
  ".cm-deletedLineGutter": { backgroundColor: "var(--bad)" },
});

/** Syntax colouring only (Apex uses the Java grammar); unknown files are plain text. Grammars
 * load on demand: the file shows at once and gains colours when its grammar arrives. */
export function languageLoader(path: string): (() => Promise<Extension>) | null {
  const name = path.toLowerCase();
  const ext = name.slice(name.lastIndexOf(".") + 1);
  if (["java", "cls", "trigger", "apex"].includes(ext)) {
    return () => import("@codemirror/lang-java").then((m) => m.java());
  }
  if (["js", "mjs", "cjs", "jsx", "ts", "mts", "cts", "tsx"].includes(ext)) {
    const typescript = ext.includes("t");
    const jsx = ext.endsWith("x");
    return () =>
      import("@codemirror/lang-javascript").then((m) => m.javascript({ typescript, jsx }));
  }
  if (ext === "json") return () => import("@codemirror/lang-json").then((m) => m.json());
  if (["xml", "page", "component", "cmp", "app", "evt", "design"].includes(ext)) {
    return () => import("@codemirror/lang-xml").then((m) => m.xml());
  }
  return null;
}

const language = new Compartment();

/** Load the file's grammar into the given editors (ignored once they are destroyed). */
function colour(path: string, views: EditorView[]): () => void {
  let live = true;
  const load = languageLoader(path);
  if (load) {
    load().then(
      (extension) => {
        if (!live) return;
        for (const view of views) view.dispatch({ effects: language.reconfigure(extension) });
      },
      () => undefined, // plain text is a complete fallback
    );
  }
  return () => {
    live = false;
  };
}

function base(label: string): Extension[] {
  return [
    lineNumbers(),
    highlightActiveLineGutter(),
    drawSelection(),
    highlightActiveLine(),
    bracketMatching(),
    search({ top: false }),
    highlightSelectionMatches(),
    syntaxHighlighting(highlight),
    language.of([]),
    theme,
    EditorView.contentAttributes.of({ "aria-label": label }),
  ];
}

export interface CodeEditorProps {
  path: string;
  value: string;
  onChange: (value: string) => void;
  onSave?: () => void;
  readOnly?: boolean;
  /** 1-based line to scroll to and select when the editor opens. */
  line?: number | null;
}

/** An editable file. Remount (React key) to load another file or revision. */
export function CodeEditor({
  path,
  value,
  onChange,
  onSave,
  readOnly = false,
  line,
}: CodeEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const change = useRef(onChange);
  const save = useRef(onSave);
  useEffect(() => {
    change.current = onChange;
    save.current = onSave;
  });

  useEffect(() => {
    if (!host.current) return undefined;
    const view = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          ...base(`Contents of ${path}`),
          history(),
          indentOnInput(),
          keymap.of([
            {
              key: "Mod-s",
              preventDefault: true,
              run: () => {
                save.current?.();
                return true;
              },
            },
            ...defaultKeymap,
            ...historyKeymap,
            ...searchKeymap,
            indentWithTab,
          ]),
          EditorState.readOnly.of(readOnly),
          EditorView.editable.of(!readOnly),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) change.current(update.state.doc.toString());
          }),
        ],
      }),
    });
    if (line && line >= 1 && line <= view.state.doc.lines) {
      const target = view.state.doc.line(line);
      view.dispatch({
        selection: { anchor: target.from, head: target.to },
        effects: EditorView.scrollIntoView(target.from, { y: "center" }),
      });
    }
    const stop = colour(path, [view]);
    return () => {
      stop();
      view.destroy();
    };
    // The editor owns its document after creation; the parent remounts it for a new file.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div ref={host} className="code-editor" data-testid="code-editor" />;
}

export interface CompareProps {
  path: string;
  before: string;
  after: string;
  layout: "side" | "inline";
}

const COLLAPSE = { margin: 3, minSize: 6 };

/** Read-only comparison of two versions of a file (older left or above, newer right or below). */
export function CompareView({ path, before, after, layout }: CompareProps) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!host.current) return undefined;
    const readOnly = [EditorState.readOnly.of(true), EditorView.editable.of(false)];
    if (layout === "side") {
      const view = new MergeView({
        parent: host.current,
        a: { doc: before, extensions: [...base(`Before: ${path}`), ...readOnly] },
        b: { doc: after, extensions: [...base(`After: ${path}`), ...readOnly] },
        collapseUnchanged: COLLAPSE,
        gutter: true,
      });
      const stop = colour(path, [view.a, view.b]);
      return () => {
        stop();
        view.destroy();
      };
    }
    const view = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: after,
        extensions: [
          ...base(`Changes to ${path}`),
          ...readOnly,
          unifiedMergeView({
            original: before,
            mergeControls: false,
            collapseUnchanged: COLLAPSE,
            gutter: true,
          }),
        ],
      }),
    });
    const stop = colour(path, [view]);
    return () => {
      stop();
      view.destroy();
    };
  }, [path, before, after, layout]);

  return (
    <div
      ref={host}
      className={`compare compare-${layout}`}
      data-testid="compare-view"
      data-layout={layout}
    />
  );
}
