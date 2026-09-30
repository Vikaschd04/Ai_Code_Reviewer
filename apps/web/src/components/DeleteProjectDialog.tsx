import { useEffect, useId, useRef, useState } from "react";

import { describeError } from "../api/client";
import { deleteProject } from "../api/endpoints";
import { Alert } from "./Common";
import { Icon } from "./Icon";

/** Confirmation for permanent deletion: the user types the project name to enable the button. */
export function DeleteProjectDialog({
  projectId,
  projectName,
  open,
  onClose,
  onDeleted,
}: {
  projectId: string;
  projectName: string;
  open: boolean;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const inputId = useId();
  const titleId = useId();
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) {
      setTyped("");
      setError(null);
      element.showModal();
    } else if (!open && element.open) {
      element.close();
    }
  }, [open]);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await deleteProject(projectId);
      onDeleted();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  const matches = typed.trim() === projectName;
  return (
    <dialog
      ref={dialog}
      className="dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form
        method="dialog"
        className="dialog-body"
        onSubmit={(event) => {
          event.preventDefault();
          if (matches && !busy) void confirm();
        }}
      >
        <div className="media">
          <span className="feature-icon feature-icon-danger" aria-hidden="true">
            <Icon name="trash" size={20} />
          </span>
          <div className="stack stack-sm">
            <h2 id={titleId} className="card-title">
              Delete this project?
            </h2>
            <p className="small secondary">
              <strong className="strong">{projectName}</strong> and everything in it — uploads,
              reviews, findings, tracked issues and architecture maps — will be deleted permanently.
              This cannot be undone.
            </p>
          </div>
        </div>
        <div className="field">
          <label htmlFor={inputId}>Type the project name to confirm</label>
          <input
            id={inputId}
            autoComplete="off"
            value={typed}
            placeholder={projectName}
            onChange={(event) => {
              setTyped(event.target.value);
            }}
          />
        </div>
        {error ? <Alert tone="bad">{error}</Alert> : null}
        <div className="dialog-actions">
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-danger-solid" disabled={!matches || busy}>
            <Icon name="trash" size={16} />
            {busy ? "Deleting…" : "Delete project"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
