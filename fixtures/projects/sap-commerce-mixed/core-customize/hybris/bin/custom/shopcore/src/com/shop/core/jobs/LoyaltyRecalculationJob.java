package com.shop.core.jobs;

import com.shop.core.model.LoyaltyAccountModel;
import com.shop.core.model.LoyaltyRecalculationCronJobModel;
import com.shop.core.service.impl.DefaultLoyaltyService;
import de.hybris.platform.cronjob.enums.CronJobResult;
import de.hybris.platform.cronjob.enums.CronJobStatus;
import de.hybris.platform.servicelayer.cronjob.AbstractJobPerformable;
import de.hybris.platform.servicelayer.cronjob.PerformResult;
import java.util.List;

public class LoyaltyRecalculationJob extends AbstractJobPerformable<LoyaltyRecalculationCronJobModel> {

    private DefaultLoyaltyService loyaltyService;
    private List<LoyaltyAccountModel> accounts;

    // Positive: a long loop that never checks whether the job was asked to abort.
    @Override
    public PerformResult perform(final LoyaltyRecalculationCronJobModel cronJob) {
        for (final LoyaltyAccountModel account : accounts) {
            loyaltyService.addPoints(List.of(account), 1);
        }
        return new PerformResult(CronJobResult.SUCCESS, CronJobStatus.FINISHED);
    }

    public void setLoyaltyService(final DefaultLoyaltyService loyaltyService) {
        this.loyaltyService = loyaltyService;
    }
}
