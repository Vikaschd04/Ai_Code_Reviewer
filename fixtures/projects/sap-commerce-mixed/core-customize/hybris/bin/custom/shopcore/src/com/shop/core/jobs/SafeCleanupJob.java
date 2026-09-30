package com.shop.core.jobs;

import de.hybris.platform.cronjob.enums.CronJobResult;
import de.hybris.platform.cronjob.enums.CronJobStatus;
import de.hybris.platform.cronjob.model.CronJobModel;
import de.hybris.platform.servicelayer.cronjob.AbstractJobPerformable;
import de.hybris.platform.servicelayer.cronjob.PerformResult;
import java.util.List;

public class SafeCleanupJob extends AbstractJobPerformable<CronJobModel> {

    private List<String> batches;

    // Negative: the loop checks for an abort request.
    @Override
    public PerformResult perform(final CronJobModel cronJob) {
        for (final String batch : batches) {
            if (clearAbortRequestedIfNeeded(cronJob)) {
                return new PerformResult(CronJobResult.ERROR, CronJobStatus.ABORTED);
            }
            process(batch);
        }
        return new PerformResult(CronJobResult.SUCCESS, CronJobStatus.FINISHED);
    }

    @Override
    public boolean isAbortable() {
        return true;
    }

    private void process(final String batch) {
        batches.remove(batch);
    }
}
