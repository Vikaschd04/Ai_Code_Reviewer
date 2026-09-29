/** A file location that keeps the file name readable: name (and line) first, folder muted. */
export function FileLocation({
  path,
  line,
  endLine,
  note,
}: {
  path: string;
  line?: number | null;
  endLine?: number | null;
  note?: string;
}) {
  const slash = path.lastIndexOf("/");
  const name = slash >= 0 ? path.slice(slash + 1) : path;
  const folder = slash >= 0 ? path.slice(0, slash) : "";
  const lines =
    line === null || line === undefined
      ? ""
      : `:${String(line)}${endLine && endLine !== line ? `–${String(endLine)}` : ""}`;
  return (
    <span className="file-loc" title={`${path}${lines}`}>
      <span className="file-name mono">
        {name}
        {lines}
        {note ? <span className="muted"> · {note}</span> : null}
      </span>
      {folder ? <span className="file-dir mono">{folder}</span> : null}
    </span>
  );
}
