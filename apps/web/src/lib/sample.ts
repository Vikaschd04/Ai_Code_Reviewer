import type { Intake, Scan } from "../api/client";
import { createSampleProject, fetchIntake, startScan } from "../api/endpoints";

export type SampleStep = "creating" | "preparing" | "starting";

export const SAMPLE_STEP_LABELS: Record<SampleStep, string> = {
  creating: "Creating the sample project…",
  preparing: "Checking and freezing its code…",
  starting: "Starting the review…",
};

async function waitUntilReady(intakeId: string): Promise<Intake> {
  for (let attempt = 0; attempt < 900; attempt++) {
    const intake = await fetchIntake(intakeId);
    if (intake.state === "READY") return intake;
    if (["REJECTED", "FAILED", "CANCELED"].includes(intake.state)) {
      throw new Error(intake.error_message ?? "The sample project could not be prepared.");
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("Preparing the sample project is taking longer than expected; try again later.");
}

/** One-click dry run: create the sample project, wait for its snapshot, start its review. */
export async function runSampleReview(
  workspaceId: string,
  onStep: (step: SampleStep) => void,
): Promise<Scan> {
  onStep("creating");
  const created = await createSampleProject(workspaceId);
  onStep("preparing");
  const intake = await waitUntilReady(created.intake.id);
  if (!intake.snapshot_id) throw new Error("The sample project has no code snapshot.");
  onStep("starting");
  return startScan(created.project.id, intake.snapshot_id);
}
