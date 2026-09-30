import { LightningElement, wire } from 'lwc';
import getAccounts from '@salesforce/apex/AccountService.getAccounts';
import upsertTier from '@salesforce/apex/lbase.TierService.upsertTier';
import NAME_FIELD from '@salesforce/schema/Account.Name';
import POINTS_FIELD from '@salesforce/schema/Loyalty_Member__c.Points__c';

export default class AccountList extends LightningElement {
    filter = '';
    fields = [NAME_FIELD, POINTS_FIELD];

    @wire(getAccounts, { nameFilter: '$filter' })
    accounts;

    handleTier(event) {
        return upsertTier({ memberId: event.detail.id });
    }
}
