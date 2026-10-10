/** NFR readiness (P12): the non-functional requirements questionnaire answered for this project —
 * what the code shows, what needs work, and what the team states. Plain status first; evidence,
 * issues and editing one click away. */
import { useState } from "react";

import {
  describeError,
  type NfrAssessment,
  type NfrProfileDocument,
  type NfrQuestion,
} from "../api/client";
import { fetchNfr, nfrExportUrl, saveNfrProfile } from "../api/endpoints";
import { Alert, Disclosure, Loading } from "../components/Common";
import { Icon, type IconName } from "../components/Icon";
import { SeverityChip } from "../components/Severity";
import { formatRelative } from "../lib/format";
import { plural } from "../lib/labels";
import { useAsync } from "../lib/useAsync";

const ORDER = [
  "needs_work",
  "needs_input",
  "evidence",
  "answered",
  "not_applicable",
  "not_checked",
] as const;
const TONE: Record<string, { tone: string; icon: IconName }> = {
  needs_work: { tone: "warn", icon: "alert" },
  needs_input: { tone: "live", icon: "info" },
  evidence: { tone: "ok", icon: "check" },
  answered: { tone: "neutral", icon: "check" },
  not_applicable: { tone: "neutral", icon: "x" },
  not_checked: { tone: "neutral", icon: "info" },
};

type Answers = NonNullable<NfrProfileDocument["answers"]>;

function StatusChip({ status, labels }: { status: string; labels: Record<string, string> }) {
  const look = TONE[status] ?? { tone: "neutral", icon: "info" as IconName };
  return (
    <span className={`badge badge-${look.tone}`} data-status={status}>
      <Icon name={look.icon} size={12} /> {labels[status] ?? status}
    </span>
  );
}

function location(path: string, line: number | null): string {
  return line ? `${path}:${String(line)}` : path;
}

function EvidenceList({ items, testId }: { items: NfrQuestion["evidence"]; testId: string }) {
  return (
    <ul className="stack stack-xs plain-list" data-testid={testId}>
      {items.map((item) => (
        <li key={item.signal} className="stack stack-xs">
          <span className="small">
            <strong>{item.label}</strong>
            {item.count > item.locations.length ? ` · ${plural(item.count, "place")}` : ""}
          </span>
          {item.locations.map((where) => (
            <span
              key={`${where.path}:${String(where.line)}:${where.detail ?? ""}`}
              className="mono small secondary wrap-anywhere"
            >
              {location(where.path, where.line)}
              {where.detail ? ` — ${where.detail}` : ""}
            </span>
          ))}
        </li>
      ))}
    </ul>
  );
}

function valueText(name: string, value: unknown, data: NfrAssessment): string {
  const spec = data.targets.find((t) => t.name === name);
  const shown = Array.isArray(value) ? value.join(", ") : String(value);
  const label = spec?.label ?? (name === "regulations" ? "Regulations" : "Platforms");
  return `${label}: ${shown}${spec?.unit ? ` ${spec.unit}` : ""}`;
}

function AnswerEditor({
  question,
  data,
  onSaved,
}: {
  question: NfrQuestion;
  data: NfrAssessment;
  onSaved: (next: NfrAssessment) => void;
}) {
  const [text, setText] = useState(question.answer?.text ?? "");
  const [notApplicable, setNotApplicable] = useState(question.answer?.not_applicable ?? false);
  const [reason, setReason] = useState(question.answer?.reason ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = () => {
    const others: Answers = Object.fromEntries(
      Object.entries(data.profile.answers ?? {}).filter(([key]) => key !== question.id),
    );
    const answers: Answers =
      text.trim() || notApplicable
        ? {
            ...others,
            [question.id]: {
              text: text.trim() || null,
              not_applicable: notApplicable,
              reason: notApplicable ? reason.trim() || null : null,
            },
          }
        : others;
    setBusy(true);
    setError(null);
    saveNfrProfile(
      data.project_id,
      { ...data.profile, answers },
      data.profile_version,
      `Answer: ${question.id}`,
    ).then(
      (next) => {
        setBusy(false);
        onSaved(next);
      },
      (caught: unknown) => {
        setBusy(false);
        setError(describeError(caught));
      },
    );
  };
  const id = `answer-${question.id}`;
  return (
    <div className="stack stack-sm" data-testid={`nfr-answer-${question.id}`}>
      <div className="field">
        <label htmlFor={id}>Your team&apos;s answer</label>
        <textarea
          id={id}
          rows={3}
          maxLength={2000}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
        />
      </div>
      <label className="checkbox-row small">
        <input
          type="checkbox"
          checked={notApplicable}
          onChange={(event) => {
            setNotApplicable(event.target.checked);
          }}
        />
        Does not apply to this system
      </label>
      {notApplicable ? (
        <div className="field">
          <label htmlFor={`${id}-reason`}>Why it does not apply</label>
          <input
            id={`${id}-reason`}
            type="text"
            maxLength={300}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
        </div>
      ) : null}
      <div className="row">
        <button type="button" className="btn btn-primary" onClick={save} disabled={busy}>
          <Icon name="check" size={15} /> {busy ? "Saving…" : "Save answer"}
        </button>
        <span className="hint">Saved as a new version with your name; shown as attested.</span>
      </div>
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </div>
  );
}

function QuestionItem({
  question,
  data,
  projectId,
  onSaved,
}: {
  question: NfrQuestion;
  data: NfrAssessment;
  projectId: string;
  onSaved: (next: NfrAssessment) => void;
}) {
  const values = Object.entries(question.values);
  return (
    <li data-testid={`nfr-q-${question.id}`}>
      <Disclosure
        summary={
          <span className="row">
            <StatusChip status={question.status} labels={data.status_labels} />
            <span>{question.text}</span>
          </span>
        }
      >
        <div className="stack stack-sm">
          <p className="small secondary">{question.help}</p>
          {question.gaps.open > 0 ? (
            <div className="stack stack-xs" data-testid="nfr-gaps">
              <h4 className="subheading">Needs work</h4>
              <p className="small">
                {plural(question.gaps.open, "open issue")} affect this question
                {question.gaps.accepted > 0
                  ? `, plus ${plural(question.gaps.accepted, "accepted risk")}`
                  : ""}
                .{" "}
                <a href={`#/projects/${projectId}?tab=issues`}>
                  See issues <Icon name="arrow" size={13} />
                </a>
              </p>
              <ul className="stack stack-xs plain-list">
                {question.gaps.top.map((issue) => (
                  <li key={issue.id} className="small">
                    <SeverityChip severity={issue.severity} /> {issue.title}{" "}
                    <span className="mono secondary wrap-anywhere">{issue.path}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {question.evidence.length > 0 ? (
            <div className="stack stack-xs">
              <h4 className="subheading">What the code shows</h4>
              <EvidenceList items={question.evidence} testId="nfr-evidence" />
            </div>
          ) : null}
          {question.context.length > 0 ? (
            <div className="stack stack-xs">
              <h4 className="subheading">In the upload, not checked yet</h4>
              <EvidenceList items={question.context} testId="nfr-context" />
            </div>
          ) : null}
          {values.length > 0 ? (
            <p className="small">
              <strong>Your targets:</strong>{" "}
              {values.map(([name, value]) => valueText(name, value, data)).join("; ")}
            </p>
          ) : null}
          {data.can_edit ? (
            <AnswerEditor
              key={`${question.id}:${String(data.profile_version)}`}
              question={question}
              data={data}
              onSaved={onSaved}
            />
          ) : question.answer ? (
            <p className="small">
              <strong>Your team&apos;s answer:</strong>{" "}
              {question.answer.not_applicable
                ? `Does not apply — ${question.answer.reason ?? ""}`
                : question.answer.text}
            </p>
          ) : null}
        </div>
      </Disclosure>
    </li>
  );
}

function TargetsForm({
  data,
  onSaved,
}: {
  data: NfrAssessment;
  onSaved: (next: NfrAssessment) => void;
}) {
  const current = (data.profile.targets ?? {}) as Record<string, number | string | null>;
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(data.targets.map((t) => [t.name, current[t.name]?.toString() ?? ""])),
  );
  const [regulations, setRegulations] = useState((data.profile.regulations ?? []).join(", "));
  const [platforms, setPlatforms] = useState((data.profile.platforms ?? []).join(", "));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const list = (text: string) =>
    text
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  const save = () => {
    const targets: Record<string, number | string> = {};
    for (const spec of data.targets) {
      const raw = (values[spec.name] ?? "").trim();
      if (!raw) continue;
      targets[spec.name] = spec.kind === "text" ? raw : Number(raw);
    }
    setBusy(true);
    setError(null);
    saveNfrProfile(
      data.project_id,
      {
        ...data.profile,
        targets,
        regulations: list(regulations),
        platforms: list(platforms),
      },
      data.profile_version,
      "Targets",
    ).then(
      (next) => {
        setBusy(false);
        onSaved(next);
      },
      (caught: unknown) => {
        setBusy(false);
        setError(describeError(caught));
      },
    );
  };
  return (
    <div className="stack" data-testid="nfr-targets">
      <div className="grid grid-auto">
        {data.targets.map((spec) => (
          <div key={spec.name} className="field">
            <label htmlFor={`target-${spec.name}`}>
              {spec.label}
              {spec.unit ? ` (${spec.unit})` : ""}
            </label>
            <input
              id={`target-${spec.name}`}
              type={spec.kind === "text" ? "text" : "number"}
              inputMode={spec.kind === "text" ? undefined : "decimal"}
              min={spec.kind === "text" ? undefined : spec.low}
              max={spec.kind === "text" ? undefined : spec.high}
              step={spec.kind === "int" ? 1 : "any"}
              value={values[spec.name] ?? ""}
              disabled={!data.can_edit}
              onChange={(event) => {
                const next = event.target.value;
                setValues((old) => ({ ...old, [spec.name]: next }));
              }}
            />
          </div>
        ))}
        <div className="field">
          <label htmlFor="nfr-regulations">Regulations (comma separated)</label>
          <input
            id="nfr-regulations"
            type="text"
            value={regulations}
            disabled={!data.can_edit}
            onChange={(event) => {
              setRegulations(event.target.value);
            }}
          />
        </div>
        <div className="field">
          <label htmlFor="nfr-platforms">Supported platforms (comma separated)</label>
          <input
            id="nfr-platforms"
            type="text"
            value={platforms}
            disabled={!data.can_edit}
            onChange={(event) => {
              setPlatforms(event.target.value);
            }}
          />
        </div>
      </div>
      {data.can_edit ? (
        <div className="row">
          <button type="button" className="btn btn-primary" onClick={save} disabled={busy}>
            <Icon name="check" size={15} /> {busy ? "Saving…" : "Save targets"}
          </button>
          <span className="hint">
            Targets are your statements; they turn evidence into answers.
          </span>
        </div>
      ) : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </div>
  );
}

export function NfrView({ projectId }: { projectId: string }) {
  const loaded = useAsync((signal) => fetchNfr(projectId, signal), [projectId]);
  const [saved, setSaved] = useState<NfrAssessment | null>(null);
  const data = saved ?? loaded.data;
  if (loaded.error) return <Alert tone="bad">{loaded.error}</Alert>;
  if (!data) return <Loading />;
  return (
    <div className="stack" data-testid="nfr">
      <section className="card stack" aria-labelledby="nfr-title">
        <div className="card-head">
          <div>
            <h2 id="nfr-title" className="card-title">
              <Icon name="shield" size={16} /> NFR readiness
            </h2>
            <p className="card-sub">
              How this system answers common non-functional requirement questions: what the code
              shows, what needs work, and what only your team can tell.
            </p>
          </div>
          <div className="row">
            <a className="btn btn-ghost" href={nfrExportUrl(projectId, "csv")} download>
              <Icon name="download" size={15} /> Spreadsheet (CSV)
            </a>
            <a className="btn btn-ghost" href={nfrExportUrl(projectId, "md")} download>
              <Icon name="download" size={15} /> Report
            </a>
          </div>
        </div>
        {data.basis ? (
          <p className="small secondary" data-testid="nfr-basis">
            Evidence from the latest review
            {data.basis.reviewed_at ? ` (${formatRelative(data.basis.reviewed_at)})` : ""}.
          </p>
        ) : (
          <Alert tone="info">
            No upload has been reviewed yet, so there is no evidence from the code. You can already
            record your targets and answers.
          </Alert>
        )}
        <div className="tiles tiles-3" role="group" aria-label="Questions by status">
          {ORDER.map((status) => (
            <div key={status} className="tile" data-tile={status}>
              <span className="tile-value">{data.counts[status] ?? 0}</span>
              <span className="tile-label">
                <StatusChip status={status} labels={data.status_labels} />
              </span>
            </div>
          ))}
        </div>
        <p className="hint">
          Evidence shows that a mechanism is declared or present, not that it works under load. Your
          team&apos;s answers are shown as attested. refactorX never certifies compliance.
        </p>
      </section>
      <section className="card stack" aria-labelledby="nfr-targets-title">
        <h2 id="nfr-targets-title" className="card-title">
          <Icon name="pulse" size={16} /> Your targets
        </h2>
        <TargetsForm key={data.profile_version} data={data} onSaved={setSaved} />
        {data.history.length > 0 ? (
          <p className="small muted">
            Version {data.profile_version}
            {data.profile_saved_by ? ` · saved by ${data.profile_saved_by}` : ""}
            {data.profile_saved_at ? ` · ${formatRelative(data.profile_saved_at)}` : ""}
          </p>
        ) : null}
      </section>
      {data.aspects.map((aspect) => (
        <section
          key={aspect.id}
          className="card stack"
          aria-labelledby={`aspect-${aspect.id}`}
          data-testid={`nfr-aspect-${aspect.id}`}
        >
          <div>
            <h2 id={`aspect-${aspect.id}`} className="card-title">
              {aspect.name}
            </h2>
            <p className="card-sub">ISO/IEC 25010: {aspect.iso}</p>
          </div>
          <ul className="stack stack-sm plain-list">
            {aspect.questions.map((question) => (
              <QuestionItem
                key={question.id}
                question={question}
                data={data}
                projectId={projectId}
                onSaved={setSaved}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
